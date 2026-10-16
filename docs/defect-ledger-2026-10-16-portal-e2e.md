# 缺陷台账 — 开发者门户端到端联调（2026-10-16）

范围：浏览器一条龙（数据资源 → 产品 → 合约签署 → 我的开发 → 生产执行 → 三通道交付 → 存证验真）
与 SS-17 三轨道（交付中心、审计验真、配额/指引）联调期间实测到的缺陷。
每条都标注**证据**与**状态**；未修的确切写「未修」。

验证口径（用户常设约束）：pytest / 构建的**权威数字来自远程 harness**
`root@100.112.5.253:22022` → `/root/cds-test/src`（主机 venv py3.12 + 真实 PostgreSQL，
源码直跑，不用容器/镜像）。本机 pytest 只作为迭代探针，不作为结论。

---

## D-01 合约 SM2 签署全链路失效（四个独立缺陷，100% 失败）

现象：前端「SM2签署」永远报 `签署失败: SM2 signature verification failed`，合约无法进入 `active`。

| # | 层 | 缺陷 | 修法 |
|---|---|---|---|
| 1 | 前端签名原文 | `contractSignPayload` 不拼 `purpose` / `purpose_scope`，与后端 `crypto_service.contract_sign_data` 不同串 | `cds-frontend/src/utils/crypto.ts` 补 `purpose/purposeScope`，`purpose_scope` 用逗号串（与后端 `",".join` 对齐） |
| 2 | 前端私钥来源 | 本地自生成密钥并丢弃服务端返回的私钥，而服务端验签读 `user.sm2_certificate` | `pages/Contracts/ContractDetail.tsx` 改用 `/auth/generate-sm2-keys` 返回的 `private_key` |
| 3 | 前端请求体 | `POST /contracts/{id}/sign` 未带 `timestamp`（`ContractSign` 必填） | `services/contractApi.ts::sign` 补 `timestamp`（422 → 200） |
| 4 | **后端摘要** | `_sm3_hash_with_z` 把 Z 算成 `SM3(ENTL‖ID‖xG‖yG‖xA‖yA)`：**漏掉曲线系数 a、b**，且 xG/yG 用的是错误常量 | `app/services/crypto_service.py` 改为 `SM3(ENTL‖ID‖a‖b‖xG‖yG‖xA‖yA)`，常量取 `gmssl.default_ecc_table`（与 EC 运算同源） |

第 4 项是根因，也最严重：**修正前服务端只能验自己签的名**，任何标准实现（浏览器 sm-crypto、
真 HSM、第三方验签工具、联盟链节点）签进来的 SM2 签名一律验不过。

交叉验证证据（本机容器内 + 宿主 sm-crypto 0.4.0 同时钟）：

```
修正前：BE_DIGEST(impl)     = d6a426e7…3940c2a0
        BE_DIGEST(standard) = d7d392a2…18c7cd0f      ← 两者不等 ⇒ 后端非标准
        FE_SIG_VS_backend_impl = False
        FE_SIG_VS_standard_z   = True                ← 前端是标准的
修正后：BE_DIGEST(impl) == BE_DIGEST(standard) == d7d392a2…18c7cd0f
        FE_SIG_VS_backend_impl = True
```

浏览器级证据（qa_dev 买方，`127.0.0.1:3001/contracts/0fc50ab3…`）：
点「SM2签署」→ 提示「签署成功」→ 服务端 `status=active`、`buyer_signed_at=2026-10-16T08:05:47Z`。

回归钉住：新增 `tests/test_sm2_standard_interop.py`（4 例）—— 用**独立实现**的标准摘要
（测试内自带 sm2p256v1 字面量常量）比对 `_sm3_hash_with_z`，并钉一条由 sm-crypto 产出的真实
签名必须验得过。远程 harness 实测通过（见 V-1）。

**兼容性后果（必须披露）**：10-16 之前由旧摘要产生的 SM2 签名/证书自修正起**不再可验**。
契约内签名只在签署时刻校验，历史行不会重算，因此存量已签署合约不受影响；
但 `certificate_service` 自签发的证书若被重新验签会失败，需要重签发。真实环境升级时应
重发 SM2 证书并告知已签署方重新签署任何待复核材料。

---

## D-02 审计 Merkle 取数无确定序：锚定与验真天然可能不一致（已修）

`merkle_service.build_tree` 是**位置**算法，root 只取决于叶子顺序，而三处取数都没有 `ORDER BY`：
`POST /anchor` 用 `id IN (...)`（主键序），`GET /verify/{id}` 与 `/merkle-proof/{id}` 用
`blockchain_tx_hash = ...`（存储序）。同一份未篡改数据两侧得到**不同 root** ⇒ 未篡改的记录也会
验真失败。探针实测（sqlite，同模型）：插入序 `D-B-A-C`、`IN` 序 `C-A-D-B`、`=` 序 `D-B-A-C`，
无 ORDER BY 时两 root 不等；加 `ORDER BY created_at ASC, id ASC` 后三者一致。

修法：`app/api/audit.py` 增加 `_MERKLE_ORDER_BY`，锚定与两处验真共用；同时暴露
`block_number`（PG 追加日志恒 `null`，绝不填 0）、`anchor_id`、`confirmed`、`merkle_root`。

诚实性修正：原 `AuditLog.tsx` 对本地 PG 日志也弹 `message.success('区块链验证通过')`，
现由 `components/Audit/AuditVerifyModal.tsx` 判定：非联盟链只能是
「本地防篡改校验通过（非联盟链上链）」，成功态仅在真实联盟链时出现。

浏览器级证据（cdsadmin，`/audit`，先 `POST /anchor` 锚定 4 条 `contract.sign`）：

```
ANCHORED 4 root 532e2b54ea396cbc tx 2a7a47d3dc6c1226 block None backend pg_append_only
未锚定行  → 「该记录尚未锚定存证 Record not anchored to blockchain」
已锚定行  → 「本地防篡改校验通过（非联盟链上链）… backend=pg_append_only」
区块高度  → 「后端未提供区块高度（本地追加日志无链上高度）」
```

遗留（非本次引入，已在代码注释披露）：`cube_egress_audit._anchor_batch` 按**文件序**锚定并把
上游 `ts` 写进 `created_at`；若同批两行时间戳完全相同，验真会重算出不同 root 并**如实报失败**。

---

## D-03 会话清理循环同步调用 runtime，阻塞整个事件循环（已修，见文末「D-03 修复落地」）

现象（本轮实测两次）：`GET /health` 连续 12s 无响应（`HEALTH=000`），登录 POST 挂死；
再等约 20s 后 `HEALTH_RETRY=200` 自行恢复 ⇒ **间歇性整体停摆**（不是死进程）；
容器 `docker ps` 显示 healthy，日志停在

```
[SessionLifecycle] Container termination failed for 6125581e-…:
[SessionLifecycle] Container termination failed for d15ff275-…:
```

判据：`app/services/session_lifecycle.py:79` 在 async 函数里直接
`runtime.terminate(session.container_id)`（**没有 `asyncio.to_thread`**），
底层是同步 docker/httpx 调用；数据面不可用时每次调用要等满自身超时，N 个僵尸会话 × 每轮清理
⇒ 事件循环整体停摆，表现为「所有请求一起卡住」（上一窗口 `POST /api/v1/contracts` 600s
ReadTimeout 属同一现象）。

