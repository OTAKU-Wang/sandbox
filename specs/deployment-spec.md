# 密态沙箱系统（CDS）· 部署配置技术规格

> 版本：v1.0
> 作者：@架构师
> 日期：2026-06-04
> 模块：S4-04 部署配置

---

## 1. 概述

CDS 系统采用 Docker Compose（开发/单机）+ Kubernetes Helm Chart（生产/集群）双模式部署。

### 1.1 服务清单

| 服务 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| cds-api | python:3.11-slim | 8000 | FastAPI 后端 |
| cds-frontend | node:18-alpine (nginx) | 80 | React 前端 |
| postgres | postgres:16 | 5432 | 主数据库 |
| redis | redis:7-alpine | 6379 | 缓存+配额 |
| clickhouse | clickhouse/clickhouse-server:24.3 | 8123 | 审计日志 |
| opa | openpolicyagent/opa:0.68.0 | 8181 | 策略引擎 |
| fisco-bcos | fiscoorg/fisco-bcos:v3.0.0 | 30300 | 区块链节点 |

---

## 2. Docker Compose（开发环境）

```yaml
# docker-compose.yml
version: "3.9"

services:
  # ─── 数据库层 ───
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: cds
      POSTGRES_USER: cds
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-cds_dev_password}
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/01-init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cds"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD:-cds_dev_redis} --maxmemory 256mb --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD:-cds_dev_redis}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  clickhouse:
    image: clickhouse/clickhouse-server:24.3-alpine
    ports:
      - "8123:8123"
      - "9000:9000"
    volumes:
      - clickhouse-data:/var/lib/clickhouse
      - ./scripts/init-clickhouse.sql:/docker-entrypoint-initdb.d/01-init.sql
    environment:
      CLICKHOUSE_DB: cds_audit
      CLICKHOUSE_USER: cds
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD:-cds_dev_ch}

  # ─── 策略引擎 ───
  opa:
    image: openpolicyagent/opa:0.68.0
    ports:
      - "8181:8181"
    command:
      - "run"
      - "--server"
      - "--addr=0.0.0.0:8181"
      - "--log-level=info"
    volumes:
      - ./policies:/policies
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8181/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  # ─── 应用层 ───
  cds-api:
    build:
      context: ./app
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://cds:${POSTGRES_PASSWORD:-cds_dev_password}@postgres:5432/cds
      REDIS_URL: redis://:${REDIS_PASSWORD:-cds_dev_redis}@redis:6379/0
      CLICKHOUSE_URL: http://clickhouse:8123/cds_audit
      OPA_URL: http://opa:8181
      JWT_SECRET: ${JWT_SECRET:-dev_jwt_secret_change_in_prod}
      KMS_MASTER_KEY: ${KMS_MASTER_KEY:-dev_kms_key_change_in_prod}
      ENVIRONMENT: development
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      clickhouse:
        condition: service_started
      opa:
        condition: service_healthy
    volumes:
      - ./app:/app
      - sandbox-workspace:/tmp/cds_sandbox
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 15s
      timeout: 5s
      retries: 3

  cds-frontend:
    build:
      context: ./cds-frontend
      dockerfile: Dockerfile
    ports:
      - "3000:80"
    depends_on:
      cds-api:
        condition: service_healthy

volumes:
  postgres-data:
  redis-data:
  clickhouse-data:
  sandbox-workspace:
```

### 2.1 API Dockerfile

```dockerfile
# app/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    bubblewrap \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

# 非 root 用户
RUN useradd -m -s /bin/bash cds
RUN chown -R cds:cds /tmp/cds_sandbox
USER cds

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### 2.2 前端 Dockerfile

```dockerfile
# cds-frontend/Dockerfile
FROM node:18-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### 2.3 Nginx 配置

```nginx
# cds-frontend/nginx.conf
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # SPA 路由
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 代理
    location /api/ {
        proxy_pass http://cds-api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 支持
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }

    # 安全头
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header X-XSS-Protection "1; mode=block";
    add_header Referrer-Policy strict-origin-when-cross-origin;
}
```

---

## 3. 环境变量

```bash
# .env.example
# ─── 数据库 ───
POSTGRES_PASSWORD=change_me_in_prod
REDIS_PASSWORD=change_me_in_prod
CLICKHOUSE_PASSWORD=change_me_in_prod

# ─── 安全 ───
JWT_SECRET=change_me_32_chars_minimum
KMS_MASTER_KEY=change_me_32_chars_minimum

# ─── 应用 ───
ENVIRONMENT=development          # development | staging | production
LOG_LEVEL=INFO                   # DEBUG | INFO | WARNING | ERROR
CORS_ORIGINS=http://localhost:3000

# ─── 区块链（可选） ───
FISCO_NODE_URL=http://fisco-bcos:30300
CHAIN_ID=1
GROUP_ID=1
CONTRACT_ADDRESS=
SIGNER_KEY_PATH=/secrets/signer.key

# ─── 沙箱 ───
SANDBOX_WORKSPACE_BASE=/tmp/cds_sandbox
FIRECRACKER_SOCKET=/var/run/firecracker.socket
OCCLUM_PATH=/opt/occlum
```

