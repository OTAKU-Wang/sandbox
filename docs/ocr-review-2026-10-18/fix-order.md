排法依据三条，不是按严重度机械排序：**① 是否"任意已认证用户"就能触发**（横向越权优先，因为这类改动面最小、收益最大）；**② 静默失效优先于报错失效**（本仓铁律：宁可拒绝不可伪造，静默降级会直接污染合规结论）；**③ 同一处代码顺带修掉同族**。组内按"已实测/已端到端复现 → 纯静态读出"排。

## 第 0 组 · 止血：一行到几行，先合掉（合计 < 半天）

| 条 | 动作 | 代价 |
|---|---|---|
| A116 | `max(1, settings.SESSION_MAX_SNAPSHOTS)`，或把 0/负当"禁用"提前返回 | 1 行（已实测 500） |
| A108 | `validate_resource_limits` 补下限与类型校验 | 3 行（已实测 0/负数被采纳、字符串抛 TypeError） |
| A103 | `agent_service.py:386` 改写掉 PEP 701 嵌套 f-string | 1 行（CI py3.11 `compileall` 必红） |
| A15 | `k8s_ops.py:33` 补 `await` | 1 词 |
| A38 | `core/redis.py:36` 打日志前把 URL 的密码段替掉 | 2 行 |
| A41 | `is_expired()` 用 `datetime.now(timezone.utc)` | 1 行 |
| A6 | `connectors` 写入列宽 8 → 与模型一致（或改列） | 1 处 |
| A134 | `blockchain.html:74` 读 `result.verified`，并把 `backend`/`verification_note` 显示出来 | 2 行（监管页现在恒显"失败"） |
| A150 | `federation_connector.py:565` 把 `.get(operation, 0)` 改成未登记即拒（或取表内最大值） | 1 行（现在一个字符串就能关掉联邦信任门） |
| A136 | `session_templates.py:102` 改 `except Exception` 并逐项校类型 | 2 行（配置形状写错=建会话 500） |
| A154 | `sdk/pyproject.toml` 把 `readme` 移回 `[project]` | 1 行（现在多一个假 extra） |

## 第 1 组 · 鉴权与密钥面（critical 主体，"任意已认证用户"级）

A1（导出全部 SM2 私钥）→ A12（取回全部明文 Shamir 分片）→ A13（DEK 全生命周期）→ A51（`encryption-config` 只 `get_current_user` 就返回明文 DEK）→ A30（游标分页丢权限谓词，已端到端复现）→ A21（不传 `session_id` 同时绕过授权与合约上限）→ A4（拒绝意图被反转成全量批准）→ A89（DEBUG 形态公开硬编码 JWT 密钥）→ A14（MFA 可被单因素静默关闭）。

这一组几乎都是**缺 `require_roles` / 缺谓词**，不是逻辑重构；改完能一次消掉报告里近半数 critical。建议在同一个 PR 批次里做，便于用一份权限矩阵测试覆盖（本仓已有 `test_capability_matrix_consistency.py` 这类文档↔代码门可挂）。

## 第 2 组 · 密态门与策略面（静默失效，比崩更危险）

A111（单元格真实输出对输出 DLP 不可见，已复现）→ A48/A49（合约策略 fire-and-forget 且未挂时 fail-open，同根一起修）→ A95（带字段 ACL/掩码的那套 PDP 无人导入，查询期只跑"看 5 项"的那套）→ **A146（网关限额读不到就是 0，而 0 在调用方约定里等于"不限"；拒绝的请求照样计数且无 `decrby`）**→ **A145（DP ε 预算每进程缓存永不失效：多副本各花各的额度，`consume` 先减内存后写库且库失败被吞）**→ A151（`can_access` 不认识即放行，潜伏但是 1 行 fail-closed）→ A57（脱敏视图不是会话拥有者的强制边界）→ A110（合约签名 payload 分隔符注入，探针证实）→ A32（文件名拼进 guest 内执行的 Python 源码，绕过 code_scan）→ A97（code_scanner 属性名/内建名不分：`re.compile()` 被 FATAL，而 `io.open` 绕过）→ A24（`_run` 里 `db` 全局查找，共享卷静默不挂载，已实跑）→ A129（RAG 维度不一致时 cosine 静默返回 0.0，已复现链路）→ **A141（KMS 显式传 policy 使启动门校验的 `ATTESTATION_ALLOWED_MEASUREMENTS` 在这条路上从未被使用）**。

