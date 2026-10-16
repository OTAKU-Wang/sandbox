# Agent 交接手册 · 2026-10-16（SS-18 原型对齐进行中）

> 用途：换机器 / 换 agent 继续本项目时**先读这一页**。这里只放"读了就能干活"的事实与命令；
> 产品口径看 `specs/data-sandbox-developer-portal-prd.md` + `specs/prototype-v3.html`，
> 差距与计划看 `specs/ss-18-prototype-alignment.md`，缺陷与验证证据看
> `docs/defect-ledger-2026-10-16-portal-e2e.md`。仓库级约定仍以根目录 `AGENTS.md` 为准。

## 0. 现在的状态（一句话）

分支 `feat/ss-11-productization`；后端业务闭环已在**真 CubeSandbox MicroVM** 上跑通并留证（台账 V-14/V-15），
本轮把**沙箱目录契约**与**开发者工作台信息架构**收敛到原型口径（SS-18 §2 与 M1 已落地），
剩下的集中在 `specs/ss-18-prototype-alignment.md` §8 的 M2–M5 与后端 P0 三项。

## 1. 验证面（用户铁律：测试必须在远程跑，本地只当廉价探针）

```bash
# 一次性：口令/连接参数不进仓库，见 §6
export CDS_LOCAL_ROOT=$PWD                       # sync_tree.sh 用
export CDS_LANE_DB_URL='postgresql+asyncpg://<user>:<pw>@127.0.0.1:5432/cds'   # 值在远程 /root/cds.env 与 /root/cds-test/remote_test_env.sh

bash scripts/remote-harness/sync_tree.sh          # 全量同步 + 按 lane 口径核对树内容一致（TREE_MD5_MATCH）
ssh -p 22022 root@100.112.5.253 'bash /root/cds-test/source_lane.sh fast'   # 后端门：runtime/compile/import/alembic/parity/gate/ss15
ssh -p 22022 root@100.112.5.253 'bash /root/cds-test/source_lane.sh full'   # 再 + 全量（~28 分钟）
bash scripts/remote-harness/fe_lane.sh            # 前端权威：node:20 里 tsc + vitest + build
```

注意：车道脚本每次都要 `cat 本地 | ssh 'cat > 远程; bash 远程'`（脚本本身不在远程留权威副本）。
`source_lane.sh` 头部那行 `export PATH="/root/cds-test/venv/bin:..."` **不能删**——删了整个 lane 会退化到
`/usr/bin/python`（3.11），表现为 alembic "No module named alembic.__main__" + 把 PEP 701 的合法 f-string
报成 `f-string: unmatched '{'`，5 个步骤一起红（2026-10-16 实测踩过，见 §6 第 9 条）。

本地只跑廉价 oracle：`python -m compileall -q app tests`、`python -c "import app.main"`、
`npx tsc -b --force`。**不要**在本地跑测试来当结论（本地是 Python 3.14 + SQLite，见 §6 第 1 条）。

## 2. 本机开发面

| 面 | 位置 |
|---|---|
| 前端 dev | `http://127.0.0.1:3001`（`cds-frontend`，Vite，`/api` 代理到 :8000） |
| 后端 | `l-cds-api` 容器 :8000；`l-vault` 已挂 `transit` 引擎（否则 KMS 走 `KMS_UNAVAILABLE` 503） |
| Cube 控制面 | 四个 systemd 服务 active；卷插件 `cds-volume-plugin`；节点 `ready_nodes=1/1` |
| 演示账号 | `qa_dev`（角色 `buyer`，走开发者闭环）；管理侧 `cdsadmin`（`admin`）。**口令不在仓库**，在远程 `/root/cds-test/qa_deploy_a.sh` |
| 演示对象 | 合约 `0fc50ab3-1037-4fb5-bd04-ac682b80c3de`；notebook「浏览器全链验证」`e42fb967-7cfe-45f4-91fa-b8c93eb6a310` |

## 3. 浏览器自动化（browser-use CLI）

```bash
browser-use < /tmp/bu_nav.py        # 只读探针：URL / 侧栏标签 / 角色
/tmp/bu_edit_run.py                 # IDE：保存 → 运行全部 → 轮询执行卡片
/tmp/bu_cell2.py /tmp/bu_login3.py /tmp/bu_form_task.py
```