---

## 4. 初始化脚本

### 4.1 PostgreSQL 初始化

```sql
-- scripts/init-db.sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 类型定义
CREATE TYPE contract_status AS ENUM (
  'draft', 'negotiating', 'signed', 'active', 'suspended', 'completed', 'violated', 'archived'
);

CREATE TYPE sandbox_type AS ENUM ('l1_tee', 'l2_microvm', 'l3_container');

CREATE TYPE sensitivity_level AS ENUM ('core', 'sensitive', 'restricted', 'public');

-- 组织表
CREATE TABLE organizations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name VARCHAR(200) NOT NULL,
  type VARCHAR(50) NOT NULL,
  unified_social_credit_code VARCHAR(18) UNIQUE,
  sm2_certificate TEXT,
  sm2_fingerprint VARCHAR(64),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 用户表
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  org_id UUID NOT NULL REFERENCES organizations(id),
  email VARCHAR(200) NOT NULL UNIQUE,
  password_hash VARCHAR(200) NOT NULL,
  name VARCHAR(100) NOT NULL,
  role VARCHAR(50) NOT NULL,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 数据产品表
CREATE TABLE data_products (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  org_id UUID NOT NULL REFERENCES organizations(id),
  name VARCHAR(200) NOT NULL,
  description TEXT,
  data_type VARCHAR(50) NOT NULL,
  sensitivity sensitivity_level NOT NULL,
  status VARCHAR(50) DEFAULT 'draft',
  schema_def JSONB,
  allowed_operations TEXT[],
  max_output_rows INT DEFAULT 1000,
  dp_budget_epsilon FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 合约表
CREATE TABLE contracts (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  contract_no VARCHAR(50) NOT NULL UNIQUE,
  version INT DEFAULT 1,
  status contract_status DEFAULT 'draft',
  provider_org_id UUID NOT NULL REFERENCES organizations(id),
  consumer_org_id UUID NOT NULL REFERENCES organizations(id),
  product_ids UUID[] NOT NULL,
  contract_type VARCHAR(50) NOT NULL,
  min_sandbox_level sandbox_type DEFAULT 'l1_tee',
  allowed_sandbox_modes TEXT[],
  max_concurrent_sessions INT DEFAULT 3,
  session_max_hours INT DEFAULT 8,
  valid_from TIMESTAMPTZ NOT NULL,
  valid_until TIMESTAMPTZ NOT NULL,
  terms JSONB NOT NULL,
  policy_bundle JSONB,
  policy_hash VARCHAR(64),
  provider_signature TEXT,
  consumer_signature TEXT,
  signed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 沙箱会话表
CREATE TABLE sandbox_sessions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  contract_id UUID NOT NULL REFERENCES contracts(id),
  user_id UUID NOT NULL REFERENCES users(id),
  sandbox_type sandbox_type NOT NULL,
  status VARCHAR(50) DEFAULT 'provisioning',
  resource_config JSONB NOT NULL,
  container_id VARCHAR(100),
  ip_address VARCHAR(45),
  port INT,
  started_at TIMESTAMPTZ,
  terminated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 任务表
CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  session_id UUID NOT NULL REFERENCES sandbox_sessions(id),
  type VARCHAR(50) NOT NULL,
  code TEXT NOT NULL,
  status VARCHAR(50) DEFAULT 'pending',
  result JSONB,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 输出审查结果表
CREATE TABLE output_results (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  task_id UUID NOT NULL REFERENCES tasks(id),
  session_id UUID NOT NULL REFERENCES sandbox_sessions(id),
  raw_output TEXT,
  redacted_output TEXT,
  watermark VARCHAR(100),
  dp_applied BOOLEAN DEFAULT FALSE,
  inspection_result JSONB NOT NULL,
  passed BOOLEAN NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- DP预算表
CREATE TABLE dp_budgets (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  contract_id UUID NOT NULL REFERENCES contracts(id),
  session_id UUID REFERENCES sandbox_sessions(id),
  total_epsilon FLOAT NOT NULL,
  consumed_epsilon FLOAT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Merkle树表
CREATE TABLE merkle_trees (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  root_hash CHAR(64) NOT NULL,
  leaf_count INT NOT NULL,
  block_number BIGINT,
  tx_hash VARCHAR(66),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  metadata JSONB DEFAULT '{}'
);

CREATE TABLE merkle_leaves (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tree_id UUID NOT NULL REFERENCES merkle_trees(id),
  leaf_index INT NOT NULL,
  leaf_hash CHAR(64) NOT NULL,
  audit_record_id UUID NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_users_org ON users(org_id);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_data_products_org ON data_products(org_id);
CREATE INDEX idx_data_products_status ON data_products(status);
CREATE INDEX idx_contracts_provider ON contracts(provider_org_id);
CREATE INDEX idx_contracts_consumer ON contracts(consumer_org_id);
CREATE INDEX idx_contracts_status ON contracts(status);
CREATE INDEX idx_sandbox_sessions_contract ON sandbox_sessions(contract_id);
CREATE INDEX idx_sandbox_sessions_user ON sandbox_sessions(user_id);
CREATE INDEX idx_sandbox_sessions_status ON sandbox_sessions(status);
CREATE INDEX idx_tasks_session ON tasks(session_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_output_results_session ON output_results(session_id);
CREATE INDEX idx_dp_budgets_contract ON dp_budgets(contract_id);
CREATE INDEX idx_merkle_leaves_tree ON merkle_leaves(tree_id);
```

