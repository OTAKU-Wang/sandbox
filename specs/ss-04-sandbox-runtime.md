# SS-04：沙箱运行时 分系统技术规格

> 版本：v1.0 | 分系统编号：SS-04  
> 上游：SS-01（密钥）、SS-02（策略包）、SS-03（任务调度）  
> 下游：SS-05（输出审查）、SS-06（审计）

---

## 1. 职责边界与运行时分类

### 1.1 全局职责

| 职责 | 说明 |
|------|------|
| 沙箱生命周期管理 | 创建→初始化→就绪→任务执行→销毁，全程密态 |
| 多数据类型支持 | 结构化/非结构化/半结构化数据在沙箱内的安全加载与计算 |
| 多场景支持 | 查询分析/数据建模/安全应用/AI训练/数据产品开发 六大场景 |
| 数据库安全存储 | 结构化数据的沙箱内加密数据库（基于 PostgreSQL/DuckDB） |
| 大模型训练支持 | LLM/视觉/多模态模型在 GPU-TEE 内安全训练 |
| 内存安全管理 | TEE 内数据全程不落盘；内存解密仅在 Enclave EPC 内 |
| 策略执行代理 | 在 TEE 内托管 SS-02 策略执行引擎（PDP） |

### 1.2 运行时架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      沙箱运行时（Sandbox Runtime）                        │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    场景路由层 (Scene Router)                        │  │
│  │  structured_query │ data_modeling │ structured_app │ product_dev   │  │
│  │  llm_sft │ llm_pretrain │ vision_train │ multimodal │ semi_etl     │  │
│  └───────────────────────┬───────────────────────────────────────────┘  │
│                          │ 按场景实例化对应运行时                         │
│  ┌───────────┐  ┌────────▼──────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ SQL引擎   │  │ Python执行器  │  │ GPU训练运行时 │  │ 应用容器运行时│  │
│  │(DuckDB/PG)│  │(CPython/LibOS)│  │(PyTorch+NCCL)│  │(隔离进程组)  │  │
│  └───────────┘  └───────────────┘  └──────────────┘  └──────────────┘  │
│                          │                                              │
│  ┌───────────────────────▼───────────────────────────────────────────┐  │
│  │              安全数据访问层 (Secure Data Access Layer)              │  │
│  │   结构化DB解密  │ 对象存储解密 │ 流式批次加载  │ 数据库内安全存储    │  │
│  └───────────────────────┬───────────────────────────────────────────┘  │
│                          │                                              │
│  ┌───────────────────────▼───────────────────────────────────────────┐  │
│  │             TEE 隔离基础层 (TEE Isolation Base)                    │  │
│  │   Occlum LibOS(SGX) │ AMD SEV-SNP │ 华为iTrustee │ Firecracker(L2)│  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 沙箱初始化流程（通用）

```python
class SandboxRuntime:

    async def initialize(self, session: SandboxSession) -> SandboxHandle:
        """
        沙箱初始化：从节点分配到就绪的完整流程
        预期耗时：L1 TEE ≤60s；L2 软件 ≤15s
        """
        handle = SandboxHandle(session_id=session.session_id)

        # ── Phase 1：TEE/隔离环境启动 ──────────────────────────────
        if session.sandbox_level == "L1":
            await self._init_tee_enclave(handle, session)
        elif session.sandbox_level == "L2":
            await self._init_firecracker_vm(handle, session)
        else:
            await self._init_container(handle, session)

        # ── Phase 2：策略包加载 ────────────────────────────────────
        bundle = policy_service.get_bundle(
            contract_id=session.contract_id,
            sandbox_mode=session.sandbox_mode,
        )
        # 策略包发送至 TEE 内，由 TEE 验证 SM3 哈希后加载
        await handle.load_policy_bundle(bundle)

        # ── Phase 3：密钥接收 ──────────────────────────────────────
        dist = kms_service.distribute_key(
            contract_id=session.contract_id,
            session_id=session.session_id,
            tee_quote=handle.get_attestation_quote(),       # TEE 提供证明报告
            node_cert_pem=handle.node_cert_pem,
        )
        await handle.receive_key(dist)  # TEE 内解密，明文仅存 EPC 内存

        # ── Phase 4：数据加载（场景相关）─────────────────────────────
        data_loader = DataLoaderFactory.create(session.sandbox_mode)
        await data_loader.load(handle, session)

        # ── Phase 5：场景运行时初始化 ──────────────────────────────
        scene_runtime = SceneRuntimeFactory.create(session.sandbox_mode)
        await scene_runtime.setup(handle, session)

        handle.status = SandboxStatus.READY
        db.update(SandboxSession, session.session_id, status="ready",
                  ready_at=datetime.utcnow())
        return handle
```

---

> **场景运行时交付分期**：
>
> | 期 | 运行时 | 状态 |
> |----|--------|------|
> | P1（核心） | StructuredQueryRuntime, DataModelingRuntime, ProductDevRuntime | 已实现（DuckDB + SecureDuckDBEngine） |
> | P1（核心） | StructuredAppRuntime | 部分实现（DB access proxy） |
> | P2（LLM） | LLMSFTRuntime, LLMPretrainRuntime | Stub（模拟训练） |
> | P2（高级） | VisionModelTrainingRuntime, MultimodalTrainingRuntime | Stub（apply() 直通） |
> | P2（高级） | SemiStructuredRuntime | 已实现（JSONL Schema 推断） |
> | P3（远期） | FederatedRuntime | 未实现 |

## 3. 结构化数据场景

### 3.1 沙箱内加密数据库（核心设计）

结构化数据的核心安全基础设施是**沙箱内加密数据库**。数据永远不以明文形式出现在 TEE Enclave 之外。

```
数据存储架构（结构化场景）：

  外部存储（加密）              TEE 内部（明文）            任务产出
  ─────────────────         ────────────────────        ──────────────
  MinIO 对象存储             沙箱内 DuckDB / PGlite       SQL 查询结果
  ・Parquet 文件             ・内存表（全量小数据集）         ↓
    SM4-GCM 加密             ・mmap 表（大数据集分块）    输出审查网关
  ・CSV 文件                 ・临时物化视图                  ↓
    SM4-GCM 加密             ・字段级脱敏视图（自动）      合规结果→买方

  加密数据库文件（可选）
  ・SQLite WAL 文件
    AES-256 全库加密
    （数商开发沙箱使用）
```

