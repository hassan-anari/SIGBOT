from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from chainura.domain.signalist import SignalistStatus
from chainura.persistence.base import Base

SIGNALIST_STATUS_ENUM = ENUM(
    SignalistStatus,
    name="signalist_status",
    native_enum=True,
    values_callable=lambda enum: [member.value for member in enum],
    create_type=False,
)


class SignalistRecord(Base):
    __tablename__ = "signalists"
    __table_args__ = (
        CheckConstraint("telegram_user_id > 0", name="ck_signalists_telegram_user_id_positive"),
        UniqueConstraint("telegram_user_id", name="uq_signalists_telegram_user_id"),
        CheckConstraint("btrim(display_name) <> ''", name="ck_signalists_display_name_nonempty"),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status <> 'archived' AND archived_at IS NULL)",
            name="ck_signalists_archive_consistency",
        ),
        Index("ix_signalists_status", "status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SignalistStatus] = mapped_column(
        SIGNALIST_STATUS_ENUM, nullable=False, server_default=text("'active'::signalist_status")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Explicit names are part of the persistence contract.
SignalistRecord.__table__.primary_key.name = "pk_signalists"
