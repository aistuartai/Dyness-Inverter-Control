"""DynessApiClient: spacing, retries and error handling, with an injected clock."""
from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.dyness_battery_cygni import api
from custom_components.dyness_battery_cygni.api import DynessApiClient

BASE = "https://example.test"
URL = f"{BASE}/openapi/ems-device/v1/x"


@pytest.fixture
def waits() -> list[float]:
    return []


@pytest.fixture
async def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, waits) -> DynessApiClient:
    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    now = iter(range(0, 10_000, 100))   # every clock read jumps 100 s: no spacing waits
    return DynessApiClient(async_get_clientsession(hass), "id", "secret", BASE,
                           clock=lambda: next(now), sleep=sleep)


async def test_call_returns_json(aioclient_mock, client) -> None:
    aioclient_mock.post(URL, json={"code": "200"})
    assert await client.call("/v1/x", {"a": 1}) == {"code": "200"}
    assert aioclient_mock.mock_calls[0][2] == '{"a":1}'      # compact JSON, as signed


async def test_spacing_waits_when_calls_are_close(hass: HomeAssistant, aioclient_mock, waits) -> None:
    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    aioclient_mock.post(URL, json={})
    client = DynessApiClient(async_get_clientsession(hass), "id", "secret", BASE,
                             clock=lambda: 100.0, sleep=sleep)
    with patch.object(api, "MIN_CALL_INTERVAL", 1.5):     # conftest zeroes it for speed
        await client.call("/v1/x", {})
        await client.call("/v1/x", {})
    assert waits == [1.5]


async def test_non_json_returns_empty(aioclient_mock, client) -> None:
    aioclient_mock.post(URL, text="<html>Bad Gateway</html>", status=502)
    assert await client.call("/v1/x", {}) == {}


async def test_rate_limit_gives_up_after_retries(aioclient_mock, client, waits) -> None:
    aioclient_mock.post(URL, status=429, text="")
    assert await client.call("/v1/x", {}) == {}
    assert waits == [10, 20, 40]


async def test_connection_error_retries_then_raises(aioclient_mock, client, waits) -> None:
    aioclient_mock.post(URL, exc=aiohttp.ClientConnectionError("down"))
    with pytest.raises(aiohttp.ClientError):
        await client.call("/v1/x", {})
    assert waits == [1, 2, 4]
    assert len(aioclient_mock.mock_calls) == 4


async def test_default_clock_and_sleep(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL, json={"ok": 1})
    client = DynessApiClient(async_get_clientsession(hass), "id", "secret", BASE)
    assert await client.call("/v1/x", {}) == {"ok": 1}
