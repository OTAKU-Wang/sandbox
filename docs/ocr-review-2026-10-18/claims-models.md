### [app/models/api_endpoint.py] medium/bug located=true
`slug` 声明为**全局唯一**（`unique=True`），但本模型语义是“合约下的交付端点”（见模块 docstring，且 `contract_id`/`owner_id` 为归属字段）。若路由实际按合约维度解析（如 `/contracts/{contract_id}/endpoints/{slug}`），全局唯一会带来两个问题：1) 不同合约/租户之间发生 slug 抢注与命名冲突，后建方无法使用常见名字（`predict`、`query`），属于跨租户可用性/信息泄露面；2) 与 `Index("ix_api_endpoints_contract_status", "contract_id", ...)` 体现的“按合约检索”意图不一致。若确认路由是全局扁平路径（`/endpoints/{slug}`），请补注释说明全局唯一是刻意的；否则应改为合约内唯一。另注：模型层未约束 slug 字符集/大小写，仅靠 `String(64)` 无法防止 `Abc` 与 `abc` 这类“大小写不同但实际冲突”的 slug（PostgreSQL 默认大小写敏感，但 URL 常按小写归一）。
-- suggested: # 若 slug 仅在合约内寻址，改为表级约束：
    # __table_args__ = (
    #     Index("ix_api_endpoints_contract_status", "contract_id", "status"),
    #     UniqueConstraint("contract_id", "slug", name="uq_api_endpoints_contract_slug"),
    # )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)  # 全局唯一 or 合约内唯一，见上

### [app/models/api_endpoint.py] medium/security located=true
`status` 默认值为 `ENABLED`，而“是否已发布”由另一个可空字段 `published_at` 表达，两者之间没有任何约束关系。结合复合索引 `ix_api_endpoints_contract_status(contract_id, status)` 可以看出，调用侧的准入判定大概率是 `contract_id + status == 'enabled'`——这意味着端点行一经创建（`published_at IS NULL`）就处于可被调用的“enabled”状态，发布动作（`published_at` 写入）不构成准入条件，容易绕过“审核/发布后才可访问”的控制。建议：新建默认置为 `DISABLED`（发布流程再翻转），或在服务层准入查询中强制 `status == enabled AND published_at IS NOT NULL`，并在字段注释中写清不变式。
-- suggested: status: Mapped[str] = mapped_column(String(16), nullable=False, default=ApiEndpointStatus.DISABLED.value, server_default=ApiEndpointStatus.DISABLED.value)

### [app/models/api_endpoint.py] medium/bug located=true
`timeout_seconds` / `rate_limit_per_minute` / `quota_total` 只有类型约束，没有任何取值范围约束，且 `default` 仅在 Python 侧生效（`timeout_seconds`、`rate_limit_per_minute` 虽有 `server_default`，但 `quota_total` 可为任意负值）。这些字段会直接喂给网关/限流器：`rate_limit_per_minute <= 0` 通常被解释为“全量放行”或“全量拒绝”（取决于实现），`timeout_seconds <= 0` 会导致立即超时或退化为不限时。由于本目录约定“模型不含业务逻辑”，校验只可能落在 schema/service 层——建议在此加 `CheckConstraint`（或至少注释标明服务层必须校验的区间，如 timeout ∈ [1, 300]、rate_limit ∈ [1, N]、quota_total > 0），避免绕过 Pydantic 的数据写入（迁移脚本、运维 SQL、其他服务直写）落库后静默改变网关行为。
-- suggested: # __table_args__ 追加：
    # CheckConstraint("timeout_seconds > 0", name="ck_api_endpoints_timeout_positive"),
    # CheckConstraint("rate_limit_per_minute > 0", name="ck_api_endpoints_rate_positive"),
    # CheckConstraint("quota_total IS NULL OR quota_total > 0", name="ck_api_endpoints_quota_positive"),
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_de

