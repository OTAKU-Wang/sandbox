# CDS Mock/模拟路径 · 逐项问题分析与真实化实现计划

> 日期：2026-09-07
> 基线：Round 44（2420 passed / 0 failed / 3 skipped）；`specs/sandbox-productization-round3-spec.md`（Round 45+ 产品化方案）已建立。
> 方法：对 app/ 全部"模拟/仿真/回退/伪造/孤儿"路径做两路并行代码级审计（加密安全栈 + AI 数据栈，全部带 file:line 证据），并对 load-bearing 断言人工复核（firecracker seccomp 回退未门禁、column_encryption 硬编码 DEK、联邦静态 JWT 键、DP 噪声仅端点调用等均已实证）。
> 性质：**问题分析清单 + 真实化实现计划**。它是 round3-spec 的配套深化——round3 spec 定产品化任务，本文件把"所有 mock 未真实实现的内容"逐个展开为可执行方案。
> 编号：`MR-XX`（Mock Remediation），避免与 round3-spec 的 `N-XX` 冲突；与本文件重叠的既有任务只引用不重述。

---

## 一、分类框架与总体结论

### 1.1 四类分类法

| 类 | 定义 | 处置原则 |
|---|---|---|
| **S1 故意产品形态** | 合成测试数据等，本就是产品能力，标注诚实 | **保持**，不真实化 |
| **S2 诚实降级的软件等价** | 有显式开关/响应标注的软件回退（生产应切真后端） | 保留回退但**生产强制真实后端**；接线计划 |
| **S3 环境门禁型模拟** | 依赖真实硬件/外部系统（TEE/GPU/链/HSM），软件只能预备 | **软件预备接线 + 环境验收**，禁伪造 |
| **S4 伪实现/失效门禁/孤儿代码** | 注释宣称已实现实则未实现、门禁空转、或死代码（**本轮新发现**） | **必须修复**（接线、强制执行或移除） |

### 1.2 总体结论（S4 类关键发现，按严重度）

1. **国密 SM4-GCM AEAD 全线实为 AES-GCM**：`SM4Cipher`（`app/utils/crypto.py:19-36`）永远走 Cryptodome AES-256-GCM，docstring（`crypto.py:2-3,13`）却宣称"生产用 Tongsuo SM4-GCM"——**无任何 Tongsuo/OpenSSL SM4-GCM 分支**；Tongsuo 为 vendor 源码但 Dockerfile/compose/脚本零接线。合规声明与实现不符。
2. **SFT 训练"真实路径"实际永远模拟**：`llm_sft_runtime._run_cpu_training` 传 `model=None, tokenizer=None`（`llm_sft_runtime.py:614-619`）→ `cpu_trainer._can_run_real_training` 返回 False（`cpu_trainer.py:254-255`）→ **即便 torch 已装也走 `_simulate_fallback`**（`cpu_trainer.py:158-163`）。所谓"CPU 真实训练"从未跑过真实前向/反向。
3. **DP 差分隐私在输出检查管线是 no-op**：`add_laplace_noise/add_gaussian_noise` 数学真实（`output_inspection.py:446-460`），但**只在 `/dp/noise` 端点被调用**（`output_control.py:177,179`）；`inspect()` 的 Stage 4"差分隐私"（`output_inspection.py:188-193`）仅标记通过、不加噪声不扣预算。另存在**双预算系统**（内存 `dp_engine._budgets` vs DB `dp_budget_ledger`），计量口径不一致。
4. **MIA `shadow_model` 硬门禁是死代码**：`mia_status="shadow_model"` 全代码库从未赋值（仅 `proxy_estimate`/`not_evaluable`），`llm_sft_runtime.py:465-469` 的硬门禁永不触发；且 `mia_status` 未在 API 响应暴露（`training.py:132-151`）。
5. **`FEDERATION_JWT_KEY_REQUIRED` 门禁空转**：只在 `validate_security_config` 里 WARN（`config.py:295-296`），运行期无任何代码拒绝静态键（`federation_connector.py:39,42-51` 直接返回本地静态键）。
6. **`TEE_SIMULATION_MODE` 死配置**：`config.py:178` 定义后全库无消费方。
7. **FISCO/AntChain 链上存证纯内存**：两适配器（`blockchain_adapter.py:89-154,157-221`）为进程内哈希链、`tx_hash` 立即 confirmed、**无任何网络代码**（全库无 web3/bcos3sdk import）；`blockchain_service._get_client` 恒返回 None（`blockchain_service.py:30-33`）；compose 里的 FISCO 节点无代码连接。PG 后端是唯一持久（本地哈希日志）。
8. **联邦 mTLS 未在实际 HTTP 路径强制**：`mutual_tls.py` 产出伪 PEM（`_cert_to_pem` 手工拼串，`mutual_tls.py:283-298`）；`FederationConnector._execute_remote_request` 用 `httpx.Client(verify=True)`（`federation_connector.py:681`）但**未实际附加客户端证书**。
9. **`column_encryption.py:319` 硬编码默认 DEK**：`hashlib.sha256(b"cds-column-encryption-default-key").digest()[:16]`——无配置门禁的静态密钥。
10. **训练 checkpoint 明文 + 伪造 state_dict**：`_write_sft_checkpoint`（`training.py:92-129`）写明文 JSON 到 `/tmp/cds_training_outputs/`，`state_dict` 为公式伪造；`secure_checkpoint_store`（真 AES-GCM 实现）在 SFT 流程**闲置**。
11. **audit/contract SM2 签名实际永远走软件**：Vault Transit 不支持 SM2（`hsm_adapter.py:127,144-146`）→ `sign_with_hsm` 回退软件（`crypto_service.py:228-241`）；审计条目**无签名来源（软件/HSM）字段**。
12. **孤儿代码五处**：`vision_multimodal_runtime.py`、`vision_pipeline.py`（全模拟且无 API 接线）、`k_anonymity.py`（output_inspection 有自建重复实现）、`query_rewriter.py`（live 路径走 secure_duckdb）、`training_pipeline.py`（无 API 引用）。
13. **firecracker seccomp 回退未门禁**：`firecracker_runtime.py:642-644,784-786` 的 `cmd_no_seccomp` 重试不检查 `SECCOMP_FALLBACK_ALLOWED`（与 `sandbox_runtime.py:324-336` 门禁行为不一致）。
14. **`GPUTeeRuntimeStub` 无 is_simulation 标注**：`gpu_tee_runtime.py:287-295` 的 AttestationReport 标 valid=True 但无模拟标志（`gpu_tee_simulator.py:50,158` 则有标注）。

