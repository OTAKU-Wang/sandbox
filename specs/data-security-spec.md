# 密态沙箱系统 · 结构化数据安全存储与防脱库设计

> 核心问题: 各类数据库数据导入沙箱后, 如何保证不被脱库(批量导出/内存dump/SQL注入泄露)

---

## 1. 威胁模型: "脱库"攻击面

```
攻击者视角 (买方/内部人员/外部入侵者):

┌─────────────────────────────────────────────────────────────┐
│                    脱库攻击面分析                              │
│                                                             │
│  ① 传输层: 中间人攻击截获数据导入流量                          │
│  ② 存储层: 直接读取沙箱磁盘上的数据库文件                       │
│  ③ 内存层: 内存dump获取明文数据                                │
│  ④ 查询层: SELECT * 全表导出 / 分批 LIMIT 泄露                 │
│  ⑤ 网络层: 查询结果通过网络通道外传                            │
│  ⑥ 代码层: 买方代码中嵌入数据窃取逻辑                          │
│  ⑦ 运维层: 运维人员通过特权访问原始数据                         │
│  ⑧ 侧信道: 通过查询时间/错误信息推断数据                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 数据导入安全架构

### 2.1 导入方式与安全模型

```
┌──────────────────────────────────────────────────────────────┐
│                    数据导入安全架构                              │
│                                                              │
│  方式一: 文件导入 (CSV/Parquet/Excel)                         │
│  ┌──────────┐    SM4-GCM     ┌──────────┐    SM4-GCM        │
│  │ 数商本地  │───加密传输────→│ TEE内解密 │───重加密────────→ │
│  │ 原始文件  │   (TLCP通道)   │ (临时内存) │   (新DEK)        │
│  └──────────┘                └──────────┘                   │
│                                    │                        │
│                              ┌─────▼─────┐                  │
│                              │ 加密存储    │                  │
│                              │ (MinIO)    │                  │
│                              └───────────┘                  │
│                                                              │
│  方式二: 数据库直连导入 (JDBC/ODBC)                            │
│  ┌──────────┐   TLS/TLCP    ┌──────────┐    SM4-GCM        │
│  │ 源数据库  │←──TEE内建立──→│ TEE内拉取 │───加密写入──────→ │
│  │ MySQL/PG  │   加密连接    │ 明文数据   │   (逐块加密)      │
│  │ Oracle/DM │   (仅在TEE内) │ (不落盘)   │                  │
│  └──────────┘                └──────────┘                   │
│                                                              │
│  方式三: 增量同步 (CDC)                                       │
│  ┌──────────┐   Binlog      ┌──────────┐    SM4-GCM        │
│  │ 源数据库  │───CDC流──────→│ TEE内解析 │───加密追加──────→ │
│  └──────────┘   (加密通道)   │ + 加密    │                  │
│                              └──────────┘                   │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 数据库直连导入详细流程