建议修法（一行，需配测试）：`await asyncio.to_thread(runtime.terminate, session.container_id)`，
并把该清理循环整体移出请求路径。当时没先修的原因：需要一条能复现「同步终止拖住 health」的测试，
不属于本轮门户联调改动面——本轮后半段已补齐修法与钉测（见下文「D-03 修复落地」）。

---

## D-04 CubeSandbox 控制面 down ⇒ 真实 MicroVM 执行不可用（阻塞，需 root；确切链路见 V-6）

`POST /api/v1/sandbox-sessions/dev-session` 返回 500，容器栈回溯到
`cube_runtime_adapter._master_ensure_volume` → `httpx.ConnectError [Errno 111]`，
目标 `CUBE_MASTER_ADDR=127.0.0.1:8089`。实测单元状态：`cubemaster` 于 10-15 10:06 被停，
cube 依赖的 mysql(`127.0.0.1:3307`)/redis/minio 单元 exit 1。

阻塞原因：这些 systemd 单元 `User=root` + `RequiresMountsFor=/data/CubeMaster/storage`，
非 root 无法拉起。**因此本报告中所有「执行/成果包/生产运行」环节只有 UI 与契约层证据，
没有任何一条真实 MicroVM 执行记录** —— 按项目铁律如实标注，不以模拟路径冒充（禁伪造）。

另一条在册旧值：`.one-click.env:423 CUBE_SANDBOX_NODE_IP=10.206.60.150` 已失效
（cube-proxy bind 失败）。

---

## D-05 外包节点交付的测试「凭想象断言」，且都未运行（已修，附流程结论）

三个节点各自新增的 pytest 文件均未运行过（节点 brief 明确要求不在本机跑测试），
首跑远程 harness 暴露四类**断言本身写错**：

| 位置 | 断言 | 真实情况 |
|---|---|---|
| `test_ss17_audit_verify_ordering.py` | `anchor["success"] is True` | `/anchor` 响应无 `success` 键（真实键：`anchored`/`merkle_root`/`tx_hash`/`anchor_id`/`confirmed`/`block_number`/`backend`） |
| 同上 | 用 `merkle_service.hash_leaf` 造叶子 | 端点用的是 `crypto_service.sm3_hash`（`hash_leaf` 多一个 `0x00` 域前缀） |
| 同上 | 证明路径重算用 `hash_leaf` | 应为 `hash_internal`（`0x01` 前缀，`position` 决定左右序） |
| 同上 | `monkeypatch backend="fisco_bcos"` | 字段类型是 `ChainBackend`，`_backend_disclosure` 取 `.value` → 500 |
| `test_ss17_quota_readonly.py` | provider 只见自己 owner 的 2 个端点 | 实现是**合约当事方可见**（与成果包列表同源）⇒ 3 个；且买方断言本身也要求当事方口径 |
| 同上 | `calls_remaining == 863` | `1000-140 = 860`（863 是单端点 `1000-137` 的值，同一段里两个口径混用） |

流程结论（已执行）：父节点把子任务的后端改动同步进远程树，用**远程 harness** 跑作为验收；
凡交付「未运行的测试」，默认按不可信处理，逐条读端点源码重断言。

---

## D-06 成果内容「脱敏预览」（已按最小正确形态实施，远程验证中）

PRD 要求交付中心提供预览弹窗。核查结论：后端唯一返回成果字节的路径是整包 zip 下载，
`POST /data-resources/{id}/sample` 返回**未过出口审查**的原始抽样行 —— 拿它做预览正是被禁止的
绕审查路径。因此本轮**没有**造预览端点，前端历史行的水印列显示「未下发」并指出去哪看。

最小正确形态（待批准后实施）：按 `_is_party` 鉴权 → 对 `delivery/{bundle_id}.zip` 做范围读 →
`output_inspector.inspect` + `should_block` → 返回前 N 行 + `review` 状态 + 专属审计动作
`delivery.dataset.preview`（不要复用 `sandbox.file_download`）。

### D-06 处置（2026-10-16 晚）

拿到继续开发授权后按上面那个形状做了，而不是按“拿 `/sample` 凑一张表”的初始想法：

- `GET /api/v1/deliveries/dataset/{bundle_id}/preview?rows=N`（`app/api/deliveries.py`）：归属鉴权
  与下载同源（`DeliveryBundle` → `_load_contract`）；先过 `_bundle_output_reviewed` 的 T5 门禁
  （加固前旧包 `409 OUTPUT_REVIEW_REQUIRED`）；再对**即将返回的那段片段**重跑
  `output_inspector.inspect` + `should_block`，判定拦截则 `403 OUTPUT_BLOCKED` 并写
  `delivery.dataset.preview_blocked` 审计，不给任何数据字节；包内无 csv/tsv 时返
  `available:false` + 真实文件清单，不凑表；放行时写 `delivery.dataset.preview`。
- 前端：`flowApi.previewBundle` + `pages/Flow/BundlePreviewModal`（展示打包时审查状态/发现数、
  预览再审查发现数、行数与截断）；CSV 解析提到 `src/utils/csvPreview.ts` 与数据资源
  脱敏预览共用（遵守“页面不互相 import”）。
- 用例 `tests/test_ss18_bundle_preview.py`（7 条）只钉门禁：非当事方 403、旧包 409 且响应
  不含数据、拦截时 `"data" not in body`、截断与 `rows` 越界 422。
- 另补一个同名但不同侧的缺口：`POST /data-resources/{id}/desensitize` 后端一直存在却零
  消费者，现在数据资源列表有「脱敏预览」入口，弹窗里固定声明“这是脱敏后的样例，不是原始
  明文”（`masking_applied` 为假时改口提醒）。

验证：本地 `pytest tests/test_ss18_bundle_preview.py` 7 passed、`tsc -b --force` rc=0、`vitest` 12 文件
141 用例全绿；**远程权威数字见 V-9**（本地数字不作结论）。

---

## D-07 `alert_records` 去重键冲突使清理与留存任务反复回滚（未修）

```
Node stale detection failed: duplicate key value violates unique constraint "ix_alert_records_dedup_key"
  parameters: ('node-stale-node-local-1', 'node_stale', …)
Retention janitor failed: This Session's transaction has been rolled back due to a previous exception during flush …
Cleanup error: …（同一条链式回滚）
```

`node-local-1` 心跳超时后每轮都试图插入同一 `dedup_key`。后果不只是噪声：同一次 flush 失败后，
**同一批清理工作（留存 janitor、会话清理）被连带回滚**，即僵尸会话永远不会被清干净
（也是 D-03 的放大器）。

机制已读到源码（`app/services/alert_center.py::raise_alerts`，第 53-70 行）：同一批内部靠
`seen` 字典 + `_get_by_dedup_key` 去重是正确的，但跨会话/跨轮次是“**先查后插**”，
上一轮的同一 key 行在本次读之后才提交时，本次 `_get_by_dedup_key` 看不到它 ⇒ 走 `db.add` ⇒
`await db.flush()` 直接撞 `alert_records.dedup_key` 唯一索引（`app/models/alert.py:36`）。
一次 flush 失败会污染整个 session，所以同一条链上的后续写入全部连带失败。