---

## 二、清单总表（M-01..M-28）

| # | 路径 | 类 | 现状摘要 | 严重度 | 真实化轨道 | 任务 |
|---|---|---|---|---|---|---|
| M-01 | `crypto.py:19-36` SM4-GCM AEAD | S4 | 全线 AES-GCM，注释虚假声明 Tongsuo | **高** | 软件 | MR-A1 |
| M-02 | `llm_sft_runtime.py:614-619` SFT 训练 | S4 | model=None → 永远模拟 | **高** | 软件 | MR-A2 |
| M-03 | `output_inspection.py:188-193` DP 管线 | S4 | DP stage no-op + 双预算 | **高** | 软件 | MR-A3 |
| M-04 | `llm_sft_runtime.py:465-469` MIA 门禁 | S4 | shadow_model 死门禁 + 未暴露 | **中高** | 软件 | MR-A4 |
| M-05 | `config.py:156` 联邦静态键门禁 | S4 | FEDERATION_JWT_KEY_REQUIRED 空转 | **中高** | 软件 | MR-A5 |
| M-06 | `config.py:178` TEE_SIMULATION_MODE | S4 | 死配置 | 中 | 软件 | MR-A6 |
| M-07 | `blockchain_adapter.py` 链上存证 | S4 | FISCO/Ant 纯内存无网络 | **高** | 软件+环境 | MR-A7 |
| M-08 | `federation_connector.py:681` mTLS | S4 | HTTP 路径未强制 mTLS + 伪 PEM | **中高** | 软件+环境 | MR-A8 |
| M-09 | `column_encryption.py:319` 默认 DEK | S4 | 硬编码静态密钥 | **高** | 软件 | MR-A9 |
| M-10 | `training.py:92-129` checkpoint | S4 | 明文+伪造 state_dict | **高** | 软件 | MR-A10 |
| M-11 | `crypto_service.py:228` SM2 签名 | S4 | 永远软件 + 无来源标注 | **中高** | 软件+HSM | MR-A11 |
| M-12 | `gpu_tee_runtime.py:287` stub 标注 | S4 | 无 is_simulation | 中 | 软件 | MR-A12 |
| M-13 | `firecracker_runtime.py:642` seccomp | S4 | 回退未门禁 | **中高** | 软件 | MR-A13 |
| M-14 | `crypto.py:39-45` sm3 SHA-256 回退 | S4 | 无标注 | 低 | 软件 | MR-A14 |
| M-15 | 孤儿五处 | S4 | vision×2/k_anonymity/query_rewriter/training_pipeline | 中 | 软件 | MR-A15 |
| M-16 | `sandbox_runtime.py:991-1080` TEE 软件路径 | S2 | 诚实标注；硬件钩子=子进程命令 | — | 环境 | FG-001 |
| M-17 | `remote_attestation.py` 证明 | S2 | 本地根自签自验、白名单默认空 | **高** | 环境+软件 | MR-B1/FG-001 |
| M-18 | `kms_service.py:219-303` 证明验证 | S2 | software quote 默认接受（ALLOW_SIMULATION 链） | **高** | 环境+软件 | MR-B1/FG-001 |
| M-19 | `result_verifier.py:24` 本地 DCAP 根 | S2 | 本地模拟 | 中 | 环境 | FG-001 |
| M-20 | `hsm_adapter.py` Vault 回退 | S2 | 已门禁 prod RAISE；Transit 无 SM2 | — | 环境+软件 | MR-A11/FG-007 |
| M-21 | `rag_embedding.py` TF 嵌入 | S2 | 诚实标注；transformers 二期 | — | 软件 | round3 N6 |
| M-22 | `pii_ner.py` 规则层 | S2 | 诚实标注；LAC 装机即真 | — | 环境 | T9/FG-014 |
| M-23 | `unstructured_pipeline.py` 引擎 | S2 | 脚本真实；镜像未含引擎 | — | 环境 | FG-010 |
| M-24 | `k8s_sandbox.py` kubectl 子进程 | S2 | 真实但脆弱；优雅降级 | — | 软件 | round3 N7 |
| M-25 | `firecracker_runtime.py` simulation | S2 | 已标注；无 VM 隔离 | — | 环境 | FG-003 |
| M-26 | `mpc_service.py` 内存态 | S2 | 真 Shamir + 无持久化 | — | 软件 | round3 N3 |
| M-27 | TEE/GPU-TEE/真链/HSM 硬件 | S3 | 环境门禁 | — | 环境 | FG-001/002/005/007 |
| M-28 | 合成测试数据 | S1 | 真实生成器 + 诚实标注 | — | 保持 | 无 |

---

## 三、逐项问题分析与实现计划

### S4 类（必须修复）

---

### M-01 · SM4-GCM AEAD 全线实为 AES-GCM

**现状**（`app/utils/crypto.py:2-3,13,17-36`）：`SM4Cipher` 构造固定 32 字节密钥（:17），`encrypt_gcm/decrypt_gcm` 走 `Cryptodome.Cipher.AES` AES-256-GCM（:19-36）。docstring 宣称"Production: Tongsuo SM4-GCM via system OpenSSL"（:2-3）与"production uses Tongsuo SM4-GCM"（:13）——**代码中无 Tongsuo/openssl 调用、无子进程、无 ctypes**。下游真实消费者：`egress_audit.py:45-61`（W19 审计日志静态加密，注释标 SM4-GCM 实为 AES-GCM）、`column_encryption.py`、`hsm_adapter.py:165-181`（软件 KEK wrap）。`secure_checkpoint.py:22-27,90-92,172-193` 直接承认"gmssl 不提供 SM4-GCM，统一用 AES-128-GCM"。`Tongsuo/` 为 vendor 源码，Dockerfile.api / docker-compose / scripts 均无构建接线（grep 实证）。

