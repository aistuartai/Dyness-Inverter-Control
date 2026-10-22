"""Config flow and options flow."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyness_battery_cygni import CONF_SCAN_INTERVAL_MINUTES, DOMAIN

from .conftest import API_ID, API_SECRET, DEVICE_SN, DONGLE_SN

FETCH = "custom_components.dyness_battery_cygni.config_flow._fetch_device_list"
SETUP = "custom_components.dyness_battery_cygni.async_setup_entry"

USER_INPUT = {"api_id": API_ID, "api_secret": API_SECRET, "region": "apac", CONF_SCAN_INTERVAL_MINUTES: "5"}

DEVICE = {
    "deviceSn": DEVICE_SN, "collectorSn": DONGLE_SN, "stationName": "Test Station",
    "deviceModelName": "Cygni 10.0HS-M8", "workStatus": "Grid-tied",
}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_single_device_creates_entry(hass: HomeAssistant) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    with patch(FETCH, return_value=[DEVICE]) as fetch, patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    fetch.assert_called_once_with(hass, API_ID, API_SECRET, "https://apacopen-api.dyness.com")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Station"
    assert result["data"] == {
        "api_id": API_ID, "api_secret": API_SECRET, "api_base": "https://apacopen-api.dyness.com",
        "device_sn": DEVICE_SN, "dongle_sn": DONGLE_SN, CONF_SCAN_INTERVAL_MINUTES: "5",
    }
    assert result["result"].unique_id == f"{API_ID}_{DEVICE_SN}"


async def test_europe_region_uses_europe_host(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with patch(FETCH, return_value=[DEVICE]) as fetch, patch(SETUP, return_value=True):
        await hass.config_entries.flow.async_configure(result["flow_id"], {**USER_INPUT, "region": "europe"})
    assert fetch.call_args.args[3] == "https://open-api.dyness.com"


async def test_no_devices_shows_error(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with patch(FETCH, return_value=[]):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "discovery_failed"}


async def test_multiple_devices_asks_which_one(hass: HomeAssistant) -> None:
    second = {**DEVICE, "deviceSn": "FAKESN000000000000002-BMS", "stationName": "Shed"}
    first = {**DEVICE, "deviceSn": "FAKESN000000000000001-BMS"}
    result = await _start(hass)
    with patch(FETCH, return_value=[first, second]):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "select_device"
    assert result["description_placeholders"] == {"count": "2"}

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device_sn": "FAKESN000000000000002-BMS"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Shed"
    assert result["data"]["device_sn"] == "FAKESN000000000000002-BMS"


async def test_same_device_twice_aborts(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await _start(hass)
    with patch(FETCH, return_value=[DEVICE]):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_manual_step(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "manual"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {
            "api_id": API_ID, "api_secret": API_SECRET, "region": "apac",
            "device_sn": DEVICE_SN, "dongle_sn": "",
        })
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["api_base"] == "https://apacopen-api.dyness.com"
    assert result["data"]["dongle_sn"] is None


async def test_options_flow_changes_scan_interval(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL_MINUTES: "15"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {CONF_SCAN_INTERVAL_MINUTES: "15"}