顺序原则：**先堵 fail-open，再修扫描器误杀**。A95/A97 都涉及"看起来有门、实际门不在链路上"，修完必须配一条"绕过即失败"的回归用例，否则下一轮重构还会掉。A146/A145 是同族的新型态：**门在，但门的输入（限额/预算）来自一个会静默失败的读取**。

## 第 3 组 · 合规可核验性（审计链与锚定）

A93（锚定走永远上不了链的本地块，写库 `tx_hash` 重启即不可验）→ A5（监管接口结论与文案自相矛盾）→ A121（`encrypted_key` 撤销后不清、`rotate_kek` 无调用方、KEK 轮换后所有 blob 解不开、Go `dekLen=16` 常量 vs Python 运行时配置）→ A130（保留期工头计数取 SELECT 条数、单表异常后 PG 事务 aborted）→ A120 / A28（唯一约束上的 check-then-act，重 key 把整批告警/审计带下、后台清理循环卡死）→ A29（审计写入与业务提交互相绑死，违反本仓已写下的契约）。

这一组的验收方式建议统一：**导出一条真实审计记录，独立重算 SM3 Merkle 路径并核对 `backend` 披露**；做不到就说明锚定仍是自证。

## 第 4 组 · 可用性与破坏半径

A59（未转义 LIKE 直接驱动 DELETE ⇒ 跨命名空间目录清空，破坏性最高）→ A60（未校验 endpoint 的带令牌 SSRF）→ **A147（合约履行：容器已拉起后的异常只进 `errors`，DB 回滚不回收容器 ⇒ 带数据通道的孤儿容器）**→ A117/A118（快照回滚"先全删再解包"且漏 symlink）→ A90/A84（`terminate()` 用错 id 导致卷永久孤儿；卷级 `read_only` 可被客户端推翻）→ A70（上传先全量进内存再校大小）→ A126（`secure_checkpoint` 零落盘 + 每次启动随机主密钥 + 无容量上限）→ A101/A102（LoRA 在优化器之后注入 ⇒ 训练静默 no-op；DP 噪声未除 batch 且 `dp_noise_injected` 是配置回声）→ A53/A54（资源不可用仍返回 201；明文副本永不回收）→ A82（僵死 running 操作永久锁住回滚）→ A26（事件循环阻塞与无上限缓冲）→ A112（信封解析不到时同一单元格再执行一遍）→ A19/A7/A22（无锁激活导致 DP 预算重复分配、轮找回读未加锁、检查点反序列化缺防护）。

## 第 5 组 · 先决策再动手（两套实现 / 配置在但没接线）

不是 bug 清单，是**架构裁决清单**：A95（两套 PDP）、A131（两套 k-匿名，reviewed 的那套零导入方）、A133（`rls_engine`/`pg_rls_manager` 整条链未接线）、**A152（`vision_pipeline` 与 `vision_multimodal_runtime` 两套假脱敏，都只被自己的单测导入，却把 `is_redacted` 置 True 并计入合规计数）**、**A151/A153（`can_access`、`task_circuit_breaker` 只被测试导入，一旦接线就是放行/误熔断）**、**A138（`key_distributions` 表全仓无写入方，启动即跑的 TTL 过期循环永远空转）**、A127（`stream_audit` 未接线）、A128（`k8s_sandbox`/`k8s_client` 在 legacy 门后）、A75（持久队列守卫缺失但非活路径）。~~A136 曾在本组，已改判为活路径 500，移入第 0 组。~~

每一条只有两种正确收场：**接线并补测试**，或**删掉并在 `docs/mock-remediation-plan.md` 登记为未实现**。保持现状的代价是每一轮评审都要重新判一次可达性（本轮为此建了 AST 探针，并且因它漏 `from PKG import SUB` 而入边而差点把 `session_templates`、`mpc_service` 误列为死代码）。

## 第 6 组 · 前端与旧后台

A114 / A83（5 个模板 innerHTML、全仓无 HTML 转义层、token 在 localStorage）、A137（fixed 侧边栏无滚动 / 无响应式 / 暗色对比度不足）。旧管理控制台按已定形态属"开发形态可达、生产不可达"，所以整族排最后；若决定继续用，第一件事是加一个 `escapeHtml` 工具并全量套用，而不是逐页修补。`cds-frontend/src`（w4）**尚未评审**，不要把这一组当成前端全貌。