**问题分析**：
1. **合规风险**：面向等保/商密的部署宣称"国密 SM4-GCM 静态加密"，实现为 AES-256-GCM——审计与验收可被一票否决。
2. **可信性**：docstring 与实现不符，属于"注释谎报"（诚实性纪律红线）。
3. **技术障碍**：gmssl（pip 包）无 SM4-GCM；需 Tongsuo/OpenSSL3+GM provider。

**真实化实现计划（MR-A1）**：
1. `app/core/config.py` 新增 `CDS_SM4_AEAD_BACKEND: str = "aes_fallback"`（合法值 `tongsuo|openssl3|aes_fallback`），四处同步（.env.example / compose / prod / helm values）。
2. `app/utils/crypto.py` 新增真实后端：
   - `tongsuo`：`tongsuo enc -sm4-gcm -K <hex> -iv <hex> -in - -out -` 子进程路径（密钥/明文走 stdin），复用既有 key/iv 派生；subprocess timeout + 失败冒泡。
   - `openssl3`：`openssl enc -sm4-gcm ...`（依赖系统 openssl 3.x + gm provider，探测失败则如实报错）。
3. `SM4Cipher` 按后端分发；`aes_fallback` 时在 `logger.warning` + `validate_security_config` 生产路径 RAISE（等同 `HSM_SOFTWARE_FALLBACK_ALLOWED` 纪律）。
4. **合规披露**：`compliance_report.py` 增加 `aead_algorithm: "SM4-GCM"|"AES-GCM(fallback)"` 字段；`/compliance/reports` 输出。
5. `egress_audit.py` / `secure_checkpoint.py` / `column_encryption.py` / `hsm_adapter.py` 的注释修正 + 走统一 `SM4Cipher` 后端（不再各自 AES-GCM 硬编码）。
6. Dockerfile.api 增加 Tongsuo 构建接线（vendor 源码已存在）：`make -C Tongsuo` + 安装 `openssl` 符号或 `libcrypto`，image 内验证 `tongsuo version` 含 sm4-gcm。

**验收**：`tests/test_crypto_backends.py`（新增）：`aes_fallback` 与 `tongsuo`（有环境）加解密互等；生产配置 `tongsuo` 时 validate RAISE 若不可用；合规报告字段正确；全量回归不降。
**工时**：2 人日（无 Tongsuo 环境时 1.5 + 环境项）。

---

### M-02 · SFT 训练"真实路径"实际永远模拟

**现状**：`llm_sft_runtime.run_sft` 分支（`llm_sft_runtime.py:410`）：torch 可用 → `_run_cpu_training`（:411）→ 构建 dataset 后 `cpu_trainer.train(model=None, tokenizer=None, ...)`（:614-619）。`cpu_trainer.train` 中 `_can_run_real_training(model, tokenizer, dataset)`（`cpu_trainer.py:254-255`）因 `model is None` 恒 False → 走 `_simulate_fallback`（:158-163）。**故 SFT 永远不跑真实训练**，`_write_sft_checkpoint` 的 `final_loss` 等全为模拟值。torch 不可用分支（:426-449）同样只调 `_simulate_training_step`，且**不检查 `TRAINING_REQUIRE_TORCH`**——该 fail-closed 门禁在 API 路径被旁路（只在 `cpu_trainer.train:146-154` 内部生效，而该路径在 torch 缺时根本不会走到）。

**问题分析**：
1. 用户/监管看到"训练任务完成 + loss 曲线 + 水印 + checkpoint"，实际是确定性公式伪造——**最严重的能力造假**。
2. `TRAINING_REQUIRE_TORCH=true`（默认，`config.py:209`）的 fail-closed 承诺未兑现（旁路）。
3. checkpoint 明文 + 伪造 state_dict（M-10）同源。

**真实化实现计划（MR-A2）**：
1. `_run_cpu_training` 加载真实模型与分词器：`transformers.AutoModelForCausalLM.from_pretrained(config.SFT_DEFAULT_MODEL)` + `AutoTokenizer`（`CDS_SFT_MODEL_NAME` 配置，默认小模型如 `sshleifer/tiny-gpt2` 或本地缓存路径；离线可用），传给 `cpu_trainer.train(model=..., tokenizer=...)` 走真实 `train_step`（`cpu_trainer.py:541-580` 含 DP-SGD clip/noise）。
2. **旁路修复**：`run_sft` 的 torch 不可用分支开头检查 `TRAINING_REQUIRE_TORCH`——为 true 则 raise（与 cpu_trainer 一致），false 才走模拟并打 `logger.warning("SIMULATED training")`。
3. 模型下载失败/不可用时**诚实失败**（task 状态 failed，不静默模拟），除非显式 `CDS_TRAINING_ALLOW_SIMULATION=true`（新增，默认 false，prod RAISE）。
4. 模拟结果加 `simulated: true` 字段贯穿 `TrainingResult`→`_sft_metrics_payload`（`training.py:132-151`）→ API 响应。

**验收**：`tests/test_sft_real_training.py`：torch 存在 + 小模型 → 真实 loss（非公式曲线）且 `simulated=false`；`TRAINING_REQUIRE_TORCH=true` + 无 torch → raise；`ALLOW_SIMULATION` 显式开启 → 返回 simulated=true。CI 无 torch 环境跑 fail-closed 用例。
**工时**：3 人日。

---

### M-03 · DP 差分隐私在输出检查管线为 no-op + 双预算

**现状**：噪声数学真实（`output_inspection.py:446-453` Laplace 逆 CDF、:455-460 Gaussian sigma=√(2ln1.25/δ)·1/ε），但仅 `output_control.py:177,179`（`/dp/noise` 端点）调用；`inspect()` Stage 4（`output_inspection.py:188-193`）仅 `stage_results[DP]=True`，不扣预算不加噪声。预算双轨：持久 `dp_budget_ledger`（`dp_budget.py`，policy_evaluator 用）vs `DifferentialPrivacyEngine._budgets`（内存 dict，`output_inspection.py:429-444`，`/dp/budget/*` 端点用）。

