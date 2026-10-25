"""Write payloads — the hardware-facing part of the integration.

Every payload is pinned field by field. Where the PDF spec (Cygni HA/HS Open
API Protocol V1.0) and observed hardware disagree, hardware wins and the
comment records both.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyness_battery_cygni import DOMAIN

from .conftest import DEVICE_SN, URL_PREFIX, calls_to, entity_id

TOU_PATH = "/v2/SetWorkModeSetting"


def _coordinator(hass: HomeAssistant, entry):
    return entry.runtime_data


def _group(**overrides) -> dict:
    g = {
        "group": 1, "enabled": True, "start_time": "12:00", "end_time": "15:00",
        "mode": "charge", "days": "0,1,2,3,4,5,6", "power": 5000,
        "dod_enabled": True, "dod": 20, "soc_max_enabled": True, "soc_max": 100,
    }
    g.update(overrides)
    return g


# ── TOU schedule ─────────────────────────────────────────────────────────────

async def test_tou_payload_golden(hass: HomeAssistant, init_integration, mock_api) -> None:
    await _coordinator(hass, init_integration).async_set_tou_schedule([_group()])
    assert calls_to(mock_api, TOU_PATH)[-1] == {
        "deviceSn": DEVICE_SN,
        "workMode": "3",                     # TOU writes always switch the inverter to TOU mode
        "workGroups": [{
            "batteryWorkGroup": 1,           # spec p30: Integer 1-4 (the only non-string field)
            "state": "1",                    # spec p30 says 0=On; hardware is 1=On (fixed 2.5.6)
            "startTime": "12:00",            # HH:mm
            "endTime": "15:00",
            "power": "5000",
            "mode": "0",                     # 0=Charge, 1=Discharge (spec and hardware agree)
            "week": "0,1,2,3,4,5,6",         # Monday=0 (spec never says; confirmed 2.5.4)
            "dodState": "1",                 # same inversion as "state"
            "dod": "20",
            "socMaxChargeState": "1",        # same inversion as "state"
            "batterySocMaxCharge": "100",
        }],
    }


async def test_tou_spec_p31_example(hass: HomeAssistant, init_integration, mock_api) -> None:
    """The spec's own SetWorkModeSetting example (p31), reproduced through the integration."""
    await _coordinator(hass, init_integration).async_set_tou_schedule([_group(
        start_time="08:00", end_time="18:00", power=2000, days="0,1,2,3,4", dod=10, soc_max=100,
    )])
    group = calls_to(mock_api, TOU_PATH)[-1]["workGroups"][0]
    assert (group["state"], group["power"], group["week"], group["mode"]) == ("1", "2000", "0,1,2,3,4", "0")


@pytest.mark.parametrize(("overrides", "field", "value"), [
    ({"enabled": False}, "state", "0"),
    ({"mode": "discharge"}, "mode", "1"),
    ({"mode": "Discharge"}, "mode", "1"),
    ({"dod_enabled": False}, "dodState", "0"),
    ({"soc_max_enabled": False}, "socMaxChargeState", "0"),
    ({"power": 10000}, "power", "10000"),    # spec p30 caps at 2000; Cygni 10 kW verified to 10000 (2.6.1)
    ({"power": 0}, "power", "0"),
])
async def test_tou_field_encoding(hass: HomeAssistant, init_integration, mock_api, overrides, field, value) -> None:
    await _coordinator(hass, init_integration).async_set_tou_schedule([_group(**overrides)])
    assert calls_to(mock_api, TOU_PATH)[-1]["workGroups"][0][field] == value


@pytest.mark.parametrize(("groups", "message"), [
    ([], "non-empty"),
    ([_group(group=5)], "group must be 1-4"),
    ([_group(power=10001)], "power must be 0-10000W"),
    ([_group(power=-1)], "power must be 0-10000W"),
    ([_group(dod=96)], "dod must be 0-95%"),
    ([_group(soc_max=9)], "soc_max must be 10-100%"),
    ([_group(soc_max=101)], "soc_max must be 10-100%"),
])
async def test_tou_validation_rejects_before_sending(
    hass: HomeAssistant, init_integration, mock_api, groups, message
) -> None:
    with pytest.raises(HomeAssistantError, match=message):
        await _coordinator(hass, init_integration).async_set_tou_schedule(groups)
    assert not calls_to(mock_api, TOU_PATH)