### [app/models/api_endpoint.py] medium/performance located=true
`calls_used` 与 `quota_total` 落在同一张配置表上，是每次调用都要写的高频计数器，存在两类风险：1) 丢失更新——若服务层用“读对象 → `obj.calls_used += 1` → commit”，并发请求下会相互覆盖，配额超用；必须用 `UPDATE api_endpoints SET calls_used = calls_used + 1 WHERE id = :id AND (quota_total IS NULL OR calls_used < quota_total) RETURNING ...` 这类原子语句（同仓库 `task_queue.py` 已示范 `FOR UPDATE SKIP LOCKED` / 条件 UPDATE 的写法），此处建议加注释固化该约束。2) 计数器与配置同表，高频 UPDATE 会对端点行长期持锁，改配置（如调 `timeout_seconds`）会被调用洪峰阻塞；同时 `Integer` 为 32 位（上限约 21.4 亿），长生命周期的热门端点存在溢出风险，建议 `BigInteger`。此外 `last_called_at` 每请求更新会进一步放大该热点。
-- suggested: quota_total: Mapped[int | None] = mapped_column(BigInteger)  # 仅允许原子 UPDATE 递增，禁止 read-modify-write
    calls_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

### [app/models/blockchain_anchor.py] medium/maintainability located=true
`JSON` 类型被导入但从未使用（死代码），同时 `metadata_json` 实际以 `Text` 存储，两者语义不一致。用 `Text` 存 JSON 意味着数据库不会校验内容合法性：写入端若忘记 `json.dumps`（例如直接传入 dict，PostgreSQL 会报错或写入被强转的字符串），或下游按 `json.loads(record.metadata_json)` 读取，就会在读取时抛 `ValueError`/`TypeError`（字段为 `None` 时同样如此）。建议要么直接使用已导入的 `JSON`（或 `postgresql.JSONB`）让数据库负责校验与序列化，要么保留 `Text` 但删除未使用的 `JSON` 导入，并在写入处统一序列化 + 校验，同时为读取端提供 `None` 安全包装。
-- suggested: from sqlalchemy import String, DateTime, Boolean, func, Index, JSON

### [app/models/blockchain_anchor.py] medium/bug located=true
锚点表缺少唯一性约束，无法保证幂等：`backend`、`tx_hash`、`data_hash` 都只是普通（非唯一）索引。若同一后端对同一 `data_hash` 重复锚定，或重放/重试（如交易回执未落库后重试写入）路径被并发触发，会产生重复记录，导致审计查询 `filter_by(backend=..., data_hash=...)` 返回多行、结果不确定。建议至少加 `UniqueConstraint("backend", "data_hash", "tx_hash", name="uq_blockchain_anchors_anchor")`（按业务允许粒度取舍，`tx_hash` 为 NULL 时需配合 partial index 或先落占位值）。另外，锚点核验最常见的查询是按 `data_hash` 检索，当前仅有 `backend`/`tx_hash`/`created_at` 索引，数据量增长后该查询会退化为全表扫描，建议为 `data_hash` 补索引（或直接由唯一约束覆盖）。
-- suggested: __table_args__ = (
        UniqueConstraint(
            "backend", "data_hash", "tx_hash", name="uq_blockchain_anchors_anchor"
        ),
        Index("ix_blockchain_anchors_backend", "backend"),
        Index("ix_blockchain_anchors_data_hash", "data_hash"),
        Index("ix_blockchain_anchors_tx_hash", "tx_hash"),
        Index("ix_blockchain_anchors_created", "created_at"),
    )

### [app/models/compute_run.py] medium/bug located=true
`__table_args__` 只有两个普通查询索引，但状态枚举里存在 `SKIPPED_OVERLAP`，说明调度器需要保证"同一合约同时只有一个在跑的运行"。目前数据库层没有任何约束实现这一点：多副本调度器（本项目 `task_queue` 明确是多副本部署）各自做"查一次 pending/running → 再插入"的 check-then-act，两个副本可以在同一瞬间都为同一合约插入 `pending`/`running` 行，出现真实重叠执行，`SKIPPED_OVERLAP` 分支形同虚设。`idempotency_key` 也挡不住这种情况，因为它只按"合约+时点"去重，人工触发时为 NULL。建议补一个部分唯一索引，把不变式下沉到数据库：

```python
Index(
    "uq_compute_runs_contract_inflight", "contract_id", unique=True,
    postgresql_where=text("status IN ('pending','running')"),
    sqlite_where=text("status IN ('pending','running')"),
)
```

写入方则捕获 `IntegrityError` 并转为 `SKIPPED_OVERLAP`，而不是靠先查后插。
-- suggested: 