**问题分析**：
1. 合约要求"DP 输出保护"时实际**无噪声保护**——买方可反推原始聚合值，DP 承诺为空。
2. 双预算口径：同一会话 `dp_engine.get_remaining` 与 `dp_budget_ledger.get_remaining` 结果可能不同，运营与审计对不上。
3. `/dp/noise` 端点本身可用作白噪音服务，但与契约 epsilon 无关（任何会话都可调用，无校验）。

**真实化实现计划（MR-A3）**：
1. `inspect()` Stage 4 接线：当 `OutputPolicy`/合约含 `dp_epsilon` 时，在输出数值字段上调用 `dp_engine.add_laplace_noise(value, sensitivity, epsilon)`（数值列），并从 `dp_budget_ledger` 扣减（单一口径）；无 DP 要求时跳过并如实标注 `dp_applied=false`。
2. **统一预算**：`/dp/budget/init`、`/dp/budget/{session_id}` 改走 `dp_budget_ledger`（删除 `dp_engine._budgets` 或改为 ledger 的薄封装）；`output_inspection` 的 `_budgets` 移除。
3. `/dp/noise` 端点加合约/会话校验（需持有有效会话 + 预算），并记录到 audit。
4. 合规披露：`output_policy` 增加 `dp_applied/epsilon_consumed` 字段；文档修正 `docs/error-codes.md` 中 DP 语义。

**验收**：`tests/test_dp_pipeline.py`：带 DP 契约的 inspect → 输出数值被扰动且 `dp_applied=true` + 预算扣减；无双轨不一致断言；无契约会话调用 `/dp/noise` → 拒绝。
**工时**：2 人日。

---

### M-04 · MIA shadow_model 硬门禁死代码 + mia_status 未暴露

**现状**：`mia_status` 合法值 `shadow_model|proxy_estimate|not_evaluable`（`llm_sft_runtime.py:113,120-124`）；实际仅 `proxy_estimate`（:424）或 `not_evaluable`（:187-197）被赋值，**`shadow_model` 全库未赋值** → `llm_sft_runtime.py:465-469` 硬门禁永不触发（死代码，:460-463 注释自认）。且 `_sft_metrics_payload`（`training.py:132-151`）不含 `mia_status`，API 用户看不到。

**问题分析**：
1. "记忆泄露硬拦截"承诺未生效——高记忆分数模型可无拦截出网。
2. 诚实标签存在但未对外暴露，等于没有。

**真实化实现计划（MR-A4）**：
1. `mia_status` 加入 `_sft_metrics_payload` + `TrainingJob.metrics` 暴露（`training.py:217`）。
2. 硬门禁接线：提供真 shadow-model 评估开关 `CDS_MIA_SHADOW_MODEL=true`（默认 false）——开启时训练一个影子模型评估并赋 `shadow_model`，门禁才生效；关闭时若 `memorization_score` 超阈**降级披露**（返回 `memeory_risk=high` 字段并记录 audit，不静默放行）。
3. 移除/显式标注死代码分支（接线后保留 shadow 分支）。

**验收**：API 响应含 `mia_status`；高记忆样本在 shadow 开启时被拒、关闭时返回 risk 字段；测试覆盖两态。
**工时**：1.5 人日。

---

### M-05 · FEDERATION_JWT_KEY_REQUIRED 门禁空转

**现状**：`config.py:156` 注释称"拒绝生产静态联邦 JWT 键"，但该配置**仅**在 `validate_security_config:295-296` WARN；`federation_connector.py:42-51` `_default_jwt_secret_key` 运行期不读它——仅在 JWT_SECRET_KEY 为默认且非 DEBUG/TESTING 时经 `validate_jwt_security` 抛错。`_LOCAL_FEDERATION_JWT_KEY`（:39）在 dev/test 直接可用。

**问题分析**：声明的安全门禁未实现为强制行为；文档与代码不符（同 M-01 类诚实性问题）。

**真实化实现计划（MR-A5）**：在 `federation_connector` 签名/验签处强制执行：`FEDERATION_JWT_KEY_REQUIRED=true` 时，若解析到的签名密钥为本地静态键（`_LOCAL_FEDERATION_JWT_KEY`）或默认值 → 拒绝握手（403）并告警；false 仅 dev/test。`validate_security_config` 改 prod RAISE（与 SECCOMP/HSM 纪律一致）。测试：prod 配置 + 静态键 → 拒绝。
**工时**：0.5 人日。

---

### M-06 · TEE_SIMULATION_MODE 死配置

**现状**：`config.py:178` 定义 `TEE_SIMULATION_MODE: bool = True`，全库无消费方（grep 实证仅定义行）。实际 gating 走 `TEE_MODE`/`TEE_ALLOW_SOFTWARE_FALLBACK`/`TEE_HARDWARE_*_CMD`。

**真实化实现计划（MR-A6）**：移除死配置键（或删除后以 `TEE_MODE` 为准并注释）；同步 `.env.example`/`.env.prod.example`；`tests/test_config.py` 加断言不再有该键引用。
**工时**：0.25 人日。

---

### M-07 · FISCO/AntChain 链上存证纯内存、无网络代码

**现状**：`blockchain_adapter.py:89-154`（FISCOBCOSAdapter）与 :157-221（AntChainAdapter）均为进程内哈希链——`tx_hash = sm3(...)` 前缀 `fisco:`/`ANT-`（:104-128），`confirmed=True` 立即返回；无 web3/bcos3sdk import（全库 grep 实证）。`blockchain_service.py:30-33` `_get_client` 恒 None → 恒 `_anchor_local`（:56-57）。`docker-compose.blockchain.yml` + `scripts/fisco-init.sh` 有 FISCO 单节点 + AuditRegistry.sol 部署，但无代码连接。PG 后端（:224-410）是真 DB 持久化哈希日志（诚实"防篡改追加日志"）。compliance 披露已诚实（`compliance_report.py:46-67` backend_label）。

