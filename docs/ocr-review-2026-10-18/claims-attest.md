### [app/services/mutual_tls.py] critical/security
`_cert_to_pem` 生成的不是合法 PEM（PEM 基线之间必须是 base64(DER)，这里塞的是 `Subject:`/`Issuer:` 等明文键值行，还截断成 `[:64]`），OpenSSL/httpx 解析必然失败。`get_tls_context` 却把这些字段命名为 `cert_pem`/`ca_cert_pem` 并配上 `"verify": True`，调用方会误以为拿到了可直接用于 TLS 握手的双向认证材料（要么握手报错，要么被当成"有证书"而绕过真实校验）。同时 `key_der` 实际返回的是十六进制私钥字符串，命名（DER）与内容都不符，且私钥以明文散落在业务 dict 中。建议：用 `cryptography` 库真正生成 X.509 DER 并 `base64` 编码为 PEM（或落盘成临时文件返回路径，字段改为 `certfile`/`keyfile`）；如果本层不打算产出可用材料，就不要暴露 `get_tls_context`，让调用方只使用 `load_federation_mtls_material`。
-- 

### [app/services/mutual_tls.py] high/security
签名载荷用未转义的 `|` / `=` 直接拼接字段（`generate_ca_certificate`、`issue_certificate`、`verify_certificate` 三处），字段值本身可含分隔符，破坏了"签名字节 ↔ 字段集合"的一一对应：
- `subject`（`CDS Space {space_id}`，space_id 来自外部）、`public_key` 等若包含 `|` 或 `=`，两种不同的字段组合可拼出完全相同的载荷（如 `subject="A|is_ca=True"` 与 `subject="A"` + 额外字段），造成签名歧义/字段语义漂移；
- 三份重复的拼接逻辑一旦某处字段增删不同步，签名与校验立刻不一致（`verify_certificate` 已经和签发侧存在 space_id 条件包含的差异，见另一条）。
建议抽出统一的规范化序列化函数，使用带转义的编码，例如 `json.dumps(cert_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()`，签发与校验共用同一实现。
-- 

### [app/services/mutual_tls.py] high/security
证书校验范围不足，`verify_certificate` 实际只做了"签名匹配 + 时间窗"，缺失 X.509 校验的核心约束：
1. 不校验 `cert.issuer` 是否等于 `ca.subject`——任何由同一密钥材料签出的证书、甚至 issuer 被填成任意值的证书都能通过；
2. 不校验 `ca.is_ca`/基本约束与 key_usage，传入叶子证书当 `ca_cert` 也不会被拒；
3. 不做主体与 space 的绑定校验（见下条），无法证明"这张证书属于我正在连接的这个对端"；
4. 没有链深度/路径构建，多级 CA 场景不可用。
另外这里的兜底 `ca_cert or self.generate_ca_certificate()` 会在调用方漏传 CA 时静默用本地 CA 私钥现造一张 CA 证书继续校验，把配置错误变成"校验通过"，与 `verify_peer_certificate` 的 fail-closed 策略相矛盾。建议：ca_cert 缺失时直接返回 False 并告警；显式校验 `ca.is_ca`、`cert.issuer == ca.subject`、有效期与预期 space/subject 绑定（必要时对 subject 做白名单/正则），并考虑引入 `cryptography` 做真正的链校验。
-- 

### [app/services/mutual_tls.py] high/bug
`verify_peer_certificate` 只回答"这张证书是否由本联邦 CA 签发且未过期"，不接收/校验期望的 `space_id`。由于每张证书都是 per-space 签发的，任何合法 space 的证书都能通过任意其它 space 的对端校验（缺少 audience/主体绑定），即 space C 可以拿自己的证书冒充 space B 完成 mTLS 身份认证。建议签名改为 `verify_peer_certificate(self, cert: CertificateInfo, expected_space_id: str) -> bool`，在验签成功后强制 `cert.space_id == expected_space_id`（并校验 subject 中的 space 标识），不匹配即返回 False 并记录审计告警。
-- 

### [app/services/mutual_tls.py] high/security
这里只验证了 cert/key 能成对加载，没有验证 client cert 与 CA bundle 的信任关系（未 `ctx.load_verify_locations(cafile=ca_bundle)`，也未校验证书链、有效期、key_usage、client cert 是否由该 CA 签发）。因此"client cert 属于另一个 CA / 已过期 / 与 CA bundle 完全不匹配"这类错误配置会通过校验，直到真正握手时才在对端失败，属于"静默放行错误配置"，与函数声明的 fail-loudly 目标不符。另外 `load_cert_chain` 除 `ssl.SSLError` 外还可能抛出其它 `OSError`（如 key 文件不可读）以及 `ValueError`（部分版本对 passphrase/格式问题抛 ValueError），只捕 `SSLError` 会漏。建议：捕获 `(ssl.SSLError, OSError, ValueError)` 并 `raise ValueError(...) from e`；补充解析 CA bundle 与 client cert（`cryptography`），校验 `not_valid_until` 未过期、client cert 可由 CA bundle 验证的链（或至少 issuer/AKI-SKI 匹配），并对 key 文件权限做提示（如 group/world 可读则告警）。
-- 

