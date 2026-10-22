"""Settings entities restore their last value after a restart.

The API has no read-back for TOU / peak / load settings, so the last value set
in HA only survives a restart through RestoreEntity.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import mock_restore_cache

from .conftest import entity_id


async def test_tou_settings_restore(hass: HomeAssistant, mock_config_entry, mock_api) -> None:
    # Entity ids are deterministic (device "Test Station" + translated name).
    mock_restore_cache(hass, [
        State("switch.test_station_tou_group_1_enabled", "on"),
        State("time.test_station_tou_group_1_start", "12:02:00"),
        State("number.test_station_tou_group_1_power", "9000.0"),
        State("select.test_station_tou_group_1_days", "Weekdays"),
        State("select.test_station_tou_group_1_mode", "Discharge"),
        State("number.test_station_on_grid_discharge_depth", "85.0"),
    ])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entry = mock_config_entry
    assert hass.states.get(entity_id(hass, entry, "switch", "tou_g1_enabled")).state == "on"
    assert hass.states.get(entity_id(hass, entry, "time", "tou_g1_start")).state == "12:02:00"
    assert hass.states.get(entity_id(hass, entry, "number", "tou_g1_power")).state == "9000.0"
    assert hass.states.get(entity_id(hass, entry, "select", "tou_g1_days")).state == "Weekdays"
    assert hass.states.get(entity_id(hass, entry, "select", "tou_g1_mode")).state == "Discharge"
    assert hass.states.get(entity_id(hass, entry, "number", "battery_on_grid_dod")).state == "85.0"


async def test_invalid_restored_values_fall_back_to_defaults(hass: HomeAssistant, mock_config_entry, mock_api) -> None:
    mock_restore_cache(hass, [
        State("time.test_station_tou_group_2_start", "garbage"),
        State("number.test_station_tou_group_2_power", "unavailable"),
        State("select.test_station_tou_group_2_days", "Fortnightly"),
    ])
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entry = mock_config_entry
    assert hass.states.get(entity_id(hass, entry, "time", "tou_g2_start")).state == "22:00:00"
    assert hass.states.get(entity_id(hass, entry, "number", "tou_g2_power")).state == "500.0"
    assert hass.states.get(entity_id(hass, entry, "select", "tou_g2_days")).state == "Every Day"