### [app/models/compute_run.py] medium/bug located=true
`idempotency_key` 仅靠注释约定，模型层没有任何机制阻止 `trigger='schedule'` 而 `idempotency_key` 为 NULL 的行被写入（该列默认即 NULL）。一旦发生，"同一时点只跑一次"的保证会静默失效——重复调度既不会被数据库拒绝，也不会有测试暴露，只会表现为重复计算与重复产出。另外 `attempt` 字段暗示存在重试：如果重试是以同一 `contract_id`+`scheduled_for` 新插入一行，主键会撞上上一条已终结记录的幂等键而被 `IntegrityError` 拒绝。建议至少：(1) 对调度触发用部分唯一索引显式表达约束（并保证写入方处理 `IntegrityError` 而非吞掉异常）；(2) 若重试会新建行，把 `attempt` 纳入键值（如 `f"{contract_id}:{scheduled_for}:{attempt}"`），否则在注释中明确"重试必须原地更新同一行"。
-- suggested: 

### [app/models/compute_run.py] medium/bug located=true
四个 JSON 列都用普通 `JSON` 类型，SQLAlchemy 的 unit of work 无法感知原地修改：`run.output_files.append(f)`、`run.resource_usage["cpu"] = 2`、`run.params["x"] = 1` 这类写法不会产生 UPDATE 语句，运行结果会被静默丢弃（对象在 `expire_on_commit=False` 的会话里仍显示新值，极难排查）。该文件的设计正是"执行过程中逐步回填产出文件/资源用量"，属于高风险写法。建议改用可跟踪类型，或强制调用方整体重新赋值并在修改后 `flag_modified(obj, "col")`：

```python
from sqlalchemy.ext.mutable import MutableList, MutableDict

output_files: Mapped[list | None] = mapped_column(MutableList.as_mutable(JSON))
resource_usage: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON))
params: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON))
resource_override: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON))
```

（注意：`MutableDict`/`MutableList` 只跟踪一层，嵌套 dict 的内层修改仍需 `flag_modified`；同时需确认 alembic 迁移链与 `verify_schema_parity.py` 不受类型声明变化影响。）
-- suggested: 

### [app/models/delivery_bundle.py] medium/bug located=true
`size_bytes` 用 `Integer`，在 PostgreSQL 中映射为 int4（上限 2,147,483,647 ≈ 2GiB）。当前唯一的护栏是 `app/api/deliveries.py` 里的 `MAX_BUNDLE_BYTES = 512MB`，它写在另一个模块、且只约束累计未压缩体积（zip 体积、未来其它写入路径、历史数据回填都不受它约束）。一旦上限调整或新增写入方，落库会直接抛 `IntegerType out of range` / `value out of range for type integer`，交付包已上传但归属记录写入失败（打包与归属记录在同一事务，结果是整个请求 500，对象存储里却留下孤儿对象）。字节数这类天然无界字段建议直接用 `BigInteger`（`file_count`/`row_count` 数量级有限，可保持 `Integer`）。注意需要同步补一条 alembic 迁移改列类型。
-- suggested: size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

### [app/models/mpc_key.py] medium/bug located=true
`mpc_keys` 只有 status 索引，缺少任何数值不变量约束：`threshold` 可以为 0/负数，`total_shares` 可以小于 `threshold`，这样的行一旦落库，密钥将永久不可恢复（或阈值语义被削弱）。目前校验只存在于 `MPCService.split_key()`，而 `rotate_key()` 直接复用旧行的 threshold/total_shares 再次落库，任何直接 SQL 写入、数据回填或未来新增的写入路径都会绕过校验。建议加数据库级 CHECK 兜底（注意：本目录约定 status/type 用 Python `str, Enum` + `String`，不加 DB 枚举，因此该建议仅针对数值列；同时需按仓库约定在 `alembic/versions/` 新增一个 revision，不能改 0008）。
-- suggested: __tablename__ = "mpc_keys"
    __table_args__ = (
        Index("ix_mpc_keys_status", "status"),
        CheckConstraint("threshold >= 1", name="ck_mpc_keys_threshold"),
        CheckConstraint(
            "total_shares >= threshold", name="ck_mpc_keys_total_ge_threshold"
        ),
    )

### [app/models/mpc_key.py] medium/bug located=true
`mpc_key_shares` 对 `key_id` 只建了普通索引，没有 `UniqueConstraint("key_id", "share_index")`，也没有约束 `share_index` 落在 `[1, total_shares]`。当前唯一性完全依赖服务层 `share_id = f"{key_id}-s{i}"` 这个命名巧合，一旦同一密钥下出现重复 index（多副本并发 split、迁移脚本、人工补录），Lagrange 插值分母为 0，`_mod_inverse(0, p)` 返回 0，会**静默**重建出错误密钥而非报错；越界 index 同样破坏阈值语义。建议加库级唯一约束（需 `from sqlalchemy import UniqueConstraint`，并新增 alembic revision）。
-- suggested: __tablename__ = "mpc_key_shares"
    __table_args__ = (
        Index("ix_mpc_shares_key", "key_id"),
        UniqueConstraint("key_id", "share_index", name="uq_mpc_key_shares_key_index"),
    )

