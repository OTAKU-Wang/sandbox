# SS-19 · 按原型补全平台 + Docker 起服务 + 浏览器端到端（Jupyter 开发环）

> 用户 2026-10-16 指令（原文）：「我本地已经启动 docker 服务，我现在需要按照 `基础版数据沙箱原型/`
> 原型完整的开发平台功能，但是其中开发功能要使用 jupyter notebook 进行沙箱开发功能，
> 原型中设计的样子无法按照 jupyter notebook 开发完成。按照原型开发完成之后使用 docker 启动服务
> 并使用 browser use 进行 chrome 在线测试，实现完整的功能，能够达到 数据上传 → 生成数据目录 →
> 生成最终交付物 的完整业务流程。」
>
> 原型 = `基础版数据沙箱原型/` 10 张 PNG（本轮模型无视觉能力，界面真值取
> `specs/data-sandbox-developer-portal-prd.md` 的 18 界面规格 + `specs/prototype-v3.html` 文案层）。

## 1. 原型 18 界面 → 现网落点（本轮盘点的缺项清单）

| 原型 | PRD | 现网落点 | 状态 |
|---|---|---|---|
| 1 首页-平台总览 | 3.1 | `pages/Dashboard/index.tsx` | **缺**：现首页是运维/安全视角（安全态势/策略拒绝分布/链上存证/实时事件流），PRD 3.1 要的是开发者视角（问候卡 + 开发任务四宫格 + 最近 5 条任务 + 调试/执行内存进度 + 7 天趋势 + 最新结果集）。**排到最后做**（不影响业务闭环），先跑通 E2E |
| 2-1 我的开发-任务列表 | 3.2.1 | `/flow/tasks` `DevTasks.tsx` | **已有**（卡片网格 + 筛选栏，SS-18 M2） |
| 2-2 新增开发任务 | 3.2.2 | `DevTaskCreateModal.tsx` | **已有**（11 字段整表单 + 关联数据看板，SS-18 M2′） |
| 2-3 调试环境 | 3.2.3 | `/notebooks` `NotebookWorkbench.tsx` | **本模块主战场**：现是自研单元格 IDE，用户要求换成真 Jupyter Notebook |
| 2-4 任务详情 | 3.2.5 | `/flow/tasks/:taskId` `TaskDetail.tsx` | **本轮新建**（KPI 4 + 基本信息与合约 + 关联数据 + 运行记录 + 产出结果集），卡片新增「详情」入口 |
| 2-5 执行确认弹窗 | 3.2.6 | `Flow/ComputeSchedule.tsx` ExecuteModal | 已有 |
| 2-6 运行记录抽屉 | 3.2.7 | `TaskRunRecords.tsx` | 已有（详情页复用） |
| 3-0 结果集资产中心 | 3.3 | `/deliveries` `DeliveryCenter.tsx` 三 Tab | 已有 |
| 3-1 OpenAPI 接口列表 | 3.3.1 | 同上 | 已有 |
| 3-2 新建 API | 3.3.2 | 同上 | 已有 |
| 3-4 在线调试弹窗 | 3.3.4 | 同上（`callOpenApiWithApiKey`） | 已有 |
| 3-3 接口监控 | 3.3.3 | 同上 + `GET /deliveries/openapi/endpoints/{id}/calls` | 已有（SS-18 P0-③ 补的调用明细） |
| 4-1 操作日志 | 3.4 | `/audit` `Audit/AuditLog.tsx` | 已有 |
| 4-2 日志详情 | 3.4 | 同上 Drawer | 已有 |
| 4-3 Merkle 验真 | 3.4 | 同上（verify / merkle-proof） | 已有 |
| 5-1 开发指引 | 3.5 | `/flow/guide` | 已有（含目录契约常驻文案） |
| 5-2 配额 | 3.5 | `/flow/quota` | 已有 |
| 成果包列表列（3.3.1 要求 T5/水印/行数） | — | `DeliveryCenter` 历史表 | **本轮补齐**：后端 P0-② 已下发三列，前端之前没读（`display.ts` 加 `bundleReviewMeta`/`bundleWatermarkMeta`，空值=「未记录」不反推） |

## 2. Jupyter 开发环（已落码，以代码为准）

本轮之前的真值：`app/services/notebook_service.py` 只有**沙箱内探测**（`probe_jupyter()` 经执行器跑
`shutil.which('jupyter')`，回 `jupyter_available`/`jupyter_path`），`JUPYTER_PROXY_BASE_URL`
（`core/config.py:110`）全仓只有定义、没有消费方；没有 Jupyter 服务端、没有反代、没有 iframe。
单元格执行的既有通路 `sandbox_sessions.execute_in_sandbox`（静态扫描 → KMS 注入 → 场景运行时 → 输出 DLP → 审计）
原样保留，开发环走的是"写文件到会话工作区 + 打包采集"这条与 `cds_kernel` 平行的路。

本轮新增（真实实现，无模拟开关）：

| 部件 | 落点 | 职责 |
|---|---|---|
| JupyterLab 容器 | `Dockerfile.jupyter` + compose `cds-jupyter`（jupyterlab + pandas/matplotlib/scikit-learn；不映射宿主端口；`terminals_enabled=False`） | 容器隔离的开发环；`base_url=/api/jupyter` 与反代前缀逐字一致 |
| 网关层 | `app/services/jupyter_gateway.py` | 会话工作区解析（与成果包采集同一个 `files/`）、笔记本落盘（幂等）、合约授权数据注入 `input/`、一次性票据（JWT + Redis 单次消费，无 Redis 拒发）、签名 Cookie、kernel 归属台账 |
| 反代 | `app/api/jupyter_proxy.py`（根级挂载，先例 `session_stream`，不入 `GROUP_PREFIXES` = 机器凭据默认拒绝） | `POST /api/jupyter/tickets`（`get_business_user`）→ `GET /api/jupyter/login` 换 Cookie 并 302 → 全量 HTTP/WS 转发；上游令牌只由反代注入，不进浏览器 |
| 隔离补偿 | 同上 `_check`/`_guard_kernel` | 带路径的上游 API 必须落在本会话前缀内（自己过 `..` 语义，不指望 Jupyter 消化）；kernel 只放行经本代理为该会话创建的 id；`api/contents` 根列目录与 `terminals`/`shutdown`/`metrics` 一律 403；`api/sessions` 响应侧按前缀过滤 |
| 前端 | `Flow/JupyterDevEnv.tsx` + `services/flow.ts`（`api.request({baseURL:'/'})`，不新建 axios 实例） | 取票据 → iframe；等级徽章与「容器隔离，非 MicroVM」字样直接取后端 `dev_env.sandbox_level`/`microvm`，不在第二处硬编等级文案 |
| 等级开关 | `DEV_SANDBOX_LEVEL`（默认 `l2c`）+ `notebooks._ensure_dev_session` | 原本写死 `l2c` ⇒ 无 Cube 节点的部署整条开发环直接 `RUNTIME_UNAVAILABLE`；现可显式降到真实存在的等级，合约 `allowed_sandbox_levels` 仍是硬门 |

上游行为已在本机 jupyterlab 4.3.5 容器实测（带令牌直连，非推断）：`/api/contents/<path>` 正常；
`/api/contents/<path>?path=<别的目录>` 与 `/api/contents?path=...` 都 **404**（jupyter_server 2.x 不再吃 query 里的 path），
`/api/contents/<pfx>/%2e%2e%2f%2e%2e%2fs2/files`（编码后的 `../`）也 **404**（服务端拒绝编码分隔符）。
所以前缀强制这一层没有被查询参数绕开的通路，反代的 `..` 拒绝是第二道保险。

已知残留（如实记录）：上游是**单一共享** Jupyter 服务，笔记本里的代码能读自身进程环境变量拿到共享令牌
（该服务只在 compose 内网可达、未映射宿主端口，且反代剥 `Authorization`）；多租户生产必须改每会话独占实例。

## 3. 执行车道（决定 Jupyter 跑在哪，用户拍板中）

本机无 CubeSandbox 计算节点：`CUBE_DATAPLANE_ENABLED=false` ⇒ `l2c` 直接 `RUNTIME_UNAVAILABLE`（SS-13 不降级）；
L0/L1/L2/L3/k8s 适配器只在 `CDS_SANDBOX_LEGACY_ENABLED=true` 后注册（生产 `validate_security_config` 直接拒）。

| 车道 | 开关 | 隔离真值 | 备注 |
|---|---|---|---|
| legacy L3 bwrap | `CDS_SANDBOX_LEGACY_ENABLED=true` + 镜像装 `bubblewrap` | 容器内进程/命名空间隔离（**非** MicroVM） | 改动最小，`cds_kernel` 与 T5 采集全复用 |
| k3d 真容器 | `scripts/install-k3s.sh CDS_K3S_MODE=k3d` | Pod 级 | 需 k3d + kubeconfig 注入 api 容器 |
| 模拟梯 | `ALLOW_SIMULATION=true` | 无隔离（subprocess） | 全程标「模拟执行」，代码不是真跑 |
| 远程 Cube 节点 | `CUBE_DATAPLANE_ENABLED=true` + CA/签名密钥 | MicroVM（生产形态） | 需要用户给地址与凭证 |

