# 密态沙箱系统（CDS）· 区块链存证技术规格

> 版本：v1.0
> 作者：@架构师
> 日期：2026-06-03
> 模块：S3-01b 区块链存证
> 参考：FISCO BCOS, Merkle Tree, SM3

---

## 1. 概述

区块链存证模块负责将关键业务操作（合约签署、沙箱执行、输出审查）的哈希值锚定到 FISCO BCOS 联盟链上，提供不可篡改的审计证据。

### 1.1 设计原则

- **链上摘要，链下详情**：仅存储 SM3 哈希，不存储原始数据
- **批量锚定**：多条记录聚合为 Merkle 树，仅锚定根哈希
- **国密合规**：SM3 哈希 + SM2 签名
- **可验证**：任何人可通过 Merkle 证明验证记录完整性

### 1.2 参考实现

- [FISCO BCOS](https://fisco-bcos.readthedocs.io/) — 国产联盟链
- [FISCO BCOS Python SDK](https://github.com/FISCO-BCOS/python-sdk)
- SS-02 数字合约技术规格

---

## 2. 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                    区块链存证架构                                  │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ 审计记录      │    │ Merkle 树    │    │ FISCO BCOS   │      │
│  │ (ClickHouse) │───►│ 聚合计算     │───►│ 智能合约     │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│         │                   │                   │               │
│         │                   │                   │               │
│         ▼                   ▼                   ▼               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ SM3 哈希     │    │ Merkle 证明  │    │ 链上存证     │      │
│  │ 计算         │    │ 生成         │    │ 查询         │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 数据模型

### 3.1 审计记录表（ClickHouse）

```sql
CREATE TABLE audit_records (
    id UUID DEFAULT generateUUIDv4(),
    event_type LowCardinality(String),
    actor_id UUID,
    resource_type LowCardinality(String),
    resource_id UUID,
    action String,
    details String,  -- JSON
    ip_address String,
    user_agent String,
    timestamp DateTime64(3) DEFAULT now64(3),

    -- Merkle 树相关
    merkle_leaf_hash FixedString(64),  -- SM3(审计记录)
    merkle_root Nullable(FixedString(64)),
    merkle_path Array(FixedString(64)),
    block_number Nullable(UInt64),
    tx_hash Nullable(String),

    -- 索引
    INDEX idx_event_type event_type TYPE bloom_filter GRANULARITY 1,
    INDEX idx_actor_id actor_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_timestamp timestamp TYPE minmax GRANULARITY 1
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, event_type, actor_id)
TTL timestamp + INTERVAL 365 DAY;
```

### 3.2 Merkle 树配置表（PostgreSQL）

```sql
CREATE TABLE merkle_trees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    root_hash CHAR(64) NOT NULL,
    leaf_count INT NOT NULL,
    block_number BIGINT,
    tx_hash VARCHAR(66),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE merkle_leaves (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tree_id UUID NOT NULL REFERENCES merkle_trees(id),
    leaf_index INT NOT NULL,
    leaf_hash CHAR(64) NOT NULL,
    audit_record_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## 4. 核心实现

### 4.1 Merkle 树服务

```python
import hashlib
from typing import List, Optional, Tuple

class MerkleTreeService:
    """SM3 Merkle 树服务"""

    def __init__(self):
        self.hash_func = hashlib.sha3_256  # 实际应使用 SM3

    def build_tree(self, leaves: List[bytes]) -> Tuple[bytes, List[List[bytes]]]:
        """构建 Merkle 树，返回根哈希和每层节点"""
        if not leaves:
            raise ValueError("Empty leaves")

        # 计算叶子节点哈希
        current_level = [self.hash_func(leaf).digest() for leaf in leaves]
        tree = [current_level]

        # 构建树
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                parent = self.hash_func(left + right).digest()
                next_level.append(parent)
            current_level = next_level
            tree.append(current_level)

        return current_level[0], tree

    def generate_proof(self, tree: List[List[bytes]], leaf_index: int) -> List[bytes]:
        """生成 Merkle 证明路径"""
        proof = []
        for level in tree[:-1]:
            if leaf_index % 2 == 0:
                # 当前节点是左子节点，兄弟在右边
                sibling_index = leaf_index + 1
            else:
                # 当前节点是右子节点，兄弟在左边
                sibling_index = leaf_index - 1

            if sibling_index < len(level):
                proof.append(level[sibling_index])
            else:
                proof.append(level[leaf_index])  # 无兄弟节点

            leaf_index //= 2

        return proof

    def verify_proof(self, leaf_hash: bytes, proof: List[bytes],
                    root_hash: bytes, leaf_index: int) -> bool:
        """验证 Merkle 证明"""
        current = leaf_hash
        for sibling in proof:
            if leaf_index % 2 == 0:
                current = self.hash_func(current + sibling).digest()
            else:
                current = self.hash_func(sibling + current).digest()
            leaf_index //= 2

        return current == root_hash
```

### 4.2 FISCO BCOS 客户端

```python
from web3 import Web3
from eth_account import Account
import json

class FISCOBCOSClient:
    """FISCO BCOS 客户端"""

    def __init__(self, node_url: str, chain_id: int, group_id: int = 1):
        self.w3 = Web3(Web3.HTTPProvider(node_url))
        self.chain_id = chain_id
        self.group_id = group_id

    def deploy_contract(self, abi: dict, bytecode: str,
                       deployer_key: str) -> str:
        """部署智能合约"""
        contract = self.w3.eth.contract(abi=abi, bytecode=bytecode)
        tx = contract.constructor().build_transaction({
            'chainId': self.chain_id,
            'gas': 2000000,
            'gasPrice': 0,  # FISCO BCOS 无 gas 费
            'nonce': self.w3.eth.get_transaction_count(
                self.w3.eth.account.from_key(deployer_key).address
            ),
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, deployer_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

        return receipt.contractAddress

    def call_contract(self, contract_address: str, abi: dict,
                     function_name: str, args: list,
                     caller_key: str) -> dict:
        """调用智能合约"""
        contract = self.w3.eth.contract(
            address=contract_address,
            abi=abi
        )

        func = getattr(contract.functions, function_name)
        tx = func(*args).build_transaction({
            'chainId': self.chain_id,
            'gas': 200000,
            'gasPrice': 0,
            'nonce': self.w3.eth.get_transaction_count(
                self.w3.eth.account.from_key(caller_key).address
            ),
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, caller_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

        return {
            'tx_hash': tx_hash.hex(),
            'block_number': receipt.blockNumber,
            'status': receipt.status,
        }
```

### 4.3 存证智能合约（Solidity）

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract AuditRegistry {
    struct AuditRecord {
        bytes32 merkleRoot;
        uint256 leafCount;
        uint256 blockNumber;
        uint256 timestamp;
        address registrant;
    }

    mapping(uint256 => AuditRecord) public records;
    uint256 public recordCount;

    event RecordRegistered(
        uint256 indexed recordId,
        bytes32 merkleRoot,
        uint256 leafCount,
        uint256 timestamp
    );

    function registerRecord(bytes32 _merkleRoot, uint256 _leafCount) public {
        recordCount++;
        records[recordCount] = AuditRecord({
            merkleRoot: _merkleRoot,
            leafCount: _leafCount,
            blockNumber: block.number,
            timestamp: block.timestamp,
            registrant: msg.sender
        });

        emit RecordRegistered(recordCount, _merkleRoot, _leafCount, block.timestamp);
    }

    function getRecord(uint256 _recordId) public view returns (
        bytes32 merkleRoot,
        uint256 leafCount,
        uint256 blockNumber,
        uint256 timestamp,
        address registrant
    ) {
        AuditRecord memory record = records[_recordId];
        return (
            record.merkleRoot,
            record.leafCount,
            record.blockNumber,
            record.timestamp,
            record.registrant
        );
    }

    function verifyRecord(uint256 _recordId, bytes32 _merkleRoot) public view returns (bool) {
        return records[_recordId].merkleRoot == _merkleRoot;
    }
}
```

### 4.4 区块链存证服务

```python
class BlockchainAnchorService:
    """区块链存证服务"""

    def __init__(self, fisco_client: FISCOBCOSClient,
                 contract_address: str, contract_abi: dict,
                 signer_key: str):
        self.fisco = fisco_client
        self.contract_address = contract_address
        self.contract_abi = contract_abi
        self.signer_key = signer_key
        self.merkle_service = MerkleTreeService()

    async def anchor_batch(self, audit_records: List[dict]) -> dict:
        """批量锚定审计记录到区块链"""
        # 1. 计算每条记录的 SM3 哈希
        leaf_hashes = []
        for record in audit_records:
            record_bytes = json.dumps(record, sort_keys=True).encode()
            leaf_hash = hashlib.sha3_256(record_bytes).digest()
            leaf_hashes.append(leaf_hash)

        # 2. 构建 Merkle 树
        root_hash, tree = self.merkle_service.build_tree(leaf_hashes)

        # 3. 生成 Merkle 证明
        proofs = []
        for i in range(len(leaf_hashes)):
            proof = self.merkle_service.generate_proof(tree, i)
            proofs.append(proof)

        # 4. 调用智能合约存储根哈希
        result = self.fisco.call_contract(
            contract_address=self.contract_address,
            abi=self.contract_abi,
            function_name='registerRecord',
            args=[root_hash, len(leaf_hashes)],
            caller_key=self.signer_key
        )

        # 5. 更新数据库记录
        for i, record in enumerate(audit_records):
            await self._update_audit_record(
                record_id=record['id'],
                merkle_leaf_hash=leaf_hashes[i].hex(),
                merkle_root=root_hash.hex(),
                merkle_path=[p.hex() for p in proofs[i]],
                block_number=result['block_number'],
                tx_hash=result['tx_hash']
            )

        return {
            'merkle_root': root_hash.hex(),
            'leaf_count': len(leaf_hashes),
            'block_number': result['block_number'],
            'tx_hash': result['tx_hash'],
        }

    async def verify_record(self, record_id: str) -> dict:
        """验证审计记录的完整性"""
        # 1. 从数据库获取记录和 Merkle 证明
        record = await self._get_audit_record(record_id)

        # 2. 重新计算叶子哈希
        record_bytes = json.dumps(record['details'], sort_keys=True).encode()
        leaf_hash = hashlib.sha3_256(record_bytes).digest()

        # 3. 验证 Merkle 证明
        is_valid = self.merkle_service.verify_proof(
            leaf_hash=leaf_hash,
            proof=[bytes.fromhex(p) for p in record['merkle_path']],
            root_hash=bytes.fromhex(record['merkle_root']),
            leaf_index=record['merkle_leaf_index']
        )

        # 4. 验证链上存证
        on_chain = self.fisco.call_contract(
            contract_address=self.contract_address,
            abi=self.contract_abi,
            function_name='verifyRecord',
            args=[record['block_number'], bytes.fromhex(record['merkle_root'])],
            caller_key=self.signer_key
        )

        return {
            'record_id': record_id,
            'merkle_valid': is_valid,
            'on_chain_valid': on_chain['status'] == 1,
            'block_number': record['block_number'],
            'tx_hash': record['tx_hash'],
        }
```

---

## 5. API 设计

```python
# POST /api/v1/audit/anchor
# 批量锚定审计记录
class AnchorRequest(BaseModel):
    record_ids: List[str]  # 审计记录 ID 列表

class AnchorResponse(BaseModel):
    merkle_root: str
    leaf_count: int
    block_number: int
    tx_hash: str

# GET /api/v1/audit/verify/{record_id}
# 验证审计记录完整性
class VerifyResponse(BaseModel):
    record_id: str
    merkle_valid: bool
    on_chain_valid: bool
    block_number: int
    tx_hash: str

# GET /api/v1/audit/records
# 查询审计记录（支持过滤）
class AuditFilter(BaseModel):
    event_type: Optional[str]
    actor_id: Optional[str]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    anchored_only: bool = False
```

---

## 6. 测试用例

```python
def test_merkle_tree_construction():
    """测试 Merkle 树构建"""
    leaves = [b"record1", b"record2", b"record3", b"record4"]
    service = MerkleTreeService()

    root, tree = service.build_tree(leaves)

    assert len(root) == 32  # SM3 哈希长度
    assert len(tree) == 3   # 4 叶子 = 3 层

def test_merkle_proof_generation_and_verification():
    """测试 Merkle 证明生成和验证"""
    leaves = [b"record1", b"record2", b"record3", b"record4"]
    service = MerkleTreeService()

    root, tree = service.build_tree(leaves)
    proof = service.generate_proof(tree, 1)  # 验证第 2 条记录

    leaf_hash = hashlib.sha3_256(b"record2").digest()
    is_valid = service.verify_proof(leaf_hash, proof, root, 1)

    assert is_valid

def test_batch_anchor():
    """测试批量锚定"""
    records = [
        {"id": "1", "event": "contract_signed", "timestamp": "2026-01-01"},
        {"id": "2", "event": "sandbox_created", "timestamp": "2026-01-02"},
        {"id": "3", "event": "output_approved", "timestamp": "2026-01-03"},
    ]

    service = BlockchainAnchorService(...)
    result = await service.anchor_batch(records)

    assert result['leaf_count'] == 3
    assert len(result['merkle_root']) == 64
    assert result['block_number'] > 0
```

---

## 7. 部署配置

```yaml
# docker-compose.yml
services:
  fisco-bcos:
    image: fiscoorg/fisco-bcos:v3.0.0
    ports:
      - "30300:30300"
      - "20200:20200"
    volumes:
      - ./fisco-data:/data
    command: >
      -c /data/config.ini
      --genesis /data/genesis.json

  blockchain-adapter:
    build: ./blockchain-adapter
    environment:
      - FISCO_NODE_URL=http://fisco-bcos:30300
      - CHAIN_ID=1
      - GROUP_ID=1
      - CONTRACT_ADDRESS=${CONTRACT_ADDRESS}
      - SIGNER_KEY_PATH=/secrets/signer.key
    volumes:
      - ./secrets:/secrets:ro
```

---

## 8. 监控指标

| 指标 | 说明 | 告警阈值 |
|------|------|---------|
| `anchor_batch_duration_ms` | 批量锚定耗时 | > 5000ms |
| `merkle_build_duration_ms` | Merkle 树构建耗时 | > 1000ms |
| `fisco_tx_pending` | 待确认交易数 | > 10 |
| `anchor_success_rate` | 锚定成功率 | < 99% |
| `verify_success_rate` | 验证成功率 | < 99% |
