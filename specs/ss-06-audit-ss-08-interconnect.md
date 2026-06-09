# SS-06：审计与存证 分系统技术规格（完整版）

> 版本：v1.0 | 分系统编号：SS-06

---

## 1. 审计日志采集架构

```
各分系统（SS-01 ~ SS-09）内部
    │ 同步写入（TEE 内，SM2 签名）
    ▼
Kafka Topic: cds-audit-raw
    │
    ├──→ 实时消费者 A：写入 ClickHouse（细粒度存储，检索用）
    │         ・批量写入（每 500 条 or 5s）
    │         ・列存压缩（LZ4），查询优化
    │
    ├──→ 实时消费者 B：Merkle 批次 → FISCO BCOS 存证
    │         ・每 1000 条 or 30s 生成一批
    │         ・SM3 Merkle 树根哈希上链
    │
    └──→ 实时消费者 C：告警规则引擎
              ・Flink 流处理（CEP 复杂事件检测）
              ・P1/P2 告警触发
```

---

## 2. 完整 ClickHouse Schema

```sql
-- 主审计事件表（按月分区）
CREATE TABLE cds_audit.audit_events
(
    event_id         UUID,
    event_type       LowCardinality(String),
    event_version    UInt8 DEFAULT 1,
    severity         LowCardinality(String) DEFAULT 'INFO',

    -- 时间（毫秒精度）
    event_time       DateTime64(3, 'UTC'),
    event_date       Date MATERIALIZED toDate(event_time),

    -- 来源标识
    space_id         LowCardinality(String),
    node_id          String,
    sandbox_level    FixedString(2),

    -- 关联上下文
    session_id       String,
    task_id          String,
    contract_id      String,
    product_ids      Array(String),

    -- 操作者
    actor_org_id     String,
    actor_cert_fp    String,
    actor_role       LowCardinality(String),
    actor_ip         IPv6,

    -- 操作对象
    resource_type    LowCardinality(String),
    resource_id      String,
    action           LowCardinality(String),

    -- 策略执行结果
    policy_decision  LowCardinality(String),
    policy_rule_id   String,
    deny_reason      String,

    -- 差分隐私指标
    dp_epsilon       Float32,
    dp_budget_remaining Float32,

    -- 数据安全指标
    pii_instances    UInt16,
    output_rows      UInt32,
    output_bytes     UInt64,

    -- 模型训练指标（训练场景填写）
    train_epoch      UInt16,
    train_loss       Float32,
    mia_score        Float32,
    memorization_score Float32,
    epsilon_consumed Float32,

    -- 完整性保障
    payload_sm3      FixedString(64),
    tee_signature    String,

    -- 链上存证
    merkle_batch_id  UInt64,
    merkle_leaf_idx  UInt32,
    blockchain_tx    String,

    -- 弹性附加字段（JSON）
    extra            String
)
ENGINE = MergeTree()
PARTITION BY (toYYYYMM(event_date), space_id)
ORDER BY (event_date, session_id, event_time, event_id)
TTL event_date + INTERVAL 365 DAY TO VOLUME 'cold_storage'
SETTINGS index_granularity = 8192;

-- Bloom filter 索引（高基数字段精确查找）
ALTER TABLE cds_audit.audit_events ADD INDEX idx_session
    (session_id) TYPE bloom_filter(0.01) GRANULARITY 4;
ALTER TABLE cds_audit.audit_events ADD INDEX idx_contract
    (contract_id) TYPE bloom_filter(0.01) GRANULARITY 4;
ALTER TABLE cds_audit.audit_events ADD INDEX idx_actor
    (actor_cert_fp) TYPE bloom_filter(0.01) GRANULARITY 4;
ALTER TABLE cds_audit.audit_events ADD INDEX idx_event_type
    (event_type) TYPE set(50) GRANULARITY 4;

-- 汇总视图（按合约维度，用于合规报告）
CREATE MATERIALIZED VIEW cds_audit.contract_summary_mv
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(event_date)
ORDER BY (contract_id, event_date, event_type)
AS SELECT
    contract_id,
    event_date,
    event_type,
    count()             AS event_count,
    sum(output_rows)    AS total_output_rows,
    sum(output_bytes)   AS total_output_bytes,
    sum(dp_epsilon)     AS total_dp_charged,
    countIf(policy_decision = 'deny') AS deny_count,
    countIf(pii_instances > 0)        AS pii_detected_count
FROM cds_audit.audit_events
GROUP BY contract_id, event_date, event_type;
```