async def test_tou_api_failure_raises(hass: HomeAssistant, init_integration, mock_api) -> None:
    mock_api.clear_requests()
    mock_api.post(f"{URL_PREFIX}{TOU_PATH}", json={"code": "500", "data": None, "info": "Invalid Parameter"})
    with pytest.raises(HomeAssistantError, match="Invalid Parameter"):
        await _coordinator(hass, init_integration).async_set_tou_schedule([_group()])


# ── Work mode ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["0", "2", "3"])
async def test_work_mode_payload(hass: HomeAssistant, init_integration, mock_api, mode) -> None:
    await _coordinator(hass, init_integration).async_set_work_mode(mode)
    # workGroups is a required field (spec p30); an empty list keeps it present.
    assert calls_to(mock_api, TOU_PATH)[-1] == {"deviceSn": DEVICE_SN, "workMode": mode, "workGroups": []}


@pytest.mark.parametrize("mode", ["1", "4", ""])
async def test_work_mode_refuses_off_grid_and_unknown(hass: HomeAssistant, init_integration, mock_api, mode) -> None:
    with pytest.raises(HomeAssistantError, match="not allowed"):
        await _coordinator(hass, init_integration).async_set_work_mode(mode)
    assert not calls_to(mock_api, TOU_PATH)


async def test_work_mode_select_sends_mode(hass: HomeAssistant, init_integration, mock_api) -> None:
    eid = entity_id(hass, init_integration, "select", "work_mode_select")
    await _call(hass, "select", "select_option", eid, option="Self-use")
    assert calls_to(mock_api, TOU_PATH)[-1] == {"deviceSn": DEVICE_SN, "workMode": "0", "workGroups": []}


async def test_work_mode_select_reverts_on_failure(hass: HomeAssistant, init_integration, mock_api) -> None:
    eid = entity_id(hass, init_integration, "select", "work_mode_select")
    before = hass.states.get(eid).state
    mock_api.clear_requests()
    mock_api.post(f"{URL_PREFIX}{TOU_PATH}", json={"code": "500", "data": None, "info": "Invalid Parameter"})
    with pytest.raises(HomeAssistantError):
        await _call(hass, "select", "select_option", eid, option="Backup")
    assert hass.states.get(eid).state == before


# ── Battery / peak / load settings ───────────────────────────────────────────

async def test_battery_setting_payload(hass: HomeAssistant, init_integration, mock_api) -> None:
    await _coordinator(hass, init_integration).async_set_battery_setting(
        {"on_grid_dod": 85, "off_grid_dod": 80, "charge_limit": 100, "off_grid_charge_limit": 95}
    )
    assert calls_to(mock_api, "/v2/SetBatterySetting")[-1] == {
        "deviceSn": DEVICE_SN,
        "onGridDischargeDod": "85",
        "offGridDischargeDod": "80",
        "batteryChargeLimit": "100",
        "batteryOffGridChargeLimit": "95",
    }


async def test_peak_control_payload(hass: HomeAssistant, init_integration, mock_api) -> None:
    await _coordinator(hass, init_integration).async_set_peak_control(
        {"enabled": True, "trigger_soc": 20, "start_time": "17:58", "end_time": "21:01", "power": 30}
    )
    assert calls_to(mock_api, "/v2/SetPeakControlSetting")[-1] == {
        "deviceSn": DEVICE_SN,
        # Spec p35 "0: Open / 1: Close" — 0 = enabled, the OPPOSITE of the TOU fields.
        # Confirmed on a Cygni 2026-10-25: 2.8.x sent "1" for enabled and the app showed Peak Control off.
        "peakControlEnable": "0",
        "triggerSoc": "20",
        "timeRange": "17:58-21:01",          # spec p35: HH:mm-HH:mm
        "peakControlPower": "30",
    }