```python
# import/db_importer.py

class SecureDBImporter:
    """安全数据库导入器 - 全程在TEE内执行"""

    SUPPORTED_DBS = {
        "mysql":      {"driver": "pymysql",    "port": 3306},
        "postgresql": {"driver": "psycopg2",   "port": 5432},
        "oracle":     {"driver": "oracledb",   "port": 1521},
        "sqlserver":  {"driver": "pyodbc",     "port": 1433},
        "dm":         {"driver": "dmPython",   "port": 5236},   # 达梦
        "kingbase":   {"driver": "kingbase8",  "port": 54321},  # 人大金仓
        "oceanbase":  {"driver": "ob_python",  "port": 2881},   # OceanBase
        "gaussdb":    {"driver": "psycopg2",   "port": 5432},   # GaussDB
    }

    def __init__(self, kms_client, sm4_cipher):
        self.kms = kms_client
        self.sm4 = sm4_cipher

    def import_from_db(
        self,
        product_id: str,
        db_type: str,
        connection_config: dict,  # host/port/database/table/user/password
        import_config: dict,       # batch_size/max_rows/columns/query
    ) -> dict:
        """
        安全数据库导入流程 (全程TEE内):

        1. 连接凭证仅在TEE内存中解密
        2. 建立TLS/TLCP加密连接到源数据库
        3. 分批拉取数据 (每批10,000行)
        4. 每批立即SM4-GCM加密后写入MinIO
        5. 明文数据立即从内存擦除
        6. 全程审计日志
        """

        # Step 1: 从KMS获取连接凭证 (TEE内解密)
        creds = self.kms.decrypt_credentials(connection_config["credential_ref"])

        # Step 2: 建立安全连接
        conn = self._create_secure_connection(db_type, creds)

        # Step 3: 获取DEK
        dek = self.kms.get_or_create_dek(product_id)

        # Step 4: 分批导入+加密
        batch_size = import_config.get("batch_size", 10000)
        query = import_config.get("query", f"SELECT * FROM {connection_config['table']}")
        # 安全检查: 禁止无条件全表扫描
        self._validate_import_query(query)

        cursor = conn.cursor()
        cursor.execute(query)

        total_rows = 0
        total_bytes = 0
        batch_idx = 0

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            # Step 4a: 序列化
            batch_data = self._serialize_batch(rows, cursor.description)

            # Step 4b: SM4-GCM加密 (每批独立IV)
            iv = os.urandom(16)
            encrypted = self.sm4.encrypt_gcm(batch_data, iv)

            # Step 4c: 写入MinIO (加密后的数据)
            object_name = f"import/{product_id}/batch_{batch_idx:06d}.enc"
            self.minio.put_object(
                "sandbox-data-products",
                object_name,
                io.BytesIO(encrypted),
                len(encrypted),
                metadata={
                    "iv": iv.hex(),
                    "batch_idx": str(batch_idx),
                    "row_count": str(len(rows)),
                    "sm3_hash": sm3_hash(encrypted).hex(),
                }
            )

            # Step 4d: 明文立即擦除
            self._secure_wipe(batch_data)
            self._secure_wipe(rows)

            total_rows += len(rows)
            total_bytes += len(encrypted)
            batch_idx += 1

        # Step 5: 记录导入元数据
        schema = self._infer_schema(cursor.description)

        # Step 6: 清理
        cursor.close()
        conn.close()
        self._secure_wipe(creds)  # 擦除连接凭证

        return {
            "product_id": product_id,
            "total_rows": total_rows,
            "total_batches": batch_idx,
            "total_encrypted_bytes": total_bytes,
            "schema": schema,
            "encryption": "SM4-GCM",
            "key_ref": f"sandbox-dek-{product_id}",
        }

    def _validate_import_query(self, query: str):
        """安全校验导入SQL"""
        q = query.upper().strip()

        # 禁止: 无条件全表扫描
        if "SELECT *" in q and "WHERE" not in q and "LIMIT" not in q:
            raise SecurityError("禁止无条件全表扫描, 必须指定WHERE或LIMIT")

        # 禁止: DDL/DML操作
        for keyword in ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "GRANT"]:
            if keyword in q:
                raise SecurityError(f"禁止{keyword}操作")

        # 禁止: 子查询泄露
        if q.count("SELECT") > 1:
            raise SecurityError("禁止子查询, 仅允许单层SELECT")

    def _create_secure_connection(self, db_type: str, creds: dict):
        """创建安全数据库连接"""
        driver_info = self.SUPPORTED_DBS[db_type]

        if db_type == "mysql":
            import pymysql
            return pymysql.connect(
                host=creds["host"],
                port=creds.get("port", 3306),
                user=creds["username"],
                password=creds["password"],
                database=creds["database"],
                ssl={"ca": "/etc/certs/ca.pem"},  # TLS加密
                connect_timeout=30,
                read_timeout=300,
            )
        elif db_type == "postgresql":
            import psycopg2
            return psycopg2.connect(
                host=creds["host"],
                port=creds.get("port", 5432),
                user=creds["username"],
                password=creds["password"],
                dbname=creds["database"],
                sslmode="verify-full",
                sslrootcert="/etc/certs/ca.pem",
                connect_timeout=30,
                options="-c statement_timeout=300000",
            )
        elif db_type == "dm":
            import dmPython
            return dmPython.connect(
                user=creds["username"],
                password=creds["password"],
                server=creds["host"],
                port=creds.get("port", 5236),
            )
        # ... 其他数据库类似

    def _secure_wipe(self, data):
        """安全擦除内存中的明文数据"""
        if isinstance(data, (bytes, bytearray)):
            # 覆写内存
            for i in range(len(data)):
                if isinstance(data, bytearray):
                    data[i] = 0
            ctypes.memset(id(data), 0, sys.getsizeof(data))
        elif isinstance(data, list):
            data.clear()
            del data
        gc.collect()  # 强制GC
```