### [app/services/remote_attestation.py] critical/security
本地信任根 `_LOCAL_ATTESTATION_ROOT` 以明文常量硬编码在源码中,而 `_sign_quote_payload`/`_signature_valid` 用它同时做“签名”和“验签”。这意味着任何拿到源码(或读到该常量)的人都能离线构造出通过 `verify_quote` 的伪造 quote,被标记为 `verified` 并触发 KMS DEK 下发,远程证明的安全价值降为零。建议:(1) 该模拟根仅允许在显式的 test/dev 开关下启用,生产环境启动即拒绝(未配置真实 DCAP/VCEK 验证就 fail-closed);(2) 若必须保留软件签名,应从 KMS/环境变量读取每实例密钥,且密钥不落源码。同时 `AttestationResult.trust_root` 目前默认 `"external"`,调用方极易把 `local-simulated` 当成真实硬件证明使用,建议对 `local-simulated` 增加强制的下游拦截。
-- 

### [app/services/remote_attestation.py] high/security
验签用 `==` 比较两个十六进制字符串,不是恒定时间比较,存在时序侧信道;更关键的是 `_sign_quote_payload` 只是 `hash(静态根 + payload)`(Merkle–Damgård 结构的 SM3),并非真正的 MAC/签名,不具备不可伪造性。建议改用 HMAC-SM3(或 HMAC-SHA256)并通过 `hmac.compare_digest` 比较。
-- 

### [app/services/remote_attestation.py] high/security
新鲜度检查完全依赖调用方构造 `TEEQuote` 时填入的 `timestamp` 字段:该字段既不在 `raw_bytes` 中,也不在签名覆盖范围内,因此攻击者可以拿一个很早生成的合法 quote,配上“当前时间”的 `timestamp` 直接绕过 `max_quote_age_seconds`(重放攻击)。同时 `report_data`(应作为会话/challenge nonce 绑定)在三个 provider 的验签逻辑中都从未与本次会话期望值比对,无法证明该 quote 是为当前会话签发的。建议:把 `timestamp` 与 `report_data` 纳入签名 payload 并在 verify 中校验 nonce 与时间戳,拒绝 `age < 0`(未来时间戳,允许时钟偏移需设上界)。此外若上游传入 naive datetime,`datetime.now(timezone.utc) - quote.timestamp` 会抛未捕获的 `TypeError`,建议显式校验 tz-aware。
-- 

### [app/services/remote_attestation.py] high/security
读取 `ATTESTATION_ALLOWED_MEASUREMENTS` 失败时仅告警并继续,而空白名单的语义是“允许所有 measurement”,构成典型 fail-open:配置缺失/损坏/配置中心抖动时会静默放宽最强的证明约束。建议这里 fail-closed(直接抛配置错误或置一个 `_policy_load_failed` 标记使 `verify_quote` 全部返回 `POLICY_VIOLATION`),并把 `except Exception` 收窄为实际的配置异常类型。
-- 

### [app/services/mutual_tls.py] medium/bug
签发与校验对 `space_id` 的处理不对称：`issue_certificate` 总是把 `space_id` 写入签名载荷（即使为 `""`），而这里只在 `cert.space_id` 为真值时才加入。因此一张 `space_id == ""` 的证书，其签名字节包含 `space_id=`，重建载荷却不含该键，验签必然失败——这是隐蔽的往返（round-trip）不一致，只表现为"莫名验签不通过"。同时 `now < cert.not_before`、以及 `self._crypto.verify(...)` 对远端提交的证书没有防御：若对端传来的 `not_before/not_after` 是 naive datetime（JSON 反序列化常见结果），比较会抛 `TypeError`；若 `signature` 非 128 位十六进制（`cert.signature = str(...)` 兜底产物或恶意构造），`crypto_service.verify` 内部 `int(..., 16)` 会抛 `ValueError`。这些异常会以 500 的形式冒泡到联邦接口，而不是按契约返回 False。建议：签发/校验统一用同一个载荷构造函数（`space_id` 一律包含）；入口处校验时间字段为 tz-aware、signature 为合法十六进制串，并把 `self._crypto.verify` 用 `except Exception` 包裹后 `logger.warning` + 返回 False。
-- 

