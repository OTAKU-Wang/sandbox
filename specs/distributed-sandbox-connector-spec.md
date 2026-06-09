# 分布式沙箱集群与连接器架构

## 1. 整体架构

```
                            ┌─────────────────────────────────────────────┐
                            │           控制平面 (Control Plane)           │
                            │  ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
                            │  │ API GW   │ │ 认证中心  │ │ 合约引擎     │ │
                            │  │ (Kong)   │ │ (Keycloak)│ │ (Contract)   │ │
                            │  └────┬─────┘ └────┬─────┘ └──────┬───────┘ │
                            └───────┼────────────┼──────────────┼─────────┘
                                    │            │              │
                            ┌───────┼────────────┼──────────────┼─────────┐
                            │       ▼            ▼              ▼         │
                            │  ┌──────────────────────────────────────┐   │
                            │  │     沙箱调度器 (Sandbox Scheduler)    │   │
                            │  │  Celery + RabbitMQ + Redis           │   │
                            │  │  • 任务队列 (按买方隔离)              │   │
                            │  │  • 资源配额管理                      │   │
                            │  │  • 安全级别路由                      │   │
                            │  └──────────┬───────────────────────────┘   │
                            │             │                               │
                            │     ┌───────┼───────┬───────────┐           │
                            │     ▼       ▼       ▼           ▼           │
                            │  ┌─────┐ ┌─────┐ ┌─────┐ ┌──────────┐     │
                            │  │L1池 │ │L2池 │ │L3池 │ │ API沙箱池 │     │
                            │  │SGX  │ │gVisr│ │Docker│ │ (常驻)   │     │
                            │  │节点群│ │节点群│ │节点群│ │ 节点群   │     │
                            │  └──┬──┘ └──┬──┘ └──┬──┘ └────┬─────┘     │
                            └─────┼───────┼───────┼─────────┼────────────┘
                                  │       │       │         │
    ┌───────────┐                 ▼       ▼       ▼         ▼
    │  连接器    │  ──→   ┌─────────────────────────────────────────┐
    │ (Connector│        │           数据平面 (Data Plane)          │
    │  Gateway) │        │  ┌─────────┐  ┌─────────┐  ┌─────────┐ │
    │           │        │  │ 沙箱实例 │  │ 沙箱实例 │  │ 沙箱实例 │ │
    │ • 数据源  │        │  │ 买方A    │  │ 买方B    │  │ 买方C    │ │
    │   适配器  │        │  │ (L2)     │  │ (L1)    │  │ (API)   │ │
    │ • 数据同步│        │  │          │  │          │  │          │ │
    │ • 协议转换│        │  │┌────────┐│  │┌────────┐│  │┌────────┐│ │
    └───────────┘        │  ││Postgres││  ││Postgres││  ││Postgres││ │
                         │  ││  加密  ││  ││  TEE   ││  ││  加密  ││ │
    ┌───────────┐        │  │└────────┘│  │└────────┘│  │└────────┘│ │
    │  数据平台  │──→     │  │┌────────┐│  │┌────────┐│  │┌────────┐│ │
    │ (外部)    │        │  ││ MinIO  ││  ││ MinIO  ││  ││ MinIO  ││ │
    │           │        │  │└────────┘│  │└────────┘│  │└────────┘│ │
    └───────────┘        │  └─────────┘  └─────────┘  └─────────┘ │
                         └─────────────────────────────────────────┘
```


## 2. 沙箱调度器 (Sandbox Scheduler)

### 2.1 核心职责

```
调度器负责:
  1. 接收买方任务请求
  2. 根据合约安全级别路由到对应节点池
  3. 创建/复用沙箱实例 (每买方每合约独立实例)
  4. 管理资源配额 (CPU/内存/磁盘/DP预算)
  5. 沙箱生命周期管理 (创建→运行→暂停→销毁)
```

### 2.2 数据模型

