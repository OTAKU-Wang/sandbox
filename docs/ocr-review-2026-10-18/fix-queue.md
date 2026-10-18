# 待修队列（自动生成自台账 + D 节已修清单）

总条目=318 已修=28 判为非缺陷=8 待修=282

| 域 | critical | high | medium-high | medium | low | latent | untagged | 合计 |
|---|---|---|---|---|---|---|---|---|
| backend | 19 | 84 | 28 | 61 | 18 | 14 | 1 | 225 |
| fe | 0 | 2 | 4 | 11 | 9 | 1 | 2 | 29 |
| plugin | 2 | 6 | 2 | 9 | 4 | 0 | 0 | 23 |
| infra | 0 | 0 | 0 | 2 | 2 | 0 | 0 | 4 |
| deploy | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 |

## backend / critical (19)

- **A1** `app/api/certificates.py` — `app/api/certificates.py` — 任意已认证用户可导出全部 SM2 私钥（critical）
- **A309** `app/api/data_resources.py` — `masking_applied` 无条件为 true ⇒ 脱敏预览弹窗在说谎（critical-honesty，**后端**）
- **A13** `app/api/kms.py` — `app/api/kms.py` — DEK 全生命周期对任意已认证用户开放（critical）
- **A12** `app/api/mpc.py` — `app/api/mpc.py:200-215` — 任意已认证用户可取回全部明文 Shamir 分片（critical）
- **A48** `app/api/sandbox_db.py` — `app/api/sandbox_db.py:262-276` + `secure_duckdb.py:123-133` — 合约策略靠 fire-and-forget 挂接，且未挂时 **fail-open**（cri
- **A49** `app/api/sandbox_db.py` — `app/api/sandbox_db.py:79/210` × `280-309` — 默认 `session_id` 永远挂不上策略，异常只进 debug（critical，与 A48 同根）
- **A30** `app/api/sandbox_sessions.py` — `app/api/sandbox_sessions.py:753` — 游标分页丢弃权限谓词 ⇒ 任何业务用户可枚举全平台会话（**critical，已端到端复现**）
- **A24** `app/api/session_stream.py` — `app/api/session_stream.py:269-273` — `_run` 里的 `db` 是**全局查找**，共享卷在流式执行里静默不挂载（critical，已实跑）
- **A110** `app/services/crypto_service.py` — 合约签名 payload 存在分隔符注入：purpose 里塞 `|purpose_scope:X` 与真的给出 scope 得到同一串字节（critical）
- **A91** `app/services/cube_template_service.py` — 模板上传对象名与预签名对象名不是同一个（critical，与 A69 是同一链路上的两处独立断路）
- **A161** `app/services/kms_service.py` — KMS 密钥库是进程内存，而 `storage_service`/任务码/工作区三类密文只存 `key_id` ⇒ 重启即永久解不开（critical，活路径）
- **A185** `app/services/model_watermark.py` — 水印写回对 numpy / 非连续 tensor **静默 no-op**，却报 `success=True`（critical，活路径，铁律“禁伪造”直接命中）
- **A111** `app/services/notebook_service.py` — 单元格真实输出对输出 DLP 不可见：DLP 看的是 base64 信封，明文解码后直落 notebook 并回前端（critical）
- **A129** `app/services/rag_embedding.py` — 空文本对所有引擎都返回 512 维零向量，索引声明的 dim 因此错误，不匹配时评分静默归零（critical，活路径）
- **A183** `app/services/remote_attestation.py` — 密钥下发的“远程证明门”验的是本地静态根自签的 quote，且测量白名单在这条路上恒为空（critical，活路径）
- **A116** `app/services/session_snapshots.py` — `CDS_SESSION_MAX_SNAPSHOTS=0` 让快照接口直接 500（critical，已实测）
- **A162** `app/services/tee_capability.py` — TEE 能力证据可由 PATH 上的任意同名文件伪造，对外发布 `tee_status="verified"`（critical，活路径）
- **A114** `app/templates/` — 管理端 5 个模板把服务端字段直接 innerHTML，全仓无 HTML 转义工具，token 存在 localStorage（critical）
- **A69** `cube_templates.py` — `POST /api/v1/cube-templates` 默认（同步）路径必然失败（critical）

## backend / high (84)

