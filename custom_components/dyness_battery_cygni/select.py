"""Select entities for Dyness Battery - Cygni Control."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Maps display label → API value sent to SetWorkModeSetting
# Off-grid (mode 1) deliberately excluded — must be set on the inverter directly
WORK_MODE_OPTIONS: dict[str, str] = {
    "Self-use":          "0",
    "Backup":            "2",
    "TOU (Time of Use)": "3",
}

# Reverse map: API value → display label (for reading current state)
WORK_MODE_LABELS: dict[str, str] = {v: k for k, v in WORK_MODE_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DynessWorkModeSelect(coordinator, entry)])


class DynessWorkModeSelect(CoordinatorEntity, SelectEntity):
    """Work mode selector for the Dyness Cygni inverter."""

    _attr_has_entity_name = True
    _attr_translation_key = "work_mode_select"
    _attr_icon = "mdi:battery-sync"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(WORK_MODE_OPTIONS.keys())

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_work_mode_select"

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

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.current_option is not None

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        raw = data.get("runModel")
        if raw is None:
            return None
        # runModel is already mapped to a label string by __init__.py
        # but if it comes through as a numeric string, map it
        if raw in WORK_MODE_LABELS:
            return WORK_MODE_LABELS[raw]
        # Already a label
        if raw in WORK_MODE_OPTIONS:
            return raw
        return None

    async def async_select_option(self, option: str) -> None:
        """Called when the user picks a new option in the UI."""
        if option not in WORK_MODE_OPTIONS:
            raise ValueError(f"Invalid work mode option: {option}")
        api_value = WORK_MODE_OPTIONS[option]
        await self.coordinator.async_set_work_mode(api_value)