```sql
-- 沙箱实例表
CREATE TABLE sandbox_instances (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_org_id    UUID NOT NULL REFERENCES orgs(id),        -- 买方组织
    contract_id     UUID NOT NULL REFERENCES contracts(id),    -- 关联合约
    product_id      UUID NOT NULL REFERENCES products(id),     -- 数据产品
    security_level  VARCHAR(2) NOT NULL CHECK (security_level IN ('L1','L2','L3')),
    status          VARCHAR(20) NOT NULL DEFAULT 'creating'
                    CHECK (status IN ('creating','running','paused','destroying','destroyed','failed')),
    -- K8s 调度信息
    k8s_namespace   VARCHAR(63) NOT NULL,          -- sandbox-{buyer_org_id[:8]}
    k8s_pod_name    VARCHAR(253),
    k8s_node_name   VARCHAR(253),                  -- 调度到的物理节点
    -- 资源配额
    cpu_limit       VARCHAR(10) DEFAULT '4',       -- 4 cores
    memory_limit    VARCHAR(10) DEFAULT '8Gi',
    disk_limit      VARCHAR(10) DEFAULT '50Gi',
    gpu_limit       INT DEFAULT 0,                 -- GPU数量
    -- 加密配置
    dek_id          UUID REFERENCES kms_keys(id),  -- 数据加密密钥
    vtpm_enabled    BOOLEAN DEFAULT FALSE,         -- L1 only
    attestation_id  VARCHAR(255),                  -- TEE远程证明ID
    -- 连接器配置
    connector_type  VARCHAR(50),                   -- internal/api_gateway/file_connector/...
    connector_config JSONB DEFAULT '{}',           -- 连接器特定配置
    -- 时间戳
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    paused_at       TIMESTAMPTZ,
    destroyed_at    TIMESTAMPTZ,
    last_heartbeat  TIMESTAMPTZ
);

-- 资源配额表 (按买方组织)
CREATE TABLE sandbox_quotas (
    org_id          UUID PRIMARY KEY REFERENCES orgs(id),
    max_instances   INT DEFAULT 5,                 -- 最大并发沙箱数
    max_cpu_cores   INT DEFAULT 16,                -- 总CPU限制
    max_memory_gb   INT DEFAULT 64,                -- 总内存限制
    max_disk_gb     INT DEFAULT 500,               -- 总磁盘限制
    max_gpu_count   INT DEFAULT 0,                 -- GPU限制
    max_dp_budget   DECIMAL(10,2) DEFAULT 100.0,   -- DP总预算ε
    used_dp_budget  DECIMAL(10,2) DEFAULT 0.0,     -- 已消耗DP预算
    -- 连接器配额
    max_connectors  INT DEFAULT 3,                 -- 最大连接器数
    max_sync_freq   INT DEFAULT 3600,              -- 最小同步间隔(秒)
    max_data_volume VARCHAR(20) DEFAULT '100Gi'    -- 连接器最大数据量
);
```

### 2.3 调度策略