### [app/models/mpc_key.py] medium/security located=true
`share_value` 仅是一个不透明 `LargeBinary`，模型层完全没有表达密文格式与密钥版本。实际写入格式是 `nonce(12) || tag(16) || ct`，解密靠 `_decrypt_share_value()` 里的固定偏移切片，而 KEK 来自 `AUDIT_ENCRYPTION_KEY or JWT_SECRET_KEY`：主密钥一旦轮换，历史份额将永久不可解密，且库内没有任何字段能区分某行是用哪个版本 KEK / 哪种算法（`algorithm` 描述的是业务密钥算法 sm4/aes256，不是份额封装算法）加密的，无法做渐进式重加密迁移。建议增加 `share_cipher_alg`（如 `SM4-GCM`）与 `share_key_version`（Integer）列，并在文档中明确 nonce/tag 的拼接布局（同样需新增 alembic revision）。
-- suggested: # Encrypted blob layout: nonce(12) || tag(16) || ciphertext.
    share_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    share_cipher_alg: Mapped[str] = mapped_column(
        String(32), nullable=False, default="SM4-GCM"
    )
    share_key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

### [app/models/rag_generative_model.py] medium/bug located=true
`size_bytes` 用 PostgreSQL `INTEGER`（int4，上限 2,147,483,647 ≈ 2GB）存储字节数，属于容量隐患：虽然 `rag_generative_service._MAX_BUNDLE_BYTES` 目前把 bundle 限制在 256MB，但该上限是可调的服务侧常量，一旦调大（生成式 ONNX 权重很容易超过 2GB）或存在其它写入路径（回填脚本、迁移、其他服务直接插入），插入会直接抛 `NumericValueOutOfRange` 或写入错误元数据，而该列同时被审计日志和 API 响应回显给调用方。建议改为 `BigInteger`（int8）以与字节数语义匹配。注意：`alembic/versions/0010_rag_generative.py` 已把该列建为 `sa.Integer()`，改动需同步一条新的迁移列类型变更。
-- suggested: size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

### [app/models/watermark_record.py] medium/bug located=true
`ForeignKey` 被导入但从未使用（本文件所有列均无外键），属于死导入，建议删除。

另外注意：这里刻意不加 FK 的话，`job_id` / `owner_id` / `model_id` 就完全没有任何完整性约束（`owner_id` 是自由字符串，而项目其它表的用户引用都是 `UUID` + FK，如 `TrainingJob.user_id`），任何人都能用任意 `owner_id`/`model_id` 落一条“权属记录”，而 `verify_watermark` 恰恰是用 `SM3(owner_id:model_id)` 重算期望比特位来判定归属的，记录被写错/伪造就直接导致验证结果不可信。建议至少加 FK 或对 owner 做校验。
-- suggested: 

### [app/models/watermark_record.py] medium/bug located=true
`job_id` 上只有普通（非唯一）索引，但业务语义是“每个 job 只能有一条水印记录”：`app/api/training.py:819-823` 用“先 SELECT 判存在、再 INSERT”的 check-then-act 方式返回 409。两个并发请求都能通过该检查，从而插入重复行；此后 `app/api/training.py:886-889` 的 `select(...).scalar_one_or_none()` 在查到多行时会抛 `MultipleResultsFound`，验证接口直接变成 500（且再也无法自愈）。

建议在库层面加唯一约束来消除这个竞态。
-- suggested: __table_args__ = (
        # 每个 job 只允许一条水印记录：唯一约束兜住 API 层 check-then-act 的竞态
        UniqueConstraint("job_id", name="uq_watermark_records_job_id"),
        Index("ix_watermark_records_owner", "owner_id"),
    )

### [app/models/watermark_record.py] medium/bug located=true
`Integer` 在 PostgreSQL 下是 32 位有符号整数（上限 2**31-1）。而写入方 `app/api/training.py` 的 `key_seed: int | None = Field(None, ...)` 没有任何 `ge`/`le` 边界校验：客户端只要传入超过 2**31-1 的 seed（例如 10**12），`INSERT` 就会抛 `DataError: value out of range for type integer`，此时权重已被改写并落盘（`_save_model_checkpoint` 已在 `db.add` 之前执行），但记录插入失败 → 模型被污染且无水印元数据可查（后续验证必然 404），接口返回 500。