### 4.2 ClickHouse 初始化

```sql
-- scripts/init-clickhouse.sql
CREATE DATABASE IF NOT EXISTS cds_audit;

CREATE TABLE cds_audit.audit_records (
    id UUID DEFAULT generateUUIDv4(),
    event_type LowCardinality(String),
    actor_id UUID,
    resource_type LowCardinality(String),
    resource_id UUID,
    action String,
    details String,
    ip_address String,
    user_agent String,
    timestamp DateTime64(3) DEFAULT now64(3),

    merkle_leaf_hash FixedString(64),
    merkle_root Nullable(FixedString(64)),
    merkle_path Array(FixedString(64)),
    block_number Nullable(UInt64),
    tx_hash Nullable(String),

    INDEX idx_event_type event_type TYPE bloom_filter GRANULARITY 1,
    INDEX idx_actor_id actor_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_timestamp timestamp TYPE minmax GRANULARITY 1
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, event_type, actor_id)
TTL timestamp + INTERVAL 365 DAY;
```

---

## 5. Kubernetes Helm Chart（生产环境）

### 5.1 Chart 结构

```
cds-helm/
├── Chart.yaml
├── values.yaml
├── templates/
│   ├── _helpers.tpl
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secret.yaml
│   ├── api-deployment.yaml
│   ├── api-service.yaml
│   ├── frontend-deployment.yaml
│   ├── frontend-service.yaml
│   ├── ingress.yaml
│   ├── postgres-statefulset.yaml
│   ├── redis-statefulset.yaml
│   ├── clickhouse-statefulset.yaml
│   ├── opa-deployment.yaml
│   ├── hpa.yaml
│   └── network-policy.yaml
```

### 5.2 values.yaml（关键配置）

```yaml
# values.yaml
replicaCount:
  api: 3
  frontend: 2
  opa: 2

image:
  api:
    repository: registry.example.com/cds/api
    tag: latest
    pullPolicy: IfNotPresent
  frontend:
    repository: registry.example.com/cds/frontend
    tag: latest

ingress:
  enabled: true
  className: nginx
  host: cds.example.com
  tls:
    enabled: true
    secretName: cds-tls

resources:
  api:
    requests:
      cpu: 500m
      memory: 512Mi
    limits:
      cpu: 2000m
      memory: 2Gi
  frontend:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilization: 70

security:
  networkPolicy:
    enabled: true
  podSecurityContext:
    runAsNonRoot: true
    fsGroup: 1000
```

---

## 6. 健康检查端点

```python
# app/main.py — 健康检查
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "1.0.0"}

@app.get("/ready")
async def readiness():
    checks = {}
    try:
        # PostgreSQL
        await database.execute("SELECT 1")
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "fail"
    try:
        # Redis
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "fail"
    try:
        # OPA
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OPA_URL}/health", timeout=5)
            checks["opa"] = "ok" if resp.status_code == 200 else "fail"
    except Exception:
        checks["opa"] = "fail"

    all_ok = all(v == "ok" for v in checks.values())
    return {"ready": all_ok, "checks": checks}
```

---

## 7. 启动命令

```bash
# 开发环境
cp .env.example .env          # 编辑 .env 填入实际值
docker compose up -d           # 启动所有服务
docker compose logs -f cds-api # 查看API日志

# 生产环境
helm install cds ./cds-helm \
  --namespace cds \
  --create-namespace \
  --values cds-helm/values-prod.yaml
```