@pytest.mark.parametrize(("enabled", "sent"), [(True, "0"), (False, "1")])
async def test_peak_control_enable_uses_open_close(hass: HomeAssistant, init_integration, mock_api, enabled, sent) -> None:
    await _coordinator(hass, init_integration).async_set_peak_control(
        {"enabled": enabled, "trigger_soc": 20, "start_time": "17:58", "end_time": "21:01", "power": 30}
    )
    assert calls_to(mock_api, "/v2/SetPeakControlSetting")[-1]["peakControlEnable"] == sent


async def test_load_control_payload(hass: HomeAssistant, init_integration, mock_api) -> None:
    await _coordinator(hass, init_integration).async_set_load_control({
        "load_switch": True, "force_close_start": "22:00", "force_close_end": "06:00",
        "force_close_off_grid_only": False, "always_close_on_grid": True,
        "relay_close_soc": 80, "relay_open_soc": 20, "pv_power_threshold": 500,
    })
    assert calls_to(mock_api, "/v2/SetLoadControlSetting")[-1] == {
        "deviceSn": DEVICE_SN,
        # Spec p33 "0: Open / 1: Close", "when turned on …" — same convention as Peak Control
        # (0 = on). UNVERIFIED on hardware; follows the spec because peakControlEnable proved it right.
        "loadSwitch": "0",
        "forceCloseTime": "22:00-06:00",
        "forceCloseOffGridOnly": "1",
        "alwaysCloseOnGridMode": "0",
        "relayCloseSoc": "80",
        "relayOpenSoc": "20",
        "pvPowerThreshold": "500",
    }


# ── Stage → Confirm through the UI entities ──────────────────────────────────

async def _call(hass, domain, service, eid, **data) -> None:
    await hass.services.async_call(domain, service, {"entity_id": eid, **data}, blocking=True)


async def _press(hass, entry, suffix) -> None:
    await _call(hass, "button", "press", entity_id(hass, entry, "button", suffix))


async def test_tou_stage_then_confirm(hass: HomeAssistant, init_integration, mock_api) -> None:
    entry = init_integration
    await _call(hass, "switch", "turn_on", entity_id(hass, entry, "switch", "tou_g1_enabled"))
    await _call(hass, "time", "set_value", entity_id(hass, entry, "time", "tou_g1_start"), time="12:02:00")
    await _call(hass, "time", "set_value", entity_id(hass, entry, "time", "tou_g1_end"), time="14:58:00")
    await _call(hass, "number", "set_value", entity_id(hass, entry, "number", "tou_g1_power"), value=9000)
    await _call(hass, "select", "select_option", entity_id(hass, entry, "select", "tou_g1_days"), option="Weekdays")

    with patch("custom_components.dyness_battery_cygni.tou_entities.pn_async_create") as notify:
        await _press(hass, entry, "tou_stage_button")
    assert notify.call_args.kwargs["notification_id"] == "dyness_tou_stage"
    assert not calls_to(mock_api, TOU_PATH), "staging must never write to the inverter"

    with patch("custom_components.dyness_battery_cygni.tou_entities.pn_async_create"), \
         patch("custom_components.dyness_battery_cygni.tou_entities.pn_async_dismiss") as dismiss:
        await _press(hass, entry, "tou_confirm_button")
    dismiss.assert_called_once()

    sent = calls_to(mock_api, TOU_PATH)
    assert len(sent) == 1
    groups = sent[0]["workGroups"]
    assert len(groups) == 4, "every TOU write sends all four groups"
    assert groups[0] == {
        "batteryWorkGroup": 1, "state": "1", "startTime": "12:02", "endTime": "14:58",
        "power": "9000", "mode": "0", "week": "0,1,2,3,4",
        "dodState": "1", "dod": "20", "socMaxChargeState": "1", "batterySocMaxCharge": "100",
    }
    # Untouched groups go out disabled with the entity defaults.
    assert [g["state"] for g in groups[1:]] == ["0", "0", "0"]
    assert groups[1]["startTime"] == "22:00" and groups[1]["endTime"] == "07:00"


