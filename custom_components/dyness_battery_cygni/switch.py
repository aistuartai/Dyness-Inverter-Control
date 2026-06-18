"""Switch platform for Dyness Battery - Cygni Control (TOU settings)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN
from .tou_entities import TouGroupSwitch, TouDodSwitch, TouSocMaxSwitch
from .settings_entities import make_peak_entities, make_load_entities, make_generator_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for g in range(1, 5):
        entities += [
            TouGroupSwitch(coordinator, entry, g),
            TouDodSwitch(coordinator, entry, g),
            TouSocMaxSwitch(coordinator, entry, g),
        ]
    entities += [
        e for e in make_peak_entities(coordinator, entry)
        + make_load_entities(coordinator, entry)
        + make_generator_entities(coordinator, entry)
        if hasattr(e, "is_on")  # switches only
    ]
    async_add_entities(entities)
