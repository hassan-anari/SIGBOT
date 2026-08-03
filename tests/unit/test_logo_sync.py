from __future__ import annotations

import asyncio
import io
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from chainura.domain.destination import Exchange
from chainura.rendering.logos import (CoinLogoStore, LogoSyncSummary,
    ToobitLogoSynchronizer, _read_bounded_body, normalize_symbol, run_logo_sync)
from chainura.telegram.runtime import Runtime, RuntimeState


def image_bytes(format: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), (20, 40, 60)).save(output, format)
    return output.getvalue()


class _Content:
    def __init__(self, data):
        self.chunks = list(data) if isinstance(data, _Fragmented) else [data]
        self.offset = 0
    async def read(self, limit):
        if not self.chunks: return b""
        chunk = self.chunks.pop(0)
        if isinstance(chunk, BaseException): raise chunk
        if len(chunk) > limit:
            self.chunks.insert(0, chunk[limit:])
            chunk = chunk[:limit]
        self.offset += len(chunk)
        return chunk


class _Fragmented:
    def __init__(self, *chunks, content_length=None):
        self.chunks = chunks
        self.content_length = content_length
    def __iter__(self): return iter(self.chunks)


class _Response:
    def __init__(self, *, document=None, data=b"", url="https://images.test/logo"):
        self.document, self.content, self.url = document, _Content(data), url
        self.content_length = (data.content_length if isinstance(data, _Fragmented)
                               else len(data) if data else None)
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def raise_for_status(self): pass
    async def json(self, **kwargs): return self.document


class _Session:
    def __init__(self, document, images): self.document, self.images = document, images
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def get(self, url):
        if "futures?" in url: return _Response(document=self.document)
        value = self.images[url]
        if isinstance(value, Exception): raise value
        if isinstance(value, tuple):
            data, final_url = value
            return _Response(data=data, url=final_url)
        return _Response(data=value, url=url)


class LogoSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CoinLogoStore(self.root)

    async def asyncTearDown(self): self.temp.cleanup()

    def factory(self, document, images):
        return lambda **kwargs: _Session(document, images)

    async def test_bounded_reader_consumes_fragmented_body_through_eof(self):
        content = _Content(_Fragmented(b"first", b"-", b"complete"))
        self.assertEqual(await _read_bounded_body(content, 100), b"first-complete")
        self.assertGreaterEqual(content.offset, len(b"first-complete"))

    async def test_valid_fragmented_png_with_short_first_chunk_is_stored(self):
        data = image_bytes("PNG")
        fragmented = _Fragmented(data[:1], data[1:7], data[7:], content_length=None)
        url = "https://images.test/fragmented"
        sync = ToobitLogoSynchronizer(self.store, session_factory=self.factory(
            {"data":{"l":[{"sn":"BTCUSDT", "i":url}]}}, {url:fragmented}))
        summary = await sync.sync_once()
        self.assertEqual((summary.downloaded, summary.failed), (1, 0))
        self.assertIsNotNone(self.store.resolve(Exchange.TOOBIT, "BTCUSDT"))

    async def test_fragmented_cumulative_limit_without_content_length_is_enforced(self):
        content = _Content(_Fragmented(b"123", b"456", content_length=None))
        with self.assertRaisesRegex(ValueError, "byte limit"):
            await _read_bounded_body(content, 5)

        bad, good = "https://images.test/fragmented-big", "https://images.test/good"
        good_data = image_bytes("PNG")
        maximum = len(good_data)
        document = {"data":{"l":[{"sn":"BTCUSDT", "i":bad},
                                     {"sn":"ETHUSDT", "i":good}]}}
        sync = ToobitLogoSynchronizer(self.store, max_image_bytes=maximum,
            session_factory=self.factory(document, {
                bad:_Fragmented(b"a" * (maximum - 1), b"body-secret"),
                good:good_data,
            }))
        summary = await sync.sync_once()
        self.assertEqual((summary.downloaded, summary.failed), (1, 1))
        self.assertIsNone(self.store.resolve(Exchange.TOOBIT, "BTCUSDT"))
        self.assertIsNotNone(self.store.resolve(Exchange.TOOBIT, "ETHUSDT"))
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    async def test_item_failure_logs_are_sanitized_once_per_normalized_symbol(self):
        redirect = "https://private.example/path?secret=yes"
        unreadable = "https://images.test/unreadable"
        document = {"data":{"l":[
            {"sn":"btcusdt", "i":redirect}, {"sn":"ethusdt", "i":unreadable}]}}
        sync = ToobitLogoSynchronizer(self.store, session_factory=self.factory(document, {
            redirect:(b"response-secret", "http://private.example/redirect?token=x"),
            unreadable:b"private-response-body",
        }))
        with self.assertLogs("chainura.rendering.logos", level="WARNING") as logs:
            summary = await sync.sync_once()
        self.assertEqual(summary.failed, 2)
        self.assertEqual(len(logs.output), 2)
        joined = "\n".join(logs.output)
        self.assertIn("symbol=BTCUSDT category=ValueError", joined)
        self.assertIn("symbol=ETHUSDT category=UnidentifiedImageError", joined)
        for unsafe in (redirect, "private.example", "response-secret",
                       "private-response-body", "token=x"):
            self.assertNotIn(unsafe, joined)

    async def test_success_and_skip_do_not_warn_and_cancellation_propagates(self):
        existing = self.store.path(Exchange.TOOBIT, "BTCUSDT")
        existing.parent.mkdir(parents=True)
        existing.write_bytes(image_bytes("PNG"))
        good = "https://images.test/good"
        document = {"data":{"l":[{"sn":"BTCUSDT", "i":"https://images.test/skip"},
                                     {"sn":"ETHUSDT", "i":good}]}}
        sync = ToobitLogoSynchronizer(self.store, session_factory=self.factory(
            document, {good:image_bytes("PNG")}))
        with patch("chainura.rendering.logos.LOGGER.warning") as warning:
            summary = await sync.sync_once()
        self.assertEqual((summary.skipped_existing, summary.downloaded), (1, 1))
        warning.assert_not_called()

        cancelled_url = "https://images.test/cancelled"
        cancelled = ToobitLogoSynchronizer(self.store, session_factory=self.factory(
            {"data":{"l":[{"sn":"SOLUSDT", "i":cancelled_url}]}},
            {cancelled_url:_Fragmented(asyncio.CancelledError())}))
        with patch("chainura.rendering.logos.LOGGER.warning") as warning:
            with self.assertRaises(asyncio.CancelledError):
                await cancelled.sync_once()
        warning.assert_not_called()

    async def test_png_webp_jpeg_and_extensionless_are_real_png(self):
        cases = {"BTCUSDT": ("PNG", "a.png"), "ETHUSDT": ("WEBP", "b.webp"),
                 "SOLUSDT": ("JPEG", "c.jpg"), "XRPUSDT": ("PNG", "no-extension")}
        items, images = [], {}
        for symbol, (format, name) in cases.items():
            url = f"https://images.test/{name}"
            items.append({"sn": symbol, "i": url}); images[url] = image_bytes(format)
        sync = ToobitLogoSynchronizer(self.store,
            session_factory=self.factory({"data":{"l":items}}, images))
        summary = await sync.sync_once()
        self.assertEqual(summary.downloaded, 4)
        for symbol in cases:
            path = self.store.resolve(Exchange.TOOBIT, symbol)
            self.assertIsNotNone(path)
            with Image.open(path) as image: self.assertEqual((image.format, image.mode), ("PNG", "RGBA"))

    async def test_existing_valid_skipped_corrupt_replaced_and_failures_isolated(self):
        valid = self.store.path(Exchange.TOOBIT, "BTCUSDT")
        corrupt = self.store.path(Exchange.TOOBIT, "ETHUSDT")
        valid.parent.mkdir(parents=True)
        valid.write_bytes(image_bytes("PNG")); corrupt.write_bytes(b"bad")
        items = [{"sn":"BTCUSDT","i":"https://images.test/a"},
                 {"sn":"ETHUSDT","i":"https://images.test/b"},
                 {"sn":"SOLUSDT","i":"https://images.test/c"},
                 {"sn":"../ESCAPE","i":"https://images.test/d"},
                 {"sn":"XRPUSDT"}]
        sync = ToobitLogoSynchronizer(self.store, session_factory=self.factory(
            {"data":{"l":items}}, {"https://images.test/b":image_bytes("JPEG"),
             "https://images.test/c":b"unreadable"}))
        summary = await sync.sync_once()
        self.assertEqual((summary.discovered, summary.skipped_existing,
                          summary.replaced_invalid, summary.failed), (3, 1, 1, 1))
        self.assertTrue(self.store.is_valid_png(corrupt))
        self.assertFalse((self.root.parent / "ESCAPE.png").exists())

    async def test_malformed_http_and_oversized_are_nonfatal_without_partial_file(self):
        malformed = ToobitLogoSynchronizer(self.store,
            session_factory=self.factory({"wrong":[]}, {}))
        self.assertEqual((await malformed.sync_once()).failed, 1)
        url = "https://images.test/huge"
        oversized = ToobitLogoSynchronizer(self.store, max_image_bytes=4,
            session_factory=self.factory({"data":{"l":[{"sn":"BTCUSDT","i":url}]}},
                                         {url:b"12345"}))
        self.assertEqual((await oversized.sync_once()).failed, 1)
        self.assertIsNone(self.store.resolve(Exchange.TOOBIT, "BTCUSDT"))
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    async def test_pixel_limit_is_checked_before_load_and_leaves_no_output(self):
        destination = self.store.path(Exchange.TOOBIT, "BTCUSDT")
        fake = unittest.mock.MagicMock()
        fake.size = (11, 10)
        fake.__enter__.return_value = fake
        with patch("chainura.rendering.logos.Image.open", return_value=fake):
            with self.assertRaisesRegex(ValueError, "pixel limit"):
                ToobitLogoSynchronizer._write_png(destination, b"header", 100)
        fake.load.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    async def test_decompression_warning_and_error_are_controlled(self):
        item = {"sn":"BTCUSDT", "i":"https://images.test/a"}
        for exception in (Image.DecompressionBombWarning,
                          Image.DecompressionBombError):
            with self.subTest(exception=exception.__name__):
                sync = ToobitLogoSynchronizer(self.store, session_factory=self.factory(
                    {"data":{"l":[item]}}, {item["i"]:image_bytes("PNG")}))
                def rejected(*args, **kwargs):
                    if exception is Image.DecompressionBombWarning:
                        warnings.warn("bomb", exception)
                    raise exception("bomb")
                with patch("chainura.rendering.logos.Image.open", side_effect=rejected):
                    summary = await sync.sync_once()
                self.assertEqual(summary.failed, 1)
                self.assertEqual(list(self.root.rglob("*.png")), [])
                self.assertEqual(list(self.root.rglob("*.tmp")), [])

    async def test_http_redirect_is_rejected_and_other_valid_logo_continues(self):
        bad, good = "https://images.test/bad", "https://images.test/good"
        document = {"data":{"l":[{"sn":"BTCUSDT","i":bad},
                                     {"sn":"ETHUSDT","i":good}]}}
        sync = ToobitLogoSynchronizer(self.store, session_factory=self.factory(document, {
            bad:(image_bytes("PNG"), "http://redirect.test/bad"),
            good:image_bytes("PNG"),
        }))
        summary = await sync.sync_once()
        self.assertEqual((summary.downloaded, summary.failed), (1, 1))
        self.assertIsNone(self.store.resolve(Exchange.TOOBIT, "BTCUSDT"))
        self.assertIsNotNone(self.store.resolve(Exchange.TOOBIT, "ETHUSDT"))

    async def test_rejected_oversized_image_does_not_replace_previous_file(self):
        destination = self.store.path(Exchange.TOOBIT, "BTCUSDT")
        destination.parent.mkdir(parents=True)
        original = image_bytes("PNG")
        destination.write_bytes(original)
        fake = unittest.mock.MagicMock()
        fake.size = (20, 20); fake.__enter__.return_value = fake
        with patch("chainura.rendering.logos.Image.open", return_value=fake):
            with self.assertRaises(ValueError):
                ToobitLogoSynchronizer._write_png(destination, b"replacement", 100)
        self.assertEqual(destination.read_bytes(), original)
        fake.load.assert_not_called()

    async def test_rejected_oversized_image_does_not_stop_valid_image(self):
        bad, good = "https://images.test/oversized", "https://images.test/good"
        document = {"data":{"l":[{"sn":"BTCUSDT","i":bad},
                                     {"sn":"ETHUSDT","i":good}]}}
        sync = ToobitLogoSynchronizer(self.store, max_image_pixels=100,
            session_factory=self.factory(document, {
                bad:b"oversized-header", good:image_bytes("PNG")}))
        real_open = Image.open
        oversized = unittest.mock.MagicMock()
        oversized.size = (11, 10); oversized.__enter__.return_value = oversized
        def open_image(stream):
            if stream.getvalue() == b"oversized-header": return oversized
            return real_open(stream)
        with patch("chainura.rendering.logos.Image.open", side_effect=open_image):
            summary = await sync.sync_once()
        self.assertEqual((summary.downloaded, summary.failed), (1, 1))
        oversized.load.assert_not_called()
        self.assertIsNone(self.store.resolve(Exchange.TOOBIT, "BTCUSDT"))
        self.assertIsNotNone(self.store.resolve(Exchange.TOOBIT, "ETHUSDT"))

    async def test_complete_symbol_operations_and_existing_validation_are_bounded(self):
        concurrency = 2
        items = [{"sn":f"COIN{index}USDT", "i":f"https://images.test/{index}"}
                 for index in range(6)]
        for item in items:
            path = self.store.path(Exchange.TOOBIT, item["sn"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes("PNG"))
        active = maximum = 0
        lock = threading.Lock()
        original = self.store.is_valid_png
        def measured(path):
            nonlocal active, maximum
            with lock:
                active += 1; maximum = max(maximum, active)
            time.sleep(.02)
            try: return original(path)
            finally:
                with lock: active -= 1
        sync = ToobitLogoSynchronizer(self.store, concurrency=concurrency,
            session_factory=self.factory({"data":{"l":items}}, {}))
        with patch.object(self.store, "is_valid_png", side_effect=measured):
            summary = await sync.sync_once()
        self.assertEqual(summary.skipped_existing, len(items))
        self.assertEqual(maximum, concurrency)

    async def test_recurring_runs_immediately_waits_and_stops(self):
        class Sync:
            def __init__(self): self.calls = 0; self.called = asyncio.Event()
            async def sync_once(self):
                self.calls += 1; self.called.set(); return LogoSyncSummary()
        sync, stop = Sync(), asyncio.Event()
        task = asyncio.create_task(run_logo_sync(sync, stop, interval=3600))
        await asyncio.wait_for(sync.called.wait(), .5)
        self.assertEqual(sync.calls, 1)
        await asyncio.sleep(0); self.assertFalse(task.done())
        stop.set(); await asyncio.wait_for(task, .5)

    async def test_runtime_cleanup_stops_and_awaits_logo_task(self):
        runtime = Runtime(object())
        runtime.state = RuntimeState.RUNNING
        runtime.logo_sync_task = asyncio.create_task(runtime.logo_sync_stop.wait())
        await runtime.cleanup()
        self.assertTrue(runtime.logo_sync_stop.is_set())
        self.assertTrue(runtime.logo_sync_task.done())

    def test_resolver_and_symbol_validation_are_local_and_safe(self):
        self.assertEqual(normalize_symbol(" btcusdt "), "BTCUSDT")
        for invalid in ("../BTC", "BTC/USD", "", None, "A" * 65):
            self.assertIsNone(self.store.path(Exchange.TOOBIT, invalid))
        self.assertIsNone(self.store.resolve(Exchange.LBANK, "BTCUSDT"))


if __name__ == "__main__": unittest.main()