修法（本轮未实施，下一轮首选）：**不要用共享 session 的 `db.rollback()`**（会把同一次请求/
同一轮清理的其他写入一起抹掉，而且 async SQLAlchemy 下 `rollback()` 会 expire 全 session 对象
→ 后续同步属性读取 `MissingGreenlet`）。正确做法是把每条告警的插入包在 **SAVEPOINT** 里：
`async with db.begin_nested(): db.add(rec)` + `except IntegrityError:` 重新 `_get_by_dedup_key`
并 `_merge_occurrence`（外部 session 不受影响）；另外建议给 `alert_records.dedup_key` 补一条
`ON CONFLICT DO UPDATE occurrence_count = occurrence_count + 1` 的 upsert 路径，
把“先查后插”从写入路径里消失。同时把节点陈旧检测 / 留存 janitor / 会话清理拆成独立 session，
避免一处失败把三者一起拖下。

---

## 遗留项判定（PRD 条目 → 实际处置）

| PRD 条目 | 处置 | 理由 |
|---|---|---|
| 操作日志详情弹窗 | **不做**（已由运行日志抽屉内的「沙箱操作日志」表覆盖） | 逐行再弹一层详情在没有真实会话时不可测；现在做＝写一个验不了的 UI |
| IDE 固化发布版本弹窗 | **已做，形态不是弹窗** | `WorkloadCenter.tsx` 已有版本号 Tag + 发布门禁（`publishGateReason` 不可发布时 Tooltip 给理由）+ 后端门禁错误码原文上屏；再加一个弹窗是冗余 UI |
| 脱敏数据预览弹窗 | **已实施**（见 D-06 处置） | 成果侧预览走 T5 门禁 + 片段重审查；资源侧走已有脱敏端点 |
| 任务聚合列表页与创建表单 | **未做**（本轮唯一真正欠账的前端项） | 当前入口拆在 Notebook 开发 / 负载测试与发布 / 计算调度 三页，能完成流程但没有「我的开发」单页汇总；不阻塞业务闭环 |

---

## D-08 外包节点把前端沙箱枚举缩到唯 l2c，打破四方一致性锁（已修）

CI 门禁子集唯一失败项：`tests/test_capability_matrix_consistency.py::test_matrix_level_set_consistent_across_sources`

```
AssertionError: FE enums.ts SandboxLevel drift: api=['L0','L1','L2','L3','k8s','l2c'] fe=['l2c']
```

该测试是 SS-11 §4.2 「更新纪律」的四方锁（API capability-matrix / FE enums.ts / SDK / docs）：
任何一处增删改名级别必须同时改齐四处。节点把 `SandboxLevel` 常量缩到 `l2c`（意图正确：
创建表单只能选 CubeSandbox），但把“**可选集**”与“**词汇表**”混为一谈：后端能力矩阵仍会返回
六个级别（带弃用标记），存量会话/合约也仍携带 L0-L3/k8s —— 枚举一旦只剩 l2c，历史行在列表/详情里
就变成不可比较的未知值。

修法：枚举拆为 `SandboxLevel`（可选，只有 l2c）+ `LEGACY_SANDBOX_LEVELS`（历史词汇表），
测试的 FE 源改为读“可选 + 历史”的并集去比 API/docs 词汇表；**额外新增一条断言**钉住
「前端可选级别必须只有 l2c」（SS-13 单一微虚机路径、禁止降级）——锁没放松，反而多钉了一项。

口径对齐：`developer-portal-implementation-plan.md` P1 的原意是收窄「表单可选项 + 前端路由」（
`enums.ts` 锁 l2c、下线 `/ops/*` 与 `/capability-matrix` 页面），**不是**收窄 API 词汇表；
若照字面把枚举一起删干净，就会再一次踩同一把四方锁（API 矩阵端点仍服务 SDK / 审计人员）。

两条断言都做了红测（不是“改到绿”）：

```
RED_CHECK drop-legacy-k8s: rc=1  1 failed, 2 passed     ← 从历史表去掉 k8s ⇒ 词汇锁红
RED_CHECK select-L3:     rc=1  1 failed, 2 passed     ← 把 L3 放回可选集 ⇒ 单选锁红
RESTORED OK（枚举文件 md5 与改动前一致）
远程 harness：`9 passed` / `TARGETED_LOOP_RC=0`（矩阵 3 + 生命周期 5 + 新增防阻塞 1）
```

---

## D-09 HSM 不可用时注册结果推送直接 500（未修）

浏览器/接口均能复现：`POST /api/v1/deliveries/webhooks` → `500 INTERNAL_ERROR`。
堆栈钉到行：

```
app/api/deliveries.py:400  create_webhook → secret_enc=encrypt_task_code(secret, scope=f"webhook-{sub_id}")
app/services/task_code_security.py:15  kms_service.generate_data_key(...)
  → self._ensure_kek()
  → self._hsm.generate_kek(_KEK_ID)
RuntimeError: HSM unavailable — cannot generate KEK
```

两个独立问题，不是一个：
1. 启动日志已经写了 `Vault authentication failed — falling back to software HSM`，但真到要
   KEK 时仍报“HSM unavailable”⇒ **软件回退路径没覆盖 `generate_kek`**（或回退根本没生效）。
2. 即便密管真不可用，给用户返 `RuntimeError` 冒出的 `INTERNAL_ERROR` 也是错的形状：应该是带
   `code` 的 5xx（如 `KMS_UNAVAILABLE` / 503），否则前端只能写“服务器错误，请重试”，而重试
   永远不会好——这与项目铁律“诚实降级”直接相左。

影响面不只 Webhook：所有走 KMS 数据密钥的业务写入（推送密钥、任务代码加密）同样会 500。

## D-10 卷插件与“节点就绪”判据脱节：ready_nodes=1/1 仍然建不了沙箱

修好 Cube 控制面后，`dev-session` 的错误从“连不上 master”变成了完全不一样的一句：

```
cube-master volume create failed for cds-data-cda57064-…:
  plugin create error: rpc volume plugin "cds-encrypted" create:
  rpc error: code = Unavailable … dial unix /run/cds-volume-plugin/cds.sock: connect: connection refused
```

现场：二进制在场（`/usr/local/bin/cds-volume-plugin`，10-14 装），socket 文件也在，但是
**10-14 16:12 的旧文件**，而单元 `cds-volume-plugin.service` 是 `disabled`——也就是说
这轮根本没人拉起它，`/flow/runtime-status` 却依旧报 `available:true, ready_nodes:1/1`。

可复用判据（把 V-6 那条推进一步）：**“节点就绪”只量心跳与注册，不量数据面依赖**；
卷插件、镜像仓库这类依赖挂掉时，控制面一列绿灯但创建沙箱必失败。查 unix socket
要用 `ss -lxn`（不是 `-t`），并且要看 socket 文件的 mtime 是不是旧的。

处置（进行中，不预先写结果）：`systemctl enable --now cds-volume-plugin.service` 后重试
`dev-session`。

## D-11 卷插件上报 `plugin backend None`，加密后端不诚实披露（未修）

开发沙箱跑通时日志里出现了这么一行（原文）：

```
[KMS] volume key vkey-c4aa5ebb… (volume cds-data-6a528d6f…) registered with unknown,
      not SM4-GCM — at-rest encryption for this volume is NOT GM/T compliant
      (plugin backend None)
```