## 第 7 组 · 本轮（w2 后半 + w3 边缘）新增，按同级严重度插进上面各组

**与第 1 组同级的 critical**：**A161（KMS 密钥库是进程内存，`storage_service`/任务码/工作区三类密文只存 `key_id` ⇒ 一次重启永久解不开，`storage_service.py:185-187` 的报错自己承认了根因）**、**A179（卷插件 `NewKMSClient` 写死明文 `http.Client` 且不校 scheme，注释与 AGENTS.md 却称生产走 mTLS ⇒ 跨节点部署时明文 DEK + 服务 token 上线）**、**A162（TEE 证据可由 PATH 上的同名文件/普通文件伪造 ⇒ 安全态势接口对外发布 `tee_status="verified"`）**。

**卷插件其余项（`cds-volume-plugin` 不在 `ci.yml`，`make test` 手动，无自动门兼顾）**：A175（helper 默认相对名 → PATH 查找，stdin 里递的是明文 DEK）→ A180（`do()` 丢 `io.ReadAll` 错误 + 只把 `>=300` 当失败 ⇒ `RevokeDEK` 可谎报「密钥已销毁」，与「revoke 失败必须保留数据」的铁律相反）→ A181（`ref` 未校验拼进 URL 路径）→ A176（`int(Seconds())` 把 <1s 的配置截成 0 ⇒ 每次 helper 调用立即超时，报错却说是超时）→ A177（`Cipher` 入口不校 key/nonce/tag 长度，短了零填充长了截断；今天靠 `kms.go:125` 钉住才不可达）→ A178（超时分支丢 `Kill` 错误后无条件 `<-done`）→ A182（HTTP 错误原样带上游响应体进日志与 gRPC）。

**后端新增（按原组归类）**：A164（`gateway_metering`/`sandbox_activity_events` 有写入有查询但全仓无建表 DDL ⇒ 计量静默归零）→ A165（`unstructured_pipeline`：`input_path` 零约束跨租户读 + 明文中间产物永不回收 + DICOM 脱敏失败写进无人读的 `partial_success`）→ A163（`run_test` 无状态/环境前置，可把生产态工件打回 TESTING 并解绑测试证据）→ A168（OpenAPI 配额读-改-写，竞态窗口＝整次沙箱执行）→ A167（`key_seed` 无上限，权重已改写之后才 500，水印记录被回滚）→ A171（`watermark_records.job_id` 无唯一约束却用 `scalar_one_or_none` ⇒ 并发后永久 500）→ A166（信任分整型截断翻转联邦准入）→ A169（webhook 派发窗口被暂停订阅的滞留行卡死）→ A157/A158（agent `http` 出网白名单只管 hostname；注册表 `roles`/`budget_cost`/`network_targets` 全是装饰）→ A172/A173（图片水印 `bits_per_channel` 只写不读；SDK `_parse_error` 遇非 dict JSON 崩成 `AttributeError`）。

**attest / privacy 回收新增（与第 1 组同级）**：**A183（远程证明门实为源码常量 `_LOCAL_ATTESTATION_ROOT` 派生的自签哈希；`kms_service.py:314` 自建策略绕过 prod 强制的测量白名单；模拟拒收门看自述 `type`）** → **A185（水印写回对 numpy/非连续张量静默 no-op 却报 success）** → **A189（DLP 命中原值进 `findings.samples`，随 `security_report` 回传）** → **A186（`key_seed` 明文列 + 接口原样回传，水印安全性 = 31-bit 种子）** → **A191（输出签名密钥进程内随机生成 ⇒ 不可抵赖不成立，同 A126 根因）** → **A193（SM2 失败时用 SM3 摘要顶替并记 `stage_results[SIGNATURE]=True`，`passed=all(...)` 计入；except 内的 `sm3_hash` 自己会抛 ⇒ 500）** → A192（DP 谎报 True 且不登记 `not_evaluated`；**修法只补披露，禁动 `should_block()`——2026-10-16 已拍板**）→ A190（重构检测按位置元组比较，改列序即绕过）→ A187/A188（空位集判通过、逐 bit 全张量复制）。

