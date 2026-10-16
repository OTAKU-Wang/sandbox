"""KMS volume-key wire schemas — the EXACT JSON shapes exchanged with the Go
volume plugin (cds-volume-plugin/kms.go). Field names are machine-consumed wire
values; do NOT rename without updating the Go client in lockstep.

Go contract (SS-10 CB-03 step 2/4; see cds-volume-plugin/README.md "KMS 契约"):
  Register:   POST   /api/v1/kms/volume-keys                    {volume_id, name, dek_hex} -> {key_id}
  Distribute: POST   /api/v1/kms/volume-keys/{ref}/distribute   {session_id}              -> {key_hex}
  Revoke:     DELETE /api/v1/kms/volume-keys/{ref}                          -> 204 (no body)
"""
from pydantic import BaseModel


class VolumeKeyRegisterRequest(BaseModel):
    """Register a plugin-generated volume DEK.

    The DEK is created by the trusted plugin (in the enclave) and delivered
    here as hex; the control plane wraps it and stores only the wrapped ref.

    `cipher` is what the plugin actually sealed the volume with — it declares
    its own backend (`CDS_SM4_AEAD_BACKEND`). Omitting it records `unknown`
    rather than a guessed SM4-GCM, because the compliance record must not
    claim GM crypto for a volume the plugin sealed with the AES fallback.
    """
    volume_id: str
    name: str
    dek_hex: str
    cipher: str | None = None


class VolumeKeyRegisterResponse(BaseModel):
    """Only the wrapped-key ref is returned — never raw DEK material."""
    key_id: str


class VolumeKeyDistributeRequest(BaseModel):
    """Distribute (unwrap) a volume DEK for a session-scoped attach."""
    session_id: str


class VolumeKeyDistributeResponse(BaseModel):
    """Plaintext DEK as hex. The Go client hex-decodes it and checks the length
    against its dekLen (16 bytes) before use — fail-closed on mismatch."""
    key_hex: str
