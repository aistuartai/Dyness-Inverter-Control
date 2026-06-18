"""Button platform for Dyness Battery - Cygni Control."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN
from .tou_entities import TouStageButton, TouConfirmButton
from .settings_entities import (
    BatteryStageButton, BatteryConfirmButton,
    PeakStageButton, PeakConfirmButton,
    LoadStageButton, LoadConfirmButton,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        TouStageButton(coordinator, entry),
        TouConfirmButton(coordinator, entry),
        BatteryStageButton(coordinator, entry),
        BatteryConfirmButton(coordinator, entry),
        PeakStageButton(coordinator, entry),
        PeakConfirmButton(coordinator, entry),
        LoadStageButton(coordinator, entry),
        LoadConfirmButton(coordinator, entry),
    ])