**edge 波次（卷插件）回收新增**：**A196（rootfs 读回跟随 guest 可控 symlink ⇒ 宿主 root 任意文件读；FIFO/设备节点 ⇒ 挂死 + loop 泄漏；critical，本轮首要项）** → **A200（Destroy 不清 staging 明文 / Detach 零校验即静默跳过 shred / shredFile 不 Sync 就 unlink / 丢 shred 错误仍报成功）** → **A201（AAD 参与 Seal 却不写回 manifest ⇒ 该卷永久 Attach 不进）** → **A197（`grpc.NewServer()` 无消息上限，与 `MaxVolumeBytes=1GiB` 矛盾 ⇒ >4 MiB 必失败）** → A199（volume_id 只在 CreateVolume 校验）→ A198（固定 `.tmp` 名 + 不 fsync + 无每卷互斥）→ A202（小项簇）。

**w3 构建面回收新增**：**A204（镜像无 CA 根 ⇒ https KMS 必 `x509 unknown authority` 且 KMS fail-closed ⇒ CreateVolume/Attach 全线不可用；`plugin_test.go:319-323` 缺 helper 就静默不测国密后端 ⇒ 唯一自动化门对 SM4-GCM 假绿；libdir 两套口径）** → A205（`VOLUME` 匿名卷 = 密文随容器重建永久丢失；KMS 两个安全参数的镜像默认值 = 空 token + 明文 HTTP 静默；0600 与 root:cds 声明矛盾在镜像侧；基础镜像无 digest pin）。

**SDK 打包面（一行修）**：**A206（`sdk/pyproject.toml:12` 把 `readme` 写进 `[project.optional-dependencies]` ⇒ setuptools 校验直接抛，本机实测 `pip wheel` 与 `pip install -e ./sdk`（= `ci.yml:34` 同一句）双双 RC=1；修法：整行移到 `[project]`，`sdk/README.md` 已确认存在）** → A207（依赖无上限、缺 `py.typed`、版本双来源 = A154 同一处）。

**前端（DevTaskCreate 一页三处）**：**A208（必填校验失败时步进器毫无反馈：`devTaskCreate.css:156-164` 常态硬编码 + `:192-198` `box-shadow:none !important` + 全文件零 `has-error` 规则，而 `DevTaskCreatePage.tsx:275/:289` 两条 required + `:155` 后端 422 回写是活路径）** → A209（`@media 1280` 被 `nth-child(3)` 特异性压住 + 全文件仅一颗断点，1281–1439 无兼容）→ A210（小字样式挂位置不挂语义）。

**alembic / SDK 尾巴（本轮新增）**：**A213（POST 也被重试 → 重复创建资源，`client.py:228-235`）** → **A212（`_request_with_headers` 漏 `>=400` 抛错，`:245-254`，仅 `:328` 一个调用点）** → **A214 里的 E2-37（`mpc_key_shares` 缺 `UNIQUE(key_id, share_index)`，同 x 坐标会让 Shamir 恢复拼出错的分片）** → A216（代码进 query string：日志泄露 + 行长上限）→ A211（`alembic/env.py:35` 的 `%` 插值炸迁移）→ A214 冗余索引族与 E2-64（`0018:30` 非 CONCURRENTLY 锁写）→ A215 / E2-12~17（low）。