建议改用 `BigInteger`（并同步在请求模型上加范围校验）。
-- suggested: key_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)

### [app/models/webhook_delivery.py] medium/bug located=true
**投递状态机存在“僵尸行”风险：`DELIVERING` 不是终态，但没有任何回收/租约字段。**

`deliver_once()` 会先把 `status` 置为 `DELIVERING` 并 `flush()`，随后才发出 HTTP 请求；若进程在此期间崩溃、事件循环被取消或 `db.commit()` 前抛异常，该行会永久停留在 `delivering`。而唯一的调度入口 `dispatch_pending()` 只筛选 `status in (pending, failed)`，也没有 `started_at` / `locked_until` 之类的租约时间戳可用于“超时重投”，因此这些记录既不会被重试，也不会被收敛为 `DEAD`，属于静默丢投递（webhook 语义上是最需要 at-least-once 的路径）。

同时，`DeliveryStatus` 也没有表达“订阅被暂停/已删除所以放弃投递”这类终态：`dispatch_pending()` 对非 ACTIVE 订阅直接 `continue`，记录会一直保持 `pending` 且 `next_retry_at` 早已过期，在 `ORDER BY created_at ASC LIMIT 20` 的轮询下会永久占据窗口头部，把后续新事件全部饿死（head-of-line blocking），且 `attempted` 计数为 0 导致日志也不会提示异常。

建议：为模型补充投递租约/心跳列（如 `locked_until` 或复用 `next_retry_at` + `started_at`），把超时仍处于 `delivering` 的记录纳入调度扫描重投或降级为 `FAILED`/`DEAD`；并为“订阅暂停/已删除”增加一个终态（如 `cancelled`/`expired`），让调度器可以把这类行推进到终态而不是无限跳过。
-- suggested: 

### [app/models/webhook.py] medium/bug located=true
`events` 在模型上声明为 `nullable=False`，但只有 Python 侧 `default=list`，没有 `server_default`；而实际建表迁移 `alembic/versions/0019_flow_orchestration.py:200` 是 `sa.Column("events", sa.JSON(), nullable=True)`。结果是：
1) 数据库层并未真正强制非空，任何绕过 ORM 的写入（SQL 脚本、数据修复、后续新迁移的 bulk insert）都能写出 `events IS NULL` 的行；
2) 这些行会被 `delivery_service.dispatch_event` 里的 `WebhookSubscription.events.is_not(None)` 静默过滤掉，表现为“订阅存在但永远收不到推送”，且没有任何日志或报错，排查成本很高。

建议二者取其一并保持一致：加一条迁移把列改为 NOT NULL（并按需 `server_default=text("'[]'::json")`），或如实把模型声明改为 `Mapped[list | None]`/`nullable=True`，让 NULL 成为显式契约的一部分。
-- suggested: 

### [app/models/webhook.py] medium/security located=true
注释与实际加密实现不符，且 `nullable=True` 会让签名静默失效：
- 注释写的是「SM4-GCM 加密后的签名密钥（utils.crypto.SM4Cipher）」，但创建与轮转实际用的是 `app/api/deliveries.py` 中的 `app.services.task_code_security.encrypt_task_code`（KMS 信封，密文带 `kms:v1:` 前缀），并非 `SM4Cipher`。这会误导后续维护者用错解密路径（`decrypt_task_code` 对无 `kms:v1:` 前缀的值会原样返回“明文”，错误路径不会报错，只会静默产出错误的密钥）。
- 列可空且无 server_default：`create_webhook` 虽然总会写入密钥，但任何未带 `secret_enc` 的行（历史数据、运维脚本插入、迁移遗留）会让 `deliver_once` 走 `secret = ""` 分支，直接发出**不带 `X-CDS-Signature-Sha256` 头**的请求（fail-open），接收方无法区分“合法但未签名”与伪造请求。

建议：把注释改为如实描述 KMS 信封加密；并考虑对签名缺失做显式约束（列改 NOT NULL，或在投递前对无密钥的订阅直接判失败/记录告警，而不是静默不签名）。
-- suggested: 

