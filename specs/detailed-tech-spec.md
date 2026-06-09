# 密态沙箱系统 · 详细技术规格说明书（Detailed Technical Spec）

> 版本：v2.0
> 对齐标准：TC609-6-2025-01、GM/T 国密系列、NDI-TR-2025-02
> 受众：系统架构师、后端工程师、DevOps 工程师
> 基于：product-spec v1.1 + tech-spec v1.0 + 开源技术调研

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [KMS & Identity/PKI 子系统](#2-kms--identitypki-子系统)
3. [Contract Engine 子系统](#3-contract-engine-子系统)
4. [Task Scheduler 子系统](#4-task-scheduler-子系统)
5. [Sandbox Runtime 子系统](#5-sandbox-runtime-子系统)
6. [Output Inspection Gateway 子系统](#6-output-inspection-gateway-子系统)
7. [Audit & Provenance 子系统](#7-audit--provenance-子系统)
8. [Unstructured Data Pipeline](#8-unstructured-data-pipeline)
9. [Interconnection Connector](#9-interconnection-connector)
10. [集成架构与部署](#10-集成架构与部署)

---

## 1. 系统架构总览

### 1.1 技术栈全景

```
┌─────────────────────────────────────────────────────────────────────┐
│                         接入层 (FastAPI + Kong)                       │
│  数商 SDK (Python)    买方 SDK (Python)    管理控制台 (React)        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS/TLCP (Tongsuo)
┌──────────────────────────────▼──────────────────────────────────────┐
│                    Interconnection Connector (FastAPI)                │
│  Session Mgmt (Redis)    SM2 JWT Auth    Catalog (PostgreSQL)       │
└────────┬─────────────────────┬──────────────────────────────────────┘
         │                     │
┌────────▼──────┐  ┌──────────▼───────────────────────────────────────┐
│ Contract Engine│  │              Data Plane                          │
│ (OPA + FISCO) │  │  ┌─────────────────────────────────────────┐    │
│               │  │  │  Task Scheduler (Celery + Pulsar)         │    │
│ Policy: OPA   │  │  │  ├─ L1 Queue (TEE tasks)                  │    │
│ State: FISCO  │  │  │  ├─ L2 Queue (gVisor tasks)               │    │
│ Lifecycle:    │  │  │  └─ L3 Queue (Container tasks)             │    │
│  SM2 signed   │  │  └────────────────────┬────────────────────┘    │
│               │  │                       │                          │
│               │  │  ┌────────────────────▼────────────────────┐    │
│               │  │  │  Sandbox Runtime                         │    │
│               │  │  │  ├─ L1: Occlum/Gramine (SGX)            │    │
│               │  │  │  ├─ L2: Firecracker + gVisor             │    │
│               │  │  │  └─ L3: Docker + Seccomp                 │    │
│               │  │  └────────────────────┬────────────────────┘    │
│               │  │                       │                          │
│               │  │  ┌────────────────────▼────────────────────┐    │
│               │  │  │  Output Inspection Gateway               │    │
│               │  │  │  ├─ DLP (Presidio + Custom CN recognizers)│   │
│               │  │  │  ├─ DP Budget (diffprivlib)               │   │
│               │  │  │  ├─ Watermark (Zero-width Unicode + LSB)  │   │
│               │  │  │  └─ NER (HanLP / PaddleNLP)               │   │
│               │  │  └──────────────────────────────────────────┘    │
└───────────────┘  └──────────────────────────────────────────────────┘
         │                     │
┌────────▼─────────────────────▼──────────────────────────────────────┐
│                    Audit & Provenance (ClickHouse + FISCO BCOS)       │
│  structlog -> Fluent Bit -> ClickHouse -> Merkle Tree -> FISCO BCOS  │
│  Prometheus + Grafana (metrics)                                      │
└─────────────────────────────────────────────────────────────────────┘
         │
┌────────▼────────────────────────────────────────────────────────────┐
│                    Storage & Key Layer                                │
│  KMS: Vault Transit + Tongsuo (SM2/SM4)    HSM: PKCS#11             │
│  Object Storage: MinIO (SM4-GCM)    DB: PostgreSQL + Redis          │
│  Message Queue: Apache Pulsar                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 开源组件选型总表

| 子系统 | 主选组件 | 备选组件 | License |
|--------|----------|----------|---------|
| KMS | HashiCorp Vault Transit | - | MPL-2.0 |
| 国密算法 | Tongsuo (铜锁) | GmSSL | Apache-2.0 |
| PKI/CA | Vault PKI + Tongsuo | cfssl | MPL-2.0 |
| 策略引擎 | OPA (Rego) | Balana (XACML) | Apache-2.0 |
| 区块链 | FISCO BCOS | - | GPL-3.0 |
| 区块链 SDK | FISCO BCOS Python SDK | WeBASE-Front | GPL-3.0 |
| 任务队列 | Celery + RabbitMQ | Temporal.io | BSD-3 |
| 消息队列 | Apache Pulsar | Apache Kafka | Apache-2.0 |
| 熔断器 | pybreaker + tenacity | - | MIT / Apache-2.0 |
| 状态机 | transitions | - | MIT |
| TEE L1 | Occlum | Gramine | BSD-3 / LGPL |
| VM L2 | Firecracker | - | Apache-2.0 |
| 容器沙箱 | gVisor (runsc) | - | Apache-2.0 |
| MPC | MP-SPDZ | - | BSD-3 |
| 代码验证 | RestrictedPython + AST | - | ZPL |
| DLP | Microsoft Presidio | - | MIT |
| 差分隐私 | IBM diffprivlib | OpenDP | MIT |
| 水印 | Stegano + 自研 Unicode | - | GPL-3.0 |
| 中文 NER | HanLP | PaddleNLP | Apache-2.0 |
| 中文 OCR | PaddleOCR | - | Apache-2.0 |
| 中文 ASR | FunASR (Paraformer) | Whisper | MIT |
| 人脸检测 | InsightFace (SCRFD) | - | MIT |
| 视频处理 | FFmpeg + OpenCV + YOLOv8 | - | Apache-2.0 |
| 审计日志 | ClickHouse + structlog | - | Apache-2.0 |
| 日志收集 | Fluent Bit | Vector | Apache-2.0 |
| 监控 | Prometheus + Grafana | - | Apache-2.0 |
| Session | Redis (redis-py) | - | MIT |
| API 框架 | FastAPI | - | MIT |
| TLCP | Tongsuo (系统 OpenSSL) | - | Apache-2.0 |
| JWT | PyJWT + SM2 自定义算法 | - | MIT |
| 跨链 | WeCross | - | Apache-2.0 |

### 1.3 Python 依赖清单

```bash
# requirements.txt - 全量依赖

# === 核心框架 ===
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
pydantic>=2.0
celery[redis]>=5.3.0
redis>=5.0.0
httpx>=0.27.0

# === 国密算法 ===
gmssl>=3.2.2                    # SM2/SM3/SM4 (pure Python, for testing)
# Production: install Tongsuo as system OpenSSL

# === KMS & PKI ===
hvac>=2.0.0                     # HashiCorp Vault Python client

# === 状态机 & 熔断 ===
transitions>=0.9.0
pybreaker>=1.0.0
tenacity>=8.2.0

# === 消息队列 ===
pulsar-client>=3.4.0            # Apache Pulsar

# === DLP & Differential Privacy ===
presidio-analyzer>=2.2.0
presidio-anonymizer>=2.2.0
diffprivlib>=0.6.0

# === Watermark ===
Stegano>=0.11.0

# === Image Processing ===
opencv-python>=4.9.0
Pillow>=10.0.0
insightface>=0.7.0
imagehash>=4.3.0
pydicom>=2.4.0

# === Document Processing ===
paddlepaddle-gpu>=2.6.0         # or paddlepaddle (CPU)
paddleocr>=2.7.0
PyMuPDF>=1.24.0
pdfplumber>=0.11.0
python-docx>=1.1.0

# === NLP ===
hanlp>=1.0.0
paddlenlp>=2.7.0

# === Audio Processing ===
funasr>=1.0.0                   # Alibaba FunASR (Chinese ASR)
librosa>=0.10.0
soundfile>=0.12.0
pyannote.audio>=3.1.0           # Speaker diarization (needs HF token)

# === Video Processing ===
ultralytics>=8.1.0              # YOLOv8
# FFmpeg via system install (apt install ffmpeg)

# === Semi-structured ===
ijson>=3.2.0
lxml>=5.1.0
orjson>=3.9.0

# === Audit & Monitoring ===
clickhouse-driver>=0.2.7
structlog>=24.1.0
prometheus-client>=0.20.0

# === JWT ===
PyJWT>=2.8.0

# === Database ===
asyncpg>=0.29.0
sqlalchemy>=2.0.0
alembic>=1.13.0
```

---

## 2. KMS & Identity/PKI 子系统

### 2.1 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                KMS & Identity Subsystem                   │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Vault Transit│  │ Tongsuo SM2  │  │  Vault PKI    │  │
│  │ (AES enc)   │  │ (SM2/SM4)    │  │  (Cert lifecycle)│
│  └──────┬──────┘  └──────┬───────┘  └──────┬────────┘  │
│         │                │                  │           │
│  ┌──────▼────────────────▼──────────────────▼────────┐  │
│  │           Key Lifecycle Manager                    │  │
│  │  generate -> encrypt -> distribute -> rotate -> revoke│
│  └──────────────────────┬────────────────────────────┘  │
│                         │                               │
│  ┌──────────────────────▼────────────────────────────┐  │
│  │           HSM (PKCS#11) - Master Key Protection    │  │
│  │  Thales Luna / Huawei KMS / Alibaba Cloud KMS     │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Vault Transit API

**核心端点:**

| 端点 | 方法 | 用途 |
|------|------|------|
| `/transit/keys/{name}` | POST | 创建命名加密密钥 |
| `/transit/encrypt/{name}` | POST | 加密明文 |
| `/transit/decrypt/{name}` | POST | 解密密文 |
| `/transit/hmac/{name}` | POST | HMAC 计算 |
| `/transit/sign/{name}` | POST | 签名 |
| `/transit/verify/{name}` | POST | 验签 |
| `/transit/keys/{name}/rotate` | POST | 密钥轮转 |
| `/transit/keys/{name}/config` | POST | 配置 (自动轮转等) |
| `/transit/datakey/plaintext/{name}` | POST | 生成信封加密 datakey |
| `/transit/rewrap/{name}` | POST | 用最新版本重新加密 |

**Python 客户端 (hvac):**

```python
import hvac

class VaultKMS:
    def __init__(self, url: str, token: str):
        self.client = hvac.Client(url=url, token=token)

    def create_dek(self, product_id: str) -> str:
        """Create DEK for data product"""
        key_name = f"sandbox-dek-{product_id}"
        self.client.secrets.transit.create_key(
            name=key_name, type="aes256-gcm96",
            exportable=False, allow_plaintext_backup=False,
        )
        return key_name

    def generate_data_key(self, product_id: str) -> tuple:
        """Envelope encryption: returns (plaintext_key, ciphertext_key)"""
        key_name = f"sandbox-dek-{product_id}"
        result = self.client.secrets.transit.generate_data_key(
            name=key_name, key_type="plaintext",
        )
        return result["data"]["plaintext"], result["data"]["ciphertext"]

    def encrypt(self, product_id: str, plaintext: bytes) -> str:
        import base64
        key_name = f"sandbox-dek-{product_id}"
        result = self.client.secrets.transit.encrypt(
            name=key_name,
            plaintext=base64.b64encode(plaintext).decode(),
        )
        return result["data"]["ciphertext"]

    def decrypt(self, product_id: str, ciphertext: str) -> bytes:
        import base64
        key_name = f"sandbox-dek-{product_id}"
        result = self.client.secrets.transit.decrypt(
            name=key_name, ciphertext=ciphertext,
        )
        return base64.b64decode(result["data"]["plaintext"])

    def rotate_key(self, product_id: str):
        self.client.secrets.transit.rotate_key(name=f"sandbox-dek-{product_id}")

    def revoke_key(self, product_id: str):
        key_name = f"sandbox-dek-{product_id}"
        self.client.secrets.transit.update_key_configuration(
            name=key_name, deletion_allowed=True,
        )
        self.client.secrets.transit.delete_key(name=key_name)
```

**Vault HSM Seal 配置 (Enterprise):**

```hcl
# vault.hcl
seal "pkcs11" {
  lib            = "/usr/lib/libCryptoki2_64.so"
  slot           = "0"
  pin            = "..."
  key_label      = "vault-hsm-key"
  mechanism      = "0x0001"  # CKM_RSA_PKCS_OAEP
}
```

### 2.3 Tongsuo SM2 PKI

**Tongsuo** (github.com/Tongsuo-Project/Tongsuo) - OpenSSL fork with native SM2/SM3/SM4/TLCP support.

```bash
# Build Tongsuo
git clone https://github.com/Tongsuo-Project/Tongsuo.git
cd Tongsuo && ./Configure --prefix=/usr/local/tongsuo && make -j$(nproc) && make install
```

**SM2 CA 操作:**

```bash
# Generate SM2 CA key + self-signed cert
tongsuo ecparam -genkey -name SM2 -out ca.key
tongsuo req -new -x509 -key ca.key -out ca.crt \
  -subj "/CN=SM2 Root CA" -days 3650 -sm3

# Issue SM2 certificate
tongsuo ecparam -genkey -name SM2 -out server.key
tongsuo req -new -key server.key -out server.csr -sm3
tongsuo x509 -req -in server.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out server.crt -days 365 -sm3
```

**Python CA 管理类:**

```python
import subprocess, tempfile, os
from pathlib import Path

class SM2CertificateAuthority:
    def __init__(self, ca_key: str, ca_cert: str, bin: str = "tongsuo"):
        self.ca_key, self.ca_cert, self.bin = ca_key, ca_cert, bin

    def _run(self, args):
        result = subprocess.run([self.bin] + args, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Tongsuo error: {result.stderr}")
        return result.stdout

    @classmethod
    def initialize(cls, ca_dir: str, cn: str = "CDS Root CA"):
        os.makedirs(ca_dir, exist_ok=True)
        ca_key, ca_cert = os.path.join(ca_dir, "ca.key"), os.path.join(ca_dir, "ca.crt")
        subprocess.run(["tongsuo", "ecparam", "-genkey", "-name", "SM2", "-out", ca_key], check=True)
        subprocess.run(["tongsuo", "req", "-new", "-x509", "-key", ca_key,
                        "-out", ca_cert, "-subj", f"/CN={cn}", "-days", "3650", "-sm3"], check=True)
        return cls(ca_key, ca_cert)

    def issue_certificate(self, cn: str, org: str, days: int = 365):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "client.key")
            csr_path = os.path.join(d, "client.csr")
            cert_path = os.path.join(d, "client.crt")
            self._run(["ecparam", "-genkey", "-name", "SM2", "-out", key_path])
            self._run(["req", "-new", "-key", key_path, "-out", csr_path,
                       "-subj", f"/CN={cn}/O={org}", "-sm3"])
            self._run(["x509", "-req", "-in", csr_path,
                       "-CA", self.ca_cert, "-CAkey", self.ca_key,
                       "-CAcreateserial", "-out", cert_path, "-days", str(days), "-sm3"])
            return Path(cert_path).read_text(), Path(key_path).read_text()
```

### 2.4 SM4 加密工具

```python
from gmssl import sm4 as gmssl_sm4
import os

class SM4Cipher:
    def __init__(self, key: bytes = None):
        self.key = key or os.urandom(16)

    def encrypt_cbc(self, plaintext: bytes) -> tuple:
        crypt = gmssl_sm4.CryptSM4()
        crypt.set_key(self.key, gmssl_sm4.SM4_ENCRYPT)
        pad_len = 16 - (len(plaintext) % 16)
        padded = plaintext + bytes([pad_len] * pad_len)
        iv = os.urandom(16)
        return crypt.cbc_encrypt(iv, padded), iv

    def decrypt_cbc(self, ciphertext: bytes, iv: bytes) -> bytes:
        crypt = gmssl_sm4.CryptSM4()
        crypt.set_key(self.key, gmssl_sm4.SM4_DECRYPT)
        decrypted = crypt.cbc_decrypt(iv, ciphertext)
        return decrypted[:-decrypted[-1]]
```

---

## 3. Contract Engine 子系统

### 3.1 架构

```
Contract Lifecycle:
  DRAFT -> PENDING_APPROVAL -> APPROVED -> SIGNED -> ACTIVE
    -> SUSPENDED / EXPIRED -> TERMINATED -> ARCHIVED

Components:
  - OPA (Rego): Policy Decision Point (PDP), REST API at :8181
  - FISCO BCOS: Smart contract state + immutable audit
  - State Machine: transitions library, SM2-signed transitions
```

### 3.2 OPA 策略引擎

**Rego 策略 (sandbox_policy.rego):**

```rego
package confidential.sandbox

default allow = false

allow {
    input.subject.contract_status == "active"
    time.now_ns() < input.subject.contract_expires_ns
}

allow {
    input.action.type == input.resource.allowed_operations[_]
}

allow {
    input.subject.security_clearance >= input.resource.required_security_level
}

allow {
    input.action.output_rows <= input.resource.max_output_rows
}
```

**Python 客户端:**

```python
import httpx

class OPAClient:
    def __init__(self, url: str = "http://localhost:8181"):
        self.base_url = url.rstrip("/")

    async def load_policy(self, policy_id: str, rego: str):
        async with httpx.AsyncClient() as c:
            await c.put(f"{self.base_url}/v1/policies/{policy_id}",
                        content=rego, headers={"Content-Type": "text/plain"})

    async def evaluate(self, policy_path: str, subject: dict,
                       resource: dict, action: dict) -> dict:
        async with httpx.AsyncClient() as c:
            resp = await c.post(f"{self.base_url}/v1/data/{policy_path}",
                                json={"input": {"subject": subject, "resource": resource, "action": action}})
            result = resp.json()
            return {"allow": result.get("result", {}).get("allow", False)}
```

### 3.3 FISCO BCOS 智能合约

**核心合约 (SandboxAuditRegistry.sol):**

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract SandboxAuditRegistry {
    struct AuditBatch {
        bytes32 merkleRoot;
        uint256 batchId;
        uint256 leafCount;
        uint256 timestamp;
        address submitter;
        string spaceId;
    }

    struct ContractRecord {
        string contractId;
        address dataProvider;
        address dataBuyer;
        string productId;
        uint256 validFrom;
        uint256 validUntil;
        uint8 status;  // 0=DRAFT, 1=ACTIVE, 2=SUSPENDED, 3=TERMINATED
        bytes32 policyHash;
    }

    mapping(uint256 => AuditBatch) public batches;
    mapping(string => ContractRecord) public contracts;
    mapping(bytes32 => bool) public rootExists;
    uint256 public batchCount;

    event BatchSubmitted(uint256 indexed batchId, bytes32 merkleRoot, uint256 timestamp);
    event ContractActivated(string contractId, uint256 validUntil);

    function submitBatch(bytes32 _merkleRoot, uint256 _leafCount, string memory _spaceId)
        external returns (uint256) {
        require(!rootExists[_merkleRoot], "Root already anchored");
        rootExists[_merkleRoot] = true;
        uint256 anchorId = batchCount++;
        batches[anchorId] = AuditBatch(_merkleRoot, anchorId, _leafCount,
            block.timestamp, msg.sender, _spaceId);
        emit BatchSubmitted(anchorId, _merkleRoot, block.timestamp);
        return anchorId;
    }

    function verifyRoot(bytes32 _merkleRoot) external view returns (bool) {
        return rootExists[_merkleRoot];
    }
}
```

**FISCO BCOS Python SDK (github.com/FISCO-BCOS/python-sdk):**

```python
from fisco_bcos.client import BcosClient

client = BcosClient("config.ini")
# Deploy contract
receipt = client.deploy(abi, bytecode)
contract_address = receipt["contractAddress"]
# Call contract
result = client.call(contract_address, abi, "submitBatch", [merkle_root, leaf_count, space_id])
```

### 3.4 合约生命周期状态机

```python
from transitions import Machine

class DigitalContract:
    states = ["draft", "pending_approval", "approved", "signed",
              "active", "suspended", "expired", "terminated", "archived"]

    transitions = [
        {"trigger": "submit",   "source": "draft",            "dest": "pending_approval"},
        {"trigger": "approve",  "source": "pending_approval",  "dest": "approved"},
        {"trigger": "sign",     "source": "approved",          "dest": "signed"},
        {"trigger": "activate", "source": "signed",            "dest": "active"},
        {"trigger": "suspend",  "source": "active",            "dest": "suspended"},
        {"trigger": "resume",   "source": "suspended",         "dest": "active"},
        {"trigger": "expire",   "source": "active",            "dest": "expired"},
        {"trigger": "terminate","source": ["active","suspended"],"dest": "terminated"},
        {"trigger": "archive",  "source": ["expired","terminated"],"dest": "archived"},
    ]

    def __init__(self, contract_data: dict):
        self.data = contract_data
        self.machine = Machine(model=self, states=self.states,
                               transitions=self.transitions, initial="draft")
```

---

## 4. Task Scheduler 子系统

### 4.1 架构

```
Apache Pulsar (message queue)
  -> task-events-l1 / task-events-l2 / task-events-l3
    -> Celery Workers (per security level)
      -> Node Router (uhashring, TEE-aware)
        -> Circuit Breaker (pybreaker + tenacity)
          -> Sandbox Node (execute task)
```

### 4.2 Celery 配置

```python
from celery import Celery
from kombu import Queue, Exchange

app = Celery("confidential_sandbox")
app.conf.broker_url = "amqp://sandbox:***@class SecurityLevel(str, Enum):
    L1 = "L1"  # TEE
    L2 = "L2"  # gVisor + Firecracker
    L3 = "L3"  # Docker + Seccomp

class NodeRouter:
    def __init__(self):
        self.nodes: dict[str, NodeInfo] = {}
        self.ring = None

    def register_node(self, node: NodeInfo):
        self.nodes[node.node_id] = node
        self._rebuild_ring()

    def _rebuild_ring(self):
        import uhashring
        self.ring = uhashring.HashRing(
            nodes={n: {"weight": self.nodes[n].weight} for n in self.nodes}
        )

    def route(self, task_id: str, level: SecurityLevel) -> NodeInfo | None:
        eligible = {nid: n for nid, n in self.nodes.items()
                    if n.current_load < n.max_concurrent
                    and (level == SecurityLevel.L3
                         or (level == SecurityLevel.L2 and n.security_level != SecurityLevel.L3)
                         or (level == SecurityLevel.L1 and n.has_sgx))}
        if not eligible:
            return None
        return min(eligible.values(), key=lambda n: n.current_load / n.max_concurrent)
```

### 4.4 熔断器

```python
import pybreaker
from tenacity import retry, stop_after_attempt, wait_exponential

# Node-level circuit breaker
tee_breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=30, name="tee-node")

@tee_breaker
def call_tee_node(node_host: str, payload: bytes) -> bytes:
    import httpx
    resp = httpx.post(f"https://{node_host}:8443/execute", content=payload, timeout=300)
    resp.raise_for_status()
    return resp.content

# Call-level retry
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def call_with_retry(node_host: str, payload: bytes) -> bytes:
    return call_tee_node(node_host, payload)
```

### 4.5 任务状态机

```python
from transitions import Machine
from datetime import datetime
import uuid

class Task:
    states = ["queued", "validating", "assigning", "running",
              "completed", "failed", "cancelled", "dead_letter"]

    transitions = [
        {"trigger": "validate",    "source": "queued",      "dest": "validating"},
        {"trigger": "assign",      "source": "validating",  "dest": "assigning"},
        {"trigger": "execute",     "source": "assigning",   "dest": "running"},
        {"trigger": "complete",    "source": "running",     "dest": "completed"},
        {"trigger": "fail",        "source": "running",     "dest": "failed"},
        {"trigger": "retry",       "source": "failed",      "dest": "queued"},
        {"trigger": "dead_letter", "source": "failed",      "dest": "dead_letter"},
        {"trigger": "cancel",      "source": ["queued","validating","assigning"], "dest": "cancelled"},
    ]

    def __init__(self, task_id=None, contract_id="", security_level="L1", task_type="sql"):
        self.task_id = task_id or str(uuid.uuid4())
        self.contract_id = contract_id
        self.security_level = security_level
        self.task_type = task_type
        self.created_at = datetime.utcnow()
        self.state_history = []
        self.machine = Machine(model=self, states=self.states,
                               transitions=self.transitions, initial="queued",
                               after_state_change=self._log)

    def _log(self):
        self.state_history.append({"state": self.state, "ts": datetime.utcnow().isoformat()})
```

### 4.6 K8s TEE 调度

```yaml
# Intel SGX Device Plugin
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: intel-sgx-plugin
spec:
  template:
    spec:
      containers:
      - name: sgx-plugin
        image: intel/intel-sgx-plugin:0.36.0
        args: ["-v=3", "-mode=plugin"]
        volumeMounts:
        - name: kubeletsockets
          mountPath: /var/lib/kubelet/device-plugins
      volumes:
      - name: kubeletsockets
        hostPath: { path: /var/lib/kubelet/device-plugins }
---
# L1 Task Pod
apiVersion: v1
kind: Pod
metadata:
  name: sandbox-l1-task
  labels: { security-level: "L1" }
spec:
  nodeSelector: { intel-sgx: "enabled" }
  containers:
  - name: worker
    image: sandbox/occlum-worker:latest
    resources:
      limits:
        intel.com/sgx-enclave: "1"
        intel.com/sgx-epc: "67108864"  # 64MB
```

---

## 5. Sandbox Runtime 子系统

### 5.1 三级沙箱对比

| 维度 | L1 (Occlum/Gramine) | L2 (Firecracker+gVisor) | L3 (Docker+Seccomp) |
|------|---------------------|-------------------------|---------------------|
| 隔离级别 | 硬件 TEE (SGX) | VM + 用户态内核 | 容器 + seccomp |
| 内存加密 | 硬件 (TME/SME) | 软件 dm-crypt | 无 |
| 远程证明 | DCAP Attestation | 软件可信证明 | 无 |
| 启动延迟 | ~60s | ~15s | ~5s |
| 性能损耗 | ~30% | ~10% | ~5% |
| 适用数据 | 重要/核心数据 | 一般敏感数据 | 低敏/公开数据 |

### 5.2 Occlum L1 管理

```python
import json, subprocess, os

class OcclumSandbox:
    def __init__(self, instance_dir: str, epc_mb: int = 256):
        self.dir = instance_dir
        self.epc_mb = epc_mb

    def initialize(self):
        subprocess.run(["occlum", "init"], cwd=self.dir, check=True)
        config = json.load(open(os.path.join(self.dir, "Occlum.json")))
        config["resource_limits"]["user_space_size"] = f"{self.epc_mb}MB"
        config["resource_limits"]["max_num_of_threads"] = 64
        config["metadata"]["debuggable"] = False
        json.dump(config, open(os.path.join(self.dir, "Occlum.json"), "w"), indent=2)

    def build(self):
        subprocess.run(["occlum", "build"], cwd=self.dir, check=True)

    def run(self, command: str, args: list = None, timeout: int = 600):
        return subprocess.run(["occlum", "run", command] + (args or []),
                              cwd=self.dir, capture_output=True, text=True, timeout=timeout)

    def cleanup(self):
        subprocess.run(["occlum", "cleanup"], cwd=self.dir, check=True)
```

**Occlum.json 示例:**

```json
{
    "resource_limits": {
        "user_space_size": "256MB",
        "kernel_space_heap_size": "32MB",
        "max_num_of_threads": 32
    },
    "process": {
        "default_stack_size": "4MB",
        "default_heap_size": "16MB"
    },
    "metadata": {
        "debuggable": false,
        "pkru": 0
    }
}
```

### 5.3 Gramine L1 配置

```toml
# manifest.toml
loader.log_level = "error"
libos.entrypoint = "/usr/bin/python3"

fs.mounts = [
    { type = "tmpfs", path = "/tmp" },
    { type = "encrypted", path = "/data", uri = "file:/data" },
]

sgx.enclave_size = "256M"
sgx.thread_num = 4
sgx.remote_attestation = "dcap"

sgx.trusted_files = [
    "file:/usr/bin/python3",
    "file:/usr/lib/python3/**",
    "file:/app/**",
]
```

```bash
gramine-manifest python3.manifest.template python3.manifest
gramine-sgx-sign --manifest python3.manifest --key enclave-key.pem --output python3.manifest.sgx
gramine-sgx python3
```

### 5.4 Firecracker L2 管理

```python
import requests_unixsocket, json, subprocess, os

class FirecrackerManager:
    def __init__(self, socket: str, kernel: str, rootfs: str):
        self.socket, self.kernel, self.rootfs = socket, kernel, rootfs
        self.session = requests_unixsocket.Session()
        self.base = f"http+unix://{socket.replace('/', '%2F')}"

    def start_process(self):
        if os.path.exists(self.socket): os.unlink(self.socket)
        return subprocess.Popen(["firecracker", "--api-sock", self.socket])

    def configure(self, vcpus=2, mem_mb=512):
        self.session.put(f"{self.base}/boot-source", json={
            "kernel_image_path": self.kernel,
            "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"})
        self.session.put(f"{self.base}/drives/rootfs", json={
            "drive_id": "rootfs", "path_on_host": self.rootfs,
            "is_root_device": True, "is_read_only": False})
        self.session.put(f"{self.base}/machine-config", json={
            "vcpu_count": vcpus, "mem_size_mib": mem_mb})

    def start(self):
        self.session.put(f"{self.base}/actions", json={"action_type": "InstanceStart"})

    def snapshot(self, snap_path, mem_path):
        self.session.put(f"{self.base}/snapshot/create", json={
            "snapshot_type": "Full", "snapshot_path": snap_path, "mem_file_path": mem_path})
```

### 5.5 gVisor K8s RuntimeClass

```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: { name: gvisor }
handler: runsc
scheduling:
  nodeSelector: { sandbox-type: "gvisor" }
---
apiVersion: v1
kind: Pod
metadata: { name: sandbox-l2-task }
spec:
  runtimeClassName: gvisor
  containers:
  - name: worker
    image: sandbox/worker:latest
    securityContext:
      runAsNonRoot: true
      runAsUser: 65534
      readOnlyRootFilesystem: true
      allowPrivilegeEscalation: false
      capabilities: { drop: ["ALL"] }
```

### 5.6 买方代码验证

```python
import ast
from RestrictedPython import compile_restricted, safe_globals

ALLOWED_NODES = {
    ast.Module, ast.FunctionDef, ast.Return, ast.Assign, ast.Name, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.Compare, ast.BoolOp, ast.If, ast.For, ast.While, ast.Break, ast.Continue,
    ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Subscript, ast.Call, ast.Attribute,
    ast.ListComp, ast.DictComp, ast.SetComp, ast.comprehension, ast.keyword,
}
BLOCKED_MODULES = {"os", "sys", "subprocess", "shutil", "socket", "http", "urllib",
                   "ctypes", "importlib", "pickle", "marshal", "signal", "multiprocessing"}
BLOCKED_BUILTINS = {"eval", "exec", "compile", "__import__", "globals", "locals",
                    "open", "input", "breakpoint", "exit"}

class ASTWhitelistChecker(ast.NodeVisitor):
    def generic_visit(self, node):
        if type(node) not in ALLOWED_NODES:
            raise SecurityError(f"Blocked: {type(node).__name__} at line {getattr(node,'lineno','?')}")
        super().generic_visit(node)
    def visit_Import(self, node):
        raise SecurityError("Import not allowed")
    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name.split(".")[0] in BLOCKED_MODULES:
                raise SecurityError(f"Blocked module: {alias.name}")
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_BUILTINS:
            raise SecurityError(f"Blocked function: {node.func.id}")
        super().generic_visit(node)

def verify_code(code: str) -> bool:
    tree = ast.parse(code)
    ASTWhitelistChecker().visit(tree)
    compile_restricted(code, "<sandbox>", "exec")
    return True
```

### 5.7 MPC (MP-SPDZ)

**MP-SPDZ** (github.com/data61/MP-SPDZ) - Multi-party computation framework.

```bash
# Two-party computation with MASCOT (malicious security)
make setup
Scripts/compile-run.py -E mascot tutorial
```

**MPC Program (Compiler/Programs/tutorial.py):**

```python
# MP-SPDZ Python dialect
a = sint.get_input_from(0)  # Party 0's input
b = sint.get_input_from(1)  # Party 1's input
c = a + b
d = a * b
print_ln("Sum: %s", c.reveal())
```

---

## 6. Output Inspection Gateway 子系统

### 6.1 审查流水线

```
TEE计算结果 -> 1.格式验证(python-magic) -> 2.DLP扫描(Presidio+HanLP)
  -> 3.DP预算(diffprivlib) -> 4.水印注入(Unicode/LSB) -> 5.SM2签名 -> 6.释放
```

### 6.2 DLP (Microsoft Presidio)

**Presidio** (github.com/microsoft/presidio) - 8.4k stars, PII detection framework.

```python
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern

# Custom Chinese recognizers
cn_id = PatternRecognizer(
    supported_entity="CN_IDENTITY_CARD", name="ChineseID",
    patterns=[Pattern(name="cn_id",
        regex=r"\b[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
        score=0.85)],
    supported_language="zh")

cn_mobile = PatternRecognizer(
    supported_entity="CN_MOBILE_PHONE", name="ChineseMobile",
    patterns=[Pattern(name="cn_mob", regex=r"\b1[3-9]\d{9}\b", score=0.75)],
    supported_language="zh")

cn_bank = PatternRecognizer(
    supported_entity="CN_BANK_CARD", name="ChineseBank",
    patterns=[Pattern(name="cn_bank", regex=r"\b(62\d{14,17})\b", score=0.7)],
    supported_language="zh")

analyzer = AnalyzerEngine()
for r in [cn_id, cn_mobile, cn_bank]:
    analyzer.registry.add_recognizer(r)

results = analyzer.analyze(text="ID 110101199003076531 phone 13812345678",
    language="zh", entities=["CN_IDENTITY_CARD","CN_MOBILE_PHONE","CN_BANK_CARD"])
```

### 6.3 差分隐私 (IBM diffprivlib)

**diffprivlib** (github.com/IBM/differential-privacy-library) - 913 stars.

```python
from diffprivlib import mechanisms
from diffprivlib.accountant import BudgetAccountant

# Budget tracking per contract
accountant = BudgetAccountant(epsilon=10.0, delta=1e-5)

with accountant:
    laplace = mechanisms.Laplace(epsilon=1.0, sensitivity=1.0)
    noisy_result = laplace.randomise(42.0)  # consumes epsilon=1.0

print(f"Remaining: {accountant.remaining()}")  # epsilon=9.0

# Reject when budget exhausted
try:
    with BudgetAccountant(epsilon=1.0, delta=1e-5):
        m = mechanisms.Laplace(epsilon=0.6, sensitivity=1.0)
        m.randomise(1)  # uses 0.6
        m.randomise(2)  # uses 0.6 -> total 1.2 > 1.0 -> ERROR
except Exception as e:
    print(f"Budget exceeded: {e}")
```

### 6.4 水印注入

**Text watermark (Zero-width Unicode):**

```python
ZWC_MAP = {'0': '\u200b', '1': '\u200c'}  # ZWSP=0, ZWNJ=1
ZWC_REVERSE = {'\u200b': '0', '\u200c': '1'}

def encode_text_watermark(text: str, watermark_id: str) -> str:
    binary = ''.join(format(ord(c), '08b') for c in watermark_id)
    watermark = ''.join(ZWC_MAP[b] for b in binary)
    return text[0] + watermark + text[1:] if text else watermark

def decode_text_watermark(text: str) -> str:
    binary = ''.join(ZWC_REVERSE[c] for c in text if c in ZWC_REVERSE)
    return ''.join(chr(int(binary[i:i+8], 2)) for i in range(0, len(binary), 8))
```

**Image watermark (LSB - Stegano):**

```python
from stegano import lsb
secret = lsb.hide("input.png", "session-abc123")
secret.save("watermarked.png")
message = lsb.reveal("watermarked.png")
```

### 6.5 中文 NER (HanLP)

**HanLP** (github.com/hankcs/HanLP) - 36.3k stars.

```python
import hanlp
ner = hanlp.load(hanlp.pretrained.ner.MSRA_NER_BERT_BASE_ZH)
entities = ner("张三在北京的中国银行工作，电话13812345678")
# Output: [('张三','NR'), ('北京','NS'), ('中国银行','NT')]
```

### 6.6 输出验证 (python-magic)

```python
import magic

mime = magic.Magic(mime=True)
detected = mime.from_file("output.csv")  # "text/csv"
# Block: image/*, audio/*, video/*, application/octet-stream
BLOCKED = {"image/png", "image/jpeg", "audio/wav", "video/mp4", "application/x-executable"}
if detected in BLOCKED:
    raise ValueError(f"Blocked MIME: {detected}")
```

---

## 7. Audit & Provenance 子系统

### 7.1 ClickHouse 审计 Schema

```sql
CREATE TABLE sandbox_audit.audit_log (
    event_id        UUID DEFAULT generateUUIDv4(),
    timestamp       DateTime64(3, 'Asia/Shanghai'),
    tenant_id       LowCardinality(String),
    user_id         String,
    session_id      String,
    contract_id     String DEFAULT '',
    action          LowCardinality(String),  -- data_read, task_submit, policy_check, export
    resource_type   LowCardinality(String),  -- dataset, model, task, result
    resource_id     String,
    source_ip       IPv4,
    policy_decision Enum8('allow'=1, 'deny'=2, 'audit'=3),
    policy_rule_id  String DEFAULT '',
    dp_epsilon      Float64 DEFAULT 0.0,
    output_rows     UInt32 DEFAULT 0,
    tee_level       Enum8('L1'=1, 'L2'=2, 'L3'=3),
    merkle_root     String DEFAULT '',
    merkle_batch_id UInt64 DEFAULT 0,
    checksum        String DEFAULT ''  -- SM3 hash for tamper detection
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (tenant_id, timestamp, user_id)
TTL timestamp + INTERVAL 1095 DELETE;  -- 3 years

-- Daily aggregate view
CREATE MATERIALIZED VIEW sandbox_audit.audit_daily_mv
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (tenant_id, day, action)
AS SELECT tenant_id, toDate(timestamp) AS day, action, policy_decision,
    count() AS event_count, sum(dp_epsilon) AS total_epsilon
FROM sandbox_audit.audit_log
GROUP BY tenant_id, day, action, policy_decision;
```

**ClickHouse Python Client:**

```python
from clickhouse_driver import Client

client = Client(host='clickhouse', port=9000, database='sandbox_audit')
events = [
    (str(uuid.uuid4()), datetime.now(), 'tenant_001', 'user_123',
     'sess_abc', 'data_read', 'dataset', 'ds_001', '192.168.1.1',
     1, 'rule-003', 0.5, 100, 1, '', 0),
]
client.execute('INSERT INTO audit_log (event_id, timestamp, tenant_id, user_id,'
    'session_id, action, resource_type, resource_id, source_ip,'
    'policy_decision, policy_rule_id, dp_epsilon, output_rows, tee_level,'
    'merkle_root, merkle_batch_id) VALUES', events)
# Performance: ~500K-1M rows/sec
```

### 7.2 SM3 Merkle Tree

```python
from gmssl import sm3, func

class SM3MerkleTree:
    def __init__(self):
        self.leaves = []
        self.layers = []

    @staticmethod
    def _hash(data: bytes) -> bytes:
        return bytes(sm3.sm3_hash(func.bytes_to_list(data)))

    def add_leaves_batch(self, data_list: list):
        for d in data_list:
            self.leaves.append(self._hash(d))
        self._rebuild()

    def _rebuild(self):
        if not self.leaves: self.layers = []; return
        self.layers = [self.leaves[:]]
        current = self.leaves[:]
        while len(current) > 1:
            nxt = []
            for i in range(0, len(current), 2):
                l, r = current[i], current[i+1] if i+1 < len(current) else current[i]
                nxt.append(self._hash(l + r))
            current = nxt
            self.layers.append(current)

    @property
    def root(self):
        return self.layers[-1][0].hex() if self.layers else ""
```

### 7.3 结构化日志 (structlog)

```python
import structlog
from contextvars import ContextVar

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

def add_audit_context(logger, method_name, event_dict):
    event_dict["correlation_id"] = correlation_id_var.get("")
    event_dict["service"] = "confidential-sandbox"
    event_dict["log_type"] = "audit"
    return event_dict

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        add_audit_context,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

audit_logger = structlog.get_logger("audit")
audit_logger.info("policy_check", decision="allow", rule_id="rule-003", dp_epsilon=0.5)
```

### 7.4 Fluent Bit -> ClickHouse

```ini
[SERVICE]
    Flush         5
    Log_Level     info

[INPUT]
    Name              tail
    Path              /var/log/containers/sandbox-*.log
    Parser            docker
    Tag               audit.*

[FILTER]
    Name          grep
    Match         audit.*
    Regex         log_type audit

[OUTPUT]
    Name            clickhouse
    Match           audit.*
    Host            clickhouse
    Port            8123
    Database        sandbox_audit
    Table           audit_log
```

### 7.5 Prometheus 指标

```python
from prometheus_client import Counter, Gauge, Histogram

TASK_TOTAL = Counter("sandbox_task_total", "Total tasks", ["tenant_id", "task_type", "status"])
POLICY_DENIALS = Counter("sandbox_policy_denials_total", "Policy denials", ["tenant_id", "rule_id"])
DP_EPSILON_REMAINING = Gauge("sandbox_dp_epsilon_remaining", "DP budget remaining", ["contract_id"])
MERKLE_ANCHORS = Counter("sandbox_merkle_anchors_total", "Merkle roots anchored")
```

---

## 8. Unstructured Data Pipeline

### 8.1 流水线总览

```
Format Detection (python-magic) -> Malware Scan (ClamAV) -> Router
  ├── image/*  -> InsightFace人脸检测 -> OpenCV模糊 -> imagehash pHash -> SM4加密
  ├── PDF/DOC  -> PyMuPDF解析 -> PaddleOCR中文OCR -> HanLP NER脱敏 -> SM4加密
  ├── audio/*  -> FunASR Paraformer中文ASR -> pyannote说话人分离 -> NER脱敏 -> SM4加密
  ├── video/*  -> FFmpeg解码 -> InsightFace人脸模糊 -> YOLOv8目标检测 -> FFmpeg重编码
  └── text/json/xml/csv -> 字段级敏感检测(正则+NER) -> 脱敏 -> SM4加密
```

### 8.2 各流水线核心库与性能

| 流水线 | 核心库 | GPU性能 | CPU性能 |
|--------|--------|---------|---------|
| 人脸检测 | InsightFace SCRFD (github.com/deepinsight/insightface) | ~20ms/帧 | ~150ms/帧 |
| 中文OCR | PaddleOCR PP-OCRv4 (github.com/PaddlePaddle/PaddleOCR) | ~50ms/页 | ~500ms/页 |
| 中文ASR | FunASR Paraformer (github.com/modelscope/FunASR) | ~30ms/秒 | ~200ms/秒 |
| 说话人分离 | pyannote.audio 3.1 (github.com/pyannote/pyannote-audio) | ~1x实时 | ~3x实时 |
| 目标检测 | YOLOv8n (github.com/ultralytics/ultralytics) | ~1ms/帧 | ~30ms/帧 |
| 视频编解码 | FFmpeg NVDEC/NVENC | ~500fps | ~30fps |
| 感知哈希 | imagehash (github.com/JohannesBuchner/imagehash) | N/A | ~5ms/图 |
| JSON流解析 | ijson (github.com/ICRAR/ijson) | N/A | ~10MB/s |
| DICOM | pydicom (github.com/pydicom/pydicom) | N/A | ~50ms/文件 |

### 8.3 图像处理

```python
import cv2, numpy as np
from insightface.app import FaceAnalysis
import imagehash
from PIL import Image

class ImagePipeline:
    def __init__(self):
        self.face_app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider","CPUExecutionProvider"])
        self.face_app.prepare(ctx_id=0, det_size=(640, 640))

    def process(self, image_bytes: bytes, config: dict) -> dict:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        # Face detection + blur
        faces = self.face_app.get(img)
        redacted = 0
        for face in faces:
            if face.det_score > 0.5:
                x1, y1, x2, y2 = face.bbox.astype(int)
                pad = int((x2-x1) * 0.15)
                x1, y1 = max(0,x1-pad), max(0,y1-pad)
                x2, y2 = min(img.shape[1],x2+pad), min(img.shape[0],y2+pad)
                ksize = max(31, int(max(x2-x1,y2-y1)*0.4)|1)
                img[y1:y2,x1:x2] = cv2.GaussianBlur(img[y1:y2,x1:x2], (ksize,ksize), 30)
                redacted += 1

        # pHash
        pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        phash = str(imagehash.phash(pil_img, hash_size=16))

        _, buf = cv2.imencode(".png", img)
        return {"processed": buf.tobytes(), "phash": phash, "faces_redacted": redacted}
```

### 8.4 文档处理

```python
import fitz  # PyMuPDF
from paddleocr import PaddleOCR
from paddlenlp import Taskflow

class DocumentPipeline:
    def __init__(self):
        self.ocr = PaddleOCR(use_angle_cls=True, lang="ch", use_gpu=True, show_log=False)
        self.ner = Taskflow("ner")

    def process_pdf(self, pdf_bytes: bytes) -> dict:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        all_text = []
        for page in doc:
            text = page.get_text("text")
            if len(text.strip()) < 50:  # scanned page
                pix = page.get_pixmap(dpi=300)
                result = self.ocr.ocr(pix.tobytes("png"), cls=True)
                if result and result[0]:
                    text = "\n".join([l[1][0] for l in result[0] if l[1][1] > 0.6])

            # NER redaction
            entities = self.ner(text)
            for ent in entities:
                if ent.get("type") in ("PER","ORG","LOC"):
                    sensitive = ent.get("text", ent.get("word", ""))
                    for inst in page.search_for(sensitive):
                        page.add_redact_annot(inst, fill=(0,0,0))
            page.apply_redactions()
            all_text.append(text)

        return {"text": "\n".join(all_text), "pages": len(doc)}
```

### 8.5 音频处理

```python
class AudioPipeline:
    def __init__(self, hf_token=None):
        from funasr import AutoModel
        self.asr = AutoModel(model="paraformer-zh", vad_model="fsmn-vad",
                             punc_model="ct-punc", device="cuda:0")
        self.diarize = None
        if hf_token:
            from pyannote.audio import Pipeline as DP
            self.diarize = DP.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=hf_token)

    def process(self, audio_path: str) -> dict:
        result = self.asr.generate(input=audio_path)
        transcript = result[0].get("text", "") if result else ""
        segments = []
        if self.diarize:
            d = self.diarize(audio_path)
            for turn, _, spk in d.itertracks(yield_label=True):
                segments.append({"start": turn.start, "end": turn.end, "speaker": spk})
        return {"transcript": transcript, "speakers": segments}
```

### 8.6 视频处理

```python
class VideoPipeline:
    def __init__(self):
        from insightface.app import FaceAnalysis
        self.face_app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider","CPUExecutionProvider"])
        self.face_app.prepare(ctx_id=0, det_size=(640, 640))

    def process(self, input_path: str, output_path: str) -> dict:
        cap = cv2.VideoCapture(input_path)
        fps, w, h = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = cv2.VideoWriter(output_path+".tmp.mp4", cv2.VideoWriter_fourcc(*"mp4v"), fps, (w,h))
        idx, total_faces = 0, 0
        while True:
            ret, frame = cap.read()
            if not ret: break
            for face in self.face_app.get(frame):
                if face.det_score > 0.5:
                    x1,y1,x2,y2 = face.bbox.astype(int)
                    pad = int((x2-x1)*0.15)
                    x1,y1 = max(0,x1-pad), max(0,y1-pad)
                    x2,y2 = min(w,x2+pad), min(h,y2+pad)
                    ksize = max(31, int(max(x2-x1,y2-y1)*0.4)|1)
                    frame[y1:y2,x1:x2] = cv2.GaussianBlur(frame[y1:y2,x1:x2], (ksize,ksize), 30)
                    total_faces += 1
            out.write(frame); idx += 1
        cap.release(); out.release()
        subprocess.run(["ffmpeg","-y","-i",output_path+".tmp.mp4","-c:v","libx264","-crf","23",output_path], capture_output=True)
        os.unlink(output_path+".tmp.mp4")
        return {"frames": idx, "faces_redacted": total_faces, "duration": idx/fps if fps else 0}
```

### 8.7 半结构化数据

```python
import ijson, csv, re
from lxml import etree

SENSITIVE_FIELDS = re.compile(r'姓名|手机|电话|身份证|地址|邮箱|银行卡|密码|name|phone|email|password', re.I)
SENSITIVE_VALUES = {
    'phone': re.compile(r'^1[3-9]\d{9}$'),
    'id_card': re.compile(r'^[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]$'),
}

def redact_value(value: str) -> str:
    if re.match(r'^1[3-9]\d{9}$', value): return value[:3] + "****" + value[-4:]
    if re.match(r'^[1-9]\d{17}[\dXx]$', value): return value[:3] + "***" + value[-3:]
    return "***REDACTED***"

def process_json_stream(input_path: str, output_path: str):
    items = []
    with open(input_path, 'rb') as f:
        for item in ijson.items(f, 'item'):
            items.append(redact_dict(item))
    import orjson
    with open(output_path, 'wb') as f:
        f.write(orjson.dumps(items, ensure_ascii=False, indent=2))

def redact_dict(d: dict) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, dict): result[k] = redact_dict(v)
        elif isinstance(v, list): result[k] = [redact_dict(i) if isinstance(i,dict) else i for i in v]
        elif SENSITIVE_FIELDS.search(k): result[k] = redact_value(str(v))
        elif any(p.match(str(v).strip()) for p in SENSITIVE_VALUES.values()): result[k] = redact_value(str(v))
        else: result[k] = v
    return result
```

---

## 9. Interconnection Connector

### 9.1 TLCP (国密 TLS)

**Tongsuo** 作为系统 OpenSSL 替换后，Python ssl 模块自动使用：

```python
import ssl

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.set_ciphers("ECDHE-SM2-WITH-SM4-SM3:SM2-WITH-SM4-SM3")
ctx.load_cert_chain(certfile="sign_cert.pem", keyfile="sign_key.pem")
ctx.load_verify_locations("ca_cert.pem")
ctx.verify_mode = ssl.CERT_REQUIRED
```

### 9.2 SM2 JWT (PyJWT 扩展)

```python
import jwt
from jwt.algorithms import Algorithm
from gmssl import sm2

class SM2Algorithm(Algorithm):
    def prepare_key(self, key): return key
    def sign(self, msg: bytes, key: dict) -> bytes:
        return sm2.CryptSM2(public_key=key["public_key"], private_key=key["private_key"]).sign(msg)
    def verify(self, msg: bytes, key: str, sig: bytes) -> bool:
        return sm2.CryptSM2(public_key=key, private_key="").verify(sig, msg)

# Register
jwt.register_algorithm("SM2", SM2Algorithm())

# Usage
token = jwt.encode({"sub": "user-123", "exp": datetime.utcnow()+timedelta(hours=24)},
                    {"private_key": sk, "public_key": pk}, algorithm="SM2")
payload = jwt.decode(token, pk, algorithms=["SM2"])
```

### 9.3 FastAPI Connector API

```python
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="CDS Interconnection Connector", version="1.0.0")
security = HTTPBearer()

class SessionCreateRequest(BaseModel):
    contract_id: str
    buyer_cert_pem: str
    buyer_signature: str
    timestamp: int
    requested_level: str = "L1"
    cross_space_token: Optional[str] = None

class TaskSubmitRequest(BaseModel):
    session_id: str
    task_type: str  # sql, python, inference
    code: Optional[str] = None
    code_signature: str

class TaskResultResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[dict] = None
    result_signature: Optional[str] = None
    dp_epsilon_consumed: Optional[float] = None
    dp_epsilon_remaining: Optional[float] = None

@app.post("/sandbox/session/create")
async def create_session(req: SessionCreateRequest, auth=Depends(security)):
    session_id = str(uuid.uuid4())
    # verify contract -> distribute key -> init sandbox
    return {"session_id": session_id, "actual_level": req.requested_level, "expires_at": 0}

@app.post("/sandbox/task/submit")
async def submit_task(req: TaskSubmitRequest, auth=Depends(security)):
    task_id = str(uuid.uuid4())
    # verify session -> verify code signature -> enqueue
    return {"task_id": task_id, "status": "queued"}

@app.get("/sandbox/task/result")
async def get_result(task_id: str, auth=Depends(security)):
    return {"task_id": task_id, "status": "pending"}

@app.get("/sandbox/attest")
async def get_attestation(session_id: str, auth=Depends(security)):
    return {"level": "L1", "report": "...", "signature": "..."}
```

### 9.4 Redis Session 管理

```python
import redis.asyncio as redis, json
from datetime import datetime, timedelta

class SessionManager:
    def __init__(self, redis_url="redis://localhost:6379"):
        self.redis = redis.from_url(redis_url)
        self.ttl = 86400  # 24h

    async def create(self, sid: str, data: dict) -> dict:
        data.update({"session_id": sid, "status": "active",
                     "created_at": datetime.utcnow().isoformat(),
                     "expires_at": (datetime.utcnow()+timedelta(seconds=self.ttl)).isoformat()})
        await self.redis.setex(f"session:{sid}", self.ttl, json.dumps(data))
        await self.redis.sadd("active_sessions", sid)
        return data

    async def get(self, sid: str) -> dict | None:
        data = await self.redis.get(f"session:{sid}")
        return json.loads(data) if data else None

    async def validate(self, sid: str) -> dict:
        session = await self.get(sid)
        if not session: raise ValueError("Session not found")
        if session.get("status") != "active": raise ValueError("Session not active")
        return session

    async def expire(self, sid: str):
        session = await self.get(sid)
        if session:
            session["status"] = "expired"
            await self.redis.setex(f"session:{sid}", 3600, json.dumps(session))
            await self.redis.srem("active_sessions", sid)
```

### 9.5 WeCross 跨链

**WeCross** (github.com/WeBankBlockchain/WeCross) - WeBank 跨链平台, JSON-RPC API:

```python
import httpx

class WeCrossClient:
    def __init__(self, router_url: str, account: str):
        self.url, self.account = router_url, account

    async def call(self, path: str, method: str, args: list):
        async with httpx.AsyncClient() as c:
            resp = await c.post(self.url, json={
                "version": "2.0", "method": method,
                "params": [path, self.account, args], "id": 1})
            return resp.json()

    async def send_transaction(self, path: str, method: str, args: list):
        async with httpx.AsyncClient() as c:
            resp = await c.post(self.url, json={
                "version": "2.0", "method": "sendTransaction",
                "params": [path, self.account, method, args], "id": 1})
            return resp.json()
```

---

## 10. 集成架构与部署

### 10.1 Docker Compose (开发环境)

```yaml
version: "3.8"
services:
  postgres:
    image: postgres:15
    environment: { POSTGRES_DB: sandbox, POSTGRES_USER: sandbox, POSTGRES_PASSWORD: sandbox_pass }
    volumes: [pg_data:/var/lib/postgresql/data]
    ports: ["5432:5432"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  clickhouse:
    image: clickhouse/clickhouse-server:24-alpine
    ports: ["8123:8123", "9000:9000"]
    volumes: [ch_data:/var/lib/clickhouse]

  rabbitmq:
    image: rabbitmq:3-management
    ports: ["5672:5672", "15672:15672"]

  vault:
    image: hashicorp/vault:1.19
    cap_add: [IPC_LOCK]
    environment: { VAULT_DEV_ROOT_TOKEN_ID: sandbox-vault-token, VAULT_DEV_LISTEN_ADDRESS: "0.0.0.0:8200" }
    ports: ["8200:8200"]

  opa:
    image: openpolicyagent/opa:latest
    ports: ["8181:8181"]
    command: ["run", "--server", "--addr=0.0.0.0:8181"]

  api:
    build: .
    command: uvicorn connector.api:app --host 0.0.0.0 --port 8000
    ports: ["8000:8000"]
    depends_on: [postgres, redis, vault, opa]

  celery-worker-l1:
    build: .
    command: celery -A scheduler.celery_app worker -Q l1_tee --concurrency=4
    depends_on: [rabbitmq, redis]

  celery-worker-l2:
    build: .
    command: celery -A scheduler.celery_app worker -Q l2_gvisor --concurrency=8
    depends_on: [rabbitmq, redis]

  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes: ["./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml"]

  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]

volumes:
  pg_data:
  ch_data:
```

### 10.2 K8s 生产部署

```yaml
# namespace
apiVersion: v1
kind: Namespace
metadata:
  name: confidential-sandbox
  labels: { security-level: "L1" }
---
# config
apiVersion: v1
kind: ConfigMap
metadata: { name: sandbox-config, namespace: confidential-sandbox }
data:
  config.yaml: |
    sandbox:
      runtime:
        l1: { enabled: true, teeFramework: occlum, epcMemoryGB: 64, maxConcurrentSessions: 20 }
        l2: { enabled: true, vmRuntime: firecracker, containerRuntime: gvisor, maxConcurrentSessions: 50 }
        l3: { enabled: false }
      policy: { defaultDenyAll: true, dpDefaultEpsilon: 1.0, outputMaxRows: 100 }
      crypto: { preferGmAlgorithms: true, hsmEnabled: true }
```

### 10.3 性能基准目标

| 操作 | L1 TEE | L2 软件 | 环境 |
|------|--------|---------|------|
| 沙箱启动 | <=60s (P99) | <=15s (P99) | SGX服务器 / 普通x86 |
| SQL聚合(1亿行) | <=120s | <=60s | 32C128G |
| 图像脱敏(1000张) | <=300s | <=180s | 含GPU(H100) |
| 音频ASR(1小时) | <=600s | <=300s | 纯CPU |
| 输出审查延迟 | <=500ms | <=200ms | 1MB文本 |
| 跨空间会话建立 | <=5s | <=3s | 同城网络 |

### 10.4 安全认证路线

| 认证 | 标准 | 计划周期 |
|------|------|----------|
| 等保三级 | GB/T 22239-2019 | Phase 1后6个月 |
| 密评 | GM/T 0054-2018 | Phase 1后 |
| TC609互操作 | TC609-6-2025-01 | Phase 2后 |
| DSMM三级 | GB/T 37988-2019 | Phase 3后 |

---

*文档版本：v2.0 | 状态：详细技术规格 | 基于8个子系统开源技术调研 | 对应产品规格：product-spec v1.1*