#### 3.1.1 加密数据库存储方案（按场景选型）

| 场景 | 数据库引擎 | 加密方案 | 适用理由 |
|------|-----------|----------|----------|
| 数据查询/分析 | DuckDB（嵌入式列存） | 全内存，不落盘；TEE 内存 = 加密介质 | 高性能列存查询，无持久化风险 |
| 数据建模/特征工程 | DuckDB + tmpfs 落盘 | dm-crypt 加密 tmpfs（L1 由 TEE MEK 保护） | 需要写中间表，支持 CREATE TABLE |
| 沙箱内应用（DB访问） | PostgreSQL-in-TEE | WAL 加密（pgcrypto SM4）+ 内存加密 | 应用程序依赖完整 PG 功能 |
| 数商开发沙箱 | DuckDB + 持久加密 DB | SM4 全库加密（开发期间持久保存工作状态）| 数商需要跨会话保留开发进度 |
| 大规模模型训练数据 | Arrow 内存格式 | 分批解密加载，用后即销毁 | 无需 SQL 接口，纯批次 DataLoader |

#### 3.1.2 DuckDB 加密集成

```python
class SecureDuckDBEngine:
    """
    TEE 内的 DuckDB 实例，对外提供安全 SQL 执行接口
    所有数据在 TEE 内存中以明文存在；TEE 外以密文存在
    """

    def __init__(self, session: SandboxSession, dek: bytes):
        # 数据库文件路径：tmpfs（L2/L3 用 dm-crypt 挂载的加密 tmpfs）
        if session.sandbox_level == "L1":
            # SGX: 所有内存自动受 EPC 保护，直接用内存数据库
            self.db = duckdb.connect(":memory:")
        else:
            # L2: 使用加密 tmpfs
            db_path = f"/mnt/encrypted_tmpfs/{session.session_id}/sandbox.db"
            self.db = duckdb.connect(db_path)

        self.dek = dek                      # 用于解密外部数据，仅在 TEE 内存中
        self.policy_engine = PolicyDecisionPoint.get_instance()
        self.field_masks = {}               # 字段级脱敏规则缓存

    def load_product_data(self, product: DataProduct, schema: DataSchema):
        """
        从加密对象存储加载数据产品，解密后注册为 DuckDB 视图
        """
        # 1. 分块下载并解密
        chunks = []
        for chunk_ref in product.chunk_refs:
            encrypted = storage.get_object(chunk_ref.path)
            # SM4-GCM 解密（dek 仅在 TEE 内存中存在）
            plaintext = sm4_gcm_decrypt(encrypted, self.dek,
                                        iv=chunk_ref.iv, tag=chunk_ref.auth_tag)
            # SM3 完整性校验
            assert sm3_hash(plaintext).hex() == chunk_ref.hash_sm3, "Chunk integrity check failed"
            chunks.append(pa.deserialize(plaintext))   # Arrow 格式

        # 2. 注册为内存表
        table = pa.concat_tables(chunks)
        self.db.register(product.product_id, table)

        # 3. 自动创建脱敏视图（高敏感字段自动替换）
        self._create_masked_view(product.product_id, schema)

    def _create_masked_view(self, table_name: str, schema: DataSchema):
        """
        根据策略包中的 field_acl，自动创建脱敏视图
        买方代码只能访问视图，不能直接访问原始表
        """
        field_exprs = []
        for field in schema.fields:
            acl = self.policy_engine.get_field_acl(field.name)
            if acl.mask_pattern == "HASH":
                # 单向哈希（保留关联性，不暴露原值）
                field_exprs.append(f"md5(CAST({field.name} AS VARCHAR)) AS {field.name}")
            elif acl.mask_pattern == "REDACT":
                # 完全遮盖
                field_exprs.append(f"NULL AS {field.name}")
            elif acl.mask_pattern == "GENERALIZE_TOP_K":
                # 泛化：仅保留 top-K 取值，其余归为 'Other'
                field_exprs.append(
                    f"CASE WHEN {field.name} IN "
                    f"(SELECT {field.name} FROM {table_name} "
                    f" GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT {acl.top_k}) "
                    f"THEN {field.name} ELSE 'Other' END AS {field.name}"
                )
            elif acl.aggregate_only:
                # 聚合模式：在视图中不暴露原始值（买方只能 GROUP BY / COUNT 此字段）
                # 通过视图安全限制实现：原始值可查，但输出审查网关会拒绝行级导出
                field_exprs.append(f"{field.name}")
            else:
                field_exprs.append(f"{field.name}")

        view_sql = f"""
            CREATE OR REPLACE VIEW {table_name}_secure AS
            SELECT {', '.join(field_exprs)}
            FROM {table_name}
        """
        self.db.execute(view_sql)
        # 原始表权限撤销（买方代码只能访问 _secure 视图）
        self._restrict_table_access(table_name)

    def execute_query(self, sql: str, session_id: str) -> QueryResult:
        """
        执行买方 SQL（策略检查 → 执行 → 结果返回输出审查）
        """
        # 1. 策略评估
        req = PolicyRequest(
            action_type="SQL_EXECUTE",
            sql=sql,
            session_id=session_id,
            accessed_fields=self._extract_accessed_fields(sql),
        )
        decision = self.policy_engine.evaluate(req)
        if not decision.allowed:
            raise PolicyDenied(decision.reasons)

        # 2. 强制重写 SQL：将直接表名引用替换为安全视图名
        rewritten_sql = self._rewrite_to_secure_views(sql)

        # 3. 执行（超时保护）
        result = execute_with_timeout(
            lambda: self.db.execute(rewritten_sql).fetchdf(),
            timeout_s=self.policy_engine.contract.session_max_runtime_s,
        )

        # 4. 发送至输出审查网关
        return output_gateway.inspect_and_release(result, session_id)
```

#### 3.1.3 PostgreSQL-in-TEE（应用场景）