### [app/models/blockchain_anchor.py] low/bug located=true
`confirmed` 由 `Mapped[bool]` 推导为 NOT NULL，但默认值只配置了 Python 侧的 `default=False`（仅 ORM flush 时生效），没有 `server_default`。任何绕过 ORM 的写入（`bulk_insert`/`execute(text(...))` 原生 SQL、数据迁移或回填脚本、其他服务共用该表）都会因 NOT NULL 约束直接失败，与相邻 `created_at` 使用 `server_default=func.now()` 的做法不一致。建议补上 `server_default=sa.false()`（Python 侧 `default` 可保留）。
-- suggested: confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=expression.false())

### [app/models/blockchain_anchor.py] low/bug located=true
`block_number` 未显式指定类型，SQLAlchemy 会推导为 32 位 `Integer`（PostgreSQL `INTEGER`，上限 2,147,483,647）。该表用于多链锚定（`backend` 为 `ChainBackend` 枚举），出块间隔短的链区块高度可超过该上限，届时写入会触发数值溢出错误。建议显式使用 `BigInteger` 以消除跨链隐患。
-- suggested: block_number: Mapped[int | None] = mapped_column(BigInteger)

### [app/models/delivery_bundle.py] low/maintainability located=true
`t5_review_status` / `watermark_status` / `row_count` 的合法取值只写在行尾注释里，DB 层没有任何约束，模型侧也没有共享枚举常量。目前取值由 `deliveries.py` 的 `_t5_review_status()` / `_watermark_status()` 生成，两处「枚举」靠字符串字面量各写一遍：新增一个状态（例如 `review_failed`）时很容易只改一侧，写进行里后交付中心前端只能按未知值渲染，审计口径也会静默漂移。建议定义 `enum.Enum` 常量供服务层与模型共用，并加 `CheckConstraint`（或 PG Enum/`VARCHAR + CHECK`）与 `size_bytes >= 0`、`file_count >= 0`、`row_count >= 0` 非负约束，让脏值在写入时就被拒绝，而不是等到读侧猜。
-- suggested: 

### [app/models/delivery_bundle.py] low/maintainability located=true
模型列定义与已落地的迁移存在默认值口径漂移：
- `alembic/versions/0021` 给 `size_bytes` / `file_count` 建了 `server_default="0"`，模型侧只有 Python 端 `default=0`，没有 `server_default`；
- `created_at` 迁移里是 `server_default=sa.text("(CURRENT_TIMESTAMP)")`，模型里是 `server_default=func.now()`（渲染为 `now()`）。
这会让 autogenerate 在下次生成迁移时把它们识别成“需要删除/变更 server default”的差异，产生无意义甚至破坏裸 SQL / 其它服务写入路径的迁移（去掉 server_default 后，绕过 ORM 的 INSERT 会因 NOT NULL 无默认值失败）。仓库内同类模型（如 `WebhookDelivery`）采用 `default=0, server_default="0"` 双写口径，建议本表对齐。
-- suggested: size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

### [app/models/delivery_bundle.py] low/security located=true
下载/预览端点把 `object_name` 当作对象存储的唯一定位键（`storage_service.download(owner.object_name)`），但表上既没有 `unique=True` 约束，也没有格式约束。当前写入方是唯一路径（`f"delivery/{bundle_id}.zip"`）所以事实上不会重复；不过一旦允许运维补录/迁移回填 `object_name`，两个 bundle 指向同一对象名就会出现“按 id A 下载到 B 的字节”的越权读取（鉴权按 A 的合约当事方判定，内容却是 B 的成果，B 可能属于另一份合约）。加 `unique=True`（并考虑 `CHECK (object_name LIKE 'delivery/%')`）成本极低，可以把这条隐式契约钉死在 schema 上。
-- suggested: 

### [app/models/mpc_key.py] low/dead code located=true
`import uuid` 在本文件中从未被引用（`key_id`/`share_id` 均由 `app/services/mpc_service.py` 以字符串生成），属于无用导入，建议删除以保持模型层整洁。
-- suggested: from datetime import datetime

### [app/models/mpc_key.py] low/maintainability located=true
与 `kms.py`/`volume_key.py` 等模型不一致：本表 status 没有配套的 `class MPCKeyStatus(str, Enum)`（active/rotated/destroyed），生命周期取值以裸字符串散落在 `mpc_service.py`（`"active"`、`"rotated"`、`"destroyed"`）与 API 层，拼写错误只能靠运行时暴露，也无法在文档/校验中复用。建议在本文件补枚举伴生类并用 `MPCKeyStatus.ACTIVE.value` 作为默认值。另：`destroy_key()` 只改 status，没有 `destroyed_at` 列，密钥销毁时间无法审计（`kms.py` 同时保留 `rotated_at`/`destroyed_at`）。
-- suggested: status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MPCKeyStatus.ACTIVE.value
    )

