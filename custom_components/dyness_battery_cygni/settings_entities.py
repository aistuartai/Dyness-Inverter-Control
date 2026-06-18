"""Battery, Peak Control, Load Control and Generator settings entities.

All entities register on coordinator._settings_entity_refs so stage buttons
can read values at press time without tight coupling.

Boolean on/off fields sent to the API use 1=On 0=Off (actual behaviour,
opposite of what the PDF documents).
"""
from __future__ import annotations

import logging
from datetime import time as dt_time

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
    async_dismiss as pn_async_dismiss,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

_NOTIFICATION_BATTERY  = "dyness_battery_settings_stage"
_NOTIFICATION_PEAK     = "dyness_peak_settings_stage"
_NOTIFICATION_LOAD     = "dyness_load_settings_stage"
_NOTIFICATION_APPLIED  = "dyness_settings_applied"


def _ensure_refs(coordinator) -> None:
    if not hasattr(coordinator, "_settings_entity_refs"):
        coordinator._settings_entity_refs = {}


# ── Base ──────────────────────────────────────────────────────────────────────

class _SettingsBase(CoordinatorEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry, uid_suffix: str, ref_key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._ref_key = ref_key
        self._attr_unique_id = f"{entry.entry_id}_{uid_suffix}"

    @property
    def device_info(self):
        di = self.coordinator.device_info
        return {
            "identifiers": {(DOMAIN, self.coordinator.device_sn)},
            "name": di.get("stationName", "Dyness Battery"),
            "manufacturer": "Dyness",
            "model": di.get("deviceModelName", "Dyness Battery"),
            "sw_version": di.get("firmwareVersion"),
        }

    async def _register_ref(self) -> None:
        _ensure_refs(self.coordinator)
        self.coordinator._settings_entity_refs[self._ref_key] = self


# ── Generic switch ────────────────────────────────────────────────────────────

class _SettingsSwitch(_SettingsBase, SwitchEntity):
    def __init__(self, coordinator, entry, uid_suffix, ref_key, default_on, icon, tk) -> None:
        super().__init__(coordinator, entry, uid_suffix, ref_key)
        self._attr_translation_key = tk
        self._attr_icon = icon
        self._is_on: bool = default_on

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_turn_on(self, **kwargs) -> None:
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._register_ref()
        state = await self.async_get_last_state()
        if state:
            self._is_on = state.state == "on"


# ── Generic number ────────────────────────────────────────────────────────────

class _SettingsNumber(_SettingsBase, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator, entry, uid_suffix, ref_key,
        min_v, max_v, step, unit, icon, tk, default
    ) -> None:
        super().__init__(coordinator, entry, uid_suffix, ref_key)
        self._attr_translation_key = tk
        self._attr_native_min_value = min_v
        self._attr_native_max_value = max_v
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._value: float = float(default)

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._register_ref()
        state = await self.async_get_last_state()
        if state and state.state not in ("unknown", "unavailable"):
            try:
                self._value = float(state.state)
            except ValueError:
                pass


# ── Generic time ──────────────────────────────────────────────────────────────

class _SettingsTime(_SettingsBase, TimeEntity):
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry, uid_suffix, ref_key, default: dt_time, tk) -> None:
        super().__init__(coordinator, entry, uid_suffix, ref_key)
        self._attr_translation_key = tk
        self._value: dt_time = default

    @property
    def native_value(self) -> dt_time:
        return self._value

    async def async_set_value(self, value: dt_time) -> None:
        self._value = value
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._register_ref()
        state = await self.async_get_last_state()
        if state and state.state not in ("unknown", "unavailable"):
            try:
                parts = state.state.split(":")
                self._value = dt_time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                pass


# ── Battery setting entities ──────────────────────────────────────────────────

def make_battery_entities(coordinator, entry):
    return [
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="battery_on_grid_dod", ref_key="battery_on_grid_dod",
            min_v=0, max_v=95, step=5, unit="%",
            icon="mdi:battery-arrow-down-outline",
            tk="battery_on_grid_dod", default=20,
        ),
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="battery_off_grid_dod", ref_key="battery_off_grid_dod",
            min_v=0, max_v=95, step=5, unit="%",
            icon="mdi:battery-arrow-down-outline",
            tk="battery_off_grid_dod", default=20,
        ),
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="battery_charge_limit", ref_key="battery_charge_limit",
            min_v=10, max_v=100, step=5, unit="%",
            icon="mdi:battery-arrow-up-outline",
            tk="battery_charge_limit", default=100,
        ),
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="battery_off_grid_charge_limit", ref_key="battery_off_grid_charge_limit",
            min_v=10, max_v=100, step=5, unit="%",
            icon="mdi:battery-arrow-up-outline",
            tk="battery_off_grid_charge_limit", default=100,
        ),
    ]


