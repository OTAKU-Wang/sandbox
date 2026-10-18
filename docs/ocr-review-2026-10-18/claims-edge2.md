# w3 边缘波次 II（新出条目）

## alembic/env.py (6)
### [E2-1] medium/bug
现码片段：
```
async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()
```
异步 engine 在异常路径上不会被释放：若 `connectable.connect()`（连接失败、鉴权失败、DNS 故障）或 `run_sync(do_run_migrations)`（迁移 SQL 报错）抛出，后面的 `await connectable.dispose()` 永远不会执行，engine 持有的连接/传输对象只能等 GC 回收，常表现为 `Task was destroyed but it is pending` / unclosed transport 告警。由于 `connectable` 是在 try 之前创建的，`async with` 只保护 connection，不保护 engine。建议 try/finally 保证 dispose。
建议：
```
try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()
```

### [E2-2] medium/bug
现码片段：
```
def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())
```
`asyncio.run()` 只能在没有运行中事件循环的线程里调用。一旦 env.py 被程序化入口复用（例如 FastAPI/CLI 的 `async def` 启动钩子里调用 `command.upgrade(cfg, "head")`，或 pytest-asyncio 用例内执行迁移），`asyncio.run` 会直接抛出 `RuntimeError: asyncio.run() cannot be called from a running event loop`，把整个宿主事件循环带崩。即使当前只从同步 `alembic` CLI 进入，也建议加上防护，避免后续在应用启动流程里复用本模块时踩坑。
建议：
```
def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_async_migrations())
        return
    # Already inside an event loop: run migrations in a dedicated thread/loop
    # so asyncio.run() does not raise RuntimeError.
    with ThreadPoolExecutor(1) as pool_exec:
        pool_exec.submit(lambda: asyncio.run(run_async_migrations())).result()
```

### [E2-3] medium/bug
现码片段：
```
context.configure(connection=connection, target_metadata=target_metadata, compare_type=COMPARE_TYPES)
```
未启用 `render_as_batch`，但注释明确说明 CI 会在 SQLite 上跑迁移/schema 比对。SQLite 不支持 `ALTER COLUMN`、`DROP COLUMN` 等 DDL：`op.alter_column(...)` 会抛 `NotImplementedError`（旧版 SQLAlchemy 则是静默不生效），`drop_column` 在旧版上同样失败。这会让 SQLite 环境下的升级结果与 PostgreSQL 不一致（或干脆失败），而 autogenerate 仍按普通 ALTER 生成脚本。建议在 SQLite dialect 下开启 batch 模式。
建议：
```
is_sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=COMPARE_TYPES,
        render_as_batch=is_sqlite,
    )
```

### [E2-4] low/bug
现码片段：
```
COMPARE_TYPES = os.environ.get("ALEMBIC_COMPARE_TYPES", "1") == "1"
```
开关只接受字面量 "1"，任何其它写法（"true"/"True"/"yes"/"0"/空串）都会落到“启用类型比对”的分支。后果有两个方向：① CI 若按直觉写成 `ALEMBIC_COMPARE_TYPES=false`，SQLite 上仍会做类型比对，`sa.UUID` 被存成 NUMERIC 亲和性，会涌出大量误报，直接破坏 CI；② 反过来在 PostgreSQL 环境误设为 0 时，列类型漂移会被静默跳过且无任何提示。建议按布尔语义解析并在关闭时打一条 warning。
建议：
```
COMPARE_TYPES = os.environ.get("ALEMBIC_COMPARE_TYPES", "1").strip().lower() not in {"0", "false", "no", "off", ""}
if not COMPARE_TYPES:
    logging.getLogger(__name__).warning(
        "Alembic type comparison disabled by ALEMBIC_COMPARE_TYPES; "
        "column type drift will NOT be detected."
    )
```

### [E2-5] low/bug
现码片段：
```
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
```
Alembic 的 `Config` 底层是 `configparser.ConfigParser`，默认使用 BasicInterpolation；`set_main_option` 写入的值在 `get_main_option` / `get_section`（在线模式 `async_engine_from_config` 走的就是它）读取时会被重新插值。因此当 `DATABASE_URL` 的密码含 `%`（强随机密码很常见）时会抛 `configparser.InterpolationSyntaxError: invalid interpolation syntax`，且报错栈完全指向 alembic 配置层，极难定位。建议对 `%` 做转义（`%%`），或直接把 URL 显式传给 engine 而不经 ini section。
建议：
```
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))
```

### [E2-6] low/maintainability
现码片段：
```
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
```
`fileConfig` 的 `disable_existing_loggers` 默认为 True，而此处调用发生在 `import app.main` 之后：app.main 及其全部 router/service/model 模块在导入期创建的 `logging.getLogger(__name__)` 对象都不在 alembic.ini 的 `loggers=` 列表里，会被整体置为 level=0（禁用）。迁移过程中来自应用模块的告警（自定义类型比对、方言兼容提示等）会被静默丢弃；若 env.py 被应用进程内复用，还会连带关掉整个应用的日志。建议显式传 `disable_existing_loggers=False`。
建议：
```
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
```

## alembic/versions/0000_baseline_all_tables.py (6)
### [E2-7] high/bug
现码片段：
```
sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
```
**时区不一致（安全/正确性风险）**：`app_credentials` 的四个时间列使用无时区的 `sa.DateTime()`（PG 上为 `timestamp without time zone`），而基线中其它所有时间列都是 `DateTime(timezone=True)`。这与应用层代码直接冲突：`app/services/gateway_service.py` 中 `authenticate()` 执行 `cred.expires_at < datetime.now(timezone.utc)` 判断凭证过期，并写入 `cred.last_used_at = datetime.now(timezone.utc)`。由于列是无时区的，从 PG/SQLite 读回的 `expires_at` 是 naive datetime，与 aware 的 `datetime.now(timezone.utc)` 比较会抛 `TypeError: can't compare offset-naive and offset-aware datetimes`（网关鉴权 500）；反之若去掉 tzinfo，则会按本地时间解释 UTC 值，导致过期/撤销判定偏差整个偏移量。建议：与同表其它时间列保持一致使用 `timezone=True`，并同步修改 `app/models/app_credential.py`，另起一个 ALTER 迁移修正已建库（注意 SQLite 无法 ALTER 列类型，需要说明处理策略）。
建议：
```
sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
```

### [E2-8] medium/performance
现码片段：
```
op.create_index('ix_certificates_serial', 'certificates', ['serial_number'], unique=True)
```
**重复索引**：本表上方已声明 `sa.UniqueConstraint('serial_number')`（PG 会自动创建唯一索引），这里又额外建了一个同列唯一索引 `ix_certificates_serial`，`serial_number` 上存在两个功能完全相同的唯一索引，纯增加写入开销与存储。应二选一（保留 UniqueConstraint 并删除该 `create_index`）。注意该重复来自 ORM 侧 `unique=True` + `index=True`/显式 `Index` 同时存在，需一并修正模型，否则 `alembic check`/autogenerate 会再次生成。

### [E2-9] medium/performance
现码片段：
```
op.create_index('ix_connector_sessions_connector', 'connector_sessions', ['connector_id'], unique=False)
    op.create_index(op.f('ix_connector_sessions_connector_id'), 'connector_sessions', ['connector_id'], unique=False)
```
**重复索引**：`connector_sessions.connector_id` 上创建了两个完全相同的非唯一索引——`ix_connector_sessions_connector`（来自 `__table_args__` 里的显式 `Index`）与 `ix_connector_sessions_connector_id`（来自列上的 `index=True`，见 `app/models/connector.py`）。同一个 FK 列建两份索引只会带来额外写入/存储成本。建议删除其中一个，并同步清理模型定义。
建议：
```
op.create_index('ix_connector_sessions_connector', 'connector_sessions', ['connector_id'], unique=False)
```

### [E2-10] medium/performance
现码片段：
```
op.create_index('ix_merkle_leaves_batch', 'merkle_leaves', ['batch_id'], unique=False)
    op.create_index(op.f('ix_merkle_leaves_batch_id'), 'merkle_leaves', ['batch_id'], unique=False)
```
**重复索引**：`merkle_leaves.batch_id` 同样存在两个同列同类型的非唯一索引（`ix_merkle_leaves_batch` 来自 `__table_args__`，`ix_merkle_leaves_batch_id` 来自列上的 `index=True`，见 `app/models/merkle_leaf.py`）。审计叶子属于高频写入表，冗余索引会放大写入成本。建议只保留一个，并同步修正模型以免 autogenerate 重新引入。
建议：
```
op.create_index('ix_merkle_leaves_batch', 'merkle_leaves', ['batch_id'], unique=False)
```

### [E2-11] low/performance
现码片段：
```
op.create_index('ix_field_visibility_product', 'field_visibility_configs', ['product_id'], unique=True)
```
**重复索引**：`field_visibility_configs` 已有 `sa.UniqueConstraint('product_id')`（自带唯一索引），此处再建唯一索引 `ix_field_visibility_product`，同一列出现两个唯一索引，属未清理的 autogenerate 冗余项。保留一个即可（该列同时是 FK，唯一索引已可服务 JOIN 查找）。

### [E2-12] low/documentation
现码片段：
```
`alembic upgrade head` builds every table, then 0001/0002 re-add their
additive columns.
```
**文档描述已过时**：docstring 只说明「0001/0002 会补回新增列」，但迁移链现已推进到 0026（0003/0005/0016 等向 `sandbox_sessions`、`sandbox_nodes` 追加了 `idle_policy`/`auto_resume`/`scheduling_disabled`/`health_state`/`tee_policy`/`trust_root` 等列，0004 起还新建了多张表）。作为基线迁移的运维说明，这会让读者误以为 `upgrade head` 之后表结构就等于当前模型。建议改为「后续 0001…00NN 逐步追加列/表」的表述，避免基线与模型对齐性判断出错。

## alembic/versions/0001_p0_security_hardening.py (4)
### [E2-13] medium/documentation
现码片段：
```
remediation — it is safe to run against a database already created by
create_all, and against an empty database it is harmless.
```
文档中的安全性声明与实际行为不符，会误导运维：

1) 对 create_all 建出来的库直接 `alembic upgrade head` 并不安全——`op.add_column` 没有存在性检查，而 ORM 模型（app/models/contract.py、policy_bundle.py、kms.py、sandbox_task.py、data_resource.py）已定义这些同名字段，create_all 建出的表已含这些列，升级会抛 `DuplicateColumn`/`ProgrammingError` 并使事务回滚。此外未 stamp 的 create_all 库还会先在 0000 的 `create_table('alert_records')` 处失败。
2) 同理，部分失败后重跑（upgrade 不具备幂等性）也会直接报错。

建议：把措辞改为「仅对空库或已 `alembic stamp` 的库执行 upgrade；create_all 库必须先 stamp」，并在 upgrade() 内用 `op.get_bind()` + `sa.inspect(...).get_columns(...)` 做存在性保护，或使用 `ADD COLUMN IF NOT EXISTS` 的原生 SQL，避免重复执行/半程重跑硬失败。

### [E2-14] medium/security
现码片段：
```
op.add_column("contracts", sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True))
    # A4 (P1): purpose limitation fields
    op.add_column("contracts", sa.Column("purpose", sa.String(length=64), nullable=True))
```
这些安全控制字段全部为可空、无 `server_default`、且迁移内无任何回填，导致存量数据上的控制默认「失效/放开」：

- `contracts.valid_until` IS NULL → A1 到期清理扫描（status=active AND valid_until <= now()）对迁移前已激活的存量合约完全命中不到，到期自动终止不会生效（app/models/contract.py 注释说明该值仅在激活时从 `terms["valid_until"]` 派生，存量行不会重新走激活流程）。
- `contracts.purpose` / `purpose_scope` IS NULL → 按模型注释语义为「用途不限」，即存量合约的用途限定（A4）默认不生效。
- `data_resources.field_classifications` IS NULL → 无字段分级映射，level 3 强制脱敏 / level 4 禁止出口对存量资源静默不生效。
- `policy_bundles.revoked_at` IS NULL 作为「未吊销」哨兵是合理的，但它同时是吊销合规证据，需与下面的回滚风险一起考虑。

建议：在同一迁移链上补一个显式回填步骤（至少对 `valid_until` 从 `terms->>'valid_until'` 回填），或把「存量行控制不生效」作为已接受风险写入文档并由后续策略迁移补齐；否则本迁移声称修复的 A1/A4/A5 缺口对生产存量数据实为空操作。

### [E2-15] medium/maintainability
现码片段：
```
def downgrade() -> None:
    op.drop_column("policy_bundles", "revoked_at")
    op.drop_column("data_encryption_keys", "sm2_encrypted_payload")
    op.drop_column("data_encryption_keys", "wrapped_payload")
```
`downgrade()` 会无条件 `DROP COLUMN`，属于不可逆的数据销毁，且涉及合规/密钥可用性证据：

- `wrapped_payload` / `sm2_encrypted_payload` 删除后，已落盘的 KEK 封装块丢失（Gap B3 的重启恢复能力），存量 DEK 将无法恢复，等价于批量密钥丢失；
- `revoked_at` 删除即抹掉策略包吊销时间与合规追溯依据；
- `field_classifications` / `purpose` 删除即丢失分级与用途限定声明。

虽然回滚顺序（与 upgrade 严格逆序）本身正确，但生产变更流程通常不允许这类降级静默丢数据。建议二选一：(a) 在 downgrade() 开头显式 `raise RuntimeError("downgrade intentionally unsupported: drops key/revocation evidence")`（保留 head 迁移不可回滚的惯例）；(b) 保留 drop 但在 docstring 中显式声明数据损失范围与回滚前置备份要求，供变更审批引用。