```python
class SandboxScheduler:
    """
    调度器核心逻辑:
    1. 安全级别路由 → 选择节点池
    2. 资源检查 → 验证配额
    3. 亲和性调度 → 同买方同节点 (减少数据移动)
    4. 沙箱复用 → 已有活跃沙箱直接复用
    """

    async def schedule_task(self, task: TaskRequest):
        # 1. 查找或创建沙箱实例
        sandbox = await self.find_or_create_sandbox(
            buyer_org_id=task.buyer_org_id,
            contract_id=task.contract_id,
            security_level=task.security_level
        )

        # 2. 检查资源配额
        quota = await self.check_quota(task.buyer_org_id, task.estimated_resources)
        if not quota.sufficient:
            raise QuotaExceeded(quota.message)

        # 3. 检查DP预算
        if task.dp_epsilon:
            dp_ok = await self.check_dp_budget(task.buyer_org_id, task.dp_epsilon)
            if not dp_ok:
                raise DPBudgetExceeded()

        # 4. 提交任务到沙箱
        result = await self.submit_to_sandbox(sandbox, task)
        return result

    async def find_or_create_sandbox(self, buyer_org_id, contract_id, security_level):
        """查找活跃沙箱或创建新实例"""
        # 查找已有的活跃沙箱
        existing = await db.query("""
            SELECT * FROM sandbox_instances
            WHERE buyer_org_id = $1 AND contract_id = $2
              AND status = 'running'
            ORDER BY last_heartbeat DESC
            LIMIT 1
        """, buyer_org_id, contract_id)

        if existing:
            # 检查心跳 (5分钟内)
            if (now() - existing.last_heartbeat).seconds < 300:
                return existing
            else:
                # 心跳超时, 标记为暂停
                await self.pause_sandbox(existing.id)

        # 创建新沙箱
        return await self.create_sandbox(buyer_org_id, contract_id, security_level)

    async def create_sandbox(self, buyer_org_id, contract_id, security_level):
        """创建K8s沙箱Pod"""
        namespace = f"sandbox-{str(buyer_org_id)[:8]}"

        # 确保namespace存在
        await k8s.create_namespace_if_not_exists(namespace, labels={
            "cds/buyer": str(buyer_org_id),
            "cds/security-level": security_level
        })

        # 根据安全级别选择RuntimeClass
        runtime_class = {
            "L1": "sgx",        # Intel SGX
            "L2": "gvisor",     # gVisor
            "L3": "runc"        # 标准容器
        }[security_level]

        # 生成K8s Pod spec
        pod_spec = self.build_pod_spec(
            namespace=namespace,
            runtime_class=runtime_class,
            security_level=security_level,
            contract_id=contract_id,
            resources=self.get_resource_limits(buyer_org_id, security_level)
        )

        # 创建Pod
        pod = await k8s.create_pod(namespace, pod_spec)

        # 等待Pod就绪
        await k8s.wait_for_pod_ready(namespace, pod.name, timeout=120)

        # 记录到数据库
        sandbox = await db.insert("sandbox_instances", {
            "buyer_org_id": buyer_org_id,
            "contract_id": contract_id,
            "security_level": security_level,
            "status": "running",
            "k8s_namespace": namespace,
            "k8s_pod_name": pod.name,
            "k8s_node_name": pod.spec.node_name,
            "started_at": now()
        })

        return sandbox

    def build_pod_spec(self, namespace, runtime_class, security_level, contract_id, resources):
        """根据安全级别构建Pod规格"""
        base_spec = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": f"sandbox-{str(contract_id)[:8]}-{int(time.time())}",
                "namespace": namespace,
                "labels": {
                    "cds/contract": str(contract_id),
                    "cds/security-level": security_level,
                    "cds/managed-by": "sandbox-scheduler"
                },
                "annotations": {
                    "cds/created-at": datetime.utcnow().isoformat(),
                    "cds/seccomp-profile": "cds-restrictive"
                }
            },
            "spec": {
                "runtimeClassName": runtime_class,
                "containers": [{
                    "name": "sandbox-worker",
                    "image": f"cds/sandbox-worker:{security_level.lower()}",
                    "ports": [{"containerPort": 8000, "name": "api"}],
                    "env": [
                        {"name": "CDS_CONTRACT_ID", "value": str(contract_id)},
                        {"name": "CDS_SECURITY_LEVEL", "value": security_level},
                        {"name": "CDS_WORKSPACE", "value": f"/workspace/{contract_id}"},
                        {"name": "CDS_DEK_PATH", "value": "/keys/dek.enc"},
                    ],
                    "resources": {
                        "requests": {"cpu": "1", "memory": "2Gi"},
                        "limits": resources
                    },
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": "/workspace"},
                        {"name": "keys", "mountPath": "/keys", "readOnly": True},
                    ]
                }],
                "volumes": [
                    {"name": "workspace", "persistentVolumeClaim": {"claimName": f"pvc-{contract_id}"}},
                    {"name": "keys", "secret": {"secretName": f"sandbox-keys-{contract_id}"}},
                ],
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "fsGroup": 1000,
                    "seccompProfile": {
                        "type": "Localhost",
                        "localhostProfile": "cds-restrictive.json"
                    }
                },
                "serviceAccountName": f"sa-{namespace}",
                "automountServiceAccountToken": False,
            }
        }

        # L1 特殊配置: SGX资源
        if security_level == "L1":
            base_spec["spec"]["containers"][0]["resources"]["limits"]["intel.com/sgx-enclave"] = "1"
            base_spec["spec"]["containers"][0]["resources"]["limits"]["intel.com/sgx-epc"] = "67108864"
            base_spec["spec"]["nodeSelector"] = {"intel-sgx": "enabled"}

        return base_spec


## 3. 多租户隔离

### 3.1 隔离层次

```
┌──────────────────────────────────────────────────────────────┐
│                    多租户隔离架构                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  买方A (银行)          买方B (保险)          买方C (科技)    │
│  ┌────────────┐        ┌────────────┐        ┌────────────┐ │
│  │ Namespace A │        │ Namespace B │        │ Namespace C │ │
│  │ sandbox-aaa │        │ sandbox-bbb │        │ sandbox-ccc │ │
│  ├────────────┤        ├────────────┤        ├────────────┤ │
│  │ 沙箱实例 1  │        │ 沙箱实例 3  │        │ 沙箱实例 5  │ │
│  │ (L1 SGX)   │        │ (L2 gVisor) │        │ (API沙箱)  │ │
│  ├────────────┤        ├────────────┤        ├────────────┤ │
│  │ 沙箱实例 2  │        │ 沙箱实例 4  │        │            │ │
│  │ (L3 Docker) │        │ (L1 SGX)   │        │            │ │
│  └────────────┘        └────────────┘        └────────────┘ │
│                                                              │
│  隔离层次:                                                   │
│  ① K8s Namespace 隔离 (网络策略 + RBAC)                      │
│  ② 容器隔离 (L1: SGX / L2: gVisor / L3: seccomp)            │
│  ③ 存储隔离 (独立PVC, 加密卷)                                 │
│  ④ 网络隔离 (NetworkPolicy, 仅允许出口到连接器)               │
│  ⑤ 身份隔离 (独立ServiceAccount, 最小权限)                   │
│  ⑥ 密钥隔离 (独立DEK, Vault per-namespace secret)            │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 K8s NetworkPolicy