- **A84** `(unresolved)` — 卷级 `read_only` 可被客户端在挂载时推翻（high）
- **A103** `agent_service.py` — `agent_service.py:386` 用了 PEP 701 语法，py3.11 门上必为 SyntaxError（high，CI）
- **A155** `app/` — 网络策略引擎“只创建不执行”：三个执行钩子全仓零调用方，但审计里写着已强制（high，活路径）
- **A7** `app/api/api_keys.py` — `app/api/api_keys.py` — 轮找回读未加锁（high，与 revoke/PATCH 互为竞态）
- **A5** `app/api/audit.py` — `app/api/audit.py` — 监管接口的结论与文案自相矛盾（high）
- **A134** `app/api/audit.py` — 审计验真页前后端字段错配，永远显示“失败”，并丢弃诚实披露字段（high）
- **A14** `app/api/auth.py` — `app/api/auth.py:366-390` + `app/models/user_mfa.py:17-18` — MFA 可被单因素静默关闭（high）
- **A2** `app/api/compute.py` — `app/api/compute.py::_check_resource_override` — 规格上限校验 fail-open（high，违背注释里的铁律）
- **A6** `app/api/connectors.py` — `app/api/connectors.py` — 列宽 8 写入 15 字符，PG 上注册接口必 500（high）
- **A19** `app/api/contracts.py` — `app/api/contracts.py:184-215` — 激活无锁 ⇒ DP 预算重复分配（high）
- **A3** `app/api/data_pipeline.py` — `app/api/data_pipeline.py` — 上传文件名未净化导致越界写（high）
- **A165** `app/api/data_pipeline.py` — `unstructured_pipeline`（OCR/ASR/视频/DICOM）三条 LIVE：路径零约束、残留明文、DICOM 脱敏只做顶层（high，活路径）
- **A53** `app/api/dev_sandbox.py` — `app/api/dev_sandbox.py:156-172` — 资源不可用时静默跳过，仍返回 201（high，踩 honest-degradation 铁律）
- **A54** `app/api/dev_sandbox.py` — `app/api/dev_sandbox.py:165` — 解密后的明文副本永不回收（high）
- **A4** `app/api/field_exposure.py` — `app/api/field_exposure.py` — 拒绝意图被反转成全量批准（high）
- **A15** `app/api/k8s_ops.py` — `app/api/k8s_ops.py:33` — 少一个 `await`，整个 ops 实机视图永久失效（high）
- **A29** `app/api/open_api.py` — `app/api/open_api.py:54-61 / 68-74` — 审计写入与业务提交互相绑死（high，且违反本仓已写下的契约）
- **A168** `app/api/open_api.py` — OpenAPI 端点配额 `calls_used` 是“读-改-写”，竞态窗口 = 整次沙箱执行（high，活路径）
- **A21** `app/api/output_control.py` — `app/api/output_control.py` — 不传 session_id 即同时绕过授权与合约上限（high）
- **A26** `app/api/rag.py` — `app/api/rag.py` — 事件循环阻塞与无上限缓冲（high，四条均命中代码）
- **A50** `app/api/sandbox_db.py` — `app/api/sandbox_db.py:223` — `json_path` 由请求体控制，服务端直接读（high，已闭环：中权限任意文件读）
- **A51** `app/api/sandbox_db.py` — `app/api/sandbox_db.py:527-542` — `GET /sessions/{id}/encryption-config` 仅 `get_current_user` 就返回明文 DEK hex（hi
- **A28** `app/api/sandbox_nodes.py` — `app/api/sandbox_nodes.py:222/271` — 向 unique 列 `dedup_key` 无条件 INSERT，把整个后台清理循环卡死（high）
- **A31** `app/api/sandbox_sessions.py` — `app/api/sandbox_sessions.py:1770-1797` — `contract_active` 把「没有合约」当成「合约失效」，无合约会话永久 410（high）
- **A294** `app/api/sandbox_sessions.py` — 输出审查的脱敏披露在跳源部署下会静默变成“已通过”（high，碰项目铁律 honest degradation）
- **A22** `app/api/training.py` — `app/api/training.py` — 检查点反序列化缺防护（high，附边界）
- **A167** `app/api/training.py` — 水印作业：`key_seed` 无上限→ int4 溢出，**在 checkpoint 已改写之后**才 500（high，活路径）
- **A40** `app/core/config.py` — `app/core/config.py:511-519` — 节点 gRPC 明文回退并未 fail-closed（high）
- **A141** `app/core/config.py` — KMS 传显式 policy 静默绕过“生产必填”的度量白名单（high；时效与绑定另有归属）
- **A42** `app/core/database.py` — `app/core/database.py:53-115` — SyncDbBridge：健康检查永远“成功”、失败后永久停在未验证态、close 泄池（high，ocr 三条均成立）
- **A43** `app/core/logging.py` — `app/core/logging.py:42-59` — 生产 JSON 模式下所有 stdlib 日志被剥成裸消息，structlog 那套是死代码（high，比 ocr 说的更重）
- **A38** `app/core/redis.py` — `app/core/redis.py:36` — 含密码的 REDIS_URL 直写 INFO 日志（high）
- **A75** `app/main.py` — 持久队列的归属守卫缺失（high 语义，但**非活路径**）
- **A120** `app/models/alert.py` — 唯一约束上的 check-then-act：并发重 key 会把整批告警/审计带下（high）
- **A203** `app/models/data_resource.py` — 四处“体积”列用 4 字节 `Integer`，而 PG 上 `>2,147,483,647` 字节（约 2.1 GB）写入必报 `IntegerOutOfRangeError`（high，活路径，我亲验）
- **A124** `app/models/federation_trust.py` — 联邦信任：模型缺“同一空间对只允许一条有效行”约束，装载又无 ORDER BY（high，与 A85 叠加）
- **A121** `app/models/kms.py` — KMS 密钥生命周期的四处“写了不读/说了不做”（high）
- **A138** `app/models/kms.py` — `key_distributions` 全仓无写入方 ⇒ 启动就跑的 TTL 清理循环是空转（high，活路径）
- **A89** `app/services/auth_service.py` — 公开硬编码 JWT 密钥在 DEBUG 形态下签发全部令牌（high）
- **A97** `app/services/code_scanner.py` — 危险内建判定不分属性名与内建名：合法代码被 FATAL，而 `io.open`/`getattr` 绕过（high）
- **A147** `app/services/contract_fulfillment.py` — 合约履行：容器已拉起后的其他异常只记进 errors，容器泄漏、会话停在非终态（high，活路径）
- **A93** `app/services/contract_service.py` — 合约锈定走的是“永远上不了链”的本地块，写库的 tx_hash 重启即不可验（high）
- **A101** `app/services/cpu_trainer.py` — LoRA 在优化器之后才注入，训练是静默 no-op（high，活路径）
- **A113** `app/services/crypto_service.py` — crypto_service 的“软件回退”是同一个调用的重复；generate 分支只记日志不回退（high）
- **A90** `app/services/cube_runtime_adapter.py` — `terminate()` 用 cubelet 的 sandbox_id 删会话卷 ⇒ 卷永久孤儿（high）
- **A145** `app/services/dp_budget.py` — DP ε 预算缓存永不失效，两副本各花各的额度（high，活路径）
- **A150** `app/services/federation_connector.py` — 联邦动态信任门的阈值表用 `.get(operation, 0)`，而 operation 由请求体决定（high，活路径）
- **A164** `app/services/gateway_service.py` — `gateway_metering` / `sandbox_activity_events` 有写入有查询但全仓无建表 DDL ⇒ 计量静默归零（high，活路径）
- **A139** `app/services/kms_service.py` — `import_wrapped` 对永远解不开的 blob 返回 True 并记“已恢复”（high，活路径：启动期）
- **A186** `app/services/model_watermark.py` — 选位密钥 `key_seed` 明文入列且原样回给 API 调用方（high，活路径）
- **A142** `app/services/mpc_service.py` — MPC 轮换先提交“删除旧分片”，再生成新分片（high，条件触发即密钥永久不可恢复）
- **A112** `app/services/notebook_service.py` — 驱动已执行但信封解析不到时，同一单元格被再执行一遍（high，非幂等副作用放大）
- **A189** `app/services/output_inspection.py` — DLP 命中的**原值**写进 `findings.samples` 并随 `security_report` 回给调用方（high，活路径，二次泄露）
- **A191** `app/services/output_inspection.py` — 输出报告的 SM2 签名密钥是**进程内随机生成**，不可抵赖性不成立（high，活路径）
- **A192** `app/services/output_inspection.py` — DP 阶段既谎报 `True` 又不登记 `not_evaluated`；无预算也计 `dp_applied=True`（high，活路径）——**与 2026-10-16 拍板的边界已写明**
- **A193** `app/services/output_inspection.py` — SM2 签名失败时用 SM3 摘要顶替并记 `stage_results[SIGNATURE]=True`，而 `passed` 是 `all(stage_results.values())`（high，活路径）
- **A95** `app/services/policy_evaluator.py` — 查询期策略门用的是“只看 5 项”的那套 PDP，带字段 ACL/掩码的那套无人导入（high）
- **A146** `app/services/quota_manager.py` — 网关配额：读不到限额当“无限制”，拒绝的请求照样计数，Redis 抖动永久转本地（high，活路径）
- **A130** `app/services/retention_janitor.py` — 保留期工头：计数拿 SELECT 条数、单表异常后 PG 事务已 aborted（high，活路径，当前 DRY_RUN 兜住）
- **A108** `app/services/sandbox_manager.py` — 资源限额只钳上限：0/负数原样采纳，字符串抛 TypeError，不传时返回共享字典（high，已实测）
- **A109** `app/services/sandbox_manager.py` — 租户配额是进程内 dict 的 check-then-act，多副本互不可见，写入是整快照覆盖（high）
- **A126** `app/services/secure_checkpoint.py` — secure_checkpoint 根本没有落盘：进程内字典 + 每次启动随机主密钥 + 无容量上限（high，活路径）
- **A117** `app/services/session_snapshots.py` — 回滚是“先全删再解包”，失败不原子、无备份（high）
- **A136** `app/services/session_templates.py` — session_templates 配置形状错→会话创建接口 500（high，活路径；本条原标“潜伏”已推翻）
- **A104** `app/services/tlcp_service.py` — 证书 DER 构造族：产出的 PEM 在 TLCP 活路径上（high）
- **A152** `app/services/vision_pipeline.py` — 两套视觉脱敏步骤都是假脱敏：不动像素却把 `is_redacted` 置 True（high，潜伏；接线即隐私泄露并违反 honest degradation）
- **A241** `app/static/js/admin.js` — 遗留 `/admin/*` 页面全是 `innerHTML` 直接插 API 数据，没有任何转义（high，**门后可达**：默认关、生产启动拒绝，我亲验整条链）
- **A83** `app/templates/` — 整个旧管理页面无 HTML 转义层（开发形态 high / 生产不可达）
- **A287** `app/templates/connectors.html` — 遗留管理台三个主要写操作全部接错契约（high，功能够不着而不是风格）
- **A70** `data_resources.py` — 上传先全量进内存再校大小（high，TRUE）；文件名的“路径穿越”那一半不成立
- **A60** `federation_connector.py` — `federation_connector.py:690-710` × `federation.py:27` — 未校验的 `endpoint` 构成带身份令牌的 SSRF（high）
- **A61** `federation_connector.py` — `federation_connector.py:710` + 两个调用点 — 同步 `httpx.Client` 直跑在 `async def` 里（high）
- **A143** `mpc_service.py` — MPC 实现族：分片静态密钥、列表即解密、holder 无序、空指针、32 位主键（high）
- **A199** `plugin.go` — `volume_id` 在 RPC 边界零校验，`paths()` 是 VolumeRoot 约束的唯一汇聚点且未净化；`cubeboxID` 放行 glob 元字符并静默取 `matches[0]`（high，子代理逐
- **A200** `plugin.go` — 销毁语义四处违反「销毁即不可恢复 / shred 失败要报错」（high，子代理逐条复验，我未复验）
- **A201** `plugin.go` — 自定义 AAD 参与 Seal 但从不写回 `Manifest.AAD`⇒该卷永久无法 Attach（high，数据不可用，子代理逐条复验，我未复验）
- **A242** `res.json` — 遗留管理台与后端契约已经对不上：四类契约脱节 + 内联事件注入 + 所有 DELETE 成功都会被报成失败（high，同样门后可达，我逐条读到盘上）
- **A204** `rootfs.go` — 三条互相咬合的构建期断链：镜像没有 CA 根⇒https KMS 必炸；国密后端被唯一自动化门静默跳过；libdir 两套口径（high，①② 我实测/亲验）
- **A57** `sandbox_db.py` — `sandbox_db.py:129-133` vs `151-156` — 脱敏视图不是会话拥有者的强制边界（high）
- **A156** `sandbox_runtime.py` — 网隔隔离链上五处独立 fail-open（接通即全量失效，high，条项依赖 A155 的接通）
- **A32** `sandbox_sessions.py` — `sandbox_sessions.py:1683-1697` × `session_files.py:41-50` — 文件名拼进 guest 内执行的 Python 源码，绕过 code_scanner 闸门（hig
- **A33** `sandbox_sessions.py` — `sandbox_sessions.py:560/597/635` — 回滚路径上直接调同步 `runtime.terminate`（high，同一文件自己已写下理由）
- **A82** `sandbox_task.py` — 僵死的 running 操作会永久锁住回滚（high，两条事实相乘）
- **A118** `session_snapshots.py` — 同一删除循环漏 symlink：非空目录链接残留、空目录链接直接抛异常（high，安全）

