# 修波派工（全量修复轮 · 2026-10-18）

判据沿用上一轮：每条改动要有本机双读数（改前红 / 改后绿），运维面允许「静态门 + `bash -n`」形态并标注「仅部署可验」。作业规范见 `fix-brief.md`，条目原文见 `ocr-verified-findings.md`，队列见 `fix-queue.md`。

| 子代理 | 域 | 文件集（互斥） | 条目 |
|---|---|---|---|
| st_01a14f57 | 后端·密钥面 | `app/api/certificates.py`、`app/api/kms.py`、`app/services/kms_service.py`、`app/services/crypto_service.py` | A1 A106 A13 A271 A161 A110 A113 A255 |
| st_01a14f58 | 后端·会话面 | `app/api/sandbox_sessions.py`、`app/api/session_stream.py`（+ 需要时 `session_service.py`） | A30 A31 A294 A24 A25 |
| st_01a14f59 | 后端·数据面门 | `app/api/sandbox_db.py`、`app/services/secure_duckdb.py` | A48 A49 A50 A51 A56 |
| st_01a14f5a | 后端·开发沙箱 | `app/api/dev_sandbox.py`、`app/services/cube_template_service.py`、`app/api/cube_templates.py` | A52 A53 A54 A55 A91 A69 |
| st_01a14f5b | 后端·水印/RAG/DLP | `app/services/model_watermark.py`、`rag_embedding.py`、`notebook_service.py` | A185 A186 A187 A129 A111 |
| st_01a14f5c | 后端·证明与能力 | `app/services/remote_attestation.py`、`tee_capability.py`、`session_snapshots.py` | A183 A162 A116 |
| st_01a14f5d | 后端·管理台模板 | `app/templates/**`、`app/static/**` | A114 |
| st_01a14f5e | 运维·CI | `.github/workflows/**`、`ci/**` | w5 claims（含 token 权限面） |
| st_01a14f5f | 运维·cube-node | `deploy/cube-node/**` | w5 claims |
| st_01a14f60 | 运维·scripts | `scripts/**`（不得改 `verify_schema_parity.py`） | w5 claims |
| st_01a14f61 | 运维·k8s/helm | `k8s/**`、`helm/cds/**`、`deploy/local/**`、`deploy/e2e-remote/**` | A301 A302 A303 + w5 claims |
| st_01a14f62 | 前端·合约/连接器 | `contractApi.ts`、`connectorApi.ts`、`ContractCreate.tsx`、`ContractDetail.tsx`、`Connectors/index.tsx` | A313 A314 A315 A316 A317 A318 A319 |
| st_01a14f63 | 前后端·脱敏声明 | `app/api/data_resources.py`、`app/services/test_data_generator.py`、`dataResourceApi.ts`、`DesensitizedPreviewModal.tsx`、`ResourceList.tsx`、`DataPipeline.tsx`、`ProductCreate.tsx` | A309 A310 A311 A312 |
| st_01a14f64 | 前端·外壳/无障碍 | `nginx.conf`、`main.tsx`、`App.tsx`、`MainLayout.tsx`、`ProtectedRoute.tsx`、`Login.tsx`、`AuditLog.tsx`、`index.css`、`Sidebar.tsx`、`DevTaskCreatePage.tsx`、`DevTaskDataPicker.tsx`、`TaskDetail.tsx` | A304 A305 A306 A307 A228 A219 A221 A208 A235 A232 |
| st_01a14f65 | 前端·工作台/SM2 | `notebookWorkbenchModel.ts`、SM2 前端模块 | A265 A267 A258 |

## 已知重叠与处置
- **A12 与 A268 同缺陷**（MPC 分片明文读取）：上一轮已修（`app/api/mpc.py` 收口 + 审计），本轮只做「确认仍在」，不重开。
- `app/api/mpc.py`、`app/core/security.py`、`app/services/merkle_service.py`、`app/services/audit_service.py` 已在上一轮改动，**本轮不派**（避免与已完成改动打架）；`app/services/output_inspection.py` 待 st_01a14f5b 报告后再决定（它与 A189-A193 同文件簇）。
- 七份部署配置副本一致性由 st_01a14f61 负责列出「同步/未同步 + 理由」清单。