```yaml
# 每个买方namespace的网络隔离策略
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sandbox-isolation
  namespace: sandbox-{buyer_org_id}
spec:
  podSelector:
    matchLabels:
      cds/managed-by: sandbox-scheduler
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # 仅允许来自控制平面的API调用
    - from:
      - namespaceSelector:
          matchLabels:
            cds/role: control-plane
      ports:
        - port: 8000
          protocol: TCP
  egress:
    # 允许DNS
    - to:
      - namespaceSelector: {}
      ports:
        - port: 53
          protocol: UDP
    # 允许访问连接器网关 (数据同步)
    - to:
      - namespaceSelector:
          matchLabels:
            cds/role: connector
      ports:
        - port: 443
          protocol: TCP
    # 允许访问Vault (密钥获取)
    - to:
      - namespaceSelector:
          matchLabels:
            cds/role: vault
      ports:
        - port: 8200
          protocol: TCP
    # 禁止: 沙箱之间禁止互访
    # 禁止: 沙箱禁止直接访问外部网络
```

### 3.3 资源配额

```yaml
# 每个买方namespace的资源配额
apiVersion: v1
kind: ResourceQuota
metadata:
  name: sandbox-quota
  namespace: sandbox-{buyer_org_id}
spec:
  hard:
    requests.cpu: "16"           # 总CPU请求
    requests.memory: "64Gi"      # 总内存请求
    limits.cpu: "32"             # 总CPU限制
    limits.memory: "128Gi"       # 总内存限制
    persistentvolumeclaims: "10" # 最多10个PVC
    pods: "20"                   # 最多20个Pod
---
# Pod安全策略
apiVersion: policy/v1beta1
kind: PodSecurityPolicy
metadata:
  name: cds-sandbox-psp
spec:
  privileged: false
  allowPrivilegeEscalation: false
  requiredDropCapabilities:
    - ALL
  volumes:
    - 'persistentVolumeClaim'
    - 'secret'
    - 'emptyDir'
  runAsUser:
    rule: 'MustRunAsNonRoot'
  seLinux:
    rule: 'RunAsAny'
  fsGroup:
    rule: 'MustRunAs'
    ranges:
      - min: 1000
        max: 65535
```


