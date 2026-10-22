"""Time platform for Dyness Battery - Cygni Control (TOU settings)."""
from __future__ import annotations

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .tou_entities import TouTimeEntity
from .settings_entities import make_peak_entities, make_load_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator = entry.runtime_data
    entities = []
    for g in range(1, 5):
        entities += [
            TouTimeEntity(coordinator, entry, g, is_start=True),
            TouTimeEntity(coordinator, entry, g, is_start=False),
        ]
    entities += [
        e for e in make_peak_entities(coordinator, entry)
        + make_load_entities(coordinator, entry)
        if isinstance(e, TimeEntity)
    ]
    async_add_entities(entities)