### [E2-16] low/documentation
现码片段：
```
- sandbox_sessions.contract_index — not needed; contract_id already indexed
```
该结论与事实不符：0000 基线为 `sandbox_sessions` 只创建了 `ix_sandbox_sessions_created`、`ix_sandbox_sessions_data_product_id`、`ix_sandbox_sessions_product_status`、`ix_sandbox_sessions_user_id`、`ix_sandbox_sessions_user_status`，并没有 `contract_id` 上的索引（模型 app/models/sandbox_session.py 中 `contract_id` 也未声明 index=True）。若合约终止/到期联动按 `contract_id` 反查会话，这条注释会让后续维护者误以为无需关注该路径，实际会走顺序扫描。建议修正注释，或确认相关查询路径后补 `op.create_index`（注意 `contract_id` 为 String(255)，索引前建议确认长度与前缀必要性）。

## alembic/versions/0002_session_usability.py (1)
### [E2-17] low/documentation
现码片段：
```
Additive-only, safe against a create_all database:
```
注释声称 “Additive-only, safe against a create_all database”，但对本迁移而言并不成立：`extended_seconds` / `pre_pause_status` 已存在于 `app/models/sandbox_session.py`，由 `Base.metadata.create_all` 建出的库里这两列已经存在，再执行 `alembic upgrade head` 会在 `op.add_column` 处抛出 `duplicate column name: extended_seconds`（PG 下是 `column "extended_seconds" of relation "sandbox_sessions" already exists`）。这与 0000/0001 中的约定一致：create_all 引导的库应先 `alembic stamp head`，而不是直接 upgrade。建议在此明确该前提，避免运维按注释直接在生产/开发库执行升级而失败。（非阻塞，仅文档表述问题）

## alembic/versions/0003_idle_policy.py (2)
### [E2-18] medium/maintainability
现码片段：
```
op.add_column("sandbox_sessions", sa.Column("idle_policy", sa.String(length=16), nullable=True))
```
数据完整性缺口：`idle_policy` 在文档中被约束为 `kill` | `pause`，但 schema 层只声明为 `String(16)`，既无 `CHECK` 约束也无 `sa.Enum`。目前唯一校验点在 `app/schemas/sandbox_session.py:SandboxSessionCreate.valid_idle_policy`，即仅覆盖「创建会话」这一条路径；任何绕过该 schema 的写入（后续的 PATCH/更新接口、运维回填脚本、直接 ORM 写入、数据导入）都能落库任意字符串。由于回收（idle reaper）逻辑通常是 `== "pause"` 判断，非法值会静默退化成默认的 kill 语义，属于难以排查的行为漂移。建议在迁移中补一个 CHECK 约束（或 `sa.Enum`），至少与本文件另一列的显式契约保持一致；若项目约定所有枚举列都只做应用层校验，也请在文档中明确「本列无 DB 级约束」。

另注意：其他枚举字段在 `app/schemas/sandbox_session.py` 都有可复用常量（`VALID_SANDBOX_LEVELS` / `VALID_TEE_POLICIES`），而 idle_policy 的合法值只以内联元组形式存在于单个 validator 中，缺少 `VALID_IDLE_POLICIES`，后续新增写入路径时很容易漏校验。

### [E2-19] low/maintainability
现码片段：
```
sa.Column("auto_resume", sa.Boolean(), nullable=False, server_default=sa.text("false")),
```
本行的布尔字面量是历史缺陷的根源：手写 `sa.text("0")` 在 PG 上会被拒绝（boolean 列不接受 integer 默认值），改成 `sa.text("false")` 后在 SQLite 上又依赖 3.23+ 才支持的 `FALSE` 关键字。更稳妥且方言无关的写法是 `server_default=sa.false()`，由 SQLAlchemy 按方言渲染（PG → `false`，SQLite → `0`），既不需要注释解释历史，也能避免同类迁移再次踩坑。

另需留意：本文件是对已发布 revision 的原地改写（而非新增前向修复迁移）。已用旧字面量应用过 0003 的 SQLite 环境里列默认值仍是 `DEFAULT 0`，与新执行 `alembic upgrade head` 得到的 `DEFAULT false` 不一致；两者在 SQLite 语义上等价，因此不影响运行，但会让 `alembic check` / autogenerate 的模型-库比对出现字面量漂移。建议在本 NOTE 中补一句「已应用环境不会被重跑，字面量差异为已知且可接受」，以免后续排查者误判为待修复问题。

## alembic/versions/0004_session_operations.py (6)
### [E2-20] medium/bug
现码片段：
```
sa.Column("session_id", sa.UUID(), nullable=False),
```
`session_id` 没有声明指向 `sandbox_sessions.id` 的外键（也没有级联删除）。`sandbox_sessions.id` 本身就是 UUID（见 0000_baseline_all_tables.py 第 474-475 行），因此这里完全可以建 FK。当前实现的后果：会话被删除后 `session_operations` 中的操作记录会永久残留成孤儿行（`app/services/session_operations.py` 中也没有任何清理逻辑），轮询接口 `get_operation` 会返回指向已不存在会话的操作。注意 0018 记录的“SQLite 不能 ALTER-ADD FK”限制只影响后补 FK 的迁移；本迁移是 CREATE TABLE，FK 在 SQLite/PostgreSQL 下都能随建表一起创建，不会破坏 CI 的 SQLite 链路，但需同步在 `app/models/session_operation.py` 加同样的 FK 以免 `alembic check` 报漂移。
建议：
```
sa.Column("session_id", sa.UUID(), sa.ForeignKey("sandbox_sessions.id", ondelete="CASCADE"), nullable=False),
```

### [E2-21] medium/bug
现码片段：
```
op.create_index("ix_session_operations_session_created", "session_operations", ["session_id", "created_at"], unique=False)
```
建表时没有任何约束用来保证“同一 session 同一时间只有一个 rollback/pending 操作”，但 `app/services/session_operations.py` 的 `_has_running_exclusive()` 是典型的 check-then-act：先 SELECT 再 INSERT，两步之间没有加锁，也不是原子操作。多副本部署（该文件注释里明确提到多 replica 的规划）下两个并发请求可以同时通过检查，插入两条 `status='pending'` 的 rollback 记录，快照/回滚并发执行会破坏工作区状态一致性。建议在建表阶段就加数据库级兜底的部分唯一索引，而不是只依赖应用层检查。
建议：
```
op.create_index("ix_session_operations_session_created", "session_operations", ["session_id", "created_at"], unique=False)
    # 数据库级互斥兜底：同一 session 同一 op_type 只允许一条未终态记录
    # PostgreSQL 下生效；SQLite 需 op.get_bind().dialect.name 判断后跳过（否则降级到应用层检查即可）
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "uq_session_operations_active",
            "session_operations",
            ["session_id", "op_type"],
            unique=True,
            postgresql_where=sa.text("status IN ('pending', 'running')"),
        )
```

### [E2-22] medium/maintainability
现码片段：
```
sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
```
`updated_at` 只有 DEFAULT，没有任何数据库级自动刷新机制（无 trigger，也没有 `ON UPDATE`），因此 UPDATE 时它保持旧值。目前自动刷新只来自 ORM 侧 `SessionOperation.updated_at` 的 `onupdate=func.now()`（app/models/session_operation.py 第 32-34 行），也就是说：任何绕过 ORM 的写入（运维手工 SQL、后续新增的非 ORM 写入路径、以及 `execute(update(...))` 这类不经过 ORM onupdate 的批量更新，SQLAlchemy Core 的 update 不会触发 `onupdate` 的 Python 默认值）都会让 `updated_at` 停在创建时间。而 `operation_payload()` 又把 `updated_at` 暴露给轮询客户端，作为“操作最近推进时间”判断依据，时间失真会直接导致客户端误判操作卡死/超时。建议至少在文档字符串中写明“updated_at 由应用层维护”，或为该表增加 UPDATE 触发器保证与链路的表一致语义。

### [E2-23] low/bug
现码片段：
```
sa.Column("progress", sa.Float(), nullable=False),
```
`progress`/`status`/`op_type` 全部没有任何数据库级取值约束。`progress` 语义上是 0.0~1.0 的比例（服务层写入 0.0/0.1/1.0，并通过 `operation_payload` 原样返回给客户端做进度展示），但库层可以写入 -5 或 300 而不会被拒绝；`status` 注释约定为 pending|running|succeeded|failed，写错拼写同样静默入库，之后依赖 `status IN ('pending','running')` 的互斥查询和终态判断会静默漏掉这些行。建议加 CHECK 约束，在写入侧第一时间暴露脏数据而不是在读侧被静默忽略。

### [E2-24] low/performance
现码片段：
```
op.create_index("ix_session_operations_session_id", "session_operations", ["session_id"], unique=False)
```
该单列索引与紧随其后的复合索引 `ix_session_operations_session_created (session_id, created_at)` 前缀完全重复：任何只用 `session_id` 过滤的查询（当前服务层查询都带 session_id 条件）走复合索引即可，单列索引只带来额外的写放大和存储开销。若想删除它，需同步去掉 `app/models/session_operation.py` 中 `session_id` 的 `index=True`，否则 `alembic check` 会报模型与链路漂移。

### [E2-25] low/bug
现码片段：
```
op.drop_index("ix_session_operations_session_created", table_name="session_operations")
    op.drop_index("ix_session_operations_session_id", table_name="session_operations")
```
`downgrade` 中显式 `drop_index` 在 PostgreSQL 上是多余的（`DROP TABLE` 会级联删除其索引），并且在 PG 中索引名是 schema 内全局的、不带表名限定，一旦 `session_operations` 因手工操作缺失或索引已被提前删除，`DROP INDEX ix_session_operations_session_created` 会直接报错中断回滚，导致 `drop_table` 不执行（PG 迁移默认在单事务中，会整体回滚）。建议用 `if_not_exists=True`（SQLAlchemy 2.0 / Alembic 1.9+ 支持）或直接只保留 `drop_table`，使 downgrade 可重复执行。
建议：
```
op.drop_table("session_operations")
```

## alembic/versions/0005_node_operations.py (2)
### [E2-26] low/documentation
现码片段：
```
- sandbox_nodes.health_state         (unknown | healthy | stale | isolated)
```
文档中声明的 health_state 取值域不完整：实际运行代码 app/api/sandbox_nodes.py 的 recover_stale_nodes() 会写入 "offline"（`node.health_state = "offline"`），而这里的枚举只列出 unknown | healthy | stale | isolated。由于本迁移没有为该列添加 CHECK 约束，当前不会报错，但后续若有人依据此文档补上 CHECK 约束（或据此实现校验逻辑），会使 stale 节点自动下线的路径在写入时直接失败。建议把取值域补全为 unknown | healthy | stale | isolated | offline，或明确说明该列为开放取值、不做约束。

### [E2-27] low/maintainability
现码片段：
```
def downgrade() -> None:
    op.drop_column("sandbox_nodes", "health_state")
    op.drop_column("sandbox_nodes", "scheduling_disabled")
```
downgrade 是无条件破坏性的：drop 这两列会永久丢弃已记录的隔离（cordon）状态与健康观测历史；同时 app/models/sandbox_node.py 仍然声明了这两个列，_node_payload()、isolate/unisolate、detect_stale_nodes() 等均在读写它们。也就是说回滚到 0004 之后、未同步回退应用代码的情况下，节点列表与调度相关接口都会因缺列而报错。建议：确认 0005 之后没有任何迁移/索引/视图/授权依赖这两列，并在 docstring 中注明「回滚必须与应用版本同步回退，且会丢失隔离与健康状态数据」，避免运维在故障处置时单独执行 `alembic downgrade`。

## alembic/versions/0006_task_queue.py (5)
### [E2-28] medium/performance
现码片段：
```
op.create_index("ix_task_queue_status", "task_queue", ["status"])
    op.create_index("ix_task_queue_status_type_created", "task_queue", ["status", "task_type", "created_at"])
```
`ix_task_queue_status` 与 `ix_task_queue_status_type_created` 的首列完全重叠（status 是复合索引前导列），凡能用前者过滤的查询都能用后者，因此它是冗余索引，只会给这张高频 UPDATE 的队列表（claim/complete/fail 每次都要写）增加额外写放大。同时当前索引集合并不能匹配真正的热点领取查询：`claim_next()` 的谓词是 `status = 'pending' AND (run_after IS NULL OR run_after <= now) ORDER BY created_at LIMIT n`，而 `run_after` 未进入任何索引，且当未传 `task_types` 时复合索引中间夹着 `task_type` 也无法用于免排序扫描；加上本迁移没有为 done/failed 历史行做任何归档/清理，表会持续增长，领取查询最终会退化成对 pending 子集的大量回表过滤+排序。建议删除冗余单列索引，并补一个面向领取路径的部分索引，例如 `CREATE INDEX ... ON task_queue (run_after NULLS FIRST, created_at) WHERE status = 'pending'`（Alembic 可用 `op.create_index(..., postgresql_where=sa.text("status = 'pending'"))`）。
建议：
```
op.create_index(
        "ix_task_queue_pending_claimable",
        "task_queue",
        ["run_after", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_task_queue_status_type_created", "task_queue", ["status", "task_type", "created_at"])
```