## backend / medium-high (28)

- **A35** `(unresolved)` — `_l2c_file_op` 四个 op 对 exec 结果的处理不一致（medium-high）
- **A98** `(unresolved)` — 白名单含 io/pathlib/pyarrow/duckdb/sqlalchemy，“白名单即闸门”的自述不成立（medium-high）
- **A226** `(unresolved)` — 三列定宽 360px 只在 ≥**1488** 成立，但断点写的是 `max-width: 1439px` ⇒ 1440–1487 整档横向溢出（medium-high，我亲验 + 算术，未跑真机）
- **A248** `(unresolved)` — 流式终端的两个独立生命周期缺陷：开连竞态与 `disabled` 后连接不关（medium-high，我逐行读过 effect）
- **A250** `(unresolved)` — 卡片可以一边贴「已发布生产」一边提示「没有生产版本」（medium-high，口径我自己对过两个函数）
- **A262** `(unresolved)` — 互信关系缺 `created_at` 时用「现在」兑底 = 编一个审计时间（medium-high）
- **A52** `app/api/dev_sandbox.py` — `app/api/dev_sandbox.py:163-170` — 同步「对象存储读 + 解密 + 落盘」直跑在事件循环上（medium-high）
- **A18** `app/api/monitoring.py` — `app/api/monitoring.py:275,503` — 同一个 AsyncSession 上 `asyncio.gather`（medium-high）
- **A45** `app/core/security.py` — `app/core/security.py:65-82, 132-146` — 未验签的 JWT `sub` 单独成桶，命中就**不再查 IP 桶**（medium-high）
- **A171** `app/models/watermark_record.py` — `watermark_records.job_id` 无唯一约束，但两处用 `scalar_one_or_none()` 读 ⇒ 并发后永久 500（medium-high，活路径）
- **A157** `app/services/agent_service.py` — agent `http` 工具的会话出网白名单只管 hostname，端口/方法/超时不收敛（medium-high，活路径）
- **A92** `app/services/auth_service.py` — 口令哈希在事件循环里同步跑 bcrypt（medium-high，登录主路径）
- **A102** `app/services/cpu_trainer.py` — DP-SGD 噪声尺度少除 batch_size，且隐私/梯度字段自“配置”而非“实际”（medium-high）
- **A255** `app/services/crypto_service.py` — 合约签名原文用 `|` 直接拼接且不转义不限长（medium-high，两边源码都对上了）
- **A135** `app/services/data_processing.py` — `ET.parse` 直接解用户可控的 XML，无实体膨胀防护（medium-high，活路径）
- **A148** `app/services/merkle_service.py` — `_sm3_fallback_allowed()` 在任何异常时返回 True，把生产诚实门翻成 fail-open（medium-high）
- **A187** `app/services/model_watermark.py` — 空位集 / 重复层名 / 零长度水印被当作“验证通过”（medium-high，活路径）
- **A190** `app/services/output_inspection.py` — 重构检测按**位置**拼元组比较，改列序 / 改数值字面量即零成本绕过（medium-high，活路径）
- **A151** `app/services/trust_evaluator.py` — `can_access()` 对不认识的信任等级放行（medium-high，潜伏；原先叠加的第二半已实测推翻）
- **A163** `app/services/workflow_service.py` — `run_test` 无状态/环境前置，会把已发布的生产态工件打回 TESTING 并解绑测试证据（medium-high，活路径）
- **A213** `client.py` — SDK 重试不区分幂等性：对 POST 也重发（medium-high，活路径，我亲验）
- **A63** `federation_connector.py` — `federation_connector.py:355` × `:327` — 重启后 `trust_id` 不再解析，**吊销开关静默 404**（medium-high）
- **A140** `kms_service.py` — `_ensure_kek` 无锁 check-then-act，软件 HSM 会用同一个 id 覆写主密钥（medium-high）
- **A181** `kms.go` — `ref`（= 控制面返回的 `key_id`）未校验就拼进 URL 路径（medium-high）
- **A56** `sandbox_db.py` — `sandbox_db.py:18/70/134` × `secure_duckdb.py:992-1005` — TTL 只清 `_engines`，`_engine_owners` 没人清（medium-high）
- **A36** `sandbox_sessions.py` — `sandbox_sessions.py:1125-1138` — 策略更新先拆后建且无补偿（medium-high，A20 同族但发生在活跃会话上）
- **A37** `sandbox_sessions.py` — `sandbox_sessions.py:1870/1883` — 会话文件上传无字节上限（medium-high）
- **A119** `session_snapshots.py` — `except TypeError` 后退回**不带 filter** 的 extractall（medium-high）