---

## 3. 告警规则引擎（Flink CEP）

```python
# Flink CEP 告警规则（Python DSL）

class AuditAlertRules:

    @staticmethod
    def consecutive_policy_deny():
        """连续策略拒绝告警：5分钟内同一会话被拒绝 ≥5 次"""
        return (
            Pattern.begin("first_deny")
                   .where(lambda e: e.policy_decision == "deny")
                   .times(5)
                   .within(timedelta(minutes=5))
                   .partition_by("session_id")
        )

    @staticmethod
    def mia_probe_failure():
        """MIA 探针失败：模型记忆风险超标，立即 P1 告警"""
        return (
            Pattern.begin("mia_fail")
                   .where(lambda e: e.event_type == "output.mia.failed")
        )

    @staticmethod
    def dp_budget_near_exhaustion():
        """DP 预算接近耗尽：剩余预算 < 10%"""
        return (
            Pattern.begin("low_budget")
                   .where(lambda e: (
                       e.event_type == "output.dp.charged" and
                       e.dp_budget_remaining / (e.dp_budget_remaining + e.dp_epsilon) < 0.1
                   ))
        )

    @staticmethod
    def anomaly_detected():
        """eBPF 安全异常：立即熔断"""
        return (
            Pattern.begin("anomaly")
                   .where(lambda e: e.event_type == "security.anomaly.detected")
        )

    @staticmethod
    def cross_space_auth_spike():
        """跨空间认证异常峰值：10分钟内同一外部空间 >100 次认证失败"""
        return (
            Pattern.begin("auth_fail")
                   .where(lambda e: (
                       e.event_type == "iam.crossspace.auth" and
                       e.policy_decision == "deny"
                   ))
                   .times_or_more(100)
                   .within(timedelta(minutes=10))
                   .partition_by("actor_org_id")
        )

ALERT_ACTIONS = {
    "consecutive_policy_deny":   AlertAction(severity="WARNING",
                                              notify=["security_team"],
                                              auto_actions=["increment_anomaly_counter"]),
    "mia_probe_failure":         AlertAction(severity="CRITICAL",
                                              notify=["security_team", "provider"],
                                              auto_actions=["block_model_export",
                                                            "suspend_session"]),
    "dp_budget_near_exhaustion": AlertAction(severity="WARNING",
                                              notify=["consumer"],
                                              auto_actions=["notify_budget_low"]),
    "anomaly_detected":          AlertAction(severity="CRITICAL",
                                              notify=["security_team"],
                                              auto_actions=["kill_session",
                                                            "revoke_session_key"]),
    "cross_space_auth_spike":    AlertAction(severity="CRITICAL",
                                              notify=["security_team", "operator"],
                                              auto_actions=["rate_limit_space"]),
}
```

---

## 4. Merkle 存证与验真