## 4. Docker 起服务实况（本机 2026-10-16）

- `docker compose up -d postgres redis clickhouse minio opa vault` ⇒ **6 容器 healthy**（`MW_RC=0`）。
- 真修一处 compose 假钉：`minio/minio:latest` 在 Docker Hub 已不存在（`pull access denied`），
  三份 compose（`docker-compose.yml` / `.middleware.yml` / `.prod.yml`）统一改钉
  `minio/minio:RELEASE.2025-01-20T14-49-07Z`（与既有 `minio/mc` 同代）。
- `Dockerfile.api` 加 `ARG CDS_BUILD_SKIP_TORCH`（默认 0，CI/生产不变）：本机以
  `CDS_BUILD_SKIP_TORCH=1 docker compose build cds-api` 跳过 torch（PyPI 默认 wheel 连 CUDA 栈 ~3.5 GB，
  笔记本上下不动）。代码本来就在无 torch 路径上跑（`cpu_trainer` 在 `TRAINING_REQUIRE_TORCH` 下 fail-closed，
  启动日志如实披露），所以这不是"造一个能跑的假环境"，而是走已存在的诚实降级路径。
  **2026-10-17 补正（以实测为准，不要再喯这条命令）**：上面这条曾被主分支合并抹掉而本小节未跟着改，
  导致连续两次「构建成功」其实没产出新镜像（管道接 `| tail` 把 BuildKit 退出码吃了）；而默认 PyPI 下
  `nvidia-*` 数 GB 让重建 20+ 分钟，把机器压到 `compileall` 单文件 120 秒超时（看着像 pytest 卡死）。
  重新加 `ARG CDS_BUILD_SKIP_TORCH` + `grep -iv '^torch'` 试过，**无效**：`peft`/`transformers` 会把 `torch`
  连同 `nvidia-nccl-cu13` 照样拖回来，只剔一行只会得到与生产不一致的图 —— 已回退，该参不存在。
  本机真正管用的是换镜像源：`PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`（实测 ~18 MB/s，
  整包几分钟），镜像照旧含 torch、与生产口径一致。构建命令不要接 `| tail`（或改接
  `grep --line-buffered`），否则失败看不见。
- 前端镜像 `sandbox-cds-frontend:latest` 已构建成功（node 20-bookworm-slim builder + nginx runtime）。

### 4.1 本轮又修掉几处「在任何机器上都起不来」的部署硬伤

1. `opa:0.68.0` 是 distroless 镜像，里面没有 `wget`/shell ⇒ `docker-compose.yml` 的健康检查
   `wget --spider http://localhost:8181/health` 永远不过 ⇒ `cds-api`（`depends_on: opa: service_healthy`）永远起不来。
   改成与 `docker-compose.prod.yml` 同写法 `["CMD", "/opa", "version"]`。
2. vault 同时给了 `VAULT_DEV_*` 与 `VAULT_LOCAL_CONFIG`：dev 模式已用 `DEV_LISTEN_ADDRESS` 占住 8200，
   两份监听配置并存 ⇒ `bind: address already in use`，vault 退出 1，同样卡死 `cds-api`。删掉 `VAULT_LOCAL_CONFIG`。
3. 工作区卷拼写错：api 挂 `/tmp/cds_sandbox`（下划线），代码真值是 `/tmp/cds-sandbox*`
   （`BwrapAdapter`/`ProcessAdapter` 默认值），声明的卷**从未覆盖真目录**，成果物随容器重建丢。
   改按真值挂并拆两卷：`cds-workspaces:/tmp/cds-sandbox`（L3）+`cds-workspaces-l0:/tmp/cds-sandbox-l0`（L0）。
4. `k8s/minio.yaml` 与三份 compose 原来同钉已不存在的 `minio/minio:latest`（`pull access denied`），全部改钉
   `RELEASE.2025-01-20T14-49-07Z`。
5. `Dockerfile.api` pip 加 `--timeout 120 --retries 5`（实测会撞 TLS handshake timeout）并装 `bubblewrap`；
   `Dockerfile.jupyter` 需 `--allow-root`（基础镜像以 root 跑，jupyter_server 默认拒绝并让健康检查恒 unhealthy）。

### 4.2 开发环目录布局（与代码逐字对应，改一边必须改另一边）

| 容器 | 卷 → 路径 | 消费方 |
|---|---|---|
| cds-api | `cds-workspaces` → `/tmp/cds-sandbox` | `BwrapAdapter` 工作区根 |
| cds-api | `cds-workspaces-l0` → `/tmp/cds-sandbox-l0` | `ProcessAdapter` 工作区根 |
| cds-jupyter | `cds-workspaces` → `/tmp/cds-sandbox`（= `ServerApp.root_dir`） | `jg.JUPYTER_ROOT_DIR` |
| cds-jupyter | `cds-workspaces-l0` → `/tmp/cds-sandbox/l0` | `jg.layout("L0") == ("/tmp/cds-sandbox-l0", "l0")` |

把 L0 那卷挂在 root 下的 `l0/` 而不是平级，是为了让 `root_dir` 以内**只有沙箱工作区**：`/tmp` 下还有
`cds-uploads` 与 `data-load/`（dev_sandbox 解密后的明文供数），不能对笔记本可见。
`tests/test_jupyter_gateway_isolation.py::TestUnavailablePaths::test_supported_levels_match_compose_layout` 钉住这组常数。

## 5. 本机前端门口径（重要，别再踩）

`node` 解析到 `C:\Program Files\nodejs` = **v22.5.1**，而 `html-encoding-sniffer@6`（CJS）
`require()` 了 ESM-only 的 `@exodus/bytes@1.15.1` ⇒ `ERR_REQUIRE_ESM`，**vitest 一个用例都起不来**
（`require(esm)` 要 node ≥ 22.12）。本机可用的是 fnm 那份：

```bash
export PATH="$HOME/AppData/Roaming/fnm/node-versions/v22.17.0/installation:$PATH"
cd cds-frontend && node node_modules/vitest/vitest.mjs run        # 22.17.0 下全绿
npx tsc -b                                                        # 与 node 版本无关
```

## 6. 闭环跑通过程中发现并真修的缺陷（D-e2e-09…13）

| ID | 用户可见现象 | 根因（实测，非推测） | 修法 | 取证 |
|---|---|---|---|---|
| D-e2e-09 | 上传数据报 422，`detail:[{type:"missing",loc:["body","file"]}]` | `cds-frontend/src/services/api.ts` 请求拦截器无条件置 `Content-Type: application/json`；multipart 被该头破坏，FastAPI 拿不到 `file` 字段 | 拦截器对 `FormData` 删除该头（boundary 交给浏览器生成） | 同源对照实验：带头 ⇒ 422（与 UI 逐字一致）；不带头 ⇒ 200（资源 `b74ec903-84c7-4b10-902d-73b274f4c1db`，60 行，MinIO 落盘）。新增 `src/services/__tests__/api.test.ts` |
| D-e2e-10 | 开发环面板写「已注入 1 个文件到 input/」，笔记本单元格里 `glob('input/*.csv')` 为空 | `jupyter_gateway.url_prefix_for()` 把对用户暴露的目录指到**会话工作区根**，而笔记本/供数/产物都在 `files/`；kernel 的 cwd 因此比笔记本高一层，相对 `input/` 指到工作区那个永远为空的 `input/`（顺带把 `.security_config.json` 暴在文件树里） | 前缀补 `files/`（与该测试文件既有常量 `PREFIX = "u-1/s-1/files/"` 一致） | kernel 内 `os.walk` 实测 `input → []`、`files/input → [<resource>.csv]`；`tests/test_jupyter_gateway_isolation.py` 新增两例 |
| D-e2e-11 | 开发环写完产物后「生成成果包」报 `NO_OUTPUT_FILES`(409)，但产物在盘上 | 采集走 `session_files.list_files()`，只看 `files/` 顶层（`p.is_file()`），写在 `files/output/` 的文件不入库；而面板明确建议产物放 `output/` | 新增递归采集 `list_output_files()` + 按相对路径读回 `read_output_file()`（排除 `input/` `tmp/`、索引与隐藏项，**不跟软链**）；`deliveries._collect_session_files` 换用它们 | `tests/test_delivery_harvest_nested.py`（嵌套采集、排除项、软链跳出不入包、缺 DEK 拒读、穿越名拒绝、`_collect_session_files` 集成） |
| D-e2e-12 | 合约在 UI 里 SM2 签署 400，手工清 localStorage 后立刻成功 | 前端盲信 `localStorage['cds_sm2_privkey_<uid>']`；服务端公钥被后续 `/auth/generate-sm2-keys` 轮换后旧私钥仍被使用，只在验签环节失败 | **未修**（需前端做「失败 ⇒ 丢本地私钥 ⇒ 重拉公钥 ⇒ 重签一次」），已列入 §8 | 浏览器实测 400 ⇒ 清 key ⇒ 200（`contract_sign` 审计行） |
| D-e2e-13 | 本机无 Cube 节点时，L3 合约只能走 API 创建 | 合约创建 UI 固定 `l2c`（SS-13 单一生产路径、禁止降级，设计如此） | 不改动；本机演示用 API 建 L3 合约（本轮 `7fe35b4a-0f63-4acb-87b5-c0c3b4a5555e`） | 签署后合约 `active`，开发环可用 |