## backend / medium (61)

- **A80** `(unresolved)` — 水印验证丢弃了 `bits_per_channel`（medium）
- **A99** `(unresolved)` — 保存路径只认字面量且用 startswith：变量路径全 FATAL，`/sandbox/output/../..` 反而通过（medium）
- **A100** `(unresolved)` — SQL 扫描按分号切段取首词：`WITH … DELETE … RETURNING` 在“只允许 SELECT”模式下放行（medium，当前引擎不可利用）
- **A209** `(unresolved)` — `@media (max-width:1280px)` 的覆盖被自己的 `nth-child(3)` 特异性压住 ⇒ 换行后第三列不按同一基准对齐（medium，自适应链路，我逐行亲验）
- **A220** `(unresolved)` — 顶栏折叠到移动端时只重置了 `height`，`line-height: 64px` 还在 ⇒ 品牌字换行后每行高 64px（medium，移动端可见，我亲验）
- **A222** `(unresolved)` — 开发环境页整份样式零断点，两栏高度写死 `calc(100vh-250px)` + `min-height:700px`（medium，我亲验）
- **A223** `(unresolved)` — 全文件零 `:focus-visible`，而图标按钮自己把 `border/background` 抹平了（medium，我亲验）
- **A224** `(unresolved)` — 「状态未知（接口未接入）」与「已关闭」在 UI 上像素级同色（medium，撞 honest-degradation 铁律，我亲验）
- **A229** `(unresolved)` — 日期区间两个输入框定宽 55px，选完值之后文字被裁（medium，我亲验行号）
- **A231** `(unresolved)` — 整屏零断点 + 三处定宽/定尺寸，最钢的是弹窗 `width: 720px !important` 且全文零 `max-width`（medium，我亲验）
- **A233** `(unresolved)` — 状态标签与表内链接的可达性（medium，对比度我自己算的，数字与 ocr 不同）
- **A246** `(unresolved)` — `/open/v1/${slug}` 不校验不编码（medium，我亲验）
- **A249** `(unresolved)` — 审计详情对 `record.action` 直接 `.endsWith`（medium，叠 A244 就是白屏）
- **A264** `(unresolved)` — 缺 `exit_code` 的执行：日志行记「成功」，状态芯片记「失败」（medium，两个函数逐行对过）
- **A266** `(unresolved)` — 首页两处：`task_filters` 三连解引用 + `失败 ${ops.failures}` 会渲染出 undefined（medium）
- **A286** `(unresolved)` — `allkeys-lru` + 256mb 上限会让 Redis 驱逐会话与限流计数（medium）
- **A297** `(unresolved)` — 数据面横幅绕过 services 边界，而这个绕过就是 A243 的成因（medium，修 A243 的正确入口）
- **A317** `(unresolved)` — 数据产品台账只显 20/N 行且无说明（medium）
- **A318** `(unresolved)` — `trust_level` 健度里硬编码 `延迟: 0 ms` 当测量值展示（medium，命中禁伪造）
- **A8** `app/api/data_products.py` — `app/api/data_products.py:115` — 运算符优先级把显式入参吃掉（medium，一行可修）
- **A11** `app/api/deliveries.py` — `app/api/deliveries.py:865`（medium，性能+竞态，同族多处）
- **A55** `app/api/dev_sandbox.py` — `app/api/dev_sandbox.py:93/178` + `data_product_sandbox.py:134-149` — 客户端自选 `sandbox_level`，合约条款不参与（medium）
- **A64** `app/api/federation.py` — 联邦整条链零审计（medium）
- **A256** `app/api/field_exposure.py` — `approved_fields` 传空数组 = 批准全部申请字段（medium，后端权威方为真，当前 UI 不可达）
- **A10** `app/api/inference.py` — `app/api/inference.py:49` × `app/services/inference_service.py:31` — 格式白名单与失效的 fail-closed 闸门（medium，含潜在 RCE 面
- **A20** `app/api/network_policy.py` — `app/api/network_policy.py:69-104` — 唯一约束上的 check-then-insert + 引擎先于提交（medium）
- **A25** `app/api/session_stream.py` — `app/api/session_stream.py:88-93` — 一次性票据消费非原子（medium，ocr 定 high 偏高）
- **A16** `app/api/users.py` — `app/api/users.py:21-27` — 全员名册对任意登录用户可读（medium）
- **A27** `app/core/cache.py` — `app/core/cache.py` — 失败吐掉 + glob 未转义（medium，ocr 定 high 偏高）
- **A46** `app/core/security.py` — `app/core/security.py:122-130` — `_local` 从不删空桶键（medium）
- **A47** `app/core/security.py` — `app/core/security.py:183-192` — 体积限制对 chunked 请求形同虚设（medium）
- **A214** `app/models/mpc_key.py` — 模式层约束/索引重复与缺失族（medium，子代理逐条复验，我抽验 E2-37）
- **A79** `app/schemas/user.py` — 用户名正则用 `$`，尾随换行可通过（medium）
- **A158** `app/services/agent_tools.py` — 工具注册表的授权字段全是装饰：`roles`/`budget_cost`/`network_targets` 无人消费（medium，活路径）
- **A9** `app/services/capability_matrix.py` — `app/services/capability_matrix.py:352-354` — `l2c_planned` 恒为 False 的死逻辑（medium）
- **A169** `app/services/delivery_service.py` — webhook 派发窗口会被“已暂停订阅”的滞留行卡住（medium，活路径）
- **A94** `app/services/hsm_adapter.py` — HSM 回退门的默认值与默认异常方向（medium）
- **A159** `app/services/memorization_check.py` — 记忆化检测与 MIA 探针分母口径不一致，`risk_level` 与可配阈值脱钩（medium，潜伏）
- **A184** `app/services/mutual_tls.py` — `mutual_tls` 签发侧无条件写 `space_id`、校验侧只在真值时写 ⇒ 空 `space_id` 的证书永远验不过（medium，潜伏）
- **A96** `app/services/sandbox_security.py` — 会话密钥与 DEK 走 argv 注入（medium，仅 L3/bwrap 可达）
- **A122** `app/services/session_state_machine.py` — `get_allowed_transitions` 直接返回转换表内部持有的 set（medium，别名地雷，与 A108 同族）
- **A153** `app/services/task_circuit_breaker.py` — 熔断器的失败窗口衰减分支不可达，`failure_window_seconds` 完全失效（medium，潜伏）
- **A166** `app/services/trust_evaluator.py` — `evaluate_trust_score` 的整型截断会真的翻转授权（medium，活路径）
- **A137** `app/static/css/admin.css` — 旧后台样式：fixed 侧边栏无滚动、无响应式、暗色对比度不足（medium）
- **A115** `app/templates/alerts.html` — 告警页“今日”统计用 UTC 日期与本地时区展示比较（medium）
- **A71** `app/utils/crypto.py` — `_sm3_fallback_allowed` 异常方向 fail-open（medium）
- **A172** `app/utils/watermark.py` — `utils/watermark.py` 图片水印：`bits_per_channel` 只写不读，验证器永远按 1 bit 解（medium，今天是哑弹）
- **A72** `catalog.py` — 目录会同时列出一产品的多个已发布版本（medium，业务正确性）
- **A81** `cube_egress_audit.py` — egress 审计入库无 IntegrityError 兑底（medium）
- **A85** `federation_connector.py` — `_persist_trust` 只 add 不 upsert，重复建信任堆积多行（medium）
- **A182** `kms.go` — HTTP 错误把原始响应体原样拼进 error（medium，附对 ocr 的修正）
- **A66** `main.py` — `/api/v1/flow` 未进 GROUP_PREFIXES（medium，SS-15 机要面全量 403）
- **A188** `model_watermark.py` — 水印读写的性能项同一根因：每读 1 bit 就整张量 `_flatten`（medium，活路径）
- **A68** `network_policy.py` — `network_policy` 端口维度 fail-open（medium，API 可达）
- **A202** `plugin.go` — 内存卫生与可用性小项簇（medium，子代理逐条复验，我未复验）
- **A160** `resp.json` — `opa_client`：评估链潜伏，但 shutdown 后会重建客户端（medium，潜伏为主）
- **A65** `scopes.py` — 默认拒绝对 key 的识别比路由器窄一个量级（medium，大小写不同的 `Bearer`）
- **A58** `secure_duckdb.py` — `secure_duckdb.py:385-401` — `close()` 不清 `_tables` / `_table_deks`（medium）
- **A205** `server.go` — 镜像与 Makefile 的部署面默认值（medium，同批 30 条里次一簇）
- **A123** `session_state_machine.py` — `REVOKED` 终端态不可达，且全仓无人写入（medium）
- **A77** `training.py` — 宿主侧真正会反序列化的是训练检查点（medium，潜在）

