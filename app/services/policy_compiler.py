"""Policy Compiler — compiles contract terms into OPA Rego policy bundles.

Supports 4 scenario-specific Rego templates (P1-1):
- structured_query: field ACL, row limits, DP budget
- llm_training: GPU quota, epoch limits, model output safety
- product_dev: output format restrictions, no raw data leakage
- structured_app: API rate limits, response streaming controls
"""
import json
import logging
from dataclasses import dataclass, field

from app.core.errors import CDSError
from app.utils.crypto import SM4Cipher, sm3_hash
from app._stubs.cube.cubebox.v1 import cubebox_pb2  # SS-10 CB-04: cube_network target

logger = logging.getLogger(__name__)


@dataclass
class PolicyBundle:
    contract_id: str
    rego_source: str
    rules: dict
    metadata: dict = field(default_factory=dict)
    sm3_hash: str | None = None
    encrypted_source: bytes | None = None


class PolicyCompiler:
    """Compiles contract terms into executable OPA Rego policies."""

    def compile(self, contract: dict) -> PolicyBundle:
        """Compile contract terms into a Rego policy bundle."""
        contract_id = contract.get("contract_id", "unknown")
        sandbox_levels = contract.get("allowed_sandbox_levels", ["L3"])
        operations = contract.get("allowed_operations", ["read"])
        max_duration = contract.get("max_duration_seconds", 3600)
        dp_epsilon = contract.get("dp_epsilon_budget")

        rego = self._generate_rego(contract_id, sandbox_levels, operations, max_duration, dp_epsilon)

        rules = {
            "sandbox_levels": sandbox_levels,
            "operations": operations,
            "max_duration": max_duration,
            "dp_epsilon": dp_epsilon,
            "status": contract.get("status", "draft"),
        }

        return PolicyBundle(
            contract_id=contract_id,
            rego_source=rego,
            rules=rules,
            sm3_hash=sm3_hash(rego.encode()),
            metadata={"version": "1.1", "compiler": "cds-policy-compiler"},
        )

    def compile_encrypted(self, contract: dict, cipher: SM4Cipher) -> PolicyBundle:
        """Compile and SM4-GCM encrypt the Rego source."""
        bundle = self.compile(contract)
        ciphertext, nonce, tag = cipher.encrypt_gcm(bundle.rego_source.encode())
        bundle.encrypted_source = nonce + tag + ciphertext
        return bundle

    def compile_for_mode(self, contract: dict, sandbox_mode: str) -> PolicyBundle:
        """Compile contract terms into a mode-specific Rego policy bundle (P1-1)."""
        contract_id = contract.get("contract_id", "unknown")
        mode = sandbox_mode.lower()

        if mode == "structured_query":
            rego = self._generate_query_rego(contract_id, contract)
        elif mode == "llm_training":
            rego = self._generate_training_rego(contract_id, contract)
        elif mode == "product_dev":
            rego = self._generate_product_dev_rego(contract_id, contract)
        elif mode == "structured_app":
            rego = self._generate_app_rego(contract_id, contract)
        else:
            rego = self._generate_rego(
                contract_id,
                contract.get("allowed_sandbox_levels", ["L3"]),
                contract.get("allowed_operations", ["read"]),
                contract.get("max_duration_seconds", 3600),
                contract.get("dp_epsilon_budget"),
            )

        rules = {
            "sandbox_mode": mode,
            "sandbox_levels": contract.get("allowed_sandbox_levels", ["L3"]),
            "operations": contract.get("allowed_operations", ["read"]),
            "max_duration": contract.get("max_duration_seconds", 3600),
            "dp_epsilon": contract.get("dp_epsilon_budget"),
            "status": contract.get("status", "draft"),
        }

        return PolicyBundle(
            contract_id=contract_id,
            rego_source=rego,
            rules=rules,
            sm3_hash=sm3_hash(rego.encode()),
            metadata={"version": "1.2", "compiler": "cds-policy-compiler", "mode": mode},
        )

    # ------------------------------------------------------------------
    # SS-10 CB-04 — cube_network target: policy package → CubeNetworkConfig
    # ------------------------------------------------------------------
    #
    # Spec: specs/ss-10-cube-dataplane-connector.md §1.4 铁律, §2.8, §4 CB-04.
    # Field names/types below were introspected from the CB-00 stubs
    # (app/_stubs/cube/cubebox/v1/cubebox_pb2, upstream 9b75d33) on 2026-10-13;
    # full mapping table: docs/cube-egress-mapping.md.

    # EgressRuleInject.format semantics — VERIFIED against the pinned upstream
    # (CubeEgress/lua/access_phase.lua:173 render_inject): `format` is a plain
    # string template and the literal placeholder is `${SECRET}`, replaced with
    # the raw secret at the node egress; a format WITHOUT `${SECRET}` is sent
    # verbatim (upstream logs a WARN). The spec sample `Bearer {{secret}}`
    # does NOT match the upstream placeholder syntax — this compiler emits
    # `Bearer ${SECRET}` instead (spec CB-04 步骤 2: "实施时核实 format 字段
    # 语义" — 核实结果见 docs/cube-egress-mapping.md).
    CUBE_INJECT_DEFAULT_HEADER = "Authorization"
    CUBE_INJECT_DEFAULT_FORMAT = "Bearer ${SECRET}"

    # Full-state invariant (spec §2.8: Create/Update 传全量；omitted field =
    # cleared upstream). Every compile_cube_network output MUST set all four
    # CubeNetworkConfig fields — there is no incremental output path.
    CUBE_NETWORK_FULL_STATE_FIELDS = ("allow_internet_access", "allow_out", "deny_out", "rules")

    def compile_cube_network(
        self,
        policy_package: dict,
        *,
        credentials: list[dict] | None = None,
    ) -> "cubebox_pb2.CubeNetworkConfig":
        """SS-10 CB-04: compile an SS-02 policy package into a FULL-STATE
        CubeNetworkConfig (the exact proto message the CB-01 adapter consumes
        at build_run_request / Update).

        Iron-rule compliance (spec §1.4 / §2.8):
        - ``allow_internet_access`` is ALWAYS False — a hardcoded default-deny
          baseline, never read from input (input trying to enable it is
          rejected; fail-closed).
        - Full-state: the output always carries CUBE_NETWORK_FULL_STATE_FIELDS
          (see _check_cube_network_full_state) — a compile after a rule REMOVAL
          simply no longer contains the rule (no incremental semantics).
        - ``rules[]`` preserves the policy package rule order — first-match-wins
          priority at the node is list order (stable, compile-time preserved).

        policy_package fields (field names follow the CDS network custody
        model — app/models/network_policy.py):
          ``allowed_ips``     list[str] — ip/cidr passthrough entries → allow_out
          ``allowed_domains`` list[str] — dns passthrough entries → allow_out
                                (CubeVS DNS A-record learning, spec §2.8)
          ``deny_out``        list[str] — explicit denies → deny_out
          ``rules``           list[dict] — contract-level L7 rules in priority
                                order: {name?, host|domain, path?,
                                method?|methods?, allow?=true, audit?}
                                Match fields AND-combine (upstream
                                rule_matches); first match wins.
          ``credentials``     list[dict] — same shape as the ``credentials``
                                kwarg (kwarg wins when both are given)

        credentials entries (app-layer custody → action.inject, spec §2.8
        凭据注入下沉): {credential_id: str, domain: str, secret: str,
        header: str = CUBE_INJECT_DEFAULT_HEADER,
        format: str = CUBE_INJECT_DEFAULT_FORMAT}. A credential attaches to
        the FIRST compiled rule whose match host equals its domain; if no such
        rule exists a synthetic allow rule ``inject-<credential_id>`` is
        appended AFTER the contract rules.

        Raises CDSError (fail-closed) on: input trying to enable internet
        access, a rule with no match constraint, or malformed credentials.
        """
        package = dict(policy_package or {})

        # 铁律: allow_internet_access = ALWAYS False. Input may never widen
        # the default-deny baseline.
        if package.get("allow_internet_access"):
            raise CDSError(
                "policy package must not set allow_internet_access=true — "
                "the L2' baseline is default-deny (allow_internet_access is "
                "hardcoded False by the compiler)",
                code="POLICY_COMPILE_ERROR",
                http_status=400,
                detail={"field": "allow_internet_access"},
            )

        allow_out = [str(e) for e in (package.get("allowed_ips") or [])]
        allow_out += [str(d) for d in (package.get("allowed_domains") or [])]
        deny_out = [str(e) for e in (package.get("deny_out") or [])]

        # rules[] — AND matching per rule, first-match-wins, order preserved
        # (stable: list append in package order; no re-sorting).
        rules: list[cubebox_pb2.EgressRule] = []
        for idx, raw in enumerate(package.get("rules") or []):
            host = raw.get("host") or raw.get("domain")
            path = raw.get("path")
            methods = raw.get("methods")
            if methods is None:
                methods = raw.get("method")
            if isinstance(methods, str):
                methods = [methods]
            if not (host or path or (methods or [])):
                raise CDSError(
                    f"egress rule #{idx + 1} has no match constraint (host/domain, "
                    "path, or methods) — an empty match would allow everything; "
                    "refusing to compile (fail-closed)",
                    code="POLICY_COMPILE_ERROR",
                    http_status=400,
                    detail={"rule_index": idx},
                )
            match = cubebox_pb2.EgressRuleMatch()
            if host:
                match.host = str(host)
            if path:
                match.path = str(path)
            for m in (methods or []):
                match.method.append(str(m))
            action = cubebox_pb2.EgressRuleAction(allow=bool(raw.get("allow", True)))
            if raw.get("audit"):
                action.audit = str(raw["audit"])
            rules.append(cubebox_pb2.EgressRule(
                name=str(raw.get("name") or f"rule-{idx + 1}"),
                match=match,
                action=action,
            ))

        # Credential injection (app_credentials custody → action.inject).
        creds = credentials if credentials is not None else (package.get("credentials") or [])
        skipped: list[str] = []
        for cred in creds or []:
            credential_id = str(cred.get("credential_id") or "").strip()
            domain = str(cred.get("domain") or "").strip()
            secret = cred.get("secret")
            if not credential_id or not domain or not secret:
                # Never emit an empty-secret inject entry; never fabricate a
                # value the custody layer cannot resolve (honest degradation —
                # the skip is disclosed, not silent).
                skipped.append(credential_id or "<unidentified>")
                logger.warning(
                    "cube_network compile: credential %r incomplete "
                    "(needs credential_id + domain + non-empty secret) — "
                    "inject entry skipped", credential_id or "<unidentified>",
                )
                continue
            inject = cubebox_pb2.EgressRuleInject(
                header=str(cred.get("header") or self.CUBE_INJECT_DEFAULT_HEADER),
                secret=str(secret),
                format=str(cred.get("format") or self.CUBE_INJECT_DEFAULT_FORMAT),
            )
            target = next(
                (r for r in rules if r.HasField("match") and r.match.host == domain),
                None,
            )
            if target is None:
                target = cubebox_pb2.EgressRule(
                    name=f"inject-{credential_id}",
                    match=cubebox_pb2.EgressRuleMatch(host=domain),
                    action=cubebox_pb2.EgressRuleAction(allow=True),
                )
                rules.append(target)  # synthetic rules come AFTER contract rules
            target.action.inject.append(inject)

        config = cubebox_pb2.CubeNetworkConfig(
            allow_internet_access=False,  # 铁律: hardcoded default-deny
            allow_out=allow_out,
            deny_out=deny_out,
            rules=rules,
        )
        # Full-state self-check (spec §2.8: omitted field = cleared upstream).
        self._check_cube_network_full_state(config)
        if skipped:
            logger.warning(
                "cube_network compile: %d credential inject entries skipped "
                "(unresolvable): %s", len(skipped), skipped,
            )
        return config

    def _check_cube_network_full_state(self, config: "cubebox_pb2.CubeNetworkConfig") -> None:
        """Full-state invariant self-check (spec §2.8, CB-04 步骤 2).

        The compiler's output must ALWAYS contain the complete
        CubeNetworkConfig field set — Create/Update are full-state
        replacements, so an omitted field is a silent clear upstream.
        Raises CDSError on proto drift or a missing mandatory presence.
        """
        desc_names = {f.name for f in cubebox_pb2.CubeNetworkConfig.DESCRIPTOR.fields}
        missing = [n for n in self.CUBE_NETWORK_FULL_STATE_FIELDS if n not in desc_names]
        if missing:
            raise CDSError(
                f"CubeNetworkConfig stub drift: fields {missing} no longer exist "
                "— the full-state field set (CUBE_NETWORK_FULL_STATE_FIELDS) "
                "must be re-derived from the pinned proto (CB-00)",
                code="INTERNAL_ERROR",
                http_status=500,
                detail={"missing_fields": missing},
            )
        # allow_internet_access is a presence field (proto3 oneof): it must be
        # EXPLICITLY set, and to the default-deny value.
        if not config.HasField("allow_internet_access"):
            raise CDSError(
                "full-state violation: allow_internet_access must be explicitly "
                "set on every compiled CubeNetworkConfig",
                code="INTERNAL_ERROR",
                http_status=500,
            )
        if config.allow_internet_access is not False:
            raise CDSError(
                "full-state violation: allow_internet_access must be False "
                "(default-deny baseline is never widened)",
                code="INTERNAL_ERROR",
                http_status=500,
            )

    def _generate_rego(self, contract_id: str, levels: list, operations: list, max_duration: int, dp_epsilon: float | None) -> str:
        """Generate generic OPA Rego source with AND-combined conditions."""
        levels_str = json.dumps(levels)
        ops_str = json.dumps(operations)
        dp_check = f"\n    input.dp_epsilon <= {dp_epsilon}" if dp_epsilon else ""

        return f"""package cds.policy.{contract_id.replace("-", "_")}

default allow = false

allow {{
    input.sandbox_level in {levels_str}
    input.operation in {ops_str}
    input.duration_seconds <= {max_duration}{dp_check}
}}"""

    def _generate_query_rego(self, contract_id: str, contract: dict) -> str:
        """Structured query mode: field ACL, row limits, DP budget enforcement."""
        levels = json.dumps(contract.get("allowed_sandbox_levels", ["L3"]))
        allowed_fields = json.dumps(contract.get("allowed_fields", ["*"]))
        max_rows = contract.get("max_output_rows", 10000)
        dp_epsilon = contract.get("dp_epsilon_budget")
        dp_check = f"\n    input.dp_epsilon_remaining >= {dp_epsilon}" if dp_epsilon else ""

        return f"""package cds.policy.{contract_id.replace("-", "_")}.query

default allow = false

allow {{
    input.sandbox_level in {levels}
    input.operation == "query"
    input.row_count <= {max_rows}
    field_allowed(input.field){dp_check}
}}

field_allowed(field) {{
    "*" in {allowed_fields}
}}

field_allowed(field) {{
    field in {allowed_fields}
}}"""

    def _generate_training_rego(self, contract_id: str, contract: dict) -> str:
        """LLM/model training mode: GPU quota, epoch limits, output format."""
        levels = json.dumps(contract.get("allowed_sandbox_levels", ["L1", "L2"]))
        max_gpu_hours = contract.get("max_gpu_hours", 100)
        max_epochs = contract.get("max_epochs", 100)
        allowed_formats = json.dumps(contract.get("allowed_output_formats", ["safetensors", "onnx"]))

        return f"""package cds.policy.{contract_id.replace("-", "_")}.training

default allow = false

allow {{
    input.sandbox_level in {levels}
    input.operation == "train"
    input.gpu_hours <= {max_gpu_hours}
    input.epochs <= {max_epochs}
    input.output_format in {allowed_formats}
}}

allow {{
    input.sandbox_level in {levels}
    input.operation == "evaluate"
    input.output_format in {allowed_formats}
}}"""

    def _generate_product_dev_rego(self, contract_id: str, contract: dict) -> str:
        """Product development mode: output format restrictions, no raw data."""
        levels = json.dumps(contract.get("allowed_sandbox_levels", ["L3"]))
        allowed_formats = json.dumps(contract.get("allowed_output_formats", ["zip", "tar.gz"]))

        return f"""package cds.policy.{contract_id.replace("-", "_")}.product_dev

default allow = false

allow {{
    input.sandbox_level in {levels}
    input.operation in ["build", "package", "test"]
    input.output_format in {allowed_formats}
    not input.contains_raw_data
}}"""

    def _generate_app_rego(self, contract_id: str, contract: dict) -> str:
        """Application mode: API rate limits, response size limits."""
        levels = json.dumps(contract.get("allowed_sandbox_levels", ["L3"]))
        max_rps = contract.get("max_requests_per_second", 100)
        max_response_mb = contract.get("max_response_mb", 10)

        return f"""package cds.policy.{contract_id.replace("-", "_")}.app

default allow = false

allow {{
    input.sandbox_level in {levels}
    input.operation == "api_call"
    input.requests_per_second <= {max_rps}
    input.response_size_mb <= {max_response_mb}
}}

allow {{
    input.sandbox_level in {levels}
    input.operation == "serve"
    input.requests_per_second <= {max_rps}
}}"""

    def field_rules_from_classifications(self, classifications: dict | None) -> dict:
        """Gap A5/T7: map field-level classifications to output policy rules.

        Level semantics (1=public, 2=internal, 3=confidential, 4=secret):
        - level >= 4 → deny_out (field must never leave the sandbox)
        - level == 3 → mask (field is force-masked on any output)
        - level <= 2 → no restriction

        Returns {"mask_fields": [...], "deny_out_fields": [...]}.
        """
        if not classifications:
            return {"mask_fields": [], "deny_out_fields": []}
        mask_fields: list[str] = []
        deny_out_fields: list[str] = []
        for field, raw_level in classifications.items():
            try:
                level = int(raw_level)
            except (TypeError, ValueError):
                continue
            if level >= 4:
                deny_out_fields.append(str(field))
            elif level == 3:
                mask_fields.append(str(field))
        return {"mask_fields": mask_fields, "deny_out_fields": deny_out_fields}

    def verify_integrity(self, bundle: PolicyBundle) -> bool:
        """Verify the SM3 hash of a policy bundle matches its rego_source."""
        if not bundle.sm3_hash or not bundle.rego_source:
            return False
        return sm3_hash(bundle.rego_source.encode()) == bundle.sm3_hash

    def decrypt_source(self, encrypted_source: bytes, cipher: SM4Cipher) -> str:
        """Decrypt an encrypted policy bundle back to Rego source."""
        nonce = encrypted_source[:12]
        tag = encrypted_source[12:28]
        ciphertext = encrypted_source[28:]
        return cipher.decrypt_gcm(ciphertext, nonce, tag).decode()


