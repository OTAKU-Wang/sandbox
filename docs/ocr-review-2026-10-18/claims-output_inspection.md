# Claims for app/services/output_inspection.py (17)

## C1 [high/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# Stage 2: DLP Scan
        if InspectionStage.DLP.value not in active_stages:
            stage_results[InspectionStage.DLP.value] = True
        dlp_passed = True
```
CLAIM:
DLP 阶段完全绕过了场景路由：这里虽然写了「不在 active_stages 就置 True」，但循环紧接着无条件执行，并在最后用 `stage_results[...DLP...] = dlp_passed` 覆盖了刚写入的 True。结果是即使场景（如 develop/application 之外的自定义场景）不启用 DLP，输出仍会被扫描、`redacted_output` 仍被改写、findings 仍被追加，且 `passed` 仍可能被 DLP 命中拖成 False。这使得检查策略越界、阶段语义与 SCENE_RULES 不一致，也让上面那行赋值成为死代码。建议把整个扫描块包进 `if InspectionStage.DLP.value in active_stages:`，否则不做扫描也不改写 redacted。
SUGGESTED FIX (tool): # Stage 2: DLP Scan (仅在该场景启用时执行)
        dlp_passed = True
        if InspectionStage.DLP.value in active_stages:
            pass  # TODO: 将下方 for 循环整体置于本 if 内

## C2 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
f"Found {len(matches)} {name} instance(s)",
                    samples=matches[:3],
```
CLAIM:
DLP 命中样本原样写入 findings，会造成二次泄露：`redacted_output` 已脱敏，但 `InspectionResult.findings[].samples` 里仍携带完整身份证号、手机号、银行卡号、邮箱等原文。该结构通常会被序列化进 API 响应、审计日志或落库，等于把最需要保护的数据以另一种路径导出，与 DLP 阶段的目的直接冲突。建议只保留命中数量与位置，或对样本做掩码（如仅保留前 3 后 2、或存哈希指纹用于复核）。
SUGGESTED FIX (tool): f"Found {len(matches)} {name} instance(s)",
                    samples=[_mask(v) for v in matches[:3]],

## C3 [high/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# else: epsilon is None or <= 0 -> DP not applied (honest no-op)
```
CLAIM:
违反本文件自述的 §8「真空披露」口径：DP 阶段被场景启用但 epsilon 为 None/<=0 时，`stage_results[DP]` 保持 True 且没有进入 `not_evaluated`，判定方会把「无从检查」读成「DP 检查通过」。同文件中 DATA_RECONSTRUCTION / K_ANONYMITY / MIA 都做了真空登记，唯独 DP 没有。建议在该 else 分支追加 `not_evaluated.append(InspectionStage.DP.value)`，或在场景强制要求 DP 时直接判阶段失败。
SUGGESTED FIX (tool): else:
                # epsilon is None or <= 0 -> DP not applied；场景启用了该阶段却无参数可查，必须登记真空
                not_evaluated.append(InspectionStage.DP.value)

## C4 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
dp_applied = True
                    dp_budget_remaining = None
```
CLAIM:
预算管控存在可绕过的路径：只有当 `has_budget_sync(session_id)` 为真时才走 fail-closed 分支；否则照样加噪并置 `dp_applied=True`、`stage_results[DP]=True`，且完全不记账。调用方只要使用一个未分配预算的 session_id（进程重启后 ledger 缓存也会清空，`has_budget_sync` 随之为 False），即可无限次获得「已应用 DP」的结论而不消耗任何 epsilon，使双预算统一（M-03）与 fail-closed 目标失效。建议对启用 DP 的场景要求必须存在预算，否则登记 not_evaluated/直接判失败。
SUGGESTED FIX (tool): dp_applied = True
                    dp_budget_remaining = None
                    # TODO: 场景启用 DP 却无预算跟踪时应 fail-closed 或至少登记 not_evaluated

## C5 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
if OutputInspector._signing_keypair is None:
                OutputInspector._signing_keypair = crypto_service.generate_keypair()
```
CLAIM:
签名密钥的惰性初始化存在两个问题：1) `_signing_keypair` 是类级共享可变状态，无锁的 check-then-set 在多线程/多协程首次并发进入时会各自生成不同密钥对（TOCTOU），导致同一服务实例产出不可互相验证的签名；2) 密钥是运行时随机生成、不落盘也不分发公钥，进程重启即更换，所谓「不可抵赖（GM/T 0009）」实际上不成立——任何人都能生成同样结构的签名，事后无法追溯到确定的签名主体。建议启动时从受保护配置/KMS 加载固定签名密钥，并用锁或幂等初始化保护。
SUGGESTED FIX (tool): with _SIGNING_KEY_LOCK:
                if OutputInspector._signing_keypair is None:
                    OutputInspector._signing_keypair = crypto_service.load_or_generate_signing_keypair()

## C6 [medium/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
f"{len(violations)} group(s) below k={self.K_ANONYMITY_MIN}",
                    samples=violations[:3],
```
CLAIM:
k-匿名违规描述同样把准标识符原值（含 message 里的 `Group {qi_values}` 与这里的 samples）带出到 findings/日志，等同于以小批量方式回显个体级数据。建议对 qi_values 做掩码或只报告列名与组大小。
SUGGESTED FIX (tool): f"{len(violations)} group(s) below k={self.K_ANONYMITY_MIN}",
                    samples=[_mask_violation(v) for v in violations[:3]],