### [app/services/mutual_tls.py] medium/bug
`issuer` 被硬编码为 `"CDS Federation CA"`（此处以及下方 `CertificateInfo(issuer=...)`），而 `generate_ca_certificate(subject=...)` 允许 CA 主体为任意值。于是用自定义主体初始化的 CA 签出的证书，其 issuer 与实际 CA 主体不一致；一旦补上 `cert.issuer == ca.subject` 的校验（见前述建议），所有已签发证书都会立刻失效。请让 `issue_certificate` 从 `self._ca_cert.subject`（不存在则先生成）取 issuer，并把字符串常量提为类常量或参数，保证签发与 CA 主体同源。
-- 

### [app/services/mutual_tls.py] medium/bug
文档字符串承诺"无效材料一律抛 ValueError（配置错误必须显式失败）"，但 `p.read_text()` 抛出的 `OSError`（权限不足、路径是目录、符号链接断裂、`is_file()` 与读取之间的 TOCTOU）与 `UnicodeDecodeError` 都会原样冒泡，破坏调用方（如 `validate_security_config` / 启动钩子）只捕获 `ValueError` 的契约，导致启动/健康检查以非预期异常类型崩溃。建议显式收窄：`try: text = Path(path).read_text(encoding="utf-8") except (OSError, UnicodeDecodeError) as e: raise ValueError(...) from e`；同时只检查子串 `BEGIN CERTIFICATE` 过弱，至少应确认文件包含合法 PEM 块（例如用 `cryptography.x509.load_pem_x509_certificate` 解析），避免把占位文本当成有效证书。
-- 

### [app/services/mutual_tls.py] medium/bug
`mtls_manager` 是模块级全局单例，会在 FastAPI 同步端点（线程池）中被并发调用，但 `_ca_cert` 与 `_identities` 的读写完全没有同步：`initialize_ca` / `create_identity` 都是"先判 None 再生成"的 check-then-act，两个线程同时进入会各自生成一张 CA 证书，后写入者覆盖前者，导致此前基于前一张 CA 签发的所有 identity 在 `verify_peer_certificate` 时验签失败（表现为随机、难复现的对端认证失败）。`create_identity` 对 `_identities[space_id]` 的写入同理存在与其他读取方（`get_identity`/`get_tls_context`）的可见性竞态。建议引入 `threading.Lock`（或 `threading.RLock`）保护 `initialize_ca`/`create_identity` 的复合更新，或改用启动时一次性初始化 + 之后只读的不可变结构。
-- 

### [app/services/mutual_tls.py] medium/bug
`create_identity` 对同一 `space_id` 重复调用会静默覆盖已有 identity：旧的私钥/证书被丢弃但没有任何告警，也没有撤销机制，而对端可能仍缓存旧证书——结果是对端认证突然失败，且旧私钥若已泄露无从吊销。建议：已存在 identity 时显式拒绝（或要求 `rotate=True` 之类的显式参数），并在轮换时记录旧证书序列号、将旧证书加入吊销/黑名单集合供 `verify_peer_certificate` 检查。另外 `keypair = self._ca._crypto.generate_keypair()` 直接访问 `SM2CertificateAuthority` 的私有属性，破坏了封装，建议在 CA 上暴露公开方法（如 `generate_keypair()`）供管理器调用。
-- 

### [app/services/remote_attestation.py] medium/bug
`except (json.JSONDecodeError, UnicodeDecodeError)` 未覆盖 `raw_bytes` 解析出非 dict 的情况:若外部提交的 quote 为 JSON 数组/标量/`null`,`parsed.get(...)` 会抛出未捕获的 `AttributeError`/`TypeError`,直接以 500 冒泡(该路径也不会写入 `_verification_log`,审计缺失)。建议在解析后显式校验 `isinstance(parsed, dict)` 并返回 `QUOTE_MALFORMED`。
-- parsed = json.loads(quote.raw_bytes)
            if not isinstance(parsed, dict):
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.QUOTE_MALFORMED,
                    tee_type=TEEType.SGX,
                    measure

### [app/services/remote_attestation.py] medium/bug
同上:非 dict 的 JSON quote 会让 `parsed.get` 抛出未捕获的 `AttributeError`/`TypeError`。建议与 SGX 一致,解析后先做 `isinstance(parsed, dict)` 校验并映射为 `QUOTE_MALFORMED`。
-- 

### [app/services/remote_attestation.py] medium/bug
同样的非 dict JSON 崩溃风险(只捕获了 `JSONDecodeError`/`UnicodeDecodeError`)。另外本 provider 与 SGX/SEV 不同,既没有 `quote.tee_type != TEEType.FIRECRACKER` 的类型守卫,也没有校验 `parsed.get("type") == "software_hash"`,一旦被直接调用或复用其他类型的 raw_bytes 仍可能通过校验。建议补齐 isinstance 校验与类型/字段守卫。
-- 