## 7. 浏览器端到端实况（本机 2026-10-17，Chrome 附屏截图 `D:/workspace/.cds-e2e/shots/`）

主链（全部真接口，无模拟开关）：产品向导 4 步 ⇒ 产品 `06d5fc52-d329-4aa1-afc4-6a40f250e2aa` ⇒ admin 审核通过
⇒ 供应商发布到目录 ⇒ L3 合约 `7fe35b4a`（modes `develop,query`）⇒ 买方/供应商 UI 内 SM2 签署 ⇒ `active`
⇒ 买方 `/flow/tasks` 新建任务（调试方法「原生 Jupyter」，导出「成果集打包导出」）⇒ `POST /api/v1/notebooks` 201
（任务 `9ca42024-3cd8-43bd-ab8e-1ccaf969dcc1`，dev-session `fde4caee-d867-4b92-8510-cddb91cceaa4`）
⇒ 「打开 Jupyter 开发环」`POST /api/jupyter/tickets` 200 ⇒ iframe 为真 JupyterLab（`title="JupyterLab"`）
⇒ kernel 内 pandas 读 `input/*.csv` 写 `output/*.csv` ⇒ 成果包 ⇒ 下载 ⇒ 审计与存证。

截图编号：00–26 / 40–44（上一轮）、`54`–`79`（上传→目录→合约→签署→任务→开发环）、
`80` kernel 执行、`81` PRD 3.1 开发者工作台、`82` 3.2.1 任务列表、`83`/`85`/`86` 3.3 成果交付（三个页签）、
`87` 3.4.1 审计列表、`88`/`89` 日志详情抽屉、`91` 锚定、`92`–`94` Merkle 验真。

开发环目录布局实测（与 §4.2 逐字对应）：kernel 的 cwd = 对用户暴露的 `files/`；
`files/input/<resource_id>.csv` = 合约供数；`files/output/` = 产物；`files/tmp/` 不入包。

审计与存证取证：`/audit` 导出按钮落下 `~/Downloads/audit_export_30d.csv`（42,250 B）；
日志详情抽屉对缺失字段逐字写「未记录」；`user.login` 锚定后行内变「已锚定」，
存证验真弹层给出 SM3 叶子哈希、重算 root、存证 TX 与 Merkle 路径，并如实标注
「本地防篡改校验通过（非联盟链上链）」（T8 口径，不谎称上链）。

## 9. 本段实测新增（2026-10-17 下午，本机口径）

**已跑通（Chrome + docker，截图 99–118，目录 `D:/workspace/.cds-e2e/shots/`）**
- 买方在 `/flow/tasks` 向导内建任务（合约 uic5、调试方法「原生 Jupyter」、导出「成果集打包导出」，
  关联数据表自动列出已发布产品 60 行）→ `/notebooks?task=…` → 「打开 Jupyter 开发环」
  票据 200 + iframe `title="Jupyter 开发环"` + 「已注入 1 个文件到 input/」（截图 115）。
- 真 JupyterLab kernel（`POST /api/jupyter/api/kernels` + WS）内 pandas 聚合成功，
  cwd = `…/<session>/files`（D-e2e-10 修复生效），`output/区域客单价汇总.csv` 写盘可见（截图 103）。
- 合约交付配置 UI（`修改配置` → 勾选「数据集交付」→ 保存）写入 `contracts.delivery_methods`
  = `["dataset"]`，对 active 合约同样生效（psql 核对，截图 109/110）。

**新发现缺陷（未修，需下一轮）**
- **D-e2e-14 供数注入静默写出密文（已真修 + 实测）**：根因在 `storage_service.download()`——
  它自己 `raise ValueError("Key ... not found in KMS")`，却被同一个
  `except (ValueError, struct.error): pass` 吞掉，于是信封对象在密钥丢失时
  **原样返回密文**，`inject_contract_data` 报「已注入 1 个文件」、kernel 里 `read_csv`
  直接 `UnicodeDecodeError`。修法：拆成三层——不是信封格式才返回原文；信封在而密钥不
  在、或 GCM 解密失败，一律 `raise ValueError`（带上 key_id 与对象路径）。
  新增 `tests/test_storage_envelope_fail_closed.py` 4 例，**改前 2 failed / 改后 4 passed**
  （用 `git stash push -- app/services/storage_service.py` 回到 HEAD 版本跑的改前数字，
  本机 `.venv-gate` py3.12 口径）。
  真表面复验（本机 Docker）：对同一 notebook 再发票据，现在返回
  `injected: []` 与
  `inject_errors: ["数据资源「UI 探针 0bc7」供数读取失败：ValueError: 密钥
  dek-storage-data-resources/b74ec903-…/ui_probe.csv-2ca99463 在 KMS 中不存在，
  数据无法解密（可能是密钥为内存态且已重启）：minio://cds-data/…"]`。
  仍开放的根因底层：KMS 主密钥为内存态，重启即丢（B 项台账），本轮只消除
  了「丢了不报」这一层。
- **D-e2e-15 打包腿 403 `CONTRACT_MISMATCH`（已真修 + 已闭环）**：根因已钉——
  `sandbox_sessions.contract_id` 是 `String(255)`（数字合约 ID），`contracts.id` 是
  `UUID(as_uuid=True)`，旧代码 `session.contract_id != contract.id` 是 str 跟 UUID 比，
  **恒为 True**，所以数据集打包对任何会话都 403。修法：抽出 `_ensure_session_contract()`
  两侧统一 `str()` 再比（不改列类型，不动 alembic 链）。新增
  `tests/test_delivery_bundle_contract_match.py` 4 例（同值 str/UUID 必须放行、异值仍 403、
  空值保持宽松）；修复前该断言必抛（`str(c) != c` 真）。
  配套：`tests/test_delivery_harvest_nested.py` 补 Windows `ModuleNotFoundError: resource`
  的模块级 skip（保留测试，不删不跳平台以外用例）。
  本机口径（`.venv-gate` py3.12）：`test_delivery_bundle_contract_match` +
  `test_delivery_harvest_nested` + `test_jupyter_gateway_isolation` = **28 passed, 1 skipped**
  （skip = harvest 用例需 POSIX `resource`）；未走远程 py3.12 车道，不代表门/基线数字。

### 9.1 闭环已走完（截图 99–122，本机 Docker + Chrome）

上传→发布→签约→建任务→真 JupyterLab 写产物→打包→下载→审计，逐腿证据：

| 腿 | 证据 |
|---|---|
| 建任务（向导） | 合约 uic5 + 「原生 Jupyter」+「成果集打包导出」，关联数据表自动列 60 行产品（截图 113） |
| 开开发环 | `POST /api/jupyter/tickets` 200、iframe `title="Jupyter 开发环"`、「已注入 1 个文件到 input/」（截图 115） |
| kernel 真执行 | kernel cwd = `…/<session>/files`，`output/区域客单价汇总.csv` 写盘（截图 103） |
| 成果包 | `0d3a449c…` / 2 文件 / 5 行 / 1.8 KB / T5「DLP 审查通过」/ 水印「未嵌入水印」（截图 120） |
| 下载 | `GET …/dataset/0d3a449c…/download` 200，落盘 `~/Downloads/cds-bundle-0d3a449c-….zip`；包内 `files/output/区域客单价汇总.csv`（嵌套采集，D-e2e-11 生效）+ `files/*.ipynb` + `MANIFEST.json`，CSV 内容与 kernel 输出逐值一致（截图 121） |
| 审计 | UI `/audit` 可见 delivery/download/bundle/jupyter_ticket 行（截图 122）；psql：`delivery.dataset.bundled` 1、`delivery.dataset.downloaded` 1、`notebook.jupyter_ticket` 25 |

诚实披露两项（不是缺陷但不得当成通过）：水印列显示「未嵌入水印」，因为 `.csv` 不在
`TEXT_WATERMARK_SUFFIXES`（既有行为，已记录）。中途一度靠 `docker cp` + `restart` 热更验证，
已收收：重跑 `docker compose build cds-api`（清华源）两次并 `up -d`，最终容器跑镜像
`f6dc437d`，用 `docker exec ... grep -c` 逐项确认 D-e2e-11/14/15 三处修复已在镜像内（1/2/2）。
在该最终镜像上重跑交付腿（截图 123–125）：成果包 `0e562008…` 2 文件 / 5 行 / 1.8 KB /
DLP 审查通过，`~/Downloads/cds-bundle-0e562008-….zip` 1864 B 落盘，`/audit` 11 行含
`bundled` / `downloaded` / `jupyter_ticket`。**镜像==代码==取证环境** 现在同时成立。