## C7 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
matches = pattern.findall(output)
            if matches:
                dlp_passed = False
```
CLAIM:
DLP 的门禁完全忽略 severity：任何一条命中（包括被标为 "low" 的 ip_address、"medium" 的 email）都会把 `dlp_passed` 置 False，进而 `passed = all(stage_results.values())` 直接判定整体不通过。`ip_address` 正则没有校验八位组范围（`999.999.999.999`、版本号 `1.2.3.4`、时间戳都会命中），因此一个无害的版本号字符串即可阻断整个输出发布，而 findings 里却写着 low —— 严重级别形同装饰，且会产生大量误拦。建议显式定义放行策略（例如仅 critical/high 使阶段失败，其余仅记录），并收紧 IP 正则。
SUGGESTED FIX (tool): matches = pattern.findall(output)
            if matches:
                if severity in ("critical", "high"):
                    dlp_passed = False

## C8 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
redacted = output
```
CLAIM:
`redacted = output` 之后的 DLP 扫描假定 output 一定是字符串，但 Stage 1 明确用 `if not output or len(output.strip()) == 0` 允许 output 为 None/空。一旦调用方传入 None（detail 缺字段、上游返回空），`pattern.findall(None)` 会抛 TypeError 并让整个请求 500，而不是按「空输出」给出 FORMAT 不通过的结论。建议在进入 DLP 前做 `if not isinstance(output, str): output = ""`（或提前返回结构化失败）。

## C9 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
noisy_rows, _noised = self._apply_dp_noise(output_rows, dp_epsilon)
                        if output_rows is not None:
                            output_rows = noisy_rows
```
CLAIM:
两处问题：1) `output_rows = noisy_rows` 是死赋值——DP 之后再无任何代码读取 `output_rows`，加噪结果只通过 `noisy_output_rows` 字段外泄，实际放行的 `redacted_output` 文本始终是未加噪的原值，若调用方按 `redacted_output` 返回即等于对外宣称已加噪而实际未加。2) 当 `output_rows` 为 None（自由文本输出）时 `_apply_dp_noise` 返回 `(None, 0)`，一个单元都没有扰动，却仍然 `dp_applied=True` 并消费预算，与注释「dp_applied must reflect what actually happened」矛盾。建议按 `num_noised > 0` 决定 dp_applied，并明确把加噪结果回填到实际输出或文档化其边界。
SUGGESTED FIX (tool): noisy_rows, num_noised = self._apply_dp_noise(output_rows, dp_epsilon)
                        # 只有真正加了噪才算 DP applied
                        dp_applied = num_noised > 0

## C10 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
if dp_budget_ledger.has_budget_sync(session_id):
                    if not dp_engine.consume(session_id, dp_epsilon):
```
CLAIM:
把 `consume_sync` 返回 False 一律解读为「预算耗尽」不准确：`dp_budget_ledger.consume_sync` 在 epsilon ≤ DP_MIN_EPSILON_CONSUMPTION 或 epsilon > DP_MAX_EPSILON_PER_CONSUMPTION 时同样返回 False，此时会误报 dp_budget_exhausted（且 remaining 可能很充裕），更严重的是这条路径跳过了加噪，却对外给出「预算耗尽」的结论，掩盖了参数非法的真实原因。另外 `has_budget_sync` 与 `consume_sync` 之间是对共享 `_cache` 的 check-then-act，多线程并发下存在同一预算被超额消费的竞态。建议区分拒绝原因（让 ledger 返回状态/抛特定异常）并对预算读改写加锁或改为原子操作。

## C11 [medium/error handling]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
except Exception:
            # Fallback to SM3 hash if SM2 signing fails (dev mode)
            sig = crypto_service.sm3_hash(redacted.encode())
            stage_results[InspectionStage.SIGNATURE.value] = True
```
CLAIM:
签名阶段 fail-open 且异常被静默吞掉：SM2 签名失败后阶段结果仍被置 True（仅记入 degraded），既不记录原始异常（无 `logger.exception`，生产上完全无法定位密钥/HSM 故障），又让一个仅具摘要功能的 SM3 值占据 `signature` 字段，下游极易把它当作可验签的 SM2 签名（hex r||s）处理。此外回退语句写在 except 内部，若 `sm3_hash` 自身抛错将直接向上冒泡成 500。建议：记录异常、把 signature 置为 None 或加显式 `signature_kind` 标记，并按策略决定是否放行。

## C12 [medium/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
confidence = model_output.get("confidence", 0.0)
        loss = model_output.get("loss", float("inf"))
```
CLAIM:
MIA 检测的默认值是 fail-open：`model_output` 缺少 `confidence` 时取 0.0，缺少 `loss` 时取 `inf`，于是 risk_score 塌陷为 0，任何缺失字段的模型输出都会被判「安全」并通过该阶段；这与「训练模式必须检查记忆化风险」的目的相反。同时若 confidence/loss 为字符串（JSON 透传常见），`confidence * (1.0/(1.0+loss))` 会抛 TypeError。建议：字段缺失时判为不可评估（登记 not_evaluated）或按保守值处理，并做数值类型校验。