## 4. 连接器架构 (Connector Architecture)

### 4.1 连接器类型

```
连接器 (Connector) 负责:
  ① 从外部数据源获取数据
  ② 将数据安全传输到沙箱
  ③ 定期同步增量数据
  ④ 协议转换 (不同数据源 → 统一接口)

连接器类型:
  ┌──────────────────────────────────────────────────────┐
  │ 类型            │ 数据源              │ 传输方式     │
  ├──────────────────────────────────────────────────────┤
  │ internal        │ 平台内部数据产品     │ MinIO直接挂载│
  │ api_gateway     │ 外部API (REST/gRPC) │ HTTPS代理    │
  │ file_connector  │ 文件系统 (SFTP/NFS) │ 加密传输     │
  │ db_connector    │ 外部数据库          │ CDC流式同步  │
  │ platform_hook   │ 第三方数据平台      │ Webhook推送  │
  │ blockchain      │ 区块链节点          │ 链上数据拉取 │
  └──────────────────────────────────────────────────────┘
```

### 4.2 连接器数据流

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        连接器数据流架构                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  外部数据源                   连接器层                    沙箱层        │
│  ┌─────────┐                ┌─────────────┐              ┌──────────┐  │
│  │ 数据平台 │──┐             │             │              │          │  │
│  │ (API)   │  │             │  Connector  │    加密通道   │  沙箱实例 │  │
│  └─────────┘  │             │  Gateway    │──────────────→│          │  │
│  ┌─────────┐  │    HTTPS    │  (FastAPI)  │   SM4-CBC    │  买方A   │  │
│  │ 外部DB  │──┼────────────→│             │              │          │  │
│  │ (MySQL) │  │    JDBC     │  ┌─────────┐│              │ ┌──────┐ │  │
│  └─────────┘  │             │  │ 数据转换 ││              │ │PG实例│ │  │
│  ┌─────────┐  │             │  │ 协议适配 ││              │ │加密表│ │  │
│  │ 文件服务器│──┼────SFTP───→│  │ 增量检测 ││              │ └──────┘ │  │
│  │ (SFTP)  │  │             │  │ 格式标准化││              │          │  │
│  └─────────┘  │             │  └─────────┘│              │ ┌──────┐ │  │
│  ┌─────────┐  │             │             │              │ │MinIO │ │  │
│  │ 区块链  │──┼──JSON-RPC──→│             │              │ │加密桶│ │  │
│  │ (FISCO) │  │             └─────────────┘              │ └──────┘ │  │
│  └─────────┘  │                                          └──────────┘  │
│               │                                                         │
└───────────────┴─────────────────────────────────────────────────────────┘
```

### 4.3 连接器网关实现

```python
# connector_gateway.py
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import hashlib
import json

app = FastAPI(title="CDS Connector Gateway", version="2.0.0")

class ConnectorConfig(BaseModel):
    """连接器配置"""
    connector_type: str          # api_gateway/file_connector/db_connector/...
    source_url: str              # 数据源URL
    auth_type: str               # api_key/oauth2/certificate/none
    auth_config: dict            # 认证配置 (加密存储)
    sync_mode: str = "full"      # full/incremental/cdc
    sync_interval: int = 3600    # 同步间隔 (秒)
    data_format: str = "json"    # json/csv/parquet/avro
    encryption_required: bool = True  # 是否需要加密传输
    max_volume: str = "10Gi"     # 最大数据量

class SyncRequest(BaseModel):
    """数据同步请求"""
    connector_id: str
    sandbox_id: str
    contract_id: str
    tables: list[str] = []       # 要同步的表 (db_connector)
    paths: list[str] = []        # 要同步的路径 (file_connector)
    filters: dict = {}           # 数据过滤条件
    since: str = None            # 增量同步起始时间