**图片真值通道（已打通，10/10 张全部逐字转录完成）**：本会话与子代理的 `read` 图像通道均被拒（模型 text-only），
但子代理发现**本机 vLLM 端点实为多模态**，用内容已知的控制图验证后逐字产出真值，每张带
bbox / ink 投影证据，看不清一律标【不清晰】：
`img-specs/group-A.md`（首页 / 2-1 / 2-2，25 KB）、`group-B.md`（2-3 / 2-4，20 KB）、
`group-C.md`（3-1 / 3-2 / 3-3 / 4-1 / 4-2，23 KB）与 `group-C1.md`（OpenAPI 三屏重复采样，18 KB）。
代码核对后的差距清单见 `D:/workspace/.cds-e2e/gap-ledger.md` G1–G15。
已落地的第一条：监控 KPI 标题按图补单位后缀「调用总次数（次）」（`DeliveryCenter.tsx:1011`，
`npx tsc -b` 本机口径 0 错）。



> **真值口径变更（2026-10-17，用户明示）**：界面最终标准改为
> `基础版数据沙箱原型/` 里的 **10 张 PNG 截图**（首页1 / 我的开发2-1…​2-4 / 结果集 OpenAPI 3-1…​3-3 /
> 安全 4-1…​4-2），`prototype-v3.html` 与 PRD §3.1–3.5 的「18 界面」口径降为参考。
> 本节下方按旧口径写的行保留作历史，新差距表见 §9。例外：2-3「调试环境（Python 脚本）」
> 的编辑器按用户要求换成真 JupyterLab，但截图里编辑器**周边**元素（数据面板/运行停止/
> 配额/日志/状态条）仍需按图补齐。

| ID | 差距 | 状态 |
|---|---|---|
| P-01 | 3.1 开发者工作台、3.2.1 任务列表、3.3.5 接口监控抽屉、3.4.1 审计导出/详情 | 本轮已补并进入前端镜像（`tsc -b` 0 错）；截图 81/82/87–94 |
| P-02 | 3.3.5 抽屉在浏览器侧未拿到数据行 | 该合约尚未建 OpenAPI 端点；组件由单测覆盖，本轮未造数据假证 |
| P-03 | `/audit` 对数据使用方（模型工程师）角色门 | 实测：买方账号访问 `/audit` 被路由守卫回落至运营概览（截图 84）——需业务确认是否放开（拍板项 3） |
| P-04 | 产品向导不从绑定资源自动带出行数/Schema | 未修（拍板项 4） |
| P-05 | `api_endpoints` / `webhook_subscriptions` 缺产品化字段 | 需 alembic 新 revision（拍板项 2） |
| P-06 | KMS 密钥为内存态，api 容器重启后旧会话密钥失效 | 需持久 KMS/信封接入（`kms_recovery` 尚未接到 compose） |
| P-07 | 上游是单一共享 Jupyter 服务（见 §2 残留） | 多租户生产必须改每会话独占实例 |

待用户拍板：(1) 是否为 PRD 口径新增 4 个聚合端点；(2) 是否允许给 `api_endpoints`/`webhook_subscriptions` 加列；
(3) 3.4 角色门是否放开到数据使用方；(4) 产品向导是否自动带出资源行数/Schema。

### 8.1 本机口径数字（未经远程 py3.12 车道，不作对外口径）

- 后端：`tests/test_jupyter_gateway_isolation.py` 24 passed（新增 2 例）、
  `tests/test_delivery_harvest_nested.py` 本轮新增（数字见行末）。
- 前端：`npx tsc -b` 退出 0；`docker compose build cds-frontend` 成功（镜像内含 `tsc -b && vite build`）。
- 前端 vitest 全量：与镜像构建并发那次为 `10 failed | 240 passed (250)`，失败集中在
  `Flow/__tests__` 的 `Test timed out in 5000ms`（CPU 争用）；无并发重跑取作口径。
- 本轮模型无视觉能力：截图作为产物归档，视觉判定一律以 DOM 文本/结构断言为准（不声称人眼验收）。

#### 8.2 G1/G2 落地后的本机口径数字（2026-10-17 晚，覆盖上文 vitest 旧数）

`.venv-gate`（py3.12）因 protobuf 冲突不可用，后端数字改由**生产同源 py3.12 镜像 + 真 PG16 容器**取，逐条取退出码不经管道：

- `PYTEST_RC=0` — **31 passed**（`test_notebook_data_subset.py` 7 例 + `test_jupyter_gateway_isolation.py` 等回归）。
- `PARITY_RC=0` — `SCHEMA PARITY OK: 54 tables, columns identical`（`scripts/verify_schema_parity.py`）。
- `UP_RC=0` — `alembic upgrade head` 0024 → `0025_notebook_data_resource_ids` → `0026_notebook_description`；`CHECK_RC=0` — `No new upgrade operations detected.`
- 前端 `npx tsc -b` **TSC_RC=0（0 条 error）**；`vitest run` 全量 **28 files / 259 tests passed, exit 0**。
  其中整页新用例在并行负载下越 5s 默认闸，把该 `describe` 的 `timeout` 抬到 20s（断言与等待条件一字未改、无 sleep）。
- 能失败性：`git stash` 掉后端三文件 → `TestSubsetAtApi` 4 failed / RC=1；对 gateway 做可控变异 → `TestSubsetAtGateway` 3 failed，还原后 7 passed / RC=0。
- compose PG 原本没有 `alembic_version` 表（历史由 `create_all` 起）→ `alembic stamp 0024_bundle_review_columns`(RC=0) 再 `upgrade head`(RC=0)，`\d notebooks` 见 `data_resource_ids json` / `description text`，`alembic_version.version_num = 0026_notebook_description`。
- 两镜像 `Built` 后 `docker compose up -d cds-api cds-frontend`(UP_RC=0)，界面复验跑在真容器上。


### 9.2 图片真值对齐（前端，本机口径）

真值改以 `基础版数据沙箱原型/` 的 10 张 PNG 为准（不是 prototype-v3.html）；逐屏规格见
`D:/workspace/.cds-e2e/img-specs/group-{A,B,C,C1}.md`，差距清单见 `D:/workspace/.cds-e2e/gap-ledger.md`（G1–G22）。

本轮已改并在**真容器 + Chrome** 复验（前端镜像 `9556dae7`，截图 126/127）：

| 图 | 屏幕上实测到的 | 取证 |
|---|---|---|
| 4-1 | 面包屑 安全管理 / 操作日志；标题「操作日志」+ 灰字说明；表头 操作时间/操作类型/操作人/来源 IP；分页「共 309 条记录」；按钮「导出日志」；页签 全部/创建调试沙箱/生产运行申请/沙箱执行/结果集导出/数据调用 | 截图 126 |
| 2-2 | 字段标签全部改为图上文案（合约名称/模型调试方法/结果导出方式/调试环境内存（G）/执行环境内存（G））；**必填星号实测 6 个**；主按钮「完 成」 | 截图 127 |
| 3-3 | KPI 标题补单位后缀「调用总次数（次）」 | 容器产物 grep 命中 |
| 1 | KPI「开发任务」→「开发任务总数」 | 容器产物 grep 命中 |

验证口径（全部本机，未走远程车道）：`npx tsc -b` 0 错；vitest **27 files / 250 tests passed**
（Node v22.17.0，52.75s）；DevTasks 7 passed（改前 3 failed，根因是 jsdom 不跑 antd 下拉收起动画，
"第一个非 hidden 下拉"会误点上一个选项，改为按选项文本认）；Audit+Layout 23 passed。
测试侧只改断言不删用例（`AuditLog.test.tsx` 三处按钮名、`DevTasks.test.tsx` 必填与默认开关联动）。

取证账号说明（不是缺陷）：`/audit` 受 `ROLE_GROUPS.auditReaders` 限制，buyer 访问会被路由踢回 `/`
（实测 buyer 身份下 crumb 停在「仪表盘」）。为取证新建 `ss19ops`（注册后 `update users set
role='operator'`），属本机测试夹具。

#### 9.2.1 G1/G2 已做（2026-10-17 拍板：向导改整页、关联数据做完整跨端子集）

后端：`Notebook` 增 `data_resource_ids`(JSON) 与 `description`(Text)，迁移 `0025`/`0026` 线性追加；
`app/api/notebooks.py::_resolve_data_resource_ids` 把勾选子集与合约授权集合求交，越权/未知 id **整单 422
`VALIDATION_ERROR`**（复用既有码，未动 error-codes.md 四处同步）；`jupyter_gateway.inject_contract_data(..., resource_ids=)`
返回 `{written, errors, subset, excluded}`，`app/api/jupyter_proxy.py:142-169` 把 `inject_subset`（子集列表）与
`inject_excluded`（被排除**条数**）写进 `notebook.jupyter_ticket` 审计。

前端：新增整页 `pages/Flow/DevTaskCreatePage.tsx`（路由 `flow/tasks/new`，四卡 + 底部「取消 / 完成」），
`DevTasks.tsx`、`DevHome.tsx` 改跳导航、原弹窗容器下线；`DevTaskDataPicker.tsx` 做「我的数据 / 已授权数据」双页签
+ `已选择数据（N个）` / `清空` / 逐条 `移除`，未授权项 `disabled` 并给原因。两处拒绝按图造假：图上「10 万行建议 16G」
改成合约已批准的内存上限读数，「所属节点」在库里无字段处显示 `UNKNOWN_TEXT`。

真容器 + Chrome 取证（截图 129–140，全文见 `D:/workspace/.cds-e2e/evidence-g2-subset.md`）：