async def test_tou_confirm_without_stage_refuses(hass: HomeAssistant, init_integration, mock_api) -> None:
    with pytest.raises(HomeAssistantError, match="No staged TOU changes"):
        await _press(hass, init_integration, "tou_confirm_button")
    assert not calls_to(mock_api, TOU_PATH)


async def test_tou_confirm_is_single_use(hass: HomeAssistant, init_integration, mock_api) -> None:
    with patch("custom_components.dyness_battery_cygni.tou_entities.pn_async_create"), \
         patch("custom_components.dyness_battery_cygni.tou_entities.pn_async_dismiss"):
        await _press(hass, init_integration, "tou_stage_button")
        await _press(hass, init_integration, "tou_confirm_button")
        with pytest.raises(HomeAssistantError, match="No staged TOU changes"):
            await _press(hass, init_integration, "tou_confirm_button")
    assert len(calls_to(mock_api, TOU_PATH)) == 1


async def test_tou_stage_rejects_enabled_group_with_zero_power(hass: HomeAssistant, init_integration) -> None:
    entry = init_integration
    await _call(hass, "switch", "turn_on", entity_id(hass, entry, "switch", "tou_g2_enabled"))
    await _call(hass, "number", "set_value", entity_id(hass, entry, "number", "tou_g2_power"), value=0)
    with pytest.raises(HomeAssistantError, match="Group 2: power must be > 0"):
        await _press(hass, entry, "tou_stage_button")


async def test_tou_stage_rejects_equal_start_and_end(hass: HomeAssistant, init_integration) -> None:
    entry = init_integration
    await _call(hass, "switch", "turn_on", entity_id(hass, entry, "switch", "tou_g3_enabled"))
    await _call(hass, "time", "set_value", entity_id(hass, entry, "time", "tou_g3_end"), time="22:00:00")
    with pytest.raises(HomeAssistantError, match="Group 3: start and end time cannot be the same"):
        await _press(hass, entry, "tou_stage_button")


async def test_battery_stage_then_confirm(hass: HomeAssistant, init_integration, mock_api) -> None:
    entry = init_integration
    await _call(hass, "number", "set_value", entity_id(hass, entry, "number", "battery_on_grid_dod"), value=85)
    with patch("custom_components.dyness_battery_cygni.settings_entities.pn_async_create"), \
         patch("custom_components.dyness_battery_cygni.settings_entities.pn_async_dismiss"):
        await _press(hass, entry, "battery_stage_button")
        assert not calls_to(mock_api, "/v2/SetBatterySetting")
        await _press(hass, entry, "battery_confirm_button")
    assert calls_to(mock_api, "/v2/SetBatterySetting") == [{
        "deviceSn": DEVICE_SN, "onGridDischargeDod": "85", "offGridDischargeDod": "20",
        "batteryChargeLimit": "100", "batteryOffGridChargeLimit": "100",
    }]


async def test_peak_stage_blocked_while_not_supported(hass: HomeAssistant, init_integration, mock_api) -> None:
    with pytest.raises(HomeAssistantError, match="Peak Control is marked as not supported"):
        await _press(hass, init_integration, "peak_stage_button")


async def test_peak_stage_then_confirm(hass: HomeAssistant, init_integration, mock_api) -> None:
    entry = init_integration
    await _call(hass, "switch", "turn_on", entity_id(hass, entry, "switch", "peak_supported"))
    await _call(hass, "switch", "turn_on", entity_id(hass, entry, "switch", "peak_enabled"))
    await _call(hass, "time", "set_value", entity_id(hass, entry, "time", "peak_start"), time="17:58:00")
    await _call(hass, "time", "set_value", entity_id(hass, entry, "time", "peak_end"), time="21:01:00")
    with patch("custom_components.dyness_battery_cygni.settings_entities.pn_async_create"), \
         patch("custom_components.dyness_battery_cygni.settings_entities.pn_async_dismiss"):
        await _press(hass, entry, "peak_stage_button")
        await _press(hass, entry, "peak_confirm_button")
    assert calls_to(mock_api, "/v2/SetPeakControlSetting") == [{
        "deviceSn": DEVICE_SN, "peakControlEnable": "0", "triggerSoc": "50",
        "timeRange": "17:58-21:01", "peakControlPower": "1000",
    }]