### [E2-29] medium/bug
现码片段：
```
sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
```
`status` 只做长度限制（String(16)）而没有 CHECK 约束，等于允许任意字符串入队；而调度侧完全依赖字面量比较（`status == "pending"`、`status == "claimed"`）。更实质的风险是 status 与 `claimed_by/claimed_at` 之间没有任何一致性约束：一旦出现 `status='claimed'` 但 `claimed_at IS NULL` 的行，`requeue_stale()` 的可见性回收条件 `claimed_at.is_not(None)` 会永远匹配不到它，该任务将永久卡在 claimed 状态既不被领取也不被回收；反之 `status='pending'` 且残留旧 `claimed_by` 也会让排障/审计判断失真（`complete()` 置 done 时同样保留过期领取信息）。建议在本迁移中加入 `sa.CheckConstraint`，至少覆盖 status 取值域与 claimed 必须带 claimed_at 的不变式（如 `status IN ('pending','claimed','done','failed')` 与 `status <> 'claimed' OR claimed_at IS NOT NULL`），并让 ORM 模型保持同步，避免 autogenerate 产生 schema drift。
建议：
```
sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'done', 'failed')",
            name="ck_task_queue_status",
        ),
        sa.CheckConstraint(
            "status <> 'claimed' OR claimed_at IS NOT NULL",
            name="ck_task_queue_claim_consistency",
        ),
```

### [E2-30] low/bug
现码片段：
```
sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
```
`attempts` / `max_attempts` 只有非空与默认值，没有范围约束：任何写入方（回填脚本、SQL 运维操作、未来新调用点）都可以写入负数或 `max_attempts = 0`。重试判定是 `if item.attempts >= item.max_attempts`，`max_attempts <= 0` 会让任务首次失败即进 failed，而 `attempts < 0` 或 `attempts > max_attempts` 的脏数据会让重试次数与死信判定与实际语义脱节，队列调度上很难事后定位。建议补 `sa.CheckConstraint("attempts >= 0 AND max_attempts > 0")`（必要时再加 `attempts <= max_attempts`）。
建议：
```
sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0",
            name="ck_task_queue_attempts_range",
        ),
```

### [E2-31] low/maintainability
现码片段：
```
sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
```
`updated_at` 仅声明了 DEFAULT CURRENT_TIMESTAMP，没有任何数据库层自动更新（触发器）或规则，因此它只在 INSERT 时被赋值，之后的 UPDATE 全靠 ORM 侧 `onupdate=func.now()`（`app/models/task_queue.py`）维护。一旦存在绕过 ORM 的写入（数据修复脚本、直接 SQL、其他服务共用该表），`updated_at` 会长期停留在创建时间，而队列的卡死检测/运维看板往往依赖该字段判断任务是否停滞，会产生误判。建议二选一：加 `BEFORE UPDATE` 触发器统一维护，或在本迁移 docstring 中显式说明「updated_at 由应用层维护，不可作为 SQL 侧新鲜度依据」。

### [E2-32] low/other
现码片段：
```
op.drop_table("task_queue")
```
`downgrade()` 直接 `drop_table`，会连同所有 pending/claimed 任务与 `last_error` 排障信息一起永久删除，且队列写入方分散在多个服务中，回滚后这些行无法重建。建议至少在 docstring 中标注「downgrade 会丢弃全部队列数据，生产环境禁止执行」，或改为先重命名/归档再删除（与 0005 一样在 docstring 里留下明确的运维约束说明），避免运维按常规「先 downgrade 再 upgrade」流程误操作。

## alembic/versions/0007_shared_volumes.py (4)
### [E2-33] medium/performance
现码片段：
```
op.create_index("ix_volume_attachments_session", "shared_volume_attachments", ["session_id"])
```
`shared_volume_attachments.volume_id` 带 `ondelete="CASCADE"` 外键，但本表只有 `ix_volume_attachments_session`（session_id 单列）和 `uq_volume_attachment(session_id, volume_id)` 两个索引，唯一约束的前导列是 session_id，无法用于按 volume_id 定位子表行。

后果：
1) PostgreSQL 在删除父表行时，会对 CASCADE 子表按 volume_id 做全表顺序扫描，并在扫描期间对 `shared_volume_attachments` 持有 `ShareLock`，阻塞该表上的并发 INSERT（attach/detach 写入路径），卷多、附件表大时级联删除会明显变慢甚至引发锁等待。
2) `app/services/shared_volumes.py:delete_volume()` 里 `select(...).where(SharedVolumeAttachment.volume_id == volume.id)` 同样只能顺序扫描。

对比同仓库 0008_mpc_persistence.py 中为 CASCADE 外键显式补了 `ix_mpc_shares_key`，这里缺失属于遗漏。注意 `app/models/shared_volume.py` 的 ORM 也没有该索引，补索引时需同步 ORM，否则 `alembic check` 会报漂移。
建议：
```
op.create_index("ix_volume_attachments_session", "shared_volume_attachments", ["session_id"])
    # CASCADE FK needs its own index: uq_volume_attachment is led by session_id,
    # so a volume delete would seq-scan + ShareLock this table on PostgreSQL.
    op.create_index("ix_volume_attachments_volume", "shared_volume_attachments", ["volume_id"])
```

### [E2-34] medium/bug
现码片段：
```
sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
```
`shared_volumes.owner_id` 没有指向 `users.id` 的外键，而 0000 基线中同类归属列都建了 FK（如 `data_products.provider_id`、`certificates.user_id`、`contracts.provider_id` 均 `ForeignKeyConstraint([...], ['users.id'])`）。这里缺失后，用户被删除时会留下无主卷，卷对应的磁盘目录（`volume_dir()`）与后续可能挂载的记录都无人回收，属于引用完整性缺口。

注意 0008 之后的链路无法再 `ALTER ADD` 外键（SQLite 不支持，见 0018_owner_fks 的说明），因此本迁移是唯一能低成本建立 FK 的位置——`create_table` 阶段在 SQLite 上写 FK 是可行的，建议在 `create_table` 内补 `sa.ForeignKeyConstraint(["owner_id"], ["users.id"])`，并同步 ORM。若确认有意由应用层维护，请在 docstring 中明确记录，避免后续误判。

### [E2-35] medium/bug
现码片段：
```
sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
```
`shared_volume_attachments.session_id` 无外键约束。基线中同样引用 `sandbox_sessions.id` 的表都建了 FK（`audit_logs.session_id`、`pipeline_tasks.session_id`、`sandbox_tasks.session_id`、`connector_sessions.sandbox_session_id`）。缺 FK 后，会话被删除（或按会话清理数据）时附件记录会成为永久孤儿：`resolve_binds()/resolve_mounts()` 虽按 session_id 过滤不会误挂载，但 `uq_volume_attachment` 会长期占据该 (session_id, volume_id) 组合，且删除卷时仍要扫这些无效行。

由于后续迁移无法再 ALTER ADD 外键（SQLite 限制，见 0018_owner_fks），建议在 `create_table` 内直接补 `sa.ForeignKeyConstraint(["session_id"], ["sandbox_sessions.id"], ondelete="CASCADE")`，与 volume_id 的 CASCADE 语义保持一致，并同步 ORM。

### [E2-36] low/maintainability
现码片段：
```
op.create_index("ix_shared_volumes_owner_id", "shared_volumes", ["owner_id"])
```
`ix_shared_volumes_owner_id` 与上面的 `uq_shared_volume_owner_name(owner_id, name)` 前导列相同，任何 `WHERE owner_id = ?` 查询都能走唯一约束索引，因此该单列索引在 PostgreSQL/SQLite 上都是冗余的，只带来额外的写入与 VACUUM/维护开销。

不过需先确认这是否是为与 ORM 保持 `index=True` 而刻意保留（0018_owner_fks 记录了本仓库要求链路与 ORM 一致，`app/models/shared_volume.py:24` 上 `owner_id` 带 `index=True`）。若要删除该索引，应同时去掉 ORM 上的 `index=True`，否则 `alembic check` 会报漂移。属非阻塞建议。

## alembic/versions/0008_mpc_persistence.py (3)
### [E2-37] high/bug
现码片段：
```
op.create_index("ix_mpc_shares_key", "mpc_key_shares", ["key_id"])
```
`mpc_key_shares` 只以 `share_id` 为主键，`key_id` 上建的是**普通索引**，缺少 `(key_id, share_index)` 唯一约束。一旦同一 `key_id` 下出现重复的 `share_index`（例如运维脚本/数据回填/未来新增写入路径绕过了 `share_id = f"{key_id}-s{i}"` 这一隐式约定），`mpc_service.reconstruct_key` 会把重复 x 坐标喂给 `_lagrange_interpolate`：此时 `denominator = ∏(xi - xj)` 变为 0，`_mod_inverse(0, p)` 即 `pow(0, p-2, p)` 返回 0（不报错），最终**静默返回错误的密钥**而非抛异常。这是 Shamir 恢复语义的核心完整性约束，应在 schema 层强制。建议补充唯一约束（注意需同步到 `app/models/mpc_key.py` 的 `__table_args__`，否则 `alembic revision --autogenerate` 会把它当作 drift 并在后续迁移中删除）。
建议：
```
sa.ForeignKeyConstraint(["key_id"], ["mpc_keys.key_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("key_id", "share_index", name="uq_mpc_share_key_index"),
    )
    op.create_index("ix_mpc_shares_key", "mpc_key_shares", ["key_id"])
```

### [E2-38] medium/bug
现码片段：
```
sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("total_shares", sa.Integer(), nullable=False),
```
`threshold` / `total_shares` 没有任何数据库层 CHECK 约束，因此可以持久化 `threshold < 1` 或 `threshold > total_shares` 这类**永远无法重建密钥**的非法行。目前校验只存在于 `MPCService.split_key` 与 Pydantic 的 `SplitKeyRequest`（ge=2/le=20、le=50），任何绕过服务层的写入（SQL 导入、数据修复、后续新增写入路径）都会写出不可用的密钥记录，且状态仍是 `active`，直到调用方 reconstruct 时才暴露。密钥生命周期数据建议由 schema 兜底。同理 `status` 也未限制取值集合（服务层写入 active/rotated/destroyed），拼写错误的状态会让 `reconstruct_key` 直接拒绝服务且难以排查。（新增约束需同步到 ORM `__table_args__`，避免 autogenerate drift。）
建议：
```
sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("total_shares", sa.Integer(), nullable=False),
        sa.CheckConstraint("threshold >= 1", name="ck_mpc_keys_threshold_positive"),
        sa.CheckConstraint("total_shares >= threshold", name="ck_mpc_keys_threshold_le_total"),
```

### [E2-39] low/performance
现码片段：
```
op.create_index("ix_mpc_keys_status", "mpc_keys", ["status"])
```
`status` 仅有 3 个离散取值（active/rotated/destroyed），选择性极低；而当前服务层所有查询（`_load_key`/`list_keys`/`_load_shares`）都只按 `key_id` 过滤，没有任何查询以 `status` 作为过滤条件。因此该索引目前不会被使用，只带来每次状态流转（rotate/destroy）时的额外写放大。若确无按状态扫描的查询需求，建议去掉；若计划支持“列出全部 active 密钥”之类的查询，则建议改为 `(status, created_at)` 复合索引以同时覆盖排序。

## alembic/versions/0009_trained_models.py (5)
### [E2-40] high/bug
现码片段：
```
sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
```
`size_bytes` 使用 `sa.Integer()`，在 PostgreSQL 上映射为 4 字节 `integer`，上限 2,147,483,647（约 2.1 GB）。模型工件（尤其是深度学习 checkpoint/微调权重）很容易超过该值，注册时 INSERT 会直接抛出 `integer out of range`，属于硬失败；而后续修正需要再写一次 ALTER 迁移。建议这里就用 `sa.BigInteger()`。同时若 `input_tokens`/`output_rows` 存在按批次累计写入（而非单次请求值）的用法，也应一并评估升级为 BigInteger。
建议：
```
sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
```

### [E2-41] medium/performance
现码片段：
```
op.create_index("ix_trained_models_product", "trained_models", ["product_id"])
```
`ix_trained_models_product` 与本链条后续迁移 0018_owner_fks 中的 `op.create_index("ix_trained_models_product_id", "trained_models", ["product_id"])` 是同一列上的重复索引（仅名称不同）。在真实 PostgreSQL 上执行完整链条后，`product_id` 上会存在两个完全等价的 B-Tree 索引：白白增加写放大与磁盘占用，并且优化器只会挑一个使用；同时 0018 的注释声称链条“从未创建该索引”与实际不符（本文件已创建），后续 ORM/autogenerate 比对仍会产生漂移噪音。建议本处删除该索引（保留 0018 的命名以与 ORM 一致），或让 0018 改为幂等地复用本索引名。

### [E2-42] medium/bug
现码片段：
```
sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
```
`inference_usage` 是用量/隐私预算（`epsilon_consumed`）计量表，但 `model_id`、`user_id` 都没有声明外键，`trained_models.model_id` 被删除或改写后仍会持续写入孤儿用量行，导致按模型聚合的用量与 epsilon 预算消耗错配且无法察觉（无法 JOIN 回来的行通常被静默丢弃）。同链条的 0008_mpc_persistence 已在 `create_table` 内声明 `ForeignKeyConstraint(..., ondelete="CASCADE")`，说明在建表时声明 FK 在本仓库是可执行的（SQLite 建表阶段支持 FK，仅 ALTER ADD 不支持）。建议至少为 `inference_usage.model_id` 增加指向 `trained_models.model_id` 的 FK，并为 `trained_models.owner_id`/`product_id` 补齐引用约束。
建议：
```
sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["trained_models.model_id"], ondelete="RESTRICT"),
```