应得的评价：应判为**披露缺口**，不是“后端不支持”。卷插件 systemd 单元里明明写了
`CDS_SM4_AEAD_BACKEND=aes_fallback`（AES-256-GCM 标注为 SM4 的开发态代替，属已知设计），
但插件向 KMS 登记时该字段递了 `None`，于是审计侧只能写“unknown”。
项目铁律是“模拟/降级必须可披露”：披露成 `aes_fallback` 是诚实，披露成 `unknown` 等于
让合规读着的人自己猜。修法：插件登记时把实际 backend 带上（Go 侧），或在 volume-keys
入参校验里拒绝 `None` 而不是存下 `unknown`。

## D-12 空笔记本点「运行全部」是空跑还报成功（已修）

现象（浏览器实抓）：UI 新建的开发任务，IDE 显示「单元格 1」，但 `GET /notebooks/{id}` 的
`content.cells` 是 `[]`（前端在 cells 为空时补一个空壳给编辑器，库里从来没有这个单元格）。
点「运行全部」→ `POST /notebooks/{id}/run` **200**；跑后 `content.cells` 仍 `[]`、
`execution_backend=null`，版本却从 v1 跳到 v2，页面「执行后端」仍写「尚未执行」。

后端 `run_notebook` 对 0 个单元格返回 `{results: [], passed: true}`——`passed` 是空积为真，
于是前端弹「全部单元格执行通过」；同时它在校验**之前**就 `_ensure_dev_session`，白起一个真
MicroVM 开发沙箱，还把版本 +1 导致编辑器以为“后端内容被他人改过”。这类缺陷的名字是
**空输入返回 200 + 真空为真**，`run` 只是当前发现的一个实例。

修法（已落地）：
- `app/api/notebooks.py::run_notebook`：把 doc 计算提到 `_ensure_dev_session` **之前**，
  无任一非空单元格 → `CDSError` 409 `NOTEBOOK_NO_CELLS`（诚实失败，不再白起沙箱）。
- `cds-frontend/src/pages/Flow/NotebookWorkbench.tsx::runAll`：cells 全空白时本地拦下并提示。
- 钉桩：`tests/test_ss15_flow_api.py::TestNotebookEmptyRun` 2 条（空 doc / 只有空白 cell），
  断 409 且版本不变、`execution_backend` 仍 null。

我自己在这条修法上踩的坑（必须记）：首版守卫写成 `for cell in nb.code_cells(doc)`，而
`code_cells` 产出的是 `(index, cell)` 元组 ⇒ **有单元格的路径直接 500 AttributeError**。
本地 `compileall` + `import app.main` 完全看不出来，是**远程定向 pytest 的第一条红**
（`1 failed, 34 passed`）逮到的。判据：给循环加守卫要照抄原循环的解包形状，并且守卫必须
在“有输入”的那条路径上也被测试跑到，否则它只会证明空输入被挡住了。

## D-13 自己跑完一次，页面反过来让你“丢弃编辑”（未修）

现象：开着「输出写入笔记」跑完一次（自己的运行），IDE 卡片立刻出现
「后端笔记内容已更新（可能由带输出的执行或其他会话保存引起）/ 为避免覆盖您的编辑，
编辑器仍显示本地内容 / 载入最新内容」。此时用户的本地源码与刚保存的完全一致，
“另一个会话”的叙事是假的，唯一选项还会把他的编辑冲掉。

读码定位：`NotebookWorkbench.tsx:313` `serverChanged = canonicalize(notebook.content) !== savedContent`。
跑一次会把 output 写进服务端 doc，而 `savedContent` 是无输出的基线 ⇒ 必不等。
修法方向（未动）：冲突比较只看单元格源码不看 outputs（源码真变了才是并发编辑），
或者运行成功后用服务端 doc 重新基线化 `savedContent`。本轮先记账：它不拦业务，但把
“自己刚刚做了什么”说成“别人改了”违反诚实口径。

## D-15 我自己改名带出的回归：`/usage` 还在 `du` 旧目录（已修）

SS-18 把 VM 内目录收敢到 `/sandbox/data` + `/sandbox/output` 后，`GET /sandbox-sessions/{id}/usage`
的 l2c 分支仍是 `du -sk /data /results /scratch`，并且按目录**尾段**匹配（`data`/`results`/`scratch`）。
后果不是报错而是**静默报 0**：新目录不在 `du` 参数里，配额页会把真实占用当成空，
这是最阴的一类回归（旧路径不存在时 `2>/dev/null` 把失败也吞了）。
修法：`du` 参数与匹配集都改成从 `_cra.GUEST_*` 取，路径只有常量一份来源。
判据：改路径/改枚举时，grep 字面量本身（不只 grep 符号名）；带 `2>/dev/null` 的探测语句
失效时不报红，必须另开一条能红的钉桩。

## D-16 开发者工作台只裁菜单不裁路由（口径缺口，非越权）

`filterMenuByWorkspace` 只过滤侧标 key，路由层不看工作台。实测：`/ops/*` 四条有
`App.tsx:106-109` 的 `k8sOpsReaders` **路由门**，所以非运维角色 URL 直达仍被拦——
**这不是安全洞**（外包审计报告把这条写得比事实严重，已按真实路由门改写）。
真实缺口是：已具备运维角色的账号切到开发者工作台后，手输 `/ops/deployments` 仍进得去，
与原型“开发者只看 4+2”的口径不一致。修法方向（未动）：给路由加一层
`WorkspaceRoute` 门，按 `resolveWorkspace(role, stored)` 判定，而不是只靠菜单隐藏。

## D-03 修复落地（对应上文 D-03）

按 D-03 的一行修法落地（共三处同源阻断点，均在 `terminate_session` 清理路径上）：

```diff
-            runtime.terminate(session.container_id)
+            await asyncio.to_thread(runtime.terminate, session.container_id)
-                        shutil.rmtree(sdir, ignore_errors=True)
+                        await asyncio.to_thread(shutil.rmtree, sdir, ignore_errors=True)
-        kms_service.destroy_key(session.session_key_id)
+        await asyncio.to_thread(kms_service.destroy_key, session.session_key_id)
```

回归钉住：`tests/test_session_lifecycle_loop_blocking.py` —— 用 `time.sleep(0.4)` 的同步
`terminate` 桩 + 20ms 心跳计数器，断言清理期间心跳跳数 `>= 3`（被阻塞时为 0，所以不可能靠调度运气过）。
本地 6 passed（含原有 5 项生命周期测试）；上面已给远程 `9 passed / rc=0`。

---

## 工具观察（影响可复核性，记下）

本轮 `read` 对同一文件单次调用返回了两份相同结果（输出里可见 `[2 read results: …]`），
前序窗口还出现过 `read` 返与磁盘不一致的告警。因此本轮所有写入依据都额外过了一道硬 oracle：
改前用 `git diff` 确认锚点、改后用 `git diff` 逐行审阅 + `compileall` + 测试，
不拿“看到的内容”当事实。

**合成输入不会进入 antd 表单状态（会把“登录失败”误判成产品缺陷）**：`Input.insertText` 和
`el.value=…; dispatchEvent(new Event('input'))` 两种写法都能让 `input.value` 看着有值，但
antd Form 内部 store 仍为空 ⇒ 点“登录”既不发请求也不报字段错（API 日志里
`grep auth/login` 计数为 0，页面停在 `/login`，`.ant-form-item-explain-error` 也是空）。
正确的分开顺序：先在页面内 `fetch()` 同一接口确认代理与后端（结果 200 带 token），再用
真实键入（`type_text`）重跑表单——真实键入一次就 `status:200, len:653` 并跳转首页。
同样适用于“选了值但提交报必填”一类现象：先看请求有没有发出，再怀疑后端。

