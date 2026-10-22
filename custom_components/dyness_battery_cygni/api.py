"""Dyness Open API client: request signing, call spacing and retries.

Signing follows the Open API spec v1.0 (p.3-8): base64(HMAC-SHA1(secret,
"POST\\n{Content-MD5}\\napplication/json\\n{Date}\\n{path}")) where the signed
path omits the /openapi/ems-device prefix.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from email.utils import formatdate

import aiohttp

_LOGGER = logging.getLogger(__package__)

# API Rate-Limit: max ~60 Calls/Stunde = 1/Minute
# Pro Update: 3 Basis-Calls + 2 pro Sub-Modul
# 1-2 Module → 5 Min, 3-4 Module → 10 Min, 5+ Module → 15 Min
MIN_CALL_INTERVAL = 1.5
RATE_LIMIT_BACKOFF = 10
MAX_RETRIES = 3


def get_gmt_time() -> str:
    return formatdate(timeval=None, localtime=False, usegmt=True)


def get_md5(body: str) -> str:
    md5 = hashlib.md5(body.encode("utf-8")).digest()
    return base64.b64encode(md5).decode("utf-8")


def get_signature(api_secret: str, content_md5: str, date: str, path: str) -> str:
    string_to_sign = (
        "POST" + "\n" + content_md5 + "\n" +
        "application/json" + "\n" + date + "\n" + path
    )
    sig = hmac.new(
        api_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        "sha1"
    ).digest()
    return base64.b64encode(sig).decode("utf-8")


def build_headers(api_id: str, api_secret: str, body: str, sign_path: str) -> dict:
    date = get_gmt_time()
    content_md5 = get_md5(body)
    signature = get_signature(api_secret, content_md5, date, sign_path)
    return {
        "Content-Type": "application/json;charset=UTF-8",
        "Content-MD5": content_md5,
        "Date": date,
        "Authorization": f"API {api_id}:{signature}",
    }


class DynessApiClient:
    """Signed, spaced, retrying POSTs to one Dyness API host.

    ``clock`` and ``sleep`` default to the running loop's clock and
    ``asyncio.sleep``; tests can inject their own.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_id: str,
        api_secret: str,
        api_base: str,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self._api_id = api_id
        self._api_secret = api_secret
        self._api_base = api_base
        self._clock = clock
        self._sleep = sleep
        self._last_call_time = 0.0

    def _now(self) -> float:
        return self._clock() if self._clock else asyncio.get_running_loop().time()

    async def _wait(self, seconds: float) -> None:
        await (self._sleep or asyncio.sleep)(seconds)

    async def call(self, path: str, body_dict: dict) -> dict:
        """POST ``body_dict`` to ``path``; returns the decoded JSON, or {} on rate limit / non-JSON.

        HTTP 429 backs off 10/20/40 s; connection errors retry after 1/2/4 s and
        re-raise once retries are exhausted.
        """
        label = "Dyness v2" if path.startswith("/v2/") else "Dyness"
        elapsed = self._now() - self._last_call_time
        if elapsed < MIN_CALL_INTERVAL:
            await self._wait(MIN_CALL_INTERVAL - elapsed)
        url = f"{self._api_base}/openapi/ems-device{path}"
        body = json.dumps(body_dict, separators=(',', ':'))
        for attempt in range(MAX_RETRIES + 1):
            self._last_call_time = self._now()
            headers = build_headers(self._api_id, self._api_secret, body, path)
            try:
                async with self._session.post(url, headers=headers, data=body) as response:
                    if response.status == 429:
                        wait = RATE_LIMIT_BACKOFF * (2 ** attempt)
                        _LOGGER.warning(
                            "%s: Rate-Limit (429) auf %s – Retry %d/%d in %ds",
                            label, path, attempt + 1, MAX_RETRIES, wait,
                        )
                        if attempt < MAX_RETRIES:
                            await self._wait(wait)
                            continue
                        return {}
                    raw_text = await response.text()
                    _LOGGER.debug("%s %s: %s", label, path, raw_text)
                    try:
                        return json.loads(raw_text)
                    except json.JSONDecodeError:
                        _LOGGER.warning("%s %s: non-JSON response (HTTP %s): %s",
                                        label, path, response.status, raw_text[:200])
                        return {}
            except aiohttp.ClientError as e:
                _LOGGER.warning("%s %s Verbindungsfehler (Versuch %d/%d): %s",
                                label, path, attempt + 1, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    await self._wait(2 ** attempt)
                    continue
                raise
        return {}  # pragma: no cover - every loop path returns, continues or raises