async def test_load_stage_blocked_while_not_supported(hass: HomeAssistant, init_integration) -> None:
    with pytest.raises(HomeAssistantError, match="Load Control is marked as not supported"):
        await _press(hass, init_integration, "load_stage_button")


async def test_peak_power_number_reaches_payload(hass: HomeAssistant, init_integration, mock_api) -> None:
    """Changing Peak Control Power in the UI must change what Confirm sends."""
    entry = init_integration
    await _call(hass, "switch", "turn_on", entity_id(hass, entry, "switch", "peak_supported"))
    await _call(hass, "number", "set_value", entity_id(hass, entry, "number", "peak_power"), value=30)
    with patch("custom_components.dyness_battery_cygni.settings_entities.pn_async_create"), \
         patch("custom_components.dyness_battery_cygni.settings_entities.pn_async_dismiss"):
        await _press(hass, entry, "peak_stage_button")
        await _press(hass, entry, "peak_confirm_button")
    assert calls_to(mock_api, "/v2/SetPeakControlSetting")[-1]["peakControlPower"] == "30"


async def test_load_stage_rejects_open_soc_not_below_close(hass: HomeAssistant, init_integration) -> None:
    entry = init_integration
    await _call(hass, "switch", "turn_on", entity_id(hass, entry, "switch", "load_supported"))
    await _call(hass, "number", "set_value", entity_id(hass, entry, "number", "load_relay_open_soc"), value=80)
    with pytest.raises(HomeAssistantError, match="must be lower than Relay Close SOC"):
        await _press(hass, entry, "load_stage_button")


# ── Service ──────────────────────────────────────────────────────────────────

async def test_set_tou_schedule_service(hass: HomeAssistant, init_integration, mock_api) -> None:
    await hass.services.async_call(DOMAIN, "set_tou_schedule", {"groups": [_group(group=4, power=8000)]}, blocking=True)
    group = calls_to(mock_api, TOU_PATH)[-1]["workGroups"][0]
    assert (group["batteryWorkGroup"], group["power"]) == (4, "8000")


@pytest.mark.parametrize("bad", ["22:00:00", "7:00", "24:00", "12:60", ""])
async def test_set_tou_schedule_service_rejects_non_hhmm_time(
    hass: HomeAssistant, init_integration, mock_api, bad: str
) -> None:
    with pytest.raises(HomeAssistantError, match="must be HH:mm"):
        await hass.services.async_call(
            DOMAIN, "set_tou_schedule", {"groups": [_group(start_time=bad)]}, blocking=True
        )
    assert not calls_to(mock_api, TOU_PATH)


async def test_service_registered_without_entries(hass: HomeAssistant) -> None:
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "set_tou_schedule")
    with pytest.raises(ServiceValidationError, match="No Dyness device is loaded"):
        await hass.services.async_call(DOMAIN, "set_tou_schedule", {"groups": [_group()]}, blocking=True)


async def test_service_picks_device_by_config_entry(hass: HomeAssistant, init_integration, mock_api) -> None:
    second = MockConfigEntry(domain=DOMAIN, title="Shed", unique_id="test_api_id_FAKESN000000000000002",
                             data={**init_integration.data, "device_sn": "FAKESN000000000000002"})
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError, match="pass config_entry_id"):
        await hass.services.async_call(DOMAIN, "set_tou_schedule", {"groups": [_group()]}, blocking=True)
    with pytest.raises(ServiceValidationError, match="No loaded Dyness device"):
        await hass.services.async_call(
            DOMAIN, "set_tou_schedule", {"config_entry_id": "nope", "groups": [_group()]}, blocking=True
        )

    await hass.services.async_call(
        DOMAIN, "set_tou_schedule", {"config_entry_id": second.entry_id, "groups": [_group()]}, blocking=True
    )
    assert calls_to(mock_api, TOU_PATH)[-1]["deviceSn"] == "FAKESN000000000000002"