**车道在错误的机器上跑（我自己的一次调度错误，记下判据）**：同一轮里我把本地的
`/tmp/fe_lane_src.sh` 直接 `bash` 掉了（应当 `ssh` 去跑 `/root/cds-test/fe_lane_src.sh`），
于是 `TSC_RC=1` / `NPM_CI_RC=1` 全是假失败。症状非常固定，下次直接按这两条认：
① `mkdir: Permission denied`（脚本要在 `$SRC=/root/cds-test/...` 建目录，本机根本建不了）；
② **本地在 pull 镜像**（远程 harness 早就有缓存，本地需要重新拉就是跑错机器的铁证）。
另一条交叉证据：去远端看 `fe_tsc.log` 发现 mtime 还是上一轮的、体积 0 字节——
**哨兵写的日志没动，就说明哨兵根本不在那台机器上跑的**。结论：凡跨机车道，入口必须
带 `ssh` 前缀，并在第一条哨兵里回显 `hostname`。

另一条同族假绿灯：**用低权限身份做“错误已清干净”类哨兵，会把权限不足当成不存在的证据**。
本轮一个脚本先 `sed` 替换失效 IP，再用 `grep -rl <旧 IP> <目录> 2>/dev/null` 自证“已清干净”，
哨兵如实打了 `OLD_IP_REPLACED`——但该目录里真正重要的几个文件是 root 所有，`grep` 读不了它们，
而 `2>/dev/null` 把这些“Permission denied”全吞了，于是“没找到”被当成了“不存在”。用 root 重查后
残留文件从 6 个变成 9 个，其中就包括总开关 `.one-click.env`（还有 Cubelet/CubeMaster 的
`plugin/volume-s3.conf`）。判据：**“找不到 X”类哨兵必须确保探测权限覆盖所有目标文件**——
要么整个哨兵以同权限跑，要么去掉 `2>/dev/null` 并区分“无匹配”与“不可读”（后者计为 UNKNOWN）；
同时记住哨兵会漏掉不含关键词的行（本轮 `OLD_IP_LEFT_IN:` 后面的文件名就被 `filter` 滤掉了，
只能回看会话原文）。

---

## 验证记录

### V-16 推送前门（远程源码直跑 gate 车道 + FE 车道）

环境前提：本轮首次跑的 gate 车道 **5 步全红**，根因是车道脚本（当时只在本机 `/tmp`）丢了
`export PATH="…/venv/bin:…"` 一行⇒ 退化成 `/usr/bin/python`（3.11）：`alembic`/`pytest` 找不到模块，
并把 `app/services/agent_service.py:386` 的合法 PEP 701 f-string **误报成 SyntaxError**。
修车道后重跑，**未改任何产品代码**。车道脚本已入仓 `scripts/remote-harness/`。

远程 `source_lane.sh fast`（Python 3.12.15，树 `STEP_OK tree_sync`）：
`compileall` / `import_smoke` / `alembic_upgrade` / `alembic_check` / `schema_parity` / **`gate`（GATE_FILES=36）**
均 `STEP_OK`；`ss15` 子集结果落地后回填。

远程 FE 车道（node:20，补跑一轮以覆盖 IDE 目录提示行）：`TSC_RC=0 VITEST_RC=0 BUILD_RC=0 FE_LANE_EXIT=0`，
`Test Files 12 passed (12) / Tests 141 passed (141)`。

推送：`56ad292..b8fc5c1` → `origin/feat/ss-11-productization`（6 个提交：车道入仓 / SS-18 文档与台账 /
前序后端轮次 / SS-18 目录契约 / 前端门户对齐 / 原型截图入仓）。
**未跑 full 全量回归**（~28 分钟）：本轮只改过目录常量与两个 API 的文件路径解析，gate 子集 +
定向 67 passed 已覆盖，全量数字待补到下一行，不得拿 22/33 基线当作本轮已验。

### V-15 SS-18 目录契约落地的远程权威复验

载体：`/root/cds-test/src`（`TREE_MD5_MATCH a51f01fd…`，与本地逐文件同内容），
`tests/test_ss18_guest_paths.py` + `test_cube_runtime_adapter.py` + `test_sandbox_sessions.py`
+ `test_ss15_flow_api.py` → **67 passed in 56.94s**。

首跑是 `1 failed, 66 passed`：`test_build_run_request_proto_fields` 仍断旧挂载表。按“改写断言而不是
删测试”把四条挂载按字面钉成新口径（`/sandbox/data` 只读 / `/sandbox/output` / `/scratch` / rootfs `/`）。
挂载表是 gRPC 请求形状契约，故意不用常量推导——改路径必须让测试变红而不是跟着代码漂。

哨兵缺陷（记我自己）：远程命令串是用 `JSON.stringify` 生成的**双引号**包，`$rc` 被本地 shell
先展开成空 ⇒ `SS18B_RC=` 是空的（上一轮 `SS18_RC=` 同样为空）。数字靠日志正文（`67 passed`）
才是真的。判据：远程哨兵里的变量必须在单引号内层（或转义），并且报告数字要回到日志正文核对，
不能只信哨兵。

### V-14 浏览器里第一次真机执行（正例，不是 API 侧拟合）

在 IDE 里真实键入 `print('browser full chain ok')` → 点保存（页面 XHR 里确有
`PUT /api/v1/notebooks/{id}`）→ 点「运行全部」→ 卡片常驻位变
**「执行后端：cds_kernel（有状态）· 来源：本次执行结果」**，单元格区显示
「执行成功 | cds_kernel 有状态 | 退出码 0 | 标准输出 stdout: browser full chain ok」，
版本 v3→v4。后端日志同时刻有 `POST /notebooks/…/run 200`。

这是“上传→数据目录→合约→开发任务→**真 MicroVM 调试执行**”这条腿第一次在浏览器里
收口（此前只有 `/tmp/e2e_full_chain.py` 的接口侧 200）。

同时证伪我自己一个假设：先前「点保存没发请求」不是产品缺陷——那一刻 `dirty=false`
（Monaco onChange 未落地就点了按钮）；重放时按钮态 `disabled:false` 且 XHR 里确有 PUT。
判据：先看请求有没有发出 → 再看 `disabled`/`dirty` 位 → 最后才怀疑后端。

### V-13b 守卫修法的远程复验

`tests/test_ss15_flow_api.py` + `tests/test_security_config_matrix.py` 同树同库：
**58 passed**（`NB2_RC=0`，41.6s；修法前同载体是 `1 failed, 34 passed`）。

### V-12 远程权威车道（KMS 修复入树后）

```
STEP_OK runtime Python 3.12.15 / tree_sync / compileall / import_smoke
STEP_OK alembic_upgrade / alembic_check / schema_parity / gate / ss15
gate : 244 passed, 2 skipped   (上一轮 242，+2 = 本轮新增的两条 KMS 门禁测试)
ss15 : 83 passed
full : 33 failed / 2951 passed / 10 skipped  (1678s)
LANE_RC=0
```

全量 33 失败与同载体基线（本文件 `ci/known-failures.md` 行：源码直跑 lane 同树
**33 failed / 2950 passed / 10 skipped**）**计数持平**，基线未增长。

其中 13 条属 `tests/test_cds_volume_plugin.py`（既有 C 助手 / Tongsuo 环境门禁）。本轮
KEK 改动是否掺进去过？读码排除：该文件全程用 `run_bin(插件二进制, "ctl", …)` + mock KMS
子进程，不 `import` `kms_service` 也不碰 `hsm_adapter`，两者无耦合（不是推测，是看完
316-333 行的依赖关系）。

