from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from chainura.domain.destination import Exchange
from chainura.rendering.logos import (CoinLogoStore, LogoSyncSummary,
    ToobitLogoSynchronizer, normalize_symbol, run_logo_sync)
from chainura.telegram.runtime import Runtime, RuntimeState


def image_bytes(format: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), (20, 40, 60)).save(output, format)
    return output.getvalue()


class _Content:
    def __init__(self, data): self.data = data
    async def read(self, limit): return self.data[:limit]


class _Response:
    def __init__(self, *, document=None, data=b"", url="https://images.test/logo"):
        self.document, self.content, self.url = document, _Content(data), url
        self.content_length = len(data) if data else None
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
        return _Response(data=value, url=url)


class LogoSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CoinLogoStore(self.root)

    async def asyncTearDown(self): self.temp.cleanup()

    def factory(self, document, images):
        return lambda **kwargs: _Session(document, images)

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