**问题分析**：联盟链存证承诺（`CDS_BLOCKCHAIN_BACKEND=fisco_bcos` 时）实为内存模拟——**重启丢锚定**；与"跨链审计"产品叙事不符（FG-005 是真链验收，但当前连网络客户端骨架都没有）。

**真实化实现计划（MR-A7）**：
1. **短期（软件）**：FISCO/AntChain 适配器改为**DB 持久化哈希链**（复用 PG 后端存储，仅保留 fisco/ant 前缀与 backend_label），杜绝重启丢失；`blockchain_service` 移除恒 None 客户端（接适配器）。
2. **中期（接线）**：新增 `web3`（或 bcos3sdk，PyPI `bcos3sdk` 评估）依赖分支：`FISCOBCOSAdapter` 接真实节点 RPC（`CDS_FISCO_RPC_URL`），部署/调用 `contracts/AuditRegistry.sol`（`fisco-init.sh` 已可部署）；`confirmed` 改为等待区块确认。
3. **诚实披露**：`compliance_report` 在 `is_consortium_chain=true` 时披露"持久化本地哈希日志（联盟链接线待 FG-005 环境验收）"，避免与真链混淆。
4. 测试：持久化跨重启锚定完整；有节点环境（环境验收）真上链用例。

**工时**：2 人日（短期）+ 环境验收（中期）。
**依赖**：与 round3 N2（chain_attestation 死代码处置）协同——本项管链上存证主路径，N2 管 TLCP 存证服务清理。

---

### M-08 · 联邦 mTLS 未在 HTTP 路径强制 + 伪 PEM 证书

**现状**：`mutual_tls.py:283-298` `_cert_to_pem` 手工拼伪 PEM（注释自认"生产应产出真正 ASN.1 DER→PEM"）；`verify_certificate`（:159-196）对字符串化字段重算签名，非真实 TLS 握手；`get_tls_context` 返回 dict（:265-281）。`FederationConnector._execute_remote_request`（`federation_connector.py:681`）`httpx.Client(verify=True)` **未附加客户端证书**（仅 headers 传 token）。

**问题分析**：宣称的双向 TLS 联邦身份认证实际未生效——远程调用仅靠 token（且静态键回退 M-05 叠加放大风险）；证书链不可验证。

**真实化实现计划（MR-A8）**：
1. `_execute_remote_request` 支持 `httpx.Client(verify=CA_BUNDLE, cert=(CLIENT_CERT, CLIENT_KEY))`（配置 `CDS_FEDERATION_CA_BUNDLE` / `CDS_FEDERATION_CLIENT_CERT` / `KEY`）；缺配置且生产 → 拒绝建立联邦连接（fail-closed）。
2. `mutual_tls` 的 CA 改为真实 `cryptography` ASN.1 证书签发（`x509.CertificateBuilder`），PEM/DER 合规；保留 SM2 曲线支持（cryptography 无 SM2 → 用 `gmssl`/Tongsuo provider 或文档注明证书用 RSA/ECC，签名算法字段诚实）。
3. 握手阶段交换 SM2 身份签名（沿用 `SM2IdentityProvider`）作为应用层第二因子。
4. 测试：缺证书生产拒绝；有证书建立真实 TLS 会话；跨空间 e2e（FG-008 环境）。
**工时**：1.5 人日（软件）+ FG-008（环境）。

---

### M-09 · column_encryption 硬编码默认 DEK

**现状**：`column_encryption.py:319` `_default_dek = hashlib.sha256(b"cds-column-encryption-default-key").digest()[:16]`——静态硬编码密钥，未配置门禁。用于确定性加密默认路径。

**问题分析**：任何拿到源码者皆知默认列加密密钥——静态列数据（如字段级分类加密）可被直接解密；确定性加密的枚举攻击面（IV 派生固定）叠加静态密钥=灾难。

**真实化实现计划（MR-A9）**：
1. `CDS_COLUMN_ENCRYPTION_KEY` 配置必填（.env.prod 必设）；dev/test 用默认时打 warning。
2. `validate_security_config` 生产路径：`CDS_COLUMN_ENCRYPTION_KEY` 缺失或等于已知默认值 → RAISE。
3. 移除 `_default_dek` 或改为从 `settings` 读取 + 启动断言非默认。
4. 测试：生产配置默认键 → 启动拒绝。
**工时**：0.5 人日。

---

### M-10 · 训练 checkpoint 明文 + 伪造 state_dict

**现状**：`training.py:92-129` `_write_sft_checkpoint` 写明文 JSON（`state_dict` 为公式伪造 `(idx+1)*0.001 + final_loss*0.0001`）到 `/tmp/cds_training_outputs/{job_id}.json`；`/jobs/{job_id}/checkpoints`（:416）列 `secure_checkpoint_store.list_checkpoints(job_id)`——该 store（真 AES-GCM，`secure_checkpoint.py`）从未被写入，恒空。

**问题分析**：训练产物（含"水印模型"）是伪造张量 + 明文存放；真实加密 checkpoint 机制闲置；用户下载的"模型"不可用。

**真实化实现计划（MR-A10）**：随 M-02 真实训练接线后，`_write_sft_checkpoint` 改经 `secure_checkpoint_store`（真加密）；`state_dict` 来自真实训练 `cpu_result`；`/checkpoints` 端点回读真实。`secure_checkpoint.py` 的 SM4-GCM 注释随 M-01 统一修正。若暂不接真实训练，则 `/checkpoints` 与 `watermark` 端点显式返回 `not_available`（诚实失败）而非伪造。
**工时**：0.5 人日（随 M-02）+ 0.5 人日（store 接线）。

---

### M-11 · audit/contract SM2 签名永远软件 + 无来源标注