### V-13 浏览器端到端（真实键入 + 真实点击）

登录不再靠接口拟合，全部在页面里发生：真实键入 `qa_dev` 后 `POST /api/v1/auth/login`
在页面 XHR 拦截器里看到 `status:200, len:653`，登录后首页立即并发
`/meta/capability-matrix`、`/monitoring/stats`、`task-trend`、`policy-rejection`。

- 我的开发 `/flow/tasks`：表头 名称/类型/阶段/合约/最近更新/操作，行里是真实数据
  （`执行/成功`、`full-chain v1/已发布`、`full-chain v1/测试通过`）——接口链路跑的东西在页面上看得到。
- 新建开发任务：名称用真实键入，合约下拉的两项来自后端（搜索 `CDS-` 后给出两条真实合约），
  选合约 → OK → 路由到 `/notebooks` 并新增一行 `浏览器全链验证 v1`。
- IDE 卡片按钮集：启动测试沙箱 / Jupyter 可用性 / 保存 / 运行全部 / 提升为负载 /
  运行单元格 / 新增单元格 / 归档当前 Notebook，卡片上常驻“执行后端”状态位（未跑时写
  “尚未执行”，跑后显示 `cds_kernel` / `cds_stateless`）。

### V-11 一条根因串起五个红灯：Vault 没挂 transit 引擎（已修，含真机复验）

`dev-session` 500 / 单元执行 500 / 整本跑 500 / 测试沙箱 `passed:false` / Webhook 注册 500
——五个红灯同一个原因，链路钉到底只有一句话：**这台机器的 Vault 可达、已认证，但从未
挂载 `transit` 引擎**。

容器内实测（不是推測）：

```
ADAPTER VaultTransitAdapter            # 工厂选中了 Vault
Vault KEK generation failed: no handler for route "transit/keys/cds-master-kek".
                                        route entry not found
DEK_ERR RuntimeError HSM unavailable — cannot generate KEK
```

为什么能瞒过启动日志：`create_hsm_adapter()` 只探“能不能连上 + 能不能认证”，认证过了就
认下 Vault；而 `_get_client()` 里那句“falling back to software HSM”在认证失败时才打印，
这台机器认证是成功的，所以软件回退路径**事实上不可达**。`vault secrets enable transit`
后同一探针立即 `DEK_OK dek-probe2`。

代码修复（两处，不只是改环境）：
1. `hsm_adapter.py`：新增 `VaultTransitAdapter.transit_available()`（查挂载表里有没有
   `transit/`），工厂探不到就当 HSM 不可用 → 回到原有的 `HSM_SOFTWARE_FALLBACK_ALLOWED`
   门（门关着依旧 fail-closed，不会默默降级）。
2. `kms_service._ensure_kek`：不再 `raise` 裸 `RuntimeError`（到 API 层就变成
   `INTERNAL_ERROR`，“重试”永远不会好），改抛 `CDSError(code=KMS_UNAVAILABLE, 503)`。
3. 钉住测试：`tests/test_security_config_matrix.py` 新增两条（transit 缺失→回退且
   fail-closed 优先；KEK 失败→`KMS_UNAVAILABLE/503`）。

真机复验（本机 l2c，非测试桩）：

```
POST /api/v1/kms/volume-keys                          201 Created
POST /api/v1/kms/volume-keys/{id}/distribute           200 OK
POST /api/v1/notebooks/{id}/dev-session                200 OK   （之前始终 500）
POST /api/v1/notebooks/{id}/cells/execute              200 OK   （单元在真 MicroVM 里跑完）
```

结果推送通道同时验到密码学层面（宿主侧独立验签，不是看“返了 200”）：
`POST /deliveries/webhooks` 201（secret 仅此一次可见，43 字符）→ `/test` 200
`delivered:true response_status:200` → 本地 sink 收到真实 POST，`X-CDS-Signature-Sha256`
长 64，按 `HMAC-SHA256(secret, "{ts}.{body}")` 重算**逐字相等**；篡改 body 后重算不相等
（说明校验有牙）；`DELETE /webhooks/{id}` 204。

代价：卷能建了，但同一行日志暴露了 D-11。

### V-1 远程 harness 定向回归（权威）

### V-1 远程 harness 定向回归（权威）

```
同步校验：SYNC_VERIFIED（crypto_service 新符号 grep 命中 3、测试新函数命中 1、0022 迁移存在）
tests（17 个文件：3 个 SS-17 新增 + SM2 互操作 + 全部受摘要修正影响的验签面 + 既有审计/链披露）
  首跑：1 failed, 251 passed（失败项＝D-05 的配额断言口径）
  修正后复跑：见本文件末尾「复跑结论」
```

同一条车道里的其他权威步骤（全量同步后与本地树内容逐文件相等，`TREE_MD5_MATCH f5cab72a…`）：

```
STEP_OK runtime Python 3.12.15   STEP_OK tree_sync      STEP_OK compileall
STEP_OK import_smoke             STEP_OK alembic_upgrade STEP_OK alembic_check
STEP_OK schema_parity            GATE_FILES=36
```

即 0022 迁移后的 `alembic check` 与 `scripts/verify_schema_parity.py` 在远程均为 OK。

本机迭代探针（非权威，仅记录）：`tests/test_sm2_standard_interop.py` 4 passed、
`tests/test_ss17_audit_verify_ordering.py` 7 passed、`tests/test_ss17_quota_readonly.py` 12 passed、
`cds-frontend: npx tsc -b` rc=0。

### V-2 浏览器端到端（已完成环节）

| 环节 | 载体 | 结果 |
|---|---|---|
| 登录（qa_dev 买方） | browser-use 注入 auth-storage | 菜单渲染 仪表盘/数据目录/合约管理/沙箱管理/数据流通 |
| 合约签署（SM2） | 点「SM2签署」 | 「签署成功」→ 服务端 `active` + `buyer_signed_at` |
| 开发指引 / 配额用量 | `/flow/guide` `/flow/quota` | 渲染正常；配额页对未采集项显示「未采集」而非 0 |
| Notebook / 负载 | `/notebooks` `/workloads` | 列表与门禁说明渲染；无会话时后端不落后端标识 → 显示「未记录」 |
| 计算调度 | `/compute` | 排程表 + 每行「执行」入口；菜单已含「开发指引 / 配额用量」（补齐 `DEVELOPER_MENU_KEYS` 后浏览器实测） |
| 生产执行 | 执行弹窗 → 「开始执行」 | 弹窗三字段如实渲染（生产版本 / 运行参数 JSON（沙箱内以 `CDS_PARAMS` 读取）/ 规格覆盖“只能向下收窄”）；提交后提示「执行已结束，状态 failed」 |
| 运行日志抽屉 | 运行记录行「日志」 | 逐列诚实披露：退出码「后端未上报」、沙箱会话「本次运行未创建沙箱会话（无真实执行）」、失败原因「该合约没有处于 published 状态的生产工作负载…」、日志区「没有会话就没有执行日志——此处不伪造记录」 |
| 成果交付 | `/deliveries` | 三通道 Tab + 成果包历史；未选合约时 API 交付面提示「请先在上方选择合约」，不凭空列端点 |
| 存证验真 | `/audit` + 弹窗 | 已锚定/未锚定/区块高度三态如实披露（见 D-02） |

