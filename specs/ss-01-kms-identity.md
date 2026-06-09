# SS-01：密钥管理（KMS）& 身份/PKI 分系统技术规格

> 版本：v1.0 | 分系统编号：SS-01  
> 上游依赖：HSM 硬件、操作系统随机数源  
> 下游依赖方：SS-02（合约引擎）、SS-03（任务调度）、SS-04（沙箱运行时）、SS-08（互联互通）

---

## 1. 职责边界

### 1.1 本系统负责

| 职责 | 说明 |
|------|------|
| SM2 PKI 体系 | 根 CA / 中间 CA / 实体证书 全链路签发、验证、吊销 |
| 数据产品密钥（DEK）生命周期 | 生成 → 托管 → 合约绑定分发 → 吊销 |
| 会话密钥（SK）管理 | per-session 短生命周期密钥派生、下发 |
| TEE 密钥分发通道 | 基于远程证明报告（Quote）向 TEE Enclave 安全下发 DEK |
| L2 MPC 密钥分片 | Shamir 秘密分享，数商分片（K1）+ 计算节点分片（K2）管理 |
| 跨空间身份联邦 | 验证来自外部数据空间的 SM2 证书（互信根 CA 链） |
| 密钥审计 | 所有密钥操作（生成/分发/吊销）记录不可篡改日志 |

### 1.2 本系统不负责

- 合约条款校验（SS-02 负责）
- 沙箱内数据解密（SS-04 在 TEE 内自主完成）
- 网络传输加密（由 API 网关 TLCP 层负责）

### 1.3 组件内部结构

```
┌────────────────────────────────────────────────────────────┐
│                  KMS & Identity Service                     │
│                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │  CA 服务     │  │  密钥库      │  │  证明验证器       │ │
│  │  CertManager │  │  KeyVault    │  │  AttestVerifier  │ │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘ │
│         │                 │                    │           │
│  ┌──────▼─────────────────▼────────────────────▼────────┐  │
│  │              核心编排层 KMSOrchestrator              │  │
│  └──────────────────────────┬───────────────────────────┘  │
│                             │                              │
│  ┌──────────────────────────▼───────────────────────────┐  │
│  │              HSM 适配层 HSMAdapter                   │  │
│  │   国产 HSM（AS2805/BJCA）| Vault Transit | 软件 KMS  │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

---

## 2. 数据模型

### 2.1 证书实体（Certificate）

```sql
CREATE TABLE certificates (
    cert_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    serial_number   BIGINT UNIQUE NOT NULL,          -- 递增序列号
    subject_dn      TEXT NOT NULL,                   -- CN=xxx,O=xxx,C=CN
    subject_type    TEXT NOT NULL                    -- 'root_ca'|'intermediate_ca'|'node'|'provider'|'consumer'|'cross_space'
                    CHECK (subject_type IN ('root_ca','intermediate_ca','node','provider','consumer','cross_space')),
    issuer_cert_id  UUID REFERENCES certificates(cert_id),
    public_key_pem  TEXT NOT NULL,                   -- SM2 公钥 PEM
    cert_pem        TEXT NOT NULL,                   -- 完整 X.509 PEM
    fingerprint_sm3 CHAR(64) NOT NULL UNIQUE,        -- SM3(DER编码证书) hex
    not_before      TIMESTAMPTZ NOT NULL,
    not_after       TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','revoked','expired')),
    revoke_reason   TEXT,                            -- RFC 5280 CRLReason
    revoked_at      TIMESTAMPTZ,
    space_id        TEXT,                            -- 所属数据空间（跨空间互认用）
    created_at      TIMESTAMPTZ DEFAULT now(),
    metadata        JSONB DEFAULT '{}'
);

