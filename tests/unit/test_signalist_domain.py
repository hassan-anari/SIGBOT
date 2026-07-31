from __future__ import annotations

import unittest
from datetime import datetime, timezone

from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.errors import InvalidSignalistInputError, InvalidSignalistStatusTransitionError


def persisted(status: SignalistStatus = SignalistStatus.ACTIVE) -> Signalist:
    now = datetime.now(timezone.utc)
    return Signalist.rehydrate(id=1, telegram_user_id=10, display_name=" Name ",
        telegram_username=None, status=status, created_at=now, updated_at=now,
        archived_at=now if status is SignalistStatus.ARCHIVED else None)


class SignalistDomainTests(unittest.TestCase):
    def test_create_validates_and_starts_active(self) -> None:
        row = Signalist.create(1, " Name ")
        self.assertEqual((row.display_name, row.status, row.id), ("Name", SignalistStatus.ACTIVE, None))

    def test_invalid_identity_and_name(self) -> None:
        for telegram_id in (0, -1, 2**63, True, "1"):
            with self.subTest(telegram_id=telegram_id), self.assertRaises(InvalidSignalistInputError):
                Signalist.create(telegram_id, "Name")  # type: ignore[arg-type]
        for name in ("", "   "):
            with self.assertRaises(InvalidSignalistInputError): Signalist.create(1, name)

    def test_all_allowed_transitions_are_pure(self) -> None:
        cases = [(SignalistStatus.ACTIVE, SignalistStatus.INACTIVE),
                 (SignalistStatus.INACTIVE, SignalistStatus.ACTIVE),
                 (SignalistStatus.ACTIVE, SignalistStatus.ARCHIVED),
                 (SignalistStatus.INACTIVE, SignalistStatus.ARCHIVED)]
        for source, target in cases:
            row = persisted(source)
            with self.subTest(source=source, target=target):
                self.assertIs(row.approve_transition(target), target)
                self.assertIs(row.status, source)
                self.assertIsNone(row.archived_at)

    def test_all_rejected_transitions(self) -> None:
        cases = [(SignalistStatus.ACTIVE, SignalistStatus.ACTIVE),
                 (SignalistStatus.INACTIVE, SignalistStatus.INACTIVE),
                 (SignalistStatus.ARCHIVED, SignalistStatus.ARCHIVED),
                 (SignalistStatus.ARCHIVED, SignalistStatus.ACTIVE),
                 (SignalistStatus.ARCHIVED, SignalistStatus.INACTIVE)]
        for source, target in cases:
            with self.subTest(source=source, target=target), self.assertRaises(InvalidSignalistStatusTransitionError):
                persisted(source).approve_transition(target)

    def test_archive_consistency_and_aware_timestamps(self) -> None:
        now = datetime.now(timezone.utc)
        with self.assertRaises(InvalidSignalistInputError):
            Signalist.rehydrate(id=1, telegram_user_id=1, display_name="x", telegram_username=None,
                status=SignalistStatus.ARCHIVED, created_at=now, updated_at=now, archived_at=None)
        with self.assertRaises(InvalidSignalistInputError):
            Signalist.rehydrate(id=1, telegram_user_id=1, display_name="x", telegram_username=None,
                status=SignalistStatus.ACTIVE, created_at=now.replace(tzinfo=None), updated_at=now, archived_at=None)

    def test_no_hard_delete_or_restore(self) -> None:
        self.assertFalse(hasattr(Signalist, "delete"))
        self.assertFalse(hasattr(Signalist, "restore"))
