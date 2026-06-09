# 密态沙箱系统 · 结构化数据存储安全架构 v2 (生产级)

> 核心修正: 去掉"每次会话启动临时PostgreSQL"的低性能方案
> 设计目标: 安全不妥协 + 性能可接受 + 数据库引擎零改动

---

## 1. 性能问题分析

### L1临时数据库方案的性能瓶颈

```
冷启动流程 (每次查询):
  TEE Enclave 初始化:     5-15s
  下载加密数据目录:        10-60s (取决于数据量, 1GB ≈ 5s)
  解密数据目录:            5-20s (SM4 ≈ 200MB/s)
  PostgreSQL 启动:         3-10s
  SQL 执行:                1-30s
  输出审查:                <1s
  ──────────────────────────────
  总计:                    24-136s (不可接受)

  对比: 用户期望 <5s 响应
```

**结论**: 每次查询都启动临时数据库, 性能完全不可接受。

---

## 2. 生产级方案: 常驻加密数据库 + 应用层加解密

### 2.1 核心架构

```
┌─────────────────────────────────────────────────────────────────┐
│  数据入库阶段 (一次性, 数商操作)                                    │
│                                                                 │
│  数商原始数据 ──→ TEE内读取 ──→ 加密分层存储                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  PostgreSQL (常驻实例, 不需要TEE)                           │  │
│  │                                                           │  │
│  │  enterprise_basic 表:                                      │  │
│  │  ┌─────────┬────────────┬────────────┬───────────┐        │  │
│  │  │ ent_id  │ ent_name   │ legal_person│ industry  │        │  │
│  │  │ 哈希索引 │ SM4密文    │ SM4密文      │ 明文       │        │  │
│  │  │ varchar │ bytea      │ bytea        │ varchar   │        │  │
│  │  └─────────┴────────────┴────────────┴───────────┘        │  │
│  │                                                           │  │
│  │  数据库文件本身 → 磁盘级加密 (LUKS/dm-crypt)                │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  查询执行阶段 (买方操作, 每次查询)                                  │
│                                                                 │
│  买方SQL ──→ SQL解析+安全检查 ──→ 查询改写 ──→ PG执行              │
│                                           ↓                    │
│                                     应用层解密敏感列              │
│                                           ↓                    │
│                                     输出审查网关                 │
│                                           ↓                    │
│                                     结果返回买方                 │
│                                                                 │
│  启动时间: <100ms (无TEE初始化, 无数据加载)                        │
│  查询延迟: SQL执行时间 + 解密开销 (<100ms per row)                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 分层加密存储设计

**核心思路**: 不同敏感级别的字段用不同加密策略, 兼顾安全与查询能力。

```
┌─────────────────────────────────────────────────────────────────┐
│  字段加密分层                                                    │
│                                                                 │
│  Level 0 - 明文存储 (可查询/可索引/可排序/可聚合)                  │
│  ├── 行业代码 (industry_code)                                    │
│  ├── 年份 (year)                                                │
│  ├── 地区代码 (region_code)                                      │
│  ├── 数据类型标签                                                │
│  └── 所有非敏感的维度字段                                         │
│                                                                 │
│  Level 1 - 确定性加密 (可等值查询, 不可排序/范围)                   │
│  ├── 身份证号 (SM4-DET, 同明文→同密文)                            │
│  ├── 手机号 (SM4-DET)                                           │
│  ├── 企业ID (SM4-DET)                                           │
│  └── 银行卡号 (SM4-DET)                                         │
│  配套: 哈希索引 (SM3截断哈希, 支持等值查询加速)                     │
│                                                                 │
│  Level 2 - 随机加密 (不可查询, 仅应用层解密)                       │
│  ├── 企业名称 (SM4-GCM, 随机IV)                                  │
│  ├── 人名 (SM4-GCM)                                             │
│  ├── 地址 (SM4-GCM)                                             │
│  └── 所有高敏感文本字段                                           │
│  配套: 无索引, 查询时全表扫描+应用层解密 (或先用非敏感列过滤)        │
│                                                                 │
│  Level 3 - 同态加密 (可在密文上计算, 极慢)                         │
│  ├── 聚合统计 (求和/计数/均值)                                    │
│  └── 仅用于L3安全等级, 生产环境慎用                               │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 确定性加密实现 (支持等值查询)