沙箱内应用（STRUCTURED_APP 模式）需要完整的 PostgreSQL 能力。PG 实例运行于 TEE 内，WAL 和数据文件使用 pgcrypto 扩展的 SM4 加密。

```
PG-in-TEE 部署拓扑：

  应用进程（TEE 内）
      │ libpq（Unix Socket）
      ▼
  PostgreSQL Server（TEE 内，端口仅 localhost）
      │
  ┌───▼────────────────────────────────────────┐
  │  pgcrypto 扩展（SM4 字段加密）               │
  │  pg_tde 扩展（透明数据加密，表空间级）        │
  │  行级安全策略（RLS）自动注入                  │
  └───────────────────────────────────────────┘
      │
  ┌───▼────────────────────────────────────────┐
  │  加密 tmpfs（L1: EPC 保护；L2: dm-crypt）   │
  │  - WAL 文件（SM4 加密）                     │
  │  - 数据文件（pg_tde 透明加密）               │
  └───────────────────────────────────────────┘
```

```sql
-- PG-in-TEE 初始化脚本（沙箱启动时自动执行）

-- 1. 启用透明数据加密（pg_tde 扩展）
CREATE EXTENSION IF NOT EXISTS pg_tde;
SELECT pg_tde_add_key_provider_sm4(
    'sandbox_sm4_provider',
    -- DEK 以 hex 形式从 TEE 内存传入（不从文件读取）
    current_setting('sandbox.dek_hex')
);
SELECT pg_tde_set_default_key('sandbox_key', 'sandbox_sm4_provider');

-- 2. 创建数据产品 Schema（只读视图）
CREATE SCHEMA IF NOT EXISTS data_products;

-- 3. 行级安全：防止应用代码访问超出合约授权的行
ALTER TABLE data_products.customer_records ENABLE ROW LEVEL SECURITY;
CREATE POLICY contract_filter ON data_products.customer_records
    USING (region_code = current_setting('sandbox.allowed_region'));

-- 4. 数据写入权限控制：应用只能写入 sandbox.results schema
CREATE SCHEMA IF NOT EXISTS sandbox_results;
GRANT SELECT ON ALL TABLES IN SCHEMA data_products TO sandbox_app_user;
GRANT ALL ON SCHEMA sandbox_results TO sandbox_app_user;
REVOKE ALL ON SCHEMA public FROM sandbox_app_user;
```

### 3.2 数据建模沙箱（STRUCTURED_MODELING）

数据建模场景支持特征工程、统计分析、机器学习模型训练（结构化数据），买方可以在沙箱内保存中间结果（不可导出）。

```python
class DataModelingRuntime:
    """
    数据建模场景运行时
    支持：特征工程 / 统计分析 / 结构化数据 ML 训练
    特点：允许创建临时表/视图，但所有写操作限制在沙箱内部存储
    """

    def setup(self, handle: SandboxHandle, session: SandboxSession):
        # 初始化加密 DuckDB（支持落盘临时表）
        self.db = SecureDuckDBEngine(session, dek=handle.dek)

        # 加载所有授权数据产品
        for product_id in session.authorized_product_ids:
            product = catalog.get_product(product_id)
            self.db.load_product_data(product, product.schema)

        # 注册机器学习 UDF（在 DuckDB 中调用 sklearn/xgboost）
        self._register_ml_udfs()

        # 初始化沙箱内部存储（用于保存中间结果，不允许导出）
        self.internal_storage = SandboxInternalStorage(
            path=f"/sandbox/internal/{session.session_id}/",
            encrypted=True,
            dek=handle.dek,
        )

    def _register_ml_udfs(self):
        """注册常用 ML 操作为 DuckDB UDF，让买方可以在 SQL 中调用"""
        # 示例：在 SQL 中训练 XGBoost 模型
        self.db.create_function(
            "train_xgb_classifier",
            lambda df, label_col, feat_cols, params: self._train_xgb(df, label_col, feat_cols, params),
            return_type="JSON"  # 返回模型评估指标（不含模型权重）
        )
        self.db.create_function(
            "feature_importance",
            lambda model_ref: self._get_feature_importance(model_ref),
            return_type="STRUCT(feature VARCHAR, importance FLOAT)[]"
        )

    def execute_modeling_task(self, script: str, session_id: str):
        """
        执行 Python 建模脚本
        沙箱内 Python 进程可以：
          - 读取数据：通过 DuckDB 接口（受策略控制）
          - 训练模型：sklearn/xgboost/lightgbm
          - 保存中间结果：只能写入 /sandbox/internal/（不可导出）
          - 输出：只能通过输出网关，模型权重 + 评估指标
        """
        sandbox_env = {
            "db": self.db,
            "internal_storage": self.internal_storage,
            "__builtins__": RESTRICTED_BUILTINS,  # 移除 open/exec/eval 等
        }
        exec(compile(script, "<sandbox>", "exec"), sandbox_env)
```

### 3.3 沙箱内安全应用（STRUCTURED_APP）

**场景**：买方将自己的业务应用部署到沙箱内，应用可直接访问数据商数据，但应用本身被完全隔离。

```
应用沙箱架构：

  买方提交的应用包（Docker Image / Python 应用）
               ↓ 代码扫描通过
  ┌────────────────────────────────────────────────────────────────┐
  │                    应用隔离容器（TEE 内子进程组）                │
  │                                                               │
  │  应用进程（受限权限：无 root，无 CAP_NET_RAW 等）               │
  │      │                                                        │
  │      │ UNIX Socket（唯一数据访问通道）                          │
  │      ▼                                                        │
  │  数据代理（Data Proxy，TEE 内）                                │
  │  ・接收应用的数据请求                                           │
  │  ・策略校验（PDP）                                             │
  │  ・透过 PG-in-TEE 执行查询                                     │
  │  ・结果脱敏后返回应用                                           │
  │      │                                                        │
  │      ▼                                                        │
  │  PostgreSQL-in-TEE（数据源）                                   │
  │                                                               │
  │  应用输出通道（唯一出口）：                                      │
  │  ・HTTP 响应：经 SS-05 输出审查后转发给外部用户                  │
  │  ・禁止：直接网络访问、文件导出、数据库 dump                      │
  └────────────────────────────────────────────────────────────────┘
```

