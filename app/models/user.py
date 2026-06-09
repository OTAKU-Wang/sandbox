import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class UserRole:
    DATA_PROVIDER = "data_provider"  # 数商
    BUYER = "buyer"  # 买方
    OPERATOR = "operator"  # 运营方
    REGULATOR = "regulator"  # 监管方
    ADMIN = "admin"  # 系统管理员


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=UserRole.BUYER)
    organization: Mapped[str | None] = mapped_column(String(255))
    sm2_certificate: Mapped[str | None] = mapped_column(String(2048))  # PEM encoded SM2 cert
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.OPERATOR)

    # Relationships
    data_resources = relationship("DataResource", back_populates="provider")
    data_products = relationship("DataProduct", back_populates="provider")