## C13 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
for row in source_rows:
            source_set.add(tuple(str(v) for v in row.values()))
```
CLAIM:
重构检测按 `row.values()` 的位置顺序 + `str(v)` 文本表示做比较，存在系统性漏检：输出与源数据的列顺序不同、做了投影（少几列）、或数值表示不一致（`1` vs `1.0`、`"007"` vs `7`、日期格式化差异）时，逐 tuple 比较必然不相等，match_rate 直接归零，攻击者只需调整列序即可绕过该关键控制。另外 docstring/类注释称「field-level exact match rate」，实现却是整行匹配，语义也不一致。建议按列名对齐后逐字段比较，或用 `frozenset((k, canon(v)) for k, v in row.items())` 做顺序无关比较。
SUGGESTED FIX (tool): for row in source_rows:
            source_set.add(frozenset((k, str(v)) for k, v in row.items()))

## C14 [low/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
import random
        scale = sensitivity / epsilon
        # Laplace distribution sampling
        u = random.random() - 0.5
```
CLAIM:
噪声函数缺少参数与边界防护：1) `scale = sensitivity / epsilon` 在 epsilon=0 时抛 ZeroDivisionError（公开方法，调用方可传任意 epsilon，应显式 `if epsilon <= 0: raise ValueError`）；2) `random.random()` 可返回 0.0，此时 `u=-0.5`、`math.log(1-2*abs(u))` 变成 `log(0)` 抛 ValueError；3) 作为隐私机制使用 Mersenne Twister 标准库 PRNG，噪声序列在攻击者可观测多次输出的情况下是可预测的，隐私场景应使用 `secrets`/CSPRNG；4) `import random`、`import math`（后者已在模块级导入）放在函数体内，而本函数按单元格逐个调用，属热路径上的无谓开销。
SUGGESTED FIX (tool): if epsilon <= 0:
            raise ValueError("epsilon must be > 0 for Laplace mechanism")
        scale = sensitivity / epsilon
        # Laplace distribution sampling（0.5 兜底避免 log(0)）
        u = secrets.randbelow(...) ...  # 使用 CSPRNG，并将 |u| 夹在 [0, 0.5-eps]

## C15 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
if quasi_identifiers is None:
            quasi_identifiers = list(rows[0].keys())
```
CLAIM:
`check_k_anonymity` 用 `rows[0].keys()` 推断准标识符集合，隐含「所有行同构」的假设：行列式不一致时，后续行缺失的列经 `row.get(col, "")` 变成空串并被并入同一组，虚增组大小从而漏报违规；同时 `str(...)` 把 `None`、空串、`0`/`"0"` 混为一谈。建议取列名并集，区分缺失值（例如单独以 MISSING 哨兵参与分组并在违规描述中标注）。

## C16 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
import hashlib
import json
```
CLAIM:
`hashlib` 与 `json` 在本文件中从未被引用（水印/哈希逻辑都在 `app.utils.watermark` 与 `crypto_service` 中），属残留导入，建议删除以免误导后续维护者以为此处直接做了哈希处理。

## C17 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
from app.services.mia_probe import mia_probe, MIARiskLevel
```
CLAIM:
`MIARiskLevel` 被导入但从未使用（本文件只用了 `self.check_mia_risk` 的返回值），且该 import 位于每次 inspect 调用都会执行的方法体内。建议删除未使用符号，并把确需延迟导入的 `mia_probe` 保留在最小必要的调用处。


## Known importers (grep of whole repo for "output_inspection" in *.py)
- app/api/audit.py
- app/api/connectors.py
- app/api/deliveries.py
- app/api/monitoring.py
- app/api/output_control.py
- app/api/sandbox_sessions.py
- app/api/sandbox_tasks.py
- app/api/session_stream.py
- app/core/metrics.py
- app/services/notebook_service.py
- app/services/output_security.py
- app/services/task_pipeline.py
- app/services/task_worker.py
- app/services/training_pipeline.py
- tests/test_connectors.py
- tests/test_e2e_flows.py
- tests/test_output_control_api.py
- tests/test_output_gateway_enforcement.py
- tests/test_output_inspection.py
- tests/test_output_inspection_extended.py
- tests/test_p05_code_scanning.py
- tests/test_pz02_dp.py
- tests/test_rag_task.py
- tests/test_reconstruction.py
- tests/test_session_exec_logs_usage.py
- tests/test_ss18_vacuum_semantics.py