| 取证点 | 屏幕上/接口实测到的 | 截图 |
|---|---|---|
| G6 面包屑 | 根路由 `仪表盘` 无 chip；`/flow/tasks` 末段带 chip | 129 |
| G1 整页 | `/flow/tasks/new` 四卡 + 底部「提交后可在…取消/完成」 | 130 |
| G2 勾选面板 | 双页签列出 2 张授权表，只勾一张 → `已选择数据（1个） / 清空 / 子集取证产品 4a66e3-1 / 2ab465ca / 移除` | 133、138 |
| 落库 | `TASK-20261017-006`：`description` 已存（0026 端到端可用）、`data_resource_ids = ["88057f46…"]` 只含勾选那张 | 134 |
| 开发环 | 票据 200，iframe 落 `/api/jupyter/lab/tree/<buyer>/<session>/files/SS-19 子集取证 LIVE 任务.ipynb` | 139、140 |
| 子集真生效 | 审计 `injected=[2ab465ca….csv]`、`inject_errors=[]`、`inject_subset=[2ab465ca…]`、`inject_excluded=1`；浏览器内 Jupyter Contents API 读 `files/input/` 仅 1 个文件（174B），同合约另一张授权资源未落盘；容器侧同源核对同一目录 | 138–140 |

未做（如实）：G5/G6 面包屑蓝底细则待逐像素对齐。本节当时以「后端给不出」为由拒绝过的三处，
已在后两轮按真能力补齐（口径以代码为准）：`date_from/date_to` 见 §9.2.2，「操作结果」列由
`audit_outcome()` 派生，类型页签改回图上的「进入生产环境」并映射到库里真会写入的 action。


### 9.2.2 审计日志屏 4-1 / 4-2（D-e2e-19，2026-10-17 第二轮）

界面真值 = 原型 PNG 的逐字转录 `.cds-e2e/img-specs/group-C.md`（图 C-4 / C-5）。这一屏的关键结论是：
**图上要的筛选与列，后端原先给不出**，所以顺序必须是先补后端能力、再改界面，前端不许自己演。

后端（`app/api/audit.py`）：

- `GET /audit/records` 新增 `date_from` / `date_to`（`created_at` 闭区间；naive 入参按 UTC，与 timestamptz 对齐）、
  `keyword`（`action`｜`resource_id`｜`users.username`｜`users.email` ILIKE，outerjoin User）、
  `outcome`（`success`/`failure`）、`event_type`（`Query(pattern)` 由 `AUDIT_EVENT_TYPES` 生成，不重复列一遍枚举）。
- `audit_outcome()`：库里没有结果列，唯一可用真值是项目自己的写入约定——失败/拒绝/阻断路径一律写独立动作名
  （`sandbox.create_failed`、`sandbox.key_distribution_denied`、`openapi.call_failed`、`delivery.dataset.blocked`…），
  成功路径写本名。标记集取 `failed/denied/rejected/blocked`，且用例机检「每个标记都必须在 `app/` 真实动作词表里出现过」，
  防止留下一个从不命中的标记（`refused` 就是这么被第一次运行逮到并删掉的）。
- `AUDIT_EVENT_TYPES` = 4-1 四个类型页签的唯一真值源，用例断言其值全部是代码真会写入的 action（禁伪造：不得为凑页签造名字）。
- 列表与详情都 join `users` 出 `username/organization/role`（「操作人」列与 4-2 的 `姓名（单位 / 角色）`）；
  `display_no` = `LOG-{created_at UTC}-{id 前 4 位}` 是**展示编码**而非业务单号（UI 写明推导规则）；
  `/audit/records/{id}` 额外真查 `contracts` 给 `related_contract`，查不到返回 null。

前端（`cds-frontend/src/pages/Audit/`）：`auditDisplay.ts`（页签/标签/环境/关联数据/耗时的取值单源）+ `AuditLog.tsx` 重写
（六列表、筛选四件套 draft→applied 两段式、五页签发 `event_type`、`共 N 条记录` + 10 条/页 + 跳页），
4-2 抽屉改 720 弹窗且**没有值的行整行不显示**、页底点名缺了哪几项；防篡改的「存证验真 / 锚定本条」从表格 checkbox
迁到弹窗底部（图上只画了「关闭」，能力保留、位置让位，已在台账记为有意偏差）。

验证（**本机口径**：`.venv-gate` protobuf 冲突不可用，取生产同源 py3.12 镜像 + 真库；node 为本机 22.5.1）：
`.cds-e2e/audit-backend-gate.sh` → `COMPILE_RC=0` / `PYTEST_RC=0`（38 passed）/ `PARITY_RC=0` / `UP_RC=0`；
同命令内 `tests/test_e2e_data_loop.py` 一条失败为 `bwrap: Creating new namespace failed`（容器无建 namespace 权限，
沙箱执行腿环境性失败，未 skip 未改码）。前端 `npx tsc -b` TSC_RC=0、`npx vitest run src/pages/Audit` 7 passed / VITEST_RC=0，
其中一条用例当场抓出改页面时漏挂的 `导出日志 onClick`（按钮可点但不发请求）。

### 9.2.3 结果集三屏 3-1/3-2/3-3、调试环境外框 2-3 与首页 A-1（D-e2e-20，并行三手）

界面真值 = `.cds-e2e/img-specs/group-C.md`（图 C-1/C-2/C-3）、`group-B.md`（图 1）、`group-A.md`（图 A-1）。
逐屏细节与逐条缺口在 `.cds-e2e/gap-ledger.md`「结果集三屏 / 调试环境外框的 1:1 收口」段，此处只记会改变后续判断的结论：

- **全站内建文案之前是英文的**（真缺陷）：`antd/locale/zh_CN` 是 CJS 壳，打包产物里 `default` 拿到的是
  `{ default: { … } }`，交给 `ConfigProvider` 不报错、只让空表显示 `No data`、分页显示 `10 / page`。
  现由 `src/antdLocale.ts` 显式脱壳（并按图把 `jump_to` 从 antd 自带「跳至」覆盖成「前往」），
  实机 DOM 复验为 `共 N 条记录 … 10 条/页 前往 页`。
- 3-2 的 `安全保障 / 查询模式 / 查询范围`：后端端点记录里没有对应字段（用例锁住 `ip_whitelist`、
  `query_mode`、`expires_at`、`slug` 一律不发），故按图渲染但**置灰不可交互**并注明「此处不可配置，
  平台实际行为是不限制来源 / 开放查询」；图上默认选中的「白名单」不照抄，因为把它画成选中态等于
  宣称一个不存在的强制能力（禁伪造）。同理，`结果集字段` 表在 `response_schema` 为空时整表写
  「后端未下发结果集字段结构」，不拿图里的 `query/content/thinking` 三行冒充。
- 2-3 的文件树来自真 Jupyter Contents API。两处接口事实按反向代理代码纠正（任务书原假设是错的）：
  裸形 `api/contents?path=` 被 `jupyter_proxy._check` 硬 403（只放行本会话前缀）、`?token=` 在
  `_upstream_url` 被剥掉（真实鉴权是 `/login?ticket=` 换的 `cds_jup_ctx` Cookie），
  `/api/jupyter/files/<path>` 同样 403 —— 所以目录请求挂在 iframe `onLoad` 之后、文件行用 `lab/tree/<path>` 深链。
- 首页 A-1 的 KPI 是 **4 张**（`开发任务总数 / 结果集导出接口 / 今日数据调用次数 / 今日操作日志`），
  不是此前记录的 3 张；`今日操作日志` 与 4-1 列表同源（`date_from` = 当天零点、`page_size=1` 读 `total`），
  - 图 2-4 运行记录卡上的「最多保留 90 天」**照图原文保留**（图里逐字就有这句），但同一屏另有一句来源披露：
  后端只有审计日志/告警/Merkle 叶子的清理任务，运行记录既无保留期字段也无清理（`devTasksModel.ts` 的
  `RUN_RECORD_RETENTION_NOTE` / `RUN_RECORD_RETENTION_SOURCE` 两个常量分开写，两句都有用例锁）——
  既不擅自删图上文案，也不把一个不存在的清理能力当成平台行为。
- **门纪律的一次实证**：本轮一次全量 vitest 报 `5 failed`，全部来自我在子代理仍在写该文件的同一分钟
  启动了收集（读到改到一半的旧内容）；单跑该文件 13 passed。**产物的生成者与门的使用者不得并发**。

## 10. 如实缺口与待拍板清单（本机口径，全部有上文证据）

### 10.1 已发现、未修（按影响排序）

1. **D-e2e-16 建单与开会话口径不一致**：提供方 `POST /notebooks` 得 201（TASK-20261017-005），
   随后申请 Jupyter 票据被 403「Only the contract buyer can create a session」拒。要么建单侧补
   buyer 前置校验（新增错误码要走 error-codes.md + API + 前端 + SDK 四处同步），要么放开 provider
   开会话（动隔离与供数审计口径）。两个方向都改语义，等产品定，见 gap-ledger.md D-e2e-16。