```python
class SM3MerkleTree:
    """SM3 哈希函数的 Merkle 树实现"""

    def __init__(self, leaves: list[bytes]):
        self.leaves = [sm3_hash(l) for l in leaves]
        self.tree = self._build(self.leaves)

    def _build(self, layer: list[bytes]) -> list[list[bytes]]:
        result = [layer]
        while len(layer) > 1:
            if len(layer) % 2 == 1:
                layer = layer + [layer[-1]]   # 奇数叶：复制最后一个
            layer = [
                sm3_hash(layer[i] + layer[i+1])
                for i in range(0, len(layer), 2)
            ]
            result.append(layer)
        return result

    @property
    def root(self) -> bytes:
        return self.tree[-1][0]

    def get_proof(self, index: int) -> list[tuple[str, bytes]]:
        """生成 Merkle 证明路径（供链上验真）"""
        proof = []
        for layer in self.tree[:-1]:
            sibling_idx = index ^ 1  # XOR 1 得到兄弟节点
            if sibling_idx < len(layer):
                direction = "right" if index % 2 == 0 else "left"
                proof.append((direction, layer[sibling_idx]))
            index //= 2
        return proof

    @staticmethod
    def verify_proof(
        leaf: bytes,
        proof: list[tuple[str, bytes]],
        root: bytes
    ) -> bool:
        """验证 Merkle 证明路径"""
        current = sm3_hash(leaf)
        for direction, sibling in proof:
            if direction == "right":
                current = sm3_hash(current + sibling)
            else:
                current = sm3_hash(sibling + current)
        return current == root
```

---

## 5. 合规报告生成

```python
class ComplianceReportGenerator:
    """
    按 TC609-6-2025-01 格式生成合规报告
    报告内容：合约执行情况、数据使用统计、安全事件摘要
    """

    def generate_contract_report(
        self, contract_id: str, period_start: date, period_end: date
    ) -> ComplianceReport:

        # 从 ClickHouse 聚合
        stats = clickhouse.query("""
            SELECT
                count() AS total_events,
                countIf(event_type LIKE 'task.%') AS task_events,
                countIf(event_type LIKE 'output.%') AS output_events,
                countIf(policy_decision = 'deny') AS policy_deny_count,
                sum(output_rows) AS total_output_rows,
                sum(output_bytes) AS total_output_bytes,
                sum(dp_epsilon) AS total_dp_consumed,
                countIf(pii_instances > 0) AS batches_with_pii,
                sum(pii_instances) AS total_pii_detected,
                countIf(event_type = 'security.anomaly.detected') AS security_anomalies
            FROM cds_audit.audit_events
            WHERE contract_id = %(contract_id)s
              AND event_date BETWEEN %(start)s AND %(end)s
        """, {"contract_id": contract_id, "start": period_start, "end": period_end})

        contract = contract_service.get(contract_id)

        return ComplianceReport(
            contract_id=contract_id,
            period=(period_start, period_end),
            summary={
                "total_sandbox_sessions": stats.task_events,
                "policy_compliance_rate": 1 - stats.policy_deny_count / max(stats.total_events, 1),
                "dp_budget_utilization": stats.total_dp_consumed / contract.dp_budget_total,
                "pii_detection_rate": stats.batches_with_pii / max(stats.output_events, 1),
                "security_incidents": stats.security_anomalies,
            },
            blockchain_verification_url=f"https://explorer.cds.internal/contract/{contract_id}",
            generated_at=datetime.utcnow(),
            report_signature=sm2_sign(str(stats), PLATFORM_SIGNING_KEY),
        )
```

---

*文档：SS-06 | 版本：v1.0*

---
---

# SS-08：互联互通连接器 分系统技术规格（完整版）

> 版本：v1.0 | 分系统编号：SS-08  
> 对齐标准：TC609-6-2025-01、NDI-TR-2025-02

---

## 1. 跨空间会话完整协议

### 1.1 连接建立时序（完整）