### [app/services/remote_attestation.py] medium/security
measurement 由随机 `quote_id` 派生(`sm3_hash(f"{components}|{quote_id}")`,SGX/SEV 同理是 `sm3_hash(f"sgx-enclave-{quote_id}")`),即每次 `generate_quote` 都产生一个全新的、不可预测的 measurement,并且 verify 只是用同样公式重算一次。后果:(1) `allowed_measurements` 白名单永远无法命中,生产环境按注释要求配置非空白名单后所有本地 quote 都会被拒,运维只能留空 → 变相 fail-open;(2) measurement 不体现任何镜像/内核/固件度量,“度量校验”实质无效。建议 measurement 只由稳定的组件度量(kernel/rootfs/config 的真实哈希)构成,不含 `quote_id`。顺带:此行 f-string 无占位符,应改为普通字符串。
-- 

### [app/services/remote_attestation.py] medium/bug
`min_tcb_version` 与 `require_signed` 两个策略字段在整个文件中从未被读取(模块 docstring 与 `verify_quote` docstring 都宣称包含 “TCB version check”/“require_signed”),即策略第 5 步 TCB 版本校验与实际签名要求均未实现,`tcb_status` 也只是 provider 硬编码返回 `"up_to_date"`。属于未落实的安全约束,建议在 `AttestationService.verify_quote` 中真正实现这两项检查,或删除字段以免给调用方错误的安全预期。
-- 

### [app/services/remote_attestation.py] medium/bug
`register_provider` 覆盖内置 provider(例如接入真实 DCAP 的 SGX provider)后,`_local_root_types` 仍包含 `TEEType.SGX`,导致 `verify_quote` 无条件把真实硬件证明的结果标记为 `trust_root="local-simulated"`(反之亦然:信任根来源被误标)。建议以 provider 实例为粒度记录信任根,例如在 provider 上暴露 `trust_root` 属性,并在注册时同步更新/移除 `_local_root_types` 中的条目。
-- 

### [app/services/remote_attestation.py] medium/bug
`_verification_log` 挂在模块级单例 `attestation_service` 上,只追加不裁剪:长驻进程中每次验证都保留完整 `AttestationResult`(含 measurement、signer_chain 等),内存单调增长;并且 `list.append` 与 `get_verification_log`/`get_stats` 的遍历并发执行时无任何同步,多线程 Web 服务下可能读到中间状态并出现统计与日志不一致。建议改为 `collections.deque(maxlen=N)` 并用 `threading.Lock` 保护读写(或使用线程安全审计存储/落盘)。
-- 

### [app/services/mutual_tls.py] low/maintainability
`hashlib`、`time` 以及 `dataclasses.field` 在本文件中均未被引用（全文没有 `hashlib.`、`time.`、`field(` 的使用），属于无用导入，会误导读者以为存在哈希/计时逻辑。请删除这三处未使用的导入。
-- 

### [app/services/remote_attestation.py] low/maintainability
`crypto_service` 在本文件中从未被引用,属于无用导入;同时顶层导入 `app.services.crypto_service` 也会在本模块被 crypto 相关模块间接引用时引入循环导入风险。建议删除该导入(或改为需要时延迟导入)。
-- 

### [app/services/remote_attestation.py] low/maintainability
这三项在 provider 已返回失败结果(如 `INVALID_SIGNATURE`/`QUOTE_MALFORMED`/`ERROR`)时依然被无条件写成 `True`,会污染审计结果;并且当 `allowed_measurements` 为空或 `require_fresh_quote=False` 时,对应检查其实并未执行,记录 `True` 具有误导性。建议按实际执行情况写入,例如 `measurement_whitelisted = bool(self._policy.allowed_measurements)`,并在 `result.verified` 为假时不追加通过性标记。
-- 

### [app/services/remote_attestation.py] low/performance
f-string 在调用 `logger.info` 前就被格式化,绕过了 logging 的惰性求值(即便 INFO 关闭也会付出格式化开销),与本文件另一处已使用 `"%s", e` 的风格不一致。建议改为占位符形式。
-- logger.info("Registered attestation provider: %s", provider.tee_type.value)

### [app/services/remote_attestation.py] low/performance
同上,f-string 直接传给 `logger.info` 会使日志级别被禁用时仍然执行格式化。建议改为 `logger.info("Attestation %s: %s (%sms)", quote.quote_id, result.status.value, result.duration_ms)`。
-- logger.info(
            "Attestation %s: %s (%sms)",
            quote.quote_id,
            result.status.value,
            result.duration_ms,
        )

### [app/services/remote_attestation.py] low/bug
`entries[-limit:]` 在 `limit=0` 时等于 `entries[0:]`,会返回全量日志而非空列表;传入负数时(如 `limit=-5`)会返回除前 5 条以外的全部条目。调用方若将外部参数(如接口 `limit` 查询参)直接传入,会引发语义错误或一次隋性的全量返回。建议先做边界归一化:`limit = max(1, min(limit, MAX_LOG))`。
-- return entries[-max(1, limit):]
