"""Constants, schema detection and small value helpers (no I/O, no HA objects)."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.const import Platform

_LOGGER = logging.getLogger(__package__)

DOMAIN = "dyness_battery_cygni"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
PLATFORMS = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.TIME,
    Platform.BUTTON,
]

# Entitäten die in früheren Versionen existierten aber entfernt wurden.
# Diese werden beim Setup automatisch aus der Entity-Registry gelöscht.
# Settings entities that versions before 2.8.0 also registered on the wrong platform
# (number.py / time.py filtered by attribute, so each let the other's types through).
# Those duplicates are removed from the entity registry on setup.
MISPLATFORMED_ENTITY_KEYS = {
    "number": {"peak_start", "peak_end", "load_force_close_start", "load_force_close_end"},
    "time": {"peak_trigger_soc", "peak_power", "load_relay_close_soc", "load_relay_open_soc", "load_pv_threshold"},
}

STALE_ENTITY_KEYS = {
    # v2.0.0: alarmStatus1 / alarmStatus2 ersetzt durch alarmText + Alarm-Bit-Sensoren
    "alarmStatus1",
    "alarmStatus2",
    # Veraltete Tower-Alarm-Duplikate (al* ohne alarm*-Präfix)
    "alSpreadV",
    "alSpreadT",
    "alInsul",
    "alAfe",
    "alBms",
    "alSys",
}

# Gültige BMS-Suffixe
_BMS_SUFFIXES = ("-BMS", "-BDU")

# ── Schema-Konstanten ─────────────────────────────────────────────────────────
SCHEMA_TOWER        = "tower"
SCHEMA_STACK100     = "stack100"
SCHEMA_DL5          = "dl5"
SCHEMA_POWERBOX_PRO = "powerbox_pro"
SCHEMA_POWERBOX_G2  = "powerbox_g2"
SCHEMA_POWERDEPOT   = "powerdepot"
SCHEMA_JUNIOR       = "junior"
SCHEMA_CYGNI        = "cygni"
SCHEMA_UNKNOWN      = "unknown"

# Explizite Model → Schema Mapping
# Neue Modelle hier eintragen — kein Code-Logik-Anfassen nötig.
# Prefix-Match greift automatisch für Varianten (z.B. STACK100-12S, Cygni 5.0HS).
_MODEL_SCHEMA_MAP: dict[str, str] = {
    # Tower Familie — exakte Namen aus API verifiziert
    "TOWER-T14":        SCHEMA_TOWER,
    "TOWER-T17":        SCHEMA_TOWER,   # modelCode 26, verifiziert via Log (#31)
    "TOWER-PRO-TP7":    SCHEMA_TOWER,   # "Tower Pro TP7"  → TOWER-PRO-TP7
    "TOWER-PRO-TP11":   SCHEMA_TOWER,   # "Tower Pro TP11" → TOWER-PRO-TP11
    "TOWER-PRO-TP15":   SCHEMA_TOWER,   # "Tower Pro TP15" → TOWER-PRO-TP15
    "TOWER-TP7":        SCHEMA_TOWER,   # Fallback falls API ohne "Pro"
    "TOWER-TP11":       SCHEMA_TOWER,
    "TOWER-TP15":       SCHEMA_TOWER,
    # Stack100 Familie
    "STACK100-8S":      SCHEMA_STACK100,
    "STACK100-10S":     SCHEMA_STACK100,
    # DL5 Familie
    "DL5.0C":           SCHEMA_DL5,
    # PowerBox G2 (modelCode 42) — eigenes Schema mit 5-stelligen Points, verifiziert
    "POWERBOX-G2":      SCHEMA_POWERBOX_G2,
    # PowerBox Pro / PowerHaus — eigenes Schema, verifiziert via Log
    "POWERBOX-PRO":     SCHEMA_POWERBOX_PRO,
    "POWERHAUS":        SCHEMA_POWERBOX_PRO,
    # PowerDepot G2 (modelCode 144) — eigenes Schema
    "POWERDEPOT-G2":    SCHEMA_POWERDEPOT,
    # Junior Box
    "JUNIOR-BOX":       SCHEMA_JUNIOR,
    # Cygni Hybrid-Wechselrichter
    "CYGNI":            SCHEMA_CYGNI,
}


def _detect_schema(device_model_name: str, rt: dict) -> str:
    """Schema-Erkennung: primär via deviceModelName, Fallback via Points.

    Neue Geräte werden ausschließlich in _MODEL_SCHEMA_MAP eingetragen.
    Der Point-Fallback bleibt als Sicherheitsnetz für noch unbekannte Modelle.
    """
    model = (device_model_name or "").upper().replace(" ", "-")

    # Exakter Match
    if model in _MODEL_SCHEMA_MAP:
        return _MODEL_SCHEMA_MAP[model]

    # Prefix-Match für Varianten (z.B. STACK100-12S, Cygni 10.0HS)
    for key, schema in _MODEL_SCHEMA_MAP.items():
        prefix = key.split("-")[0]
        if model.startswith(prefix):
            _LOGGER.info(
                "Dyness: Unbekannte Modell-Variante '%s' → Schema '%s' per Prefix-Match ('%s')",
                model, schema, prefix,
            )
            return schema

    # Fallback: Point-Heuristik (letzter Ausweg für komplett unbekannte Modelle)
    _LOGGER.warning(
        "Dyness: Unbekanntes Modell '%s' — Schema-Erkennung via Points (Fallback). "
        "Bitte ein Issue mit Log-Datei erstellen.",
        model,
    )
    if "1400" in rt and ("2400" in rt or "2700" in rt):
        return SCHEMA_TOWER
    if "800" in rt:
        return SCHEMA_JUNIOR
    if ("13400" in rt or "12400" in rt) and "800" not in rt and "1400" not in rt:
        return SCHEMA_POWERDEPOT
    return SCHEMA_UNKNOWN


def _scan_interval_for_modules(n: int, user_min: int = 5) -> timedelta:
    """Dynamic scan interval: user_min is the floor; module count can only push it higher."""
    if n <= 2:
        minutes = user_min
    elif n <= 4:
        minutes = max(user_min, 10)
    else:
        minutes = max(user_min, 15)
    return timedelta(minutes=minutes)


def _to_float(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _is_success(result: dict) -> bool:
    """Prüft ob API-Antwort erfolgreich — akzeptiert code als String oder Integer."""
    code = result.get("code")
    return str(code) in ("0", "200") or code == 0