**现状**：`audit_service.py:74-102`：先 `generate_hsm_signing_keypair("audit-signing")`（:79-82），HSM 不可用回退内存软件钥（:85-87，`_hsm_key_id="__software__"`）；`sign_with_hsm`（:94）→ `hsm_adapter.sign`，Vault Transit 不支持 SM2（`hsm_adapter.py:127,144-146`）→ 回退软件（`crypto_service.py:228-241`）。**实际永远软件签名**。`contract_service.py:298-320` 平台见证签名同构。审计条目 detail 记录 `_sm2_signature`（`audit_service.py:117`）但**无签名来源字段**；`monitoring.py:111-122` 仅显示 hsm_vault capability。

**问题分析**：
1. "SM2 签名由 HSM 保管私钥"承诺未兑现（Vault Transit 无 SM2）。
2. 软件私钥在内存/DB——签名不可由硬件密钥审计，合规口径弱。
3. 无来源标注→运营无法区分软件/HSM 签名，无法评估风险。

**真实化实现计划（MR-A11）**：
1. 审计条目 schema 增加 `signing_backend: "hsm"|"software"`（`audit_service.py` detail 注入）。
2. 接入支持 SM2 的真 HSM 适配器（`HSM_PROVIDER` 配置：vault_transit（AES 类）/ pkcs11_sm2 / tongsuo_hsm / software）；软件回退时按 `HSM_SOFTWARE_FALLBACK_ALLOWED` 门禁（生产 RAISE 已存在，补 SM2 分支）。
3. `validate_security_config` 生产要求 `signing_backend != software`（与 SM4 M-01 同纪律）。
4. 测试：软件签名打标；有真 HSM 环境（FG-007）验签路径。
**工时**：1 人日（软件）+ FG-007（环境）。

---

### M-12 · GPUTeeRuntimeStub 无 is_simulation 标注

**现状**：`gpu_tee_runtime.py:287-295`（stub 的 attest）AttestationReport `valid=True, tee_enabled=True` 无模拟标志；`gpu_tee_simulator.py:50,92-95,158` 则 `is_simulation=True` 诚实标注。singleton 默认 `GPUTeeSimulator`（`gpu_tee_runtime.py:483-488`）。

**问题分析**：走 `local` 后端（`LocalSoftwareGPUTeeRuntime`）的报告看起来像真 TEE 已启用——误导监控/审计。

**真实化实现计划（MR-A12）**：`GPUTeeRuntimeStub` 的 AttestationReport 增加 `is_simulation=True` + `tee_backend="local_software"`；`monitoring.py:99-108` 以运行期报告为准（而非仅配置）。测试：断言 stub 报告含 simulation 标志。
**工时**：0.25 人日。

---

### M-13 · firecracker seccomp 回退未门禁

**现状**：`firecracker_runtime.py:642-644,784-786`：`cmd_no_seccomp` 重试仅当 `_seccomp_retry_needed(stderr, fd)`，**不检查 `SECCOMP_FALLBACK_ALLOWED`**（与 `sandbox_runtime.py:324-336` 的 `_seccomp_fallback_allowed()` 门禁不一致）。

**问题分析**：生产 `SECCOMP_FALLBACK_ALLOWED=false` 时，L2 firecracker 路径仍可能无 seccomp 重试执行——沙箱降级隔离而未被拦截（安全姿态不一致）。

**真实化实现计划（MR-A13）**：`firecracker_runtime` 重试前检查 `_seccomp_fallback_allowed()`（复用 sandbox_runtime 逻辑或抽公共函数）；不允许时该次执行返回失败并记录 audit，不静默降级。测试：`SECCOMP_FALLBACK_ALLOWED=false` + 触发 seccomp 错误 → 失败而非重试。
**工时**：0.5 人日。

---

### M-14 · sm3 SHA-256 回退无标注

**现状**：`crypto.py:39-45` `sm3_hash`：gmssl 可用用 SM3，否则 SHA-256，无配置门禁无响应标注。使用方（merkle/audit/watermark）可能哈希算法混用。

**问题分析**：跨进程/跨节点哈希一致性依赖运行环境一致；一处装 gmssl 一处不装 → 审计哈希链条不一致。

**真实化实现计划（MR-A14）**：`CDS_SM3_FALLBACK_ALLOWED: bool`（默认 true dev / false prod RAISE）；`sm3_hash` 在 fallback 时 warning 一次；文档标注。低优先。
**工时**：0.25 人日。

---

### M-15 · 孤儿代码五处

**现状**（grep 实证均无 API/其他服务引用）：
- `vision_multimodal_runtime.py` + `vision_pipeline.py`：全模拟（字节标记"变换"、伪造 loss/accuracy、伪水印）+ **无 API 接线**（训练 API 只接 `llm_sft_runtime`）。
- `k_anonymity.py`：真算法但 `output_inspection.py:325-366` 有自建重复 k-anonymity 检查，服务未导入。
- `query_rewriter.py`：真 SQL 重写（:88-148）+ ResultDecryptor（:175-235）但 live 加密库路径走 `secure_duckdb.py`，未导入。
- `training_pipeline.py`：`TrainingPipelineManager`（:374）未被任何 API 导入。

**问题分析**：死代码维护成本 + 误导（误以为视觉/多模态/训练管线已产化）；重复实现口径分裂（两套 k-anonymity）。

**真实化实现计划（MR-A15）**（并入 round3 N2 死代码处置，此处登记）：
1. vision×2 + training_pipeline：若本期不立项产化，加模块顶部 `Deprecated` 标注 + 移除或移入 `app/experimental/`；`specs` 登记为"待产品化（N10/N12 相关）"。
2. k_anonymity：统一到 `output_inspection` 的实现（删服务或让服务成为唯一实现并供 output_inspection 复用，二选一）。
3. query_rewriter：若 secure_duckdb 已覆盖，移除或标注 deprecated；否则接线。
**工时**：0.5 人日。

---

### S2 类（诚实降级，接线/切真）

