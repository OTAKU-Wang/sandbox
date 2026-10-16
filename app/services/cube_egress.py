"""SS-10 CB-04 — L2' egress hot-swap + CA-bake probe hook.

Two responsibilities (spec ss-10-cube-dataplane-connector.md §4 CB-04):

1. **Hot-swap (步骤 2)**: contract change / mid-session permission
   adjustment → ``Update(CubeNetworkConfig)`` full-state push. The existing
   contract-update flow (network policy (re)application — the
   ``network_policy_engine.remove_policy() + create_policy()`` pair used by
   ``PUT /sessions/{id}/network-policy`` and ``PATCH /network-policies/{id}``)
   routes l2c sessions through :func:`apply_network_policy_to_cube`, which
   compiles a fresh FULL-STATE ``CubeNetworkConfig``
   (``policy_compiler.compile_cube_network``) and pushes it to the node
   cubelet. Full-state semantics (spec §2.8): omitted field = cleared
   upstream — the compiler never emits incremental state.

2. **CA bake (步骤 4)**: the CubeEgress root CA must be baked into CDS
   scenario images (``Dockerfile.cube-sandbox``, build-arg
   ``CUBE_EGRESS_CA``). :func:`check_cube_egress_ca_baked` is the documented
   probe hook that CB-05's template build calls; a missing CA fails the
   template build with an honest error.

Credential injection (步骤 1 / spec §2.8 凭据注入下沉): app-layer custody
(``app_credentials`` — hash-only bindings, custody record) + contract
``terms["credentials"]`` (the control-plane-held secret values) →
``action.inject`` entries. A credential whose plaintext the custody layer
cannot resolve is SKIPPED and disclosed (never fabricated — 禁伪造).
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session
from app.core.errors import CDSError
from app.models.app_credential import AppCredential, CredentialStatus
from app.models.contract import Contract
from app.models.sandbox_node import SandboxNode
from app.models.sandbox_session import SandboxSession
from app.services import egress_audit
from app.services.network_policy import NetworkPolicyConfig
from app.services.policy_compiler import compile_cube_network

logger = logging.getLogger(__name__)

# Upstream CubeEgress root CA location on a node (verified 2026-10-13 against
# the pinned upstream: CubeEgress/nginx.conf ``ca_cert_path``,
# CubeEgress/start.sh ``CA_DIR``). The CA ARTIFACT for image builds is a
# CDS-side copy of this node file (CB-02 bring-up output).
CUBE_EGRESS_CA_NODE_PATH = "/etc/cube/ca/cube-root-ca.crt"


def check_cube_egress_ca_baked(ca_path: str | None = None) -> dict:
    """CA-presence check — the documented probe hook for CB-05's template build.

    Spec §4 CB-04 步骤 4: 模板 probe 阶段校验 CA 在场（缺失 → 模板构建失败，
    诚实错误）. Resolution order: explicit ``ca_path`` argument >
    ``CDS_CUBE_EGRESS_CA`` setting > :data:`CUBE_EGRESS_CA_NODE_PATH`.

    Returns ``{"ca_path", "sha256", "size_bytes"}`` on success. Raises
    CDSError (honest, actionable message) when the CA is missing, empty, or
    unreadable — CB-05 must treat the raise as a template-build failure.
    """
    path = ca_path or getattr(get_settings(), "CUBE_EGRESS_CA", None) or CUBE_EGRESS_CA_NODE_PATH
    if not os.path.isfile(path):
        raise CDSError(
            f"CubeEgress root CA not found at {path!r} — the CA artifact from "
            f"CB-02 node bring-up (node path {CUBE_EGRESS_CA_NODE_PATH}) must be "
            "baked into the scenario image (Dockerfile.cube-sandbox build-arg "
            "CUBE_EGRESS_CA) / verified at template probe. Template build "
            "cannot proceed without it (honest failure, no silent skip).",
            code="RUNTIME_UNAVAILABLE",
            http_status=409,
            detail={"ca_path": path, "node_source": CUBE_EGRESS_CA_NODE_PATH},
        )
    if os.path.getsize(path) == 0:
        raise CDSError(
            f"CubeEgress root CA at {path!r} is empty — refusing to treat it "
            "as present (template probe).",
            code="RUNTIME_UNAVAILABLE",
            http_status=409,
            detail={"ca_path": path},
        )
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise CDSError(
            f"CubeEgress root CA at {path!r} is unreadable: {exc}",
            code="RUNTIME_UNAVAILABLE",
            http_status=409,
            detail={"ca_path": path},
        ) from exc
    return {
        "ca_path": path,
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


async def load_policy_package(session: SandboxSession, config: NetworkPolicyConfig) -> dict:
    """Assemble the session's SS-02 policy package for the cube_network target.

    Sources (see docs/cube-egress-mapping.md):
    - ip/cidr + dns whitelist: the session ``NetworkPolicy`` custody
      (``allowed_ips`` / ``allowed_domains`` — passed in via ``config``).
    - contract-level L7 rules: ``contracts.terms["egress_rules"]``.
    - credential injection: active ``app_credentials`` bindings (custody
      record, hash-only) joined with ``contracts.terms["credentials"]``
      (control-plane-held secret values). Unresolvable secrets are skipped
      and disclosed in ``skipped_credentials`` — never fabricated.
    """
    package: dict = {
        "contract_id": session.contract_id,
        "allowed_ips": list(config.allowed_ips or []),
        "allowed_domains": list(config.allowed_domains or []),
        "deny_out": list(config.deny_out or []),
        "rules": [],
        "credentials": [],
        "skipped_credentials": [],
    }
    if not session.contract_id:
        return package

    contract = None
    try:
        async with async_session() as db:
            contract_id = uuid.UUID(str(session.contract_id))
            row = (await db.execute(
                select(Contract).where(Contract.id == contract_id)
            )).scalar_one_or_none()
            if row is not None:
                terms = row.terms or {}
                package["rules"] = list(terms.get("egress_rules") or [])
                terms_credentials = list(terms.get("credentials") or [])

                creds = (await db.execute(
                    select(AppCredential).where(
                        AppCredential.contract_id == contract_id,
                        AppCredential.status == CredentialStatus.ACTIVE.value,
                        AppCredential.expires_at.is_(None),
                    )
                )).scalars().all()
            else:
                terms_credentials = []
                creds = []
    except Exception as exc:  # noqa: BLE001 — custody lookup is best-effort;
        # a DB hiccup must not take the egress push down, and the push then
        # carries whitelist-only state (disclosed in audit detail).
        logger.warning("CB-04: contract/credential lookup failed for %s: %s",
                       session.id, exc)
        return package

    for cred in creds:
        value = next(
            (t for t in terms_credentials
             if t.get("credential_id") == cred.app_id or t.get("app_id") == cred.app_id),
            None,
        ) or {}
        secret = value.get("secret")
        domain = value.get("domain")
        if not secret or not domain:
            # Hash-only custody: the control plane holds no plaintext for this
            # binding — skip the inject entry and disclose (禁伪造: never
            # fabricate a credential value).
            package["skipped_credentials"].append(cred.app_id)
            logger.warning(
                "CB-04: no resolvable plaintext secret for credential %s "
                "(contract %s) — inject entry skipped, disclosed in audit",
                cred.app_id, session.contract_id,
            )
            continue
        package["credentials"].append({
            "credential_id": cred.app_id,
            "domain": str(domain),
            "secret": str(secret),
            "header": value.get("header"),
            "format": value.get("format"),
        })
    return package


async def apply_network_policy_to_cube(
    session_id: str,
    config: NetworkPolicyConfig,
    *,
    default_deny: bool = False,
    db=None,
) -> dict:
    """Push the compiled full-state CubeNetworkConfig to the session's l2c sandbox.

    The hot-swap entry point (spec §4 CB-04 步骤 2): called by
    ``network_policy_engine.create_policy`` / ``remove_policy`` for l2c
    sessions. No-ops (reason ``not_l2c``) for every other level — L0/L3 keep
    the host netns/iptables path untouched.

    ``default_deny=True`` pushes the zero-trust baseline (policy removed ⇒
    deny all), used by the remove path.

    ``db`` is the caller's request session — the engine hot-swap branch
    always passes it, so the session/node lookup runs on the same session
    as the rest of the request (never on a second session: on the shared
    in-memory test connection that would wipe the caller's uncommitted
    rows; on a pooled connection it would miss the in-flight row).
    When ``db`` is None (standalone use outside request scope) this
    function opens its own session — the only second-session path left in
    this module.
    """
    if db is None:
        async with async_session() as own_db:
            return await _apply_network_policy_to_cube(
                own_db, session_id, config, default_deny=default_deny
            )
    return await _apply_network_policy_to_cube(
        db, session_id, config, default_deny=default_deny
    )


async def _apply_network_policy_to_cube(
    db, session_id: str, config: NetworkPolicyConfig, *, default_deny: bool = False
) -> dict:
    from app.services import cube_node_client, cube_runtime_adapter

    try:
        session_uuid = uuid.UUID(str(session_id))
    except ValueError:
        return {"applied": False, "reason": "invalid_session_id"}
    session = (await db.execute(
        select(SandboxSession).where(SandboxSession.id == session_uuid)
    )).scalar_one_or_none()
    if session is None:
        return {"applied": False, "reason": "session_not_found"}
    if session.sandbox_level != "l2c":
        return {"applied": False, "reason": "not_l2c"}
    container_id = str(session.container_id or "")
    if not container_id.startswith("l2c-"):
        return {"applied": False, "reason": "no_l2c_container"}

    if default_deny:
        package = {"allowed_ips": [], "allowed_domains": [], "rules": [],
                   "credentials": [], "skipped_credentials": []}
    else:
        package = await load_policy_package(session, config)

    cube_config = compile_cube_network(package)

    node_id, sandbox_id = cube_runtime_adapter.CubeRuntimeAdapter._parse_container_id(container_id)
    node = (await db.execute(
        select(SandboxNode).where(SandboxNode.node_id == node_id)
    )).scalar_one_or_none()
    if node is None or not cube_node_client.node_cubelet_endpoint(node):
        raise CDSError(
            f"cube node {node_id} (from container {container_id!r}) has no "
            "cubelet endpoint — cannot apply egress policy",
            code="RUNTIME_UNAVAILABLE",
            http_status=503,
            detail={"node_id": node_id, "session_id": str(session_id)},
        )

    client = cube_node_client.get_cube_node_client(node)
    from app._stubs.cube.cubebox.v1 import cubebox_pb2

    # SS-14 RT-07 (2026-10-14): 上游 Update 强制 annotations 携带 update action
    # （update.go:67 实证 —— 无 annotations = 130483 "must provide Annotations"）;
    # 网络策略热换 = "network"。
    request = cubebox_pb2.UpdateCubeSandboxRequest(
        sandboxID=sandbox_id,
        cube_network_config=cube_config,
    )
    request.annotations["cube.master.update_action"] = "network"
    try:
        client.update(request)
    except CDSError as exc:
        # 沙箱已不存在的指纹: ContainerNotFound 映射, 或上游 Update 对已销毁
        # cubebox 的 Get 失败 —— cubelet update.go:94 把 store 查找错误原样
        # 填入 ret_msg（"key not found", InvalidParamFormat 130483）。
        # 沙箱已消失 = 策略推送无对象, 非节点异常（不得记 security_event）。
        if exc.code == "SANDBOX_NOT_FOUND" or "key not found" in str(exc):
            egress_audit.log_egress_event(
                session_id=str(session_id), event_type="access",
                verdict="policy_applied", rule_id="cube_hotswap",
                detail={"note": "sandbox already gone — Update no-op"},
            )
            return {"applied": False, "reason": "sandbox_gone"}
        egress_audit.log_egress_event(
            session_id=str(session_id), event_type="security_event",
            verdict="deny", rule_id="cube_hotswap",
            detail={"error": str(exc), "code": exc.code,
                    "note": "egress policy Update failed — node state unknown"},
        )
        raise

    egress_audit.log_egress_event(
        session_id=str(session_id), event_type="access",
        verdict="policy_applied", rule_id="cube_hotswap",
        detail={
            "mode": config.mode,
            "default_deny": default_deny,
            "allow_out": len(cube_config.allow_out),
            "rules": len(cube_config.rules),
            "inject": sum(len(r.action.inject) for r in cube_config.rules),
            "skipped_credentials": package.get("skipped_credentials", []),
        },
    )
    return {
        "applied": True,
        "node_id": node_id,
        "sandbox_id": sandbox_id,
        "allow_out": len(cube_config.allow_out),
        "rules": len(cube_config.rules),
    }
