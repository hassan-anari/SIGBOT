from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, Enum, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chainura.domain.signalist import SignalistStatus
from chainura.persistence.base import Base
from chainura.persistence.types import UTCDateTime

SIGNALIST_STATUS_ENUM = Enum(
    SignalistStatus,
    name="signalist_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda enum: [member.value for member in enum],
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SignalistRecord(Base):
    __tablename__ = "signalists"
    __table_args__ = (
        CheckConstraint("telegram_user_id > 0", name="ck_signalists_telegram_user_id_positive"),
        UniqueConstraint("telegram_user_id", name="uq_signalists_telegram_user_id"),
        CheckConstraint("trim(display_name) <> ''", name="ck_signalists_display_name_nonempty"),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status <> 'archived' AND archived_at IS NULL)",
            name="ck_signalists_archive_consistency",
        ),
        Index("ix_signalists_status", "status"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SignalistStatus] = mapped_column(
        SIGNALIST_STATUS_ENUM, nullable=False, default=SignalistStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now,
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


# Explicit names are part of the persistence contract.
SignalistRecord.__table__.primary_key.name = "pk_signalists"