### 2.3 连接凭证安全

```yaml
# 连接凭证管理方案
CredentialManagement:
  storage: "Vault KV v2 (加密存储)"
  lifecycle:
    1_register: "数商通过Vault API注册数据库连接凭证"
    2_encrypt: "Vault使用SM4加密存储凭证"
    3_reference: "数据产品元数据仅存储credential_ref (Vault路径)"
    4_access: "导入时TEE内从Vault拉取, 用完立即擦除"
    5_rotation: "支持凭证定期轮转"

  # 凭证在TEE内的生命周期
  tee_lifecycle:
    - "Vault Agent Token 通过TEE远程证明分发"
    - "TEE内调用Vault API获取凭证明文"
    - "凭证仅存在于TEE加密内存中"
    - "建立数据库连接后立即擦除凭证"
    - "连接关闭后擦除连接句柄"
```

---

## 3. 沙箱内安全存储架构

### 3.1 存储层次

```
┌──────────────────────────────────────────────────────────────┐
│                    沙箱内数据存储层次                            │
│                                                              │
│  Layer 1: 明文层 (仅在TEE加密内存中)                           │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  SQL执行引擎 / Pandas DataFrame / 内存数据库          │    │
│  │  • 数据仅在TEE Enclave内存中以明文存在                  │    │
│  │  • 内存由CPU硬件加密 (SGX EPC / AMD SME)               │    │
│  │  • 任务结束后安全擦除                                   │    │
│  │  • 禁止swap到磁盘 (mlock + cgroup memory.limit)       │    │
│  └──────────────────────────────────────────────────────┘    │
│                          ↕ SM4-GCM (DEK)                     │
│  Layer 2: 加密层 (MinIO 对象存储)                             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  MinIO (SM4-GCM 服务端加密)                            │    │
│  │  • 每个数据产品独立DEK                                  │    │
│  │  • 块级加密 (16MB/块, 独立IV)                          │    │
│  │  • Merkle Tree完整性校验                               │    │
│  │  • DEK由KMS(HSM)托管, MinIO无法自行解密                │    │
│  └──────────────────────────────────────────────────────┘    │
│                          ↕ KMS (HSM保护)                     │
│  Layer 3: 密钥层 (HashiCorp Vault + HSM)                     │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  Vault Transit (密钥管理) + HSM (主密钥保护)            │    │
│  │  • DEK由Vault Transit生成和托管                        │    │
│  │  • DEK不可导出 (transit/encrypt API)                   │    │
│  │  • 会话密钥 (Session Key) 保护DEK跨节点传输             │    │
│  │  • 主密钥 (Master Key) 在HSM中, 不可提取               │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 数据在沙箱内的状态模型

```
数据生命周期:

[导入中] → [加密存储] → [查询时解密] → [结果审查] → [结果输出]
  │            │              │              │           │
  │            │              │              │           │
TEE内存      MinIO磁盘     TEE内存        TEE内存      买方
(临时明文)   (SM4密文)     (临时明文)     (审查后)     (脱敏结果)
  │            │              │              │
  ↓            │              ↓              ↓
擦除          │            擦除           擦除
              │
              ↓ (合约到期/密钥吊销)
         密文不可解密 (数据"销毁")