## backend / low (18)

- **A227** `(unresolved)` — 同一屏的 antd 内部类覆盖族（low，可维护性）
- **A230** `(unresolved)` — 本屏的令牌拄贝与结构耦合族（low，含两处 ocr 说大了）
- **A236** `(unresolved)` — 卡头标题改成 `display: flex` 作废了 antd 自带的省略（low，我亲验）
- **A238** `(unresolved)` — 注释与实现不一致两处，其中一处让设计好的 hover 态根本不存在（low，我亲验）
- **A239** `(unresolved)` — `auto-fill` 兼容档在窄端会溢出（low，结论同 ocr 但它的算术错了）
- **A240** `(unresolved)` — 全局样式的维护族（low，逐条 grep 过）
- **A251** `(unresolved)` — 注册成功后不校验响应字段就写登录态（low，ocr 定 high，我降级）
- **A263** `(unresolved)` — 按 id 取合约的 query 只挡 `null` 不挡空串（low）
- **A292** `(unresolved)` — 结论与披露字段来自两个端点，可同屏自相矛盾（low-medium）
- **A293** `(unresolved)` — `ErrorCard` 的按钮与告警两套口径（low）
- **A319** `(unresolved)` — 文案错字「把行」→「把关」（low，两文件）
- **A310** `app/api/data_resources.py` — `GET /data-resources/` 无分页，前端每页全量下载再 `slice`（low）
- **A271** `app/api/kms.py` — `normalizeKmsKey` 硬编码 `key_type: 'dek'` / `algorithm || 'SM4'`（low，当前不成立）
- **A300** `capability_matrix.py` — `mode` / `reason` 是死字段，而后端把“为什么是控制面档”已经算好了（low）
- **A312** `data_resources.py` — 删除接口有响应体但声明 `Promise<void>`，UI 无条件报「删除成功」（low）
- **A67** `main.py` — 中间件与 deps 的凭据优先级相反（low，自我伤害型 403）
- **A247** `package.json` — 两条 ocr 报成 high、实测要降级（low）
- **A170** `webhook.py` — 模型层的小额真实项（low）