### V-3 OpenAPI 结果通道：密钥作用域实测

人工主体签密钥（`POST /api/v1/api-keys`，admin）：`preset=custom` + `scopes=["gateway:*"]`
→ 返回 `cds_ak_hMOe…`，`scopes=['gateway:*']`（逐密钥功能权限可见，SS-12 设计项）。
预设词表为 `readonly|operator|full|custom`（`app/core/scopes.py::expand_preset`）。

用裸密钥直调数据面（不走 JWT 拦截器）：

```
POST /open/v1/ss17-no-such-endpoint   合法密钥 → http=404 {"code":"HTTP_404","message":"endpoint not found"}
POST /open/v1/ss17-no-such-endpoint   篡改密钥 → http=401 {"code":"API_KEY_INVALID",…"key_prefix":"cds_ak_hwron","path":…}
```

即：`gateway` 功能组对机器主体正确放行（否则会是 403/未映射拒绝），错密钥不会伪装成“端点不存在”。

**未完成环节（阻塞原因逐个点名）**：

| 环节 | 为何未完成 |
|---|---|
| 真实 MicroVM 执行 / 成果包生成与下载 | D-04 Cube 控制面 down（需 root）；执行链在 `WORKLOAD_NOT_PUBLISHED` 前置校验处如实停下 |
| OpenAPI 在线调试台实调 | 需先建端点，而 `POST /deliveries/openapi/endpoints` 要求合约有 published 负载（412）→ 同上阻塞 |
| 结果推送投递回执 | 同上：无真实产出就没有投递事件 |
| 负载「测试→发布」门禁实跑 | 测试沙箱需 dev session（Cube）；不伪造 `tested` 状态以绕开门禁 |

### V-4 远程权威车道

```
首轮（修门禁前）：STEP_FAIL gate rc=1 → 1 failed, 241 passed, 2 skipped in 167.94s（唯一失败＝D-08）
修 D-08 后复跑（全量同步与本地树逐文件相等 TREE_MD5_MATCH 9b2c8ffe…）：
  STEP_OK runtime Python 3.12.15 / tree_sync / compileall / import_smoke
  STEP_OK alembic_upgrade / alembic_check / schema_parity
  STEP_OK gate → 242 passed, 2 skipped, 6 warnings in 166.54s   ← 门禁子集全绿
  STEP_OK ss15 → 83 passed in 65.52s
```

**读数陷阱（必须记下）**：`source_lane.sh` 末尾的 `SUMMARY` 循环无条件打印 gate/ss15/full
三个日志，而 `fast` 模式**不跑** full —— 因此那次输出里的
`SUMMARY full: 33 failed, 2951 passed … in 1678.49s` 是**上一轮遗留日志**，不是本轮数字，
不得引用（本轮没跑全量）。mtime 已核实：`full.log` 15:49（上一轮）对比
`gate.log` 16:30 / `ss15.log` 16:31（本轮），相差 41 分钟。

### V-5 远程前端车道（权威，node:20 镜像，树与本地逐文件相等）

```
TSC_RC=0        npx tsc -b（strict）
VITEST_RC=0     Test Files 10 passed (10) / Tests 131 passed (131)
BUILD_RC=0      vite 生产构建成功（仅 chunk-size / dynamic import 提示）
FE_LANE_EXIT=0  ALL_LANES_EXIT=0
```

上面的 131 是**新增「我的开发」聚合页之前**那一轮的基线；含新页的最终数字见下一小节，
两个时点不得混引。

#### V-5b 最终树复跑（含「我的开发」聚合页与只列已生效合约的修正）

```
TREE_MD5_MATCH + SYNC_ALL_OK，前端源码树逐文件相同（src/*.ts,tsx,css 聚合 md5 本地=远程 64f0016c…）
TSC_RC=0        strict tsc，零错误
VITEST_RC=0     Test Files 11 passed (11) / Tests 135 passed (135)
BUILD_RC=0      vite 生产构建成功
FE_LANE_EXIT=0  REMOTE_FE_EXIT=0
```

135 = 131 基线 + 新增 4 条「笔记本阶段判定 / 未知状态原样透出 / 时间倒序 / 各自入口」纯函数测试。

另：曾出现过一次 `TSC_RC=1 / NPM_CI_RC=1`，已定性为**车道跑错了机器**（详见上文工具观察），
不是代码缺陷；同一棵树直接重跑 `npx tsc -b` 返回 0。

子任务自称的「vitest 131/131」至此从 claims 变成实测事实（10 个测试文件全跑）。

读数陷阱：`TREE_MD5_MATCH` 的口径只覆盖 `app tests alembic` 下的 `*.py`（与 `source_lane.sh` 同口径），
前端源码是**另一条 tar 流**同步的。所以只改前端时该哈希不变是正常的，但也**不能拿它
当前端内容一致的证据**（前端一致性靠 FE 车道自身的 `tree_sync` 步骤与哨兵）。
前端本轮改动（`crypto.ts` 签名原文、`contractApi.sign` 时间戳、`workspace.ts` 菜单可见性、
`ComputeSchedule` 执行弹窗 + 运行日志抽屉、`enums.ts` 可选/历史拆分）全部包含在这棵树里。

### 复跑结论（已回填，权威）

```
SYNC_VERIFIED
TARGETED_RC=0
252 passed, 4 warnings in 118.86s (0:01:58)
```

即：SM2 摘要修正 + 三轨道新增面 + 全部受影响的验签调用方，在远程 harness（py3.12 + gmssl +
真实 PostgreSQL）上**零失败**。首跑的唯一失败项是 D-05 的配额断言口径，已按实现的
「合约当事方可见」契约重断言（不是改实现迁就想象）。

### V-6 真实微虚机链路的确切阻塞点（修正 D-04 的归因）

拿到 `qa_dev` 身份后直接实跑产品路径（`POST /notebooks` → `POST /notebooks/{id}/dev-session`）：
笔记本创建 `201`，dev-session `500 INTERNAL_ERROR`，容器日志给出完整堆栈：

```
app/services/cube_runtime_adapter.py:512 prepare_session_volume
app/services/cube_runtime_adapter.py:380 _master_ensure_volume
httpx.ConnectError: [Errno 111] Connection refused      # 指向 CDS_CUBE_MASTER_ADDR=http://127.0.0.1:8089
```

同时 `GET /api/v1/flow/runtime-status` 返回 `{"available": true, "ready_nodes": 1, "total_nodes": 1}`，
`ss -ltn` 确认 cubelet gRPC `:9999` 在 LISTEN。两个事实合起来修正了 D-04：

- **不是「节点 down」**：cubelet 在线、调度面自报就绪；
- 而是**创建会话时的数据卷准备需要 CubeMaster HTTP(:8089)**，而 cubemaster 依赖的
  mysql/redis/minio 三个单元在 crash-loop；
- 三个依赖的共同根因不是配置错误，而是 **dockerd 在跑但宿主 `DOCKER-FORWARD` 链已被清除**：
  `failed to create network support_default: ... iptables: No chain/target/match by that name`。
  修法是重启 dockerd（重建自己的链），需 root。

「ready_nodes=1 但仍创建不了沙箱」是本轮可复用的判据：runtime-status 只度量节点心跳，
不度量卷/镜像分发链路，不能当作“可以真实执行”的充分证据。