### M-16 · TEE 软件路径（software_confidential）
**现状**：`sandbox_runtime.py:991-1080`：TEE 能力探测（`tee_capability.py` 设备/命令）→ 硬件不可用 + `TEE_ALLOW_SOFTWARE_FALLBACK` → `software_confidential`，`is_simulation=True, tee_mode="software_confidential", hardware_available=False, fallback_reason`（:1061-1065）诚实标注。硬件钩子=四子进程命令 `CDS_TEE_HARDWARE_*_CMD`（`config.py:171-174`，默认空）。
**处置**：软件路径保留（诚实）；硬件真实化属 FG-001。**软件预备项（MR-B0）**：硬件命令钩子输出契约文档化 + 失败冒泡测试。工时 0.5。

### M-17 · 远程证明本地根自签自验
**现状**：`remote_attestation.py`：SGX（:161-226）、SEV（:280-341）、Firecracker（:397-480）全部用本地静态根 `_LOCAL_ATTESTATION_ROOT=b"cds-local-attestation-root-v1"`（:36）自签自验；`AttestationPolicy.allowed_measurements` 默认空=允许一切（:122）；无 Intel PCS/AMD KDS/DCAP 库（grep 实证仅注释）；SGX 模拟 quote 标 `sgx_ecdsa`（类型层误标）。
**问题分析**：证明=自证，无信任根价值；measurement 白名单默认空（KMS 处同）——即便有硬件也默认放行一切 measurement。
**真实化实现计划（MR-B1）**：
1. 将 `allowed_measurements` 默认从空改为 `required`（配置 `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` 必填，prod RAISE）——先于硬件接入即闭环。
2. 外部信任根接线点：`verify_quote` 支持注入 `trust_root`（Intel PCS/AMD KDS client 或静态可验证链），当前软件根时响应含 `trust_root="local-simulated"` 明确标注。
3. SGX 模拟 quote 的 `quote_type` 改诚实值（`sgx_ecdsa_simulated` 或加 `is_simulation`），不冒充真 ECDSA。
4. 硬件接入归 FG-001。
**工时**：1 人日（软件）+ FG-001（硬件）。**与 M-18 强关联**。

### M-18 · KMS 证明验证自验证环
**现状**：`kms_service.py:219-303` `_verify_attestation`：解析 raw attestation→TEEQuote（:244-266）；`QuoteType.SOFTWARE_HASH` 仅在 `ALLOW_SIMULATION=false` 拒绝（:271-282，默认 true 接受）；`AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300)`（:284）但 `allowed_measurements` 空；`verify_quote` 自签自验通过（:285-286）。
**处置**：随 M-17 收紧——`KMS_REQUIRE_ATTESTATION=true` 时若 `ALLOW_SIMULATION=true` 仍接受 software quote，需在审计/响应中标注 `attestation_trust="local-simulated"`；`allowed_measurements` 必填后，KMS 强制非空校验。**测试**：空白名单在 prod → 拒绝。工时 0.5。

### M-19 · result_verifier 本地 DCAP 根
**现状**：`result_verifier.py:24` `_LOCAL_DCAP_ROOT=b"cds-local-dcap-root-v1"`，DCAP quote 自签自验（:268-293,344-356）；`AttestationStatus.SIMULATED` 声明为 deprecated 不接受（:38）。
**处置**：随 M-17；`FULL` 等级在无真验证时应为 `FAILED` 或 `SIMULATED`（当前 local quote 能达 FULL，过松）。测试：local quote → 非 FULL。工时 0.5。

### M-20 · HSM/Vault 回退
**现状**：`hsm_adapter.py`：Vault Transit 真 AES KEK wrap（:60-146，hvac 存在时），SM2 不支持（:144-146）；软件回退受 `HSM_SOFTWARE_FALLBACK_ALLOWED` 门禁（:208-240，prod RAISE 已生效，`.env.prod.example:25` false）。
**处置**：保持；SM2 来源标注见 M-11。真 HSM 归 FG-007。

### M-21 · RAG 嵌入 TF
**现状**：`rag_embedding.py`：`tf`（确定性 FNV-1a char n-gram，runner 可复现）/`regex`/`transformers`（仅宿主侧）；`auto→tf`（:133-141）；`_RUNNER_REPLICABLE_ENGINES={"tf","regex"}`（:30）→ 沙箱侧 transformers fail-closed（`rag_service.py:207`）；`EmbeddingResult.engine`/`RagIndex.engine` 诚实标注（:50-57）。
**处置**：保持诚实；transformers 沙箱侧嵌入 = round3 N6（M-21 引用）。

### M-22 · PII NER 规则层
**现状**：`pii_ner.py`：`auto`→LAC→rule（:206-236），模型缺装诚实降级 rule 并打 warning（:223-229）；`ner_engine` 字段诚实（:65）；LAC/transformers 加载器就绪（:238-268）；`PII_NER_USE_ML=false` 默认。
**处置**：保持诚实；装模型即真（LAC 属 T9/FG-014 环境项）。

### M-23 · 非结构化管线引擎
**现状**：`unstructured_pipeline.py`：bwrap 沙箱内 Python 脚本调真引擎——pytesseract（:267-269）、whisper/vosk（:292-329）、ffmpeg/ffprobe（:354-383）、pdftotext/python-docx（:416-442）、pydicom（:481-495）；引擎缺装 per-file note + `partial_success`；脚本依赖镜像未含引擎。
**处置**：脚本真实；引擎进镜像属 FG-010 环境项。软件预备：镜像构建清单（`scripts/` 增加 OCR/ASR 依赖安装说明）。工时 0.25。

### M-24 · K8s kubectl 子进程
**现状**：`k8s_sandbox.py`：`_kubectl` 子进程（:69-81）遍布（:144-146,183,250,292,362,491,529,575,611,638）；缺 kubectl 优雅降级（provision 返回 failed，不 fail-open）。
**处置**：Python client 化 = round3 N7（M-24 引用，不重述）。

### M-25 · Firecracker simulation 回退
**现状**：`firecracker_runtime.py`：`_detect_backend`（:117-134）firecracker→qemu-tcg→simulation（bwrap 直接子进程无 VM）；simulation `create_vm` 立即 RUNNING（:215-219）；`is_simulation` 经 provision 传播（`sandbox_runtime.py:906`）；warning 诚实（:112-113）。
**处置**：保持标注；真 VM 通道属 FG-003。