# ── Peak control entities ─────────────────────────────────────────────────────

def make_peak_entities(coordinator, entry):
    return [
        _SettingsSwitch(
            coordinator, entry,
            uid_suffix="peak_supported", ref_key="peak_supported",
            default_on=False, icon="mdi:help-circle-outline",
            tk="peak_supported",
        ),
        _SettingsSwitch(
            coordinator, entry,
            uid_suffix="peak_enabled", ref_key="peak_enabled",
            default_on=False, icon="mdi:chart-bell-curve",
            tk="peak_enabled",
        ),
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="peak_trigger_soc", ref_key="peak_trigger_soc",
            min_v=0, max_v=100, step=5, unit="%",
            icon="mdi:battery-50",
            tk="peak_trigger_soc", default=50,
        ),
        _SettingsTime(
            coordinator, entry,
            uid_suffix="peak_start", ref_key="peak_start",
            default=dt_time(16, 0), tk="peak_start",
        ),
        _SettingsTime(
            coordinator, entry,
            uid_suffix="peak_end", ref_key="peak_end",
            default=dt_time(21, 0), tk="peak_end",
        ),
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="peak_power", ref_key="peak_power",
            min_v=0, max_v=10000, step=100, unit=UnitOfPower.WATT,
            icon="mdi:lightning-bolt",
            tk="peak_power", default=1000,
        ),
    ]


# ── Load control entities ─────────────────────────────────────────────────────

def make_load_entities(coordinator, entry):
    return [
        _SettingsSwitch(
            coordinator, entry,
            uid_suffix="load_supported", ref_key="load_supported",
            default_on=False, icon="mdi:help-circle-outline",
            tk="load_supported",
        ),
        _SettingsSwitch(
            coordinator, entry,
            uid_suffix="load_switch", ref_key="load_switch",
            default_on=False, icon="mdi:electric-switch",
            tk="load_switch",
        ),
        _SettingsTime(
            coordinator, entry,
            uid_suffix="load_force_close_start", ref_key="load_force_close_start",
            default=dt_time(22, 0), tk="load_force_close_start",
        ),
        _SettingsTime(
            coordinator, entry,
            uid_suffix="load_force_close_end", ref_key="load_force_close_end",
            default=dt_time(6, 0), tk="load_force_close_end",
        ),
        _SettingsSwitch(
            coordinator, entry,
            uid_suffix="load_force_off_grid_only", ref_key="load_force_off_grid_only",
            default_on=False, icon="mdi:transmission-tower-off",
            tk="load_force_off_grid_only",
        ),
        _SettingsSwitch(
            coordinator, entry,
            uid_suffix="load_always_close_on_grid", ref_key="load_always_close_on_grid",
            default_on=False, icon="mdi:transmission-tower",
            tk="load_always_close_on_grid",
        ),
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="load_relay_close_soc", ref_key="load_relay_close_soc",
            min_v=0, max_v=100, step=5, unit="%",
            icon="mdi:electric-switch-closed",
            tk="load_relay_close_soc", default=80,
        ),
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="load_relay_open_soc", ref_key="load_relay_open_soc",
            min_v=0, max_v=100, step=5, unit="%",
            icon="mdi:electric-switch",
            tk="load_relay_open_soc", default=20,
        ),
        _SettingsNumber(
            coordinator, entry,
            uid_suffix="load_pv_threshold", ref_key="load_pv_threshold",
            min_v=0, max_v=10000, step=100, unit=UnitOfPower.WATT,
            icon="mdi:solar-power",
            tk="load_pv_threshold", default=500,
        ),
    ]


# ── Generator feature switch (future) ────────────────────────────────────────

def make_generator_entities(coordinator, entry):
    return [
        _SettingsSwitch(
            coordinator, entry,
            uid_suffix="generator_connected", ref_key="generator_connected",
            default_on=False, icon="mdi:engine",
            tk="generator_connected",
        ),
    ]


# ── Notification formatters ───────────────────────────────────────────────────

def _fmt_battery(refs) -> str:
    lines = [
        "**Proposed Battery Settings**", "",
        f"  • On-Grid Discharge Depth: {int(refs['battery_on_grid_dod'].native_value)}%",
        f"  • Off-Grid Discharge Depth: {int(refs['battery_off_grid_dod'].native_value)}%",
        f"  • On-Grid Charge Limit: {int(refs['battery_charge_limit'].native_value)}%",
        f"  • Off-Grid Charge Limit: {int(refs['battery_off_grid_charge_limit'].native_value)}%",
        "",
        "Press **Confirm & Apply Battery Settings** to send to inverter.",
    ]
    return "\n".join(lines)


