"""N6: RAG generative answer model — ONNX bundle (model.onnx + vocab.txt) zipped.

The bundle is uploaded by a provider, validated (inner onnx via onnx.checker),
stored encrypted in storage_service, and materialized into the buyer's sandbox
workspace at query time when ``answer_mode="generative"``. The runner loads it
with onnxruntime (same loader as N5) and decodes the argmax token over the
vocab — honest conditional answer synthesis, fail-closed without the bundle.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RAGGenerativeModel(Base):
    __tablename__ = "rag_generative_models"

    model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    vocab_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
