"""Cube template registry (SS-10 CB-05, spec §2.5 / §4 CB-05 step 1).

A cube template is a CDS-built rootfs ext4 artifact (converted from a CDS
scenario image on the independent build machine — 红线 2) registered in the
control plane and distributed to cube nodes via the Images gRPC service
(``CreateImage`` with the ``cube.master.rootfs.artifact.*`` annotations,
upstream ``Cubelet/pkg/constants/const.go:242-247``).

``cube_templates`` is the template row (registry + build metadata, incl. the
probe config that carries the C4 "HTTP 2xx = READY" semantics);
``cube_template_nodes`` is the per-node distribution row (PENDING /
DISTRIBUTING / READY / FAILED, sha verification result, last error).
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CubeTemplateStatus(str, Enum):
    """Template lifecycle: built → distributing → READY (at least one node READY)."""

    BUILDING = "BUILDING"
    DISTRIBUTING = "DISTRIBUTING"
    READY = "READY"


class CubeTemplateNodeStatus(str, Enum):
    """Per-node distribution state machine.

    PENDING → DISTRIBUTING → READY; any of {PENDING, DISTRIBUTING} may go to
    FAILED (CreateImage error / sha mismatch / download failure); FAILED may
    be retried back into DISTRIBUTING (re-issued presigned URL + token).
    READY is terminal per node (only GC/delete clears it).
    """

    PENDING = "PENDING"
    DISTRIBUTING = "DISTRIBUTING"
    READY = "READY"
    FAILED = "FAILED"


class CubeTemplate(Base):
    """A registered cube template artifact (ext4 rootfs in CDS MinIO)."""

    __tablename__ = "cube_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_cube_templates_name_version"),
        Index("ix_cube_templates_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    # Node-side artifact id (e.g. "rfs-<uuid>"): the value that goes into
    # ImageSpec.image + the cube.master.rootfs.artifact.id annotation.
    artifact_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    # Integrity of the MinIO object (computed at upload; nodes re-verify the
    # same value from the artifact.sha256 annotation at download).
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # The CDS scenario image this artifact was built from (registry ref).
    image_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CubeTemplateStatus.BUILDING.value)
    # Probe config that travels with the registry row (spec CB-05 step 2:
    # 就绪语义对齐 C4 — HTTP probe 2xx = READY). Shape:
    # {"type": "http", "port": 80, "path": "/", "expected_status": "2xx"}
    probe_config: Mapped[dict | None] = mapped_column(JSON)
    # Build/distribution log tail for the UI (not the audit trail — that is
    # the audit_service action cube.template.*).
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    nodes: Mapped[list["CubeTemplateNode"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class CubeTemplateNode(Base):
    """Per-node distribution state for a template artifact."""

    __tablename__ = "cube_template_nodes"
    __table_args__ = (
        Index("ix_cube_template_nodes_node_status", "node_id", "status"),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cube_templates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Matches SandboxNode.node_id (String(64), unique) — nodes are not FK'd so
    # node rows survive node deletion (W14 chain) and GC can still reconcile.
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CubeTemplateNodeStatus.PENDING.value)
    # The sha256 the node reported/verified for this artifact (== template
    # sha256 when READY; a mismatch drives FAILED + critical alert, 不降级).
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    template: Mapped["CubeTemplate"] = relationship(back_populates="nodes")