## backend / latent (14)

- **A259** `(unresolved)` — 开发者菜单白名单命中即整节点放行（latent，当前靠一个命名约定在撑）
- **A105** `app_output_proxy.py` — 输出代理两条真值（潜伏：接线即 high）
- **A106** `app/api/certificates.py` — 证书链校验与撤销族（潜伏）
- **A282** `app/api/federation.py` — 远端空间标签靠 `name` 前缀 + `LIKE` 过滤，`%`/`_` 没转义（latent / low，我把自己推的危害调低了）
- **A125** `app/models/policy_bundle.py` — policy_bundle 模型层两条 high 随 A73 一起降级（潜伏）
- **A132** `app/services/gpu_tee_runtime.py` — GPU-TEE 双胞胎：模拟器的证明报告不披露 is_simulation（碰铁律）、auto 分支不回退、SM4 密钥长度标签错（潜伏）
- **A131** `app/services/k_anonymity.py` — k_anonymity 只校验首行的准标识符存在性，缺失值归入同一个等价类（潜伏，隐私门可假通过）
- **A128** `app/services/k8s_client.py` — k8s_client 的 RWX 默认 StorageClass 猜选与 exec 透传（潜伏）
- **A127** `app/services/stream_audit.py` — stream_audit 三处并发/隐私缺陷，但模块零生产导入方（潜伏）
- **A73** `contract_service.py` — 已撤销的策略包继续做授权决策（**已降级→潜伏**，见第二十九批“A73 定级修正”）
- **A149** `field_mask.py` — 三个未接线引擎共 38 条潜伏（潜伏）
- **A194** `pii_ner.py` — 潜伏族定档（latent，代码事实成立但无生产入口——接线前必修）
- **A133** `rev_imports2.py` — RLS 双子系统 `rls_engine` / `pg_rls_manager`（潜伏：子代理 36 条，我独立复验了可达性）
- **A144** `tests/test_scenario_engine.py` — scenario_engine 全部 11 条潜伏，接线即 critical（潜伏）

## backend / untagged (1)

- **A0** `app/core/scopes.py` — 前提事实：`require_group(...)` 对人类主体是 no-op（决定了下面多条）

## fe / high (2)

- **A314** `contractApi.ts` — `CreateContractRequest` 缺 `purpose`/`purpose_scope` ⇒ Gap A4 目的门对 UI 创建的合约永远空转（high）
- **A315** `contractApi.ts` — 清空「允许操作」会静默恢复后端默认 `read,analyze`（high）

## fe / medium-high (4)

- **A228** `AuditLog.tsx` — 详情弹窗挂在 portal 里，拿不到本屏令牌与色板 ⇒ 灰面板底色与标签配色整块丢失（medium-high，活路径，我逐行亲验；ocr 定 high，我下调一档）
- **A304** `cds-frontend/nginx.conf` — SPA 文档无 CSP（nginx 侧与后端侧都只给了别的头）（medium-high）
- **A208** `DevTaskCreatePage.tsx` — 步进器把边框/背景写死成常态值，又用 `!important` 掉掉 antd 的环 ⇒ 必填项校验失败时输入盒完全不变样（medium-high，活路径，我逐行亲验）
- **A265** `notebookWorkbenchModel.ts` — 审计 detail 的标量字段全量拼进工作台文本（medium-high，清单内容我读了）