### V-7 全链基线（Cube 拉起前的逐步真实状态，脚本 `/tmp/e2e_full_chain.py`）

以 `qa_dev`（数据需方）身份跑完整产品链，每步只记真实状态码：

| 步骤 | 结果 | 读法 |
|---|---|---|
| 1 创建笔记本 | `201` | 正常 |
| 2 开开发沙箱 | `500` | 卷准备要 CubeMaster(:8089)，即 V-6 |
| 3 执行单元 / 4 整本跑 | `500` | 同样卡在没有会话 |
| 5 创建负载 | `201` | 正常 |
| 6 测试沙箱 | `200` 但 `passed:false`，`entrypoint_executed` 明细就是 `[Errno 111] Connection …` | **失败如实写进响应**，不伪报通过 |
| 7 固化发布 | `412 WORKLOAD_TEST_REQUIRED` | **发布门禁按设计拦住**（未过测不得发布） |
| 8 生产执行 | `200`，`status:failed` | 无已发布负载，不伪造成功 |
| 9 成果包列表 | `200` `items:[]` | 空就空，不编数据 |
| 10/11 审计列表与验真 | 待重跑（首跑用的 `/audit/events` 是我猜的路径，真实为 `/api/v1/audit/records`，已改） | 脚本自我校验 |

这张表的价值：它是“除 Cube 硬件外全链契约就位”的完整快照，而且逐行证明
**没有一处把模拟/失败伪装成成功**；Cube 拉起后同脚本重跑，目标就是 2/3/4 转绿、6 转
`passed:true`、7 不再 412。

### V-8 Cube 控制面起不来的真因：部署配置里残留一个已失效的宿主机 IP

修好 dockerd 的 `DOCKER-FORWARD` 链后，redis/mysql 立即 active，但 minio 仍起不来，journal 给出
真正的错：

```
failed to set up container networking: ... failed to bind host port 10.206.60.150:9000/tcp:
cannot assign requested address
```

本机现在根本没有这个地址（`ip -4 addr`：`wlp0s20f3 192.168.0.190`、`tailscale0 100.89.53.22`、
`tun0 2.0.1.130`）——它是一次性安装时探测到的宿主机地址（看形式是公司 VPN 分配的 10.x），
VPN 重连换了地址后，所有把它写进配置的单元都会在绑定时死：
`cube-proxy` 循环重启、`cubeops` 健康检查超时、`cubemaster` 因依赖不健而失败。受影响的是
安装器生成的 6 个文件（`support/docker-compose.yaml`、`coredns/Corefile`、`cubeproxy/{nginx.conf,
global.conf,docker-compose.yaml}`、`cube-lifecycle-manager/docker-compose.yaml`）共 11 处。

处理方式：先把 6 个文件整份备份到 `.bak-ipfix-<时间戳>/`，再把旧 IP 全量换成本机当前地址，
然后 `systemctl restart cube-sandbox-control.target`。这是安装器在“当前地址的机器上本来就会
写出的值”，不是手改逻辑。

可复用的判据：**一个单机微虚机集群的部署配置不该写死宿主机 IP**；IP 一变就整面崩，而且
症状出现在与网络无关的单元上（cubeops 看起来像“健康检查超时”，其实是它依赖的 minio 根本没监听）。
排查时看到 `cannot assign requested address` 应先 `ip -4 addr` 对地址，而不是去查防火墙。

另记一条环内观察（未改代码，因此不进缺陷表）：docker 重启后的几分钟内，`l-cds-api` 在
`docker ps` 里显示 `healthy`，但从宿主请求 `/health` 会挂住不返回（`curl` 000），约几分钟后
自行恢复（三列表接口现 200 / ~8ms）。当时容器内自测 `/health` 是 200，所以这是“中间件重启
后连接池/就绪态恢复期”，不是新缺陷；但它说明**容器自己的 healthcheck 与“宿主可用”不是同一件事**。

### V-9 成果包预览的远程权威验证

同树 `TREE_MD5_MATCH d94ecc0e95081c2f578ee69a767f96bd`（含新端点与新用例），跑在远程
harness（真 PostgreSQL + py3.12 venv，不是本地 sqlite 替身）：

```
BE_RC=0
52 passed in 65.06s (0:01:05)
  = tests/test_ss18_bundle_preview.py（7 条，新增）
  + tests/test_ss17_delivery_bundles.py
  + tests/test_ss15_flow_api.py
```

**一个 harness 读数陷阱**：前端第一轮跑出 13 文件 / 147 用例，比预期多一套。原因不是
代码重复，而是**同步是 tar 解包，只新增不删除**：本轮把 `previewModel.ts` 与其用例改名
移到 `src/utils/csvPreview.ts` 后，远程旧路径的文件仍在，vitest 把两份都跑了。删残留重跑后的
**权威前端数字：`TSC_RC=0`、`VITEST_RC=0`（Test Files 12 passed / Tests 141 passed）、`BUILD_RC=0`、
`FE_LANE_EXIT=0`**（141 = 135 基线 + 6 条 `csvPreview` 解析用例）；147 那一轮作废。
这也提醒：**重命名/删除文件后，远程树的“一致”不能只看 tar 解包成功**；后端的 `TREE_MD5`
只覆盖 `app/tests/alembic` 的 `.py`，前端侧目前只能靠车道自己的同步步骤 + 残留检查。

### V-10 单机共存的下一个坑：Cube MinIO 默认要宿主 9000，而 9000 是 ClickHouse 原生端口

清完 IP 后 minio 仍起不来，真正的错换了：

```
failed to bind host port 192.168.0.190:9000/tcp: address already in use
```

查占有者：`ss -ltnp 'sport = :9000'` 显示 `*:9000` 属于 `clickhouse-serv`——CDS 本地 `l-clickhouse`
是 host 网络，ClickHouse 的 native TCP 就是 9000。两个栈在一台机器上必然钉鱼。
修法用官方变量而不是手改生成物：`up-support.sh` 与 `minio-postcheck.sh` 都读
`CUBE_SANDBOX_MINIO_API_PORT`（默认 9000），因此在 `.one-click.env` 里设成 19000，并把
`CUBE_S3_ENDPOINT` 与 `Cubelet/CubeMaster` 的 `plugin/volume-s3.conf` 里的 endpoint 同步改端口
（`support/docker-compose.yaml` 是启动时生成的，改它会被覆盖）。

可复用判据：**`docker-compose.yaml` 这类安装器生成物不要手改**，先找它读的环境变量；
端口冲突时先 `ss -ltnp 'sport = :PORT'` 拿占有者名字（比“restart 一下看看”快很多）。，勿再“修”）

早前记录过一条「开发者菜单有 `/audit`，但 `RoleRoute` 把 buyer 弹回 `/`」——读源码后**推翻**：
`Sidebar.tsx:123-133` 的「审计中心」整组由 `hasAnyRole(role, ROLE_GROUPS.auditReaders)`
（= operator / regulator / admin）门控，`Sidebar.tsx:200` 的
`filterMenuByWorkspace(getMenuItems(role), workspace)` 是在**已按角色过滤后的列表上再做交集**；
`DEVELOPER_MENU_KEYS` 里的 `/audit` 只服务「本来就有审计读权限、但切到开发者视图」的用户，
对 buyer 不会渲染出菜单项。当时的结论是从常量推导的，没看消费侧。若按那条“修”就是**扩大审计
可见面**，属于安全回退，因此在此记为已排除项。