def _fmt_peak(refs) -> str:
    enabled = refs["peak_enabled"].is_on
    lines = [
        "**Proposed Peak Control Settings**", "",
        f"  • Peak Control: {'Enabled' if enabled else 'Disabled'}",
    ]
    if enabled:
        lines += [
            f"  • Trigger SOC: {int(refs['peak_trigger_soc'].native_value)}%",
            f"  • Time Window: {refs['peak_start'].native_value.strftime('%H:%M')} → {refs['peak_end'].native_value.strftime('%H:%M')}",
            f"  • Power Limit: {int(refs['peak_power'].native_value)} W",
        ]
    lines += ["", "Press **Confirm & Apply Peak Control** to send to inverter."]
    return "\n".join(lines)


def _fmt_load(refs) -> str:
    sw = refs["load_switch"].is_on
    lines = [
        "**Proposed Load Control Settings**", "",
        f"  • Load Switch: {'On' if sw else 'Off'}",
        f"  • Force Close Window: {refs['load_force_close_start'].native_value.strftime('%H:%M')} → {refs['load_force_close_end'].native_value.strftime('%H:%M')}",
        f"  • Force Close Off-Grid Only: {'Yes' if refs['load_force_off_grid_only'].is_on else 'No'}",
        f"  • Always Close On Grid: {'Yes' if refs['load_always_close_on_grid'].is_on else 'No'}",
        f"  • Relay Close SOC: {int(refs['load_relay_close_soc'].native_value)}%",
        f"  • Relay Open SOC: {int(refs['load_relay_open_soc'].native_value)}%",
        f"  • PV Power Threshold: {int(refs['load_pv_threshold'].native_value)} W",
        "",
        "Press **Confirm & Apply Load Control** to send to inverter.",
    ]
    return "\n".join(lines)


# ── Button base ───────────────────────────────────────────────────────────────

class _SettingsButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry, uid_suffix: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{uid_suffix}"

    @property
    def device_info(self):
        di = self.coordinator.device_info
        return {
            "identifiers": {(DOMAIN, self.coordinator.device_sn)},
            "name": di.get("stationName", "Dyness Battery"),
            "manufacturer": "Dyness",
            "model": di.get("deviceModelName", "Dyness Battery"),
            "sw_version": di.get("firmwareVersion"),
        }

    def _refs(self) -> dict:
        return getattr(self.coordinator, "_settings_entity_refs", {})


# ── Battery buttons ───────────────────────────────────────────────────────────

class BatteryStageButton(_SettingsButton):
    _attr_translation_key = "battery_stage_button"
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "battery_stage_button")

    async def async_press(self) -> None:
        refs = self._refs()
        required = ["battery_on_grid_dod", "battery_off_grid_dod",
                    "battery_charge_limit", "battery_off_grid_charge_limit"]
        missing = [k for k in required if k not in refs]
        if missing:
            raise HomeAssistantError(f"Battery setting entities not ready: {missing}")

        staged = {
            "on_grid_dod":            int(refs["battery_on_grid_dod"].native_value),
            "off_grid_dod":           int(refs["battery_off_grid_dod"].native_value),
            "charge_limit":           int(refs["battery_charge_limit"].native_value),
            "off_grid_charge_limit":  int(refs["battery_off_grid_charge_limit"].native_value),
        }
        self.coordinator._staged_battery = staged
        _LOGGER.info("Dyness: battery settings staged")
        pn_async_create(
            self.hass,
            message=_fmt_battery(refs),
            title="Dyness Battery Settings — Review & Confirm",
            notification_id=_NOTIFICATION_BATTERY,
        )


class BatteryConfirmButton(_SettingsButton):
    _attr_translation_key = "battery_confirm_button"
    _attr_icon = "mdi:send-check"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "battery_confirm_button")

    async def async_press(self) -> None:
        staged = getattr(self.coordinator, "_staged_battery", None)
        if not staged:
            raise HomeAssistantError(
                "No staged battery settings. Press 'Stage Battery Settings' first."
            )
        await self.coordinator.async_set_battery_setting(staged)
        self.coordinator._staged_battery = None
        pn_async_dismiss(self.hass, notification_id=_NOTIFICATION_BATTERY)
        pn_async_create(
            self.hass,
            message="Battery settings successfully applied to inverter.",
            title="Dyness Battery Settings — Applied ✓",
            notification_id=_NOTIFICATION_APPLIED,
        )


# ── Peak control buttons ──────────────────────────────────────────────────────

