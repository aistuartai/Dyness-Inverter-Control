"""Parsing for every supported model family, driven by synthetic data.

These pin current behaviour (state snapshot per model) plus the capacity and
module rules each schema is documented to follow. See tests/synthetic.py for
why the data is synthetic.
"""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service
from syrupy.assertion import SnapshotAssertion

from custom_components.dyness_battery_cygni import DOMAIN

from .synthetic import MODELS, SyntheticDevice


async def _setup(hass: HomeAssistant, aioclient_mock, device: SyntheticDevice) -> MockConfigEntry:
    # Alarm handling calls persistent_notification services, which real HA always has.
    if not hass.services.has_service("persistent_notification", "create"):
        assert await async_setup_component(hass, "persistent_notification", {})
    device.register(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test Station", unique_id=f"id_{device.master_sn}",
        data={"api_id": "id", "api_secret": "secret", "api_base": "https://apacopen-api.dyness.com",
              "device_sn": device.master_sn, "dongle_sn": None},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _data(hass: HomeAssistant, entry) -> dict:
    return entry.runtime_data.data


@pytest.mark.parametrize("model", sorted(MODELS))
async def test_model_state_snapshot(
    hass: HomeAssistant, aioclient_mock, snapshot: SnapshotAssertion, model: str
) -> None:
    entry = await _setup(hass, aioclient_mock, MODELS[model]())
    states = {}
    for e in sorted(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id), key=lambda e: e.entity_id):
        if e.domain != "sensor":
            continue
        st = hass.states.get(e.entity_id)
        states[e.entity_id] = None if st is None else st.state
    assert states == snapshot


@pytest.mark.parametrize("model", sorted(MODELS))
async def test_model_coordinator_data_snapshot(
    hass: HomeAssistant, aioclient_mock, snapshot: SnapshotAssertion, model: str
) -> None:
    """The full parsed dict, including keys no entity exposes — guards the parser refactor."""
    entry = await _setup(hass, aioclient_mock, MODELS[model]())
    assert _data(hass, entry) == snapshot


@pytest.mark.parametrize(("model", "schema"), [
    ("tower_t14", "tower"), ("tower_pro_tp7", "tower"), ("stack100", "stack100"), ("dl5", "dl5"),
    ("junior_box", "junior"), ("powerbox_pro", "powerbox_pro"), ("powerbox_g2", "powerbox_g2"),
    ("powerdepot_g2", "powerdepot"), ("unknown", "unknown"),
])
async def test_schema_detected(hass: HomeAssistant, aioclient_mock, model: str, schema: str) -> None:
    entry = await _setup(hass, aioclient_mock, MODELS[model]())
    assert _data(hass, entry)["_schema"] == schema


async def test_stack100_capacity_from_bms_point(hass: HomeAssistant, aioclient_mock) -> None:
    # Point 1700 beats station/info (stays right after a module expansion).
    data = _data(hass, await _setup(hass, aioclient_mock, MODELS["stack100"]()))
    assert data["batteryCapacity"] == 81.9
    assert (data["usableKwh"], data["remainingKwh"]) == (81.9, 60.5)
    assert data["balancingStatus"] is True


async def test_stack100_modules_parsed(hass: HomeAssistant, aioclient_mock) -> None:
    data = _data(hass, await _setup(hass, aioclient_mock, MODELS["stack100"]()))
    assert data["moduleCount"] == 2
    module = data["module_data"]["FAKESTK0001-M1"]
    assert module["cell_count"] == 16
    assert (module["cell_voltage_max"], module["cell_voltage_min"]) == (3.34, 3.30)
    assert module["cell_voltage_spread_mv"] == 40.0


async def test_tp7_modules_have_30_cells(hass: HomeAssistant, aioclient_mock) -> None:
    data = _data(hass, await _setup(hass, aioclient_mock, MODELS["tower_pro_tp7"]()))
    module = data["module_data"]["FAKETP70001-M1"]
    assert module["is_tp7"] is True
    assert module["cell_30"] == 3.34
    assert data["alarmTotal"] == "0"


async def test_dl5_capacity_scales_with_modules(hass: HomeAssistant, aioclient_mock) -> None:
    data = _data(hass, await _setup(hass, aioclient_mock, MODELS["dl5"]()))
    assert data["batteryCapacity"] == 10.24           # station 5.12 × 2 sub-modules
    assert data["chargeCurrentLimit"] == 100.0        # only trusted with sub-modules present


async def test_junior_box_ignores_current_limits(hass: HomeAssistant, aioclient_mock) -> None:
    data = _data(hass, await _setup(hass, aioclient_mock, MODELS["junior_box"]()))
    assert "chargeCurrentLimit" not in data           # Junior Box reports unreliable limits
    assert data["balancingStatus"] is True


async def test_powerbox_pro_uses_module_amp_hours(hass: HomeAssistant, aioclient_mock) -> None:
    data = _data(hass, await _setup(hass, aioclient_mock, MODELS["powerbox_pro"]()))
    module = data["module_data"]["FAKEPBP0001-M1"]
    assert (module["remain_ah"], module["total_ah"]) == (132.0, 200.0)
    assert "soc" not in module                        # 14000 > 100 is Ah, not SOC
    # Two modules × 200 Ah × 52.1 V
    assert data["usableKwh"] == pytest.approx(20.84)


async def test_powerbox_g2_cells_and_firmware(hass: HomeAssistant, aioclient_mock) -> None:
    data = _data(hass, await _setup(hass, aioclient_mock, MODELS["powerbox_g2"]()))
    assert (data["cellVoltageMax"], data["cellVoltageMin"]) == (3.34, 3.30)
    assert (data["tempMax"], data["tempMin"]) == (26.1, 25.0)


async def test_powerdepot_fault_without_alarms_shows_standby(hass: HomeAssistant, aioclient_mock) -> None:
    data = _data(hass, await _setup(hass, aioclient_mock, MODELS["powerdepot_g2"]()))
    assert data["batteryCapacity"] == 15.36           # station 5.12 × point 400 (3 modules)
    assert data["workStatus"] == "Standby"


async def test_alarm_notification_raised_then_cleared(hass: HomeAssistant, aioclient_mock) -> None:
    create = async_mock_service(hass, "persistent_notification", "create")
    dismiss = async_mock_service(hass, "persistent_notification", "dismiss")
    device = MODELS["tower_t14"]()
    entry = await _setup(hass, aioclient_mock, device)

    assert _data(hass, entry)["alarmText"] == "Temperature spread alarm"
    assert len(create) == 1
    assert create[0].data["notification_id"] == "dyness_alarm_FAKETWR0001"

    device.master_points["5002"] = "0"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _data(hass, entry)["alarmText"] == "OK"
    assert len(dismiss) == 1