```

### 3.3 防止数据库文件被直接读取

```python
# storage/secure_storage.py

class SecureSandboxStorage:
    """
    沙箱内安全存储层

    核心原则:
    1. 磁盘上永远只有密文 (SM4-GCM加密)
    2. 明文仅在TEE加密内存中短暂存在
    3. 数据库引擎运行在tmpfs上 (内存文件系统, 不落盘)
    """

    def __init__(self, kms_client):
        self.kms = kms_client

    def create_secure_db(self, product_id: str, schema: dict):
        """
        创建安全数据库实例

        存储策略:
        - SQLite/DuckDB: 运行在tmpfs上, 数据文件SM4加密后持久化到MinIO
        - PostgreSQL: 使用pgcrypto列级加密, 数据目录在加密卷上
        - 内存数据库(推荐): 直接在TEE内存中, 不落盘
        """
        # 方案A: 内存数据库 (推荐, 最安全)
        # 使用DuckDB内存模式, 数据从MinIO加载后解密到内存
        import duckpy
        db = duckpy.connect(":memory:")
        # 从MinIO加载加密数据, TEE内解密后导入
        return db

        # 方案B: tmpfs上的SQLite
        # tmpfs = 内存文件系统, 掉电即销毁
        # os.makedirs("/dev/shm/sandbox_dbs", exist_ok=True)
        # db_path = f"/dev/shm/sandbox_dbs/{product_id}.db"
        # 数据库文件在tmpfs上, 但数据本身仍需列级加密

    def load_encrypted_data_to_db(self, product_id: str, db, table_name: str):
        """
        从MinIO加载加密数据到内存数据库

        流程:
        1. 从KMS获取DEK (TEE内)
        2. 从MinIO下载加密数据块
        3. TEE内SM4-GCM解密
        4. 导入内存数据库
        5. 临时解密数据立即擦除缓冲区
        """
        dek = self.kms.get_dek(product_id)

        # 列出所有加密块
        blocks = self.minio.list_objects("sandbox-data-products", prefix=f"import/{product_id}/")

        for block in blocks:
            # 下载加密块
            encrypted_data = self.minio.get_object("sandbox-data-products", block.object_name)
            iv = bytes.fromhex(block.metadata["iv"])

            # TEE内解密
            plaintext = self.sm4.decrypt_gcm(encrypted_data, iv)

            # 导入内存数据库
            self._import_to_memory_db(db, table_name, plaintext)

            # 立即擦除解密缓冲区
            self._secure_wipe(plaintext)
            self._secure_wipe(encrypted_data)

    def export_query_result(self, db, query: str, product_id: str) -> bytes:
        """
        执行查询并返回结果 (结果经过输出审查)

        安全约束:
        - 查询在TEE内执行
        - 结果经输出审查网关
        - 仅审查通过的结果返回
        """
        # 执行查询
        result = db.execute(query).fetchall()

        # 序列化结果
        result_bytes = self._serialize_result(result)

        # 输出审查 (由OutputInspectionGateway处理)
        # ... 见输出审查网关设计

        return result_bytes
```

### 3.4 内存安全保护

```python
# security/memory_protection.py

import ctypes
import mmap
import resource

