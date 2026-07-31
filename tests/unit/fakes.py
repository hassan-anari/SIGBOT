from __future__ import annotations

from datetime import datetime, timezone

from chainura.domain.signalist import Signalist, SignalistStatus


class FakeRepository:
    def __init__(self) -> None:
        self.rows: dict[int, Signalist] = {}
        self.fail: Exception | None = None

    async def add(self, signalist: Signalist) -> Signalist:
        if self.fail: raise self.fail
        now = datetime.now(timezone.utc)
        row = Signalist.rehydrate(id=max(self.rows, default=0) + 1,
            telegram_user_id=signalist.telegram_user_id, display_name=signalist.display_name,
            telegram_username=signalist.telegram_username, status=SignalistStatus.ACTIVE,
            created_at=now, updated_at=now, archived_at=None)
        self.rows[row.id] = row  # type: ignore[index]
        return row

    async def get_by_id(self, signalist_id: int) -> Signalist | None:
        if self.fail: raise self.fail
        return self.rows.get(signalist_id)

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Signalist | None:
        if self.fail: raise self.fail
        return next((row for row in self.rows.values() if row.telegram_user_id == telegram_user_id), None)

    async def list(self, status: SignalistStatus | None = None) -> list[Signalist]:
        return [row for row in self.rows.values() if status is None or row.status is status]

    async def persist_status(self, signalist_id: int, target: SignalistStatus) -> Signalist:
        if self.fail: raise self.fail
        old = self.rows[signalist_id]
        now = datetime.now(timezone.utc)
        row = Signalist.rehydrate(id=old.id, telegram_user_id=old.telegram_user_id,
            display_name=old.display_name, telegram_username=old.telegram_username,
            status=target, created_at=old.created_at, updated_at=now,
            archived_at=now if target is SignalistStatus.ARCHIVED else None)  # type: ignore[arg-type]
        self.rows[signalist_id] = row
        return row


class FakeTransaction:
    def __init__(self, repository: FakeRepository, *, fail_commit: bool = False) -> None:
        self.repository = repository
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def __aenter__(self) -> "FakeTransaction": return self
    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit: raise RuntimeError("commit failed")
    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc is not None: self.rollbacks += 1
        self.closed = True


class FakeTransactionFactory:
    def __init__(self, repository: FakeRepository, *, fail_commit: bool = False) -> None:
        self.repository = repository
        self.fail_commit = fail_commit
        self.created: list[FakeTransaction] = []
    def __call__(self) -> FakeTransaction:
        transaction = FakeTransaction(self.repository, fail_commit=self.fail_commit)
        self.created.append(transaction)
        return transaction