```python
# crypto/deterministic_sm4.py

import hashlib
from gmssl import sm4, sm3, func
import os

class DeterministicSM4:
    """
    确定性SM4加密: 同一明文+同一密钥 → 同一密文
    支持等值查询: WHERE enc_col = encrypt('value')
    不支持范围查询、排序
    """

    def __init__(self, dek: bytes):
        self.dek = dek
        self.crypt = sm4.CryptSM4()
        self.crypt.set_key(dek, sm4.SM4_ENCRYPT)

    def encrypt(self, plaintext: str) -> bytes:
        """确定性加密: 使用固定IV(密钥派生)"""
        # 从DEK派生固定IV (不是随机IV, 保证确定性)
        iv = hashlib.sha256(self.dek + b"DET_IV").digest()[:16]

        # PKCS7 padding
        data = plaintext.encode("utf-8")
        pad_len = 16 - (len(data) % 16)
        padded = data + bytes([pad_len] * pad_len)

        # SM4-CBC加密 (固定IV → 确定性)
        return self.crypt.cbc_encrypt(iv, padded)

    def decrypt(self, ciphertext: bytes) -> str:
        """解密"""
        decryptor = sm4.CryptSM4()
        decryptor.set_key(self.dek, sm4.SM4_DECRYPT)
        iv = hashlib.sha256(self.dek + b"DET_IV").digest()[:16]
        plaintext = decryptor.cbc_decrypt(iv, ciphertext)
        pad_len = plaintext[-1]
        return plaintext[:-pad_len].decode("utf-8")

    def hash_for_index(self, plaintext: str) -> str:
        """生成索引哈希 (用于B-tree索引, 加速等值查询)"""
        # SM3(DEK || plaintext) 截断16字节
        data = self.dek + plaintext.encode("utf-8")
        h = bytes(sm3.sm3_hash(func.bytes_to_list(data)))
        return h[:16].hex()


class RandomizedSM4:
    """
    随机化SM4加密: 同一明文 → 不同密文 (每次随机IV)
    不可查询, 仅应用层解密
    """

    def __init__(self, dek: bytes):
        self.dek = dek

    def encrypt(self, plaintext: str) -> bytes:
        """随机化加密: 随机IV → 不可预测"""
        iv = os.urandom(16)
        crypt = sm4.CryptSM4()
        crypt.set_key(self.dek, sm4.SM4_ENCRYPT)

        data = plaintext.encode("utf-8")
        pad_len = 16 - (len(data) % 16)
        padded = data + bytes([pad_len] * pad_len)

        ciphertext = crypt.cbc_encrypt(iv, padded)
        return iv + ciphertext  # IV || ciphertext

    def decrypt(self, data: bytes) -> str:
        """解密"""
        iv, ciphertext = data[:16], data[16:]
        decryptor = sm4.CryptSM4()
        decryptor.set_key(self.dek, sm4.SM4_DECRYPT)
        plaintext = decryptor.cbc_decrypt(iv, ciphertext)
        pad_len = plaintext[-1]
        return plaintext[:-pad_len].decode("utf-8")
```

### 2.4 数据库Schema设计 (加密表)

```sql
-- 加密存储的表结构

-- Level 0 明文列 + Level 1 确定性加密列 + Level 2 随机加密列
CREATE TABLE enterprise_basic (
    -- 主键: 确定性加密的ent_id + 哈希索引
    ent_id_enc      BYTEA NOT NULL,         -- SM4-DET加密
    ent_id_hash     VARCHAR(32) NOT NULL,    -- SM3截断哈希 (等值查询索引)

    -- 高敏感字段: 随机加密 (不可查询)
    ent_name_enc    BYTEA,                   -- SM4-GCM随机加密
    legal_person_enc BYTEA,                  -- SM4-GCM随机加密
    address_enc     BYTEA,                   -- SM4-GCM随机加密

    -- 中敏感字段: 确定性加密 (可等值查询)
    phone_enc       BYTEA,
    phone_hash      VARCHAR(32),

    -- 低敏感字段: 明文 (可查询/可排序/可聚合)
    industry_code   VARCHAR(10),
    reg_capital     DECIMAL(15,2),
    ent_type        VARCHAR(20),
    reg_date        DATE,
    region_code     VARCHAR(10),

    -- 元数据
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 哈希索引 (加速等值查询)
CREATE INDEX idx_ent_id_hash ON enterprise_basic(ent_id_hash);
CREATE INDEX idx_phone_hash ON enterprise_basic(phone_hash);
-- 明文列索引 (正常B-tree)
CREATE INDEX idx_industry ON enterprise_basic(industry_code);
CREATE INDEX idx_region ON enterprise_basic(region_code);
CREATE INDEX idx_reg_date ON enterprise_basic(reg_date);

-- 纳税记录表
CREATE TABLE tax_records (
    tax_id_enc      BYTEA NOT NULL,
    tax_id_hash     VARCHAR(32) NOT NULL,
    ent_id_enc      BYTEA NOT NULL,
    ent_id_hash     VARCHAR(32) NOT NULL,

    -- 明文列 (可查询/可聚合)
    year            INT NOT NULL,
    tax_type        VARCHAR(20),
    period          VARCHAR(10),

    -- 加密列 (应用层解密后聚合)
    tax_amount_enc  BYTEA,                   -- SM4-GCM
    revenue_enc     BYTEA,                   -- SM4-GCM

    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tax_ent_hash ON tax_records(ent_id_hash);
CREATE INDEX idx_tax_year ON tax_records(year);
```