class MemoryProtection:
    """TEE内内存安全保护"""

    @staticmethod
    def lock_memory():
        """
        防止内存被swap到磁盘

        mlockall: 将进程所有内存锁定在物理RAM中
        即使系统内存不足, 也不会将敏感数据换出到磁盘
        """
        # mlockall(MCL_CURRENT | MCL_FUTURE)
        libc = ctypes.CDLL("libc.so.6")
        libc.mlockall(3)  # MCL_CURRENT=1, MCL_FUTURE=2

        # 设置cgroup内存限制 (K8s)
        # memory.limit_in_bytes = 硬限制
        # memory.swappiness = 0 (禁止swap)

    @staticmethod
    def secure_malloc(size: int) -> mmap.mmap:
        """
        安全内存分配

        使用mmap分配匿名内存页:
        - MAP_PRIVATE: 私有映射
        - MAP_ANONYMOUS: 不关联文件
        - MAP_LOCKED: 锁定在内存中
        """
        mem = mmap.mmap(-1, size, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        # mlock这片内存
        libc = ctypes.CDLL("libc.so.6")
        libc.mlock(ctypes.c_void_p(id(mem)), size)
        return mem

    @staticmethod
    def secure_wipe(ptr, size: int):
        """
        安全擦除内存

        使用explicit_bzero (如果可用) 或手动物化覆写
        确保编译器不会优化掉擦除操作
        """
        try:
            libc = ctypes.CDLL("libc.so.6")
            libc.explicit_bzero(ptr, size)
        except AttributeError:
            # explicit_bzero不可用, 使用volatile写入
            ctypes.memset(ptr, 0xAA, size)  # 先写0xAA
            ctypes.memset(ptr, 0x55, size)  # 再写0x55
            ctypes.memset(ptr, 0x00, size)  # 最后写0x00

    @staticmethod
    def disable_core_dump():
        """禁止core dump (防止内存泄露到磁盘)"""
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    @staticmethod
    def set_no_swap():
        """通过cgroup禁止swap (K8s环境)"""
        try:
            with open("/sys/fs/cgroup/memory/memory.swappiness", "w") as f:
                f.write("0")
        except FileNotFoundError:
            # cgroup v2
            try:
                with open("/sys/fs/cgroup/memory.swap.max", "w") as f:
                    f.write("0")
            except FileNotFoundError:
                pass  # 非cgroup环境
```

---

## 4. 防脱库: 查询层安全控制

### 4.1 查询安全策略

```python
# security/query_guard.py

import sqlparse
from sqlparse.sql import Statement, Where, Identifier, Function
from sqlparse.tokens import Keyword, DML

class QuerySecurityGuard:
    """
    查询层防脱库引擎

    核心防线:
    1. SQL解析 + AST分析
    2. 行为模式检测 (批量导出模式)
    3. 实时配额控制
    4. 结果集大小限制
    """

    # 危险查询模式
    DANGEROUS_PATTERNS = [
        "SELECT *",                    # 全字段导出
        "INTO OUTFILE",                # 写入文件
        "INTO DUMPFILE",               # 写入二进制文件
        "LOAD_FILE",                   # 读取文件
        "BENCHMARK(",                  # 时间盲注
        "SLEEP(",                      # 时间盲注
        "WAITFOR DELAY",               # SQL Server时间盲注
        "DBMS_PIPE.RECEIVE_MESSAGE",   # Oracle时间盲注
        "UNION SELECT",                # UNION注入
        "INFORMATION_SCHEMA",          # 元数据泄露
        "SHOW TABLES",                 # 表枚举
        "SHOW DATABASES",              # 数据库枚举
        "DESCRIBE ",                   # Schema探测
        "EXPLAIN ",                    # 执行计划泄露
    ]

    # 必须包含的约束
    REQUIRED_CONSTRAINTS = {
        "max_output_rows": 1000,       # 默认最大输出行数
        "require_limit": True,         # 是否强制LIMIT
        "require_where": True,         # 是否强制WHERE
        "block_select_star": True,     # 是否禁止SELECT *
    }

    def validate_query(self, query: str, policy: dict) -> tuple[bool, str, str]:
        """
        验证查询安全性

        Returns: (allowed, reason, sanitized_query)
        """
        q = query.strip()
        q_upper = q.upper()

        # 1. SQL语法解析
        parsed = sqlparse.parse(q)
        if not parsed:
            return False, "SQL解析失败", ""

        stmt = parsed[0]
        stmt_type = stmt.get_type()

        # 2. 仅允许SELECT
        if stmt_type != "SELECT":
            return False, f"仅允许SELECT查询, 检测到: {stmt_type}", ""

        # 3. 危险模式检测
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in q_upper:
                return False, f"检测到危险模式: {pattern}", ""

        # 4. 强制LIMIT
        if policy.get("require_limit", True):
            if "LIMIT" not in q_upper:
                max_rows = policy.get("max_output_rows", 1000)
                q = f"{q.rstrip(';')} LIMIT {max_rows}"
                # 不拒绝, 而是自动添加LIMIT

        # 5. SELECT * 检测
        if policy.get("block_select_star", True):
            if "SELECT *" in q_upper:
                return False, "禁止SELECT *, 请指定具体字段", ""

        # 6. 子查询深度限制
        subquery_count = q_upper.count("SELECT") - 1
        if subquery_count > 2:
            return False, f"子查询嵌套过深 ({subquery_count}层), 最多允许2层", ""

        # 7. 聚合函数检测 (允许聚合, 限制输出)
        has_aggregate = any(fn in q_upper for fn in ["COUNT(", "SUM(", "AVG(", "MIN(", "MAX("])
        if has_aggregate:
            # 聚合查询通常安全 (不泄露个体数据)
            pass

        # 8. ORDER BY + LIMIT 检测 (可能用于分批导出)
        if "ORDER BY" in q_upper and "LIMIT" in q_upper:
            # 检查是否是分批导出模式 (LIMIT offset, count)
            import re
            limit_match = re.search(r'LIMIT\s+(\d+)\s*,\s*(\d+)', q_upper)
            if limit_match:
                offset = int(limit_match.group(1))
                count = int(limit_match.group(2))
                if offset > 10000 or count > 1000:
                    return False, "检测到分批导出模式, 禁止", ""

        return True, "查询安全检查通过", q


class BehaviorAnalyzer:
    """
    查询行为分析器 - 检测脱库行为模式

    检测模式:
    1. 分批导出: 短时间内大量LIMIT offset,count查询
    2. 全表扫描: 多次查询覆盖所有行
    3. 字段枚举: 逐个字段查询
    4. 异常频率: 查询频率突然升高
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    def analyze(self, session_id: str, query: str, result_rows: int) -> tuple[bool, str]:
        """
        分析查询行为, 检测脱库模式

        Returns: (is_suspicious, reason)
        """
        import re

        # 记录查询历史
        key = f"query_history:{session_id}"
        self.redis.lpush(key, json.dumps({
            "query": query[:500],  # 截断保存
            "rows": result_rows,
            "timestamp": time.time(),
        }))
        self.redis.ltrim(key, 0, 99)  # 保留最近100条
        self.redis.expire(key, 3600)

        # 检测分批导出模式
        recent_queries = self.redis.lrange(key, 0, 19)  # 最近20条
        offsets = []
        for q_json in recent_queries:
            q = json.loads(q_json)["query"]
            match = re.search(r'LIMIT\s+(\d+)\s*,\s*\d+', q.upper())
            if match:
                offsets.append(int(match.group(1)))

        if len(offsets) >= 5:
            # 检查是否是递增offset (分批导出特征)
            sorted_offsets = sorted(offsets)
            if sorted_offsets == offsets and len(set(offsets)) >= 5:
                return True, "检测到分批导出模式 (递增LIMIT offset)"

        # 检测累计输出量
        total_rows_key = f"total_output:{session_id}"
        total = self.redis.incrby(total_rows_key, result_rows)
        self.redis.expire(total_rows_key, 3600)

        if total > 10000:  # 累计输出超过10,000行
            return True, f"累计输出行数异常: {total}行"

        # 检测查询频率
        freq_key = f"query_freq:{session_id}"
        count = self.redis.incr(freq_key)
        self.redis.expire(freq_key, 60)  # 1分钟窗口

        if count > 30:  # 1分钟内超过30次查询
            return True, f"查询频率异常: {count}次/分钟"

        return False, "行为正常"
```

### 4.2 查询执行沙箱

```python
# runtime/query_executor.py

class SecureQueryExecutor:
    """
    安全查询执行器

    执行流程:
    1. SQL安全校验 (QuerySecurityGuard)
    2. 行为分析 (BehaviorAnalyzer)
    3. 策略引擎校验 (OPA)
    4. TEE内执行查询
    5. 结果集大小检查
    6. 输出审查网关
    7. DP噪声注入
    8. 水印注入
    9. SM2签名
    10. 返回结果
    """

    def __init__(self, query_guard, behavior_analyzer, opa_client,
                 output_inspector, dp_manager, watermark, sm2_signer):
        self.guard = query_guard
        self.behavior = behavior_analyzer
        self.opa = opa_client
        self.inspector = output_inspector
        self.dp = dp_manager
        self.watermark = watermark
        self.signer = sm2_signer

    async def execute(
        self,
        session_id: str,
        contract_id: str,
        query: str,
        db_connection,  # 内存数据库连接
    ) -> dict:

        # 1. SQL安全校验
        policy = await self._get_contract_policy(contract_id)
        allowed, reason, sanitized_query = self.guard.validate_query(query, policy)
        if not allowed:
            self._log_security_event(session_id, "query_blocked", reason)
            return {"status": "blocked", "reason": reason}

        # 2. 行为分析
        is_suspicious, behavior_reason = self.behavior.analyze(session_id, query, 0)
        if is_suspicious:
            self._log_security_event(session_id, "behavior_suspicious", behavior_reason)
            return {"status": "blocked", "reason": f"行为异常: {behavior_reason}"}

        # 3. OPA策略校验
        policy_result = await self.opa.evaluate(
            "confidential.sandbox",
            subject={"session_id": session_id, "contract_id": contract_id},
            resource={"query": sanitized_query, "max_rows": policy["max_output_rows"]},
            action={"type": "query", "output_rows": 0},
        )
        if not policy_result["allow"]:
            return {"status": "blocked", "reason": f"策略拒绝: {policy_result.get('reason')}"}

        # 4. TEE内执行查询
        try:
            cursor = db_connection.cursor()
            cursor.execute(sanitized_query)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
        except Exception as e:
            return {"status": "error", "reason": f"查询执行失败: {str(e)}"}

        # 5. 结果集大小检查
        if len(rows) > policy.get("max_output_rows", 1000):
            return {"status": "blocked", "reason": f"结果行数超限: {len(rows)} > {policy['max_output_rows']}"}

        # 6. 输出审查网关
        inspection = self.inspector.inspect(rows, columns, policy)
        if inspection["status"] == "blocked":
            return {"status": "blocked", "reason": inspection["reason"]}

        # 7. DP噪声注入
        if policy.get("require_dp", True):
            epsilon = policy.get("dp_epsilon", 1.0)
            if not self.dp.consume(contract_id, epsilon):
                return {"status": "blocked", "reason": "DP预算耗尽"}
            rows = self.dp.inject_noise(rows, columns, epsilon)

        # 8. 水印注入
        rows = self.watermark.inject(rows, session_id)

        # 9. SM2签名
        result_data = {"columns": columns, "rows": rows}
        result_bytes = json.dumps(result_data, ensure_ascii=False).encode()
        signature = self.signer.sign(result_bytes)

        # 10. 审计日志
        self._log_query_result(session_id, len(rows), len(result_bytes))

        return {
            "status": "completed",
            "result": result_data,
            "signature": signature.hex(),
            "row_count": len(rows),
            "dp_epsilon_used": epsilon if policy.get("require_dp") else 0,
        }
```

---

## 5. 安全存储配置

### 5.1 MinIO 加密配置

```yaml
# MinIO服务端加密配置 (SSE-S4, 国密SM4)
# minio-env.sh
MINIO_KMS_SECRET_KEY="my-key:0123456789abcdef0123456789abcdef"
# 或对接Vault Transit
MINIO_KMS_VAULT_ENDPOINT="https://vault:8200"
MINIO_KMS_VAULT_AUTH_TYPE="approle"
MINIO_KMS_VAULT_SECRETS_ENGINE="transit"
MINIO_KMS_VAULT_KEY_NAME="sandbox-minio-key"
```

### 5.2 K8s 加密卷配置

```yaml
# 使用dm-crypt加密PVC (L2环境)
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: sandbox-data-pvc
  namespace: confidential-sandbox
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: encrypted-ssd
  resources:
    requests:
      storage: 500Gi
---
# StorageClass with encryption
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: encrypted-ssd
provisioner: kubernetes.io/aws-ebs  # 或其他CSI
parameters:
  encrypted: "true"
  kmsKeyId: "alias/sandbox-key"
```

### 5.3 tmpfs 配置 (内存文件系统)

```yaml
# 沙箱Pod使用tmpfs存储临时数据
apiVersion: v1
kind: Pod
metadata:
  name: sandbox-worker
spec:
  containers:
  - name: worker
    image: sandbox/worker:latest
    volumeMounts:
    - name: tmpfs-data
      mountPath: /dev/shm/sandbox
    - name: encrypted-data
      mountPath: /data/encrypted
    securityContext:
      readOnlyRootFilesystem: true
      runAsNonRoot: true
  volumes:
  - name: tmpfs-data
    emptyDir:
      medium: Memory          # 使用内存(tmpfs), 不落盘
      sizeLimit: 16Gi         # 内存限制
  - name: encrypted-data
    persistentVolumeClaim:
      claimName: sandbox-data-pvc
```

---

## 6. 防脱库检查清单

```
┌──────────────────────────────────────────────────────────────┐
│                    防脱库安全检查清单                            │
│                                                              │
│  传输层:                                                     │
│  ✓ 数据导入全程SM4-GCM加密传输 (TLCP)                         │
│  ✓ 数据库连接在TEE内建立, TLS/TLCP加密                         │
│  ✓ 连接凭证仅在TEE内存中, 用完立即擦除                          │
│                                                              │
│  存储层:                                                     │
│  ✓ 磁盘上只有SM4-GCM密文 (MinIO SSE)                          │
│  ✓ DEK由Vault Transit托管, 不可导出                            │
│  ✓ 主密钥在HSM中, 不可提取                                     │
│  ✓ 临时数据在tmpfs上 (内存文件系统, 掉电即销毁)                   │
│  ✓ 数据库引擎运行在tmpfs或内存模式                               │
│                                                              │
│  内存层:                                                     │
│  ✓ TEE硬件内存加密 (SGX EPC / AMD SME)                        │
│  ✓ mlockall禁止swap到磁盘                                     │
│  ✓ cgroup禁止swap (memory.swappiness=0)                      │
│  ✓ 禁止core dump (RLIMIT_CORE=0)                             │
│  ✓ 任务结束后安全擦除内存 (explicit_bzero)                      │
│                                                              │
│  查询层:                                                     │
│  ✓ SQL解析+AST分析 (禁止危险模式)                               │
│  ✓ 禁止SELECT * (必须指定字段)                                  │
│  ✓ 强制LIMIT (最大输出行数)                                     │
│  ✓ 禁止INTO OUTFILE/DUMPFILE (防止写文件)                      │
│  ✓ 禁止UNION SELECT (防止注入)                                 │
│  ✓ 分批导出检测 (递增LIMIT offset模式)                          │
│  ✓ 累计输出量限制 (单会话≤10,000行)                             │
│  ✓ 查询频率限制 (≤30次/分钟)                                   │
│                                                              │
│  代码层:                                                     │
│  ✓ AST白名单 (禁止import os/subprocess/socket)                │
│  ✓ RestrictedPython编译 (禁止危险内置函数)                      │
│  ✓ 禁止网络调用 (Network Namespace隔离)                        │
│  ✓ 禁止文件写入 (ReadOnlyRootFilesystem)                       │
│  ✓ 禁止进程创建 (Seccomp-BPF白名单)                            │
│                                                              │
│  运维层:                                                     │
│  ✓ 运维人员零权限访问沙箱内数据                                  │
│  ✓ 所有运维操作通过外部接口并记录                                 │
│  ✓ 审计日志不可篡改 (SM2签名 + 链上存证)                        │
└──────────────────────────────────────────────────────────────┘
```