```python
class StructuredAppRuntime:

    def deploy_application(self, app_bundle: AppBundle, session: SandboxSession):
        """
        在沙箱内部署买方应用
        """
        # 1. 应用镜像签名验证
        verify_image_signature(app_bundle.image_digest, session.consumer_cert_pem)

        # 2. 启动受限子进程（cgroup 资源限制）
        cgroup = CGroupConfig(
            cpu_quota=session.allocated_cpu * 100000,    # microseconds/100ms
            memory_limit=f"{session.allocated_mem_gb}G",
            pids_limit=256,                              # 最大进程数
            net_cls=SANDBOX_NET_CLASS,                   # 流量打标，iptables 拦截外联
        )
        proc = launch_isolated_process(
            command=app_bundle.entrypoint,
            env={
                "DB_PROXY_SOCKET": "/run/sandbox/db_proxy.sock",
                "SANDBOX_SESSION_ID": session.session_id,
                # 禁止传入任何敏感环境变量
            },
            cgroup=cgroup,
            seccomp_profile=APP_SECCOMP_PROFILE,         # 严格系统调用白名单
        )

        # 3. 启动数据代理（应用唯一数据访问通道）
        data_proxy = DataProxy(
            socket_path="/run/sandbox/db_proxy.sock",
            pg_conn=self.pg_engine.connection,
            policy_engine=self.policy_engine,
        )
        data_proxy.start()

        # 4. 应用 HTTP 输出代理（拦截并审查应用的 HTTP 响应）
        output_proxy = AppOutputProxy(
            app_port=app_bundle.listen_port,
            output_gateway=output_gateway,
            session_id=session.session_id,
        )
        output_proxy.start()

        return AppHandle(proc=proc, data_proxy=data_proxy, output_proxy=output_proxy)
```

### 3.4 数商开发沙箱（PRODUCT_DEVELOPMENT）

**场景**：数商在密态沙箱内开发、测试、验证数据产品，可以看到自己数据的原始行（样本），但不能将数据以任何形式导出。开发完成后，导出的是"数据产品包"（仅含元数据、脱敏脚本、策略配置），不含数据本身。

```
开发沙箱特殊架构：

 数商开发者（通过 IDE 插件 or Web Terminal 连接）
              │ SSH/WebSocket（TLCP 加密）
              ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │               数商开发沙箱（PRODUCT_DEVELOPMENT）               │
 │                                                                │
 │  开发工作区（Jupyter-like 环境）                                │
 │  ┌──────────────────────────────────────────────────────────┐  │
 │  │ Cell 执行器（Python / SQL）                               │  │
 │  │ ・可读原始数据行（≤1000行 采样，可配置）                    │  │
 │  │ ・可执行数据探索、脚本开发、脱敏规则测试                    │  │
 │  │ ・不可将数据写入沙箱外任何位置                             │  │
 │  └──────────────────────────────────────────────────────────┘  │
 │                                                                │
 │  产品定义工作区                                                 │
 │  ┌──────────────────────────────────────────────────────────┐  │
 │  │ Schema 设计器（字段定义、敏感度标注、脱敏规则配置）         │  │
 │  │ 策略编辑器（使用控制策略、输出约束）                        │  │
 │  │ 质量检测器（数据完整性、分布统计、样本质量报告）             │  │
 │  │ 脱敏预览（在 TEE 内执行脱敏，验证效果）                    │  │
 │  └──────────────────────────────────────────────────────────┘  │
 │                                                                │
 │  唯一出口（数据产品包，不含数据内容）：                          │
 │  ・product_manifest.json（元数据、Schema 定义）                 │
 │  ・masking_scripts/（脱敏脚本，不含示例数据）                   │
 │  ・policy_bundle.enc（加密策略包）                              │
 │  ・quality_report.pdf（数据质量报告，仅统计数字，无行级数据）    │
 └─────────────────────────────────────────────────────────────────┘
```

```python
class ProductDevRuntime:
    """
    数商开发沙箱运行时
    特殊权限：数商可读原始行数据（开发调试用）
    特殊限制：任何数据内容都不可导出；只能导出产品包
    """

    MAX_SAMPLE_ROWS = 1000          # 开发模式最大采样行数

    def setup(self, handle: SandboxHandle, session: SandboxSession):
        # 开发沙箱：数商必须是产品 owner，使用特殊策略包
        assert session.sandbox_mode == SandboxMode.PRODUCT_DEVELOPMENT
        assert session.consumer_org_id == session.provider_org_id  # 数商自访问

        self.db = SecureDuckDBEngine(session, dek=handle.dek)
        self.db.set_dev_mode(max_sample_rows=self.MAX_SAMPLE_ROWS)

        # 加载原始数据（开发模式：不自动脱敏，数商可见原始行）
        for product_id in session.authorized_product_ids:
            product = catalog.get_product(product_id)
            self.db.load_product_data_raw(product, apply_masking=False)

        # 初始化产品定义工作区
        self.product_workspace = ProductWorkspace(
            session_id=session.session_id,
            storage=SandboxInternalStorage(session),
        )

    def execute_dev_cell(self, code: str, language: str) -> CellOutput:
        """执行开发 Cell（Jupyter-like）"""
        output = []

        if language == "python":
            # 受限 Python：允许 pandas/duckdb/numpy，禁止文件 IO
            result = self._exec_python(code)
            output.append(CellOutput(type="data_frame", content=result.head(self.MAX_SAMPLE_ROWS)))
        elif language == "sql":
            result = self.db.execute_dev_query(code, row_limit=self.MAX_SAMPLE_ROWS)
            output.append(CellOutput(type="data_frame", content=result))

        # 所有 Cell 输出都流经开发模式输出过滤器
        # 注意：开发沙箱的输出过滤比正式场景宽松（允许展示行数据），
        # 但仍然防止数据被持久化到沙箱外
        return dev_output_filter.filter(output, session_id=self.session.session_id)

    def define_masking_rule(self, field_name: str, rule: MaskingRule) -> PreviewResult:
        """测试脱敏规则效果（在 TEE 内执行，仅返回统计摘要）"""
        sample = self.db.sample(field_name, n=100)
        masked = [rule.apply(v) for v in sample]
        return PreviewResult(
            original_cardinality=len(set(sample)),
            masked_cardinality=len(set(masked)),
            null_rate_after=masked.count(None) / len(masked),
            sample_masked=masked[:5],           # 仅展示5个脱敏示例
        )

    def export_product_package(self) -> ProductPackage:
        """
        导出数据产品包（唯一合法导出操作）
        严格检查：包内不能含任何数据行
        """
        package = self.product_workspace.build_package()

        # 最终检查：确保包内无数据内容
        for item in package.items:
            if item.type == "data":
                raise DataLeakError(f"Product package must not contain data: {item.name}")

        # SM4 加密打包
        encrypted_pkg = sm4_gcm_encrypt(package.serialize(), self.dev_export_key)
        audit_service.record("product_package_exported", self.session.session_id)
        return encrypted_pkg
```