**前端 CSS 第二轮**：**A226（`deliveryOpenapi.css:60-63` 三列定宽 360 ⇒ 按仓内定标注释反推，该布局只在 ≥1488 成立，而断点从 1440 才退档 ⇒ 1440–1487 档横向溢出；修法：断点抬 1490 或改 `minmax(0,1fr)`）** → A222/A223（开发环境页零断点 + 零 `focus-visible`）→ **A224（「未接入」与「已关闭」同色，撞 honest-degradation 铁律）** → A219/A220/A221（进度条 `!important`／移动端 header 行高／灰态一族不过 AA）→ A225/A227（卫生族）。
**前端 CSS 第三轮**：**A228（审计详情弹窗挂 portal，本屏令牌/色板整块失效：`--audit-head-bg` 丢灰面板 + `t-*` 标签配色无规则命中）** → A231/A232/A233（任务详情页零断点 + 弹窗 720px 无 `max-width` + 值盒无溢出策略 + 状态标签对比度 2.78/3.25/4.04 全不过 AA） → A229（日期输入 55px 裁掉真实值） → A230/A234（令牌拷贝、页面级 `.ant-alert` 通配、重复盒定义）。
**前端 CSS 第四轮（CSS 集收口）**：A238（`.cds-task-card` hover 蓝描边是空操作：基态 `border: none` ⇒ 只改 `border-color` 无效；另 `:380` 注释「高 36」vs `:385` `height: 32px`） → A239（≤1023 档 `auto-fill minmax(280px,1fr)` 在 ≲328 视口溢出，改 `min(280px,100%)`） → A235（分页带 `text-align:right` 在 flex 容器上无效 ⇒ 右对齐判据没生效 + mini 档箭头尺寸没同步） → A236/A237/A240（卡头 `display:flex` 作废省略、`data-testid` 当选择器 6 处、无背景令牌、`.ant-table` 圆角无裁切、`transition: height` 触发 reflow）。**前端逻辑类第一批（这批有 critical）**：**A243（数据面横幅永不渲染：`DataplaneBanner.tsx:27` 对已被 `api.ts:37` 拆过的响应又取一次 `.data` ⇒ honest-degradation 披露面静默失效）** → **A244（全站零 ErrorBoundary + `CapabilityMatrix.tsx:30` 对 `data.matrix` 无兜底 ⇒ 一个空字段整站白屏）** → **A245（`SimulationBadge.tsx:69-70` 先判 `includes('sim')` ⇒ `not_simulated` 被贴「模拟」，诚实披露失真）** → A246（`display.ts:227` slug 不编码） → A247（`errorHints` 原型链 / `test:coverage` 缺 `@vitest/coverage-v8`，均已降级）。**遗留管理台（门后可达，默认关、生产拒绝）**：A241（六张 Jinja 页 + `admin.js` 全用 `innerHTML` 插 API 数据，零转义 ⇒ 存储/反射型 XSS，ocr 在这一族报的 20 critical + 42 high 全部收敛到这条）→ A242（`del()` 不发 body ⇒ 证书吊销必 422、204 还误报失败；告警枚举 `active/firing` vs 后端 `open/acknowledged/resolved`；「已验证」只看字段有没有值、压根不调 `/verify`；`onclick="revokeCert('${cert_id}')"` 内联注入；token 存 `localStorage` 与 XSS 合成完整窃取链）。**前端逻辑类第二批**：**A248（流式终端：`socketRef` 守卫跨 await 失效 ⇒ 双开 WebSocket 且第一条关不掉；`disabled` 后 effect 不重跑 ⇒ xterm+连接留在已卸载的宿主上，与自述的 running-only 策略不一致）** → **A250（`devTasksModel.ts` 阶段集合不过 `env`、生产执行集合过 ⇒ 同一张卡同时贴「已发布生产」和「没有 published 的生产版本」）** → A249（`auditDisplay.ts:89` 对 `record.action` 无保护 ×A244 无边界 = 白屏）→ A251（注册不校验 token，已降级 low）。**前端逻辑类第三批（跨方签名与密钥链路）**：**A255（`crypto_service.py:215-219` 合约签名原文 `|` 拼接不转义，前端 `utils/crypto.ts` 逐字镜像 ⇒ 跨方可控字段能拼出同一原文）** → **A256（`field_exposure.py:287` 空 `approved_fields` ⇒ 全量批准，当前 UI 恒传全集故不可达，属 API 边界潜伏）** → **A257（`identityApi.ts:113/:195` 证书状态与验链 fail-open：解析失败判有效、缺 `valid` 字段判通过）** → A258（SM2 私钥服务端生成下发 + localStorage 明文 = AGENTS.md 容忍项；`signData` 带私钥上行但 0 调用者 = 死代码）。路径参数不编码 5 处（`mpcApi.ts:47/53/58`、`fieldExposureApi.ts:55`、`display.ts:227`）并入 A246 一条修。**前端逻辑类第四批**：**A260（`csvPreview.ts:63` 先按 `\n` 切行 ⇒ 引号内换行被拆两条记录、字段内空行被 `filter` 删；`:75` 表头同名静默覆盖）** → **A259（`workspace.ts:90` 白名单命中即整节点放行，不裁 children —— 当前靠“分组 key 不带斜杠”侥幸没穿，记 latent）** → A246 补编码点。证伪两条不修：`types/models.ts` 的 `allowed_operations`（后端本来就一 str 一 list）、`types/security.ts` 的三态（`display.ts:182/208` 已是 `=== true` / `!== true`）。**前端逻辑类第五批（含一条新的 critical）**：**A268（critical：`app/api/mpc.py:200-221` 读侧只有 `get_current_user`，返回 `share_value`+`index`+`threshold` ⇒ 任意登录用户可取全部分片离线重组；`:160-166 GET /keys` 同样无角色且 `list_keys(db)` 不收 user ⇒ 全平台密钥可枚举 —— 对照 `:228/:260` 写侧 `require_roles(ADMIN)`）** → **A264（缺 `exit_code`：日志行记成功、状态芯片记失败、还不计问题数）** → **A265（审计 detail 标量全量拼进工作台文本，`NOISY_DETAIL_KEYS` 是噪音清单不是敏感清单）** → **A261（合规报告 `anchoring` 后端可空/前端非空 + 消费点无保护 ⇒ 白屏）** → **A262（互信记录缺 `created_at` 就编一个当前时间）** → A267（无时间记录被排成最新）→ A266（首页 `task_filters` 解引用 + 「失败 undefined」）。降级不修：A269 antdLocale、A270 useCrumbSegments、A271 normalizeKmsKey（当前 API 形状下不成立）。**前端逻辑类第六批（高危层收尾）**：**A272（`AuditVerifyModal.tsx:177` `deriveVerdict` 收不到 proof 的 pending 态 ⇒ 审计验真在 Merkle 路径回来前先亮绿「通过」）**；证伪一条：`computeApi.ts` 的「超时+重试=重复执行」不成立（`App.tsx:63` 只给 `queries` 配 retry:1，mutations 默认 0 次）——残留只是「无幂等键」，定 low。`k8sOpsApi.ts:106`、`outputControlApi.ts:50/53` 并入 A246（现列 8 处）。**中低危首批（前端）**：**A274（`identityApi.ts` 同一文件三种解包：`:144` 正确 / `:157 getKeyAudit` 裸数组⇒静默空审计列表 / `:171 listCertificates` 包装对象⇒抛+无边界=白屏，抽 `unwrapList` 一处收）** → **A275（审计页导出窗口是死的：`EXPORT_RANGES` 0 引用、`setExportDays` 0 调用，Tooltip 却写「按所选时间窗口」并把 `GET /audit/export?days=…` 摊给用户看）** → **A276（同弹窗里导出按 `canExport` 禁用，锚定本条/存证验真任意角色可点）** → A277（toast 渲染「Merkle Root: undefined」/ `invalidateQueries` 从闭包取 `detailId` 失效错 key / `saveBlob` 无 try/finally 漏 Blob URL）→ A273（全仓 112 处路径未编码：类级 low，只修 A246 列出的可被用户输入控制的 5 处）。**中低危第二批（登录页）**：A278 —— `Login.tsx:38` 把 `JSON.stringify(后端错误结构)` 渲染给未登录访客（`detail` 可带内部路由/权限组名，例 `scopes.py:311`）→ 密码规则 `required`+`min:8` 共用一条 message（留空也提示「至少8位」）→ `:20-23` `/health` 能力预检强断言静默兜底 → `:29` 写态不校验（同 A251，不另开号）。自我证伪一条：`main.py:322` 的 `require_group("audit")` 对匿名/no-key 主体是 no-op（`scopes.py:295-307`），且登录页显式传 `baseURL: ''`，所以「/health 预检必 401」不成立。**中低危第三批（审计服务层，挖出一条后端）**：**A279（Merkle 叶子原文 `f"{id}|{action}|{resource_type}|{resource_id}|{created_at}"` 在 `app/api/audit.py:358/421/476/493` 重复 4 次且不转义 `|` ⇒ 跨字段边界歧义落在审计证据基座上；抽 `merkle_leaf_preimage()` 一处收，与 A255 同改）** → **A280（导出只看状态码：2xx 的 HTML 也会被当 `audit_export_30d.csv` 落盘；文件名/Content-Type 是前端复刻后端 `Content-Disposition` 的隐式契约）**。证伪：`anchor` 空数组不会全量/零锚定（`audit.py:352-353` 404），残留只是 `record_ids` 无长度上限（low）；非 Blob 错误其实原样 `throw`（`:191`），不是绕过。**中低危第四批（联邦面）**：**A281（`GET /federation/catalog/entries` 后端 `limit` 默认 100、无 offset、返回裸列表无 total，前端 `federationApi.ts:96` 不透传 ⇒ 目录被静默截到 100 条且 UI 无从分辨）** → A282（远端空间归属编码在 `DataProduct.name` 前缀 + `LIKE` 不过滤 `%`/`_` —— 因该端点本就无按空间授权且要 ADMIN/OPERATOR，我把自己推的「跨空间读取」调低为 latent/low）→ `remote_space_id || ''` 让信任表与同步状态表关联静默失效（归 A257/A262 改造）。**w6 运维面（compose/启动装配）**：**A283（prod compose 裸 `${VAR}` + 全端口绑 0.0.0.0 + Vault dev 模式：`.env` 漏项时 api 会因 `validate_security_config` 拒绝启动，但 PG/Redis/CH/MinIO/Vault 已以空密码在宿主网卡上听者 —— 改 `${VAR:?required}` + 删无需求 ports）** → **A284（OPA `8181:8181` 无认证可写策略 = 授权决策可被任意改写；已证伪 fail-open：无策略返 `{}` ⇒ `.get("result", False)` DENY，不可用时回落 `_evaluate_python`；另记 `:116` 注释与实现不符：每次评估都重推 rego）** → **A285（`CDS_EGRESS_AUDIT_LOG_PATH=/data/egress-audit/…` 与 `CDS_SHARED_VOLUME_ROOT=/data/shared-volumes` 未入卷，与 `:234-236` 注释自相矛盾 ⇒ egress 审计日志容器重建即丢）** → A286（`allkeys-lru`+256mb 驱逐会话/限流计数）。**w6 最终快照补判（三个写操作 + 两个构建/测试口径）**：**A287（遗留管理台三个主写操作 100% 不可用：`connectors.html:61` 的“恢复”永远打 `/suspend`，而后端另有 reactivate 路由；`kms.py:194-199` DELETE 要 `Body(...)` 而 `admin.js` 的 `del()` 不发体；`kms.py:64-68` `product_id` 被 FastAPI 当 query 而模板放 body）** → **A289（`tests/e2e_full_test.py` 会被裸 pytest 采集：`pyproject.toml:3` 无 `python_files` 覆盖 ⇒ 默认含 `*_test.py`，AGENTS.md 的“never collected”判错；12 条打硬编码集群 IP，但四处 requests 都带 timeout ⇒ 有界红，记环境缺失而非代码缺陷）** → **A288（`.dockerignore` 只排 4 项 ⇒ `COPY . .` 把 `.env*`/`.npmrc` 烤进 builder 层；同一 RUN 的 `for` 循环吞退出码被 `test -x` 挡住，降为 latent）**。**中低危第五批（存证验真面）**：**A290（`AuditVerifyModal.tsx:204` 与 `:208` 同层独立 ⇒ 重取失败时「验真请求失败」与上一轮绿色结论同屏，A272 的放大器）** → **A291（`:293` 只看 `proof_path.length===0` 就断言「本批次仅一条」，同响应里 `total_leaves`（`audit.py:519`）已在 `:291` 显示 ⇒ 应双条件；空路径+多叶子要说“证明数据不完整”）** → A292（`:180-186` 披露字段回退 proofData，而 `:177` verdict 只看 verifyData ⇒ 两口来源可矛盾）→ A293（`ErrorCard.tsx:45-46` canRetry/warning 两套口径）。证伪：`pg-log` 绿色不成立（徽标语义＝是否模拟，文案已写「未上链」，`SimulationBadge.tsx:41-56`）。**中低危第六批（下载/输出审查面，含一条 high）**：**A294（跨源部署下脱敏披露静默失效：后端只发 `passed|redacted; findings=N`（`sandbox_sessions.py:1956/1966`），`security.py:204-210` 的 CORSMiddleware **没有 `expose_headers`** ⇒ 跨源时 `parseReviewHeader` 拿到 null（`sandboxApi.ts:276`），而 `NotebookWorkbench.tsx:430` / `SessionDetail.tsx:525` 只对 `redacted` 报警、全仓没有第三态 ⇒ 内容已被脱敏而界面一声不响，破 honest degradation 铁律。修法两端各半：加 `expose_headers` + 前端把 `null` 当要披露的第三态）** → A295（`homeLandingPath` 仍返回 `/home`，`App.tsx:107` 只做重定向；`workspace.test.ts:83` 把陈旧值钉成断言 = 假绿家族）→ A296（`| string` 让联合类型退化、`FileDownloadBlockedError` 死类型、keyless 菜单三连、`== null` 与「禁宽松相等」规范冲突需裁决）。证伪：M-168「所有 409 都被当输出阻断」——`sandbox_sessions.py` 只有 `:1949/:2024` 两处 409，都在 DLP 通道。**中低危第七批（数据面横幅 / 反馈组件 / MPC 服务层）**：**A297（`DataplaneBanner.tsx:26` 绕过 services 直接 `api.get`，而 `metaApi.ts:77-78` 早已封装好 —— 这个绕过就是 A243 双重解包的成因，修 A243 应改调 `metaApi.getCapabilityMatrix()`）** → **A298（`:29-31` 探针失败静默 + `:35` 把 `dp===null` 与「数据面可用」同一渲染 ⇒ 控制面档横幅消失＝完整档，与 A294 同根：全仓缺「披露不可得」第三态）** → A299（antd v5 `Spin tip` 独立用法不渲染 ⇒ `CenteredLoading` 文案从未出现）→ A300（`mode`/`reason` 死字段，后端 `capability_matrix.py:320/324` 已把 `reason="dataplane_disabled"` 送到手边，前端硬编码文案）。降级：M-27「`enabled` 类型漂移」不成立（`:320` 是 `bool(...)`，latent）；M-164 只核到类型面，页面文案未逐行读，不作断言。