### M-26 · MPC 内存态
**现状**：`mpc_service.py`：真 Shamir SSS（:18-53 多项式求值、:126 secrets.randbelow、:31-53/184 Lagrange）；内存 dict（:86-87）重启即丢；无计算协议。
**处置**：持久化 = round3 N3（M-26 引用，不重述）；HE/MPC 计算 = round3 N11。

### S3 类（环境门禁）
### M-27 · TEE/GPU-TEE/真链/HSM 硬件
**处置**：全部按 FG-001/002/005/007 环境验收；软件预备项 = 本文件 M-17/M-18/M-19/M-20/M-07 中期；禁伪造（项目铁律）。

### S1 类（保持）
### M-28 · 合成测试数据
**现状**：`test_data_generator.py` 真随机生成器（:58-84，random 模块 + 中文 PII 风格值，非占位）；`data_resources.py:389-400` generate-synthetic（`"synthetic": true`）+ :403-410 generate-mock（deprecated）；`data_products.py:465-477/480-488/491-540/543-594` synthetic/mock(deprecated)/sample(真抽样)/desensitize(真掩码)。合成数据是**故意产品形态**（开发/测试沙箱喂数）。
**处置**：**保持**；deprecated 别名可在后续清理（非 mock 问题）。

---

## 四、优先级矩阵与排期

### 4.1 严重度 × 成本矩阵

```
           修复成本
         低        中        高
    高 ┌──────────────────────────┐
严  高 │ M-09 M-13 M-14   M-01    │  第一阶段必做
重  中 │ M-05 M-06 M-12   M-02    │
度  中高│ M-04 M-11(软)    M-03    │
    中 │ M-15             M-07(短)│  第二阶段
    低 │ M-19 M-20        M-08    │  环境轨道
     └──────────────────────────┘
```

### 4.2 分阶段排期

| 阶段 | 内容 | 任务 | 工时 | 时点 |
|---|---|---|---|---|
| **P0-1 诚实性速修** | 死门禁/死配置/硬编码/标注缺失 | MR-A5, A6, A9, A12, A13, A14 | 2 人日 | 第 1 周 |
| **P0-2 能力造假修复** | SM4-GCM 真后端、SFT 真实训练、DP 接线、checkpoint、签名来源 | MR-A1, A2, A3, A10, A11 | 9 人日 | 第 2-3 周 |
| **P1-1 证明与链** | 证明白名单强制、本地根标注、链存证持久化 | MR-B1, A18, A19, A7(短) | 3.5 人日 | 第 4 周 |
| **P1-2 联邦与孤儿** | mTLS 接线、MIA 暴露、孤儿处置 | MR-A8, A4, A15 | 3 人日 | 第 5 周 |
| **环境轨道** | TEE/GPU/链/HSM/引擎镜像/LAC | FG-001/002/005/007/010/014 + MR 软件预备 | 持续 | 硬件就绪后 |

**总软件工时 ≈ 18 人日**（P0 11 + P1 6.5 + 预备 0.5）。

### 4.3 与既有计划的关系

| 本文件任务 | 关联 |
|---|---|
| MR-A1（SM4） | 独立新增（round3 spec 未覆盖） |
| MR-A2/A3/A4/A7/A8/A9/A10/A11 | 独立新增 |
| MR-A15（孤儿） | 扩展 round3 N2 |
| MR-A7 中期 / MR-B1 / M-17/18/19 | FG-001 软件前置 |
| M-21 → round3 N6；M-24 → round3 N7；M-26 → round3 N3；M-23 → FG-010 | 引用 |
| MR-A11 真 HSM / M-20 | FG-007 |

---

## 五、横切面

### 5.1 配置开关矩阵（新增/修改）

| 字段 | 默认 | 生产 | 任务 |
|---|---|---|---|
| `CDS_SM4_AEAD_BACKEND` | aes_fallback | tongsuo/openssl3（必填） | MR-A1 |
| `CDS_SFT_MODEL_NAME` | tiny-gpt2 或缓存路径 | 同 | MR-A2 |
| `CDS_TRAINING_ALLOW_SIMULATION` | false | false（prod RAISE） | MR-A2 |
| `CDS_MIA_SHADOW_MODEL` | false | 按需 | MR-A4 |
| `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` | 空（→ 必填） | 必填（prod RAISE） | MR-B1 |
| `CDS_COLUMN_ENCRYPTION_KEY` | 默认 | 必填非默认（prod RAISE） | MR-A9 |
| `CDS_SM3_FALLBACK_ALLOWED` | true | false（prod RAISE） | MR-A14 |
| `CDS_FEDERATION_CA_BUNDLE/CERT/KEY` | 空 | 必填（prod 缺则拒联邦） | MR-A8 |
| `CDS_FISCO_RPC_URL` | 空 | 真链时必填 | MR-A7 |

原则沿用：**安全默认 fail-closed，宽松只在显式配置**；四处同步；改动过 `tests/test_config.py`。

### 5.2 诚实性披露（跨任务）

- 所有模拟路径在 API/审计/合规响应中带显式字段：`simulated / is_simulation / backend / ner_engine / mia_status / signing_backend / aead_algorithm / trust_root / dp_applied`。
- `validate_security_config` 生产路径对"宣称真实实为模拟"的开关一律 RAISE（SM4、SFT 模拟、SM3、联邦 mTLS、证明白名单、列密钥）。
- `docs/error-codes.md` 与 `compliance_report` 随任务同步。

### 5.3 每任务 DoD（沿用既有 + 本轮新增）

1. 新测试全绿；2. 全量回归不降基线（2420）；3. `compileall` 全绿；4. 配置四同步；5. 模拟路径显式标注；6. 生产 fail-closed 开关经 `validate_security_config` 验证；7. 无裸 `except: pass` 新增；8. 回写本文件执行记录。

---

## 六、执行记录

（待实施轮次逐轮追加：任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项）