---

## 4. 非结构化数据 — AI 大模型训练场景

### 4.1 GPU-TEE 训练环境架构

大模型训练需要 GPU 加速。NVIDIA H100/H800 支持机密计算（Confidential Computing，CC）模式，GPU 内存受 HW 加密保护，CPU-TEE 与 GPU-TEE 通过加密通道通信。

```
GPU-TEE 训练架构：

  训练数据（加密对象存储）
         │ SM4 解密（CPU-TEE 内）
         ▼
  CPU-TEE（Occlum/SGX 或 AMD SEV）
  ┌──────────────────────────────────────────────────────────────┐
  │  DataLoader（批次生成）                                        │
  │  ・解密数据块 → 预处理（Tokenize/Augment）→ 批次张量          │
  │  ・批次在 CPU-TEE 内明文存在                                  │
  │  ・通过加密 PCIe 通道传输至 GPU                               │
  └────────────────────────────┬─────────────────────────────────┘
                               │ NVIDIA CC 加密通道
                               ▼
  GPU-TEE（NVIDIA H100 CC 模式）
  ┌──────────────────────────────────────────────────────────────┐
  │  ・显存全程加密（HW-based Memory Encryption）                 │
  │  ・前向传播 / 反向传播 / 梯度更新 全在 GPU 内完成             │
  │  ・DP 裁剪在 GPU 内执行（联邦场景）                           │
  │  ・模型权重在显存中不可被 Host 读取                           │
  └────────────────────────────┬─────────────────────────────────┘
                               │ 模型 checkpoint（加密）
                               ▼
  训练完成 → 模型权重加密存储至沙箱内部 → SS-05 审查 → 导出
```

#### 4.1.1 GPU 节点初始化（CC 模式）

```python
class GPUTEERuntime:
    """
    NVIDIA H100 机密计算模式训练运行时
    """

    def initialize_gpu_cc(self, session: SandboxSession):
        # 1. 验证 GPU CC 模式激活
        gpu_info = nvidia_cc.get_device_info()
        assert gpu_info.cc_mode_enabled, "GPU Confidential Computing mode not enabled"
        assert gpu_info.device_attestation_valid, "GPU attestation failed"

        # 2. 建立 CPU-TEE ↔ GPU-TEE 加密通道
        # GPU 产生证明报告（包含 GPU 固件哈希）
        gpu_quote = nvidia_cc.get_gpu_attestation_report()
        # KMS 验证 GPU quote（防止伪造 GPU 接入）
        kms_service.verify_gpu_attestation(gpu_quote)

        # 3. 协商 CPU-GPU 通道加密密钥（仅用于批次数据传输）
        channel_key = derive_channel_key(
            cpu_tee_key=self.enclave_key,
            gpu_measurement=gpu_quote.firmware_hash,
        )
        nvidia_cc.set_channel_key(channel_key)

        self.gpu_handle = GPUHandle(device_id=gpu_info.device_id, channel_key=channel_key)

    def run_training_epoch(
        self,
        dataloader: SecureDataLoader,
        model_config: ModelConfig,
        optimizer_config: OptimizerConfig,
    ) -> EpochMetrics:
        model = self._load_or_init_model(model_config)
        optimizer = build_optimizer(model, optimizer_config)
        metrics = EpochMetrics()

        for batch_idx, (inputs, labels) in enumerate(dataloader):
            # batch 在 CPU-TEE 内明文 → 加密传至 GPU-TEE
            gpu_inputs = self.gpu_handle.send_encrypted(inputs)
            gpu_labels = self.gpu_handle.send_encrypted(labels)

            # 前向 + 反向传播（全在 GPU-TEE 显存中）
            loss = model.forward_backward(gpu_inputs, gpu_labels)

            # 策略检查：是否允许继续训练（配额/时间检查）
            if not self.policy_engine.check_training_step(batch_idx):
                break

            optimizer.step()
            optimizer.zero_grad()
            metrics.update(loss.item())

        return metrics
```

### 4.2 LLM 监督微调（SFT）实现

