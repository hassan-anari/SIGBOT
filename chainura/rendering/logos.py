from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, UnidentifiedImageError

from chainura.domain.destination import Exchange

LOGGER = logging.getLogger("chainura.rendering.logos")
TOOBIT_SYMBOLS_URL = "https://bapi.toobit.com/capi/v4/basic/symbol/futures?business_type=USDT"
SYNC_INTERVAL_SECONDS = 6 * 60 * 60
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
_SYMBOL = re.compile(r"[A-Z0-9]{1,64}")


def normalize_symbol(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if _SYMBOL.fullmatch(normalized) else None


class CoinLogoStore:
    def __init__(self, root: Path | str = Path("var/coin_logos")) -> None:
        self.root = Path(root)

    def path(self, exchange: Exchange, native_symbol: object) -> Path | None:
        symbol = normalize_symbol(native_symbol)
        if symbol is None or exchange is not Exchange.TOOBIT:
            return None
        return self.root / exchange.value / f"{symbol}.png"

    def resolve(self, exchange: Exchange, native_symbol: object) -> Path | None:
        path = self.path(exchange, native_symbol)
        return path if path is not None and self.is_valid_png(path) else None

    @staticmethod
    def is_valid_png(path: Path) -> bool:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    image.verify()
                    return image.format == "PNG"
        except (OSError, UnidentifiedImageError, Image.DecompressionBombWarning,
                Image.DecompressionBombError):
            return False


@dataclass(frozen=True, slots=True)
class LogoSyncSummary:
    discovered: int = 0
    skipped_existing: int = 0
    downloaded: int = 0
    replaced_invalid: int = 0
    failed: int = 0


class ToobitLogoSynchronizer:
    def __init__(self, store: CoinLogoStore, *, concurrency: int = 4,
                 max_image_bytes: int = MAX_IMAGE_BYTES,
                 max_image_pixels: int = MAX_IMAGE_PIXELS,
                 session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession):
        if concurrency <= 0 or max_image_bytes <= 0 or max_image_pixels <= 0:
            raise ValueError("logo synchronization limits must be positive")
        self.store = store
        self.concurrency = concurrency
        self.max_image_bytes = max_image_bytes
        self.max_image_pixels = max_image_pixels
        self.session_factory = session_factory

    async def sync_once(self) -> LogoSyncSummary:
        timeout = aiohttp.ClientTimeout(total=45, connect=8, sock_read=15)
        try:
            async with self.session_factory(timeout=timeout) as session:
                async with session.get(TOOBIT_SYMBOLS_URL) as response:
                    response.raise_for_status()
                    document = await response.json(content_type=None)
                items = document.get("data", {}).get("l") if isinstance(document, dict) else None
                if not isinstance(items, list):
                    raise ValueError("malformed Toobit symbol response")
                pairs = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    symbol = normalize_symbol(item.get("sn"))
                    url = item.get("i")
                    if symbol and isinstance(url, str) and urlsplit(url).scheme == "https":
                        pairs.append((symbol, url))
                semaphore = asyncio.Semaphore(self.concurrency)
                results = await asyncio.gather(
                    *(self._sync_logo(session, semaphore, symbol, url) for symbol, url in pairs),
                    return_exceptions=True,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error("Toobit logo sync failed category=%s", type(exc).__name__)
            return LogoSyncSummary(failed=1)

        counts = {"skipped": 0, "downloaded": 0, "replaced": 0, "failed": 0}
        for result in results:
            counts[result if isinstance(result, str) else "failed"] += 1
        summary = LogoSyncSummary(len(pairs), counts["skipped"], counts["downloaded"],
                                  counts["replaced"], counts["failed"])
        LOGGER.info("Toobit logo sync discovered=%d skipped_existing=%d downloaded=%d "
                    "replaced_invalid=%d failed=%d", summary.discovered,
                    summary.skipped_existing, summary.downloaded,
                    summary.replaced_invalid, summary.failed)
        return summary

    async def _sync_logo(self, session, semaphore, symbol: str, url: str) -> str:
        destination = self.store.path(Exchange.TOOBIT, symbol)
        assert destination is not None
        async with semaphore:
            return await self._sync_logo_bounded(session, destination, url)

    async def _sync_logo_bounded(self, session, destination: Path, url: str) -> str:
        """Perform every disk, network, and decode step under the caller's permit."""
        try:
            existed = await asyncio.to_thread(destination.exists)
            if existed and await asyncio.to_thread(self.store.is_valid_png, destination):
                return "skipped"
            async with session.get(url) as response:
                response.raise_for_status()
                if urlsplit(str(response.url)).scheme != "https":
                    raise ValueError("logo redirect is not HTTPS")
                content_length = response.content_length
                if content_length is not None and content_length > self.max_image_bytes:
                    raise ValueError("logo exceeds byte limit")
                data = await response.content.read(self.max_image_bytes + 1)
                if len(data) > self.max_image_bytes:
                    raise ValueError("logo exceeds byte limit")
            await asyncio.to_thread(
                self._write_png, destination, data, self.max_image_pixels
            )
            return "replaced" if existed else "downloaded"
        except asyncio.CancelledError:
            raise
        except (Image.DecompressionBombWarning, Image.DecompressionBombError):
            return "failed"
        except Exception:
            return "failed"

    @staticmethod
    def _write_png(destination: Path, data: bytes, max_image_pixels: int) -> None:
        temporary: Path | None = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                source = Image.open(io.BytesIO(data))
            with source:
                width, height = source.size
                if (isinstance(width, bool) or not isinstance(width, int) or width <= 0
                        or isinstance(height, bool) or not isinstance(height, int)
                        or height <= 0):
                    raise ValueError("logo dimensions are invalid")
                if width * height > max_image_pixels:
                    raise ValueError("logo exceeds pixel limit")
                source.load()
                converted = source.convert("RGBA")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp",
                                             dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                converted.save(stream, "PNG")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


async def run_logo_sync(synchronizer: ToobitLogoSynchronizer, stop: asyncio.Event,
                        interval: float = SYNC_INTERVAL_SECONDS) -> None:
    while not stop.is_set():
        await synchronizer.sync_once()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