2. **KMS 主密钥为内存态**：容器重启后既有信封对象不可解密。D-e2e-14 只消除了"丢了不报"，
   现在会如实报「密钥 … 在 KMS 中不存在，数据无法解密（可能是密钥为内存态且已重启）」，
   但老对象仍读不出来。根因修法是主密钥落盘/接 KMS 后端，属安全项，不在本轮范围。
3. **交付物水印未嵌入**：`.csv` 不在 `TEXT_WATERMARK_SUFFIXES`（app 侧常量），所以成果包显示
   「未嵌入水印」。要按原型给 CSV 加水印需先定水印注入语义（文本注释行还是派生列），不自行发明。
4. **D-e2e-17 UI 新建的合约在本机打不开 Jupyter 开发环**：`ContractCreate.tsx` 的「允许沙箱级别」在 SS-13 后是
   `disabled` 的 Select、固定写 `l2c`；dev compose 却是 `CDS_DEV_SANDBOX_LEVEL: "L3"`（`docker-compose.yml:199`），
   `app/api/notebooks.py:246-256` 用它建 dev session，`app/api/sandbox_sessions.py:260-262` 按
   `contract.allowed_sandbox_levels` 把门 → 实测 400 `Sandbox level 'L3' not allowed by contract`。
   运行时这条拒绝**符合** SS-13「不得降级隔离」，不是可以靠放宽校验消掉的 bug；要么合约表单按部署能力矩阵给出
   本机真实可用档位（带披露），要么 dev compose 回到 `l2c` 并接 Cube 节点。属产品口径，未自行改。
   本轮 G2 取证改用 API 建的 `allowed_sandbox_levels=L3` 合约（`CDS-1792215562-B17439`），签署 / 发布 / 建任务 /
   开开发环仍全部在 Chrome 里真点。
5. **D-e2e-18 库里 `ready` 的资源对象可能已不在 MinIO**：早期 fixture `88057f46…`（零售订单 031858）注入时
   `S3Error … NoSuchKey`，票据如实记 `injected: []` + 逐资源错误、界面不假装注入成功（诚实降级按预期），
   但 `data_resources.status=ready` 与桶内对象存在性之间没有一致性巡检。

### 10.2 原型自相矛盾处（按图记录，不自行统一）

- `查询` 按钮在 2-1 是蓝实心、在 2-2 是描边。
- 3-3 监控 KPI 成功率 100% 与明细里存在「调用失败」并存。
- 2-2 表第 4 行产品名两遍读数冲突，标【不清晰】。
- 面包屑叫「新建API」、按钮叫「+ 新建接口」。

### 10.3 待你拍板（问题已用提问工具发出，未答前不动）

- ~~G1 / G2~~：**已拍板并落地**（2026-10-17「向导整页化: 改成整页」「数据集勾选: 做完整跨端子集」），
  实现与取证见 §9.2.1、数字见 §8.2，不再列为待决。
- 执行车道（本机无 CubeSandbox 节点）仍未答：D-e2e-17 就是它在合约面的复现。

### 10.4 拒绝按图造假的点（不是遗漏；已随能力补齐而失效的条目一并标注）

- ~~不做「今日操作日志」KPI~~：**已失效** —— §9.2.2 给 `GET /audit/records` 补了 `date_from/date_to`，
  KPI 与 4-1 「共 N 条记录」同一查询，不再有「用累计值冒充今日值」的问题。
- ~~表里不加「操作结果」列~~：**已失效** —— `audit_outcome()` 从动作名派生（标记集 `failed/denied/rejected/blocked`，
  用例机检每个标记都在 `app/` 真实动作词表里出现过），库里确实没有结果列这件事写在字段推导注释与用例里。
- ~~类型页签叫「生产运行申请」~~：**已失效** —— 页签改回图上的「进入生产环境」，值发 `event_type`，
  由 `AUDIT_EVENT_TYPES` 单源约束到代码真会写入的 action。
- 仍然拒绝：3-2 把 `白名单 / 限定查询` 画成可用并默认选中（后端无字段、网关不校验请求 Schema）；
  `结果集字段` 用图里的三行示例数据填充；2-3 的状态条 `CPU 23% / 内存 3.2/8 GB / 已自动保存 / UTF-8·LF·4 空格`
  一类无端点支撑的读数；2-3 文件树的行内勾选框（成果包按整个 `files/` 采集，无按勾选导出端点）。
  这些位置一律显示「未记录」或明写平台真实行为，而不是照抄图上的数字与选中态。

### 10.5 本机口径声明

本文件所有测试数字均为**本机口径**，未走远程生产车道：后端为生产同源 py3.12 镜像 + 本机
Docker（PG16/minio/jupyter）——`.venv-gate` 因 protobuf 冲突收集不起来；前端为本机 node（v22.5.1，需
`NODE_OPTIONS="--experimental-require-module"`，否则 jsdom 链上 `ERR_REQUIRE_ESM` 会让 28 个文件全部
起不了 worker，汇总行看起来像「28 errors」而不是测试失败）的 `npx tsc -b` 与
`node node_modules/vitest/vitest.mjs run`。退出码一律 `> log 2>&1; echo RC=$?` 取，**不经管道**。
截图证据在 `D:/workspace/.cds-e2e/shots/`（编号 80–157，其内 145–157 是容器面 :3000），G1/G2 子集取证全文在
`D:/workspace/.cds-e2e/evidence-g2-subset.md`，差距与缺陷台账在 `D:/workspace/.cds-e2e/gap-ledger.md`，
逐屏图片真值在 `D:/workspace/.cds-e2e/img-specs/`。截图本身未做人工目视复核（本轮模型无视觉能力），
判定一律来自 DOM 文本、接口响应与库里读数。
全程未 commit、未 push。

## 11. 容器面（:3000）逐屏复验与 OpenAPI 交付腿的真实闸口（2026-10-17 傍晚，D-e2e-21）

### 11.1 换面：不是 dev server，是容器

`docker compose build cds-frontend` **BUILD_RC=0**（`.cds-e2e/fe-build.log`）、
`docker compose up -d cds-frontend` **UP_RC=0**，`sandbox-cds-frontend-1` 占 :3000→80；解析态用
`docker compose -f docker-compose.yml config cds-frontend` 核过，不靠猜挂载。截图 145–157 全部在
这个面上取，逐屏 DOM 读数与结论同 `gap-ledger.md` 末节（引用不重述）。

已复验屏与关键读数：145 首页四 KPI（11 / 0 / 未记录 / 无权限 + 403 说明）；146 开发任务首页 `共 11`；
147 任务详情七卡同序 + 三动作 + 四统计卡 + 双内存；148 真 Jupyter iframe
（`/api/jupyter/login?ticket=…` → `cds_jup_ctx`，目录/计数/占用来自真 Contents API）；150–152 结果集
OpenAPI 列表与建单八字段；153/154 审计六列同序 + 五页签 + 中文分页（整串
「共 410 条记录 … 10 条/页 前往 页」，旧镜像是 `10 / page`，故可反证 :3000 跑的确实是重建后的包）
+ 720px 详情抽屉（`LOG-20261017-064442-5622`、九项字段、三处诚实降级）。

### 11.2 OpenAPI 交付腿的完整链条（从合约到发布闸，逐环 psql 可查）

| 环节 | 结果 | 取证 |
|------|------|------|
| 合约能勾 API 交付 | 可以 | `/contracts/create` 的 `交付方式（可多选）` 含「API 交付 发布为 /open/v1/{slug} 在线端点」 |
| 旧数据全库无 `open_api` 合约 | 确实为 0 | `select … where delivery_methods::text ilike '%open%'` → 0 rows（这是 3-2 关联任务先前为空的数据侧原因） |
| UI 造合约 | 成功 | `CDS-1792219543-654572`，`delivery_methods=["open_api","dataset"]`（截图 155） |
| 双方签署 | 成功 | 买方 SM2 签署 → `negotiating` 且 `buyer_signature` 非空；提供方再签 → `active` |
| UI 建负载 | 成功 | `/workloads`「新建负载」→ `SS19 交付闭环负载`（test/draft，入口指纹 `e91cb9651749…`） |
| 发起测试 | **诚实失败** | `status=test_failed`，抽屉原文：未启用 MicroVM 沙箱数据面（`CDS_CUBE_DATAPLANE_ENABLED=false`），需按 `deploy/cube-node/` 部署且节点有 `/dev/kvm`（截图 156/157） |
| 发布→接口可绑 | 过不去 | 3-2 关联任务按产品闸只列 `env=production, status=published`（`DeliveryCenter.tsx:923`） |

**口径更正**：本文件之前往 10.1/9.2.3 写的「Workload 数据缺失」是半句。真实链条是「合约勾 API 交付（UI 已可做）
→ 负载测试通过并发布（需真 MicroVM/Cube 数据面）」。界面侧到闸口为止全程走得通；过不去的是**执行车道**，
即 §10.3 仍未答的那条（l2c × L3）。按禁伪造铁律，不拿模拟路径冒充这一环跑通。

### 11.3 本轮新增的两个缺陷（未修，不影响已有用例）

1. **建负载静默失败**：来源 Notebook 推不出 entrypoint 时，后端 400 的中文提示几秒后自动消失、
   表单上无残留错误位，用户只看得到「点了没反应」。修法：失败时把错误留在 `Form.Item`
   的 explain 位且不自动收起。
