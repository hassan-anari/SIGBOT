from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class HttpError(RuntimeError):
    pass


def _get_json_sync(url: str, params: dict[str, str] | None, timeout: float) -> Any:
    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(params)}"

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "haz-market-data-probe/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except Exception as exc:  # urllib exposes several transport exceptions
        raise HttpError(f"GET failed: {url}: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HttpError(f"Invalid JSON from: {url}") from exc


async def get_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> Any:
    return await asyncio.to_thread(_get_json_sync, url, params, timeout)
