"""Shared fixtures.

All API traffic is faked through HA's ``aioclient_mock``; no test ever reaches
the Dyness cloud. Fixture JSON under ``fixtures/cygni`` is real data captured
from a Cygni 10.0HS-M8 with every identifier replaced (see fixtures/README.md).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.dyness_battery_cygni import CONF_SCAN_INTERVAL_MINUTES, DOMAIN

FIXTURES = Path(__file__).parent / "fixtures" / "cygni"

API_BASE = "https://apacopen-api.dyness.com"
URL_PREFIX = f"{API_BASE}/openapi/ems-device"
DEVICE_SN = "FAKESN000000000000001"
DONGLE_SN = "FAKECOL001"
API_ID = "test_api_id"
API_SECRET = "test_api_secret"

READ_ENDPOINTS: dict[str, str] = {
    "/v1/device/storage/list": "v1_device_storage_list",
    "/v1/device/bindSn": "v1_device_bindSn",
    "/v1/station/info": "v1_station_info",
    "/v1/device/household/storage/detail": "v1_device_household_storage_detail",
    "/v1/device/realTime/data": "v1_device_realTime_data",
    "/v1/device/getLastRunningDataBySn": "v1_device_getLastRunningDataBySn",
    "/v1/device/getLastPowerDataBySn": "v1_device_getLastPowerDataBySn",
    "/v2/GetRealTimeDataBySN": "v2_GetRealTimeDataBySN",
    "/v2/GetStatusInfBySN": "v2_GetStatusInfBySN",
}

WRITE_ENDPOINTS = (
    "/v2/SetWorkModeSetting",
    "/v2/SetBatterySetting",
    "/v2/SetPeakControlSetting",
    "/v2/SetLoadControlSetting",
)

WRITE_OK = {"code": "200", "data": None, "info": "Operation Successful"}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def calls_to(aioclient_mock: AiohttpClientMocker, path: str) -> list[dict]:
    """Decoded JSON bodies of every request sent to ``path``."""
    url = f"{URL_PREFIX}{path}"
    return [json.loads(data) for _method, req_url, data, _headers in aioclient_mock.mock_calls
            if str(req_url) == url]


def entity_id(hass: HomeAssistant, entry: MockConfigEntry, domain: str, suffix: str) -> str:
    """Resolve an entity id from its unique-id suffix (``<entry_id>_<suffix>``)."""
    eid = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{suffix}")
    assert eid is not None, f"no {domain} entity with unique_id suffix {suffix!r}"
    return eid


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_call_spacing():
    """The coordinator spaces API calls 1.5 s apart; tests don't need to wait."""
    with patch("custom_components.dyness_battery_cygni.api.MIN_CALL_INTERVAL", 0):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Station",
        unique_id=f"{API_ID}_{DEVICE_SN}",
        data={
            "api_id": API_ID,
            "api_secret": API_SECRET,
            "api_base": API_BASE,
            "device_sn": DEVICE_SN,
            "dongle_sn": DONGLE_SN,
            CONF_SCAN_INTERVAL_MINUTES: "5",
        },
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer every read endpoint with its captured fixture and every write with success."""
    for path, name in READ_ENDPOINTS.items():
        aioclient_mock.post(f"{URL_PREFIX}{path}", json=load_fixture(name))
    for path in WRITE_ENDPOINTS:
        aioclient_mock.post(f"{URL_PREFIX}{path}", json=WRITE_OK)
    return aioclient_mock


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> MockConfigEntry:
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