2. **G11 仍无实机对比**：本机零端点，3-3 无真流水可看。列表页纯文字 vs 3-3 chip 的差异目前
   **只有用例 `DeliveryCenterOpenApi.test.tsx:156` 锁住**，不得声称截图证过。

取证陷阱（写进台账以免重踩）：antd Select 选项 >10 时走虚拟列表，DOM 只渲染 ~9 个节点，
遍历 DOM 找目标项会假判「选项不存在」（本次合约列表第 17 项因此第一次没选中）；
必须先往 search input 写关键词再点唯一命中项。

### 11.4 执行记录（本机口径）

| 任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项 |
|------|------|----------|----------------|----------|----------|
| D-e2e-21 容器面复验 | 完成 | 镜像重建 + 13 张容器面截图（145–157）+ OpenAPI 链条逐环取证 | 本轮无新增用例（纯取证轮）；全量 `npx tsc -b` 与 `npx vitest run` 见下条 | 无回归 | G11 实机对比需先有已发布负载（卡执行车道） |
| 最终门 | 已跑（五次） | `.cds-e2e/tsc-cfg.log` **TSC_RC=0**（0 条 `error TS`）；`vitest-cfg.log` **32 files / 308 passed，VITEST_RC=0，78.90s** | 本轮无新增用例（纯取证轮） | 无回归 | G11 实机对比需先有已发布负载（卡执行车道） |

### 11.5 全量 vitest 的 5s 上界会随机器饱和度随机红（必读，否则下轮会误当成回归）

同一代码树同一条命令 `npx vitest run` 四次四样：静默 **308/308 绿**（`vitest-final3.log`，14:36）；
Chrome 实机取证 + Docker Desktop + vite dev 同时跑 → **2 failed**（`vitest-final4.log`）；
再加一个并发 vitest → **4 failed**（`vitest-final5.log`）；机器静默重跑 → `vitest-final6.log`。

三条判据证明它是环境负载而不是代码：（1）红的全是 `Error: Test timed out in 5000ms`，
`AssertionError`/`Unable to find` **0 条**；（2）红的集合不固定、负载越重红越多；
（3）同一两条在重载下用 `--testTimeout=25000` 跑只需 **1641ms / 1820ms**
（`.cds-e2e/vitest-load25.log`）——有界真实工作，不是悬挂。本机 16 核 ⇒ 默认 16 worker，
叠 Chrome + Docker Desktop 就有人越 5s。**没改 `testTimeout`、没 skip、没删用例**；
**已改的唯一一处**：`cds-frontend/vite.config.ts` 的 `test.maxWorkers = 4`（vitest 4.1.8 里它是 `test`
顶层键，`pool` 默认 `forks`；CLI 用 `--max-workers`，`--poolOptions` 会 `CACError: Unknown option`）。
改后全量 **32 files / 308 passed，VITEST_RC=0，78.90s**（`vitest-cfg.log`，同时 `tsc-cfg.log`
**TSC_RC=0**），比 16 worker 的 80.63s 快；限并发前的饱和跑只能当负载样本。
`ci.yml:114` 前端 job 只跑 `npm run build`（vitest 不在 CI），所以它也不是 CI 回归。
逐条数据与复跑命令在 `.cds-e2e/gap-ledger.md` 「门数字波动」节。

> 门数字以 `.cds-e2e/tsc-cfg.log` / `.cds-e2e/vitest-cfg.log` 里**写进文件本体**的
> `TSC_RC=` / `VITEST_RC=` 行为准（退出码不再经管道取，这是本仓库反复踩过的坑）。

---

## 12. 交付腿在容器面真闭环 + 一处后端真修（2026-10-17 晚，D-e2e-22，本机口径）

### 12.1 链条（全程 :3000 容器面；api 容器 07:16:17Z 重启后）

| 步 | 面 | 实测读数 |
|----|----|----------|
| 建会话 | 开发环「启动测试沙箱」 | toast「测试沙箱已创建」→ `c62afad6-2918-4d20-95c1-06b43d8e63fc`，`running`，`sandbox_level=L3` / `sandbox_mode=product_dev` / `container_id=bwrap-…` |
| 传文件 | `POST /sandbox-sessions/{id}/files` | 200，`baseline.csv` 24 B，`encrypted=true`，`sha256=b7d0f709…`；宿主落 `<ws>/files/` 并写 `.files_index.json` |
| 沙箱内执行 | `POST …/exec` | `exit_code=0`（`ls -lR /workspace/files` 可见 `output/`）；审计 `sandbox.exec` 15:17:42 |
| 打包 | 成果交付 → 数据集打包（会话 c62afad6 + 合约 `CDS-1792207842-AC2336`） | toast「**打包完成：1 个文件，826 B**」；列表行 T5 `DLP 审查通过`、水印 `未嵌入水印` |

[Showing lines 1-538 of 1188 (50.0KB limit). Use offset=539 to continue.]

## 22. C-2「新建API」按图整页化与尾差收口（2026-10-17，D-e2e-45，开发道 :5199 口径）

真值：`基础版数据沙箱原型/3-2、我的结果集-OpenAPI导出-新建API.png`（判据 `.cds-e2e/img-specs/group-C.md`）。

| 判据 | PNG | 现物 | Δ |
|------|-----|------|---|
| 面包屑带（钻入后全屏） | y89–103 | y103（下缘） | ≤2 |
| 字段列簇 | 267/651/1035 | 267/651/1035 | 0 |
| 第一字段带 | y~150（星号簇） | 标签行 y137–159（星号居中≈148） | ≤2 |
| 新增接口钮 | 110×40 右缘 1395 | 110×40 右缘 1391 | 4 |
| 底部灰字+钮行 | y780 | y809 | 29（诚实披露文案多出的两行行高，铁律优先登记） |
| 上线开关 | 胶囊内左「上线」蓝 | 同 | 0 |

结构改动：①`.cds-delivery-fullbleed`——钻入新建/监控视图时隐藏工具带+页签条（CSS 层，不动组件树，防 remount 丢视图态）；②`.cds-row3` grid 360/360/1fr gap24（antd md8=368/392 会整列右漂 18px/列）；③新增接口钮 paddingInline 22（large 默认 15→w96）。

诚实降级登记（铁律优先于 1:1）：白名单/有效期/查询范围/结果集字段四处后端无字段，渲染为 disabled+灰字披露；「到期自动下线」等 PNG 文案不落地。

门：`tsc -b` RC=0、Flow vitest **23 files/276 RC=0**（未过滤原命令，本机 py3.12/node 口径）。截图 228（改前）/229（整页化后）。

## 23. C 组路由拆分与 C-1/C-3 几何收口（2026-10-17，D-e2e-46，开发道 :5199 口径）

真值：`3-1、我的结果集-OpenAPI导出.png` / `3-3、OpenAPI接口监控.png` 2x 放大直读复量（判据 `.cds-e2e/img-specs/group-C.md`）。

**路由拆分**：「我的结果集」在 PNG 是单行菜单，但 OpenAPI 导出/数据集打包/结果推送三子项各有整屏（3-1/3-4/3-5）——拆为 `/deliveries`、`/deliveries/dataset`、`/deliveries/push` 三条路由（同 RoleRoute+WorkspaceRoute 壳），`routeCrumb.ts` 加 dataset/push 段（find+startsWith 顺序，dataset/push 排在 /deliveries 前），侧栏两角色菜单改子菜单，`workspace.ts` DEVELOPER_MENU_KEYS +2；容器只卸载清栈，段名走注册表。**Δ 登记**：PNG 单行菜单→antd 子菜单（组件口径，不强改单行手风琴）。

**C-1 表体几何（本轮根因链，全部 WebView 实测）**：

| 判据 | PNG | 现物 | 定性 |
|------|-----|------|------|
| 列宽簇 | 170/190/160/100/~168/70/80/170 | 同（tableLayout fixed + scroll.x 1108） | 0 |
| 横向滚动 | 无 | sw=cw=1108 | 0（apiUrl 190→168 凑卡内宽） |
| 数据行高 | 52 | 52 | 0 |
| 表头高 | ~48 | 49 | ≤1 |
| 按钮 | ≈121×32 | 121×32（minWidth 121，去 size=large） | 0 |

行高收口的三层假因与真因（登记以免复发）：①误诊一：antd cssinjs 运行时注入压过静态 CSS——实为作用域 ConfigProvider `cellPaddingBlock` 对 middle 档无效（middle 吃 `cellPaddingBlockMD`，`table/style/index.js` default/MD 两档分家）；②真因 A（tbody）：全局 `:is(.cds-home,.cds-detail) .ant-table-wrapper .ant-table .ant-table-tbody > tr > td {padding:12px}`（index.css L612 族，/deliveries 在 detailScope 内命中），特异度 4类+元素；收口＝`.cds-openapi-table .ant-table .ant-table-tbody > tr > td.ant-table-cell {padding:15px 16px}` 垫到同特异度、文件后插稳定胜出，不用 !important；③真因 B（thead 70）：`是否在线` 列 80px 容不下 13px×4 字+32px 内边距 → 换行成两行行盒，表格行等高把**全部**表头撑到 70（th::before 细线为次要因素）；收口＝thead th `line-height:20px + white-space:nowrap + height:48px`，并删 `th::before`（antd 表头竖分隔细线，PNG 表头本就无分隔线，一举两得）。