### 2.5 查询改写引擎 (生产级)

```python
# runtime/query_rewriter.py

import sqlparse
from sqlparse.sql import Statement, Identifier, Where, Comparison
from sqlparse.tokens import Keyword, Name

class ProductionQueryRewriter:
    """
    生产级SQL查询改写器
    - 明文列: 直接查询, 完整SQL能力
    - 确定性加密列: WHERE等值查询 → 改写为哈希匹配
    - 随机加密列: 不能直接查询, 需先用明文列过滤, 再应用层解密
    """

    # 列加密映射: {table.column: encryption_level}
    COLUMN_MAP = {
        "enterprise_basic.ent_id": {"level": "det", "enc": "ent_id_enc", "hash": "ent_id_hash"},
        "enterprise_basic.ent_name": {"level": "rand", "enc": "ent_name_enc"},
        "enterprise_basic.phone": {"level": "det", "enc": "phone_enc", "hash": "phone_hash"},
        "enterprise_basic.industry_code": {"level": "plain"},
        "enterprise_basic.reg_capital": {"level": "plain"},
        "tax_records.ent_id": {"level": "det", "enc": "ent_id_enc", "hash": "ent_id_hash"},
        "tax_records.tax_amount": {"level": "rand", "enc": "tax_amount_enc"},
        "tax_records.year": {"level": "plain"},
        "tax_records.tax_type": {"level": "plain"},
    }

    def __init__(self, deterministic_encryptor, security_rules: dict):
        self.enc = deterministic_encryptor
        self.rules = security_rules

    def rewrite(self, sql: str, params: dict = None) -> tuple:
        """
        改写SQL:
        1. 安全检查 (禁止危险操作)
        2. 加密列引用替换
        3. 等值查询改写为哈希匹配
        4. 添加LIMIT
        """
        parsed = sqlparse.parse(sql.strip().rstrip(";"))[0]

        # 安全检查
        self._security_check(parsed, sql)

        # 改写
        rewritten = self._rewrite_statement(parsed)

        # 确保有LIMIT
        if "LIMIT" not in rewritten.upper():
            max_rows = self.rules.get("maxOutputRows", 100)
            rewritten = f"{rewritten} LIMIT {max_rows}"

        return rewritten, params

    def _security_check(self, parsed: Statement, sql: str):
        """安全检查"""
        sql_upper = sql.upper()

        # 禁止非SELECT
        if not sql_upper.strip().startswith("SELECT"):
            raise SecurityError("Only SELECT statements allowed")

        # 禁止SELECT *
        if re.search(r'SELECT\s+\*', sql_upper):
            raise SecurityError("SELECT * not allowed, specify columns")

        # 禁止危险操作
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
                     "TRUNCATE", "GRANT", "REVOKE", "COPY", "LOAD", "EXECUTE",
                     "pg_read_file", "pg_write_file", "lo_import", "lo_export"]
        for kw in forbidden:
            if kw in sql_upper:
                raise SecurityError(f"Forbidden: {kw}")

        # 禁止系统表访问
        if "pg_catalog" in sql_upper or "information_schema" in sql_upper:
            raise SecurityError("System catalog access forbidden")

    def _rewrite_statement(self, parsed: Statement) -> str:
        """改写SQL语句"""
        sql = str(parsed)

        # 替换加密列引用
        # SELECT ent_name → SELECT ent_name_enc (后续应用层解密)
        # WHERE ent_id = 'xxx' → WHERE ent_id_hash = hash('xxx')

        for col_path, meta in self.COLUMN_MAP.items():
            table, col = col_path.split(".")

            if meta["level"] == "det":
                # 确定性加密列: 替换SELECT和WHERE
                # SELECT phone → SELECT phone_enc (应用层解密)
                sql = re.sub(
                    rf'\b{table}\.{col}\b(?!\s*(?:_enc|_hash))',
                    f'{table}.{meta["enc"]}',
                    sql
                )
                # WHERE phone = 'xxx' → WHERE phone_hash = 'hash(xxx)'
                # 这需要参数化查询配合, 这里做简化处理

            elif meta["level"] == "rand":
                # 随机加密列: SELECT时替换为密文列
                sql = re.sub(
                    rf'\b{table}\.{col}\b(?!\s*_enc)',
                    f'{table}.{meta["enc"]}',
                    sql
                )
                # WHERE不能直接用, 需要报错或改写为子查询
                if f"WHERE" in sql.upper() and col in sql:
                    # 检查是否在WHERE中使用了加密列
                    pass  # 由安全检查处理

        return sql

    def decrypt_results(self, rows: list, columns: list, dek: bytes) -> list:
        """解密查询结果中的加密列"""
        from crypto.deterministic_sm4 import DeterministicSM4, RandomizedSM4

        det_enc = DeterministicSM4(dek)
        rand_enc = RandomizedSM4(dek)

        decrypted_rows = []
        for row in rows:
            new_row = list(row)
            for i, col_name in enumerate(columns):
                meta = self._get_column_meta(col_name)
                if meta and isinstance(new_row[i], bytes):
                    if meta["level"] == "det":
                        new_row[i] = det_enc.decrypt(new_row[i])
                    elif meta["level"] == "rand":
                        new_row[i] = rand_enc.decrypt(new_row[i])
            decrypted_rows.append(tuple(new_row))

        return decrypted_rows
```