## fe / medium (11)

- **A307** `App.tsx` — MainLayout 内无 `<Suspense>`，冷加载把页头+边栏一起换成 spinner（medium）
- **A258** `cds-frontend/AGENTS.md` — SM2 私钥由服务端生成并下发、明文入 localStorage；`signData` 带私钥上行但是死代码（medium，**ocr 两条 critical 我都降级**）
- **A219** `cds-frontend/src/index.css` — 进度条高度靠 `!important` 盖到所有 `.ant-progress-*`，注释与代码不一致（medium，我亲验；环形的部分是潜伏）
- **A305** `cds-frontend/src/main.tsx` — `#root` 为空时 `createRoot` 静默白屏（medium）
- **A313** `connectorApi.ts` — 连接器列表在前端被截到 100 条且游标丢失（medium）
- **A316** `ContractDetail.tsx` — 激活/终止失败吞掉后端原因（medium）
- **A235** `DevTaskDataPicker.tsx` — 分页带的「右对齐」判据其实没生效（medium，两路亲验）
- **A306** `Login.tsx` — 未认证跳登录丢了 `state.from`，登录后必回 `/`（medium）
- **A267** `notebookWorkbenchModel.ts` — 「时间未知的记录」被排成最新（medium，同一行代码可验）
- **A221** `Sidebar.tsx` — 灰态文字一族全部不过 WCAG AA（medium，我亲验用色点 + 自己算对比度）
- **A232** `TaskDetail.tsx` — 只读值盒与状态标签都没有溢出策略（medium，我亲验 JSX）

## fe / low (9)

- **A254** `antdReact19Patch.ts` — `antdReact19Patch.ts` 的两条都需要“容器复用”才成立 ⇒ 现阶段记潜伏（low）
- **A311** `api.ts` — 上传走全局 30s 超时而后端上限 500MB（low）
- **A295** `App.tsx` — 开发者落地页钉在旧 `/home`，而且测试把这个陈旧值当断言（low，属“假绿”家族）
- **A273** `cds-frontend/src/services` — 路径拼接不编码：全仓 112 处（low，类级判定，计数来自机器枚举）
- **A210** `DevTaskCreatePage.tsx` — 「配额列小字」靠 `nth-child(3)` 定位，背地里也打到了另一张卡的 `export_mode`（low，可维护性）
- **A237** `DevTaskCreatePage.tsx` — 本文件的债：全局作用域 + 测试钩子当选择器 + 两支品牌蓝（low，我逐条 grep）
- **A253** `MainLayout.tsx` — `trigger={null}` 把侧栏折回去就 Manual 展开不了（low，代码里自带权衡说明）
- **A225** `NotebookWorkbench.tsx` — 同文件卫生小项族（low，但 F-71 那一半是潜伏）
- **A234** `TaskDetail.tsx` — 本屏卫生族四条（low；其中 ocr 当现行缺陷的一条实测为潜伏）

## fe / latent (1)

- **A270** `routeCrumb.ts` — useCrumbSegments 两条降为 low（latent）

## fe / untagged (2)

- **A269** `antdLocale.ts` — antdLocale 两条降级为 low（一层脱壳 + 模块加载期无校验）
- **A296** `sandboxApi.ts` — 两个文件的其余条目（合并成一条，不刷编号）

## plugin / critical (2)

- **A179** `cds-volume-plugin/kms.go` — “生产传输是 mTLS”在代码里不存在：`NewKMSClient` 写死明文 `http.Client`，也不校 scheme（critical，活路径）
- **A196** `cds-volume-plugin/rootfs.go` — rootfs 读回路径跟随 guest 可控符号链接⇒宿主 root 任意文件读；FIFO/设备节点⇒挂死与 loop 挂载泄漏（critical，活路径，我逐行复验）

## plugin / high (6)

- **A175** `cds-volume-plugin/config.go` — helper 可执行文件默认是个相对名，等于把 DEK 交给 PATH 上的任意二进制（high）
- **A180** `cds-volume-plugin/kms.go` — `do()` 丢弃 `io.ReadAll` 错误且只把 `>=300` 当失败 ⇒ `RevokeDEK` 可谎报“密钥已销毁”（high，活路径）
- **A197** `cds-volume-plugin/server.go` — grpc 服务未设消息尺寸/并发上限/恢复拦截器，与 `MaxVolumeBytes=1GiB` 自相矛盾⇒>4 MiB 卷数据必失败（high，活路径，我逐行复验）
- **A78** `sdk/cds_sdk/__init__.py` — SDK 打包元数据错位（high，已用 tomllib 实测）
- **A74** `sdk/cds_sdk/stream.py` — SDK 把 recv 超时当“正常结束”（high，违反 honest-degradation）
- **A206** `sdk/pyproject.toml` — `readme = "README.md"` 写在 `[project.optional-dependencies]` 里 ⇒ SDK 打包直接失败（high，活路径，两个探针实测）

## plugin / medium-high (2)

- **A198** `cds-volume-plugin/layout.go` — “原子写”用固定 `path + ".tmp"` 且不 `fsync`，叠加无每卷互斥⇒manifest 并发互踩、残留 0400 tmp 让 Attach 永久失败（medium-high，活路径，我逐行复验）
- **A212** `sdk/cds_sdk/client.py` — SDK 的 `_request_with_headers` 漏了 `>= 400` 抛错（medium-high，活路径，我亲验）

## plugin / medium (9)

