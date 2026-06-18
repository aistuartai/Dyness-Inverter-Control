"""Time platform for Dyness Battery - Cygni Control (TOU settings)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN
from .tou_entities import TouTimeEntity
from .settings_entities import make_peak_entities, make_load_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for g in range(1, 5):
        entities += [
            TouTimeEntity(coordinator, entry, g, is_start=True),
            TouTimeEntity(coordinator, entry, g, is_start=False),
        ]
    entities += [
        e for e in make_peak_entities(coordinator, entry)
        + make_load_entities(coordinator, entry)
        if hasattr(e, "native_value") and hasattr(e, "async_set_value")  # time entities
    ]
    async_add_entities(entities)
