# Claims for app/services/mutual_tls.py (11)

## C1 [critical/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
lines = [
            "-----BEGIN CERTIFICATE-----",
            f"Subject: {cert.subject}",
```
CLAIM:
`_cert_to_pem` 生成的不是合法 PEM（PEM 基线之间必须是 base64(DER)，这里塞的是 `Subject:`/`Issuer:` 等明文键值行，还截断成 `[:64]`），OpenSSL/httpx 解析必然失败。`get_tls_context` 却把这些字段命名为 `cert_pem`/`ca_cert_pem` 并配上 `"verify": True`，调用方会误以为拿到了可直接用于 TLS 握手的双向认证材料（要么握手报错，要么被当成"有证书"而绕过真实校验）。同时 `key_der` 实际返回的是十六进制私钥字符串，命名（DER）与内容都不符，且私钥以明文散落在业务 dict 中。建议：用 `cryptography` 库真正生成 X.509 DER 并 `base64` 编码为 PEM（或落盘成临时文件返回路径，字段改为 `certfile`/`keyfile`）；如果本层不打算产出可用材料，就不要暴露 `get_tls_context`，让调用方只使用 `load_federation_mtls_material`。

## C2 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# Sign the certificate payload
        payload_str = "|".join(f"{k}={v}" for k, v in sorted(cert_data.items()))
```
CLAIM:
签名载荷用未转义的 `|` / `=` 直接拼接字段（`generate_ca_certificate`、`issue_certificate`、`verify_certificate` 三处），字段值本身可含分隔符，破坏了"签名字节 ↔ 字段集合"的一一对应：
- `subject`（`CDS Space {space_id}`，space_id 来自外部）、`public_key` 等若包含 `|` 或 `=`，两种不同的字段组合可拼出完全相同的载荷（如 `subject="A|is_ca=True"` 与 `subject="A"` + 额外字段），造成签名歧义/字段语义漂移；
- 三份重复的拼接逻辑一旦某处字段增删不同步，签名与校验立刻不一致（`verify_certificate` 已经和签发侧存在 space_id 条件包含的差异，见另一条）。
建议抽出统一的规范化序列化函数，使用带转义的编码，例如 `json.dumps(cert_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()`，签发与校验共用同一实现。

## C3 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
ca = ca_cert or self.generate_ca_certificate()
```
CLAIM:
证书校验范围不足，`verify_certificate` 实际只做了"签名匹配 + 时间窗"，缺失 X.509 校验的核心约束：
1. 不校验 `cert.issuer` 是否等于 `ca.subject`——任何由同一密钥材料签出的证书、甚至 issuer 被填成任意值的证书都能通过；
2. 不校验 `ca.is_ca`/基本约束与 key_usage，传入叶子证书当 `ca_cert` 也不会被拒；
3. 不做主体与 space 的绑定校验（见下条），无法证明"这张证书属于我正在连接的这个对端"；
4. 没有链深度/路径构建，多级 CA 场景不可用。
另外这里的兜底 `ca_cert or self.generate_ca_certificate()` 会在调用方漏传 CA 时静默用本地 CA 私钥现造一张 CA 证书继续校验，把配置错误变成"校验通过"，与 `verify_peer_certificate` 的 fail-closed 策略相矛盾。建议：ca_cert 缺失时直接返回 False 并告警；显式校验 `ca.is_ca`、`cert.issuer == ca.subject`、有效期与预期 space/subject 绑定（必要时对 subject 做白名单/正则），并考虑引入 `cryptography` 做真正的链校验。

## C4 [high/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
def verify_peer_certificate(self, cert: CertificateInfo) -> bool:
```
CLAIM:
`verify_peer_certificate` 只回答"这张证书是否由本联邦 CA 签发且未过期"，不接收/校验期望的 `space_id`。由于每张证书都是 per-space 签发的，任何合法 space 的证书都能通过任意其它 space 的对端校验（缺少 audience/主体绑定），即 space C 可以拿自己的证书冒充 space B 完成 mTLS 身份认证。建议签名改为 `verify_peer_certificate(self, cert: CertificateInfo, expected_space_id: str) -> bool`，在验签成功后强制 `cert.space_id == expected_space_id`（并校验 subject 中的 space 标识），不匹配即返回 False 并记录审计告警。

## C5 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        ctx.load_cert_chain(certfile=cert, keyfile=key)
    except ssl.SSLError as e:
        raise ValueError(f"Federation mTLS cert/key pair is invalid: {e}")
```
CLAIM:
这里只验证了 cert/key 能成对加载，没有验证 client cert 与 CA bundle 的信任关系（未 `ctx.load_verify_locations(cafile=ca_bundle)`，也未校验证书链、有效期、key_usage、client cert 是否由该 CA 签发）。因此"client cert 属于另一个 CA / 已过期 / 与 CA bundle 完全不匹配"这类错误配置会通过校验，直到真正握手时才在对端失败，属于"静默放行错误配置"，与函数声明的 fail-loudly 目标不符。另外 `load_cert_chain` 除 `ssl.SSLError` 外还可能抛出其它 `OSError`（如 key 文件不可读）以及 `ValueError`（部分版本对 passphrase/格式问题抛 ValueError），只捕 `SSLError` 会漏。建议：捕获 `(ssl.SSLError, OSError, ValueError)` 并 `raise ValueError(...) from e`；补充解析 CA bundle 与 client cert（`cryptography`），校验 `not_valid_until` 未过期、client cert 可由 CA bundle 验证的链（或至少 issuer/AKI-SKI 匹配），并对 key 文件权限做提示（如 group/world 可读则告警）。

## C6 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
if cert.space_id:
            cert_data["space_id"] = cert.space_id
```
CLAIM:
签发与校验对 `space_id` 的处理不对称：`issue_certificate` 总是把 `space_id` 写入签名载荷（即使为 `""`），而这里只在 `cert.space_id` 为真值时才加入。因此一张 `space_id == ""` 的证书，其签名字节包含 `space_id=`，重建载荷却不含该键，验签必然失败——这是隐蔽的往返（round-trip）不一致，只表现为"莫名验签不通过"。同时 `now < cert.not_before`、以及 `self._crypto.verify(...)` 对远端提交的证书没有防御：若对端传来的 `not_before/not_after` 是 naive datetime（JSON 反序列化常见结果），比较会抛 `TypeError`；若 `signature` 非 128 位十六进制（`cert.signature = str(...)` 兜底产物或恶意构造），`crypto_service.verify` 内部 `int(..., 16)` 会抛 `ValueError`。这些异常会以 500 的形式冒泡到联邦接口，而不是按契约返回 False。建议：签发/校验统一用同一个载荷构造函数（`space_id` 一律包含）；入口处校验时间字段为 tz-aware、signature 为合法十六进制串，并把 `self._crypto.verify` 用 `except Exception` 包裹后 `logger.warning` + 返回 False。

## C7 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
"issuer": "CDS Federation CA",
```
CLAIM:
`issuer` 被硬编码为 `"CDS Federation CA"`（此处以及下方 `CertificateInfo(issuer=...)`），而 `generate_ca_certificate(subject=...)` 允许 CA 主体为任意值。于是用自定义主体初始化的 CA 签出的证书，其 issuer 与实际 CA 主体不一致；一旦补上 `cert.issuer == ca.subject` 的校验（见前述建议），所有已签发证书都会立刻失效。请让 `issue_certificate` 从 `self._ca_cert.subject`（不存在则先生成）取 issuer，并把字符串常量提为类常量或参数，保证签发与 CA 主体同源。

## C8 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
text = p.read_text()
        if must_contain not in text:
            raise ValueError(f"Federation {label} is not valid PEM ({must_contain} missing): {path}")
```
CLAIM:
文档字符串承诺"无效材料一律抛 ValueError（配置错误必须显式失败）"，但 `p.read_text()` 抛出的 `OSError`（权限不足、路径是目录、符号链接断裂、`is_file()` 与读取之间的 TOCTOU）与 `UnicodeDecodeError` 都会原样冒泡，破坏调用方（如 `validate_security_config` / 启动钩子）只捕获 `ValueError` 的契约，导致启动/健康检查以非预期异常类型崩溃。建议显式收窄：`try: text = Path(path).read_text(encoding="utf-8") except (OSError, UnicodeDecodeError) as e: raise ValueError(...) from e`；同时只检查子串 `BEGIN CERTIFICATE` 过弱，至少应确认文件包含合法 PEM 块（例如用 `cryptography.x509.load_pem_x509_certificate` 解析），避免把占位文本当成有效证书。

## C9 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
if self._ca_cert is None:
            self._ca_cert = self._ca.generate_ca_certificate()
        return self._ca_cert
```
CLAIM:
`mtls_manager` 是模块级全局单例，会在 FastAPI 同步端点（线程池）中被并发调用，但 `_ca_cert` 与 `_identities` 的读写完全没有同步：`initialize_ca` / `create_identity` 都是"先判 None 再生成"的 check-then-act，两个线程同时进入会各自生成一张 CA 证书，后写入者覆盖前者，导致此前基于前一张 CA 签发的所有 identity 在 `verify_peer_certificate` 时验签失败（表现为随机、难复现的对端认证失败）。`create_identity` 对 `_identities[space_id]` 的写入同理存在与其他读取方（`get_identity`/`get_tls_context`）的可见性竞态。建议引入 `threading.Lock`（或 `threading.RLock`）保护 `initialize_ca`/`create_identity` 的复合更新，或改用启动时一次性初始化 + 之后只读的不可变结构。

## C10 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
self._identities[space_id] = identity
        logger.info(f"[mTLS] Created identity for space {space_id}")
```
CLAIM:
`create_identity` 对同一 `space_id` 重复调用会静默覆盖已有 identity：旧的私钥/证书被丢弃但没有任何告警，也没有撤销机制，而对端可能仍缓存旧证书——结果是对端认证突然失败，且旧私钥若已泄露无从吊销。建议：已存在 identity 时显式拒绝（或要求 `rotate=True` 之类的显式参数），并在轮换时记录旧证书序列号、将旧证书加入吊销/黑名单集合供 `verify_peer_certificate` 检查。另外 `keypair = self._ca._crypto.generate_keypair()` 直接访问 `SM2CertificateAuthority` 的私有属性，破坏了封装，建议在 CA 上暴露公开方法（如 `generate_keypair()`）供管理器调用。

## C11 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
```
CLAIM:
`hashlib`、`time` 以及 `dataclasses.field` 在本文件中均未被引用（全文没有 `hashlib.`、`time.`、`field(` 的使用），属于无用导入，会误导读者以为存在哈希/计时逻辑。请删除这三处未使用的导入。


## Known importers (grep of whole repo for "mutual_tls" in *.py)
- app/services/federation_connector.py
- tests/test_mtls.py
- tests/test_pz05_federation_attestation.py
