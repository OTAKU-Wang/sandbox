# Claims for app/services/remote_attestation.py (16)

## C1 [critical/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
_LOCAL_ATTESTATION_ROOT = b"cds-local-attestation-root-v1"
```
CLAIM:
本地信任根 `_LOCAL_ATTESTATION_ROOT` 以明文常量硬编码在源码中,而 `_sign_quote_payload`/`_signature_valid` 用它同时做“签名”和“验签”。这意味着任何拿到源码(或读到该常量)的人都能离线构造出通过 `verify_quote` 的伪造 quote,被标记为 `verified` 并触发 KMS DEK 下发,远程证明的安全价值降为零。建议:(1) 该模拟根仅允许在显式的 test/dev 开关下启用,生产环境启动即拒绝(未配置真实 DCAP/VCEK 验证就 fail-closed);(2) 若必须保留软件签名,应从 KMS/环境变量读取每实例密钥,且密钥不落源码。同时 `AttestationResult.trust_root` 目前默认 `"external"`,调用方极易把 `local-simulated` 当成真实硬件证明使用,建议对 `local-simulated` 增加强制的下游拦截。

## C2 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
def _signature_valid(payload: dict[str, Any]) -> bool:
    signature = payload.get("signature")
    return isinstance(signature, str) and signature == _sign_quote_payload(payload)
```
CLAIM:
验签用 `==` 比较两个十六进制字符串,不是恒定时间比较,存在时序侧信道;更关键的是 `_sign_quote_payload` 只是 `hash(静态根 + payload)`(Merkle–Damgård 结构的 SM3),并非真正的 MAC/签名,不具备不可伪造性。建议改用 HMAC-SM3(或 HMAC-SHA256)并通过 `hmac.compare_digest` 比较。

## C3 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# Policy check: quote freshness
        if self._policy.require_fresh_quote:
            age = (datetime.now(timezone.utc) - quote.timestamp).total_seconds()
```
CLAIM:
新鲜度检查完全依赖调用方构造 `TEEQuote` 时填入的 `timestamp` 字段:该字段既不在 `raw_bytes` 中,也不在签名覆盖范围内,因此攻击者可以拿一个很早生成的合法 quote,配上“当前时间”的 `timestamp` 直接绕过 `max_quote_age_seconds`(重放攻击)。同时 `report_data`(应作为会话/challenge nonce 绑定)在三个 provider 的验签逻辑中都从未与本次会话期望值比对,无法证明该 quote 是为当前会话签发的。建议:把 `timestamp` 与 `report_data` 纳入签名 payload 并在 verify 中校验 nonce 与时间戳,拒绝 `age < 0`(未来时间戳,允许时钟偏移需设上界)。此外若上游传入 naive datetime,`datetime.now(timezone.utc) - quote.timestamp` 会抛未捕获的 `TypeError`,建议显式校验 tz-aware。

## C4 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
except Exception as e:
                logger.warning("Could not load ATTESTATION_ALLOWED_MEASUREMENTS from settings: %s", e)
            policy.allowed_measurements = set(measurements)
```
CLAIM:
读取 `ATTESTATION_ALLOWED_MEASUREMENTS` 失败时仅告警并继续,而空白名单的语义是“允许所有 measurement”,构成典型 fail-open:配置缺失/损坏/配置中心抖动时会静默放宽最强的证明约束。建议这里 fail-closed(直接抛配置错误或置一个 `_policy_load_failed` 标记使 `verify_quote` 全部返回 `POLICY_VIOLATION`),并把 `except Exception` 收窄为实际的配置异常类型。

## C5 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
parsed = json.loads(quote.raw_bytes)
            if parsed.get("type") != "sgx_ecdsa":