### [E2-43] medium/maintainability
现码片段：
```
sa.Column("status", sa.String(length=16), nullable=False, server_default="registered"),
        sa.Column("watermark_json", sa.Text(), nullable=True),
```
`status` 只有长度限制和默认值，没有任何取值约束，且与 `revoked_at` 一起构成了“撤销状态”的双重真值来源：写入方拼错的值（如 `"revokd"`、`"Revoked"`）会被静默接受，出现 `status='registered'` 但 `revoked_at` 已置位（或反之）的矛盾行，而推理侧“模型是否可用”的判定通常只看其中一个字段，容易绕过撤销检查。建议加 `sa.CheckConstraint` 枚举合法值（PG 建表时可用），并在文档/ORM 侧明确 `status` 与 `revoked_at` 的单一权威来源与同步规则。

### [E2-44] low/performance
现码片段：
```
op.create_index("ix_inference_usage_model", "inference_usage", ["model_id"])
```
`ix_inference_usage_model` 为单列索引，而该表的典型访问是“某模型在某时间窗内的用量/预算聚合”，`created_at` 没有任何索引，随用量增长会退化为按 model 过滤后的大范围扫描 + 排序。同理 `trained_models` 列表页常按 `product_id`/`owner_id` + `status` 过滤，现有两个单列索引难以覆盖。建议把用量表索引改为复合索引 `["model_id", "created_at"]`（PostgreSQL 下仍可服务仅按 model_id 的查询，因此不损失原有能力），并视真实查询计划评估 `["product_id", "status"]`。
建议：
```
op.create_index("ix_inference_usage_model", "inference_usage", ["model_id", "created_at"])
```

## alembic/versions/0010_rag_generative.py (2)
### [E2-45] medium/bug
现码片段：
```
op.add_column("sandbox_tasks", sa.Column("rag_generative_model_id", sa.String(length=64), nullable=True))
```
类型不一致导致引用完整性无法约束：`sandbox_tasks.rag_generative_model_id` 声明为 `String(64)`，而本迁移新建的 `rag_generative_models.model_id` 是 `postgresql.UUID`（`app/models/rag_generative_model.py:22` 与 ORM 一致）。

后果：
1. 两者无法建立数据库级外键（PG 下 `varchar = uuid` 的比较/JOIN 会直接报 `operator does not exist`），任何按该列查询生成模型的语句都必须显式 cast；
2. 列中没有格式约束，写入非法字符串（非 UUID 文本）不会在写入时被拒绝，但下游把它转成 `uuid.UUID` 或作为 UUID 参数查询时会抛出 DB 层 `invalid input syntax for type uuid`，把本应 404/422 的输入变成 500；
3. 模型行被删除后任务列中会残留悬空 ID（当前无 FK/ON DELETE 语义可依赖）。

建议：若两列确为关联关系，把该列改为 `postgresql.UUID(as_uuid=True)` 并 `op.create_foreign_key(...)`（同时为兼容 SQLite CI，参照 0018 的做法明确取舍）；若刻意以字符串保存，则在 API/服务层入参处强制 `uuid.UUID(...)` 校验，并考虑加 `op.create_index` 支撑按模型反查任务。

### [E2-46] low/bug
现码片段：
```
sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
```
`size_bytes` 用 `sa.Integer()` 映射为 PG `INTEGER`，上限 2,147,483,647（约 2 GiB），超过即 `integer out of range` 插入失败。目前仅靠 `rag_generative_service._MAX_BUNDLE_BYTES = 256 MiB` 这个应用层常量兜住，DB 层没有任何保护；一旦上限调整（生成式 ONNX bundle 很容易超过 2 GiB）或存在旁路写入（数据修复/导入），就会在写入时静默报错。模型字节数属于典型的 64 位量字段，建议直接用 `sa.BigInteger()`（与 `trained_models.size_bytes` 一并评估），同时便于后续提高上传上限。
建议：
```
sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
```

## alembic/versions/0011_chain_anchoring_records.py (2)
### [E2-47] medium/bug
现码片段：
```
sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("receipt", sa.JSON(), nullable=True),
```
该表是锚点凭据的唯一持久化来源（模型 docstring 明确说明重启后依赖 `receipt` 重新水合校验锚点），但 DDL 层没有约束保证“已确认 ⇒ 必须存在链上证据”：`confirmed=false` 之外，`tx_hash`/`block_height`/`receipt` 全部可空，因此允许写入 `confirmed=true` 且 `tx_hash IS NULL`、`receipt IS NULL` 的行——这种行既无法在重启后被 verify（`receipt` 为空即丢失 AnchorRecord 全量 payload），又会让审计锚点校验把无凭据记录当作已确认，属于安全敏感的完整性缺口。

建议在 `create_table` 中补一个表级 CHECK 约束（PG/SQLite 均可表达），把不变量下沉到 DDL，而不是仅依赖应用层：
```python
sa.CheckConstraint(
    "confirmed = false OR tx_hash IS NOT NULL",
    name="ck_chain_anchoring_confirmed_has_tx",
),
```
若 `receipt` 对 verify 是必需的，也应一并约束 `receipt IS NOT NULL`。

（关于 pre-scan 提到的 `audit_record_id` 缺外键：按 `app/models/chain_anchoring.py` 的语义，无显式 record id 的锚点会把 anchor id 写入该列，且本仓库迁移链刻意不使用 FK（见 0018 的说明，SQLite 无法 ALTER ADD FK），因此不加 FK 属有意设计，此处不作问题上报。）
建议：
```
sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "confirmed = false OR tx_hash IS NOT NULL",
            name="ck_chain_anchoring_confirmed_has_tx",
        ),
```

### [E2-48] low/bug
现码片段：
```
sa.Column("block_height", sa.Integer(), nullable=True),
```
`block_height` 使用 `sa.Integer()`，在 PostgreSQL 上即 32 位 INTEGER（上限 2147483647）。链高/槽位号在语义上是 u64（如 Solana slot、部分链的 block height 增长极快），一旦超过 2^31，写入会直接抛 `NumericValueOutOfRange`，导致锚点落库失败——而锚点落库失败通常意味着审计锚定链路中断。`app/models/blockchain_anchor.py` 的 `block_number` 同样按 Integer 推断，存在同一隐患。

建议新建表时直接用 `sa.BigInteger()`（`app/models/chain_anchoring.py` 中对应字段同步改为 `BigInteger`）。注意：若 0011 已在任何环境执行过，不要原地修改本迁移（会与已建表结构不一致且破坏 `alembic check`），应追加一条新迁移做 `ALTER COLUMN ... TYPE BIGINT`。
建议：
```
sa.Column("block_height", sa.BigInteger(), nullable=True),
```

## alembic/versions/0013_volume_keys.py (2)
### [E2-49] medium/bug
现码片段：
```
sa.Column("algorithm", sa.String(length=32), nullable=False, server_default="SM4-GCM"),
```
`algorithm` 的 `server_default="SM4-GCM"` 与本文件所属特性的核心约束相冲突：`app/api/kms.py::_volume_cipher_label` 明确对插件未声明 cipher 的情况写入 `"unknown"`，注释里写的是「禁伪造：不得在审计记录中主张未经验证的国密算法」，测试 `test_register_without_declared_cipher_is_honest_unknown` 也固化了这一契约。但把 `SM4-GCM` 设成数据库层默认值后，任何不显式带 `algorithm` 的写入（SQL 回填/数据修复、绕过 ORM 的运维脚本、未来新增写入路径）都会静默地把一条未经验证的国密声明写进合规记录，而这恰恰是该特性想消除的伪造风险；同时它也与插件默认后端实为 AES fallback（`aes-128-gcm(fallback,non-GM)`）的现实不符。建议去掉该默认值（保持 NOT NULL 强制显式声明），或改为 `server_default="unknown"`。
建议：
```
# No server default: an undeclared cipher must be "unknown", never an
        # unverified SM4-GCM claim (see app/api/kms.py::_volume_cipher_label).
        sa.Column("algorithm", sa.String(length=32), nullable=False),
```

### [E2-50] low/security
现码片段：
```
sa.Column("key_length", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
```
`key_length` / `status` 只用默认值表达预期取值，没有任何数据库级 CHECK 约束，两条不变量都只靠应用层单点守卫：
1. `key_length` 硬编码默认 16，而实际长度由 `settings.CUBE_DEK_LENGTH` 决定（`app/api/kms.py` 按 `len(dek)` 写入），且 `kms_service` 其他路径使用的是 32 字节 SM4-256 密钥；一旦配置调整，DB 默认值就会写出与算法不匹配的行长（如 SM4-GCM/16 之外的组合、AES-128-GCM 配 32）。
2. `status` 未限定在 `active/revoked/destroyed` 集合内，一个大小写或拼写偏差（如 `"Active"`）会让 `distribute_volume_key` 直接 404（密钥不可用），而 `revoke_volume_key` 的 `status not in (...)` 守卫又会把它当作可撤销记录重复处理，故障表现为静默的密钥不可用而非报错。
3. `status='active'` 且 `wrapped_key IS NULL` 这种「无密钥材料的活跃密钥」在 schema 上是合法行（当前仅靠 `distribute` 里的 `vk.wrapped_key is None` 兜底）。
建议补充 `sa.CheckConstraint`（status 枚举、algorithm 与 key_length 配对、`status <> 'active' OR wrapped_key IS NOT NULL`），把不变量下沉到数据库。
建议：
```
sa.Column("key_length", sa.Integer(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'destroyed')",
            name="ck_volume_keys_status",
        ),
        sa.CheckConstraint(
            "status <> 'active' OR wrapped_key IS NOT NULL",
            name="ck_volume_keys_active_has_key",
        ),
```

## alembic/versions/0014_cube_templates.py (2)
### [E2-51] medium/bug
现码片段：
```
sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_cube_templates_name_version"),
```
`updated_at` 只设置了 `server_default=CURRENT_TIMESTAMP`，数据库层面**不会在后续 UPDATE 时自动刷新**。这意味着：

1. 任何绕过 ORM 的写入（手工 SQL 修数据、`UPDATE ... FROM` 批量修复脚本、其他语言写入方）都会让 `updated_at` 永远停留在插入时间；
2. 本表是节点分发状态机（PENDING/DISTRIBUTING/READY/FAILED）的审计时间来源，运维排查“节点卡在 DISTRIBUTING 多久了”“多久没重试”时依赖该字段，时间失真会直接误导排障与 GC/重试判断；
3. 目前只有 `app/models/cube_template.py` 中的 `onupdate=func.now()`（SQLAlchemy 客户端侧）保证刷新，属于隐式约束——迁移链才是生产 PG schema 的真实来源（参见 0018_owner_fks 的教训），schema 与 ORM 的这处语义差异建议显式补齐或至少在 docstring 中写明由应用层维护。

建议在迁移中加一个 `BEFORE UPDATE` 触发器（或改用数据库触发器统一维护），两张表同理（见下方 `cube_template_nodes.updated_at`）。
建议：
```
sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_cube_templates_name_version"),
        # 注意：updated_at 仅靠 server_default 不会在 UPDATE 时刷新，需配套触发器：
        # op.execute('''CREATE FUNCTION cube_templates_touch_updated_at() RETURNS trigger AS $$
        #     BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END; $$ LANGUAGE plpgsql;
        #     CREATE TRIGGER trg_cube_templates_touch_updated_at BEFORE UPDATE ON cube_templates
        #     FOR EACH RO
```

### [E2-52] low/bug
现码片段：
```
sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
```
`status` 列仅用 `String(16) + server_default` 限定，没有 CHECK / ENUM 约束，允许写入任意状态值。与上层状态机组合后这是一个真实的“数据卡死”风险：

`app/services/cube_template_service.py` 的 `_check_transition()` 使用 `table.get(src, set())`，即**未知/越界状态的值域为空集**，任何一条 status 被写成状态机之外的值（如大小写不一致 `ready`、遗留值 `ERROR`、脏数据），该行将永久无法转换，只能靠人工改库恢复；而 `cube_template_nodes.status` 还直接进入 `ready_node_ids()` 调度过滤，脏值会静默让节点被判定为“不可用”，表现为 `NO_NODE_TEMPLATE` 503 而非明确的配置错误。

建议在本迁移中补上显式 CHECK 约束（回滚时随表删除，属附加式变更），让非法状态在写入时即报错，而不是在状态机里变成不可达分支。
建议：
```
sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.CheckConstraint(
            "status IN ('PENDING','DISTRIBUTING','READY','FAILED')",
            name="ck_cube_template_nodes_status",
        ),
```

## alembic/versions/0015_cube_egress_dedup.py (5)
### [E2-53] medium/performance
现码片段：
```
op.create_index("ix_cube_egress_dedup_node", "cube_egress_dedup", ["node_id"])
```
`uq_cube_egress_dedup_key` 唯一约束会自动创建一个以 `node_id` 为前导列的 btree 索引，因此下面这行额外建的 `ix_cube_egress_dedup_node`（单列 node_id）在读取上是完全冗余的：按 node_id 前缀过滤（含本表唯一键点查）都能被唯一索引直接服务。而本表是 egress 审计热写入路径（每行 JSONL 一条 dedup 记录），多一个索引即多一份写放大与存储。建议删除该索引；若确有 janitor 按 `created_at` 扫描等其它访问模式，应改为为访问列单独建索引，而不是重复 node_id。
建议：
```
# 唯一约束 (node_id, file_name, offset, line_no) 已提供以 node_id 为前导列的索引，
    # 无需再建单列 ix_cube_egress_dedup_node（写热表上的冗余写放大）。
    # 若后续 janitor 需按时间清理，改为：
    # op.create_index("ix_cube_egress_dedup_created", "cube_egress_dedup", ["created_at"])
```