```
买方（空间B）              连接器B          连接器A           CDS沙箱（空间A侧）
    │                        │                │                      │
    │ 1.查询数据目录           │                │                      │
    ├───────────────────────>│                │                      │
    │                        │ 2.跨空间目录查询 │                      │
    │                        ├──────────────>│                      │
    │                        │<─产品列表─────  │                      │
    │<──────────────────────  │                │                      │
    │                        │                │                      │
    │ 3.发起合约协商           │                │                      │
    ├───────────────────────>│                │                      │
    │                        │ 4.转发合约草稿  │                      │
    │                        ├──────────────>│                      │
    │                        │ [协商循环]      │                      │
    │                        │<─确认条款─────  │                      │
    │<─合约草稿───────────────│                │                      │
    │                        │                │                      │
    │ 5.买方 SM2 签名          │                │                      │
    ├───────────────────────>│ 6.转发买方签名  │                      │
    │                        ├──────────────>│                      │
    │                        │ 7.数商 SM2 签名 │                      │
    │                        │<─数商签名─────  │                      │
    │                        │ 8.平台见证 + 上链│                      │
    │                        │<─合约生效───── │                      │
    │<─合约 ID + 生效通知──────│                │                      │
    │                        │                │                      │
    │ 9.创建沙箱会话           │                │                      │
    │  （携带合约 ID + 买方证书）│                │                      │
    ├───────────────────────>│                │                      │
    │                        │ 10.跨空间认证   │                      │
    │                        ├──────────────>│                      │
    │                        │                │ 11.创建 TEE 沙箱      │
    │                        │                ├────────────────────>│
    │                        │                │ 12.获取证明报告        │
    │                        │                │<─────────────────────│
    │                        │<─会话ID+证明──  │                      │
    │<─会话 ID + 证明报告──────│                │                      │
    │                        │                │                      │
    │ 13.独立验证证明报告      │                │                      │
    │   （DCAP/Intel）        │                │                      │
    │                        │                │                      │
    │ 14.提交计算任务          │                │                      │
    ├───────────────────────>│                │                      │
    │                        │ 15.路由至沙箱   │                      │
    │                        ├──────────────────────────────────>│
    │                        │                │ TEE 内执行             │
    │                        │                │ SS-05 审查             │
    │                        │<──────────────────────────────────│
    │<─审查后结果 + 签名────────│                │                      │
```

---

## 2. 跨空间数据目录同步协议

```python
class CrossSpaceCatalogSync:
    """
    跨空间数据目录同步：将本空间可外部访问的数据产品同步至联盟目录
    支持：推送模式（数据更新时主动推送）+ 拉取模式（对端查询时按需拉取）
    """

    def publish_product_to_federation(self, product: DataProduct, target_spaces: list[str]):
        """将数据产品发布至指定外部空间可见"""
        catalog_entry = self._build_cross_space_entry(product)

        for space_id in target_spaces:
            connector = self.get_connector(space_id)
            try:
                connector.call_with_breaker(
                    lambda: connector.post(
                        "/connector/v1/catalog/ingest",
                        json=catalog_entry,
                        headers={"X-Source-Space": self.local_space_id,
                                 "X-Signature": sm2_sign(catalog_entry, PLATFORM_KEY)}
                    )
                )
                logger.info(f"Published product {product.product_id} to space {space_id}")
            except ConnectorCircuitOpen:
                # 目标空间不可达：存入本地缓冲，待恢复后重试
                self.pending_publishes.append((product.product_id, space_id))

    def _build_cross_space_entry(self, product: DataProduct) -> dict:
        """构建跨空间可见的目录条目（不含内部敏感配置）"""
        return {
            "product_id": f"{self.local_space_id}::{product.product_id}",
            "source_space_id": self.local_space_id,
            "name": product.name,
            "category": product.category,
            "data_type": product.data_type,
            "media_type": product.media_type,
            "volume_summary": product.get_volume_summary(),  # 不含详细行数
            "schema_summary": product.get_public_schema(),   # 字段名+类型，不含敏感配置
            "sandbox_modes": product.allowed_sandbox_modes,
            "min_sandbox_level": product.min_sandbox_level,
            "certifications": product.certifications,
            "pricing_model": product.pricing.model,
            "contact_space": self.local_space_id,
            "updated_at": product.updated_at.isoformat(),
            # 不包含：DEK 引用、内部策略配置、字段敏感度详情
        }
```