- **A176** `cds-volume-plugin/cipher.go` — `SubprocTimeout` 被 `int(Seconds())` 截成秒：小于 1s 的配置把插件整体打死（medium）
- **A177** `cds-volume-plugin/cipher.go` — `Cipher` 接口入口不校长度：密钥短了静默零填充、长了静默截断（medium，今天不可达，但这是“铁律”边界）
- **A178** `cds-volume-plugin/cipher.go` — helper 超时分支丢弃 `Kill` 错误后无条件 `<-done`（medium-low）
- **A76** `sdk/cds_sdk/client.py` — SDK `_parse_error` 对非 dict 的合法 JSON 抛 `AttributeError`（medium）
- **A87** `sdk/cds_sdk/client.py` — 任务创建把脚本原文放进 query string（medium，根因在服务端契约）
- **A88** `sdk/cds_sdk/client.py` — 非幂等 POST 参与 429/503 重试（medium）
- **A216** `sdk/cds_sdk/client.py` — SDK 把整段任务代码与 RAG 问句放进 POST 的 **query string**（medium，我亲验行号）
- **A86** `sdk/cds_sdk/stream.py` — WS 会话的两处错误契约缺口（medium）
- **A154** `sdk/pyproject.toml` — `readme` 被写进 `[project.optional-dependencies]`，变成一个幽灵 extra（medium，构建元数据）

## plugin / low (4)

- **A173** `sdk/cds_sdk/client.py` — SDK `_parse_error` 只接 `ValueError`，非 dict 的合法 JSON 直接 `AttributeError`（low-medium，活路径）
- **A215** `sdk/cds_sdk/client.py` — SDK 把标识符直接拼进请求路径，不做百分号编码（low，我亲验：全文件无编码调用）
- **A218** `sdk/cds_sdk/stream.py` — `stream_exec(timeout_seconds=…)` 不做校验，直接把 `ValueError` 丢给用户（low，我亲验行号，顺手修正了子代理的坐标）
- **A207** `sdk/pyproject.toml` — `sdk/pyproject.toml` 其余五条：依赖无上限、无 `py.typed`、版本双来源（low，打包卫生）

## infra / medium (2)

- **A211** `alembic/env.py` — `alembic/env.py:35` 把含 `%` 的密码再送进 ConfigParser 插值 ⇒ 带转义密码的库直接跑不了迁移（medium，活路径，我亲验调用点）
- **A195** `contracts/AuditRegistry.sol` — `contracts/AuditRegistry.sol` 无访问控制且锚定可被任何人抢先/污染（medium，潜伏：链未接线）

## infra / low (2)

- **A217** `alembic/env.py` — `ALEMBIC_COMPARE_TYPES` 只认字面 `"1"`：写 `true` / `yes` / `on` 都是**静默关掉**类型对比（low，我亲验）
- **A174** `alembic/versions` — 文档漂移：alembic 实际 head 已到 **0026**，AGENTS.md 与多处文档仍写 0021（low）

## deploy / high (1)

- **A284** `docker-compose.prod.yml` — OPA 在宿主上无认证可写策略 = 授权决策可被任意改写；但“启动时空策略”不是 fail-open（high，半真）

## 后端按文件聚合（给互斥修波用）

- `(unresolved)` (38) A35/medium-high A80/medium A84/high A98/medium-high A99/medium A100/medium A209/medium A220/medium A222/medium A223/medium A224/medium A226/medium-high A227/low A229/medium A230/low A231/medium A233/medium A236/low A238/low A239/low A240/low A246/medium A248/medium-high A249/medium A250/medium-high A251/low A259/latent A262/medium-high A263/low A264/medium A266/medium A286/medium A292/low A293/low A297/medium A317/medium A318/medium A319/low
- `app/services/output_inspection.py` (5) A189/high A190/medium-high A191/high A192/high A193/high
- `sandbox_sessions.py` (4) A32/high A33/high A36/medium-high A37/medium-high
- `app/api/sandbox_db.py` (4) A48/critical A49/critical A50/high A51/high
- `app/api/dev_sandbox.py` (4) A52/medium-high A53/high A54/high A55/medium
- `federation_connector.py` (4) A60/high A61/high A63/medium-high A85/medium
- `plugin.go` (4) A199/high A200/high A201/high A202/medium
- `app/api/sandbox_sessions.py` (3) A30/critical A31/high A294/high
- `app/core/security.py` (3) A45/medium-high A46/medium A47/medium
- `app/services/crypto_service.py` (3) A110/critical A113/high A255/medium-high
- `app/services/model_watermark.py` (3) A185/critical A186/high A187/medium-high
- `app/api/certificates.py` (2) A1/critical A106/latent
- `app/api/data_pipeline.py` (2) A3/high A165/high
- `app/api/field_exposure.py` (2) A4/high A256/medium
- `app/api/audit.py` (2) A5/high A134/high
- `app/api/kms.py` (2) A13/critical A271/low
- `app/api/training.py` (2) A22/high A167/high
- `app/api/session_stream.py` (2) A24/critical A25/medium
- `app/api/open_api.py` (2) A29/high A168/high
- `app/core/config.py` (2) A40/high A141/high
- `sandbox_db.py` (2) A56/medium-high A57/high
- `app/api/federation.py` (2) A64/medium A282/latent
- `main.py` (2) A66/medium A67/low
- `data_resources.py` (2) A70/high A312/low
- `app/templates/` (2) A83/high A114/critical
- `app/services/auth_service.py` (2) A89/high A92/medium-high
- `app/services/cpu_trainer.py` (2) A101/high A102/medium-high
- `app/services/sandbox_manager.py` (2) A108/high A109/high
- `app/services/notebook_service.py` (2) A111/critical A112/high
- `app/services/session_snapshots.py` (2) A116/critical A117/high
- `session_snapshots.py` (2) A118/high A119/medium-high
- `app/models/kms.py` (2) A121/high A138/high
- `app/services/kms_service.py` (2) A139/high A161/critical
- `app/services/trust_evaluator.py` (2) A151/medium-high A166/medium
- `kms.go` (2) A181/medium-high A182/medium
- `app/api/data_resources.py` (2) A309/critical A310/low
- `app/api/compute.py` (1) A2/high
- `app/api/connectors.py` (1) A6/high
- `app/api/api_keys.py` (1) A7/high
- `app/api/data_products.py` (1) A8/medium

文件总数(后端待修)=136