**C-3 监控（2x 直读推翻旧小图记录，旧记录更正）**：①「已上线」chip **带蓝色 link 图标**（旧记录与旧测试钉「无图标」=误读，改 LinkOutlined 并改写 `OpenApiMonitor.test` 钉点）；②监控头部是白卡 → 包 `Card size="small"`；③KPI 带浅蓝渐变 `.cds-kpi-band`；④明细分页 `.cds-openapi-pag`（flex，total `margin-inline-end:auto` 左置，totalX=264=卡左缘实测）。

**Δ 登记（组件口径，不强改）**：①rc-pagination `shouldDisplayQuickJumper = total > pageSize`——单页无「前往__页」（多页有且被监控测试钉住）；②分页 DOM 顺序（sizeChanger 在页码后）vs PNG 拆分式——antd 结构不可拆；③侧栏子菜单（见上）。

**门**：`tsc -b` RC=0；定向 vitest（DeliveryCenterOpenApi + OpenApiMonitor + Layout 全目录）**9 files / 94 tests RC=0**（本机 node 口径，命令带 `NODE_OPTIONS=--experimental-require-module`、RC 不经管道）。WebView 截图序列 238–257（定稿 255/256/257）。

## 24. 十屏对标复核扫收口（2026-10-18，D-e2e-47，开发道 :5199 + 镜像 :3000 双口径）

十屏全部按 PNG 1:1 裁读复核一轮（判据法与踩坑见记忆卡 png-truth-crop-calibration：read 缩略坐标陷阱 / WebView scale 复合公式）；三条旧「1:1 记录」被 2x/4x 重裁推翻并登记：2-2 步进器钮序（真值 [值][−][+][G块]）、2-2 关联数据「查询」钮（ghost 非实心）、2-4 我的合约（三枚灰底只读盒非通栏表）。

**2-2 新增开发任务**：红星真因＝antd 竖排 `label::after content ':'` 特异度压过自定星号（computed 曾为 `":"`），提特异度至 (0,3,1) 重写后 content `'*'` 色 #f54646 ✓；另收步进器钮序、配额浅蓝 alert 卡、块题 ⓘ、页脚 ⓘ、「查询」改 ghost、删字段级问号。旧钉点改写：devTaskExecution 卡片三钮 `['调 试','执 行','详 情']`→`['调试','执行','详情']`（带图标后 antd 不再拆字）。截图 272/273 零残差。

**2-4 任务详情**：我的合约三盒（合约名称 / 模型调试方法 CodeOutlined / 结果导出方式 RiseOutlined，tooltip 口径留 label）；资源配额＝灰底只读盒 h42+右端白底 G 片（quotaParts 拆 ' G' 尾缀，非大号数字）；页头钮＝返回列表白底描边 / 进入调试蓝 ghost+ImportOutlined / 执行任务实心；块题小图标补齐（ⓘ / LockOutlined+DatabaseOutlined extra / ClockCircleOutlined / ExportOutlined / 资源面板蓝字题+CheckCircleFilled）。TaskDetail.test 7 处契约改写（含「① 」前缀钉点改文本+`.anticon-info-circle` 双断言）。截图 274（改前）/275（卡区）/279（运行记录+结果集下半屏）。

**首页四差距**：KpiCard 图标方块左置（40 圆角 10，文右）、内存大数字改品牌蓝 24px、开发任务总览题后 ⓘ、结果集题后 ⇗、欢迎钮补 ControlOutlined。截图 260（改前）→277（改后）。

**4-2 操作日志**：详情弹窗 Descriptions `colon={false}`（PNG 标签无冒号），截图 278 ✓；存证块（验真/锚定）＝功能增强保留。**Δ 登记**：弹窗底部钮区右排（PNG 仅居中「关闭」，增强态保留 antd 惯例）、弹窗 body 定高滚动（因增强块超屏）、4-1 角色菜单差异（SS-18 口径，§19）。

**2-3 在线开发调试定性**：现物＝诚实降级态（黄条披露无可用 MicroVM/L2′、`CDS_CUBE_DATAPLANE_ENABLED=false`＋空态引导）；PNG 的「沙箱已连接」IDE 态需 L2′ 数据面，本部署不可达，**不在本机伪造硬件**（铁律），IDE 结构在 NotebookWorkbench 可达路径保留。

**门**：`tsc -b` RC=0；全量未过滤 `NODE_OPTIONS=--experimental-require-module npx vitest run` **44 files / 480 tests RC=0**（本机 node 口径，110.4s）；`docker compose build cds-frontend` RC=0，`up -d` 后 :3000 登录复量（截图 280）与开发道 277 一致（图标左置/蓝字/ⓘ/⇗/logo 均在镜像内）。截图序列 272–280。

## 25. 在线调试环四修（2026-10-18，D-e2e-48，本机开发道口径）

用户问「在线 jupyter 调试是否完整」后的调查→修复轮。调查结论：API 腿早已全绿（`/cells/execute` L3 真执行、白名单真拦），真断点在 **iframe 被 XFO 拦死**（UI 从未目检过那个 iframe）。

**① X-Frame-Options 前缀豁免**（`app/core/security.py`）：`SecurityHeadersMiddleware` 对所有响应发 `DENY` → 开发环 iframe 灰屏（截图 285 实锤）。修：`/api/jupyter/` 前缀改发 `SAMEORIGIN` + CSP 追加 `frame-ancestors 'self'`，其余维持 DENY（安全边界不变：一次性票据＋会话目录前缀＋终端镜像内关闭；安全边界写在 docstring）。验证：`/api/jupyter/login` → SAMEORIGIN+fa，`/health` 与 `/api/v1/*` → DENY；钉点 `tests/test_security.py::test_security_headers_jupyter_embed_exempt`（双向：豁免到位 + 不泄漏到相邻前缀）。

**② ws 升级透传**（`cds-frontend/vite.config.ts`）：`/api` 代理条目加 `ws: true`（kernel channels 走 WebSocket；nginx 容器道本就有 `Upgrade`/`Connection "upgrade"` 头，无需改）。端到端实证：持本会话 cookie 经代理 `POST /api/jupyter/api/kernels`（body 带 `path=本会话前缀/…`）→ 201 → `ws …/channels` 握手 open → 发 `execute_request "21*2"` → 收回 **execute_result 42**。守卫同时被验到在工作：无 cookie/越界目录 → 403「只能在开发任务自己的目录里开会话」；拿别人的 kernel id → close 4403「该 kernel 不属于本会话」（`claim_kernel` 只在代理拦截 POST 时登记归属）。

**③ 自动保存**（`NotebookWorkbench.tsx`）：原型卡头「已自动保存 HH:MM:SS」由文案假位变成真行为——前端 `AUTO_SAVE_DEBOUNCE_MS=2000` 防抖 PUT（导出常量给测试；保存静默不弹 toast；手动保存重置 auto 态）。诚实口径写进 tooltip 与文件头：**没有服务端自动保存任务**，离开本页即不再触发，每次自动保存同样 +1 版本。浏览器实证：新增单元格后卡头变「已自动保存 01:38:04」（截图 288）；契约：未编辑时不得预先出现该文案（旧钉点改写为「只有防抖 PUT 真发生后才允许」）。

**④ 状态条计量**（`GET /sandbox-sessions/{id}/metering` + 状态条接线）：后端只读 cgroup v2 真计数（`memory.current`/`memory.max`/`cpu.stat` 250ms 双采样/`pids.current`），读不到（l2c 在 VM 内／会话非运行／容器无权限）就回 `available:false + reason`，前端继续「未计量」——绝不编百分比（禁伪造）。本机开发容器实测 `available:false`（截图 289 状态条形态诚实）；cgroup 可读写入方出真读数（前端契约测试用 mock 读数钉 `CPU 12.3%` / `内存 3.8 G / 8 G 配额` 两态）。Python 版本仍无下发接口 → 保留未计量。

**副产品**：光标行列经真点击复验为已接线且活（Monaco `onDidChangeCursorPosition` → 状态条「行 1, 列 7」，截图 289；未点击时「行 —, 列 —」是诚实态不是缺口）。

**门（均本机口径：node 全量 + `.venv-gate` py3.12；未走远程 py3.12 生产镜像车道）**：`tsc -b` RC=0；全量未过滤 vitest **44 files / 482 tests RC=0**（较 §24 基线 +2：计量读数态 + 自动保存态）；`pytest tests/test_security.py` 9/9；`tests/test_jupyter_gateway_isolation.py` 24/24。证据截图：285（修复前灰屏）→ 286（JupyterLab 真渲染，`contentDocument.title=JupyterLab`）→ 288（自动保存卡头）→ 289（光标活 + 诚实状态条）。

**踩坑登记**：uvicorn `--reload` 改后端后会卡在 `Waiting for background tasks`（:8001 挂起），处置 = `docker restart cds-api-dev`；cookie 值是签名包装（`\054` 转义 + 尾随签名），脚本侧解析要截 `{…}` 段再反转义。