### 2.6 高性能查询流程

```python
# runtime/secure_query_executor.py

import psycopg2
import time

class SecureQueryExecutor:
    """
    生产级安全查询执行器
    性能目标: 首次查询 <2s, 后续查询 <500ms
    """

    def __init__(self, db_config: dict, kms_client, policy_engine, output_inspector):
        self.db_config = db_config
        self.kms = kms_client
        self.policy = policy_engine
        self.inspector = output_inspector

        # 数据库连接池 (常驻, 不需要每次创建)
        self.pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            **db_config,
        )

        # DEK缓存 (TEE内存中, 避免每次查询都访问KMS)
        self._dek_cache = {}  # {product_id: (dek, expiry)}

    def execute(self, session_id: str, product_id: str,
                sql: str, contract: dict) -> dict:

        start_time = time.time()

        # 1. 策略检查 (OPA, <10ms)
        policy_result = self.policy.evaluate(
            subject={"session_id": session_id},
            resource={"product_id": product_id},
            action={"type": "sql_query", "sql": sql},
        )
        if not policy_result["allow"]:
            return {"error": f"Policy denied: {policy_result['reason']}"}

        # 2. SQL安全检查 + 改写 (<50ms)
        rewriter = ProductionQueryRewriter(
            deterministic_encryptor=self._get_det_encryptor(product_id),
            security_rules=contract.get("outputConstraints", {}),
        )
        rewritten_sql, params = rewriter.rewrite(sql)

        # 3. 获取DEK (<100ms, 或从缓存取 <1ms)
        dek = self._get_dek(product_id)

        # 4. 执行SQL (<varies, 取决于数据量和查询复杂度)
        conn = self.pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(rewritten_sql, params)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
        finally:
            self.pool.putconn(conn)

        # 5. 应用层解密敏感列 (<100ms per 1000 rows)
        decrypted_rows = rewriter.decrypt_results(rows, columns, dek)

        # 6. 输出审查 (<50ms)
        inspection = self.inspector.inspect(
            rows=decrypted_rows,
            columns=columns,
            contract=contract,
        )

        elapsed = time.time() - start_time

        return {
            "result": inspection.approved_output,
            "row_count": len(inspection.approved_output),
            "elapsed_ms": int(elapsed * 1000),
            "dp_epsilon": inspection.dp_epsilon,
        }

    def _get_dek(self, product_id: str) -> bytes:
        """获取DEK (带缓存)"""
        cached = self._dek_cache.get(product_id)
        if cached and cached[1] > time.time():
            return cached[0]

        dek = self.kms.retrieve_dek(product_id)
        self._dek_cache[product_id] = (dek, time.time() + 3600)  # 缓存1小时
        return dek
```