---

## 3. 跨空间 Trust Score 机制

```python
class CrossSpaceTrustEvaluator:
    """
    跨空间信任评分：基于历史合约履约记录动态调整互信级别
    影响：可访问的数据产品范围、审批流程（自动/人工）、会话并发上限
    """

    TRUST_LEVELS = {
        "PLATINUM": {"auto_approve_threshold": 100, "max_sessions": 50,
                     "accessible_sensitivity": 3},  # 可访问高敏感产品
        "GOLD":     {"auto_approve_threshold": 500,  "max_sessions": 20,
                     "accessible_sensitivity": 2},
        "SILVER":   {"auto_approve_threshold": float("inf"), "max_sessions": 5,
                     "accessible_sensitivity": 1},  # 仅低敏感产品
        "NEW":      {"auto_approve_threshold": float("inf"), "max_sessions": 1,
                     "accessible_sensitivity": 1},
    }

    def evaluate(self, space_id: str) -> TrustLevel:
        history = self._get_contract_history(space_id)
        if not history:
            return "NEW"

        # 违约率
        violation_rate = history.violated_count / max(history.total_count, 1)
        if violation_rate > 0.05:
            return "SILVER"  # 超过 5% 违约率，降级

        # 成功合约数
        if history.completed_count >= 50 and violation_rate < 0.01:
            return "PLATINUM"
        elif history.completed_count >= 10:
            return "GOLD"
        else:
            return "SILVER"

    def should_auto_approve(self, space_id: str, contract: Contract) -> bool:
        trust = self.evaluate(space_id)
        limits = self.TRUST_LEVELS[trust]
        # 合约金额（等价标准化值）超过阈值需要人工审批
        contract_value = contract.estimate_value()
        return contract_value < limits["auto_approve_threshold"]
```

---

## 4. 跨空间结果互信验证

买方在外部空间收到沙箱结果后，可以独立验真：

```python
class ResultVerifier:
    """
    买方侧独立结果验真
    不依赖数据空间平台，直接通过链上存证和 TEE 证明报告验证结果可信性
    """

    def verify_result(
        self,
        result: Any,
        result_signature: str,        # 沙箱节点 SM2 签名
        session_attestation: str,     # 沙箱 TEE 证明报告
        node_cert_pem: str,           # 沙箱节点 SM2 证书
        audit_event_id: str,          # 审计事件 ID
        merkle_proof: MerkleProof,    # Merkle 证明路径
    ) -> VerificationResult:

        # 1. 验证节点证书链（节点证书 → 数据空间 CA → 联盟根 CA）
        verify_cert_chain(node_cert_pem, trusted_space_ca, alliance_root_ca)

        # 2. 验证结果签名（确认结果由该 TEE 节点产生）
        result_hash = sm3_hash(serialize(result)).hex()
        verify_sm2_signature(result_signature, result_hash, load_pubkey(node_cert_pem))

        # 3. 验证 TEE 证明报告（Intel DCAP 或国产 TEE 证明服务）
        quote = base64_decode(session_attestation)
        attestation_ok = dcap_verifier.verify_quote(quote)

        # 4. 链上审计验真（Merkle 证明路径验证）
        leaf_bytes = canonical_encode(audit_event_id)
        chain_ok = SM3MerkleTree.verify_proof(
            leaf=leaf_bytes,
            proof=merkle_proof.proof_path,
            root=bytes.fromhex(merkle_proof.root_hash),
        )

        # 5. 链上根哈希核对（对比 FISCO BCOS 链上记录）
        on_chain_root = fisco_client.call_view(
            "AuditRegistry", "getBatchRoot",
            [merkle_proof.batch_id]
        )
        root_match = on_chain_root == merkle_proof.root_hash

        return VerificationResult(
            cert_valid=True,
            signature_valid=True,
            tee_attested=attestation_ok.valid,
            audit_chain_valid=chain_ok and root_match,
            overall_trusted=(attestation_ok.valid and chain_ok and root_match),
            attestation_mrenclave=attestation_ok.mrenclave,
        )
```