### [app/models/rag_generative_model.py] low/maintainability located=true
`vocab_size` / `size_bytes` 只有 `default=0`，数据库层没有任何非负（或正数）约束，任何一条越界写入都会静默持久化：`vocab_size <= 0` 会让下游按 argmax 解码 token 的逻辑拿到无意义词表规模（runner 侧据 `vocab_size` 解释 logits 维度），`size_bytes < 0` 会污染审计与容量展示。此外模型声明用的是 Python 侧 `default=0`，而建表迁移用的是 `server_default="0"`，两者不一致会让 `alembic --autogenerate` 持续产生噪音 diff，且不经过 ORM 的写入（SQL 脚本/回填）会因缺少服务端默认值而违反 NOT NULL。建议加 `CheckConstraint` 并与迁移对齐（同一问题也存在于 `size_bytes`）。
-- suggested: __table_args__ = (
        CheckConstraint("vocab_size >= 0", name="ck_rag_generative_models_vocab_size_nonneg"),
        CheckConstraint("size_bytes >= 0", name="ck_rag_generative_models_size_bytes_nonneg"),
    )

    vocab_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

### [app/models/training_job.py] low/dead located=true
`Integer` 在本文件中从未被使用（本表所有列只用到 String / DateTime / Text / JSON / ForeignKey / func / Index / UUID），属于无用导入。建议从导入列表中移除，避免误导后续维护者以为存在整型列（如同目录 `pipeline_task.py` 中的 `duration_ms`）。

建议：`from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Index`
-- suggested: 

### [app/models/training_job.py] low/performance located=true
`unique=True` 在 PostgreSQL/SQLite 上已经会为 `job_id` 创建一个唯一索引，再加 `index=True` 会额外创建一个列完全相同的普通索引：写入时需要维护两份索引、占用额外存储，且优化器可能选中区分度更低的那个。两者只需保留一个。

另外，`job_id` 既无 Python 端 `default` 也无 `server_default`，而列是 `nullable=False`：任何遗漏赋值的插入（新增调用点、迁移脚本回填）都会在 DB 层抛 IntegrityError，而不是得到可预期的业务标识。当前 `app/api/training.py` 靠调用方自己拼 `f"sft-{uuid4().hex[:12]}"`，模型层对格式/前缀与 `job_type` 的一致性也没有任何约束。若确实要求“业务标识必须由调用方提供”，建议在此加注释说明；否则给出默认生成器。
-- suggested: # unique=True 已建唯一索引，不要再加 index=True；job_id 必须由调用方显式赋值（无 default）
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

### [app/models/training_job.py] low/performance located=true
`app/api/training.py::list_training_jobs` 的查询形态是 `WHERE user_id = ? [AND status = ?] ORDER BY created_at DESC OFFSET/LIMIT`：`ix_training_jobs_user_status` 只能覆盖过滤条件，排序仍需对全部匹配行做显式 sort + offset 扫描。同目录的兄弟队列表都为此显式补了时间索引（`ix_pipeline_tasks_created`、`ix_sandbox_sessions_created`、`ix_compute_runs_status_created`），本表缺失。随作业量增长（训练任务会长期累积，且无保留清理）该列表接口会退化为全用户行排序。

建议补充 `Index("ix_training_jobs_created", "created_at")`，或把复合索引扩展为 `(user_id, status, created_at)` 以同时覆盖过滤与排序。
-- suggested: Index("ix_training_jobs_status", "status"),
        Index("ix_training_jobs_user_status", "user_id", "status"),
        Index("ix_training_jobs_created", "created_at"),

### [app/models/training_job.py] low/maintainability located=true
状态与生命周期时间戳之间没有任何一致性约束：`status` 可以被写成 `completed`/`failed`/`cancelled`，而 `started_at`/`completed_at` 仍为 NULL，也可以出现 `completed_at < started_at`。当前只有 `app/api/training.py` 在写入时靠约定手工配对赋值，一旦存在其他写入方（运维脚本、未来的异步 worker、失败分支只写 `completed_at` 等），下游的超时判定、重试点筛选与审计计时（本表是训练合规审计的锚点，`/jobs/{id}/audit` 依赖这些时间）都会静默拿到不一致的数据。此外本表缺少同目录 `compute_run.py` 具备的 `updated_at`（`onupdate=func.now()`），无法追溯状态最后一次变更时间。