### [E2-54] medium/bug
现码片段：
```
sa.UniqueConstraint("node_id", "file_name", "offset", "line_no", name="uq_cube_egress_dedup_key"),
```
该表面向 at-least-once 重投递，但下游 `app/services/cube_egress_audit.ingest_egress_batch` 采用的是 “先 SELECT 查 dedup 行 → 再 db.add + 整批 commit” 的 check-then-act 模式，且整个循环外加一次性 `await db.commit()`，没有对唯一冲突做任何捕获。两条并发（或批内自重复）的同 key 推送会在 commit 时抛 IntegrityError：整个事务被中止，本批中所有“新行”一并丢失，接口返回 500，与 docstring 承诺的“重复只计数、不重复入库”不符。建议在摄入侧改为 `INSERT ... ON CONFLICT (node_id, file_name, offset, line_no) DO NOTHING`（用 rowcount 判定 duplicate），或对每行使用 SAVEPOINT 并捕获 IntegrityError 计入 duplicates。

### [E2-55] medium/maintainability
现码片段：
```
op.create_table(
```
本表是纯 append-only 幂等键表：每条 egress 审计行都永久占一行，但 `app/services/retention_janitor.py` 的清理计划只覆盖 audit_logs / alert_records / merkle_leaves，未包含 cube_egress_dedup，也没有任何 TTL/分区/清理逻辑。在 egress 流量较大的部署中该表会无界增长（且其唯一索引随表同步膨胀，点查逐渐变慢），属于长期运维风险。建议：把本表纳入 retention 计划（或按 created_at 做分区/TTL 清理），并同步为清理条件加上 `created_at` 索引；同时在 docstring 里写明“清理窗口必须 ≥ 节点最大重投递窗口”，否则会破坏幂等语义。

### [E2-56] low/bug
现码片段：
```
sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
```
`id` 是 NOT NULL 主键，但 DDL 未提供任何数据库级默认值（`server_default`），实际插入依赖 ORM 侧 `app/models/cube_egress_dedup.py` 里的 `default=uuid.uuid4`（Python 端生成）。因此任何绕过 ORM 的写入路径（SQL 控制台补数、批量回填脚本、`INSERT ... SELECT`、未来用原生 SQL 的服务）都会因缺少 id 直接触发 NOT NULL 违反而失败，且迁移与模型之间存在“默认值只存在于应用层”的隐式契约。建议加数据库级默认值，使 DDL 自洽。

（注：若刻意保持与 0011/0014 一致的“应用层生成 UUID”约定，则应在本文件 docstring 中显式写明，避免后续误用。）
建议：
```
sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False,
                server_default=sa.text("gen_random_uuid()")),
```

### [E2-57] low/maintainability
现码片段：
```
sa.Column("offset", sa.BigInteger(), nullable=False),
```
`offset` / `line_no` 只禁止 NULL，没有非负/下界 CHECK 约束。当前唯一的写入入口 `app/api/cube_egress.py` 用 Pydantic（`offset: int = Field(ge=0)`、`line_no: int = Field(ge=1)`）做了校验，所以现网不会被污染；但幂等键的取值合法性目前只存在于 API 层，任何绕过该 schema 的写入（补数、其它摄入路径、直接 SQL）都能把负 offset 写进唯一键，使同一行以不同 key 重复入库。建议以数据库 CHECK 约束把契约下沉（非阻塞的加固建议）。
建议：
```
sa.Column("offset", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.CheckConstraint("offset >= 0", name="ck_cube_egress_dedup_offset"),
        sa.CheckConstraint("line_no >= 1", name="ck_cube_egress_dedup_line_no"),
```

## alembic/versions/0016_sandbox_tee_policy.py (4)
### [E2-58] medium/bug
现码片段：
```
sa.Column("tee_policy", sa.String(length=32), nullable=True, server_default="tee_if_available"),
```
`tee_policy` 与 `trust_root` 同时声明 `nullable=True` + `server_default`，语义上是三态：DB 里既有历史遗留的 `NULL`，又有显式的 `'none'`，还有正常策略值。

1. 对 `trust_root` 而言 `'none'` 是一个有明确安全含义的值（文档所说“honest degradation：节点 TEE 能力缺失/畸形一律记为 none，绝不上调”），而 `NULL` 表示“未知/未记录”。由于列可空，应用层任何一次显式写 `None`（或绕过 ORM 的 `update()` / `insert()` 表达式语句，不会走 Python 侧 default）都会留下 `NULL`；下游若写 `if session.trust_root != "none": 视为有可信根` 就会把 `NULL` 判成“有可信根”，正好是安全策略的反向失效。
2. 对 `tee_policy` 同理，`NULL` 与 `'tee_if_available'` 会被下游当成两种不同分支，产生行为分歧。

建议二选一：要么 `nullable=False, server_default=...`（列语义唯一，不可能出现 NULL）；要么去掉 `server_default`，让存量行保持 `NULL` 并由应用层显式区分“未记录”与“none”（与 0024 的做法一致）。同时把该列在模型里也补上 `server_default`，避免 autogenerate 反向生成 DROP DEFAULT。

### [E2-59] medium/security
现码片段：
```
sa.Column("trust_root", sa.String(length=16), nullable=True, server_default="none"),
```
`tee_policy` / `trust_root` 是安全策略字段，但只有定长 `String`，没有任何 DB 级取值约束：`'TEE_Required'`、`'tee-required'`、`'tee_requried'`、`'sev-snp'` 这类拼写/大小写错误都会被静默写入。一旦 `tee_policy` 被写成非法值，下游按 `== "tee_required"` 做强校验的分支就不会命中，会话会在“应当强制 TEE”的情况下按 best-effort 甚至无 TEE 调度，属于静默降级且难以排查。建议至少给 `tee_policy` 加 `sa.CheckConstraint("tee_policy in ('tee_if_available','tee_required','none')", name="ck_sandbox_sessions_tee_policy")`（`trust_root` 同理），或改用 PG 原生 ENUM；注意 `server_default` 必须落在允许集合内，否则约束会挡住 ADD COLUMN。

### [E2-60] low/documentation
现码片段：
```
op.add_column(
        "sandbox_sessions",
        sa.Column("tee_policy", sa.String(length=32), nullable=True, server_default="tee_if_available"),
    )
```
`server_default` 会把所有存量 `sandbox_sessions` 行回填成 `"tee_if_available"`，但这些历史会话在创建时根本不存在“请求 TEE 策略”这个概念——这等于给历史审计记录伪造了一个从未表达过的策略意图。项目里同类“加列”迁移（0024_bundle_review_columns）明确写的是“只加列、不回填，存量行保持 NULL 并由前端显示未记录，绝不反推”。

如果 `tee_policy` / `trust_root` 会用于安全审计或合规追溯（“该会话当时要求的隔离强度是什么”），建议保持存量行为 `NULL`（去掉 `server_default`，由应用层在 create 时写默认值），并在 docstring 里明确“存量行不回填”的取舍；反之如果确认这两个字段只影响新建会话的调度、不进审计链，请在 docstring 补一句说明，避免后续读者误读成历史策略。

### [E2-61] low/documentation
现码片段：
```
op.drop_column("sandbox_sessions", "trust_root")
    op.drop_column("sandbox_sessions", "tee_policy")
```
`downgrade()` 直接 `drop_column` 会不可逆丢弃已落库的 TEE 策略与信任根。信任根本质上是节点可信根的证据快照（`tdx` / `sev_snp`），一旦回滚，事后无法回答“某会话当时跑在哪种可信根上”。虽然 additive 列回滚删列本身是常规做法，但鉴于这是安全字段，建议在 `downgrade` 里加一行注释声明该数据丢失是已知且可接受的（或改为先把非 `none` 值转存到审计表/JSON 列再删列），避免运维在未评估审计影响的情况下执行 downgrade。

## alembic/versions/0017_api_keys.py (2)
### [E2-62] medium/bug
现码片段：
```
sa.ForeignKeyConstraint(["issued_by"], ["users.id"]),
```
`issued_by` 为 NOT NULL 且引用 `users.id`，但未声明 `ondelete`（也未声明 `nullable=True`）。默认语义是 NO ACTION/RESTRICT，因此一旦引入用户删除（含运维清理脚本、`retention_janitor` 类清理任务扩展），删除 `users` 行会被本表外键直接阻断并抛 IntegrityError；而本表按设计“行永不物理删除”，两者会形成死锁式的冲突。另需注意 `app/api/api_keys.py:rotate_api_key` 会把新行的 `issued_by` 写成执行轮转的管理员，签发人随轮转漂移，取证语义也随之变化。

建议：显式声明删除语义（例如 `ondelete="RESTRICT"` 并在注释中写明“用户只做停用不做物理删除”，或改为 `SET NULL` + 允许 nullable），使该约束成为有意声明而非隐式默认。
建议：
```
sa.ForeignKeyConstraint(["issued_by"], ["users.id"], ondelete="RESTRICT"),
```

### [E2-63] low/security
现码片段：
```
sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
```
`status` 只有 `server_default="active"`，数据库层没有任何取值域约束；`revoked_at` / `expires_at` / `updated_at` 之间也没有跨字段 CHECK。当前 `app/api/api_keys.py` 的 PATCH 只允许 `active|disabled`、`revoked` 由 revoke/rotate 路径写入，所以 ORM 路径下状态机是自洽的；但由于本表是**机器凭据**表，任何绕过 ORM 的写入（运维 SQL、后续新增的迁移内 `op.execute` 回填、批量修复脚本）都可能写出 `status='revoked'` 而 `revoked_at IS NULL`、或拼错的 `status`（如 `'enabled'`）——`app/core/deps.py:_resolve_api_key` 用 `key.status != "active"` 判定，这类脏数据会静默变成“永久不可用/状态与取证字段不一致”，而没有任何约束会拒绝它。同时 `server_default="active"` 意味着“默认即签发可用”，缺少 CHECK 时更易被误用。

建议：为该凭据表补一条取值域 CHECK（至少覆盖 `status IN ('active','disabled','revoked')`，以及 `status='revoked'` 时 `revoked_at IS NOT NULL`），把状态机不变量下沉到 schema。
建议：
```
sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        # 凭据状态机不变量下沉到 schema（防止绕过 ORM 的写入产生非法生命周期状态）
        sa.CheckConstraint(
            "status IN ('active','disabled','revoked')",
            name="ck_api_keys_status",
        ),
```

## alembic/versions/0018_owner_fks.py (3)
### [E2-64] medium/performance
现码片段：
```
op.create_index("ix_trained_models_product_id", "trained_models", ["product_id"])
```
在 PostgreSQL 上，`op.create_index` 默认创建的是非并发索引，且执行在迁移事务内：`CREATE INDEX` 会对 `trained_models` 持有阻塞写入的锁，若该表规模较大，会显著拉长部署窗口甚至造成线上写入等待（本迁移正是为了修复 PG 生产链路而存在，因此这条路径一定会被真实执行）。SQLite 无此问题，但需要区分方言处理。
建议：
```
if op.get_bind().dialect.name == "postgresql":
        # CREATE INDEX 会在事务内长时间持锁阻塞写入，PG 上改用 CONCURRENTLY（不能在事务中执行）
        with op.autocommit_block():
            op.create_index(
                "ix_trained_models_product_id",
                "trained_models",
                ["product_id"],
                postgresql_concurrently=True,
            )
    else:
        op.create_index("ix_trained_models_product_id", "trained_models", ["product_id"])
```

### [E2-65] low/maintainability
现码片段：
```
import sqlalchemy as sa
```
`import sqlalchemy as sa` 在本迁移中从未被引用（upgrade/downgrade 只调用 `op.create_index` / `op.drop_index`），属于无功能价值的死导入，容易让后续维护者误以为这里还有额外的 schema 操作。建议删除该行（若后续需要可再引入）。

### [E2-66] low/bug
现码片段：
```
op.drop_index("ix_trained_models_product_id", table_name="trained_models")
```
`downgrade` 无条件假设索引一定存在：若迁移链与真实 schema 不一致（例如历史上有人手工建/删过该索引、或 downgrade 被重复执行），`drop_index` 会直接抛出 `Index ... does not exist` 使回滚中断。建议加上 `if_exists=True` 提升幂等性；另外如果 upgrade 采纳了 `postgresql_concurrently=True`，这里也需对应使用并发删除，避免回滚时重新阻塞表。
建议：
```
op.drop_index(
        "ix_trained_models_product_id",
        table_name="trained_models",
        if_exists=True,
    )
```

## alembic/versions/0019_flow_orchestration.py (3)
### [E2-67] medium/bug
现码片段：
```
op.add_column("workloads", sa.Column("test_run_id", sa.UUID(), nullable=True))
```
`test_run_id` 只做了 `add_column`，没有创建指向 `workload_test_runs.id` 的外键，但模块 docstring 明确写着“保持线性迁移且保留真实外键”，实现与声明不一致。此处 `workload_test_runs` 已经建好，技术上完全可以补 FK（`workload_test_runs.workload_id → workloads.id` 是先建的父引用，不会形成建表死锁：先插 workload（test_run_id 为 NULL）→ 插 test run → 回填 test_run_id）。当前状态下任意值都能写进 `test_run_id`，一旦应用层（workflow_service 的 test_passed/published 门禁）出现 bug 或有人手工改数据，就会出现“指向不存在测试运行的发布版本”，而这正是发布门禁唯一凭证字段，悬空引用会让门禁判断静默失效。若确认要保留松引用，请至少同步修正 docstring 的“保留真实外键”表述，避免误导后续维护者；若要补 FK，需同时给 `app/models/workload.py` 的 `test_run_id` 加上 `ForeignKey("workload_test_runs.id")`，否则模型元数据与库结构会产生 autogenerate 漂移，另外还要注意删除 workload 时会被该 FK RESTRICT。
建议：
```
op.add_column("workloads", sa.Column("test_run_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_workloads_test_run_id",
        "workloads",
        "workload_test_runs",
        ["test_run_id"],
        ["id"],
    )
```