class PeakStageButton(_SettingsButton):
    _attr_translation_key = "peak_stage_button"
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "peak_stage_button")

    async def async_press(self) -> None:
        refs = self._refs()
        if not refs.get("peak_supported", None) or not refs["peak_supported"].is_on:
            raise HomeAssistantError(
                "Peak Control is marked as not supported on this device. "
                "Turn on 'Peak Control Supported' if your inverter supports this feature."
            )
        required = ["peak_enabled", "peak_trigger_soc", "peak_start", "peak_end", "peak_power"]
        missing = [k for k in required if k not in refs]
        if missing:
            raise HomeAssistantError(f"Peak control entities not ready: {missing}")

        start = refs["peak_start"].native_value
        end   = refs["peak_end"].native_value
        if start == end:
            raise HomeAssistantError("Peak control start and end time cannot be the same")

        staged = {
            "enabled":     refs["peak_enabled"].is_on,
            "trigger_soc": int(refs["peak_trigger_soc"].native_value),
            "start_time":  start.strftime("%H:%M"),
            "end_time":    end.strftime("%H:%M"),
            "power":       int(refs["peak_power"].native_value),
        }
        self.coordinator._staged_peak = staged
        _LOGGER.info("Dyness: peak control settings staged")
        pn_async_create(
            self.hass,
            message=_fmt_peak(refs),
            title="Dyness Peak Control — Review & Confirm",
            notification_id=_NOTIFICATION_PEAK,
        )


class PeakConfirmButton(_SettingsButton):
    _attr_translation_key = "peak_confirm_button"
    _attr_icon = "mdi:send-check"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "peak_confirm_button")

    async def async_press(self) -> None:
        staged = getattr(self.coordinator, "_staged_peak", None)
        if not staged:
            raise HomeAssistantError(
                "No staged peak control settings. Press 'Stage Peak Control' first."
            )
        await self.coordinator.async_set_peak_control(staged)
        self.coordinator._staged_peak = None
        pn_async_dismiss(self.hass, notification_id=_NOTIFICATION_PEAK)
        pn_async_create(
            self.hass,
            message="Peak control settings successfully applied to inverter.",
            title="Dyness Peak Control — Applied ✓",
            notification_id=_NOTIFICATION_APPLIED,
        )


# ── Load control buttons ──────────────────────────────────────────────────────

class LoadStageButton(_SettingsButton):
    _attr_translation_key = "load_stage_button"
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "load_stage_button")

    async def async_press(self) -> None:
        refs = self._refs()
        if not refs.get("load_supported", None) or not refs["load_supported"].is_on:
            raise HomeAssistantError(
                "Load Control is marked as not supported. "
                "Turn on 'Load Control Supported' if your inverter has a relay output wired."
            )
        required = [
            "load_switch", "load_force_close_start", "load_force_close_end",
            "load_force_off_grid_only", "load_always_close_on_grid",
            "load_relay_close_soc", "load_relay_open_soc", "load_pv_threshold",
        ]
        missing = [k for k in required if k not in refs]
        if missing:
            raise HomeAssistantError(f"Load control entities not ready: {missing}")

        close_soc = int(refs["load_relay_close_soc"].native_value)
        open_soc  = int(refs["load_relay_open_soc"].native_value)
        if open_soc >= close_soc:
            raise HomeAssistantError(
                f"Relay Open SOC ({open_soc}%) must be lower than Relay Close SOC ({close_soc}%)"
            )

        staged = {
            "load_switch":             refs["load_switch"].is_on,
            "force_close_start":       refs["load_force_close_start"].native_value.strftime("%H:%M"),
            "force_close_end":         refs["load_force_close_end"].native_value.strftime("%H:%M"),
            "force_close_off_grid_only": refs["load_force_off_grid_only"].is_on,
            "always_close_on_grid":    refs["load_always_close_on_grid"].is_on,
            "relay_close_soc":         close_soc,
            "relay_open_soc":          open_soc,
            "pv_power_threshold":      int(refs["load_pv_threshold"].native_value),
        }
        self.coordinator._staged_load = staged
        _LOGGER.info("Dyness: load control settings staged")
        pn_async_create(
            self.hass,
            message=_fmt_load(refs),
            title="Dyness Load Control — Review & Confirm",
            notification_id=_NOTIFICATION_LOAD,
        )


class LoadConfirmButton(_SettingsButton):
    _attr_translation_key = "load_confirm_button"
    _attr_icon = "mdi:send-check"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "load_confirm_button")

    async def async_press(self) -> None:
        staged = getattr(self.coordinator, "_staged_load", None)
        if not staged:
            raise HomeAssistantError(
                "No staged load control settings. Press 'Stage Load Control' first."
            )
        await self.coordinator.async_set_load_control(staged)
        self.coordinator._staged_load = None
        pn_async_dismiss(self.hass, notification_id=_NOTIFICATION_LOAD)
        pn_async_create(
            self.hass,
            message="Load control settings successfully applied to inverter.",
            title="Dyness Load Control — Applied ✓",
            notification_id=_NOTIFICATION_APPLIED,
        )