@app.post("/connectors")
async def create_connector(config: ConnectorConfig, buyer_org_id: str = Depends(get_buyer)):
    """创建连接器"""
    # 1. 验证买方配额
    quota = await get_connector_quota(buyer_org_id)
    if quota.used >= quota.max_connectors:
        raise HTTPException(400, "连接器数量已达上限")

    # 2. 测试连接
    test_result = await test_connector_connection(config)
    if not test_result.success:
        raise HTTPException(400, f"连接测试失败: {test_result.error}")

    # 3. 存储配置 (加密)
    connector = await db.insert("connectors", {
        "buyer_org_id": buyer_org_id,
        "connector_type": config.connector_type,
        "source_url": encrypt(config.source_url),           # SM4加密
        "auth_config": encrypt(json.dumps(config.auth_config)),  # SM4加密
        "sync_mode": config.sync_mode,
        "sync_interval": config.sync_interval,
        "data_format": config.data_format,
        "max_volume": config.max_volume,
        "status": "active"
    })

    return {"connector_id": connector.id, "status": "created"}


@app.post("/connectors/{connector_id}/sync")
async def trigger_sync(connector_id: str, req: SyncRequest):
    """触发数据同步 → 数据进入沙箱"""
    connector = await get_connector(connector_id)

    # 1. 验证合约状态
    contract = await get_contract(req.contract_id)
    if contract.status != "running":
        raise HTTPException(400, "合约状态异常")

    # 2. 获取沙箱信息
    sandbox = await get_sandbox(req.sandbox_id)
    if sandbox.status != "running":
        raise HTTPException(400, "沙箱未就绪")

    # 3. 创建同步任务 (Celery)
    task = sync_task.delay(
        connector_id=connector_id,
        sandbox_id=req.sandbox_id,
        contract_id=req.contract_id,
        tables=req.tables,
        paths=req.paths,
        filters=req.filters,
        since=req.since
    )

    return {"task_id": task.id, "status": "queued"}


@app.get("/connectors/{connector_id}/sync/{task_id}")
async def get_sync_status(connector_id: str, task_id: str):
    """查询同步进度"""
    task = sync_task.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": task.status,
        "progress": task.info.get("progress", 0),
        "records_synced": task.info.get("records_synced", 0),
        "bytes_transferred": task.info.get("bytes_transferred", 0),
        "errors": task.info.get("errors", [])
    }
```

### 4.4 数据同步 Worker

```python
# sync_worker.py
from celery import Celery
import hashlib

celery = Celery('sync', broker='amqp://cds:***@rabbitmq:5672/cds')

@celery.task(bind=True, max_retries=3)
def sync_task(self, connector_id, sandbox_id, contract_id, tables, paths, filters, since):
    """
    数据同步Worker:
    1. 从数据源拉取数据
    2. 加密传输到沙箱
    3. 写入沙箱内的PostgreSQL/MinIO
    4. 记录审计日志
    """
    connector = db.get_connector(connector_id)
    sandbox = db.get_sandbox(sandbox_id)
    contract = db.get_contract(contract_id)

    # 获取沙箱内PostgreSQL连接信息
    pg_conn = get_sandbox_pg_connection(sandbox)

    # 根据连接器类型选择同步策略
    if connector.connector_type == "db_connector":
        sync_from_database(connector, sandbox, pg_conn, tables, filters, since)
    elif connector.connector_type == "file_connector":
        sync_from_files(connector, sandbox, paths, filters)
    elif connector.connector_type == "api_gateway":
        sync_from_api(connector, sandbox, filters)
    elif connector.connector_type == "platform_hook":
        sync_from_platform(connector, sandbox, contract, filters)