CREATE INDEX idx_cert_subject_type ON certificates(subject_type, status);
CREATE INDEX idx_cert_fingerprint ON certificates(fingerprint_sm3);
CREATE INDEX idx_cert_not_after ON certificates(not_after) WHERE status = 'active';
```

### 2.2 数据产品密钥（DEK）

```sql
CREATE TABLE data_encryption_keys (
    dek_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id      TEXT NOT NULL,                   -- 关联数据产品 ID
    space_id        TEXT NOT NULL,
    key_version     INT NOT NULL DEFAULT 1,          -- 支持密钥轮换
    algorithm       TEXT NOT NULL DEFAULT 'SM4-GCM', -- SM4-GCM | SM4-CBC
    key_length_bits INT NOT NULL DEFAULT 128,
    -- 密钥材料从不以明文存入数据库
    -- encrypted_key_material: DEK 明文 → SM2 加密（provider 公钥）→ Base64
    encrypted_key_b64   TEXT NOT NULL,
    encryption_cert_id  UUID REFERENCES certificates(cert_id),  -- 用哪个公钥加密
    hsm_key_label   TEXT,                            -- HSM 内 key label（若托管于 HSM）
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','suspended','revoked')),
    created_at      TIMESTAMPTZ DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    revoke_trigger  TEXT,                            -- 'provider_request'|'contract_expire'|'security_incident'
    usage_count     BIGINT DEFAULT 0,                -- 已分发次数
    max_usage       BIGINT DEFAULT 10000,            -- 最大分发次数（防止过度使用）
    UNIQUE (product_id, key_version)
);
```

### 2.3 密钥分发记录（KeyDistribution）

```sql
CREATE TABLE key_distributions (
    dist_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dek_id          UUID NOT NULL REFERENCES data_encryption_keys(dek_id),
    contract_id     TEXT NOT NULL,                   -- 触发分发的合约
    session_id      TEXT NOT NULL,                   -- 目标沙箱会话
    sandbox_level   TEXT NOT NULL CHECK (sandbox_level IN ('L1','L2','L3')),
    -- L1: Quote 哈希（验证 TEE 环境）
    tee_quote_hash  TEXT,
    tee_mrenclave   TEXT,                            -- Enclave 度量值，白名单验证
    -- L2: 分片信息
    mpc_shard_k2_encrypted  TEXT,                   -- K2 分片，计算节点公钥加密
    mpc_shard_k1_ref        TEXT,                   -- K1 分片引用（数商侧保管，仅存引用）
    -- 分发的会话密钥（加密 DEK 用）
    session_key_enc TEXT NOT NULL,                   -- SM4 会话密钥，用目标节点公钥加密
    distributed_at  TIMESTAMPTZ DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,            -- 会话密钥过期时间
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','expired','revoked')),
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX idx_dist_session ON key_distributions(session_id, status);
CREATE INDEX idx_dist_dek ON key_distributions(dek_id);
```

### 2.4 密钥审计事件（KeyAuditEvent）

```sql
CREATE TABLE key_audit_events (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      TEXT NOT NULL,
    -- 枚举: cert_issue|cert_revoke|dek_create|dek_distribute|dek_revoke|
    --       session_key_issue|mpc_shard_distribute|attestation_verify
    actor_cert_id   UUID REFERENCES certificates(cert_id),
    actor_ip        INET,
    target_id       TEXT NOT NULL,                   -- dek_id 或 cert_id
    target_type     TEXT NOT NULL,
    result          TEXT NOT NULL CHECK (result IN ('success','failure')),
    failure_reason  TEXT,
    request_payload JSONB,                           -- 脱敏后的请求摘要
    sm3_digest      CHAR(64),                        -- SM3(event_id+event_type+actor+target+result)
    created_at      TIMESTAMPTZ DEFAULT now()
);
-- 不允许 UPDATE/DELETE（仅 INSERT）通过行级安全策略强制
ALTER TABLE key_audit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_insert_only ON key_audit_events FOR INSERT TO kms_service WITH CHECK (true);
-- 注：禁止 UPDATE 和 DELETE 通过权限控制（kms_service 角色无 UPDATE/DELETE 权限）
```

---

## 3. 状态机

### 3.1 证书生命周期

```
         颁发
PENDING ──────→ ACTIVE
                  │
                  ├── 主动吊销（数商申请 / 安全事件）
                  │         ↓
                  │      REVOKED  ←── 私钥泄露/合规违规触发
                  │
                  └── 自然过期（not_after 到期）
                            ↓
                         EXPIRED
```

**状态转换规则**：
- `ACTIVE → REVOKED`：需要操作员二次确认（防误操作）；同步触发 CRL 更新 + 所有关联 DEK 暂停
- `ACTIVE → EXPIRED`：后台定时任务（每小时）扫描，自动转换；不触发 DEK 暂停（正常到期）
- `REVOKED/EXPIRED → ACTIVE`：**禁止**（证书不可恢复，需重新签发）

### 3.2 DEK 生命周期

```
                  数商上传数据产品
                       ↓
                    CREATED
                       │
             数据产品通过质检并发布
                       ↓
                    ACTIVE ←─────────────────────┐
                       │                          │
          合约激活，有合法会话请求分发              │（密钥轮换，新版本 ACTIVE）
                       │
              [分发流程：见 §4.2]
                       │
              ┌────────┴────────────┐
        合约到期/主动吊销       安全事件/异常
              ↓                    ↓
           REVOKED             SUSPENDED
              │                    │
              │               调查完成
              │                    │
              └──── 永久 ──→ REVOKED
```

### 3.3 密钥分发会话状态

```
PENDING_ATTESTATION              ← 等待沙箱提交证明报告
       │
       ├── attestation 验证失败 → REJECTED（终态）
       │
       ▼
ATTESTATION_VERIFIED
       │
       ├── 合约校验失败（合约未激活/已过期）→ REJECTED
       │
       ▼
KEY_DISTRIBUTED                  ← DEK（加密后）已下发至 TEE
       │
       ├── 会话超时 → EXPIRED（终态）
       ├── 主动吊销 → REVOKED（终态）
       │
       ▼
ACTIVE（沙箱正在使用密钥）
       │
       └── 任务完成 / 会话超时 / 主动注销 → COMPLETED（终态）
```

---

## 4. 核心流程实现

### 4.1 证书签发（SM2 X.509）

```python
class CertManager:
    def issue_certificate(
        self,
        subject_dn: str,
        subject_type: SubjectType,
        subject_public_key_pem: str,
        validity_days: int,
        issuer: "CertManager",
        extensions: dict = None
    ) -> Certificate:
        """
        签发 SM2 X.509v3 证书
        """
        # 1. 参数校验
        validate_dn_format(subject_dn)  # 必须含 C=CN, O=xxx, CN=xxx
        sm2_pub = load_sm2_public_key(subject_public_key_pem)

        # 2. 构造 TBSCertificate（To-Be-Signed）
        serial = self._next_serial()    # 原子递增，防重复
        not_before = datetime.utcnow()
        not_after = not_before + timedelta(days=validity_days)

        tbs = TBSCertificate(
            version=2,                  # X.509 v3
            serial_number=serial,
            signature_algorithm=AlgorithmIdentifier("SM2-SM3"),  # OID 1.2.156.10197.1.501
            issuer=issuer.subject_dn,
            validity=Validity(not_before, not_after),
            subject=subject_dn,
            subject_public_key_info=SubjectPublicKeyInfo(sm2_pub),
            extensions=self._build_extensions(subject_type, extensions)
        )

        # 3. DER 编码 → SM3 摘要 → SM2 签名（HSM 保护私钥）
        tbs_der = encode_der(tbs)
        signature = self.hsm.sm2_sign(
            data=sm3_hash(tbs_der),
            key_label=issuer.hsm_key_label  # 私钥从不离开 HSM
        )

        # 4. 组装完整证书
        cert_der = encode_certificate(tbs, signature)
        fingerprint = sm3_hash(cert_der).hex()

        # 5. 持久化
        cert = Certificate(
            serial_number=serial,
            subject_dn=subject_dn,
            subject_type=subject_type,
            public_key_pem=subject_public_key_pem,
            cert_pem=der_to_pem(cert_der),
            fingerprint_sm3=fingerprint,
            not_before=not_before,
            not_after=not_after,
        )
        db.insert(cert)
        self._emit_audit_event("cert_issue", cert.cert_id)
        return cert

    def _build_extensions(self, subject_type: SubjectType, extra: dict) -> list:
        exts = [
            BasicConstraints(ca=(subject_type in ('root_ca', 'intermediate_ca')),
                             path_length=0 if subject_type == 'intermediate_ca' else None),
            KeyUsage(digital_signature=True, key_encipherment=True),
            SubjectKeyIdentifier(auto=True),
            AuthorityKeyIdentifier(auto=True),
        ]
        if subject_type == 'node':
            exts.append(ExtendedKeyUsage(['serverAuth', 'clientAuth']))
        if subject_type == 'cross_space':
            # 跨空间互认证书携带 space_id 自定义扩展
            exts.append(CustomExtension(
                oid="1.2.156.10197.6.1.4.2.99",   # 自定义 OID（向 OSCCA 申请）
                value=extra.get('space_id', '').encode()
            ))
        if extra:
            exts.extend(extra.get('additional_extensions', []))
        return exts
```

### 4.2 TEE 密钥分发流程（L1）

```python
class KeyVault:
    def distribute_key_to_tee(
        self,
        dek_id: str,
        contract_id: str,
        session_id: str,
        tee_quote: bytes,           # TEE 提交的证明报告（SGX Quote）
        node_cert_pem: str          # 沙箱节点 SM2 证书
    ) -> KeyDistributionResult:

        # Step 1: 验证证明报告
        quote_result = self.attest_verifier.verify_sgx_quote(tee_quote)
        if not quote_result.valid:
            raise AttestationError(quote_result.reason)

        # Step 2: MRENCLAVE 白名单校验（防止恶意 Enclave 伪装）
        expected_mrenclave = self._load_approved_mrenclave(
            product_id=self._get_product_by_dek(dek_id)
        )
        if quote_result.mrenclave != expected_mrenclave:
            raise EnclaveNotApproved(
                f"MRENCLAVE mismatch: got {quote_result.mrenclave}, "
                f"expected {expected_mrenclave}"
            )

        # Step 3: 验证合约有效性（调用 SS-02）
        contract = contract_service.get_active_contract(contract_id)
        if contract.status != ContractStatus.ACTIVE:
            raise ContractNotActive(contract_id)
        if dek_id not in contract.authorized_dek_ids:
            raise UnauthorizedDEK(dek_id)

        # Step 4: 生成会话密钥 SK（ephemeral，仅此次分发使用）
        sk = generate_random_sm4_key()          # 16 bytes，SM4-128
        sk_expiry = now() + timedelta(hours=contract.session_max_hours)

        # Step 5: 取出 DEK 明文（仅在 HSM 内操作，明文不离开 HSM）
        # HSM 操作：DEK_plaintext = HSM.decrypt(dek.encrypted_key_b64, provider_privkey)
        # 再用 SK 加密：enc_dek = SM4_GCM_encrypt(DEK_plaintext, SK)
        # 整个操作在 HSM 内完成，避免 DEK 明文出现在 KMS 进程内存中
        enc_dek_with_sk = self.hsm.rewrap_key(
            source_ciphertext=dek.encrypted_key_b64,
            source_key_label=dek.encryption_cert_id,    # 用 provider 私钥解密
            target_key=sk,                              # 用 SK 重新加密
            algorithm="SM4-GCM"
        )

        # Step 6: 用 TEE 节点公钥加密 SK（只有 TEE 内部持有私钥能解密）
        node_pubkey = load_sm2_pubkey(node_cert_pem)
        enc_sk = sm2_encrypt(sk, node_pubkey)

        # Step 7: 持久化分发记录
        dist = KeyDistribution(
            dek_id=dek_id,
            contract_id=contract_id,
            session_id=session_id,
            sandbox_level="L1",
            tee_quote_hash=sm3_hash(tee_quote).hex(),
            tee_mrenclave=quote_result.mrenclave,
            session_key_enc=base64(enc_sk),
            expires_at=sk_expiry,
        )
        db.insert(dist)
        dek.usage_count += 1
        self._emit_audit_event("dek_distribute", dek_id, contract_id=contract_id)

        return KeyDistributionResult(
            enc_sk=base64(enc_sk),          # TEE 用节点私钥解密得到 SK
            enc_dek_with_sk=enc_dek_with_sk, # TEE 用 SK 解密得到 DEK
            expires_at=sk_expiry,
        )
```

### 4.3 L2 MPC 密钥分片（Shamir 2-of-2）

```python
class MPCKeyManager:
    """
    L2 环境无 TEE，通过 Shamir (2,2) 门限确保计算节点无法单独获取 DEK
    """
    def distribute_l2_shards(
        self,
        dek_id: str,
        contract_id: str,
        provider_cert_id: str,      # 数商证书（K1 分片接收方）
        compute_node_cert_id: str   # 计算节点证书（K2 分片接收方）
    ) -> L2ShardResult:

        # Step 1: 在 HSM 内部做 (2,2) 秘密分享
        # DEK 明文 → Shamir split → (K1, K2)
        # 整个分片操作在 HSM 内完成，K1/K2 明文不出 HSM
        k1_enc, k2_enc = self.hsm.shamir_split_and_encrypt(
            source_key_label=dek.hsm_key_label,
            recipient1_cert_id=provider_cert_id,    # K1 用数商公钥加密
            recipient2_cert_id=compute_node_cert_id  # K2 用计算节点公钥加密
        )

        # Step 2: K1 直接返回给数商（数商侧保管，不存入 KMS 数据库）
        # K2 存入 KMS，合约激活期间可取

        dist = KeyDistribution(
            dek_id=dek_id,
            contract_id=contract_id,
            sandbox_level="L2",
            mpc_shard_k2_encrypted=base64(k2_enc),
            mpc_shard_k1_ref=f"provider_side:{provider_cert_id}:{contract_id}",
        )
        db.insert(dist)

        return L2ShardResult(
            k1_for_provider=base64(k1_enc),  # 数商自己保管
            k2_ref=dist.dist_id,             # 计算节点通过 dist_id 取 K2
        )

    def reconstruct_key_in_mpc(
        self,
        dist_id: str,
        k1_contribution: bytes,     # 数商在 MPC 协议中贡献 K1
        compute_session_token: str  # 计算节点身份令牌
    ) -> bool:
        """
        MPC 在线阶段：双方贡献分片，在密态通道内重组 DEK
        重组结果直接注入沙箱内存，不落盘，不被任一方单独持有
        """
        dist = db.get(KeyDistribution, dist_id)
        # 验证计算节点合法性
        verify_session_token(compute_session_token, dist.contract_id)

        # MPC 协议执行（SPDZ 变体，两方通信）
        # 具体实现委托给 MPC 运行时库（如 MP-SPDZ 国产化版本）
        mpc_runtime.online_phase(
            party1_input=k1_contribution,           # 数商侧
            party2_input=dist.mpc_shard_k2_encrypted,  # 计算节点侧
            output_target="sandbox_memory",         # 结果直接写入沙箱内存
            session_id=dist.session_id,
        )
        # 注：此函数返回 True 表示 MPC 协议成功完成，不返回 DEK 明文
        return True
```

### 4.4 证书吊销与 CRL/OCSP

> **设计决策**：采用 OCSP 在线验证 + 定期完整 CRL 方案，移除增量 CRL。理由：(1) 内部系统 OCSP 响应延迟 <100ms，(2) 增量 CRL 增加复杂度但收益有限，(3) OCSP Stapling 可进一步减少查询开销。

```python
class CRLManager:
    """
    证书吊销列表（CRL）管理
    采用 OCSP 在线验证 + 定期完整 CRL 方案
    """
    CRL_FULL_INTERVAL_HOURS = 24      # 完整 CRL 每 24h 重签发
    OCSP_CACHE_TTL_SECONDS = 60       # OCSP 响应缓存 60s

    def revoke_certificate(self, cert_id: str, reason: CRLReason, operator_id: str):
        cert = db.get(Certificate, cert_id)
        if cert.status != 'active':
            raise CertNotRevocable(f"Certificate {cert_id} is already {cert.status}")

        with db.transaction():
            # 1. 更新证书状态
            db.update(Certificate, cert_id, status='revoked',
                      revoke_reason=reason.value, revoked_at=now())

            # 2. 联动暂停所有关联 DEK
            affected_deks = db.query(
                "SELECT dek_id FROM data_encryption_keys "
                "WHERE encryption_cert_id = %s AND status = 'active'", cert_id
            )
            for dek in affected_deks:
                db.update(DEK, dek.dek_id, status='suspended')

            # 3. 吊销所有活跃密钥分发会话
            active_dists = db.query(
                "SELECT dist_id FROM key_distributions WHERE dek_id = ANY(%s) "
                "AND status = 'active'",
                [d.dek_id for d in affected_deks]
            )
            for dist in active_dists:
                db.update(KeyDistribution, dist.dist_id, status='revoked', revoked_at=now())
                # 通知沙箱立即销毁密钥
                sandbox_notifier.send_key_revocation(dist.session_id)

        # 4. 清除 OCSP 缓存（使下次查询实时生效）
        redis.delete(f"ocsp:{cert_id}")
        # 5. 标记 CRL 需要重建（下一个定时任务会处理）
        redis.set("crl_dirty", "1")
        self._emit_audit_event("cert_revoke", cert_id, operator_id=operator_id)

    def check_ocsp(self, cert_id: str) -> dict:
        """OCSP 在线验证 — 实时查询证书状态"""
        cached = redis.get(f"ocsp:{cert_id}")
        if cached:
            return json.loads(cached)

        cert = db.get(Certificate, cert_id)
        result = {
            "cert_id": cert_id,
            "status": cert.status,
            "revoked_at": cert.revoked_at.isoformat() if cert.revoked_at else None,
            "this_update": datetime.now(timezone.utc).isoformat(),
            "next_update": (datetime.now(timezone.utc) + timedelta(seconds=self.OCSP_CACHE_TTL_SECONDS)).isoformat(),
        }
        redis.setex(f"ocsp:{cert_id}", self.OCSP_CACHE_TTL_SECONDS, json.dumps(result))
        return result

    def get_crl(self) -> bytes:
        """返回 DER 编码的完整 CRL（每 24h 重建）"""
        cache_key = "full_crl"
        cached = redis.get(cache_key)
        if cached:
            return cached
        crl_bytes = self._build_crl()
        redis.setex(cache_key, self.CRL_FULL_INTERVAL_HOURS * 3600, crl_bytes)
        return crl_bytes
```

---

## 5. HSM 适配层

### 5.1 支持的 HSM 型号

| 产品 | 厂商 | 接口协议 | 优先级 |
|------|------|----------|--------|
| BJCA GHSM 系列 | 北京CA | PKCS#11 + 专有API | 国产首选 |
| AS2805 | 三未信安 | PKCS#11 | 国产备选 |
| Utimaco SecurityServer | Utimaco | PKCS#11 | 进口备用 |
| HashiCorp Vault（Transit Secrets Engine）| 软件 | REST API | 开发/测试环境 |

### 5.2 HSM 适配器接口

```python
class HSMAdapter(Protocol):
    """统一 HSM 操作接口（Strategy 模式，屏蔽不同 HSM 型号差异）"""

    def sm2_sign(self, data: bytes, key_label: str) -> bytes:
        """SM2 签名，私钥不离开 HSM"""
        ...

    def sm2_decrypt(self, ciphertext: bytes, key_label: str) -> bytes:
        """SM2 解密"""
        ...

    def sm4_generate_key(self, key_label: str, exportable: bool = False) -> str:
        """生成 SM4 密钥，返回 HSM 内 label"""
        ...

    def sm4_encrypt(self, plaintext: bytes, key_label: str, iv: bytes) -> bytes:
        """SM4-GCM 加密（密钥在 HSM 内）"""
        ...

    def rewrap_key(
        self, source_ciphertext: bytes, source_key_label: str,
        target_key: bytes, algorithm: str
    ) -> bytes:
        """密钥重包装：HSM 内解密后重加密，明文不出 HSM"""
        ...

    def shamir_split_and_encrypt(
        self, source_key_label: str,
        recipient1_cert_id: str, recipient2_cert_id: str
    ) -> tuple[bytes, bytes]:
        """Shamir (2,2) 分片，各分片用对应接收方公钥加密后返回密文"""
        ...

    def health_check(self) -> HSMHealthStatus:
        ...
```

### 5.3 HSM 故障降级策略

```
HSM 状态检测（每 30s）
    │
    ├── 健康 → 正常路由至 HSM
    │
    ├── 响应慢（>500ms）→ 告警 + 记录，继续使用
    │
    ├── 主 HSM 不可达（3次重试失败）
    │       ↓
    │   自动切换至备用 HSM（主备部署，同步密钥材料）
    │       ↓
    │   告警升级（P1 告警，15分钟内人工介入）
    │
    └── 主备均不可达（极端情况）
            ↓
        拒绝所有新密钥分发请求（新任务无法启动）
        已运行沙箱会话继续直到超时（不强制终止，防止数据丢失）
        开启应急 Vault Transit 模式（软件级，安全等级降级，需运营审批）
```

---

## 6. 对外 REST 接口（内部服务间）

> **设计决策**：KMS 作为单体服务的内部模块，使用 REST API 而非 gRPC。理由：(1) 减少依赖复杂度，(2) 与现有 FastAPI 架构一致，(3) 内部调用无需 gRPC 的多语言支持和流式特性。

### 6.1 证书管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/kms/certificates` | POST | 签发证书 |
| `/api/v1/kms/certificates/{id}/verify` | GET | 验证证书 |
| `/api/v1/kms/certificates/{id}/revoke` | POST | 吊销证书 |
| `/api/v1/kms/crl` | GET | 获取 CRL |

### 6.2 DEK 管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/kms/dek` | POST | 创建 DEK |
| `/api/v1/kms/dek/{id}/distribute` | POST | 分发 DEK 到 TEE |
| `/api/v1/kms/dek/{id}/distribute-l2` | POST | 分发 MPC 分片 |
| `/api/v1/kms/dek/{id}/revoke` | POST | 吊销 DEK |

### 6.3 证明验证

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/kms/attestation/verify` | POST | 验证 TEE Quote |

请求体：
```json
{
  "sgx_quote": "<base64>",
  "expected_product_id": "xxx",
  "session_id": "uuid"
}
```

响应：
```json
{
  "valid": true,
  "mrenclave": "abc123...",
  "mrsigner": "def456...",
  "isv_svn": 1,
  "failure_reason": null
}
```

### 6.4 跨空间身份验证

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/kms/cross-space/verify` | POST | 跨空间身份验证 |

---

## 7. 安全注意事项

### 7.1 密钥材料保护

> **DEK 明文位置一致性声明**：
> - **KMS 进程**：DEK 明文仅在 HSM 内部出现，KMS 进程内存中只有密文
> - **沙箱 TEE 进程**：DEK 明文在 TEE 内存中存在（由 KMS 用 TEE 公钥加密后下发，TEE 内部解密）
> - **沙箱非 TEE 进程**：DEK 明文在安全隔离的进程内存中存在（由 KMS 用节点公钥加密后下发）
> - **磁盘/网络**：DEK 明文永远不出现，始终以加密形式存储和传输
>
> SS-04 中 `sm4_gcm_decrypt(encrypted, self.dek)` 的 `self.dek` 指的是 TEE/沙箱进程内存中的明文 DEK，由 KMS 安全分发。

| 风险 | 缓解措施 |
|------|----------|
| DEK 明文出现在 KMS 进程内存 | 所有密钥操作通过 HSM 完成；KMS 进程只处理密文 |
| 数据库被拖库 | DEK 以数商公钥加密存储，数据库即使泄露也无法解密 |
| KMS 节点被攻陷 | HSM 私钥物理隔离；KMS 仅能使用 HSM API，不能导出私钥 |
| 内存 dump 攻击 | KMS 进程启用内存加密（Linux 进程内存锁定 + mlock）；swap 禁用 |
| 会话密钥重放 | 每个 SK 绑定 session_id + expires_at；KMS 校验时间窗口（±5s 容忍） |

### 7.2 证明报告防伪

- MRENCLAVE 白名单在 KMS 配置文件中维护，由运营方通过 OTA 更新（不允许运行时动态修改）
- 任何 MRENCLAVE 更新需要运营方 + 安全团队双人审批，并记录变更日志
- Intel DCAP 证明服务证书链须定期更新（Intel PCK 证书有效期约 2 年）

### 7.3 MPC 分片安全

- K1 分片仅发送给数商，不存入 KMS 数据库（KMS 只存 K1 分片引用，不存内容）
- K2 分片以计算节点公钥加密存储，且绑定合约 ID（不同合约的 K2 不可混用）
- MPC 在线阶段通信必须通过 TLCP 加密通道，防止中间人截获分片

---

## 8. 配置参数

```yaml
kms:
  hsm:
    provider: "pkcs11"              # pkcs11 | vault_transit | software
    pkcs11_lib_path: "/usr/lib/softhsm/libsofthsm2.so"
    slot_id: 0
    pin_env_var: "HSM_PIN"          # 从环境变量读取，不写配置文件
    redundancy:
      mode: "active_passive"        # 主备模式
      primary_slot_id: 0
      backup_slot_id: 1
      failover_timeout_ms: 3000
      health_check_interval_s: 30

  ca:
    root_cert_path: "/etc/cds/pki/root-ca.pem"   # 根 CA 证书（只读）
    intermediate_key_label: "intermediate_ca_key"  # 中间 CA 私钥在 HSM 中的 label
    default_validity_days:
      provider: 365
      consumer: 365
      node: 90
      cross_space: 730
    crl_distribution_point: "https://cds.internal/pki/crl"
    ocsp_responder_url: "https://cds.internal/pki/ocsp"

  dek:
    algorithm: "SM4-GCM"
    key_length_bits: 128
    max_usage_per_key: 10000        # 单个 DEK 最大分发次数
    auto_rotate_after_days: 90      # 超期自动标记需轮换

  mpc:
    threshold: 2                    # (2,2) 门限
    protocol: "spdz_2k"             # spdz_2k | semi2k（离线预计算模式）
    preprocessing_batch_size: 1000  # 每批预生成的乘法三元组数量

  session_key:
    default_ttl_hours: 24
    max_ttl_hours: 168              # 最长 7 天（合约最长期限限制）

  audit:
    emit_to_kafka_topic: "kms-audit-events"
    chain_batch_size: 100
    chain_batch_interval_s: 30
```

---

*文档：SS-01 | 版本：v1.0 | 依赖：HSM 硬件规格、TC609 PKI 规范*