```python
class LLMSFTRuntime:
    """
    大语言模型监督微调（Supervised Fine-Tuning）
    支持：全参数微调 / LoRA / QLoRA
    数据：文本语料（对话/指令/知识，经脱敏）
    """

    ALLOWED_BASE_MODELS = {
        "Qwen2.5-7B", "Qwen2.5-14B", "Qwen2.5-72B",
        "Llama-3.1-8B", "Llama-3.1-70B",
        "InternLM2.5-7B", "InternLM2.5-20B",
        "Baichuan2-7B", "Baichuan2-13B",
    }

    def setup(self, handle: SandboxHandle, session: SandboxSession):
        self.gpu_runtime = GPUTEERuntime()
        self.gpu_runtime.initialize_gpu_cc(session)

        # 加载基座模型（从数空间内部模型仓库，不允许访问外网 HuggingFace）
        model_name = session.training_config["base_model"]
        assert model_name in self.ALLOWED_BASE_MODELS
        self.model = load_model_from_internal_registry(model_name)

        # 初始化安全数据加载器
        self.dataloader = SecureTextDataLoader(
            product_ids=session.authorized_product_ids,
            dek=handle.dek,
            tokenizer_name=model_name,
            batch_size=session.training_config.get("batch_size", 8),
            max_seq_len=session.training_config.get("max_seq_len", 2048),
            apply_text_masking=True,   # 训练数据批次级 PII 自动脱敏
        )

    def run_sft(self, training_config: SFTConfig) -> SFTResult:
        """
        执行 SFT 训练
        """
        # LoRA 配置（推荐：减少显存占用，降低记忆风险）
        if training_config.use_lora:
            from peft import get_peft_model, LoraConfig
            peft_config = LoraConfig(
                r=training_config.lora_r,               # 秩，建议 8~64
                lora_alpha=training_config.lora_alpha,
                target_modules=training_config.target_modules,
                lora_dropout=0.1,
                bias="none",
            )
            self.model = get_peft_model(self.model, peft_config)

        trainer = SecureSFTTrainer(
            model=self.model,
            dataloader=self.dataloader,
            gpu_runtime=self.gpu_runtime,
            policy_engine=self.policy_engine,
            dp_config=training_config.dp_config,        # 差分隐私（可选）
            checkpoint_dir="/sandbox/checkpoints/",     # 沙箱内加密存储
        )

        # 训练循环
        for epoch in range(training_config.num_epochs):
            metrics = trainer.train_epoch(epoch)
            # 每个 epoch 后：检查是否需要中间验证 + 记录审计
            audit_service.record_training_progress(
                session_id=self.session.session_id,
                epoch=epoch,
                loss=metrics.loss,
                samples_seen=metrics.samples_seen,
            )

        # 训练完成：模型记忆性检测（防止模型记住训练数据）
        memorization_score = self._run_memorization_probe()
        if memorization_score > training_config.max_memorization_score:
            raise MemorizationRiskError(
                f"Model memorization score {memorization_score:.3f} exceeds "
                f"threshold {training_config.max_memorization_score}"
            )

        # 注入模型水印（不可见，可验真）
        self._inject_model_watermark()

        return SFTResult(
            model_path="/sandbox/output/model/",
            metrics=trainer.get_final_metrics(),
            memorization_score=memorization_score,
        )

    def _run_memorization_probe(self) -> float:
        """
        Membership Inference Attack (MIA) 探针
        检测模型是否过度记忆训练数据
        使用 shadow model 方法估算记忆风险分数
        """
        mia_probe = MIAProbe(
            model=self.model,
            member_samples=self.dataloader.get_sample(n=200),     # 训练集样本
            non_member_samples=self.dataloader.get_holdout(n=200), # 留出集样本
        )
        return mia_probe.compute_advantage()   # 0~1，越低越安全

    def _inject_model_watermark(self):
        """
        在模型权重中注入不可见溯源水印
        使用 EmbMarker 或 Radioactive Data 方案
        水印信息：session_id + contract_id + timestamp（SM3 哈希后12字节）
        """
        from model_watermark import WeightWatermarker
        watermarker = WeightWatermarker(secret_key=self.session.watermark_key)
        watermarker.embed(self.model, payload=self.session.session_id[:12])
```

### 4.3 视觉模型训练（VISION_TRAIN）

```python
class VisionModelTrainingRuntime:
    """
    视觉模型训练沙箱运行时
    支持：图像分类 / 目标检测 / 分割 / 医疗影像分析
    特殊处理：图像数据脱敏（人脸/敏感区域）后才进入训练批次
    """

    def setup(self, handle: SandboxHandle, session: SandboxSession):
        self.gpu_runtime = GPUTEERuntime()
        self.gpu_runtime.initialize_gpu_cc(session)

        # 视觉数据预处理流水线（在 CPU-TEE 内，图像解密 + 脱敏 + 增强）
        self.dataloader = SecureImageDataLoader(
            product_ids=session.authorized_product_ids,
            dek=handle.dek,
            preprocessing_pipeline=self._build_preprocessing_pipeline(session),
            batch_size=session.training_config.get("batch_size", 32),
        )

    def _build_preprocessing_pipeline(self, session: SandboxSession):
        """
        根据数据产品配置构建图像预处理流水线
        所有预处理在 CPU-TEE 内执行，处理后才加密发往 GPU
        """
        pipeline_steps = []

        # 1. 解码（JPEG/PNG/DICOM）
        pipeline_steps.append(ImageDecodeStep())

        # 2. 自动脱敏（按数据产品配置）
        redaction_rules = session.product_redaction_rules
        if redaction_rules.get("face_blur"):
            pipeline_steps.append(FaceDetectAndBlurStep(
                model="retinaface_mobilenet",    # 轻量级，适合批量处理
                blur_method="gaussian_k51",
            ))
        if redaction_rules.get("dicom_strip"):
            pipeline_steps.append(DICOMTagStripStep(tags=SENSITIVE_DICOM_TAGS))
        if redaction_rules.get("license_plate_mosaic"):
            pipeline_steps.append(LicensePlateStep(method="mosaic"))

        # 3. 标准图像增强（数据增强，不含敏感内容）
        pipeline_steps.extend([
            RandomHorizontalFlipStep(p=0.5),
            RandomResizeCropStep(size=224),
            NormalizeStep(mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225]),
        ])

        return Pipeline(pipeline_steps)

    def run_training(self, config: VisionTrainingConfig) -> TrainingResult:
        model = build_vision_model(config.architecture, config.num_classes)
        trainer = SecureVisionTrainer(
            model=model,
            dataloader=self.dataloader,
            gpu_runtime=self.gpu_runtime,
            loss_fn=config.loss_fn,
            optimizer=config.optimizer,
        )

        for epoch in range(config.num_epochs):
            train_metrics = trainer.train_epoch()
            val_metrics = trainer.validate_epoch()

            # 定期 checkpoint（加密保存至沙箱内部存储）
            if epoch % config.checkpoint_every == 0:
                trainer.save_checkpoint(f"/sandbox/checkpoints/epoch_{epoch}/")

        # 训练完成后：标准评估指标（Accuracy/mAP/AUC）由审查网关决定是否可导出
        final_metrics = trainer.get_evaluation_metrics()

        # 模型水印注入
        self._inject_model_watermark()

        return TrainingResult(
            model_path="/sandbox/output/vision_model/",
            metrics=final_metrics,
        )
```