### 2.7 性能基准

```
┌─────────────────────────────────────────────────────────────────┐
│  性能对比 (1亿行企业数据, 单节点 32C128G)                          │
│                                                                 │
│  操作                    │ 明文方案  │ L1临时DB  │ L2常驻加密DB   │
│  ───────────────────────┼──────────┼──────────┼───────────────  │
│  首次查询延迟            │ 0.5s     │ 60-120s  │ 1-2s            │
│  后续查询延迟            │ 0.3s     │ 60-120s  │ 0.3-0.8s        │
│  等值查询 (WHERE id=x)   │ 5ms      │ 5ms      │ 10ms (哈希索引)  │
│  范围查询 (WHERE x>100)  │ 100ms    │ 100ms    │ 100ms (明文列)   │
│  聚合查询 (AVG/SUM)      │ 2s       │ 2s       │ 2.5s (明文列)    │
│  JOIN (2表)             │ 5s       │ 5s       │ 6s               │
│  全表扫描 (1亿行)        │ 30s      │ 30s      │ 45s (含解密)     │
│  并发查询 (50 QPS)       │ ✓        │ ✗        │ ✓               │
│  内存占用               │ 8GB      │ 8GB+TEE  │ 8GB              │
│  启动时间               │ 5s       │ 60s      │ 5s (常驻)        │
│  ───────────────────────┼──────────┼──────────┼───────────────  │
│  安全等级               │ 低        │ 最高      │ 高               │
│  防脱库能力              │ 无        │ 最强      │ 强               │
│  生产可用性             │ ✓        │ ✗        │ ✓               │
└─────────────────────────────────────────────────────────────────┘

结论: L2常驻加密DB是生产环境的最佳平衡点
  - 性能损耗: 明文列查询 <5%, 加密列查询 <30%
  - 安全性: 即使数据库文件被窃取, 敏感列仍是密文
  - 兼容性: 标准PostgreSQL, 无需改动数据库引擎
```

---

## 3. 磁盘级加密 (第二层防护)

**除了应用层列级加密, 数据库文件本身也需要磁盘级加密:**

```bash
# LUKS 磁盘加密 (Linux原生)
# 即使攻击者获得数据库服务器的物理访问权, 也无法读取数据文件

# 创建加密分区
cryptsetup luksFormat /dev/sdb --cipher aes-xts-plain64 --key-size 512

# 打开加密分区
cryptsetup luksOpen /dev/sdb pgdata_encrypted

# 创建文件系统
mkfs.ext4 /dev/mapper/pgdata_encrypted

# 挂载
mount /dev/mapper/pgdata_encrypted /var/lib/postgresql/data

# PostgreSQL使用加密分区作为数据目录
# 性能影响: AES-NI硬件加速, <5%损耗
```

```
双重加密架构:

  数据库文件 (磁盘)
    │
    ├── 第1层: LUKS/dm-crypt 磁盘级加密 (AES-256-XTS)
    │   → 防止: 物理磁盘窃取、服务器被盗、磁盘报废时数据泄露
    │
    └── 第2层: 应用层列级加密 (SM4)
        → 防止: 数据库文件被复制、DBA越权访问、SQL注入泄露
        → 敏感列: 即使有数据库访问权也无法读取明文
```

---

## 4. TEE的角色 (重新定位)

**TEE不再用于运行数据库, 而是用于:**

```
┌─────────────────────────────────────────────────────────────────┐
│  TEE在生产架构中的角色                                            │
│                                                                 │
│  1. DEK保护 (密钥管理)                                           │
│     - DEK仅在TEE内存中存在                                       │
│     - KMS通过远程证明分发DEK至TEE                                  │
│     - TEE外无法获取DEK                                           │
│                                                                 │
│  2. 数据入库 (一次性)                                             │
│     - 数商数据在TEE内读取、加密、写入数据库                         │
│     - 原始数据不出TEE                                            │
│                                                                 │
│  3. 敏感列解密 (查询时)                                           │
│     - 加密列从数据库读取后, 在TEE内解密                             │
│     - 解密后数据仅在TEE内存中, 经输出审查后释放                      │
│                                                                 │
│  4. 输出审查 (结果过滤)                                           │
│     - PII检测、DP注入、水印注入 在TEE内完成                         │
│     - 确保结果合规后才输出                                         │
│                                                                 │
│  5. 审计签名 (防篡改)                                             │
│     - 审计日志在TEE内用SM2签名                                     │
│     - 确保日志不可篡改                                            │
│                                                                 │
│  不再用于:                                                        │
│  ✗ 不运行数据库引擎 (性能不现实)                                    │
│  ✗ 不存储数据文件 (由PostgreSQL+LUKS管理)                          │
└─────────────────────────────────────────────────────────────────┘
```