### [E2-68] low/bug
现码片段：
```
sa.UniqueConstraint("event_id", name="uq_webhook_deliveries_event_id"),
```
`event_id` 上加了全局 UNIQUE，但该表每一行本身已经带 `subscription_id`，即“一个事件 × 一个订阅 = 一行投递”的扇出模型。目前 `delivery_service.dispatch_event` 对每个匹配订阅都用 `uuid.uuid4().hex` 生成独立 `event_id`，所以暂时不会冲突；但这意味着 `event_id` 实际是“投递 ID”而非“事件 ID”：同一逻辑事件推给多个订阅时接收方看到的是不同 ID，后续若想改为按逻辑事件生成确定性 `event_id`（便于接收方跨订阅去重、或做投递幂等），这条全局唯一约束会直接拒绝第二条记录。与订阅维度的语义一致的做法是复合唯一 `(subscription_id, event_id)`（重试仍复用同一行，去重语义不受影响）。
建议：
```
sa.UniqueConstraint("subscription_id", "event_id", name="uq_webhook_deliveries_sub_event"),
```

### [E2-69] low/performance
现码片段：
```
sa.ForeignKeyConstraint(["source_notebook_id"], ["notebooks.id"]),
        sa.ForeignKeyConstraint(["source_task_id"], ["sandbox_tasks.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["workloads.id"]),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"]),
```
本迁移新增了大量外键列，但除少数几列外都没有配套索引：`workloads.source_notebook_id / source_task_id / parent_id / published_by`、`notebooks.session_id / parent_id`、`workload_test_runs.triggered_by / session_id`、`compute_runs.workload_id / task_id / requested_by / session_id`、`api_endpoints.workload_id`。PostgreSQL 不会为外键自动建索引，父表行被删除或主键更新时需要全表扫描子表做约束校验；这里的工作负载版本化（`parent_id` 链、`is_latest` 翻版）、按合约清理 compute_runs、退役 workload 等都会命中这条路径，子表随运行时间增长后删除/更新代价会明显放大。建议至少给写入路径上会被父表删除/更新的列补索引（`ix_*_workload_id`、`ix_compute_runs_workload_id`、`ix_compute_runs_task_id`、`ix_workloads_parent_id`）。

## alembic/versions/0020_webhook_detached_history.py (3)
### [E2-70] high/bug
现码片段：
```
def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("webhook_deliveries", "subscription_id", existing_type=sa.UUID(), nullable=False)
```
**downgrade 会因 upgrade 后产生的 NULL 行必然失败（迁移不可回滚）**

本迁移的语义前提就是：升级后 `subscription_id` 允许为空，删除订阅时把子表外键置空并保留投递历史。也就是说，只要线上发生过一次“删除订阅”，`webhook_deliveries` 中就会出现 `subscription_id IS NULL` 的行。此时执行 `downgrade` 直接把列改回 `nullable=False`，PostgreSQL 会抛 `NotNullViolation`（`... contains null values`），SQLite 会抛 `NOT NULL constraint failed`，回滚流程直接中断。

这不仅是“回滚失败”，还会把数据库留在中间状态：PG 下 `alter_column` 单语句失败会自动回滚，事务可保持干净；但 SQLite 分支的 `batch_alter_table` 走的是“新建表 → 拷贝数据 → 交换表名”，NOT NULL 校验发生在拷贝阶段，可能留下临时表 / 已丢失的原表，需人工介入。

建议二选一：
1. 在 downgrade 开头显式处理孤儿行（删除，或回填一个占位订阅 ID），再改非空；若无法回填则主动 `raise` 给出明确错误信息，提示运维先清理数据。
2. 若认为该变更不可逆，则 downgrade 直接 `pass` 并在 docstring 中注明“nullable 变更不回滚”，避免产生“可以安全回滚”的错误预期。

### [E2-71] medium/bug
现码片段：
```
with op.batch_alter_table("webhook_deliveries") as batch:
            batch.alter_column("subscription_id", existing_type=sa.UUID(), nullable=False)
```
SQLite 分支同样存在上述 NOT NULL 回滚失败问题，并且风险更高：`batch_alter_table` 在 SQLite 上通过“建临时表 + INSERT ... SELECT 全量拷贝 + rename”实现列属性变更。一旦存量数据中存在 `subscription_id IS NULL` 的投递历史，拷贝阶段就会报 `NOT NULL constraint failed: webhook_deliveries.subscription_id`，此时原表已被 `DROP`，索引（`ix_webhook_deliveries_subscription_id` / `ix_webhook_deliveries_sub_status` / `ix_webhook_deliveries_status_retry`）与 FK 关系也处于半重建状态。

建议在进入 batch 之前先做一次显式校验，失败时给出可读错误而不是底层 DDL 报错，例如：
```python
null_count = op.get_bind().execute(
    sa.text("SELECT count(*) FROM webhook_deliveries WHERE subscription_id IS NULL")
).scalar()
if null_count:
    raise RuntimeError(f"webhook_deliveries 存在 {null_count} 条 subscription_id 为空的历史记录，无法回滚为 NOT NULL，请先清理")
```

### [E2-72] low/maintainability
现码片段：
```
if op.get_bind().dialect.name == "postgresql":
        op.alter_column("webhook_deliveries", "subscription_id", existing_type=sa.UUID(), nullable=True)
```
`upgrade` 只放开约束，没有对“已被置空的历史行”做任何可追溯性保障：投递记录一旦失去 `subscription_id`，其 `payload` 之外的订阅上下文（url / name / user_id）全部丢失，既无法在后续迁移中回填，也无法做“孤儿历史”统计与清理策略；而 0019 建立的 FK 仍是无 `ON DELETE` 的 RESTRICT 语义，一旦 `delete_webhook` 的置空逻辑被回退或被旁路（如直接 `DELETE FROM webhook_subscriptions` 的运维脚本、数据修复任务），仍会重新触发 ForeignKeyViolation。

建议至少在 docstring 中补充：孤儿行的判定与保留策略（例如 `WHERE subscription_id IS NULL` 的清理/归档任务），并考虑在 upgrade 末尾创建一个 `webhook_deliveries_orphan_history` 视图或统计索引，便于后续迁移与运维定位这类记录。

## alembic/versions/0021_delivery_bundle_ownership.py (2)
### [E2-73] high/bug
现码片段：
```
sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
```
`size_bytes` 用 `sa.Integer()`，在 PostgreSQL 上映射为 32 位 `INTEGER`（上限 2,147,483,647 ≈ 2GB）。数据集交付包（zip 打包整批密态成果）很容易超过 2GB，插入时 PG 会抛 `numeric value out of range`（SQLAlchemy `DataError`），表现为创建交付包/写归属记录时 500；而 CI/dev 走 SQLite 时不校验范围，测试会保持绿色（与本仓库 0018 记录的“PG 路径漂移”同一类问题）。建议改为 `sa.BigInteger()`，并同步修改 `app/models/delivery_bundle.py` 中 `size_bytes` 的列类型，以免 `alembic check` 报漂移。
建议：
```
sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
```

### [E2-74] medium/bug
现码片段：
```
sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sandbox_sessions.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
```
三个外键都没有显式声明删除策略，PG 下等价于 `ON DELETE NO ACTION`：本表一旦有行，硬删除对应合约/沙箱会话/用户就会被约束阻塞并抛 `IntegrityError`（表现为 500）。这正是 0020 为 `webhook_deliveries.subscription_id` 处理过的同类问题（“投递记录属交付历史，按保留策略不删”），而此处新建表时未做同样决策；SQLite（CI/dev）默认也不强制外键，问题只会在生产 PG 暴露。请明确生命周期设计并落文：`session_id`/`created_by` 建议 `ondelete="SET NULL"`（两列本身可空），`contract_id` 为 NOT NULL，需显式选择 `ondelete="CASCADE"` 还是有意保留 RESTRICT 并在删除合约的服务层先处理归属记录（与 0020 的做法一致：不依赖 ON DELETE 是否被方言强制）。
建议：
```
sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sandbox_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
```

## alembic/versions/0022_compute_run_params.py (2)
### [E2-75] medium/bug
现码片段：
```
op.add_column("compute_runs", sa.Column("params", sa.JSON(), nullable=True))
    op.add_column("compute_runs", sa.Column("resource_override", sa.JSON(), nullable=True))
```
**NULL 与「显式空参数」语义不可区分，与本迁移自述的可复现目标冲突**（focus area 1）

本文件 docstring 明确「参数本体必须与运行行同存」以保证运行可复现，但两列都只写了 `nullable=True` 且没有 `server_default`，导致三种事实在库里长得一模一样：
1. 迁移前的存量运行行（从未记录过输入）；
2. 调度触发路径未传参（`claim_run(..., params=None, resource_override=None)`，见 `compute_scheduler.claim_run`）；
3. 人工触发时用户显式提交了 `{}` / 空的规格覆盖。

第 3 类尤其危险：`execute_run` 里 `spec = {**contract.sandbox_resource_spec, **workload.resource_spec, **(run.resource_override or {})}`，`NULL` 与 `{}` 都退化为「不覆盖」；`_entrypoint_with_params` 同样以 `if not params` 做真值判断，`{}` 时根本不注入 `CDS_PARAMS`。也就是说「显式收窄到空」这一意图被静默吞掉，事后按 `params`/`resource_override` 回放时无法判断当时到底传了什么，审计口径与真实执行不一致。

建议：至少在本迁移中用注释/`comment=` 固化语义（NULL=未记录，`{}`=显式空），并在写入侧（人工触发路径）对确实提交了参数的运行强制落非 NULL 值（如 `{}`），使「未记录」与「记录为空」在 DB 层可区分。若需要硬约束，可考虑对人工触发（`trigger='manual'`）的运行加 CHECK 约束。
建议：
```
# 语义约定：NULL = 未记录（迁移前存量 / 调度触发未传参）；'{}' = 本次显式提交了空输入。
    # 人工触发路径必须写入非 NULL，否则回放时无法区分「没传」与「传了空」。
    op.add_column("compute_runs", sa.Column("params", sa.JSON(), nullable=True, comment="本次运行提交参数；NULL=未记录，{}=显式空"))
    op.add_column("compute_runs", sa.Column("resource_override", sa.JSON(), nullable=True, comment="规格向下收窄覆盖；NULL=未记录，{}=显式空"))
```

### [E2-76] medium/bug
现码片段：
```
op.drop_column("compute_runs", "resource_override")
    op.drop_column("compute_runs", "params")
```
`downgrade()` 无条件 `drop_column`，会把 `compute_runs` 中已留存的运行输入**不可逆删除**（focus area 2）。

这两列存在的唯一目的就是让历史运行可复现/可审计（`params` 注入的是被审代码的一部分），而 `drop_column` 在 PG 上会连带丢掉所有已写入的 JSON 值，回滚后即使重新 `upgrade` 也无法恢复，历史运行记录从此只剩 `output_files`/`resource_usage` 等结果侧字段，输入侧永久缺失——这与本项目「诚实记录、不反推」的审计纪律相悖（对比 0023/0024 明确采用 additive-only 纪律）。

建议：在 docstring 中显式声明「downgrade 会丢弃全部运行输入，仅可用于未上生产的环境」；若需要更稳妥，可让 downgrade 先把两列数据导出到一张备份表（或将列改名保留），至少在执行前打印显式告警，避免运维在日常环境回滚时误删审计数据。
建议：
```
# 注意：以下 DROP 会不可逆丢弃全部已留存的运行输入（params / resource_override），
    # 回滚后 compute_runs 历史行不再可复现；生产环境回滚前请先备份这两列。
    op.drop_column("compute_runs", "resource_override")
    op.drop_column("compute_runs", "params")
```

## alembic/versions/0023_task_fields.py (1)
### [E2-77] medium/performance
现码片段：
```
op.create_index("ix_notebooks_task_no", "notebooks", ["task_no"], unique=True)
```
在大表 `notebooks` 上直接 `CREATE UNIQUE INDEX`（非 CONCURRENTLY）会与前面的 5 个 `ADD COLUMN` 处在同一个迁移事务里（alembic/env.py 的 `do_run_migrations` 用 `context.begin_transaction()` 包裹），PG 上该语句持 ACCESS EXCLUSIVE/SHARE 锁并全表扫描排序，迁移期间会阻塞 notebooks 的写入；索引越大阻塞越久，且因为在同一事务内，无法中途改用 CONCURRENTLY。建议对生产路径显式处理：PG 方言下把建索引放进 `op.get_context().autocommit_block()` 并用 `postgresql_concurrently=True`（注意 CONCURRENTLY 失败会留下 INVALID 索引，需在其后校验/重建），或在本文件 docstring/发布手册中明确该迁移需停机窗口。同时 `downgrade()` 里的 `drop_index` 也是阻塞操作，回滚窗口同样要评估（task_no/内存配置等数据被删即不可恢复，建议只在确认无数据依赖时开放 downgrade）。
建议：
```
# 大表在线迁移：PG 下并发改用 CONCURRENTLY（需 autocommit_block），其余方言保持原路径
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_notebooks_task_no",
            "notebooks",
            ["task_no"],
            unique=True,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
```