### 4.4 多模态模型训练（MULTIMODAL_TRAIN）

```python
class MultimodalTrainingRuntime:
    """
    多模态训练支持：图文对齐 / VQA / 图文生成
    代表模型：InternVL2 / LLaVA / Qwen-VL / CogVLM
    数据类型：图像 + 文本（对话/描述）配对数据集
    """

    ALLOWED_MULTIMODAL_MODELS = {
        "InternVL2-8B", "InternVL2-26B",
        "Qwen2-VL-7B", "Qwen2-VL-72B",
        "CogVLM2", "MiniCPM-V-2",
    }

    def setup(self, handle: SandboxHandle, session: SandboxSession):
        self.gpu_runtime = GPUTEERuntime()
        self.gpu_runtime.initialize_gpu_cc(session)

        # 混合数据加载器：图像 + 文本同时加载
        self.dataloader = SecureMultimodalDataLoader(
            image_product_ids=session.training_config["image_product_ids"],
            text_product_ids=session.training_config["text_product_ids"],
            dek=handle.dek,
            image_pipeline=self._build_image_pipeline(session),
            text_tokenizer=session.training_config["base_model"],
            align_strategy="pair",      # "pair" | "interleaved"
        )

    def run_multimodal_training(self, config: MultimodalTrainingConfig) -> TrainingResult:
        base_model_name = config.base_model
        assert base_model_name in self.ALLOWED_MULTIMODAL_MODELS

        # 加载预训练多模态基座（从内部模型仓库）
        model = load_multimodal_model(base_model_name)

        # 使用 LoRA 微调（视觉编码器 + 语言模型连接器）
        model = apply_lora_to_multimodal(model, config.lora_config)

        trainer = SecureMultimodalTrainer(
            model=model,
            dataloader=self.dataloader,
            gpu_runtime=self.gpu_runtime,
        )
        trainer.train(config)

        # 多模态模型特有安全检查：
        # 1. 图像重建攻击检测（防止通过文本输出反推图像）
        self._check_image_reconstruction_risk()
        # 2. 训练数据引用检测（防止模型在推理时复述训练文本）
        self._run_memorization_probe_multimodal()

        return TrainingResult(model_path="/sandbox/output/multimodal/")

    def _check_image_reconstruction_risk(self):
        """
        检测：在给定特定 prompt 时，模型是否能生成接近训练图像的输出
        方法：使用 CLIP 相似度检测模型输出与训练图像的视觉相似性
        """
        test_prompts = self.dataloader.get_image_description_pairs(n=50)
        max_similarity = 0.0
        for prompt, reference_img in test_prompts:
            generated_text = self.model.generate(prompt, max_tokens=200)
            # 如果生成内容能高度还原图像内容，认为存在泄露风险
            # 此处仅做文本级检查（不生成图像）
            similarity = text_image_alignment_score(generated_text, reference_img)
            max_similarity = max(max_similarity, similarity)

        if max_similarity > MULTIMODAL_LEAKAGE_THRESHOLD:
            raise ImageLeakageRisk(f"Max similarity {max_similarity:.3f} exceeds threshold")
```

---

## 5. 半结构化数据场景（SEMI_STRUCTURED_ETL）

```python
class SemiStructuredRuntime:
    """
    半结构化数据处理：JSON / XML / 日志 / 埋点 / 传感器流
    典型场景：业务日志分析、IoT 数据清洗、埋点行为分析
    """

    SUPPORTED_FORMATS = ["json", "jsonl", "xml", "csv_with_nested", "avro", "parquet"]

    def setup(self, handle: SandboxHandle, session: SandboxSession):
        self.db = SecureDuckDBEngine(session, handle.dek)
        # DuckDB 原生支持 JSON 读取和展开
        self.db.execute("INSTALL json; LOAD json;")

    def load_semi_structured_data(self, product: DataProduct):
        """
        加载半结构化数据，自动 Schema 推断 + 嵌套展开
        """
        for chunk_ref in product.chunk_refs:
            encrypted = storage.get_object(chunk_ref.path)
            plaintext = sm4_gcm_decrypt(encrypted, self.db.dek, ...)

            if chunk_ref.format == "jsonl":
                # JSONL：每行一条 JSON 记录
                # 自动推断 Schema，嵌套字段展开为列
                self.db.execute(f"""
                    CREATE OR REPLACE TABLE raw_{product.product_id} AS
                    SELECT * FROM read_ndjson_auto(
                        '/tmp/sandbox/{chunk_ref.chunk_id}.jsonl',
                        auto_detect=true,
                        flatten_nested=true    -- 嵌套 JSON 自动展开
                    )
                """)
            elif chunk_ref.format == "log":
                # 日志：使用 Grok 模式解析
                self.db.execute(f"""
                    CREATE OR REPLACE TABLE raw_{product.product_id} AS
                    SELECT
                        regexp_extract(line, '{product.log_grok_pattern}') AS parsed
                    FROM read_text('/tmp/sandbox/{chunk_ref.chunk_id}.log')
                """)

            # 应用字段级脱敏视图
            self.db._create_masked_view(f"raw_{product.product_id}", product.schema)

    def execute_etl_task(self, script: str) -> ETLResult:
        """ETL 脚本执行（SQL 或 Python pandas）"""
        # 输出：仅允许聚合统计结果，不允许原始行导出
        result = self.db.execute_query(script, self.session.session_id)
        return output_gateway.inspect_and_release(result, self.session.session_id)
```

---

## 6. L2 软件增强沙箱（降级实现）

