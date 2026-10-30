# 待修队列（自动生成自台账 + D 节已修清单）

总条目=319 已修=262 判为非缺陷=8 待修=49

| 域 | critical | high | medium-high | medium | low | latent | untagged | 合计 |
|---|---|---|---|---|---|---|---|---|
| backend | 0 | 0 | 0 | 0 | 17 | 14 | 0 | 31 |
| fe | 0 | 0 | 0 | 0 | 9 | 1 | 2 | 12 |
| plugin | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 4 |
| infra | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| deploy | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## backend / low (17)

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

## plugin / low (4)

- **A173** `sdk/cds_sdk/client.py` — SDK `_parse_error` 只接 `ValueError`，非 dict 的合法 JSON 直接 `AttributeError`（low-medium，活路径）
- **A215** `sdk/cds_sdk/client.py` — SDK 把标识符直接拼进请求路径，不做百分号编码（low，我亲验：全文件无编码调用）
- **A218** `sdk/cds_sdk/stream.py` — `stream_exec(timeout_seconds=…)` 不做校验，直接把 `ValueError` 丢给用户（low，我亲验行号，顺手修正了子代理的坐标）
- **A207** `sdk/pyproject.toml` — `sdk/pyproject.toml` 其余五条：依赖无上限、无 `py.typed`、版本双来源（low，打包卫生）

## infra / low (2)

- **A217** `alembic/env.py` — `ALEMBIC_COMPARE_TYPES` 只认字面 `"1"`：写 `true` / `yes` / `on` 都是**静默关掉**类型对比（low，我亲验）
- **A174** `alembic/versions` — 文档漂移：alembic 实际 head 已到 **0026**，AGENTS.md 与多处文档仍写 0021（low）

## 后端按文件聚合（给互斥修波用）

- `(unresolved)` (11) A227/low A230/low A236/low A238/low A239/low A240/low A251/low A259/latent A263/low A292/low A293/low
- `main.py` (1) A67/low
- `contract_service.py` (1) A73/latent
- `app_output_proxy.py` (1) A105/latent
- `app/api/certificates.py` (1) A106/latent
- `app/models/policy_bundle.py` (1) A125/latent
- `app/services/stream_audit.py` (1) A127/latent
- `app/services/k8s_client.py` (1) A128/latent
- `app/services/k_anonymity.py` (1) A131/latent
- `app/services/gpu_tee_runtime.py` (1) A132/latent
- `rev_imports2.py` (1) A133/latent
- `tests/test_scenario_engine.py` (1) A144/latent
- `field_mask.py` (1) A149/latent
- `webhook.py` (1) A170/low
- `pii_ner.py` (1) A194/latent
- `package.json` (1) A247/low
- `app/api/kms.py` (1) A271/low
- `app/api/federation.py` (1) A282/latent
- `capability_matrix.py` (1) A300/low
- `app/api/data_resources.py` (1) A310/low
- `data_resources.py` (1) A312/low

文件总数(后端待修)=21