## 第二波（后端 high + medium-high，已排除在跑子代理占用的 13 个文件）
按**基名**归一后分桶；同名文件先在 `app/` 下 grep 定位再动手。

- W2桶1（11 条）：federation_connector.py[A150 A60 A61 A63]、plugin.go[A199 A200 A201]、agent_service.py[A103 A157]、[A155 A83]
- W2桶2（12 条）：audit.py[A5 A134]、data_pipeline.py[A3 A165]、open_api.py[A29 A168]、training.py[A22 A167]、config.py[A40 A141]、auth_service.py[A89 A92]
- W2桶3（9 条）：cpu_trainer.py[A101 A102]、mpc_service.py[A142 A143]、sandbox_manager.py[A108 A109]、api_keys.py[A7]、auth.py[A14]、compute.py[A2]
- W2桶4（6 条）：connectors.py[A6]、contracts.py[A19]、field_exposure.py[A4]、k8s_ops.py[A15]、output_control.py[A21]、rag.py[A26]
- W2桶5（6 条）：sandbox_nodes.py[A28]、database.py[A42]、logging.py[A43]、redis.py[A38]、main.py[A75]、alert.py[A120]
- W2桶6（6 条）：data_resource.py[A203]、federation_trust.py[A124]、code_scanner.py[A97]、contract_fulfillment.py[A147]、contract_service.py[A93]、cube_runtime_adapter.py[A90]
- W2桶7（6 条）：dp_budget.py[A145]、gateway_service.py[A164]、policy_evaluator.py[A95]、quota_manager.py[A146]、retention_janitor.py[A130]、secure_checkpoint.py[A126]
- W2桶8（6 条）：session_templates.py[A136]、tlcp_service.py[A104]、vision_pipeline.py[A152]、admin.js[A241]、connectors.html[A287]、res.json[A242]
- W2桶9（6 条）：rootfs.go[A204]、sandbox_runtime.py[A156]、sandbox_task.py[A82]、monitoring.py[A18]、watermark_record.py[A171]、data_processing.py[A135]
- W2桶10（4 条）：trust_evaluator.py[A151]、workflow_service.py[A163]、client.py[A213]、kms.go[A181]

未解析路径的条目（A155 A83 等 7 条）需先从台账原文取路径再归桶。

## 第三波（后端 medium / low / latent / untagged + infra，已排除前两波占用文件）
- W3桶1（8 条）：env.py[A211/medium A217/low]、capability_matrix.py[A300/low]、package.json[A247/low]、webhook.py[A170/low]、app_output_proxy.py[A105/latent]、federation.py[A282/latent]、policy_bundle.py[A125/latent]
- W3桶2（7 条）：gpu_tee_runtime.py[A132/latent]、k_anonymity.py[A131/latent]、k8s_client.py[A128/latent]、stream_audit.py[A127/latent]、field_mask.py[A149/latent]、pii_ner.py[A194/latent]、rev_imports2.py[A133/latent]
- W3桶3（4 条）：test_scenario_engine.py[A144/latent]、scopes.py[A0/untagged]、AuditRegistry.sol[A195/medium]、versions[A174/low]
- 未解析路径条目（7 条）与其余待补：见 `fix-queue.md` 的 `(unresolved)` 行（多为前端 CSS/文案簇，由前端修波处理）。

## 第四波 + 前端补波（29 路子代理的完整归属，A-编号级）
| 子代理 | 条目 |
|---|---|
| st_01a14f6c | A190 A191 A192 A45 A46 A47 A74 A86 A218 A206 A154 A207 |
| st_01a14f6d | A80 A81 A148 A8 A11 A10 A20 A68 A16 A27 |
| st_01a14f6e | A214 A79 A158 A169 A94 A159 A184 A96 |
| st_01a14f6f | A122 A123 A153 A137 A115 A71 A172 A72 |
| st_01a14f70 | A78 A84 A160 A286 |
| st_01a14f71 | A197 A205 A176 A177 A178 A175 A198（Go，仅静态门） |
| st_01a14f72 | A222 A223 A224 A226 A227 A231 A236 A246 A248 A249 |
| st_01a14f73 | A254 A269 A270 A296 A273 A250 A259 A262 A263 A266 A292 A293 A297 A251 A225 |
| A284（docker-compose.prod.yml） | 归 st_01a14f61，待其报完用 task_send 并回 |

