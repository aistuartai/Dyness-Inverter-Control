"""TOU schedule configuration entities for Dyness Battery - Cygni Control.

Entities register themselves on coordinator._tou_entity_refs so the stage
button can read their values at press time without tight coupling.
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
from homeassistant.components.select import SelectEntity
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

# ── Days presets ──────────────────────────────────────────────────────────────
DAYS_OPTIONS: dict[str, str] = {
    "Every Day": "0,1,2,3,4,5,6",
    "Weekdays":  "0,1,2,3,4",
    "Weekends":  "5,6",
    "Monday":    "0",
    "Tuesday":   "1",
    "Wednesday": "2",
    "Thursday":  "3",
    "Friday":    "4",
    "Saturday":  "5",
    "Sunday":    "6",
}

MODE_OPTIONS: dict[str, str] = {
    "Charge":    "charge",
    "Discharge": "discharge",
}

_DEFAULT_START   = dt_time(22, 0)
_DEFAULT_END     = dt_time(7, 0)
_DEFAULT_POWER   = 500.0
_DEFAULT_DOD     = 20.0
_DEFAULT_SOC_MAX = 100.0
_DEFAULT_DAYS    = "Every Day"
_DEFAULT_MODE    = "Charge"

_NOTIFICATION_STAGE   = "dyness_tou_stage"
_NOTIFICATION_APPLIED = "dyness_tou_applied"


def _ensure_refs(coordinator) -> None:
    if not hasattr(coordinator, "_tou_entity_refs"):
        coordinator._tou_entity_refs = {}


# ── Base ──────────────────────────────────────────────────────────────────────

class _TouBase(CoordinatorEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator, entry: ConfigEntry, group: int, suffix: str, ref_key: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._group = group
        self._ref_key = ref_key
        self._attr_unique_id = f"{entry.entry_id}_tou_g{group}_{suffix}"

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
        self.coordinator._tou_entity_refs[self._ref_key] = self


# ── Switches ──────────────────────────────────────────────────────────────────

class _TouSwitch(_TouBase, SwitchEntity):
    def __init__(
        self, coordinator, entry, group, suffix, ref_key, default_on: bool, icon: str, tk: str
    ) -> None:
        super().__init__(coordinator, entry, group, suffix, ref_key)
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


def TouGroupSwitch(coordinator, entry, group: int) -> _TouSwitch:
    return _TouSwitch(
        coordinator, entry, group,
        suffix="enabled", ref_key=f"g{group}_enabled",
        default_on=False, icon="mdi:toggle-switch",
        tk=f"tou_g{group}_enabled",
    )


def TouDodSwitch(coordinator, entry, group: int) -> _TouSwitch:
    return _TouSwitch(
        coordinator, entry, group,
        suffix="dod_enabled", ref_key=f"g{group}_dod_enabled",
        default_on=True, icon="mdi:battery-arrow-down",
        tk=f"tou_g{group}_dod_enabled",
    )


def TouSocMaxSwitch(coordinator, entry, group: int) -> _TouSwitch:
    return _TouSwitch(
        coordinator, entry, group,
        suffix="soc_max_enabled", ref_key=f"g{group}_soc_max_enabled",
        default_on=True, icon="mdi:battery-arrow-up",
        tk=f"tou_g{group}_soc_max_enabled",
    )


# ── Time ──────────────────────────────────────────────────────────────────────

class TouTimeEntity(_TouBase, TimeEntity):
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry, group: int, is_start: bool) -> None:
        label = "start" if is_start else "end"
        super().__init__(
            coordinator, entry, group,
            suffix=label, ref_key=f"g{group}_{label}",
        )
        self._attr_translation_key = f"tou_g{group}_{label}"
        self._value: dt_time = _DEFAULT_START if is_start else _DEFAULT_END

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


# ── Selects ───────────────────────────────────────────────────────────────────

class TouModeSelect(_TouBase, SelectEntity):
    _attr_icon = "mdi:battery-charging"
    _attr_options = list(MODE_OPTIONS.keys())

    def __init__(self, coordinator, entry, group: int) -> None:
        super().__init__(coordinator, entry, group, "mode", f"g{group}_mode")
        self._attr_translation_key = f"tou_g{group}_mode"
        self._current: str = _DEFAULT_MODE

    @property
    def current_option(self) -> str:
        return self._current

    async def async_select_option(self, option: str) -> None:
        self._current = option
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._register_ref()
        state = await self.async_get_last_state()
        if state and state.state in self._attr_options:
            self._current = state.state


class TouDaysSelect(_TouBase, SelectEntity):
    _attr_icon = "mdi:calendar-week"
    _attr_options = list(DAYS_OPTIONS.keys())

    def __init__(self, coordinator, entry, group: int) -> None:
        super().__init__(coordinator, entry, group, "days", f"g{group}_days")
        self._attr_translation_key = f"tou_g{group}_days"
        self._current: str = _DEFAULT_DAYS

    @property
    def current_option(self) -> str:
        return self._current

    async def async_select_option(self, option: str) -> None:
        self._current = option
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._register_ref()
        state = await self.async_get_last_state()
        if state and state.state in self._attr_options:
            self._current = state.state


# ── Numbers ───────────────────────────────────────────────────────────────────

class _TouNumber(_TouBase, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator, entry, group, suffix, ref_key,
        min_v, max_v, step, unit, icon, tk, default
    ) -> None:
        super().__init__(coordinator, entry, group, suffix, ref_key)
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


def TouPowerNumber(coordinator, entry, group: int) -> _TouNumber:
    return _TouNumber(
        coordinator, entry, group,
        suffix="power", ref_key=f"g{group}_power",
        min_v=0, max_v=2000, step=100,
        unit=UnitOfPower.WATT, icon="mdi:lightning-bolt",
        tk=f"tou_g{group}_power", default=_DEFAULT_POWER,
    )


def TouDodNumber(coordinator, entry, group: int) -> _TouNumber:
    return _TouNumber(
        coordinator, entry, group,
        suffix="dod", ref_key=f"g{group}_dod",
        min_v=0, max_v=95, step=5,
        unit="%", icon="mdi:battery-low",
        tk=f"tou_g{group}_dod", default=_DEFAULT_DOD,
    )


def TouSocMaxNumber(coordinator, entry, group: int) -> _TouNumber:
    return _TouNumber(
        coordinator, entry, group,
        suffix="soc_max", ref_key=f"g{group}_soc_max",
        min_v=10, max_v=100, step=5,
        unit="%", icon="mdi:battery-high",
        tk=f"tou_g{group}_soc_max", default=_DEFAULT_SOC_MAX,
    )


# ── Notification helper ───────────────────────────────────────────────────────

def _format_tou_summary(groups: list[dict]) -> str:
    lines = ["**Proposed TOU Schedule**", ""]
    lines.append("⚠️ Applying will switch the inverter to **TOU mode**.")
    lines.append("")
    for g in groups:
        num = g["group"]
        enabled = g["enabled"]
        status = "✅ Enabled" if enabled else "⛔ Disabled"
        lines.append(f"**Group {num}** — {status}")
        if enabled:
            lines.append(
                f"  • Time: {g['start_time']} → {g['end_time']}  |  "
                f"Mode: {g['mode'].capitalize()}  |  Power: {g['power']} W"
            )
            lines.append(
                f"  • Days: {g['days_label']}  |  "
                f"DOD: {'on' if g['dod_enabled'] else 'off'} ({g['dod']}%)  |  "
                f"Max SOC: {'on' if g['soc_max_enabled'] else 'off'} ({g['soc_max']}%)"
            )
        else:
            lines.append("  • (skipped — group disabled)")
        lines.append("")
    lines.append("Press **Confirm & Apply TOU** to send to inverter, or")
    lines.append("press **Stage TOU Changes** again to update this preview.")
    return "\n".join(lines)


# ── Buttons ───────────────────────────────────────────────────────────────────

class TouStageButton(CoordinatorEntity, ButtonEntity):
    """Read all TOU entity states, validate, store snapshot, show notification."""

    _attr_has_entity_name = True
    _attr_translation_key = "tou_stage_button"
    _attr_icon = "mdi:clipboard-check-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tou_stage_button"

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

    async def async_press(self) -> None:
        refs = getattr(self.coordinator, "_tou_entity_refs", {})
        missing = [
            k for g in range(1, 5)
            for k in (
                f"g{g}_enabled", f"g{g}_dod_enabled", f"g{g}_soc_max_enabled",
                f"g{g}_start", f"g{g}_end", f"g{g}_mode", f"g{g}_days",
                f"g{g}_power", f"g{g}_dod", f"g{g}_soc_max",
            )
            if k not in refs
        ]
        if missing:
            raise HomeAssistantError(
                f"TOU entities not yet ready: {missing}. Restart Home Assistant."
            )

        groups = []
        for g in range(1, 5):
            enabled    = refs[f"g{g}_enabled"].is_on
            start      = refs[f"g{g}_start"].native_value
            end        = refs[f"g{g}_end"].native_value
            mode_label = refs[f"g{g}_mode"].current_option
            days_label = refs[f"g{g}_days"].current_option
            power      = int(refs[f"g{g}_power"].native_value)
            dod_en     = refs[f"g{g}_dod_enabled"].is_on
            dod        = int(refs[f"g{g}_dod"].native_value)
            soc_en     = refs[f"g{g}_soc_max_enabled"].is_on
            soc_max    = int(refs[f"g{g}_soc_max"].native_value)

            if enabled:
                if power == 0:
                    raise HomeAssistantError(
                        f"TOU Group {g}: power must be > 0 W when group is enabled"
                    )
                if start == end:
                    raise HomeAssistantError(
                        f"TOU Group {g}: start and end time cannot be the same"
                    )

            groups.append({
                "group":           g,
                "enabled":         enabled,
                "start_time":      start.strftime("%H:%M"),
                "end_time":        end.strftime("%H:%M"),
                "mode":            MODE_OPTIONS[mode_label],
                "days":            DAYS_OPTIONS[days_label],
                "days_label":      days_label,
                "power":           power,
                "dod_enabled":     dod_en,
                "dod":             dod,
                "soc_max_enabled": soc_en,
                "soc_max":         soc_max,
            })

        self.coordinator._staged_tou = groups
        _LOGGER.info("Dyness TOU: staged %d groups for review", len(groups))

        pn_async_create(
            self.hass,
            message=_format_tou_summary(groups),
            title="Dyness TOU — Review & Confirm",
            notification_id=_NOTIFICATION_STAGE,
        )


class TouConfirmButton(CoordinatorEntity, ButtonEntity):
    """Send staged TOU snapshot to the inverter."""

    _attr_has_entity_name = True
    _attr_translation_key = "tou_confirm_button"
    _attr_icon = "mdi:send-check"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tou_confirm_button"

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

    async def async_press(self) -> None:
        staged = getattr(self.coordinator, "_staged_tou", None)
        if not staged:
            raise HomeAssistantError(
                "No staged TOU changes. Press 'Stage TOU Changes' first to preview."
            )

        _LOGGER.info("Dyness TOU: confirmed — sending staged schedule to inverter")
        await self.coordinator.async_set_tou_schedule(staged)

        self.coordinator._staged_tou = None
        pn_async_dismiss(self.hass, notification_id=_NOTIFICATION_STAGE)
        pn_async_create(
            self.hass,
            message="TOU schedule successfully applied to inverter.",
            title="Dyness TOU — Applied ✓",
            notification_id=_NOTIFICATION_APPLIED,
        )
        _LOGGER.info("Dyness TOU: schedule applied successfully")
