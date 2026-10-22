"""Setup, unload and coordinator behaviour."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.dyness_battery_cygni import DOMAIN

from .conftest import READ_ENDPOINTS, URL_PREFIX, calls_to, load_fixture


def _fail_all(aioclient_mock) -> None:
    aioclient_mock.clear_requests()
    for path in READ_ENDPOINTS:
        aioclient_mock.post(f"{URL_PREFIX}{path}", json={"code": "401", "data": None, "info": "Unauthorized"})


async def test_setup_and_unload(hass: HomeAssistant, init_integration) -> None:
    entry = init_integration
    assert entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "set_tou_schedule")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_first_refresh_calls_every_read_endpoint(hass: HomeAssistant, init_integration, mock_api) -> None:
    for path in (
        "/v1/device/bindSn",
        "/v1/station/info",
        "/v1/device/household/storage/detail",
        "/v1/device/storage/list",
        "/v1/device/realTime/data",
        "/v1/device/getLastRunningDataBySn",
        "/v1/device/getLastPowerDataBySn",
        "/v2/GetRealTimeDataBySN",
        "/v2/GetStatusInfBySN",
    ):
        assert calls_to(mock_api, path), f"{path} was never called"
    # No write endpoint may be touched just by loading the integration.
    assert not calls_to(mock_api, "/v2/SetWorkModeSetting")


async def test_requests_are_signed_with_path_excluding_prefix(hass: HomeAssistant, init_integration, mock_api) -> None:
    # Spec p4: the signed path omits the /openapi/ems-device prefix of the URL.
    for _method, url, _data, headers in mock_api.mock_calls:
        assert str(url).startswith(URL_PREFIX)
        assert headers["Authorization"].startswith("API test_api_id:")
        assert set(headers) >= {"Content-MD5", "Date", "Content-Type"}


async def test_total_api_failure_retries_setup(hass: HomeAssistant, mock_config_entry, aioclient_mock) -> None:
    _fail_all(aioclient_mock)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_power_endpoint_failure_keeps_other_data(hass: HomeAssistant, mock_config_entry, mock_api) -> None:
    """A failing getLastPowerDataBySn must not take every sensor unavailable."""
    mock_api.clear_requests()
    for path, name in READ_ENDPOINTS.items():
        if path == "/v1/device/getLastPowerDataBySn":
            mock_api.post(f"{URL_PREFIX}{path}", json={"code": "500", "data": None, "info": "boom"})
        else:
            mock_api.post(f"{URL_PREFIX}{path}", json=load_fixture(name))
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.test_station_inverter_total_power").state == "429.0"


async def test_rate_limit_backs_off_then_succeeds(hass: HomeAssistant, mock_config_entry, mock_api) -> None:
    attempts = {"n": 0}

    async def first_two_rate_limited(method, url, data):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            return AiohttpClientMockResponse(method, url, status=429, text="")
        return AiohttpClientMockResponse(method, url, json=load_fixture("v1_station_info"))

    mock_api.clear_requests()
    for path, name in READ_ENDPOINTS.items():
        if path == "/v1/station/info":
            mock_api.post(f"{URL_PREFIX}{path}", side_effect=first_two_rate_limited)
        else:
            mock_api.post(f"{URL_PREFIX}{path}", json=load_fixture(name))

    sleep = AsyncMock()
    with patch("custom_components.dyness_battery_cygni.asyncio.sleep", sleep):
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert attempts["n"] == 3
    # The patch is global (asyncio is shared), so ignore HA's own sleep(0) yields.
    assert [c.args[0] for c in sleep.await_args_list if c.args[0]] == [10, 20]   # exponential backoff
    assert mock_config_entry.runtime_data.station_info["batteryCapacity"] == "30.72"


async def test_v2_not_found_disables_v2_polling(hass: HomeAssistant, mock_config_entry, mock_api) -> None:
    mock_api.clear_requests()
    for path, name in READ_ENDPOINTS.items():
        if path.startswith("/v2/"):
            mock_api.post(f"{URL_PREFIX}{path}", json={"code": "404", "data": None, "info": "Not Found"})
        else:
            mock_api.post(f"{URL_PREFIX}{path}", json=load_fixture(name))
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    assert coordinator._v2_unavailable is True
    assert not calls_to(mock_api, "/v2/GetStatusInfBySN")

    before = len(calls_to(mock_api, "/v2/GetRealTimeDataBySN"))
    await coordinator.async_refresh()
    assert len(calls_to(mock_api, "/v2/GetRealTimeDataBySN")) == before


async def test_v2_endpoints_polled_every_third_cycle(hass: HomeAssistant, init_integration, mock_api) -> None:
    coordinator = init_integration.runtime_data
    first = len(calls_to(mock_api, "/v2/GetRealTimeDataBySN"))
    for _ in range(3):
        await coordinator.async_refresh()
    assert len(calls_to(mock_api, "/v2/GetRealTimeDataBySN")) == first + 1
