# 修复作业规范（CDS 仓库 · 全量修复轮）

仓库 `D:/workspace/sandbox`，分支 `feat/ss-11-productization`。**只改派给你的文件**（外加你新增/扩展的测试文件）。禁止 git 写操作。

## 每条发现的处置流程（严格按序）
1. 读台账原文：`C:/Users/清彧/AppData/Local/Temp/ocr-cds/ocr-verified-findings.md` 里 `### A<编号>` 那一节（`grep -n '^### A91 ' 文件`）。台账是 ocr 断言 + 前一轮复核，**不是事实**：先回读代码确认缺陷现在仍在（可能已被上一轮修掉，或行号漂移）。
2. 确认仍在 → 最小修复；不要顺手重构、不要改公共签名（除非缺陷本身就是签名）。
3. 写/扩展用例把它钉住：**必须先看到红**。做法：先只写测试、跑一次记下失败（或逐 hunk 回退改动再跑），再应用修复跑绿。
4. 后端跑法（本机 3.12 门）：
   `cd /d/workspace/sandbox && .venv-gate/Scripts/python.exe -m pytest tests/<文件> -q > /d/workspace/sandbox/.gate-<名>-red.log 2>&1; echo RC=$?`
   门命令**不许接管道**（管道会偷走退出码）。
5. 汇报必须含：每条 `已修 / 已被修过 / 判为不成立 / 本机证不了`、红与绿的 RC 与 `N passed` 原文、改动的 file:line。

## 本仓硬规则（违反即返工）
- 错误响应走 W2 契约 `{code,message,detail,request_id}`；错误码必须用 `docs/error-codes.md` 里已有的，缺码就**在汇报里提出**，别自造新码。`SESSION_TERMINATED`=410 是终止语义，SDK 不重试。
- 权限：路由用 `get_current_user` / `require_roles(...)`；越权修复要**收紧而不是放宽**，并在测试里断言被拒角色真被拒（403 的 code 要钉住），同时断言合法角色仍可用（防过度修正）。
- 审计：涉密读取/密钥操作/导出/锚定要 `audit_service.log(db, action=..., resource_type=..., ...)`，action 命名对齐同目录既有 `xxx.yyy.zzz`；成功路径走请求事务。
- 诚实降级铁律（禁伪造）：TEE/GPU/HSM/链/硬件不可用时必须**显式披露**（`is_simulation` / `available:false` / 明确的 `RUNTIME_UNAVAILABLE`），**绝不允许**把模拟当真实、把 no-op 报成 success、把空值报成通过。宁可拒绝，不可假装。
- 隔离等级不可降级代替拒绝：L2' 无 MicroVM ⇒ 400 `RUNTIME_UNAVAILABLE`，不得退到进程/bwrap。
- alembic 链线性只增不改历史；新列走新修订；布尔用 `text("false")` 不用原生 bool 默认。
- 不要动 `app/_stubs/cube/`、`Tongsuo/`、`cds-volume-plugin/tongsuo-build/`、`cds-frontend/node_modules/`。
- 模拟路径（`*_simulator`、sim 模式、`test_data_generator`）是**有意特性**，不是缺陷，别删。
- KMS 线格式是跨语言契约：`app/schemas/kms.py` 与 `cds-volume-plugin/kms.go` 必须同步动（有测试钉字段名）。
- 测试环境：`tests/conftest.py` 在 import app 之前钉了 12 个环境变量，别在测试里提前 import app；`get_settings()` 是 lru_cached，prod 形态的用例用 monkeypatch 替换缓存对象 + `delenv` TESTING/PYTEST_CURRENT_TEST。
- 本机是 py3.12 口径，权威数字在远端 py3.12 + PG16；你给的数字要写明「本机 3.12 口径」。

## 已知本机环境坑（别踩）
- 任何 `Path(...).read_text()` 不带 `encoding="utf-8"` 在本机 cp936 下会 `UnicodeDecodeError` —— 读源码文本的测试要显式编码。
- 前端测试必须 `NODE_OPTIONS=--experimental-require-module`（本机 node 22.5.1 + jsdom29）。
- 需要 docker / k8s / 真实链 / 真实 TEE 的验收本机做不了：**照修**（改动本身要正确且静态可推），但在汇报里单列「仅部署可验」，不要谎称已验。

## 运维/部署面专用（w5 队列）
- 待判清单：`C:/Users/清彧/AppData/Local/Temp/ocr-cds/claims-ops.md`（w5：417 条 / 55 文件；critical 23 / high 123 / medium 199 / low 70）。同样**每条先回读代码判真伪**，ocr 的 code_search 在本机是坏的。
- 本机可用校验器（已实测）：`bash -n <script>`（语法门，`RC=0`）、`kubectl`（存在；无集群 ⇒ 只能 `--dry-run=client` 级别）、`helm` **不存在**、`yq` **不存在**、`.venv-gate/Scripts/python.exe` 有 `pyyaml`。
- 因此运维面的「改前红/改后绿」这样出：
  - shell：写一个 `tests/test_<域>_scripts.py`，用 `subprocess` 跑 `bash -n`、或用正则/解析断言脚本里的不变量（fail-fast 列表包含某变量、`set -euo pipefail` 在位、卷挂载数、`${VAR:?}` 而非 `${VAR:-默认弱口令}`）；先跑红再改脚本跑绿。`deploy/cube-node/` 与 `deploy/e2e-remote/` 的代码**只有 tests/ 这一道自动门**，所以新增测试文件是正解。
  - yaml/k8s/helm：同一形状——用 pyyaml 解析后断言（replicas、有无 volume/`emptyDir`、`resources.limits`、`imagePullPolicy`、无明文默认口令），别声称 `helm template` 验过（本机没这个二进制）。
  - SQL（`scripts/clickhouse-init.sql`）：可用文本/正则断言列数与 TTL；**不得**声称连过 ClickHouse。
- 明文口令类（如 `${VAR:-CdsSecureP@ss2026}`）改成 `${VAR:?...}` 强制注入是**正确方向**，但必须同时确认没有 CI/脚本依赖旧兜底值（grep 该变量名，列出依赖点）。
- 诚实性：修完只说「本机静态门绿 + 语法门绿」，真实集群/真实节点验收仍是待办。