def sync_from_database(connector, sandbox, pg_conn, tables, filters, since):
    """
    从外部数据库同步到沙箱PostgreSQL
    流程: 外部DB → 拉取 → SM4加密 → 传输 → 沙箱内解密 → 写入PG
    """
    source_conn = connect_external_db(connector)

    for table in tables:
        # 1. 获取表结构
        schema = source_conn.get_table_schema(table)

        # 2. 在沙箱内创建对应表 (加密列配置)
        create_sandbox_table(pg_conn, table, schema, contract.data_config)

        # 3. 分批拉取数据
        offset = 0
        batch_size = 10000
        total_synced = 0

        while True:
            # 拉取一批数据
            batch = source_conn.query(f"""
                SELECT * FROM {table}
                WHERE {build_incremental_filter(table, since, filters)}
                ORDER BY id
                LIMIT {batch_size} OFFSET {offset}
            """)

            if not batch:
                break

            # 4. 应用列级加密 (根据合约配置)
            encrypted_batch = apply_column_encryption(batch, schema, contract.encryption_config)

            # 5. 写入沙箱PostgreSQL
            bulk_insert(pg_conn, table, encrypted_batch)

            total_synced += len(batch)
            offset += batch_size

            # 更新进度
            self.update_state(state='PROGRESS', meta={
                'progress': min(95, int(total_synced / estimated_total * 100)),
                'records_synced': total_synced
            })

        # 6. 更新哈希链 (审计)
        table_hash = compute_table_hash(pg_conn, table)
        audit_log(connector_id, sandbox.id, table, total_synced, table_hash)


def sync_from_files(connector, sandbox, paths, filters):
    """
    从文件系统同步到沙箱MinIO
    流程: SFTP/NFS → 拉取 → SM4加密 → 传输 → 沙箱内MinIO
    """
    # 获取沙箱MinIO连接信息
    minio_client = get_sandbox_minio_connection(sandbox)

    for path in paths:
        # 1. 列出文件
        files = list_remote_files(connector, path, filters)

        for file_info in files:
            # 2. 拉取文件
            file_data = download_file(connector, file_info.path)

            # 3. SM4加密
            encrypted_data = sm4_encrypt(file_data, sandbox.dek)

            # 4. 计算哈希
            file_hash = sm3_hash(file_data)

            # 5. 上传到沙箱MinIO
            minio_client.put_object(
                bucket=f"sandbox-{sandbox.contract_id}",
                key=file_info.relative_path,
                data=encrypted_data,
                metadata={
                    "original_hash": file_hash,
                    "encrypted_at": datetime.utcnow().isoformat(),
                    "source": connector.source_url
                }
            )

            # 6. 审计日志
            audit_log(connector_id, sandbox.id, file_info.path, file_info.size, file_hash)


def sync_from_api(connector, sandbox, filters):
    """
    从外部API同步到沙箱
    流程: API → 拉取 → SM4加密 → 传输 → 沙箱内存储
    """
    minio_client = get_sandbox_minio_connection(sandbox)
    pg_conn = get_sandbox_pg_connection(sandbox)

    # 1. 调用外部API
    api_data = call_external_api(connector, filters)

    # 2. 根据数据格式处理
    if connector.data_format == "json":
        # JSON数据 → 存入MinIO
        encrypted = sm4_encrypt(json.dumps(api_data).encode(), sandbox.dek)
        minio_client.put_object(
            bucket=f"sandbox-{sandbox.contract_id}",
            key=f"api-data/{connector.id}/{int(time.time())}.json.enc",
            data=encrypted
        )
    elif connector.data_format == "csv":
        # CSV数据 → 解析后存入PostgreSQL
        df = pd.read_csv(io.StringIO(api_data))
        encrypted_df = apply_column_encryption(df, schema, contract.encryption_config)
        bulk_insert_dataframe(pg_conn, "api_import", encrypted_df)