## contracts/AuditRegistry.sol (4)
### [E2-78] high/security
现码片段：
```
function anchorAudit(
        bytes32 merkleRoot,
        string memory metadata
    ) external {
```
`anchorAudit` 是 `external` 且没有任何访问控制（无 `onlyOwner`/`onlyRole`、无签名校验），而下方 `require(audits[merkleRoot] == 0)` 使写入是 **先到先得（first-writer-wins）** 且**永久不可修正**。

后果：任何地址都可以抢先（front-run，甚至直接用 `eth_call`/mempool 观测待锚定的 root）把伪造的 Merkle root 写入 `audits[merkleRoot]`，一旦写入：
1) 合法审计方永远无法再锚定该 root（`already anchored` 永久 revert），造成合法审计记录被永久 DoS/锁定；
2) 下游若把本注册表当作权威来源（`verifyAudit == true` 即认为审计有效），会接受一个由攻击者任意时间戳背书的伪造 root。

即使当前设计意图是 permissionless，也应至少将写入权限收敛到审计方（`onlyRole(ANCHORER_ROLE)`）或采用 `ecrecover`/EIP-712 签名校验（签名需绑定 `merkleRoot`、`msg.sender`、chain id、nonce）；若确实要保持 permissionless，则必须在下游把 `verifyAudit` 降级为“存在性证明”而非“权威性证明”，并在事件中记录锚定者（见另一条反馈）。
建议：
```
/// @dev 需要引入访问控制（示例：仅 ANCHORER_ROLE 可写入）
    function anchorAudit(
        bytes32 merkleRoot,
        string memory metadata
    ) external onlyRole(ANCHORER_ROLE) {
```

### [E2-79] medium/bug
现码片段：
```
require(audits[merkleRoot] == 0, "AuditRegistry: already anchored");
```
未校验 `merkleRoot != bytes32(0)`，与 `audits[merkleRoot] == 0` 作为“未锚定”哨兵值组合后有两个问题：
1) 攻击者可合法地把空 root（`bytes32(0)`）锚定进来，从而占位并污染注册表；若下游对空叶子/空批次的证明计算得到全零 root，会被 `verifyAudit(bytes32(0))` 判定为“已锚定”，形成假阳性验证。
2) 全零 root 本身不是有效的 SM3 Merkle 根，应显式拒绝。

建议在写入前加零值校验。
建议：
```
require(merkleRoot != bytes32(0), "AuditRegistry: null root");
        require(audits[merkleRoot] != 0, "AuditRegistry: not anchored"); // 反向断言见下行
        require(audits[merkleRoot] == 0, "AuditRegistry: already anchored");
```

### [E2-80] medium/maintainability
现码片段：
```
event AuditAnchored(
        bytes32 indexed merkle_root,
        uint256 timestamp,
        string metadata
    );
```
状态变更事件缺少“受影响方/操作者”标识：`AuditAnchored` 只记录了 root、时间戳和 metadata，没有记录锚定者地址，`audits` 映射也未保存锚定者。

在 `anchorAudit` 对所有人开放（见上面的权限问题）的前提下，索引器/审计方无法区分“合约自身写入的合法锚定”与“外部地址抢跑写入的伪造锚定”，事后无法追溯与作废，等于丧失了唯一的取证手段。

建议：在事件中加入 `address indexed anchor`（并考虑记录 `tx.sender`），同时在 `audits` 的值或新增结构中保存锚定者地址。
建议：
```
event AuditAnchored(
        bytes32 indexed merkle_root,
        address indexed anchor,
        uint256 timestamp,
        string metadata
    );
```

### [E2-81] low/bug
现码片段：
```
audits[merkleRoot] = block.timestamp;
```
用 `0` 表示“未锚定”、用 `block.timestamp` 表示“已锚定”，把“存在性”与“时间戳”复用在同一个 slot 上，逻辑上依赖 `block.timestamp > 0` 这一环境假设：
- 在本地/测试网/自建链（Foundry `vm.roll(0)`、部分 L2/私链创世区块时间戳为 0）上，时间戳为 0 时写入的锚定记录在 `verifyAudit` 中会返回 `false`，且可被重复锚定，测试与生产语义不一致；
- 同时该设计使“锚定时间”无法被合法地设为哨兵值，也永远无法表达“已撤销/已更新”等状态。

建议将存在性与时间分离（例如 `mapping(bytes32 => uint256) public audits;` 保存 `block.timestamp + 1`，或引入 `mapping(bytes32 => AuditRecord)` 并带 `exists`/`version` 字段），避免依赖环境时间戳下界。
建议：
```
// 偏移 1，避免 block.timestamp 为 0 时与“未锚定”哨兵值冲突
        audits[merkleRoot] = block.timestamp + 1;
```

## policies/system.rego (2)
### [E2-82] low/maintainability
现码片段：
```
health = true
```
`health` 是无条件为 `true` 的常量规则，任何 `input`/`data` 下都返回 `true`，不反映 OPA 实际状态（bundle 是否加载、策略是否编译通过、依赖后端是否可达）。

已核对：`k8s/opa.yaml` 的 readinessProbe 走的是 OPA 自带的 `/health` HTTP 端点，而不是 `data.cds.system.health`，因此当前看不到它被用于授权或就绪判定的证据——这也正是需要确认的点：如果它只是占位/就绪标记，建议加一行注释说明其语义边界，避免后续有人把它当作可信健康证据接入授权路径或运维探测（那样会变成"永远健康"的假阳性）；若确实要表达真实状态，应改为基于可观测信号（如依赖的 `data` 是否存在）求值。

另：若确认该规则当前无人使用，删除比保留一个恒真规则更安全。

### [E2-83] low/test
现码片段：
```
default allow = false
```
该包目前只有 `default allow = false`，没有任何正向 `allow` 规则，即 `data.cds.system.allow` 在所有输入下恒为 `false`；同时仓库内没有对应的 `policies/system_test.rego` 来固化这一"默认拒绝"契约（全仓仅此一个 .rego 文件）。

风险在于：后续在此包新增 `allow if ...` 时，Rego 的语义是所有同名 `allow` 规则**并集**（任一命中即放行），没有测试基线就容易在无意中加入过宽规则而把系统级 deny 基线翻成放行。建议补一个最小测试固定两条断言：1) 空/常规 `input` 下 `allow == false`；2) 明确列出预期的拒绝场景。若确实设计上要放行健康类端点，也应在此包显式补上有约束的 `allow` 规则并配对应 allow-path 测试，而不是依赖调用方绕过本包。

## sdk/cds_sdk/__init__.py (1)
### [E2-84] medium/bug
现码片段：
```
__version__ = "0.2.0"
```
版本元数据不一致：`sdk/pyproject.toml` 中 `cds-sdk` 的 `version = "0.3.0"`，而包入口的 `__version__` 仍为 `"0.2.0"`。安装后调用方通过 `importlib.metadata.version("cds-sdk")` 得到 0.3.0，而 `cds_sdk.__version__` 得到 0.2.0，诊断信息、User-Agent 与问题上报会出现自相矛盾的版本号。建议同步为 0.3.0，或改为从包元数据派生（`__version__ = importlib.metadata.version("cds-sdk")`，并对未安装场景做 fallback），避免两处版本号各自漂移。
建议：
```
__version__ = "0.3.0"
```

## sdk/cds_sdk/client.py (18)
### [E2-85] high/bug
现码片段：
```
def _request_with_headers(self, method: str, path: str, **kwargs: Any) -> tuple[Any, dict]:
        retries = kwargs.pop("_retries", self._max_retries)
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code in (429, 503) and retries > 0:
            time.sleep(min(_retry_after_seconds(resp), 5.0))
            return self._request_with_headers(method, path, _retries=retries - 1, **kwargs)
        body = None if resp.status_code == 204 or not resp.content else resp
```
`_request_with_headers` 与 `_request` 的语义不一致：它从不检查 `resp.status_code >= 400`，因此 401/403/404/500 会被当成成功返回（且返回体为错误 JSON 或 `None`）；同时 `resp.json()` 未捕获 `json.JSONDecodeError`，网关返回 HTML/纯文本时会直接抛出与 API 无关的解析异常。

调用方 `list_sessions` 还在此之上做 `body["next_cursor"] = ...`：当响应为 204/空体（`body is None`）或返回 JSON 数组（`list`）时会抛 `TypeError`，调用方拿到的是难定位的 TypeError 而不是 `CDSApiError`。

建议与 `_request` 对齐：非 2xx 抛 `_parse_error(resp, path)`，JSON 解析失败回退为原始文本，并在 `list_sessions` 里用 `if isinstance(body, dict):` 守卫后再写入 `next_cursor`。
建议：
```
def _request_with_headers(self, method: str, path: str, **kwargs: Any) -> tuple[Any, dict]:
        retries = kwargs.pop("_retries", self._max_retries)
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code in (429, 503) and retries > 0:
            time.sleep(min(_retry_after_seconds(resp), 5.0))
            return self._request_with_headers(method, path, _retries=retries - 1, **kwargs)
        if resp.status_code >= 400:
            raise _parse_error(resp, path)
        if resp.status_code == 204 or not resp.content:
            body = None
        else:
            try:
                body = resp.json()
            except ValueError:
                body
```

### [E2-86] high/bug
现码片段：
```
try:
        payload = resp.json()
        detail = payload.get("detail", payload)
    except ValueError:
        payload = None
        detail = resp.text
```
`resp.json()` 对合法但不是 JSON object 的错误体（如 `["boom"]`、`"unauthorized"`、`123`）不会抛 `ValueError`，因此走到 `payload.get("detail", payload)` 会抛 `AttributeError: 'list' object has no attribute 'get'`，把真实的 HTTP 错误（401/403/429 等）掩盖成解析异常，且丢失 status_code/detail。另外 `json.JSONDecodeError` 只是 `ValueError` 子类，httpx 在某些后端下抛的 `JSONDecodeError`（简单json）不一定被捕获。
建议：
```
try:
        payload = resp.json()
    except Exception:
        payload = None
        detail = resp.text
    else:
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
```

### [E2-87] high/security
现码片段：
```
path = f"/api/v1/sandbox-sessions/{session_id}/files/{filename}"
        resp = self._client.get(path)
```
`filename`（以及各处 `session_id`/`snapshot_id`/`slug` 等标识符）未经校验或百分号编码直接拼进 URL 路径：
- 传入 `"../../admin/users"` 之类的值会逃逸出 `/files/` 段，请求被打到同前缀下的其他路由（配合客户端已带上的 Bearer / `X-App-Secret`，构成 SSRF/越权访问面）；
- 含 `?`/`#` 时后半段被当作 query/fragment 静默丢弃，含空格或非 ASCII 时 httpx 可能抛 `InvalidURL`。

`delete_file` 存在同样问题。建议用 `httpx.URL` 的路径段编码或 `urllib.parse.quote(filename, safe="")`，并对含 `/`、`..`、`\0` 的输入直接抛 `ValueError`。
建议：
```
safe_name = quote(str(filename), safe="")
        path = f"/api/v1/sandbox-sessions/{session_id}/files/{safe_name}"
        resp = self._client.get(path)
```

### [E2-88] medium/security
现码片段：
```
if code is not None:
            params["code"] = code
```
`code` / `rag_query` 通过 URL query 传输（虽与后端 FastAPI 的 query 参数签名一致），但这两个字段是完整脚本正文/自然语言查询，通常包含 SQL、表名、业务数据等敏感内容：它们会出现在服务端 access log、反向代理与网关日志、以及任何中间盒的 URL 记录中，属于敏感信息落到非受控存储。

此外 query string 受 URL 长度限制（常见网关 4-8KB），较大的代码会返回 414/431 而非可诊断错误。

建议后端把该端点改为 JSON body（`json={...}`）后同步修改客户端；若必须保持兼容，至少在文档中明示 `code` 不得含敏感数据，并对超长 code 做本地校验后给出明确报错。

### [E2-89] medium/bug
现码片段：
```
if resp.status_code in (429, 503) and retries > 0:
            time.sleep(min(_retry_after_seconds(resp), 5.0))
            return self._request(method, path, _retries=retries - 1, **kwargs)
```
重试对所有方法一视同仁：`create_session`、`execute`、`exec_command`、`terminate_session`、`rollback_snapshot`、`create_snapshot`、`trigger_compute_run`、`redeliver_webhook_delivery` 等非幂等 POST 在 429/503 时会被重放。503 常发生在请求已被上游处理但响应链路中断的场景，重放可能造成重复创建会话/重复执行任务/重复回滚。

建议只对幂等方法（GET/HEAD/PUT/DELETE）自动重试，非幂等 POST 需要调用方显式传 `_retries`（或由服务端提供 idempotency-key）才重试。

### [E2-90] medium/bug
现码片段：
```
if has_key and not api_key.startswith("cds_ak_"):
            raise CDSError(400, "api_key must start with 'cds_ak_'")
```
认证路径互斥校验不完整：`has_app` 为真时会拒绝 `token`/`api_key` 共存，但当调用方只传 `app_id`/`app_secret` 又同时传 `token` 时逻辑正确；反之只传 `api_key` 与 `app_id`（`app_secret` 缺失）会先在“exactly one auth path”处报错，错误语义误导。