```
CLAIM:
`except (json.JSONDecodeError, UnicodeDecodeError)` 未覆盖 `raw_bytes` 解析出非 dict 的情况:若外部提交的 quote 为 JSON 数组/标量/`null`,`parsed.get(...)` 会抛出未捕获的 `AttributeError`/`TypeError`,直接以 500 冒泡(该路径也不会写入 `_verification_log`,审计缺失)。建议在解析后显式校验 `isinstance(parsed, dict)` 并返回 `QUOTE_MALFORMED`。
SUGGESTED FIX (tool): parsed = json.loads(quote.raw_bytes)
            if not isinstance(parsed, dict):
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.QUOTE_MALFORMED,
                    tee_type=TEEType.SGX,
                    measurement=quote.measurement,
                    error_message="Quote payload is not a JSON object",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if parsed.get("type") != "sgx_ecdsa":

## C6 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
parsed = json.loads(quote.raw_bytes)
            if parsed.get("type") != "sev_snp":
```
CLAIM:
同上:非 dict 的 JSON quote 会让 `parsed.get` 抛出未捕获的 `AttributeError`/`TypeError`。建议与 SGX 一致,解析后先做 `isinstance(parsed, dict)` 校验并映射为 `QUOTE_MALFORMED`。

## C7 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
parsed = json.loads(quote.raw_bytes)
            expected_components = "kernel:v5.15-hardened|rootfs:gvisor-ext4|config:isolated"
            if parsed.get("components") != expected_components:
```
CLAIM:
同样的非 dict JSON 崩溃风险(只捕获了 `JSONDecodeError`/`UnicodeDecodeError`)。另外本 provider 与 SGX/SEV 不同,既没有 `quote.tee_type != TEEType.FIRECRACKER` 的类型守卫,也没有校验 `parsed.get("type") == "software_hash"`,一旦被直接调用或复用其他类型的 raw_bytes 仍可能通过校验。建议补齐 isinstance 校验与类型/字段守卫。

## C8 [medium/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
components = f"kernel:v5.15-hardened|rootfs:gvisor-ext4|config:isolated"
        from app.utils.crypto import sm3_hash
        measurement = sm3_hash(f"{components}|{quote_id}".encode())
```
CLAIM:
measurement 由随机 `quote_id` 派生(`sm3_hash(f"{components}|{quote_id}")`,SGX/SEV 同理是 `sm3_hash(f"sgx-enclave-{quote_id}")`),即每次 `generate_quote` 都产生一个全新的、不可预测的 measurement,并且 verify 只是用同样公式重算一次。后果:(1) `allowed_measurements` 白名单永远无法命中,生产环境按注释要求配置非空白名单后所有本地 quote 都会被拒,运维只能留空 → 变相 fail-open;(2) measurement 不体现任何镜像/内核/固件度量,“度量校验”实质无效。建议 measurement 只由稳定的组件度量(kernel/rootfs/config 的真实哈希)构成,不含 `quote_id`。顺带:此行 f-string 无占位符,应改为普通字符串。

## C9 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
min_tcb_version: str = ""
    require_signed: bool = True
```
CLAIM:
`min_tcb_version` 与 `require_signed` 两个策略字段在整个文件中从未被读取(模块 docstring 与 `verify_quote` docstring 都宣称包含 “TCB version check”/“require_signed”),即策略第 5 步 TCB 版本校验与实际签名要求均未实现,`tcb_status` 也只是 provider 硬编码返回 `"up_to_date"`。属于未落实的安全约束,建议在 `AttestationService.verify_quote` 中真正实现这两项检查,或删除字段以免给调用方错误的安全预期。

## C10 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
def register_provider(self, provider: AttestationProvider) -> None:
        """Register a custom TEE attestation provider."""
        self._providers[provider.tee_type] = provider
```
CLAIM:
`register_provider` 覆盖内置 provider(例如接入真实 DCAP 的 SGX provider)后,`_local_root_types` 仍包含 `TEEType.SGX`,导致 `verify_quote` 无条件把真实硬件证明的结果标记为 `trust_root="local-simulated"`(反之亦然:信任根来源被误标)。建议以 provider 实例为粒度记录信任根,例如在 provider 上暴露 `trust_root` 属性,并在注册时同步更新/移除 `_local_root_types` 中的条目。