---

# 修复映射（2026-10-18 修复轮，本机 3.12 `.venv-gate` 口径）

判据：每组「改前红 / 改后绿」双读数；拿不出本地读数的一律回退或降级为「待部署验收」。台账明细见 `ocr-fixes.md`。

| 结论 | 条目 | 证据（本机实测） |
|---|---|---|
| **已修** | A268（含追加审计留痕） | 改前逐 hunk 回退 ⇒ `2 failed,16 deselected` / `1 failed,17 deselected` / `1 failed … got 0`；改后 `28 passed RC=0` |
| **已修** | A294b CORS `expose_headers` | 新 `tests/test_cors_expose_headers.py` 2 passed；`compileall -q app` RC=0 |
| **已修** | A279 Merkle 叶子 `|` 注入（五处，含我漏计的第 5 处 `cube_egress_audit.py:220`） | 改前 `9 failed, 67 passed` ⇒ 改后 `76 passed` |
| **已修** | A283/A285 prod compose 变量与卷 | 真空文件 `config` ⇒ `RC=1` + 17 行 `required variable`；真实 `.env.prod` ⇒ 缺 4 项被点名（改前静默空值） |
| **已修** | A243/A298/A245/A299 披露与横幅族 | 改前 `5 failed / 4 passed` ⇒ 改后 `3 files / 9 passed`，`tsc --noEmit RC=0` |
| **已修** | A274/A257/A281 服务层解包与取值默认值 | 改前 `17 failed / 33 passed` ⇒ 改后 `50 passed RC=0`，邻居回归 `107/107` |
| **已修** | A272/A290/A291/A275/A276/A277/A280 审计面 | 改前 `15 failed / 6 passed` ⇒ 改后 `21 passed`，存量 `26 passed` |
| **已修** | A278/A261 登录页与合规披露 | 改前 `10 failed / 2 passed` ⇒ 改后 `12 passed`，复跑仍 `12 passed` |
| **已修** | A244 全局 ErrorBoundary | 移出组件 ⇒ `RC=1 Failed to resolve import` ⇒ 加回 `4 passed`；组件层合并门 `13 files / 91 passed` |
| **已修** | A289 `tests/e2e_full_test.py` 被裸采集 | 改前 `12 tests collected` ⇒ 改后 `0 collected / 1 skipped`，`CDS_LIVE_E2E=1` 仍 `12 collected` |
| **已修（显示层）** | 证书 `unknown` 态标签 | 该文件 tsc 0 错误；文案显示属部署验收 |
| **回退** | A288 `.dockerignore` 排除项 | 按字节还原 —— 本机无法验证（要 docker build 上下文），已移入「待部署验收」 |
| **待部署验收** | A288、`unknown` 标签显示、federation/证书页面级提示 | 需镜像构建或真机点击 |
| **登记未修** | A301 remote_deploy fail-fast 漏项 + `CDS_DEBUG=true`；A302 `k8s/cds-app.yaml` 零卷 + `replicas: 2`；A303 middleware 兜底口令 | 验收要跑真实远程车道 / 集群，本机证不了 |

**终局门读数**：后端 `106 passed, 1 skipped / RC=0`（10 文件，118.6s；1 skipped = A289 的模块级开关）；`verify_schema_parity.py` `RC=0`（54 tables, columns identical）。前端门读数见 `ocr-fixes.md`（必须带 `NODE_OPTIONS=--experimental-require-module`）。