判据（踩过）：
- `browser-use <<'PY'` 里**不要**写跨行 JS 字符串拼接（SyntaxError）；写成 `/tmp/*.py` 再重定向进去。
- 判"点了没反应"的顺序：先看请求有没有发出（页面内 XHR 钩子 `/tmp/bu_login3.py`）→ 再看按钮 `disabled`
  与表单 `dirty` → 最后才怀疑后端。合成输入不会进入 antd 表单状态，会把它误判成"登录失败"（台账已记）。
- 自动化脚本都在 `/tmp`，换机器需要重建：模式是 `js(...)` / `click_at_xy(x,y)` / `type_text(...)` / `cdp(...)`。

## 4. 读代码时要知道的关键真相（都已实证，别再重新发现一遍）

1. **唯一执行路径是 l2c/CubeSandbox**。`bwrap`/`firecracker`/`k8s` 已退役，选择器 `disabled`
   （`ContractCreate.tsx:216`、`SessionList.tsx:244`），退役口径是 `LEGACY_LEVEL_RETIRED`。
2. **声明卷不进 guest**（上游冷启动限制，`cube_runtime_adapter.py` 注释自证）。所以：
   - 授权数据是 `inject_session_data` 经 exec 通道 base64 分块写进 VM 的**单个不透明文件**
     `/sandbox/data/session-data`，不是 per-contract 文件树；
   - `/sandbox/data` 的"只读"是**声明 + 采集面不取它**，不是 hypervisor 强制；真正的出域约束是 T5 出口网关；
   - exec 输出经 `/results/.cds-exec/<id>` 中转、由节点插件 `ReadSandboxFile`（rootfs 只读回环）读回，
     **这个目录永远不进成果包**。
3. **目录契约常量只有一个来源**：`cube_runtime_adapter.py` 的 `GUEST_DATA_DIR` / `GUEST_OUTPUT_DIR` /
   `GUEST_WORK_DIR` / `GUEST_EXEC_DIR` / `HARVEST_DIRS`。`/sandbox/output` 是唯一采集目录，
   `/scratch` 是工作区（兼容期仍参与采集，逐文件标源目录）。改路径必须同时改 `tests/test_cube_runtime_adapter.py`
   里按字面钉住的挂载表断言。
4. **没有 Task 实体**：`notebook` + `workload` + `compute_run` 的前端读视图
   （`cds-frontend/src/pages/Flow/devTasksModel.ts` 客户端并表并派生"调试中/已发布生产"）。
   原型的任务编号 / 进行中·已结束 / 调试·执行双内存 / 任务级导出方式都还没有后端落点。
5. **`/ops/*` 有路由门**（`App.tsx:106-109` 的 `k8sOpsReaders`），侧栏 `filterMenuByWorkspace` 只裁菜单——
   所以 URL 直达**不是越权**，缺口是"开发者视图对已具备该角色的账号不设防"（待补 `WorkspaceRoute`）。
6. **Webhook 签名是 HMAC-SHA256，不是国密**；`/open/v1/{slug}` 需要 API Key；默认存证后端是 `pg-log` 不是真链
   （UI 必须如实披露，见 `AuditVerifyModal.tsx`）。PRD 第四章那张"端点表"是发明的，**别引用**。

## 5. 待办（权威清单在 `specs/ss-18-prototype-alignment.md` §8）

- **M2** 任务列表卡片网格 + 筛选栏；**M2'** 新增开发任务全表单（11 字段 + 关联数据双 Tab）——**需要后端先给字段**（§8 P0-③）。
- **M3** 在线 notebook 调试页外壳（沙箱连接灯 / CPU·内存条 / 目录树含 `/sandbox/data` 锁标 / 底部控制台）。
- **M4** 任务详情页 `/flow/tasks/:id` + 执行确认弹窗 + 运行记录抽屉（T5 五步流水）。
- **M5** 结果集三通道资产中心 + **新增只读端点 `GET /deliveries/openapi/endpoints/{id}/calls`**
  （现在 `openapi.call` 连来源 IP 都不记，成功率/耗时无数据源）+ 审计日志详情字段化 + 存证验真弹窗。