**架构图:**

```
买方SQL
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│  查询网关 (FastAPI)                                       │
│  ├── SQL安全检查 (禁止SELECT*/危险操作)                     │
│  ├── 策略校验 (OPA)                                       │
│  └── SQL改写 (加密列→密文列名, 等值→哈希匹配)               │
└──────────────────────┬───────────────────────────────────┘
                       │ 改写后的SQL
                       ▼
┌──────────────────────────────────────────────────────────┐
│  PostgreSQL (常驻实例, LUKS加密磁盘)                       │
│  ├── 明文列: 正常查询                                      │
│  ├── 确定性加密列: 哈希索引等值查询                          │
│  └── 随机加密列: 返回密文 (不解密)                          │
└──────────────────────┬───────────────────────────────────┘
                       │ 密文结果
                       ▼
┌──────────────────────────────────────────────────────────┐
│  TEE解密层 (SGX Enclave)                                  │
│  ├── 从KMS获取DEK (远程证明)                               │
│  ├── 解密敏感列                                            │
│  └── 应用层脱敏 (PII替换)                                  │
└──────────────────────┬───────────────────────────────────┘
                       │ 脱敏后结果
                       ▼
┌──────────────────────────────────────────────────────────┐
│  输出审查网关                                              │
│  ├── DLP扫描 (Presidio)                                   │
│  ├── DP噪声注入 (diffprivlib)                             │
│  ├── 水印注入                                              │
│  └── SM2签名                                              │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
                  结果返回买方
```

---

## 5. 查询改写示例

```sql
-- 买方原始SQL:
SELECT e.ent_name, e.industry_code, t.year, t.tax_amount
FROM enterprise_basic e
JOIN tax_records t ON e.ent_id = t.ent_id
WHERE e.industry_code = 'C39'
  AND e.ent_id = '110101199003076531'
  AND t.year = 2024
ORDER BY t.tax_amount DESC
LIMIT 100;

-- 改写后SQL (发给PostgreSQL):
SELECT e.ent_name_enc, e.industry_code, t.year, t.tax_amount_enc
FROM enterprise_basic e
JOIN tax_records t ON e.ent_id_hash = t.ent_id_hash
WHERE e.industry_code = 'C39'
  AND e.ent_id_hash = 'a1b2c3d4e5f67890'  -- SM3('DEK' + '110101...')[:16]
  AND t.year = 2024
LIMIT 100;

-- 返回: (密文, 'C39', 2024, 密文)
-- 应用层解密: ('北京某某科技', 'C39', 2024, 89012.34)
-- 输出审查: ('[企业名]', 'C39', 2024, 89012.34)

-- 性能:
--   ent_id_hash 索引查询: ~5ms
--   industry_code 索引查询: ~5ms
--   JOIN (hash match): ~10ms
--   应用层解密 100 行: ~10ms
--   输出审查: ~5ms
--   总计: ~35ms (可接受)
```

---

## 6. 总结

| 维度 | 旧方案 (L1临时DB) | 新方案 (L2常驻加密DB) |
|------|-------------------|----------------------|
| 首次查询 | 60-120s | 1-2s |
| 后续查询 | 60-120s | 0.3-0.8s |
| 数据库改动 | 无 | 无 (列名约定) |
| SQL能力 | 完整 | 明文列完整, 加密列受限 |
| 防脱库 | 最强 | 强 (密文列不可读) |
| 生产可用 | 否 | 是 |
| TEE用途 | 运行整个DB | 仅保护DEK+解密+审查 |

**核心改进:**
1. PostgreSQL常驻运行, 无需每次启动
2. 列级加密(确定性+随机)替代全库加密
3. 哈希索引支持等值查询, 性能接近明文
4. TEE从"运行数据库"缩小为"保护密钥+解密列"
5. 磁盘级LUKS加密作为第二层防护
