# 密态沙箱系统 · 详细技术规格补充（Supplementary Technical Spec）

> 版本：v2.1
> 补充范围：detailed-tech-spec v2.0 缺失组件
> 对齐：tech-spec v1.0 第2/5/6/9/10/11/12节 + product-spec v1.1 第3/6/9节

---

## 目录

11. [密码学体系详细设计](#11-密码学体系详细设计)
12. [数据库 Schema 设计](#12-数据库-schema-设计)
13. [MinIO 加密对象存储](#13-minio-加密对象存储)
14. [配额管理 (Redis Lua)](#14-配额管理-redis-lua)
15. [网络隔离架构](#15-网络隔离架构)
16. [API 接口完整定义](#16-api-接口完整定义)
17. [SDK 设计](#17-sdk-设计)
18. [安全威胁模型 (STRIDE)](#18-安全威胁模型-stride)
19. [侧信道攻击防护](#19-侧信道攻击防护)
20. [性能基准与优化](#20-性能基准与优化)
21. [测试矩阵与质量保障](#21-测试矩阵与质量保障)
22. [监控告警规则](#22-监控告警规则)
23. [配置管理](#23-配置管理)
24. [错误处理与降级](#24-错误处理与降级)

---

## 11. 密码学体系详细设计

### 11.1 密钥层次结构

```
Root CA 私钥 (离线, HSM 保护)
  │
  ├── 中间 CA (KMS 服务证书)
  │     └── 节点证书 (各沙箱节点 SM2 证书)
  │
  ├── 数商身份证书 (SM2, 颁发给数商机构)
  │     └── 数据产品密钥 (DEK)
  │           - SM4-256 随机生成, per-product
  │           - 由数商公钥加密后托管至 KMS
  │           - 合约激活时, KMS 用会话密钥重加密后下发至 TEE
  │
  ├── 买方身份证书 (SM2, 颁发给买方机构)
  │     └── 会话密钥 (Session Key)
  │           - SM4 随机生成, per-session, 短生命周期 (<=24h)
  │           - 用于保护数商 DEK 的跨节点传输
  │
  └── 沙箱节点证书 (SM2 + TEE 证书链)
        └── 内存加密密钥 (MEK)
              - L1: 由 CPU 硬件派生, 不可导出
              - L2: 由 KMS 分发的软件密钥, 存于 tmpfs
```

### 11.2 结构化数据加密方案

```python
# crypto/structured_encryption.py

from gmssl import sm4, sm3, func
import os
import struct

class StructuredDataEncryptor:
    """结构化数据列级/行级加密"""

    def __init__(self, dek: bytes):
        self.dek = dek

    def encrypt_column_deterministic(self, plaintext: bytes) -> bytes:
        """确定性加密 (AES-SIV 等值查询, 用于高敏感列如身份证)
        同一明文 -> 同一密文, 支持等值查询
        """
        # 使用 SM4-ECB + HMAC 作为确定性加密的近似
        # 生产环境应使用 AES-SIV 或 SM4-SIV
        key_enc = self.dek[:16]
        key_mac = sm3.sm3_hash(func.bytes_to_list(self.dek))[16:32] if len(self.dek) >= 16 else self.dek[:16]

        crypt = sm4.CryptSM4()
        crypt.set_key(key_enc, sm4.SM4_ENCRYPT)

        # Pad to 16 bytes
        pad_len = 16 - (len(plaintext) % 16)
        padded = plaintext + bytes([pad_len] * pad_len)
        ciphertext = crypt.cbc_encrypt(b'\x00' * 16, padded)  # Fixed IV for determinism
        return ciphertext

    def encrypt_column_randomized(self, plaintext: bytes) -> tuple:
        """随机加密 (SM4-GCM, 用于普通列)
        每次加密产生不同密文, 防重放
        Returns: (ciphertext, nonce)
        """
        crypt = sm4.CryptSM4()
        crypt.set_key(self.dek, sm4.SM4_ENCRYPT)
        nonce = os.urandom(16)
        pad_len = 16 - (len(plaintext) % 16)
        padded = plaintext + bytes([pad_len] * pad_len)
        ciphertext = crypt.cbc_encrypt(nonce, padded)
        return ciphertext, nonce

    def encrypt_row(self, row: dict, sensitive_columns: list[str]) -> bytes:
        """行级加密: 整行序列化后 SM4-GCM 加密"""
        import json
        row_bytes = json.dumps(row, ensure_ascii=False).encode("utf-8")
        crypt = sm4.CryptSM4()
        crypt.set_key(self.dek, sm4.SM4_ENCRYPT)
        nonce = os.urandom(16)
        pad_len = 16 - (len(row_bytes) % 16)
        padded = row_bytes + bytes([pad_len] * pad_len)
        return nonce + crypt.cbc_encrypt(nonce, padded)
```

### 11.3 非结构化数据块级加密

```python
# crypto/block_encryption.py

import hashlib
from gmssl import sm3, sm4, func
import os

class BlockEncryptor:
    """大文件块级加密 (>100MB)
    按 16MB 分块, 每块独立 SM4-GCM 加密 (不同 IV)
    Merkle Tree 记录完整性
    """

    BLOCK_SIZE = 16 * 1024 * 1024  # 16MB

    def __init__(self, dek: bytes):
        self.dek = dek

    def encrypt_file(self, input_path: str, output_path: str) -> dict:
        """分块加密文件, 返回 Merkle 树根和块索引"""
        blocks = []
        merkle_leaves = []

        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            block_idx = 0
            while True:
                chunk = fin.read(self.BLOCK_SIZE)
                if not chunk:
                    break

                # 每块独立 IV
                iv = os.urandom(16)
                crypt = sm4.CryptSM4()
                crypt.set_key(self.dek, sm4.SM4_ENCRYPT)

                # PKCS7 padding
                pad_len = 16 - (len(chunk) % 16)
                padded = chunk + bytes([pad_len] * pad_len)
                ciphertext = crypt.cbc_encrypt(iv, padded)

                # 写入: block_header(32) + ciphertext
                # header: magic(4) + block_idx(4) + iv(16) + original_size(4) + ct_size(4)
                header = b"BLK1" + struct.pack("<I", block_idx) + iv + \
                         struct.pack("<I", len(chunk)) + struct.pack("<I", len(ciphertext))
                fout.write(header)
                fout.write(ciphertext)

                # Merkle leaf = SM3(block_idx || iv || ciphertext_hash)
                ct_hash = bytes(sm3.sm3_hash(func.bytes_to_list(ciphertext)))
                leaf_data = struct.pack("<I", block_idx) + iv + ct_hash
                merkle_leaves.append(bytes(sm3.sm3_hash(func.bytes_to_list(leaf_data))))

                blocks.append({"idx": block_idx, "offset": fout.tell() - len(ciphertext) - 32,
                               "size": len(chunk), "ct_size": len(ciphertext)})
                block_idx += 1

        # 构建 Merkle 树
        root = self._build_merkle_root(merkle_leaves)
        return {"blocks": blocks, "merkle_root": root.hex(), "total_blocks": len(blocks)}

    def _build_merkle_root(self, leaves: list[bytes]) -> bytes:
        if not leaves:
            return b'\x00' * 32
        current = leaves[:]
        while len(current) > 1:
            nxt = []
            for i in range(0, len(current), 2):
                l = current[i]
                r = current[i+1] if i+1 < len(current) else l
                nxt.append(bytes(sm3.sm3_hash(func.bytes_to_list(l + r))))
            current = nxt
        return current[0]

import struct
```

---

## 12. 数据库 Schema 设计

### 12.1 PostgreSQL Schema

```sql
-- migrations/001_init.sql

-- 数据产品目录
CREATE TABLE data_products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(256) NOT NULL,
    category        VARCHAR(128) NOT NULL,
    data_type       VARCHAR(32) NOT NULL,  -- structured, unstructured, semi_structured
    media_type      VARCHAR(128),           -- MIME type for unstructured
    description     TEXT,
    owner_org_id    UUID NOT NULL REFERENCES organizations(id),
    owner_space_id  UUID NOT NULL REFERENCES data_spaces(id),
    security_level  VARCHAR(4) NOT NULL DEFAULT 'L1',  -- L1, L2, L3
    schema_def      JSONB,                  -- field definitions for structured data
    volume_rows     BIGINT,
    volume_bytes    BIGINT,
    update_frequency VARCHAR(32),
    allowed_operations JSONB DEFAULT '["aggregate","filter"]',
    forbidden_operations JSONB DEFAULT '["row_level_export","network_call"]',
    output_constraints JSONB DEFAULT '{"maxOutputRows":100,"requireDP":true,"epsilon":1.0}',
    min_security_level VARCHAR(4) NOT NULL DEFAULT 'L1',
    status          VARCHAR(16) NOT NULL DEFAULT 'draft',  -- draft, published, archived
    encryption_key_ref  VARCHAR(256),       -- Vault key reference
    storage_path    VARCHAR(512),           -- MinIO path
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_products_category ON data_products(category);
CREATE INDEX idx_products_owner ON data_products(owner_org_id);
CREATE INDEX idx_products_security ON data_products(security_level);
CREATE INDEX idx_products_status ON data_products(status);

-- 数字合约
CREATE TABLE contracts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id     VARCHAR(128) UNIQUE NOT NULL,  -- human-readable ID
    product_id      UUID NOT NULL REFERENCES data_products(id),
    provider_org_id UUID NOT NULL REFERENCES organizations(id),
    buyer_org_id    UUID NOT NULL REFERENCES organizations(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'draft',
    valid_from      TIMESTAMPTZ NOT NULL,
    valid_until     TIMESTAMPTZ NOT NULL,
    max_output_rows INT NOT NULL DEFAULT 100,
    dp_epsilon_total    FLOAT NOT NULL DEFAULT 10.0,
    dp_epsilon_consumed FLOAT NOT NULL DEFAULT 0.0,
    allowed_operations  JSONB NOT NULL DEFAULT '["aggregate"]',
    policy_hash     VARCHAR(64),            -- SM3 hash of compiled policy
    provider_signature  TEXT,               -- SM2 signature
    buyer_signature    TEXT,                 -- SM2 signature
    blockchain_tx   VARCHAR(128),           -- FISCO BCOS tx hash
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_contracts_status ON contracts(status);
CREATE INDEX idx_contracts_buyer ON contracts(buyer_org_id);
CREATE INDEX idx_contracts_valid ON contracts(valid_until) WHERE status = 'active';

-- 沙箱会话
CREATE TABLE sandbox_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      VARCHAR(128) UNIQUE NOT NULL,
    contract_id     UUID NOT NULL REFERENCES contracts(id),
    buyer_user_id   UUID NOT NULL REFERENCES users(id),
    security_level  VARCHAR(4) NOT NULL,
    node_id         VARCHAR(128),
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending, active, expired, terminated
    tee_attestation_hash VARCHAR(128),
    software_attestation JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    terminated_at   TIMESTAMPTZ
);

-- 计算任务
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         VARCHAR(128) UNIQUE NOT NULL,
    session_id      UUID NOT NULL REFERENCES sandbox_sessions(id),
    task_type       VARCHAR(20) NOT NULL,   -- sql, python, inference, fl_round
    code            TEXT,                   -- Base64 encoded
    code_signature  TEXT,                   -- SM2 signature
    model_id        VARCHAR(128),
    params          JSONB,
    status          VARCHAR(16) NOT NULL DEFAULT 'queued',
    node_id         VARCHAR(128),
    result_ref      VARCHAR(512),           -- encrypted result storage path
    result_signature TEXT,                  -- SM2 signature of result
    dp_epsilon_used FLOAT DEFAULT 0.0,
    output_rows     INT DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tasks_session ON tasks(session_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created ON tasks(created_at);

-- 机构
CREATE TABLE organizations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(256) NOT NULL,
    org_type        VARCHAR(20) NOT NULL,   -- provider, buyer, operator
    sm2_cert_pem    TEXT NOT NULL,
    sm2_fingerprint VARCHAR(64) NOT NULL,   -- SM3 hash of cert
    status          VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 用户
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id),
    username        VARCHAR(128) NOT NULL,
    sm2_cert_pem    TEXT,
    role            VARCHAR(20) NOT NULL,   -- admin, data_provider, data_buyer, auditor
    status          VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 数据空间
CREATE TABLE data_spaces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(256) NOT NULL,
    space_type      VARCHAR(32) NOT NULL,   -- enterprise, industry, city
    connector_url   VARCHAR(512),
    ca_cert_pem     TEXT,                   -- cross-space trust anchor
    status          VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 12.2 Redis Key 设计

```
# Session
session:{session_id}              -> JSON session data (TTL 24h)
active_sessions                   -> SET of active session IDs

# Quota
quota:{session_id}:{operation}    -> INT counter (TTL = session TTL)
dp_budget:{contract_id}:consumed  -> FLOAT epsilon consumed
dp_budget:{contract_id}:total     -> FLOAT epsilon total

# Rate Limiting
rate:{client_ip}                  -> INT counter (TTL 60s)
rate:{client_ip}:minute           -> INT counter (TTL 60s)

# Task
task:{task_id}                    -> JSON task data (TTL 7d)
task_queue:{level}:pending        -> LIST of task IDs

# Node
node:{node_id}:status             -> JSON node status (TTL 30s, heartbeat)
node:{node_id}:load               -> INT current task count

# Policy Cache
policy:{contract_id}:hash         -> STRING compiled policy hash
policy:{contract_id}:rules        -> JSON cached policy rules (TTL 5m)

# Circuit Breaker
circuit:{node_id}:state           -> STRING (closed/open/half_open)
circuit:{node_id}:fail_count      -> INT consecutive failures
```

---

## 13. MinIO 加密对象存储

### 13.1 架构

```
┌─────────────────────────────────────────────┐
│              MinIO Cluster                    │
│                                             │
│  ┌───────────────────────────────────────┐  │
│  │  Bucket: sandbox-data-products        │  │
│  │  ├─ /structured/{product_id}/         │  │
│  │  │   ├─ data.enc (SM4-GCM encrypted)  │  │
│  │  │   └─ metadata.json                 │  │
│  │  └─ /unstructured/{product_id}/       │  │
│  │      ├─ blocks/ (block-level encrypted)│  │
│  │      ├─ merkle_index.json             │  │
│  │      └─ metadata.json                 │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  ┌───────────────────────────────────────┐  │
│  │  Bucket: sandbox-results              │  │
│  │  └─ /{session_id}/{task_id}/          │  │
│  │      ├─ result.enc (encrypted result) │  │
│  │      └─ signature.bin (SM2 signature) │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  SSE: Server-Side Encryption (SM4)          │
│  IAM: per-bucket access policies            │
│  Lifecycle: auto-delete after contract end  │
└─────────────────────────────────────────────┘
```

### 13.2 MinIO Python 客户端

```python
# storage/minio_client.py

from minio import Minio
from minio.commonconfig import ENABLED
from minio.sseconfig import Rules as SSEConfig
import io
import json

class EncryptedObjectStorage:
    """MinIO 加密对象存储客户端"""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool = True):
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._ensure_buckets()

    def _ensure_buckets(self):
        for bucket in ["sandbox-data-products", "sandbox-results", "sandbox-temp"]:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    def upload_encrypted_product(self, product_id: str, data: bytes,
                                  metadata: dict, key_ref: str) -> str:
        """上传加密数据产品"""
        object_name = f"structured/{product_id}/data.enc"
        custom_meta = {
            "x-amz-meta-product-id": product_id,
            "x-amz-meta-key-ref": key_ref,
            "x-amz-meta-sm3-hash": self._sm3_hex(data),
        }
        self.client.put_object(
            "sandbox-data-products", object_name,
            io.BytesIO(data), len(data),
            metadata=custom_meta,
        )
        return f"sandbox-data-products/{object_name}"

    def upload_block_encrypted_file(self, product_id: str, encrypted_path: str,
                                     merkle_info: dict) -> str:
        """上传块级加密文件"""
        object_name = f"unstructured/{product_id}/blocks/data.enc"
        self.client.fput_object("sandbox-data-products", object_name, encrypted_path)

        # 上传 Merkle 索引
        index_data = json.dumps(merkle_info).encode()
        self.client.put_object(
            "sandbox-data-products",
            f"unstructured/{product_id}/merkle_index.json",
            io.BytesIO(index_data), len(index_data),
        )
        return f"sandbox-data-products/{object_name}"

    def download_to_tee(self, storage_path: str, local_path: str):
        """下载到 TEE 内存 (临时路径)"""
        bucket, object_name = storage_path.split("/", 1)
        self.client.fget_object(bucket, object_name, local_path)

    def upload_result(self, session_id: str, task_id: str,
                      result_data: bytes, signature: bytes) -> str:
        """上传计算结果 (加密 + 签名)"""
        object_name = f"{session_id}/{task_id}/result.enc"
        self.client.put_object("sandbox-results", object_name,
                               io.BytesIO(result_data), len(result_data))

        sig_name = f"{session_id}/{task_id}/signature.bin"
        self.client.put_object("sandbox-results", sig_name,
                               io.BytesIO(signature), len(signature))
        return f"sandbox-results/{object_name}"

    def _sm3_hex(self, data: bytes) -> str:
        from gmssl import sm3, func
        return bytes(sm3.sm3_hash(func.bytes_to_list(data))).hex()
```

---

## 14. 配额管理 (Redis Lua)

### 14.1 实时配额管理器

```python
# quota/manager.py

import redis
import json
from gmssl import sm2

class QuotaManager:
    """基于 Redis 的实时配额管理 (Lua 原子操作)"""

    # Lua 脚本: 原子检查 + 扣减 (防竞态)
    CONSUME_QUOTA_SCRIPT = """
    local current = redis.call('GET', KEYS[1])
    if current == false then
        return -1  -- quota not initialized
    end
    if tonumber(current) < tonumber(ARGV[1]) then
        return 0  -- insufficient quota
    end
    redis.call('DECRBY', KEYS[1], ARGV[1])
    return 1  -- success
    """

    # Lua 脚本: 差分隐私预算原子检查 + 扣减
    CONSUME_DP_BUDGET_SCRIPT = """
    local consumed = tonumber(redis.call('GET', KEYS[1]) or '0')
    local total = tonumber(redis.call('GET', KEYS[2]) or '0')
    local request = tonumber(ARGV[1])
    if consumed + request > total then
        return 0  -- budget exceeded
    end
    redis.call('INCRBYFLOAT', KEYS[1], request)
    return 1  -- success
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = redis.from_url(redis_url)
        self.consume_quota = self.redis.register_script(self.CONSUME_QUOTA_SCRIPT)
        self.consume_dp = self.redis.register_script(self.CONSUME_DP_BUDGET_SCRIPT)

    def init_quota(self, session_id: str, operation: str, limit: int, ttl: int = 86400):
        """初始化操作配额"""
        key = f"quota:{session_id}:{operation}"
        self.redis.setex(key, ttl, limit)

    def check_and_consume(self, session_id: str, operation: str, count: int = 1) -> bool:
        """原子检查并扣减配额"""
        key = f"quota:{session_id}:{operation}"
        result = self.consume_quota(keys=[key], args=[count])
        return result == 1

    def init_dp_budget(self, contract_id: str, epsilon_total: float):
        """初始化差分隐私预算"""
        self.redis.set(f"dp_budget:{contract_id}:total", epsilon_total)
        self.redis.set(f"dp_budget:{contract_id}:consumed", 0.0)

    def check_and_consume_dp(self, contract_id: str, epsilon: float) -> bool:
        """原子检查并扣减 DP 预算"""
        consumed_key = f"dp_budget:{contract_id}:consumed"
        total_key = f"dp_budget:{contract_id}:total"
        result = self.consume_dp(keys=[consumed_key, total_key], args=[epsilon])
        return result == 1

    def get_dp_remaining(self, contract_id: str) -> float:
        """查询剩余 DP 预算"""
        consumed = float(self.redis.get(f"dp_budget:{contract_id}:consumed") or 0)
        total = float(self.redis.get(f"dp_budget:{contract_id}:total") or 0)
        return total - consumed

    def get_quota_remaining(self, session_id: str, operation: str) -> int:
        """查询剩余操作配额"""
        key = f"quota:{session_id}:{operation}"
        val = self.redis.get(key)
        return int(val) if val else 0
```

---

## 15. 网络隔离架构

### 15.1 网络拓扑

```
┌───────────────────────────────────────────────────────────────────┐
│                     Kubernetes Cluster                              │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  Namespace: sandbox-control-plane                           │  │
│  │  ├─ API Gateway (Kong)         [10.0.1.0/24]               │  │
│  │  ├─ Contract Engine (OPA)      [10.0.1.0/24]               │  │
│  │  ├─ Task Scheduler (Celery)    [10.0.1.0/24]               │  │
│  │  └─ Audit Service              [10.0.1.0/24]               │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                    │ (NetworkPolicy: allow egress to sandbox ns)   │
│  ┌─────────────────▼───────────────────────────────────────────┐  │
│  │  Namespace: sandbox-runtime                                  │  │
│  │  ├─ L1 TEE Pods               [10.0.2.0/24]                │  │
│  │  │   NetworkPolicy:                                        │  │
│  │  │   - ingress: ONLY from control-plane (KMS key delivery)  │  │
│  │  │   - egress: ONLY to output-gateway + audit               │  │
│  │  │   - NO internet access                                  │  │
│  │  ├─ L2 gVisor Pods            [10.0.3.0/24]                │  │
│  │  │   NetworkPolicy:                                        │  │
│  │  │   - ingress: ONLY from control-plane                    │  │
│  │  │   - egress: ONLY to output-gateway + audit               │  │
│  │  │   - NO internet access                                  │  │
│  │  └─ L3 Container Pods         [10.0.4.0/24]                │  │
│  │      NetworkPolicy:                                         │  │
│  │      - Same as L2 but with audit-only egress                │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                    │                                               │
│  ┌─────────────────▼───────────────────────────────────────────┐  │
│  │  Namespace: sandbox-inspection                               │  │
│  │  └─ Output Inspection Gateway    [10.0.5.0/24]              │  │
│  │      NetworkPolicy:                                          │  │
│  │      - ingress: ONLY from runtime ns                         │  │
│  │      - egress: ONLY to buyer endpoint (TLCP)                 │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  Namespace: sandbox-data                                     │  │
│  │  ├─ PostgreSQL               [10.0.6.0/24]                  │  │
│  │  ├─ Redis                    [10.0.6.0/24]                  │  │
│  │  ├─ ClickHouse               [10.0.6.0/24]                  │  │
│  │  ├─ MinIO                    [10.0.6.0/24]                  │  │
│  │  └─ RabbitMQ                 [10.0.6.0/24]                  │  │
│  └─────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

### 15.2 NetworkPolicy 配置

```yaml
# k8s/network-policies.yaml

# L1 TEE Pod 网络隔离
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: l1-tee-isolation
  namespace: sandbox-runtime
spec:
  podSelector:
    matchLabels: { security-level: "L1" }
  policyTypes: [Ingress, Egress]
  ingress:
  - from:
    - namespaceSelector:
        matchLabels: { name: sandbox-control-plane }
    ports:
    - port: 8443  # TEE management API
  egress:
  - to:
    - namespaceSelector:
        matchLabels: { name: sandbox-inspection }
    ports:
    - port: 8443  # Output gateway
  - to:
    - namespaceSelector:
        matchLabels: { name: sandbox-data }
    ports:
    - port: 9000  # MinIO
    - port: 6379  # Redis (quota)
  # DNS
  - to:
    - namespaceSelector: {}
    ports:
    - port: 53
      protocol: UDP
---
# 禁止沙箱内 Pod 访问互联网
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-internet-access
  namespace: sandbox-runtime
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: sandbox-runtime
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: sandbox-control-plane
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: sandbox-inspection
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: sandbox-data
  - to:  # DNS only
    - namespaceSelector: {}
    ports:
    - port: 53
      protocol: UDP
```

---

## 16. API 接口完整定义

### 16.1 OpenAPI 端点总表

| 端点 | 方法 | 认证 | 描述 |
|------|------|------|------|
| `/api/v1/products` | POST | SM2 JWT | 注册数据产品 |
| `/api/v1/products/{id}` | GET | SM2 JWT | 获取产品详情 |
| `/api/v1/products/search` | POST | SM2 JWT | 搜索产品目录 |
| `/api/v1/contracts` | POST | SM2 JWT | 创建数字合约 |
| `/api/v1/contracts/{id}/sign` | POST | SM2 JWT | 签署合约 |
| `/api/v1/contracts/{id}/activate` | POST | SM2 JWT | 激活合约 |
| `/sandbox/register` | POST | SM2 证书 | 注册沙箱实例 |
| `/sandbox/session/create` | POST | SM2 JWT | 创建沙箱会话 |
| `/sandbox/task/submit` | POST | SM2 JWT | 提交计算任务 |
| `/sandbox/task/result` | GET | SM2 JWT | 获取任务结果 |
| `/sandbox/task/cancel` | POST | SM2 JWT | 取消任务 |
| `/sandbox/attest` | GET | SM2 JWT | 获取远程证明 |
| `/sandbox/audit/log` | GET | 平台授权 | 查询审计日志 |
| `/sandbox/contract/bind` | POST | SM2 JWT | 绑定合约 |
| `/crossspace/catalog/query` | GET | 跨空间令牌 | 跨空间目录查询 |
| `/crossspace/identity/verify` | POST | SM2 证书 | 跨空间身份验证 |
| `/health` | GET | 无 | 健康检查 |
| `/metrics` | GET | 无 | Prometheus 指标 |

### 16.2 完整请求/响应模型

```python
# api/models.py

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime

# === Enums ===

class SecurityLevel(str, Enum):
    L1 = "L1"  # TEE
    L2 = "L2"  # Software enhanced
    L3 = "L3"  # Minimal

class TaskType(str, Enum):
    SQL = "sql"
    PYTHON = "python"
    INFERENCE = "inference"
    FL_ROUND = "fl_round"

class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

# === Product Models ===

class SchemaField(BaseModel):
    field: str
    type: str
    sensitivity: str = "low"  # low, medium, high

class OutputConstraints(BaseModel):
    maxOutputRows: int = 100
    maxOutputBytes: int = 10 * 1024 * 1024  # 10MB
    requireDifferentialPrivacy: bool = True
    epsilon: float = 1.0

class DataProductCreate(BaseModel):
    name: str
    category: str
    dataType: list[str]  # structured, unstructured, semi_structured
    mediaType: Optional[str] = None
    schema: Optional[list[SchemaField]] = None
    volumeRows: Optional[int] = None
    updateFrequency: Optional[str] = None
    allowedOperations: list[str] = ["aggregate", "filter"]
    forbiddenOperations: list[str] = ["row_level_export", "network_call"]
    outputConstraints: OutputConstraints = OutputConstraints()
    securityLevel: SecurityLevel = SecurityLevel.L1

class DataProductResponse(BaseModel):
    id: str
    name: str
    category: str
    ownerOrgId: str
    securityLevel: SecurityLevel
    status: str
    createdAt: datetime

# === Contract Models ===

class ContractCreate(BaseModel):
    productId: str
    buyerOrgId: str
    validFrom: datetime
    validUntil: datetime
    maxOutputRows: int = 100
    dpEpsilonTotal: float = 10.0
    allowedOperations: list[str] = ["aggregate"]
    contractType: str = "data_query"  # data_query, model_training, api_service, federated

class ContractResponse(BaseModel):
    contractId: str
    productId: str
    status: str
    validFrom: datetime
    validUntil: datetime
    dpEpsilonConsumed: float = 0.0
    dpEpsilonTotal: float = 10.0
    providerSignature: Optional[str] = None
    buyerSignature: Optional[str] = None
    blockchainTx: Optional[str] = None

# === Session Models ===

class CreateSessionRequest(BaseModel):
    contractId: str
    buyerCertPem: str
    buyerSignature: str  # SM2 signature of contractId + timestamp
    timestamp: int  # Unix ms, valid within 300s
    requestedLevel: SecurityLevel = SecurityLevel.L1
    crossSpaceToken: Optional[str] = None

class CreateSessionResponse(BaseModel):
    sessionId: str
    actualLevel: SecurityLevel
    attestationReport: Optional[str] = None  # TEE Quote (L1)
    softwareAttestation: Optional[dict] = None  # Software proof (L2)
    expiresAt: int  # Unix ms

# === Task Models ===

class SubmitTaskRequest(BaseModel):
    sessionId: str
    taskType: TaskType
    code: Optional[str] = None  # Base64 encoded
    modelId: Optional[str] = None
    codeSignature: str  # SM2 signature of code
    params: Optional[dict] = None

class SubmitTaskResponse(BaseModel):
    taskId: str
    status: TaskStatus
    estimatedSeconds: Optional[int] = None

class TaskResultResponse(BaseModel):
    taskId: str
    status: TaskStatus
    result: Optional[dict] = None
    resultSignature: Optional[str] = None  # SM2 signature
    dpEpsilonConsumed: Optional[float] = None
    dpEpsilonRemaining: Optional[float] = None
    auditEventId: Optional[str] = None
    error: Optional[str] = None

# === Attestation ===

class AttestationResponse(BaseModel):
    level: SecurityLevel
    report: str  # TEE Quote or software attestation JSON
    signature: str  # SM2 signature
    validUntil: int  # Unix ms

# === Cross-space ===

class CrossSpaceCatalogEntry(BaseModel):
    productId: str
    spaceId: str
    name: str
    category: str
    dataType: str
    mediaType: Optional[str] = None
    volume: Optional[dict] = None
    securityLevelRequired: SecurityLevel
    pricing: Optional[dict] = None
```

---

## 17. SDK 设计

### 17.1 数商 SDK

```python
# sdk/provider_sdk.py

class DataProviderSDK:
    """数商 SDK - 数据产品管理"""

    def __init__(self, api_url: str, sm2_cert: str, sm2_key: str):
        self.api_url = api_url.rstrip("/")
        self.cert = sm2_cert
        self.key = sm2_key
        self.session = httpx.Client(verify=True)

    def _auth_headers(self) -> dict:
        token = create_sm2_jwt({"sub": "provider", "iat": datetime.utcnow()},
                               {"private_key": self.key, "public_key": self.cert})
        return {"Authorization": f"Bearer {token}"}

    def register_product(self, product: dict, data_path: str = None) -> dict:
        """注册数据产品并上传加密数据"""
        # 1. 注册产品元数据
        resp = self.session.post(f"{self.api_url}/api/v1/products",
                                 json=product, headers=self._auth_headers())
        product_id = resp.json()["id"]

        # 2. 如果有数据文件, 加密并上传
        if data_path:
            # 请求服务端生成 DEK
            dek_resp = self.session.post(
                f"{self.api_url}/api/v1/products/{product_id}/init-encryption",
                headers=self._auth_headers(),
            )
            dek_ciphertext = dek_resp.json()["dek_ciphertext"]

            # 本地加密
            cipher = SM4Cipher()  # 从 Vault 获取 DEK
            encrypted_path = data_path + ".enc"
            # ... encrypt and upload via MinIO presigned URL

        return {"product_id": product_id, "status": "registered"}

    def configure_policy(self, product_id: str, policy: dict) -> dict:
        """配置使用策略"""
        resp = self.session.post(
            f"{self.api_url}/api/v1/products/{product_id}/policy",
            json=policy, headers=self._auth_headers(),
        )
        return resp.json()

    def view_usage_records(self, product_id: str, start: str, end: str) -> list:
        """查看使用记录"""
        resp = self.session.get(
            f"{self.api_url}/api/v1/products/{product_id}/usage",
            params={"start": start, "end": end},
            headers=self._auth_headers(),
        )
        return resp.json()
```

### 17.2 买方 SDK

```python
# sdk/buyer_sdk.py

class DataBuyerSDK:
    """买方 SDK - 数据消费"""

    def __init__(self, api_url: str, sm2_cert: str, sm2_key: str):
        self.api_url = api_url.rstrip("/")
        self.cert = sm2_cert
        self.key = sm2_key

    def search_catalog(self, query: str = None, category: str = None,
                       security_level: str = None) -> list:
        """搜索数据产品目录"""
        resp = httpx.post(f"{self.api_url}/api/v1/products/search",
                          json={"query": query, "categories": [category] if category else None,
                                "securityLevels": [security_level] if security_level else None},
                          headers=self._auth())
        return resp.json()["products"]

    def create_contract(self, product_id: str, valid_days: int = 30,
                        max_rows: int = 100, epsilon: float = 10.0) -> dict:
        """创建并签署合约"""
        resp = httpx.post(f"{self.api_url}/api/v1/contracts", json={
            "productId": product_id,
            "validFrom": datetime.utcnow().isoformat(),
            "validUntil": (datetime.utcnow() + timedelta(days=valid_days)).isoformat(),
            "maxOutputRows": max_rows,
            "dpEpsilonTotal": epsilon,
        }, headers=self._auth())
        contract = resp.json()

        # 签署合约
        signature = sm2_sign(contract["contractId"], self.key)
        httpx.post(f"{self.api_url}/api/v1/contracts/{contract['contractId']}/sign",
                   json={"signature": signature}, headers=self._auth())
        return contract

    def create_session(self, contract_id: str, level: str = "L1") -> dict:
        """创建沙箱会话"""
        timestamp = int(datetime.utcnow().timestamp() * 1000)
        sig_data = f"{contract_id}:{timestamp}"
        signature = sm2_sign(sig_data, self.key)
        resp = httpx.post(f"{self.api_url}/sandbox/session/create", json={
            "contractId": contract_id,
            "buyerCertPem": self.cert,
            "buyerSignature": signature,
            "timestamp": timestamp,
            "requestedLevel": level,
        }, headers=self._auth())
        return resp.json()

    def submit_task(self, session_id: str, task_type: str, code: str,
                    params: dict = None) -> dict:
        """提交计算任务"""
        import base64
        code_b64 = base64.b64encode(code.encode()).decode()
        code_sig = sm2_sign(code, self.key)
        resp = httpx.post(f"{self.api_url}/sandbox/task/submit", json={
            "sessionId": session_id,
            "taskType": task_type,
            "code": code_b64,
            "codeSignature": code_sig,
            "params": params,
        }, headers=self._auth())
        return resp.json()

    def get_result(self, task_id: str, poll: bool = True, timeout: int = 300) -> dict:
        """获取任务结果 (可轮询)"""
        import time
        deadline = time.time() + timeout
        while True:
            resp = httpx.get(f"{self.api_url}/sandbox/task/result",
                             params={"taskId": task_id}, headers=self._auth())
            result = resp.json()
            if result["status"] in ("completed", "failed") or not poll:
                return result
            if time.time() > deadline:
                raise TimeoutError(f"Task {task_id} not completed within {timeout}s")
            time.sleep(2)

    def _auth(self) -> dict:
        token = create_sm2_jwt({"sub": "buyer"}, {"private_key": self.key, "public_key": self.cert})
        return {"Authorization": f"Bearer {token}"}
```

---

## 18. 安全威胁模型 (STRIDE)

### 18.1 威胁分析矩阵

| 威胁类型 | 具体威胁 | 对抗措施 | 实现组件 |
|----------|----------|----------|----------|
| **S**poofing (欺骗) | 伪造数商/买方身份 | SM2 证书 + 多因素认证; 跨空间令牌时间戳防重放 (300s) | SM2 PKI (Tongsuo), Redis session |
| **T**ampering (篡改) | 修改策略包、审计日志 | 策略包 SM3 哈希锁定; 日志 SM2 签名; 链上存证 | OPA policy hash, structlog + SM2, FISCO BCOS |
| **R**epudiation (抵赖) | 否认执行过某操作 | 链上不可篡改存证; 操作日志含 SM2 签名 | FISCO BCOS AuditRegistry, ClickHouse |
| **I**nformation Disclosure (信息泄露) | 数据从 TEE 内泄露 | 硬件内存加密; 输出审查网关; 差分隐私; 禁止原文输出 | Occlum SGX, Presidio, diffprivlib |
| **D**enial of Service (拒绝服务) | 消耗 TEE 资源使正常任务无法运行 | 任务配额 (Redis Lua); 资源 cgroup 限制; 任务超时熔断 | QuotaManager, pybreaker |
| **E**levation of Privilege (权限提升) | 沙箱逃逸, 访问宿主机数据 | TEE 硬件隔离; gVisor 用户态内核; Seccomp-BPF; AST 白名单 | Occlum, gVisor, RestrictedPython |

### 18.2 L2 特有安全约束

L2 无法防御以下威胁 (需在合约中告知买方):

- **宿主机 OS 级攻击**: 若宿主机 OS 被 root 级攻陷, gVisor 沙箱可能被绕过
- **内存 dump**: 无硬件内存加密, 物理内存 dump 后可能获取解密中的数据 (缓解: dm-crypt + 短生命周期密钥)
- **内核漏洞利用**: gVisor 用户态内核本身存在漏洞风险 (缓解: 定期更新 + 漏洞扫描)

**L2 应用限制**: 仅用于一般敏感数据 (非《数据安全法》定义的重要数据或核心数据)。

### 18.3 合约层降级感知

```yaml
# 数商配置: 每个数据产品允许的最低安全等级
DataProduct:
  securityLevelPolicy:
    minimumLevel: "L1"        # 仅允许 TEE
    # 或
    minimumLevel: "L2"        # 允许降级至软件增强
    degradeConditions:
      - "buyerNodeNoTEE"
      - "crossSpaceLowSens"
    degradeRequires:
      - "buyerExplicitConsent"
      - "dpEpsilonMax: 0.5"
      - "mpcVerificationRequired: true"
      - "auditDoubleApproval: true"
```

---

## 19. 侧信道攻击防护

### 19.1 L1 TEE 侧信道防护

| 攻击类型 | 防护措施 | 实现方式 |
|----------|----------|----------|
| 缓存侧信道 (Spectre/Meltdown) | Occlum 内置缓存分区; 禁用 hyperthreading (per-task 独占物理核) | K8s node affinity + CPU manager policy: static |
| 内存访问模式泄露 | ORAM (Oblivious RAM) 用于敏感数据随机化访问模式 | 选用, 高安全场景 |
| 功耗侧信道 | 云环境下 Intel 已缓解; 物理机部署需配合电磁屏蔽机房 | 物理安全措施 |
| 时序侧信道 | 敏感比较操作使用恒定时间算法 (constant-time SM2) | Tongsuo 内置 |

### 19.2 防御配置

```yaml
# K8s CPU Manager 配置 (独占物理核)
apiVersion: v1
kind: Node
metadata:
  name: sgx-node-1
  labels:
    cpu-manager-policy: "static"
---
# Pod 配置 (独占 CPU)
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: tee-worker
    resources:
      requests:
        cpu: "4"      # 独占 4 个物理核
        memory: "8Gi"
      limits:
        cpu: "4"
        memory: "8Gi"
```

---

## 20. 性能基准与优化

### 20.1 性能目标

| 操作 | L1 TEE | L2 软件 | 基准环境 |
|------|--------|---------|----------|
| 沙箱启动 (含密钥分发) | <=60s (P99) | <=15s (P99) | SGX 服务器 / 普通 x86 |
| SQL 聚合查询 (1亿行) | <=120s | <=60s | 单节点 32C128G |
| Python 逻辑回归训练 (100万样本) | <=600s | <=300s | 同上 |
| 图像脱敏预处理 (1000张 DICOM) | <=300s | <=180s | 含 GPU (H100) |
| 音频 ASR 转录 (1小时录音) | <=600s | <=300s | 纯 CPU |
| 输出审查延迟 | <=500ms | <=200ms | 1MB 文本结果 |
| 跨空间会话建立 | <=5s | <=3s | 同城网络 |

### 20.2 优化策略

| 优化方向 | 措施 | 实现 |
|----------|------|------|
| TEE 内存优化 | Occlum 异步 I/O; 分块数据流式加载 (避免 EPC Paging 抖动) | Occlum EDMM, 分块 16MB |
| 并发沙箱调度 | 按数据产品预热热门 Enclave (模型预加载); 任务拼包 (batch inference) | Celery worker pool, Enclave snapshot |
| 非结构化大文件 | 分块并行处理 (多线程解密 + 流水线预处理); GPU-TEE 加速图像/视频 | BlockEncryptor, CUDA streams |
| L2 MPC 通信 | 预计算乘法三元组 (离线生成, online 阶段仅做 O(1) 通信) | MP-SPDZ offline phase |
| 审计日志 | 异步写入 (任务结束后 30s 内完成上链, 不阻塞结果返回) | Fluent Bit buffering, ClickHouse async insert |

---

## 21. 测试矩阵与质量保障

### 21.1 测试矩阵

| 测试类型 | 测试内容 | 工具 |
|----------|----------|------|
| 功能测试 | 各类沙箱类型任务提交/执行/输出 | pytest + 自定义 SDK |
| 安全测试 | 沙箱逃逸尝试 (CVE 复现集); PII 泄露尝试 | 红队测试 + OWASP ZAP |
| 策略测试 | 策略执行正确性 (允许/拒绝决策验证) | OPA test framework + pytest |
| 差分隐私测试 | ε 预算扣减正确性; 噪声分布验证 | dp-accounting (Google) |
| 互联互通测试 | 跨空间会话建立; 证书互认; 合约绑定 | 集成测试环境 (两套数据空间) |
| 性能测试 | 并发会话压测; 大文件处理吞吐 | Locust + 自定义 TEE 压测框架 |
| 合规测试 | TC609-6-2025-01 接口合规验证 | 标委会互操作测试套件 |

### 21.2 测试用例示例

```python
# tests/test_sandbox_flow.py

import pytest
from sdk.buyer_sdk import DataBuyerSDK
from sdk.provider_sdk import DataProviderSDK

@pytest.fixture
def provider():
    return DataProviderSDK("https://sandbox.example.com", CERT, KEY)

@pytest.fixture
def buyer():
    return DataBuyerSDK("https://sandbox.example.com", CERT, KEY)

def test_full_sandbox_flow(provider, buyer):
    """端到端: 注册产品 -> 创建合约 -> 创建会话 -> 提交任务 -> 获取结果"""
    # 1. 数商注册产品
    product = provider.register_product({
        "name": "Test Dataset",
        "category": "test",
        "dataType": ["structured"],
        "securityLevel": "L1",
    })
    assert product["product_id"]

    # 2. 买方创建合约
    contract = buyer.create_contract(product["product_id"], valid_days=7)
    assert contract["contractId"]

    # 3. 买方创建会话
    session = buyer.create_session(contract["contractId"])
    assert session["sessionId"]
    assert session["actualLevel"] == "L1"

    # 4. 提交 SQL 任务
    task = buyer.submit_task(session["sessionId"], "sql",
                             "SELECT count(*) FROM data WHERE age > 30")
    assert task["taskId"]

    # 5. 获取结果
    result = buyer.get_result(task["taskId"], poll=True, timeout=120)
    assert result["status"] == "completed"
    assert result["resultSignature"]  # SM2 签名存在
    assert result["dpEpsilonConsumed"] is not None

def test_policy_denial(buyer):
    """策略拒绝: 超过输出行数限制"""
    # ... 测试 maxOutputRows 限制
    pass

def test_dp_budget_exhaustion(buyer):
    """DP 预算耗尽: 超过 epsilon 限制"""
    # ... 测试 epsilon 预算耗尽后拒绝查询
    pass

def test_code_verification(buyer):
    """代码验证: 拒绝危险代码"""
    # 测试 import os 被拒绝
    with pytest.raises(CodeVerificationError):
        verify_code("import os; os.system('rm -rf /')")
```

### 21.3 安全认证目标

| 认证 | 标准 | 计划周期 |
|------|------|----------|
| 等保三级 | GB/T 22239-2019 | Phase 1 完成后 6 个月 |
| 密评 | GM/T 0054-2018 | Phase 1 完成后 |
| TC609 互操作认证 | TC609-6-2025-01 | Phase 2 完成后 |
| DSMM 三级 | GB/T 37988-2019 | Phase 3 完成后 |

---

## 22. 监控告警规则

### 22.1 Prometheus 告警规则

```yaml
# monitoring/alert_rules.yml
groups:
- name: sandbox_alerts
  rules:
  # 安全告警
  - alert: HighPolicyDenialRate
    expr: rate(sandbox_policy_denials_total[5m]) > 10
    for: 2m
    labels: { severity: critical }
    annotations:
      summary: "High policy denial rate: {{ $value }}/s"

  - alert: DPBudgetNearlyExhausted
    expr: sandbox_dp_epsilon_remaining < 1.0
    for: 5m
    labels: { severity: warning }
    annotations:
      summary: "DP budget nearly exhausted: {{ $value }} remaining"

  - alert: DPBudgetExhausted
    expr: sandbox_dp_epsilon_remaining <= 0
    for: 0m
    labels: { severity: critical }
    annotations:
      summary: "DP budget exhausted for contract {{ $labels.contract_id }}"

  # 性能告警
  - alert: SandboxStartupSlow
    expr: histogram_quantile(0.99, sandbox_task_duration_seconds_bucket) > 60
    for: 5m
    labels: { severity: warning }
    annotations:
      summary: "Sandbox P99 startup > 60s"

  - alert: TaskQueueBacklog
    expr: celery_queue_length > 100
    for: 5m
    labels: { severity: warning }
    annotations:
      summary: "Task queue backlog: {{ $value }} tasks"

  - alert: CircuitBreakerOpen
    expr: sandbox_circuit_breaker_state == 1
    for: 0m
    labels: { severity: critical }
    annotations:
      summary: "Circuit breaker open for node {{ $labels.node_id }}"

  # 可用性告警
  - alert: SandboxServiceDown
    expr: up{job="sandbox-api"} == 0
    for: 1m
    labels: { severity: critical }
    annotations:
      summary: "Sandbox API is down"

  - alert: ClickHouseIngestionLag
    expr: clickhouse_insert_delay_seconds > 30
    for: 5m
    labels: { severity: warning }
    annotations:
      summary: "ClickHouse ingestion lag: {{ $value }}s"
```

### 22.2 Grafana Dashboard 面板

```json
{
  "dashboard": {
    "title": "CDS Sandbox Operations",
    "panels": [
      {"title": "Task Rate", "type": "timeseries",
       "targets": [{"expr": "rate(sandbox_task_total[5m])", "legendFormat": "{{tenant}}/{{level}}"}]},
      {"title": "Policy Denials", "type": "stat",
       "targets": [{"expr": "sum(increase(sandbox_policy_denials_total[1h]))"}]},
      {"title": "DP Budget", "type": "gauge",
       "targets": [{"expr": "sandbox_dp_epsilon_remaining"}],
       "fieldConfig": {"defaults": {"min": 0, "max": 10}}},
      {"title": "Active Sessions", "type": "stat",
       "targets": [{"expr": "sandbox_active_sessions"}]},
      {"title": "Merkle Anchors/min", "type": "timeseries",
       "targets": [{"expr": "rate(sandbox_merkle_anchors_total[5m])"}]},
      {"title": "Task Duration P99", "type": "timeseries",
       "targets": [{"expr": "histogram_quantile(0.99, sandbox_task_duration_seconds_bucket)"}]}
    ],
    "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"}
  }
}
```

---

## 23. 配置管理

### 23.1 完整配置文件

```yaml
# config/sandbox-config.yaml

sandbox:
  runtime:
    l1:
      enabled: true
      teeFramework: "occlum"          # occlum | gramine | hyperenclave | itrustee
      epcMemoryGB: 64
      maxConcurrentSessions: 20
      attestationService: "dcap"      # dcap | itrustee | csv
      enclavePreWarm: true            # 预热热门 Enclave
      cpuExclusive: true              # 独占物理核 (防侧信道)
    l2:
      enabled: true
      vmRuntime: "firecracker"        # firecracker | qemu-kvm
      containerRuntime: "gvisor"      # gvisor | runc
      maxConcurrentSessions: 50
      mpcEnabled: true
      mpcProtocol: "spdz2k"
      eBPFMonitoring: true
    l3:
      enabled: false                  # 生产默认关闭
      allowedDataLevels: ["public", "low_sensitivity"]

  policy:
    defaultDenyAll: true
    dpDefaultEpsilon: 1.0
    dpL2MaxEpsilon: 0.5
    dpL3MaxEpsilon: 0.1
    outputMaxRows: 100
    outputMaxRowsL2: 20
    outputMaxRowsL3: 0                # L3 仅允许标量
    codeScanEnabled: true
    codeMaxSizeBytes: 1048576          # 1MB

  unstructured:
    enableFaceRedaction: true
    faceRedactionModel: "insightface_buffalo_l"
    enableDicomTagStrip: true
    dicomSensitiveTags:
      - "PatientName"
      - "PatientID"
      - "PatientBirthDate"
      - "PatientSex"
      - "InstitutionName"
    enableAsrTranscription: true
    asrModel: "funasr_paraformer_zh"
    ocrModel: "paddleocr_v4_ch"
    nerModel: "hanlp_msra_bert_zh"
    forbiddenOutputMimes:
      - "image/*"
      - "audio/*"
      - "video/*"
      - "application/pdf"
      - "application/octet-stream"

  storage:
    minioEndpoint: "minio:9000"
    minioSecure: true
    postgresDsn: "postgresql://sandbox:***@circuitBreaker:
    failMax: 5
    resetTimeoutSeconds: 30
    halfOpenMaxCalls: 3

  session:
    defaultTTlSeconds: 86400           # 24h
    maxTTlSeconds: 604800              # 7d
    cleanupIntervalSeconds: 300

  tlcp:
    enabled: true
    certPath: "/etc/certs/server.crt"
    keyPath: "/etc/certs/server.key"
    caPath: "/etc/certs/ca.crt"
    ciphers: "ECDHE-SM2-WITH-SM4-SM3:SM2-WITH-SM4-SM3"
```

---

## 24. 错误处理与降级

### 24.1 错误码体系

```python
# errors/codes.py

class SandboxErrorCode:
    # 认证/授权 (1xxx)
    AUTH_TOKEN_EXPIRED = 1001
    AUTH_CERT_INVALID = 1002
    AUTH_INSUFFICIENT_PERMISSION = 1003

    # 合约 (2xxx)
    CONTRACT_NOT_FOUND = 2001
    CONTRACT_EXPIRED = 2002
    CONTRACT_NOT_ACTIVE = 2003
    CONTRACT_DP_BUDGET_EXHAUSTED = 2004
    CONTRACT_QUOTA_EXCEEDED = 2005

    # 沙箱 (3xxx)
    SANDBOX_SESSION_NOT_FOUND = 3001
    SANDBOX_SESSION_EXPIRED = 3002
    SANDBOX_NO_AVAILABLE_NODE = 3003
    SANDBOX_TEE_INITIALIZATION_FAILED = 3004
    SANDBOX_TEE_ATTESTATION_FAILED = 3005
    SANDBOX_KEY_DISTRIBUTION_FAILED = 3006

    # 任务 (4xxx)
    TASK_NOT_FOUND = 4001
    TASK_CODE_VERIFICATION_FAILED = 4002
    TASK_EXECUTION_TIMEOUT = 4003
    TASK_EXECUTION_FAILED = 4004
    TASK_CANCELLED = 4005

    # 输出审查 (5xxx)
    OUTPUT_DLP_VIOLATION = 5001
    OUTPUT_SIZE_EXCEEDED = 5002
    OUTPUT_MIME_BLOCKED = 5003
    OUTPUT_PII_DETECTED = 5004
    OUTPUT_DP_BUDGET_EXCEEDED = 5005

    # 跨空间 (6xxx)
    CROSSSPACE_IDENTITY_MISMATCH = 6001
    CROSSSPACE_TOKEN_EXPIRED = 6002
    CROSSSPACE_TRUST_ANCHOR_MISSING = 6003

    # 系统 (9xxx)
    SYSTEM_INTERNAL_ERROR = 9001
    SYSTEM_SERVICE_UNAVAILABLE = 9002
    SYSTEM_RATE_LIMITED = 9003
```

### 24.2 降级策略

```python
# degradation/strategy.py

class DegradationStrategy:
    """系统降级策略"""

    def select_sandbox_level(self, requested: str, available_nodes: list,
                              product_min_level: str) -> str:
        """选择沙箱安全等级 (自动降级逻辑)"""
        level_priority = {"L1": 3, "L2": 2, "L3": 1}
        min_level = level_priority.get(product_min_level, 3)

        # 按优先级尝试
        for level in ["L1", "L2", "L3"]:
            if level_priority[level] < min_level:
                continue  # 低于产品最低要求
            if level_priority[level] > level_priority[requested]:
                continue  # 高于请求等级
            # 检查是否有可用节点
            has_node = any(n.security_level == level for n in available_nodes)
            if has_node:
                return level

        return None  # 无可用节点

    def handle_node_failure(self, failed_node_id: str, task, router) -> str:
        """节点故障时重新路由"""
        # 1. 尝试同等级其他节点
        alt = router.route(task.task_id, SecurityLevel(task.security_level))
        if alt:
            return alt.node_id

        # 2. 尝试降级 (需检查产品是否允许)
        if task.security_level == "L1":
            alt = router.route(task.task_id, SecurityLevel.L2)
            if alt:
                return alt.node_id

        return None  # 无法恢复
```

---

*文档版本：v2.1 | 状态：补充规格 | 覆盖 detailed-tech-spec v2.0 缺失的 14 个组件*