- **后端 P0**：Task 字段（编号 / 双内存配额 / 导出方式，additive 迁移 0023）、`DeliveryBundle` 补
  T5 状态·水印状态·行数三列、端点调用明细端点；另有 6 处"空输入返回 200 + 真空为真"（ss-18 §8 列全）。

## 6. 踩过的坑（照着做能省几小时）

1. 本地 venv 是 **Python 3.14**，生产/远程是 **3.12**：3.14 延迟求值注解（PEP 649），3.12 在 def 期求值。
   本地全绿**永远**看不到类体内 `def list(...)` 遮蔽内建、注解里漏 import 这类错。改完 app 代码要在
   3.12 里做 import 冒烟（远程 lane 有 `import_smoke` 步骤）。
2. 远程哨兵：`ssh '...; rc=$?; echo RC=$rc'` 的命令串如果由程序生成成**双引号**，`$rc` 会被**本地**先展开成空
   ⇒ 哨兵恒空。数字要回日志正文核对。（同族：`cmd | tail; echo OK` 的 OK 永远是 0；`pipefail` 必须写进
   **最内层**那个 shell。）
3. 一条 tar 归档流只能被读一次：`tar czf - … | ssh 'tar xzf - -C A; tar xzf - -C B'` 的第二个 `tar` 收到空流，
   一个文件也不会落地。多目标 = 每目标一条流。
4. 全量回归**几秒钟结束 = collection error**，不是绿。
5. 给循环加守卫要照抄原循环的解包形状（`nb.code_cells()` 产出 `(index, cell)` 元组，按 `cell` 用会 500，
   而且只有"有输入"的路径才崩，本地编译完全看不出来）。
6. 写断言前必须读 handler 拿真实响应形状（猜 `{"uploaded": ...}` 结果 upload 分支根本不 return）；
   测试夹具里的 ID 格式要从真实证据取（`cubelet` 的 sandbox_id 是裸 32 位 hex）。
7. `edit` 工具多行替换：`oldText` 带尾换行而 `newText` 不带 ⇒ 静默把下一行并上来（语法仍合法，`compileall` 放行）；
   多行 `oldText` 少写几行 ⇒ 静默删掉兄弟行（曾吞掉 Sidebar 的 3 行导航，靠 `tsc` 抓到）。CJK 文档里
   还要防不可见字节漂移——改完必须回读 diff。
8. async SQLAlchemy：**不要**在请求内 `db.rollback()`（会 expire 整个 session，后续同步属性访问
   `MissingGreenlet`）；隔离单条语句用 SAVEPOINT。`onupdate=func.now()` 会让对象在 flush 期被 expire，
   写路径返回 ORM 对象前要 `await db.refresh(obj)`。
9. 车道全步同码失败（全是 rc=1 / 125）⇒ **怀疑环境不是怀疑代码**（PATH 丢就是这一类）。
10. 外包子任务的 `file:line` 在被写进仓库文档前必须自己复核：本轮三份只读审计里有一份声称
    "首页 `/home` 500 崩溃（`useWatch`/`DashboardProvider`/`QUICK_LINKS`）"——`src/` 里根本没有这些符号，
    `/home` 实测 200；另一份把 `/ops/*` URL 直达写成越权。
11. 空/纯空白输入的 200 是缺陷家族：`NOTEBOOK_NO_CELLS`（409）已修一处，`ss-18` §8 还列着 5 处。
12. sudo 口令只在当条命令内联使用，绝不写进任何仓库/文档/内存文件。

## 7. 不可动摇的口径（项目铁律）

- **禁伪造 / 诚实降级**：模拟路径必须 `ALLOW_SIMULATION` / `is_simulation` 披露；不得伪造 TEE/GPU/HSM/链。
  不得把 sim 结果当 real 展示，不得声称默认存真链。
- alembic 线性链**只增不改**；生产禁 `create_all`；`ci/known-failures.md` 基线不得增长。
- 基础设施改动要同步到 5 份部署配置（root 4 个 compose + `k8s/` + `helm/`）；新 `CDS_*` 开关也要同步。
- 审计日志与 Merkle 叶子**永不 purge**（retention 0 = 永久，导出归档）。
- `mpc_service` 是 Shamir 密钥托管，**不是**多方密文计算。
- 提交/推送只在用户明确要求时做（本轮 = 用户明确要求）。