## C11 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
self._verification_log: list[AttestationResult] = []
```
CLAIM:
`_verification_log` 挂在模块级单例 `attestation_service` 上,只追加不裁剪:长驻进程中每次验证都保留完整 `AttestationResult`(含 measurement、signer_chain 等),内存单调增长;并且 `list.append` 与 `get_verification_log`/`get_stats` 的遍历并发执行时无任何同步,多线程 Web 服务下可能读到中间状态并出现统计与日志不一致。建议改为 `collections.deque(maxlen=N)` 并用 `threading.Lock` 保护读写(或使用线程安全审计存储/落盘)。

## C12 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
from app.services.crypto_service import crypto_service
```
CLAIM:
`crypto_service` 在本文件中从未被引用,属于无用导入;同时顶层导入 `app.services.crypto_service` 也会在本模块被 crypto 相关模块间接引用时引入循环导入风险。建议删除该导入(或改为需要时延迟导入)。

## C13 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
result.policy_checks["tee_type_allowed"] = True
        result.policy_checks["quote_fresh"] = True
        result.policy_checks["measurement_whitelisted"] = True
```
CLAIM:
这三项在 provider 已返回失败结果(如 `INVALID_SIGNATURE`/`QUOTE_MALFORMED`/`ERROR`)时依然被无条件写成 `True`,会污染审计结果;并且当 `allowed_measurements` 为空或 `require_fresh_quote=False` 时,对应检查其实并未执行,记录 `True` 具有误导性。建议按实际执行情况写入,例如 `measurement_whitelisted = bool(self._policy.allowed_measurements)`,并在 `result.verified` 为假时不追加通过性标记。

## C14 [low/performance]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
logger.info(f"Registered attestation provider: {provider.tee_type.value}")
```
CLAIM:
f-string 在调用 `logger.info` 前就被格式化,绕过了 logging 的惰性求值(即便 INFO 关闭也会付出格式化开销),与本文件另一处已使用 `"%s", e` 的风格不一致。建议改为占位符形式。
SUGGESTED FIX (tool): logger.info("Registered attestation provider: %s", provider.tee_type.value)

## C15 [low/performance]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
logger.info(f"Attestation {quote.quote_id}: {result.status.value} ({result.duration_ms}ms)")
```
CLAIM:
同上,f-string 直接传给 `logger.info` 会使日志级别被禁用时仍然执行格式化。建议改为 `logger.info("Attestation %s: %s (%sms)", quote.quote_id, result.status.value, result.duration_ms)`。
SUGGESTED FIX (tool): logger.info(
            "Attestation %s: %s (%sms)",
            quote.quote_id,
            result.status.value,
            result.duration_ms,
        )

## C16 [low/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
return entries[-limit:]
```
CLAIM:
`entries[-limit:]` 在 `limit=0` 时等于 `entries[0:]`,会返回全量日志而非空列表;传入负数时(如 `limit=-5`)会返回除前 5 条以外的全部条目。调用方若将外部参数(如接口 `limit` 查询参)直接传入,会引发语义错误或一次隋性的全量返回。建议先做边界归一化:`limit = max(1, min(limit, MAX_LOG))`。
SUGGESTED FIX (tool): return entries[-max(1, limit):]


## Known importers (grep of whole repo for "remote_attestation" in *.py)
- app/services/compliance_report.py
- app/services/kms_service.py
- app/services/result_verifier.py
- app/services/sandbox_runtime.py
- tests/test_kms_attestation_required.py
- tests/test_pz05_federation_attestation.py
- tests/test_pz06_disclosure.py
- tests/test_remote_attestation.py
- tests/test_security_config_matrix.py