---

## 5. TLCP 国密通信配置

```python
class TLCPConnector:
    """
    跨空间通信使用 TLCP（国密 TLS，GM/T 0024）
    双证书体系：签名证书 + 加密证书
    """

    def create_tlcp_context(self, role: str = "client") -> ssl.SSLContext:
        """
        创建 TLCP SSL 上下文
        需要国密 SSL 库支持（如 GmSSL 或 OpenSSL 国密补丁版）
        """
        import gmssl_python as gmssl

        ctx = gmssl.TLCPContext(role=role)

        # 签名证书（SM2，用于身份认证）
        ctx.load_sign_cert(
            cert_file="/etc/cds/pki/connector-sign.pem",
            key_file="/etc/cds/pki/connector-sign-key.pem",
        )

        # 加密证书（SM2，用于密钥协商）
        ctx.load_enc_cert(
            cert_file="/etc/cds/pki/connector-enc.pem",
            key_file="/etc/cds/pki/connector-enc-key.pem",
        )

        # 信任的 CA 证书
        ctx.load_verify_locations("/etc/cds/pki/alliance-root-ca.pem")

        # 要求客户端证书（双向认证）
        ctx.verify_mode = gmssl.VERIFY_PEER | gmssl.VERIFY_FAIL_IF_NO_PEER_CERT

        # 国密套件（SM2-SM4-SM3）
        ctx.set_ciphers("ECC-SM4-SM3:ECC-SM4-GCM-SM3")

        return ctx

    def make_request(self, space_id: str, path: str, data: dict) -> dict:
        """跨空间 TLCP 请求"""
        endpoint = self.space_registry.get_connector_url(space_id)
        ctx = self.create_tlcp_context()

        with gmssl.HTTPSConnection(endpoint, context=ctx) as conn:
            conn.request("POST", path,
                         body=json.dumps(data),
                         headers={
                             "Content-Type": "application/json",
                             "X-Source-Space": self.local_space_id,
                             "X-Request-Id": generate_nonce(),
                             "X-Timestamp": str(int(time.time())),
                         })
            resp = conn.getresponse()
            return json.loads(resp.read())
```

---

## 6. 配置参数

```yaml
connector:
  identity:
    local_space_id: "space-finance-sh-001"
    sign_cert: "/etc/cds/pki/connector-sign.pem"
    sign_key: "/etc/cds/pki/connector-sign-key.pem"
    enc_cert: "/etc/cds/pki/connector-enc.pem"
    enc_key: "/etc/cds/pki/connector-enc-key.pem"
    root_ca: "/etc/cds/pki/alliance-root-ca.pem"

  federation:
    registry_url: "https://federation.cds-alliance.org.cn/v1"
    catalog_sync_interval_s: 300
    trusted_spaces_config: "/etc/cds/trusted_spaces.yaml"

  cross_space_policy:
    force_l1: true                         # 跨空间强制 TEE L1
    require_mutual_tls: true
    token_ttl_s: 3600
    max_sessions_per_space: 10
    auto_approve: false                    # 默认需要人工审批（新空间）

  trust_scoring:
    enabled: true
    platinum_completed_threshold: 50
    platinum_violation_rate_max: 0.01
    gold_completed_threshold: 10

  circuit_breaker:
    failure_threshold: 5
    recovery_timeout_s: 60
    half_open_max_calls: 3

  tlcp:
    cipher_suites: ["ECC-SM4-SM3", "ECC-SM4-GCM-SM3"]
    session_timeout_s: 3600
    renegotiation: disabled

  catalog:
    publish_on_update: true
    cache_ttl_s: 300
    max_cross_space_results: 100
```

---

*文档：SS-06、SS-08 | 版本：v1.0*