```


## 5. 完整数据流: 连接器 → 沙箱 → 模型

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    完整数据流: 从数据源到模型产出                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  阶段1: 数据导入 (数商操作)                                             │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                        │
│  │ 外部数据  │────→│ 连接器   │────→│ 平台内部  │                        │
│  │ (API/DB/ │     │ 网关     │     │ 存储      │                        │
│  │  文件)   │     │ (加密)   │     │ (MinIO)   │                        │
│  └──────────┘     └──────────┘     └──────────┘                        │
│                                                                         │
│  阶段2: 数据产品发布 (数商操作)                                         │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                        │
│  │ 数据产品  │────→│ 合约引擎 │────→│ 权限开通  │                        │
│  │ 配置     │     │ 创建合约 │     │ 分发DEK   │                        │
│  └──────────┘     └──────────┘     └──────────┘                        │
│                                                                         │
│  阶段3: 沙箱创建 (买方触发)                                             │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                        │
│  │ 买方申请  │────→│ 调度器   │────→│ 沙箱实例  │                        │
│  │ 使用数据  │     │ 创建Pod  │     │ (K8s Pod) │                        │
│  └──────────┘     └──────────┘     └──────────┘                        │
│                                                                         │
│  阶段4: 数据同步 (连接器)                                               │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                        │
│  │ 平台内部  │────→│ 连接器   │────→│ 沙箱内   │                        │
│  │ 数据产品  │     │ 同步任务 │     │ PG+MinIO │                        │
│  │ (加密)   │     │ (SM4)   │     │ (解密)   │                        │
│  └──────────┘     └──────────┘     └──────────┘                        │
│                                                                         │
│  阶段5: 特征工程 (买方操作, 沙箱内)                                     │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                        │
│  │ 特征模板  │────→│ SQL执行  │────→│ 特征向量  │                        │
│  │ (数商定义)│     │ (聚合)   │     │ (统计值)  │                        │
│  └──────────┘     └──────────┘     └──────────┘                        │
│                                                                         │
│  阶段6: 模型训练 (买方操作, 沙箱内)                                     │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                        │
│  │ 特征向量  │────→│ 训练引擎 │────→│ 模型文件  │                        │
│  │          │     │ (DP注入) │     │ +安全检查 │                        │
│  └──────────┘     └──────────┘     └──────────┘                        │
│                                                                         │
│  阶段7: 安全产出 (买方选择)                                             │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                        │
│  │ 模型文件  │────→│ 安全审查 │────→│ 导出/API  │                        │
│  │          │     │ 网关     │     │ 沙箱推理  │                        │
│  └──────────┘     └──────────┘     └──────────┘                        │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```


## 6. 连接器配置示例

### 6.1 从MySQL数据库同步

```yaml
# 买方配置的连接器: 从外部MySQL同步数据到沙箱
connector_type: db_connector
source_url: "mysql://db.example.com:3306/enterprise_db"
auth_type: api_key
auth_config:
  username: "cds_reader"
  api_key: "***"  # Vault加密存储
sync_mode: incremental
sync_interval: 3600  # 每小时同步一次
data_format: structured
encryption_required: true
tables:
  - name: enterprise_basic
    primary_key: ent_id
    incremental_field: updated_at
    filter: "status = 'active'"
  - name: tax_records
    primary_key: id
    incremental_field: updated_at
    filter: "year >= 2022"
```

### 6.2 从API同步

```yaml
# 买方配置的连接器: 从第三方API同步数据
connector_type: api_gateway
source_url: "https://api.thirddata.com/v2"
auth_type: oauth2
auth_config:
  client_id: "cds_client"
  client_secret: "***"  # Vault加密存储
  token_url: "https://api.thirddata.com/oauth/token"
sync_mode: incremental
sync_interval: 7200  # 每2小时同步一次
data_format: json
endpoints:
  - path: "/enterprise/{ent_id}/risk"
    method: GET
    params:
      include_history: true
```

### 6.3 从文件服务器同步

```yaml
# 买方配置的连接器: 从SFTP同步文件
connector_type: file_connector
source_url: "sftp://files.example.com/data"
auth_type: certificate
auth_config:
  username: "cds_user"
  private_key: "***"  # Vault加密存储
sync_mode: full
sync_interval: 86400  # 每天同步一次
data_format: parquet
paths:
  - "/exports/enterprise/*.parquet"
  - "/exports/financial/*.csv"
filters:
  modified_within_days: 30
```
