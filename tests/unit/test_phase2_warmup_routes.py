import unittest
from unittest.mock import AsyncMock

from chainura.application.access import Actor, ActorKind
from chainura.domain.signalist import Signalist
from chainura.telegram.presentation import texts
from chainura.telegram.warmup_routes import warmup


class Message:
    def __init__(self): self.answer = AsyncMock()


class Service:
    def __init__(self, planned): self.planned = planned; self.trigger = AsyncMock(return_value=planned)


class WarmupRouterTests(unittest.IsolatedAsyncioTestCase):
    def actor(self):
        signalist = Signalist.create(123, "نام محرمانه")
        object.__setattr__(signalist, "id", 9)
        return Actor(ActorKind.ACTIVE_SIGNALIST, signalist)

    async def test_every_planning_outcome_has_identical_private_acknowledgement(self):
        forbidden = ("Secret Destination", "9", "-100123", "eligible", "skipped",
                     "3600", "cooldown", "failed")
        answers = []
        for planned in (1, 5, 0, 0):  # one, many, all cooling down, no eligible destinations
            message = Message(); service = Service(planned)
            await warmup(message, self.actor(), service)
            service.trigger.assert_awaited_once_with(9)
            answer = message.answer.await_args.args[0]; answers.append(answer)
            self.assertEqual(answer, texts.WARMUP_ACCEPTED)
            for secret in forbidden: self.assertNotIn(secret, answer)
        self.assertEqual(len(set(answers)), 1)

    async def test_inactive_archived_and_unknown_are_denied_without_planning(self):
        expected = {ActorKind.INACTIVE_SIGNALIST: texts.INACTIVE,
                    ActorKind.ARCHIVED_SIGNALIST: texts.ARCHIVED,
                    ActorKind.UNKNOWN: texts.UNKNOWN}
        for kind, denial in expected.items():
            message = Message(); service = Service(1)
            await warmup(message, Actor(kind), service)
            service.trigger.assert_not_awaited()
            self.assertEqual(message.answer.await_args.args[0], denial)