更实质的问题是：`api_key` 分支的 `has_key` 与 `has_token` 都写入同一个 `Authorization: Bearer` 头，但客户端无法区分二者；`CDSError` 使用 400 状态码表示“构造期参数错误”，与它作为 HTTP 状态码的语义冲突，调用方按 HTTP 语义处理（例如把 400 当作可重试的协议错误）会误判。建议构造期校验改抛 `ValueError`。

### [E2-91] medium/bug
现码片段：
```
cursor: str | None = ""  # empty string = keyset page 1
        while True:
            body = self.list_sessions(status=status, cursor=cursor, limit=page_size)
            yield from body.get("items", [])
```
`body.get("items", [])` 假定返回体一定是 dict，但 `_request_with_headers` 在 204/空体时返回 `None`（`NoneType.get` 直接 AttributeError），且 `items` 缺失时静默 yield 空，调用方无法区分“没有数据”与“响应结构异常/认证失败被当成 200”。结合上面 `_request_with_headers` 不抛错的问题，认证失效时 `iter_sessions` 会“正常地”返回 0 条记录，属于静默数据丢失。

### [E2-92] medium/bug
现码片段：
```
if cursor is not None or (limit is not None and page == 1 and page_size == 20):
            params: dict[str, Any] = {"limit": limit if limit is not None else page_size}
```
这里用 `page == 1 and page_size == 20` 的默认值组合来推断调用方“想要 keyset 分页”，属于脆弱的隐式约定：调用方若显式写 `list_sessions(page=1, page_size=20, limit=50)` 的意图无法表达，而后端只接受 `skip/limit` 的偏移模式与 `cursor/limit` 的游标模式，一旦服务端对同时出现 `cursor` 与 `skip` 的行为变化，就会静默返回错误页。建议用显式参数（如 `mode="keyset"` 或 `cursor` 必传）区分两种分页，而不是靠默认值猜测。

### [E2-93] medium/bug
现码片段：
```
while time.monotonic() < deadline:
            last = self.get_session(session_id)
```
边界处理两处问题：
1. `timeout <= 0` 时循环体一次都不执行，直接抛 `TimeoutError`，而 `last` 为 `{}`，错误信息里 `last.get('status')` 恒为 `None`，对调用方毫无诊断价值（甚至从未发起过一次请求）。
2. 轮询过程中任何一次 `get_session` 抛错（429 重试耗尽、瞬时 5xx、网络抖动）都会让整体等待提前失败，而这类瞬时错误在轮询语义下本应继续重试到 deadline。

建议先做一次无条件请求，并捕获 `CDSError`/`httpx.TransportError` 在未到 deadline 时继续轮询。

### [E2-94] medium/bug
现码片段：
```
if (has_token + has_app + has_key) > 1:
            raise CDSError(
                400,
                "pass exactly one auth path: token, app_id/app_secret, "
                "or api_key — not more than one",
            )
```
重试计数语义被 `kwargs` 污染且重试会丢弃调用方的 `_retries` 语义之外还有泄漏风险：`retries = kwargs.pop("_retries", self._max_retries)` 意味着任何公开方法只要 kwargs 中带上名为 `_retries` 的键（`stream_exec`/`_stream` 内部调用 `_request` 时，或未来新增透传 `**kwargs` 的方法）就会静默改变重试次数。

另外 `__init__` 完全忽略了文档中声明的 `self._access_token` 刷新链路：`self._access_token` 在构造和 `_set_tokens` 中被赋值，但除 `_set_tokens` 内部外从未被任何请求逻辑读取（请求头由 `self._client.headers` 承载），属于死状态；`_set_tokens` 在 `api_key`/`app_id` 认证模式下被调用时还会把 `X-App-*` 之外的 `Authorization` 头覆盖成刷新得到的 JWT，破坏“三种认证路径互斥”的约定。建议在 `refresh_token` 入口拒绝非 token 模式，并移除或实际使用 `_access_token`。

### [E2-95] medium/bug
现码片段：
```
client = cls(base_url, body["access_token"], **kwargs)
        client._refresh_token = body.get("refresh_token")
        return client

    @classmethod
    def register(cls, base_url: str, username: str, email: str, password: str,
```
`login`/`register` 把 `**kwargs` 原样传给 `cls(...)`：调用方为探测请求传入的 `transport`/`timeout` 会被当作构造参数（这两个是合法构造参数，没问题），但任何额外键（如 `username`、`password` 误传）会变成 `TypeError`；更关键的是——若调用方在 kwargs 里带了 `headers`，`__init__` 中 `{**auth_headers, **(headers or {})}` 会让自定义 `headers` 覆盖刚拿到的 `Authorization: Bearer <access_token>`，客户端将带着无认证的头运行并在后续请求中收到 401，很难定位。

另外 `body["access_token"]` 直接下标访问，后端返回 200 但字段缺失/改名时抛 `KeyError`，建议改为显式校验并抛出带上下文的 `CDSApiError`。

### [E2-96] low/bug
现码片段：
```
while True:
            op = self.get_operation(operation_id)
            if op.get("status") in ("succeeded", "failed"):
```
两处问题：
1. `get_operation` 返回 `None`（204/空体）或 JSON 数组时，`op.get(...)` 抛 `AttributeError`。
2. 循环内未先把 `status` 落到局部变量并显式处理 `cancelled`/`timeout` 等其它终态（文件头已定义 `_TERMINAL_STATUSES` 含 `terminated`/`expired`/`completed`），一旦服务端返回这些终态，本方法会一直轮询到 timeout 才抛 `TimeoutError`，掩盖真实结果。

### [E2-97] low/bug
现码片段：
```
if resp.status_code >= 400:
            raise _parse_error(resp, f"/files/{filename}")
        return resp.content
```
`download_file` 绕过 `_request` 后未复用重试逻辑，也未校验 204/空体，行为与其它方法不一致：429/503 时直接抛错（而文档承诺 429/503 会重试），调用方无法通过 `max_retries` 得到一致体验。`download_dataset_bundle` 同样如此。建议抽取一个 `_request_bytes` 复用重试与非 2xx 解析。

### [E2-98] low/maintainability
现码片段：
```
def _compact(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop unset (``None``) keys so PATCH/PUT bodies only carry what the caller passed."""
    return {key: value for key, value in fields.items() if value is not None}
```
`_compact` 一律丢弃 `None`，使“显式置空”在语义上不可表达：`update_openapi_endpoint(quota_total=None)`、`update_notebook(session_id=None)`、`update_contract_flow_config(auto_compute=None)` 这类调用无法把字段清空/取消，调用方只能猜“传 None 是没改还是清除”。建议增加 `unset` 哨兵或 `clear` 参数区分两种意图。

### [E2-99] low/bug
现码片段：
```
if timeout_seconds:
            params["timeout_seconds"] = timeout_seconds
        if purpose:
            params["purpose"] = purpose
```
`create_task` 用真值判断决定是否携带参数，`timeout_seconds=0` 会被静默丢弃（回落到服务端默认 3600）；`create_session` 里 `if resource_limits:` / `if idle_policy:` 同样无法表达空字典/空串。而同一文件里 `exec_command`、`refresh` 又正确使用 `is not None`，两种约定混用易导致调用方设的值不生效。

### [E2-100] low/style
现码片段：
```
self.code = code or (f"HTTP_{status_code}" if not isinstance(detail, dict) or "code" not in detail else str(detail["code"]))
```
该行过长且嵌套三元表达式难以阅读，且 `detail["code"]` 可能为 `None`，`str(None)` 会得到字符串 `"None"` 而不是回落到 `HTTP_{status_code}`。建议拆成显式分支并跳过 `None`/空串。

### [E2-101] low/bug
现码片段：
```
if isinstance(data, str):
            with open(data, "rb") as fh:
                payload = fh.read()
```
`upload_file` 把 `str` 一律当作本地路径读取，若调用方想上传字符串内容（这是常见用法）会抛 `FileNotFoundError`（且错误信息里带上可能被截断的内容，泄露路径信息）。建议显式区分：文本内容要求传 `bytes` 或加 `content=` 参数；同时 `data.read()` 返回 `str`（文本模式打开的文件对象）时应给出明确报错，而不是把 `str` 直接交给 httpx `files=`。

### [E2-102] low/bug
现码片段：
```
level = session.get("sandbox_level") if isinstance(session, dict) else None
        if level not in _stream.SUPPORTED_STREAM_LEVELS:
```
`get_session` 返回 `None`（空体）或字段缺失时，`level` 为 `None`，此处抛出的错误信息是 “this session is None”，与实际原因（会话不存在/响应异常）不符，排障成本高。建议先区分 “session 为空” 与 “level 不支持” 两种情况。

## sdk/cds_sdk/stream.py (5)
### [E2-103] high/bug
现码片段：
```
def _is_closed_error(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in ("WebSocketConnectionClosedException", "WebSocketTimeoutException"):
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))
```
`_is_closed_error` 把超时（`WebSocketTimeoutException` / `TimeoutError` / `socket.timeout`）和「连接已关闭」混为一谈，调用点在 `recv` 失败时一律 `return`，于是流被静默截断：调用方拿到一堆 stdout/stderr 帧却没有任何 `exit`/`blocked`/`error` 终端帧，无法区分「服务端正常关闭」「recv 超时、命令可能仍在运行」「连接被中断」。在本模块的安全语义下这属于 fail-open：DLP 的 `blocked` 帧可能恰好在超时之后才到达，被当作正常结束吞掉，调用方按 docstring（"Iteration stops after a terminal frame or when the server closes the connection"）会误以为拿到了完整输出。建议区分两类异常，超时抛 `CDSError(504, ...)`（或至少 yield 一个显式的 `type="timeout"` 帧），不要静默 return。
建议：
```
def _is_closed_error(exc: Exception) -> bool:
    return type(exc).__name__ == "WebSocketConnectionClosedException" or isinstance(
        exc, ConnectionError
    )


def _is_timeout_error(exc: Exception) -> bool:
    return type(exc).__name__ == "WebSocketTimeoutException" or isinstance(
        exc, TimeoutError
    )
```

### [E2-104] high/security
现码片段：
```
path = (
        f"/api/v1/sandbox-sessions/{session_id}/exec/stream"
        f"?ticket={ticket}"
    )
```
`session_id` 与一次性凭证 `ticket` 直接字符串拼进 WebSocket URL，未做百分号编码。任一值含 `?`/`&`/`#`/空格等字符时 URL 会被破坏：`#` 会截断 ticket（服务端鉴权失败），`&`/`?` 则会让 `session_id` 向查询串注入额外参数（例如 `session_id = "x?ticket=y"`），造成参数走私或指向非预期会话。另外 ticket 是 30s 单用凭证，出现在查询串中会进入网关/代理访问日志与服务端 URL 日志，需确认脱敏；若 W10 契约允许，优先改用首帧或 `Sec-WebSocket-Protocol` 传递。这里至少要用 `urllib.parse.quote(..., safe='')` 编码两段。
建议：
```
path = (
        f"/api/v1/sandbox-sessions/{quote(str(session_id), safe='')}/exec/stream"
        f"?ticket={quote(str(ticket), safe='')}"
    )
```

### [E2-105] medium/bug
现码片段：
```
def _default_ws_open(url: str, timeout: float):
    ws = _import_websocket().WebSocket()
    ws.connect(url)
    ws.settimeout(timeout)
    return ws
```
`timeout` 只在握手成功后通过 `settimeout` 生效，`ws.connect(url)` 没有接收超时参数。websocket-client 的 `WebSocket.connect()` 支持 `timeout=`（用于 socket 建连/握手阶段）与 `handshake_timeout=`；不传时若对端 SYN 不回或握手挂起，`open_ws` 会长时间（甚至到 OS 级 TCP 超时）阻塞，`recv_timeout` 完全不起作用，也和 `CDSClient(timeout=...)` 的 HTTP 超时语义不一致。
建议：
```
def _default_ws_open(url: str, timeout: float):
    ws = _import_websocket().WebSocket()
    ws.connect(url, timeout=timeout, handshake_timeout=timeout)
    ws.settimeout(timeout)
    return ws
```

### [E2-106] medium/bug
现码片段：
```
ws.send(json.dumps(frame))
```
`connect` 与 `recv` 的底层异常都被包装成 `CDSError`，但 `ws.send` 裸在外面：握手成功后立即断连时，websocket-client 的 `WebSocketConnectionClosedException` / `socket.error` 会直接抛给调用方，破坏「只抛 `CDSError`」的错误契约（`client.py` 与文档都按 `CDSError`/`CDSApiError` 约定错误处理）。请与 recv 一致地包装为 `CDSError(502, ...)`。
建议：
```
try:
            ws.send(json.dumps(frame))
        except Exception as exc:
            raise CDSError(502, f"websocket send failed: {exc}") from exc
```

### [E2-107] low/bug
现码片段：
```
if timeout_seconds is not None:
            frame["timeout"] = int(timeout_seconds)
```
`int(timeout_seconds)` 直接转换外部入参：传 `"30s"`、`30.5.5` 之类会抛裸 `ValueError`（`TypeError` for 非数字类型），与本函数其余入参校验统一抛 `CDSError(400)` 的风格不一致（`_build_command` 对空 code 就是这么处理的）。另外负值/0 未校验，会被原样发给服务端，错误暴露得晚且难定位。建议在此显式校验并抛 `CDSError(400, ...)`。
建议：
```
if timeout_seconds is not None:
            try:
                timeout_value = int(timeout_seconds)
            except (TypeError, ValueError) as exc:
                raise CDSError(400, "timeout_seconds must be an integer") from exc
            if timeout_value <= 0:
                raise CDSError(400, "timeout_seconds must be > 0")
            frame["timeout"] = timeout_value
```
