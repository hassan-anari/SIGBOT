import asyncio
from unittest.mock import Mock
from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError,TelegramRetryAfter
from chainura.application.telegram_delivery import normalize_telegram_error

def method():return Mock()

def test_retry_after_is_safe_and_explicit():
    failure=normalize_telegram_error(TelegramRetryAfter(method=method(),message="private",retry_after=12))
    assert (failure.category,failure.retry_after,failure.definite_no_send)==("rate_limited",12.0,True)

def test_bad_request_categories_never_copy_payload():
    failure=normalize_telegram_error(TelegramBadRequest(method=method(),message="Bad Request: reply message not found 12345"))
    assert failure.category=="reply_parent_not_found"
    assert "12345" not in failure.category

def test_forbidden_is_definite_and_timeout_is_ambiguous():
    assert normalize_telegram_error(TelegramForbiddenError(method=method(),message="secret")).category=="insufficient_rights"
    assert not normalize_telegram_error(asyncio.TimeoutError()).definite_no_send