class PolicyEvaluator:
    """Runtime policy evaluation (PDP — Policy Decision Point)."""

    def evaluate(self, policy: PolicyBundle, request: dict) -> dict:
        """Evaluate a request against a policy bundle.
        Returns {allowed: bool, reason: str}.
        """
        rules = policy.rules

        if rules.get("status") not in ("active", "signed"):
            return {"allowed": False, "reason": "Contract not active"}

        level = request.get("sandbox_level", "L3")
        if level not in rules.get("sandbox_levels", []):
            return {"allowed": False, "reason": f"Sandbox level {level} not permitted"}

        operation = request.get("operation", "read")
        if operation not in rules.get("operations", []):
            return {"allowed": False, "reason": f"Operation {operation} not permitted"}

        duration = request.get("duration_seconds", 0)
        if duration > rules.get("max_duration", 3600):
            return {"allowed": False, "reason": "Duration exceeds limit"}

        dp_epsilon = rules.get("dp_epsilon")
        if dp_epsilon is not None:
            requested_dp = request.get("dp_epsilon", 0)
            if requested_dp > dp_epsilon:
                return {"allowed": False, "reason": "DP epsilon budget exceeded"}

        return {"allowed": True, "reason": "All checks passed"}


policy_compiler = PolicyCompiler()
policy_evaluator = PolicyEvaluator()


def compile_cube_network(
    policy_package: dict,
    *,
    credentials: list[dict] | None = None,
) -> "cubebox_pb2.CubeNetworkConfig":
    """Module-level entry (SS-10 CB-04): policy package → full-state
    CubeNetworkConfig. See PolicyCompiler.compile_cube_network."""
    return policy_compiler.compile_cube_network(policy_package, credentials=credentials)


def verify_cube_network_full_state(config: "cubebox_pb2.CubeNetworkConfig") -> None:
    """Public full-state self-check (SS-10 CB-04, spec §2.8). Raises CDSError
    unless `config` carries the complete CubeNetworkConfig field set with
    allow_internet_access explicitly False."""
    policy_compiler._check_cube_network_full_state(config)