```python
class FirecrackerSandboxRuntime:
    """
    L2 降级沙箱：Firecracker microVM + gVisor
    无硬件 TEE，通过软件隔离 + MPC 补偿
    """

    FIRECRACKER_CONFIG = {
        "boot_source": {
            "kernel_image_path": "/images/sandbox-kernel-5.15-hardened.bin",
            "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"
        },
        "drives": [
            {
                "drive_id": "rootfs",
                "path_on_host": "/images/sandbox-rootfs-gvisor.ext4",
                "is_root_device": True,
                "is_read_only": True,    # 只读根文件系统
            }
        ],
        "network_interfaces": [],        # 完全禁用网络
        "machine_config": {
            "vcpu_count": None,          # 运行时动态设置
            "mem_size_mib": None,
            "smt": False,                # 禁用超线程（防侧信道）
        }
    }

    async def launch(self, session: SandboxSession) -> L2Handle:
        # 1. 启动 Firecracker microVM
        vm_config = {**self.FIRECRACKER_CONFIG}
        vm_config["machine_config"]["vcpu_count"] = session.allocated_cpu
        vm_config["machine_config"]["mem_size_mib"] = session.allocated_mem_gb * 1024

        vm = firecracker.create_vm(vm_config)
        vm.start()

        # 2. 等待 VM 内 gVisor 就绪（约5s）
        await asyncio.sleep(5)

        # 3. MPC 密钥分片接收（替代 TEE 远程证明）
        # 数商 K1 + 计算节点 K2 通过 MPC 协议在线重组 DEK
        mpc_result = await mpc_runtime.online_phase(
            session_id=session.session_id,
            k2_ref=session.mpc_k2_ref,
        )
        # DEK 直接注入 VM 内存（通过 vsock），不经过宿主机文件系统

        # 4. 启动 eBPF 监控探针（宿主机侧，监控 VM 内系统调用）
        ebpf_probe = eBPFSandboxProbe(vm_pid=vm.pid, session_id=session.session_id)
        ebpf_probe.attach()

        # 5. 应用加严的 Seccomp 规则至 VM 内进程
        vm.apply_seccomp_profile(L2_SECCOMP_WHITELIST)

        return L2Handle(vm=vm, ebpf_probe=ebpf_probe, session_id=session.session_id)

    async def destroy(self, handle: L2Handle):
        # 安全销毁顺序：密钥清除 → 进程终止 → 内存擦除 → VM 删除
        handle.vm.guest_exec("shred -u /proc/self/mem 2>/dev/null; true")  # 尽力擦除
        handle.vm.kill()
        handle.ebpf_probe.detach()
        firecracker.delete_vm(handle.vm.vm_id)
```

---

## 7. 沙箱销毁（安全清理）

```python
class SandboxDestructor:
    """
    任务完成或会话到期后的安全销毁流程
    确保：数据无残留、密钥已吊销、内存已擦除
    """

    async def destroy(self, handle: SandboxHandle, reason: str):
        session_id = handle.session_id

        try:
            # 1. 通知 KMS 吊销会话密钥（防止密钥在销毁后被复用）
            await kms_service.revoke_session_key(session_id)

            # 2. TEE 内触发安全清理（覆写 EPC 内存）
            if handle.level == "L1":
                await handle.enclave.trigger_secure_wipe()
                # Occlum 的 secure_wipe 会：
                # 1. 将所有 EPC 页覆写为 0xAA 再覆写为 0x55
                # 2. 释放 EPC 页，CPU 会清除 EPC 内容
                await handle.enclave.destroy()

            elif handle.level == "L2":
                await handle.vm.guest_exec("sync; echo 3 > /proc/sys/vm/drop_caches")
                await asyncio.sleep(1)
                handle.vm.kill()
                # 销毁加密 tmpfs 挂载点
                os.system(f"cryptsetup close sandbox_{session_id}")

            # 3. 清理磁盘上的临时加密块（如果有）
            tmpfs_path = f"/mnt/sandbox/{session_id}/"
            if os.path.exists(tmpfs_path):
                subprocess.run(["shred", "-u", "-z", "-n", "3", tmpfs_path])

            # 4. 更新会话状态
            db.update(SandboxSession, session_id,
                      status="completed",
                      completed_at=datetime.utcnow())

            # 5. 释放节点资源
            node_manager.release_resources(handle.node_id, handle.allocated_resources)

        except Exception as e:
            logger.error(f"Sandbox {session_id} destruction failed: {e}")
            # 强制标记为失败，触发人工介入
            db.update(SandboxSession, session_id, status="failed",
                      metadata={"destroy_error": str(e)})
            alert_service.send_critical(f"Sandbox destroy failed: {session_id}")

        finally:
            audit_service.record_session_end(session_id, reason)
```

---

## 8. 配置参数

```yaml
sandbox_runtime:
  scene_defaults:
    structured_query:
      max_memory_gb: 32
      max_cpu_cores: 8
      max_runtime_s: 3600
      db_engine: "duckdb"
    structured_modeling:
      max_memory_gb: 64
      max_cpu_cores: 16
      max_runtime_s: 14400        # 4 小时
      db_engine: "duckdb_persistent"
    structured_app:
      max_memory_gb: 16
      max_cpu_cores: 4
      max_runtime_s: 86400        # 24 小时（持续运行服务）
      db_engine: "postgresql"
    product_development:
      max_memory_gb: 32
      max_cpu_cores: 8
      max_runtime_s: 28800        # 8 小时工作日
      max_sample_rows: 1000
      persist_workspace: true     # 跨会话保留工作区
    llm_sft:
      min_sandbox_level: "L1"
      require_gpu: true
      require_gpu_cc: true
      max_gpu_count: 8
      max_memory_gb: 512
      max_runtime_s: 172800       # 48 小时
      max_model_params_b: 72
    vision_train:
      require_gpu: true
      require_gpu_cc: true
      max_gpu_count: 4
      max_memory_gb: 128
    multimodal_train:
      require_gpu: true
      require_gpu_cc: true
      max_gpu_count: 8
      max_memory_gb: 256

  security:
    l1_tee_framework: "occlum"    # occlum | hyperenclave | itrustee
    l2_vm_runtime: "firecracker"
    l2_container_runtime: "gvisor"
    memory_wipe_passes: 3         # 销毁时内存覆写次数
    disable_hyperthreading: true  # 防侧信道，训练节点强制禁用 SMT

  data_loading:
    chunk_size_mb: 16
    max_parallel_decrypt_threads: 4
    integrity_check: "sm3"

  model_security:
    memorization_threshold: 0.15  # MIA advantage ≤ 15% 认为安全
    watermark_method: "weight_shift"
    watermark_key_rotation_days: 30
```

---

*文档：SS-04 | 版本：v1.0 | 行数：~650*
