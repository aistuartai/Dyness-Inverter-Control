"""Entity registry and sensor values for the real (scrubbed) Cygni capture.

The registry snapshot protects users' dashboards and automations: any renamed
entity id, entity moved between platforms, or missing sensor shows up as a
snapshot diff. Regenerate deliberate changes with ``pytest --snapshot-update``.
"""
from __future__ import annotations

from collections import Counter

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from syrupy.assertion import SnapshotAssertion

from .conftest import entity_id


def _registry(hass: HomeAssistant, entry) -> list:
    return sorted(
        er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id),
        key=lambda e: e.entity_id,
    )


def _suffix(entry, unique_id: str) -> str:
    return unique_id.removeprefix(f"{entry.entry_id}_")


async def test_entity_registry_snapshot(hass: HomeAssistant, init_integration, snapshot: SnapshotAssertion) -> None:
    entry = init_integration
    rows = [
        {
            "entity_id": e.entity_id,
            "unique_id": _suffix(entry, e.unique_id),
            "translation_key": e.translation_key,
            "category": e.entity_category,
            "disabled_by": e.disabled_by,
        }
        for e in _registry(hass, entry)
    ]
    assert rows == snapshot


async def test_state_snapshot(hass: HomeAssistant, init_integration, snapshot: SnapshotAssertion) -> None:
    """Every entity's state and unit for the captured data — the sensor parsing baseline."""
    states = {}
    for e in _registry(hass, init_integration):
        st = hass.states.get(e.entity_id)
        states[e.entity_id] = None if st is None else (st.state, st.attributes.get("unit_of_measurement"))
    assert states == snapshot


@pytest.mark.xfail(strict=True, reason="2.8.0 fix list: number.py/time.py add the same object to both platforms")
async def test_each_setting_lives_on_exactly_one_platform(hass: HomeAssistant, init_integration) -> None:
    per_uid = Counter(e.unique_id for e in _registry(hass, init_integration))
    assert [uid for uid, n in per_uid.items() if n > 1] == []


# ── Golden values (hardware-confirmed meanings) ──────────────────────────────

def _state(hass, entry, suffix: str) -> str:
    return hass.states.get(entity_id(hass, entry, "sensor", suffix)).state


async def test_v2_realtime_is_nested_and_flattened(hass: HomeAssistant, init_integration) -> None:
    # Spec p16-21 shows a flat payload; the real Cygni nests it under gridInfo/loadInfo/
    # batteryInfo/... (live capture 2026-10-22). Values below come from gridInfo/loadInfo.
    entry = init_integration
    assert _state(hass, entry, "apparentPower") == "719.0"
    assert _state(hass, entry, "inverterTotalPower") == "429.0"
    assert _state(hass, entry, "backupLoadPower") == "0.0"


async def test_battery_power_negative_while_discharging(hass: HomeAssistant, init_integration) -> None:
    # Cygni realtime point 172 reports discharge as a positive number (476 in the capture);
    # the integration flips it so negative = discharging, matching HA's battery convention.
    # (The v2 batteryInfo.batteryPower uses the raw positive-discharge sign.)
    entry = init_integration
    assert _state(hass, entry, "realTimePower") == "-476.0"
    assert _state(hass, entry, "batteryStatus") == "Discharging"


async def test_meter_type_two_is_smart_meter(hass: HomeAssistant, init_integration) -> None:
    assert _state(hass, init_integration, "meterType") == "Smart Meter"


@pytest.mark.xfail(strict=True, reason="2.8.0 fix list: realtime bmsCommunicationStatus 1 = Normal (spec p18, hardware)")
async def test_bms_communication_normal_on_healthy_battery(hass: HomeAssistant, init_integration) -> None:
    assert _state(hass, init_integration, "bmsCommunicationStatus") == "Normal"


@pytest.mark.xfail(strict=True, reason="2.8.0 fix list: module count must be clusters x modules (2 x 4 = 8)")
async def test_module_count_counts_every_cluster(hass: HomeAssistant, init_integration) -> None:
    assert _state(hass, init_integration, "batteryCount") == "8"