建议至少在字段注释中明确不变式（终态必须带 `completed_at`；`running` 之后必须带 `started_at`），并补 `updated_at`；若允许引入 DB 约束，可加 `CheckConstraint("completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at")`（注意需按项目约定新增 alembic revision，并过 `verify_schema_parity.py`）。
-- suggested: 

### [app/models/watermark_record.py] low/security located=true
`key_seed` 是该水印方案（Uchida 2017）的保密参数：它决定了哪些权重位置被用于编码，属于“密钥材料”。这里以明文 `Integer` 落库，且 `WatermarkEmbedResponse` 又把 `key_seed` 和完整的 `layer_positions` 原样回传给调用方——嵌入方（即潜在的模型再分发者）拿到二者后可直接定位并复原被改写的 LSB，从而零成本去除水印，使这张表记录的“权属证明”失效。

建议参照同目录 `volume_key.py` 的信封加密做法（KEK 包裹后存 `LargeBinary`），至少不要在 API 响应中回传 seed 与完整位置，并在字段注释中明确其保密性与访问控制要求。
-- suggested: 

### [app/models/webhook_delivery.py] low/performance located=true
冗余索引：`subscription_id` 上的 `index=True` 会生成单列索引 `ix_webhook_deliveries_subscription_id`，而下方 `__table_args__` 中的复合索引 `ix_webhook_deliveries_sub_status (subscription_id, status)` 的最左前缀已完全覆盖单列查询。两者查询计划等价，单列索引只带来额外的写放大与存储开销（该表是高频写入的投递流水表，每次推送至少一次 INSERT/UPDATE）。

建议去掉 `index=True`（保留复合索引），若确有仅按 `subscription_id` 过滤的高频查询，则改为只保留单列索引、由它承担前缀查询。
-- suggested: subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhook_subscriptions.id"), nullable=True
    )

### [app/models/webhook.py] low/performance located=true
`user_id` 上的单列索引与 `__table_args__` 中的复合索引 `ix_webhook_subs_user_status (user_id, status)` 前缀重复：任何 `user_id = ?` 或 `user_id = ? AND status = ?` 的查询都能走复合索引，单列索引 `ix_webhook_subscriptions_user_id` 不会被选中，只在每次 INSERT/UPDATE 时额外维护一份 B-Tree，属于纯写放大。建议去掉 `index=True`（若已有迁移建了该索引，补一条 drop_index 迁移），保留复合索引即可。
-- suggested: 

### [app/models/workload_test_run.py] low/maintainability located=true
`checks` / `report` 是发布门禁的判定依据（`workflow_service.publish()` 读 `run.passed` 与 `run.report["entrypoint_hash"]`），但用的是普通 `JSON`，SQLAlchemy 不会跟踪就地修改（`run.checks.append(...)`、`run.report["x"]=...`）。这类就地改动不会进入 unit of work，flush/commit 时被静默丢弃，最终落库的 `passed`/`checks` 可能与实际判定不一致——对「不可绕过的门禁凭证」而言是隐性风险。当前 `run_test` 是整体赋值（`run.checks = checks`）所以尚未暴露，但后续任何在服务里追加 check 项的写法都会踩坑。建议用 `MutableList.as_mutable(JSON)` / `MutableDict.as_mutable(JSON)` 声明，从模型层杜绝这类静默丢失。
-- suggested: checks: Mapped[list | None] = mapped_column(MutableList.as_mutable(JSON))
    report: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON))

### [app/models/workload_test_run.py] low/maintainability located=true
这张表的行是「先建行、后就地改写结论」的：`workflow_service.run_test()` 先 `db.add(run)` 建 RUNNING 行，随后再改写 `status/passed/checks/report/exit_code/error_message`。但本表是 SS-15 流程表里唯一没有 `updated_at` 的（`workloads`、`compute_runs`、`sandbox_sessions`、`api_endpoints`、`webhook_deliveries` 都有），因此这条门禁凭证的最后修改时间在库中不可追溯，排障时也无法区分「创建即结束」与「长期卡在 RUNNING」。建议按项目加性迁移约定新增一版 revision（`alembic/versions/`）补 `updated_at: DateTime(timezone=True), server_default=func.now(), onupdate=func.now()`，与 `scripts/verify_schema_parity.py` 保持一致。
-- suggested: created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