> 冲突处置：session_state_machine.py(A122/A123) 与 network_policy.py(A20/A68) 原被分桶表拆到两桶，已并回单一归属；output_inspection.py 原被两路要求跳过，现明确归 st_01a14f6c。

## W3 实际派工
- st_01a14f74 = W3桶1（A211 A217 A300 A247 A170 A105 A282 A125）
- st_01a14f75 = W3桶2 潜伏/硬件披露（A132 A131 A128 A127 A149 A194 A133）
- st_01a14f76 = W3桶3（A144 A0 A195 A174）

## 并回队列（同文件的其余严重度，56 条）
见 `fix-leftovers.md`：按**归属人**分节；各子代理报完后用 `task_send` 把对应小节的 A-编号发给它，禁止另开子代理改同一文件。


## 接续登记（本机 kill_bash(all) 事故后）

| st_01a14f60 → **st_01a14f86** | 运维·scripts `scripts/**` | 跑满 46m56s 被本机 `The operation was aborted.` 中止（非我 kill，见 §工具限制第 8 条：本轮第二次） | 盘上半成品：`install-k3s.sh`、`remote-harness/{sync_tree,source_lane,fe_lane}.sh`、`gen-cube-stubs.sh`、`deploy/cube-node/preflight.sh` + 未验证测试 `tests/test_ops_scripts_gates.py`、`tests/test_cube_node_scripts.py`；**门读数尚未拿到，交付前不得计入已修** |
| st_01a14f61 → **st_01a14f8a** | 运维·k8s/helm/deploy(local+e2e-remote) | 跑满 52m09s `The operation was aborted.`（第三次，墙钟上限 ~50min 确证） | 半成品：`deploy/e2e-remote/*` 5 文件 + `deploy/local/local_deploy.sh` + 新增 `tests/test_deploy_manifest_gates.py`（未验证）；`k8s/**`、`helm/cds/**` 未动。已把范围切出 `scripts/**`(f86) 与 `deploy/cube-node/**`(f88) 防撞车 |
| st_01a14f5f → **st_01a14f88** | 运维·cube-node `deploy/cube-node/**` | 跑满 48m19s 同样 `The operation was aborted.` | 以盘上现状为基线继续；验收判据收紧为「bash -n 只是语法层，行为要么假环境真跑要么标仅部署可验」 |

**新增观测（n=2，同一失败文本）**：deep-high 子在 ~47–48 分钟处被统一中止（`The operation was aborted.`），与我 10-18 早段的 `kill_bash(all)` 自伤无关。⇒ 处置：接续作业以盘上 diff 为基线、门读数一律由我复跑，且**后续派工倾向拆小**。

| 中断作业 | 状态 | 接续作业 | 接管内容 | 我已亲自复跑的读数 |
|---|---|---|---|---|
| st_01a14f57 密钥面 | error (aborted) | **st_01a14f77** | A1 A13 A161 A106 A271 + A121 A138 A139 A140 + 新增 A320（`certificate_service.verify_chain` tz-naive ⇒ 500） | crypto 簇由其报 33 passed（我未复跑，待验）；`certificates.py` 盘上未改 |
| st_01a14f59 数据面门 | error (aborted) | **st_01a14f78** | A48 A49 A50 A51 + A56 A57 A58 | 两个文件盘上有半成品 |
| st_01a14f5a 开发沙箱 | error (aborted) | **st_01a14f79** | A52 A53 A54 A55 A91 A69 + (unowned) A80 A81 | `tests/test_cube_template_dist.py` **11 passed / PARENTVERIFY_F5A_RC=0**（旧 `.gate-c6-green.log` 的 RC=1 已被超越） |
| st_01a14f58 会话面 | **completed** | — | 已交付 A30 A31 A294 A24 A25 | 我复跑 `test_session_list_cursor_scoping.py` + `test_exec_stream_shared_volumes.py` ⇒ **12 passed / PARENTVERIFY_RC=0**；余项 A32 A33 A36 A37 A35 因**并发容量满**未能并回，待容量释放后 `task_send` |

文件归属变更：`app/services/certificate_service.py` → st_01a14f77；`app/services/cube_egress_audit.py`（含 A80/A81）→ st_01a14f79。
