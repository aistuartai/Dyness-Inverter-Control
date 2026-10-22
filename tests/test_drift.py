"""Drift between HA's stored settings and what the inverter reports.

The API has no read-back for most settings, so values changed in the Dyness
app never reach HA. The v2 realtime data does report the actual on/off-grid
discharge depth; those two settings show it and flag drift. Nothing is
synced automatically — that would silently change what Stage sends.
"""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from .conftest import entity_id


async def _set(hass, eid, value) -> None:
    await hass.services.async_call("number", "set_value", {"entity_id": eid, "value": value}, blocking=True)


async def test_dod_shows_inverter_value_and_drift(hass: HomeAssistant, init_integration) -> None:
    entry = init_integration
    on_grid = entity_id(hass, entry, "number", "battery_on_grid_dod")
    state = hass.states.get(on_grid)
    assert state.state == "20.0"                                   # HA default, never written
    assert state.attributes["inverter_value"] == 85.0              # capture: onGridDischargeDepth 85
    assert state.attributes["in_sync"] is False

    await _set(hass, on_grid, 85)
    assert hass.states.get(on_grid).attributes["in_sync"] is True


async def test_settings_without_readback_have_no_drift_attributes(hass: HomeAssistant, init_integration) -> None:
    charge_limit = entity_id(hass, init_integration, "number", "battery_charge_limit")
    assert "in_sync" not in hass.states.get(charge_limit).attributes


async def test_stage_notification_shows_inverter_values_when_different(
    hass: HomeAssistant, init_integration
) -> None:
    entry = init_integration
    await _set(hass, entity_id(hass, entry, "number", "battery_off_grid_dod"), 85)   # in sync
    with patch("custom_components.dyness_battery_cygni.settings_entities.pn_async_create") as notify:
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id(hass, entry, "button", "battery_stage_button")},
            blocking=True,
        )
    message = notify.call_args.kwargs["message"]
    assert "On-Grid Discharge Depth: 20% (inverter currently 85%)" in message
    assert "Off-Grid Discharge Depth: 85%\n" in message                # no note when in sync
    assert "differ from what the inverter reports" in message


async def test_stage_notification_plain_when_in_sync(hass: HomeAssistant, init_integration) -> None:
    entry = init_integration
    for key in ("battery_on_grid_dod", "battery_off_grid_dod"):
        await _set(hass, entity_id(hass, entry, "number", key), 85)
    with patch("custom_components.dyness_battery_cygni.settings_entities.pn_async_create") as notify:
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id(hass, entry, "button", "battery_stage_button")},
            blocking=True,
        )
    message = notify.call_args.kwargs["message"]
    assert "inverter currently" not in message and "differ" not in message
