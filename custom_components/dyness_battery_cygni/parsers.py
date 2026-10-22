"""Turn raw Dyness API responses into the coordinator's flat data dict.

Pure functions: no I/O, no Home Assistant objects. Moved verbatim out of
DynessDataCoordinator._async_update_data (2.8.0 refactor); behaviour is pinned
by the coordinator-data snapshot tests.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .const import (
    SCHEMA_CYGNI,
    SCHEMA_DL5,
    SCHEMA_JUNIOR,
    SCHEMA_POWERBOX_G2,
    SCHEMA_POWERBOX_PRO,
    SCHEMA_POWERDEPOT,
    SCHEMA_STACK100,
    SCHEMA_TOWER,
    _detect_schema,
    _to_float,
)

_LOGGER = logging.getLogger(__package__)


@dataclass
class ParseInput:
    """Everything the coordinator has fetched, as plain data."""

    device_sn: str | None = None
    station_info: dict = field(default_factory=dict)
    device_info: dict = field(default_factory=dict)
    storage_info: dict = field(default_factory=dict)
    realtime_data: dict = field(default_factory=dict)
    running_data: dict = field(default_factory=dict)
    v2_realtime_data: dict = field(default_factory=dict)
    v2_status_data: dict = field(default_factory=dict)
    module_sns: list[str] = field(default_factory=list)
    module_data: dict[str, dict] = field(default_factory=dict)


def realtime_points(raw: list) -> dict:
    """realTime/data list of {pointId, pointValue} -> {pointId: pointValue}."""
    return {
        item["pointId"]: item["pointValue"]
        for item in raw
        if isinstance(item, dict) and "pointId" in item
    }


def flatten_v2(raw: dict) -> dict:
    """GetRealTimeDataBySN nests its fields (gridInfo, batteryInfo, ...); merge them into one dict."""
    flat: dict = {}
    for v in raw.values():
        if isinstance(v, dict):
            flat.update(v)
    return flat


def _parse_stack100(data: dict, rt: dict, src: ParseInput) -> None:
    """Stack100: points straight from the BMS master."""
    # Stack100 Schema — Points direkt vom BMS Master
    data["packVoltage"] = rt.get("1100") if rt.get("1100") is not None else data.get("packVoltage")
    data["soh"]            = rt.get("1500")
    data["cycleCount"]     = rt.get("1800")
    data["energyChargeTotal"] = rt.get("1900")

    # Kapazität direkt aus BMS — zuverlässiger als station_info × n_modules.
    # Bleibt korrekt auch nach Modulerweiterungen (z.B. 7 → 13 Module).
    stack_remaining = _to_float(rt.get("1600"))
    stack_usable    = _to_float(rt.get("1700"))
    if stack_remaining is not None and stack_remaining > 0:
        data["remainingKwh"]    = stack_remaining
    if stack_usable is not None and stack_usable > 0:
        data["usableKwh"]       = stack_usable
        data["batteryCapacity"] = stack_usable  # Point 1700 > station_info

    # Zellspannungen Master-Ebene
    data["cellVoltageMax"]       = rt.get("2400")
    data["cellVoltageMin"]       = rt.get("2700")
    data["cellVoltageMaxModule"] = rt.get("2500")
    data["cellVoltageMaxCell"]   = rt.get("2600")
    data["cellVoltageMinModule"] = rt.get("2800")
    data["cellVoltageMinCell"]   = rt.get("2900")

    # Temperaturen
    data["tempMax"] = rt.get("3000")
    data["tempMin"] = rt.get("3300")

    # Strom- und Spannungslimits
    cl = _to_float(rt.get("2000"))
    dl = _to_float(rt.get("2100"))
    if cl is not None and cl > 0:
        data["chargeCurrentLimit"]    = cl
    if dl is not None and dl > 0:
        data["dischargeCurrentLimit"] = dl

    # Balancing
    bal = rt.get("4000")
    if bal is not None:
        data["balancingStatus"] = str(bal) != "0"

    # Alarm-Bits
    data["alarmSpreadV"] = str(rt.get("5001", "0")) == "1"
    data["alarmSpreadT"] = str(rt.get("5002", "0")) == "1"
    data["alarmInsul"]   = str(rt.get("5003", "0")) == "1"
    data["alarmAfe"]     = str(rt.get("5101", "0")) == "1"
    data["alarmBms"]     = str(rt.get("5102", "0")) == "1"
    data["alarmSys"]     = str(rt.get("5104", "0")) == "1"
    data["alarmTotal"]   = rt.get("9999999")

    _LOGGER.debug(
        "Dyness Stack100: usable=%.2f kWh remaining=%.2f kWh "
        "cellMax=%s V cellMin=%s V",
        stack_usable or 0, stack_remaining or 0,
        data.get("cellVoltageMax"), data.get("cellVoltageMin"),
    )


def _parse_junior_dl5(data: dict, rt: dict, src: ParseInput) -> None:
    """Junior Box and DL5.0C."""
    data["packVoltage"]            = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
    data["soh"]                   = rt.get("1200")
    data["temp"]                  = rt.get("1800")
    data["cellVoltageMax"]         = rt.get("1300")
    data["cellVoltageMin"]         = rt.get("1500")
    data["energyChargeDay"]        = rt.get("7200")
    data["energyDischargeDay"]     = rt.get("7400")
    data["energyChargeTotal"]      = rt.get("7100")
    data["energyDischargeTotal"]   = rt.get("7300")
    data["tempMosfet"]             = rt.get("2300")
    data["tempBmsMax"]             = rt.get("2800")
    data["tempBmsMin"]             = rt.get("3000")
    data["alarmStatus1"]           = rt.get("3200")
    data["alarmStatus2"]           = rt.get("3300")
    data["alarmTotal"]             = rt.get("4100")
    # Max Lade-/Entladestrom — nur bei Multi-Modul Geräten (DL5.0C etc.)
    # Junior Box / PowerHaus liefern unzuverlässige Werte (nicht konform mit Datenblatt)
    if len(src.module_sns) > 0:
        cl = _to_float(rt.get("3800"))
        dl = _to_float(rt.get("3900"))
        if cl is not None and cl > 0:
            data["chargeCurrentLimit"] = cl
        if dl is not None and dl > 0:
            data["dischargeCurrentLimit"] = dl


def _parse_powerbox_pro(data: dict, rt: dict, src: ParseInput) -> None:
    """PowerBox Pro / PowerHaus."""
    # PowerBox Pro / PowerHaus Schema — verifiziert via Log
    # batteryCapacity aus station/info = Gesamtkapazität direkt
    # (kein × n_modules — unabhängig von Modulanzahl)
    # Points verifiziert:
    # 600  = Pack Voltage, 700 = Current, 800 = SOC
    # 1200 = SOH, 1300 = Cell Voltage Max, 1500 = Cell Voltage Min
    # 1800 = Temp Max, 2000 = Temp Min, 2300 = MOSFET Temp
    # 3000 = BMS Temp Max, 3600/3700 = Voltage Limits
    # 3800/3900 = Charge/Discharge Current Limit
    # 3200–3500 = Alarm-Bits (gleiche Struktur wie Junior/DL5)
    # 4000 = Ah-Wert (kein Balancing-Flag — nicht nutzen)
    # 900/1000/1100/1900 = leer oder Modul-Nummern → kein Cycle Count
    data["packVoltage"] = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
    data["soh"]            = rt.get("1200")
    data["cellVoltageMax"] = rt.get("1300")
    data["cellVoltageMin"] = rt.get("1500")
    data["cellVoltageMaxModule"] = rt.get("1401")
    data["cellVoltageMaxCell"]   = rt.get("1402")
    data["cellVoltageMinModule"] = rt.get("1601")
    data["cellVoltageMinCell"]   = rt.get("1602")
    data["tempMax"]        = rt.get("1800")
    data["tempMin"]        = rt.get("2000")
    data["tempMosfet"]     = rt.get("2300")
    data["tempBmsMax"]     = rt.get("3000")

    cv = _to_float(rt.get("3600"))
    dv = _to_float(rt.get("3700"))
    if cv is not None and cv > 0:
        data["chargeVoltageLimit"]    = cv
    if dv is not None and dv > 0:
        data["dischargeVoltageLimit"] = dv
    cl = _to_float(rt.get("3800"))
    dl = _to_float(rt.get("3900"))
    if cl is not None and cl > 0:
        data["chargeCurrentLimit"]    = cl
    if dl is not None and dl > 0:
        data["dischargeCurrentLimit"] = dl

    # Alarm-Bits — gleiche Struktur wie Junior/DL5
    data["alarmStatus1"] = rt.get("3200")
    data["alarmStatus2"] = rt.get("3300")
    data["alarmTotal"]   = rt.get("4100")

    # usableKwh: batteryCapacity (station/info = Gesamtkapazität)
    bc  = _to_float(data.get("batteryCapacity"))
    soc = _to_float(data.get("soc"))
    soh = _to_float(rt.get("1200"))
    if bc is not None and soc is not None:
        soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
        data["usableKwh"]    = round(bc * soh_factor, 3)
        data["remainingKwh"] = round(bc * soh_factor * soc / 100, 3)


def _parse_tower(data: dict, rt: dict, src: ParseInput) -> None:
    """Tower T14 and Tower Pro TP7/TP11/TP15."""
    # Tower Schema (Tower T14 + Tower Pro TP7/TP11/TP15)
    data["soh"]                   = rt.get("1500")
    data["tempMax"]               = rt.get("3000")
    data["tempMin"]               = rt.get("3300")
    data["cellVoltageMax"]         = rt.get("2400")
    data["cellVoltageMin"]         = rt.get("2700")
    data["cycleCount"]             = rt.get("1800")
    data["energyChargeTotal"]      = rt.get("1900")
    # Point 1600 = Verbleibende Kapazität kWh (direkt vom Tower/TP7-BMS)
    tower_remaining = _to_float(rt.get("1600"))
    if tower_remaining is not None and tower_remaining > 0:
        data["remainingKwh"] = tower_remaining
    # Point 1700 = Nutzbare (Nenn-)Kapazität kWh (Tower Pro TP7)
    # Überschreibt batteryCapacity aus station/info falls vorhanden
    tower_usable = _to_float(rt.get("1700"))
    if tower_usable is not None and tower_usable > 0:
        data["usableKwh"] = tower_usable
        # batteryCapacity angleichen falls abweichend
        if data.get("batteryCapacity") is None:
            data["batteryCapacity"] = tower_usable
    # Tower Pro TP7: Alarm-Flags (4400-4805, je Flag-Register + Bit-Aufgliederung)
    # Tower T14: Alarm-Bits direkt (5001-5104)
    if rt.get("4400") is not None:
        # Tower Pro TP7 Alarm-Schema
        data["alarmSpreadV"] = str(rt.get("4402", "0")) == "1"  # Einzelzellspannung zu hoch - Alarm Stufe 1
        data["alarmSpreadT"] = str(rt.get("4403", "0")) == "1"  # Ladetemperatur zu hoch - Alarm Stufe 1
        data["alarmInsul"]   = False  # TP7 hat keinen separaten Isolationsfehler-Bit
        data["alarmAfe"]     = False
        data["alarmBms"]     = False
        data["alarmSys"]     = False
        # Gesamtalarm: irgendein Flag-Register ≠ 0
        flags = [rt.get(str(f), "0") for f in [4400, 4500, 4600, 4700, 4800, 4900]]
        data["alarmTotal"] = str(int(any(str(f) != "0" for f in flags)))
    else:
        # Tower T14 Alarm-Schema (verifiziert)
        data["alarmSpreadV"]  = str(rt.get("5001", "0")) == "1"
        data["alarmSpreadT"]  = str(rt.get("5002", "0")) == "1"
        data["alarmInsul"]    = str(rt.get("5003", "0")) == "1"
        data["alarmAfe"]      = str(rt.get("5101", "0")) == "1"
        data["alarmBms"]      = str(rt.get("5102", "0")) == "1"
        data["alarmSys"]      = str(rt.get("5104", "0")) == "1"
        data["alarmTotal"]    = rt.get("9999999")


def _parse_powerbox_g2(data: dict, rt: dict, src: ParseInput) -> None:
    """PowerBox G2 (modelCode 42)."""
    # PowerBox G2 Schema (modelCode 42) — verifiziert via Log
    # Master-Points: 5-stellig (10xxx/12xxx/13xxx/14xxx)
    # Sub-Modul-Points identisch mit PowerDepot G2
    # batteryCapacity aus station/info = Gesamtkapazität direkt
    data["packVoltage"] = rt.get("13500") if rt.get("13500") is not None else data.get("packVoltage")
    data["cycleCount"]     = rt.get("13900")

    # Temperaturen
    bms_temp = _to_float(rt.get("12400"))
    if bms_temp is not None:
        data["tempBmsMax"] = bms_temp
    cell_temps = [
        _to_float(rt.get(str(12500 + i * 100)))
        for i in range(4)
    ]
    valid_temps = [t for t in cell_temps if t is not None and t > 0]
    if valid_temps:
        data["tempMax"] = max(valid_temps)
        data["tempMin"] = min(valid_temps) if len(valid_temps) > 1 else None

    # Zellspannungen aus Sub-Modul-Daten (10300–11800)
    cells = []
    for i in range(1, 17):
        v = _to_float(rt.get(str(10200 + i * 100)))
        if v is not None and v > 0:
            cells.append(v)
    if cells:
        data["cellVoltageMax"] = max(cells)
        data["cellVoltageMin"] = min(cells)

    # Kapazität: station/info = Gesamtkapazität direkt (kein × n_modules)
    bc  = _to_float(data.get("batteryCapacity"))
    soc = _to_float(data.get("soc"))
    soh = _to_float(data.get("soh"))
    if bc is not None and soc is not None:
        soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
        data["usableKwh"]    = round(bc * soh_factor, 3)
        data["remainingKwh"] = round(bc * soh_factor * soc / 100, 3)

    # Voltage + Current Limits
    cv = _to_float(rt.get("18700"))
    dv = _to_float(rt.get("18800"))
    cl = _to_float(rt.get("18600"))
    dl = _to_float(rt.get("19200"))
    if cv is not None and cv > 0:
        data["chargeVoltageLimit"]    = cv
    if dv is not None and dv > 0:
        data["dischargeVoltageLimit"] = dv
    if cl is not None and cl > 0:
        data["chargeCurrentLimit"]    = cl
    if dl is not None and dl > 0:
        data["dischargeCurrentLimit"] = dl

    _LOGGER.debug(
        "Dyness PowerBox G2: packVoltage=%s V, SOC=%s%%, "
        "cells=%d, tempMax=%s°C",
        data.get("packVoltage"), soc,
        len(cells), data.get("tempMax"),
    )


def _parse_powerdepot(data: dict, rt: dict, src: ParseInput) -> None:
    """PowerDepot G2 (modelCode 144)."""
    # PowerDepot G2 Schema (modelCode 144) — vollständig verifiziert
    # Point 400 = Modulanzahl direkt vom BMS → robuster als _module_sns
    # batteryCapacity ZUERST setzen damit usableKwh korrekt rechnet
    n_mod_bms = _to_float(rt.get("400"))
    if n_mod_bms is not None and n_mod_bms > 0:
        bc_single = _to_float(src.station_info.get("batteryCapacity"))
        if bc_single is not None:
            data["batteryCapacity"] = round(bc_single * int(n_mod_bms), 3)
    elif data.get("batteryCapacity") is None:
        # Fallback: _module_sns Anzahl wenn Point 400 leer
        bc_single = _to_float(src.station_info.get("batteryCapacity"))
        n_mods = max(len(src.module_sns), 1)
        # Unreachable today: batteryCapacity is only None here when station_info has no capacity.
        if bc_single is not None and n_mods > 1:  # pragma: no cover
            data["batteryCapacity"] = round(bc_single * n_mods, 3)

    data["packVoltage"] = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
    data["realTimeCurrent"]      = rt.get("700")
    data["soc"]                  = rt.get("800")
    data["soh"]                  = rt.get("1200")
    data["cellVoltageMax"]       = rt.get("1300")
    data["cellVoltageMaxModule"] = rt.get("1401")
    data["cellVoltageMaxCell"]   = rt.get("1402")
    data["cellVoltageMin"]       = rt.get("1500")
    data["cellVoltageMinModule"] = rt.get("1601")
    data["cellVoltageMinCell"]   = rt.get("1602")
    data["tempMax"]              = rt.get("1800")
    data["tempMaxModule"]        = rt.get("1901")
    data["tempMin"]              = rt.get("2000")
    data["tempMinModule"]        = rt.get("2101")
    data["tempMosfet"]           = rt.get("2300")
    data["tempBmsMax"]           = rt.get("2800")
    data["tempBmsMin"]           = rt.get("3000")

    cv = _to_float(rt.get("3600"))
    dv = _to_float(rt.get("3700"))
    cl = _to_float(rt.get("3800"))
    dl = _to_float(rt.get("3900"))
    if cv is not None and cv > 0:
        data["chargeVoltageLimit"]    = cv
    if dv is not None and dv > 0:
        data["dischargeVoltageLimit"] = dv
    if cl is not None and cl > 0:
        data["chargeCurrentLimit"]    = cl
    if dl is not None and dl > 0:
        data["dischargeCurrentLimit"] = dl

    # Kapazität aus BMS-Modulanzahl
    bc  = _to_float(data.get("batteryCapacity"))
    soc_pd = _to_float(rt.get("800"))
    soh_pd = _to_float(rt.get("1200"))
    if bc is not None and soc_pd is not None:
        soh_factor = (soh_pd / 100) if (soh_pd is not None and soh_pd <= 100) else 1.0
        data["usableKwh"]    = round(bc * soh_factor, 3)
        data["remainingKwh"] = round(bc * soh_factor * soc_pd / 100, 3)

    # cycleCount aus Sub-Modul-Daten aggregieren (Point 13900)
    # kein Master-Point verfügbar → Mittelwert über alle Module
    mod_cycles = [
        _to_float(m.get("cycleCount"))
        for m in (data.get("module_data") or {}).values()
        if m.get("cycleCount") is not None
    ]
    if mod_cycles:
        data["cycleCount"] = round(sum(mod_cycles) / len(mod_cycles), 0)

    # temp (Durchschnitt) aus Sub-Modul-Daten
    mod_temps = [
        _to_float(m.get("temp"))
        for m in (data.get("module_data") or {}).values()
        if m.get("temp") is not None
    ]
    if mod_temps:
        data["temp"] = round(sum(mod_temps) / len(mod_temps), 1)

    # workStatus: aus batteryStatus ableiten statt aus storage/list
    # storage/list zeigt "Fault" obwohl Gerät normal lädt/entlädt
    battery_status_pd = data.get("batteryStatus")
    if battery_status_pd == "Charging":
        data["workStatus"] = "Charging"
    elif battery_status_pd == "Discharging":
        data["workStatus"] = "Discharging"
    else:
        alarm_bits_pd = [
            rt.get("3200"), rt.get("3201"), rt.get("3202"),
            rt.get("3300"), rt.get("3400"), rt.get("3500"),
        ]
        all_clear_pd = all(
            v is None or str(v) in ("0", "0.0", "")
            for v in alarm_bits_pd
        )
        if all_clear_pd and data.get("workStatus") == "Fault":
            data["workStatus"] = "Standby"
            _LOGGER.debug(
                "Dyness PowerDepot G2: workStatus 'Fault' → 'Standby' "
                "— alle Alarm-Bits sind 0"
            )

    # Alarm-Sensoren — korrekte Point-Mappings (verifiziert)
    # 3200 = Sammelbyte 1, 3201=Voltage Spread, 3202=MOSFET Temp
    # 3300 = Sammelbyte 2, 3400=AFE Comm, 3500=System Fault
    data["alarmStatus"]  = (
        str(rt.get("3200", "0")) != "0"
        or str(rt.get("3300", "0")) != "0"
    )
    data["alarmSpreadV"] = str(rt.get("3201", "0")) != "0"
    data["alarmSpreadT"] = str(rt.get("3202", "0")) != "0"
    data["alarmAfe"]     = str(rt.get("3400", "0")) != "0"
    data["alarmSys"]     = str(rt.get("3500", "0")) != "0"

    _LOGGER.debug(
        "Dyness PowerDepot G2: n_modules=%s, batteryCapacity=%s kWh, "
        "SOC=%s%%, usableKwh=%s kWh, workStatus=%s",
        n_mod_bms, data.get("batteryCapacity"),
        soc_pd, data.get("usableKwh"), data.get("workStatus"),
    )


def _parse_cygni(data: dict, rt: dict, src: ParseInput) -> None:
    """Cygni hybrid inverter (modelCode 192)."""
    # Cygni 10.0HS-M8 Schema (modelCode 192) — Hybrid-Wechselrichter
    # Verifiziert via API-Log (Discussion #18)
    #
    # Besonderheiten:
    # - Keine Sub-Module (SUB leer)
    # - INVERTIERTE Polarität: negativ = Laden, positiv = Entladen
    #   (entgegengesetzt zu allen anderen Dyness-Modellen!)
    # - getLastRunningDataBySn ist primäre Datenquelle (vollständig)
    # - getLastPowerDataBySn liefert nur SOC/Power (unvollständig)
    # - batteryCapacity aus station/info = Gesamtkapazität direkt
    #   (30.72 kWh = 4 × 7.68 kWh)
    #
    # Points aus realTime/data:
    # 170  = Battery Voltage (V)
    # 171  = Battery Current (A)
    # 172  = Battery Power (W) — invertiert!
    # 173  = Battery Status
    # 2003 = Battery Temperature (°C)
    # 2004 = Charge Current Limit (A)
    # 2005 = Discharge Current Limit (A)
    # 2010 = SOC (%)
    # 2011 = SOH (%)
    # 164  = Internal Temperature
    # 165  = Heat Dissipation Temperature
    # 166  = Module Temperature

    data["packVoltage"] = rt.get("170") if rt.get("170") is not None else data.get("packVoltage")
    data["soc"]         = rt.get("2010")
    data["soh"]         = rt.get("2011")
    data["temp"]        = rt.get("2003")

    cl = _to_float(rt.get("2004"))
    dl = _to_float(rt.get("2005"))
    if cl is not None and cl > 0:
        data["chargeCurrentLimit"]    = cl
    if dl is not None and dl > 0:
        data["dischargeCurrentLimit"] = dl

    # Invertierte Polarität korrigieren
    raw_power   = _to_float(rt.get("172"))
    raw_current = _to_float(rt.get("171"))
    if raw_power is not None:
        data["realTimePower"]   = raw_power * -1
    if raw_current is not None:
        data["realTimeCurrent"] = raw_current * -1

    # Kapazität: station/info = Gesamtkapazität direkt
    bc  = _to_float(data.get("batteryCapacity"))
    soc_c = _to_float(rt.get("2010"))
    soh_c = _to_float(rt.get("2011"))
    if bc is not None and soc_c is not None:
        soh_factor = (soh_c / 100) if (soh_c is not None and soh_c <= 100) else 1.0
        data["usableKwh"]    = round(bc * soh_factor, 3)
        data["remainingKwh"] = round(bc * soh_factor * soc_c / 100, 3)

    _LOGGER.debug(
        "Dyness Cygni: packVoltage=%s V, SOC=%s%%, SOH=%s%%, "
        "power=%s W (invertiert korrigiert), temp=%s°C",
        data.get("packVoltage"), soc_c, soh_c,
        data.get("realTimePower"), data.get("temp"),
    )


_SCHEMA_PARSERS = {
    SCHEMA_STACK100: _parse_stack100,
    SCHEMA_JUNIOR: _parse_junior_dl5,
    SCHEMA_DL5: _parse_junior_dl5,
    SCHEMA_POWERBOX_PRO: _parse_powerbox_pro,
    SCHEMA_TOWER: _parse_tower,
    SCHEMA_POWERBOX_G2: _parse_powerbox_g2,
    SCHEMA_POWERDEPOT: _parse_powerdepot,
    SCHEMA_CYGNI: _parse_cygni,
}


def build_data(src: ParseInput, data: dict | list) -> tuple[dict, list[str]]:
    """Build the coordinator data dict.

    ``data`` is the getLastPowerDataBySn payload (dict, list of records, or {}).
    Returns the data dict and the list of active alarm texts.
    """
    if isinstance(data, list):
        valid = [d for d in data if d.get("soc") is not None]
        if not valid:
            _LOGGER.warning(
                "Dyness: Alle %d Datenpunkte haben soc=null (deviceSn=%s)",
                len(data), src.device_sn
            )
        data = valid[-1] if valid else (data[-1] if data else {})

    # ── Statische Felder ──────────────────────────────────────
    # deviceCommunicationStatus: aus storage/list (bei jedem Update aktuell)
    # statt device_info (einmalig beim Start — veraltet nach Neuverbindung).
    comm_status = src.storage_info.get("deviceCommunicationStatus")
    if comm_status is None:
        comm_status = src.device_info.get("deviceCommunicationStatus")
    data["deviceCommunicationStatus"] = comm_status
    data["firmwareVersion"]            = src.device_info.get("firmwareVersion")
    data["workStatus"]                 = src.storage_info.get("workStatus")

    # ── realTime/data Felder ──────────────────────────────────
    rt = src.realtime_data

    # Schema-Erkennung via deviceModelName (primär) + Point-Fallback
    schema = _detect_schema(
        src.device_info.get("deviceModelName", ""), rt
    )
    data["_schema"] = schema
    _LOGGER.debug("Dyness: Schema erkannt: %s", schema)

    # PowerBox G2: firmware aus storage_info (device_info liefert null)
    if schema == SCHEMA_POWERBOX_G2:
        fw = src.storage_info.get("firmwareVersion")
        if fw:
            data["firmwareVersion"] = fw

    # batteryCapacity:
    # - Stack100 + Tower: direkt aus BMS-Point (1700) — überschreibt station_info.
    #   station_info kann veraltet sein (z.B. nach Modulerweiterung 7→13 Module).
    # - DL5 Master/Slave: station_info × n_sub_modules
    # - Alle anderen: station_info × n_modules
    bc_single = _to_float(src.station_info.get("batteryCapacity"))
    n_modules = max(len(src.module_sns), 1)
    if schema in (SCHEMA_STACK100, SCHEMA_TOWER):
        # Wird unten von Point 1700 überschrieben — hier nur Initialwert
        data["batteryCapacity"] = bc_single
    elif bc_single is not None and n_modules > 1:
        data["batteryCapacity"] = round(bc_single * n_modules, 3)
        _LOGGER.debug(
            "Dyness: batteryCapacity %s × %d Module = %s kWh",
            bc_single, n_modules, data["batteryCapacity"]
        )
    else:
        data["batteryCapacity"] = bc_single
    parse_schema = _SCHEMA_PARSERS.get(schema)
    if parse_schema is not None:
        parse_schema(data, rt, src)

    # ── Temperatur-Logik ─────────────────────────────────────
    # Wenn tempMax == tempMin → nur tempMax behalten (ein Sensor)
    # Wenn verschieden → beide behalten (zwei Sensoren)
    t_max = _to_float(data.get("tempMax"))
    t_min = _to_float(data.get("tempMin"))
    if t_max is not None and t_min is not None and t_max == t_min:
        data.pop("tempMin", None)  # Doppelten Sensor vermeiden

    # BMS Temp: gleiche Logik
    bms_max = _to_float(data.get("tempBmsMax"))
    bms_min = _to_float(data.get("tempBmsMin"))
    if bms_max is not None and bms_min is not None and bms_max == bms_min:
        data.pop("tempBmsMin", None)

    # ── Berechnete Felder ─────────────────────────────────────
    try:
        vmax = _to_float(data.get("cellVoltageMax"))
        vmin = _to_float(data.get("cellVoltageMin"))
        if vmax is not None and vmin is not None and vmax > 0 and vmin > 0:
            data["cellVoltageDiffMv"] = round((vmax - vmin) * 1000, 1)
    except (ValueError, TypeError):  # pragma: no cover - _to_float already returns float or None
        pass

    # ── getLastRunningDataBySn Felder ─────────────────────────
    rd = src.running_data
    if rd:
        _GRID_STATUS = {"0": "Off Grid", "1": "On Grid"}
        _RUN_MODE    = {"0": "Self-use", "1": "Feed-in Priority", "2": "Backup", "3": "TOU (Time of Use)"}
        _INV_STATUS  = {
            "0": "Waiting", "1": "Grid-Connected", "2": "Off-Grid",
            "3": "Fault", "4": "Programming", "5": "Detection",
        }

        # Leistung
        for key, rdkey in [
            ("pvPower",       "pvPower"),
            ("loadPower",     "loadPower"),
            ("pv1Power",      "pv1Power"),
            ("pv2Power",      "pv2Power"),
            ("pv3Power",      "pv3Power"),
            ("pv4Power",      "pv4Power"),
        ]:
            v = _to_float(rd.get(rdkey))
            if v is not None:
                data[key] = v

        # gridPower: API positive=exporting, negate for HA convention (positive=importing)
        gp = _to_float(rd.get("gridPower"))
        if gp is not None:
            data["gridPower"] = -gp if gp != 0 else 0

        # Energie
        for key, rdkey in [
            ("pvEnergyToday",    "dayGeneration"),
            ("loadEnergyToday",  "dayElectricity"),
            ("gridImportToday",  "buyEnergy"),
            ("gridExportToday",  "sellEnergy"),
            ("pvEnergyTotal",    "totalGeneration"),
            ("loadEnergyTotal",  "totalElectricity"),
            ("gridImportTotal",  "totalBuyEnergy"),
            ("gridExportTotal",  "totalSellEnergy"),
        ]:
            v = _to_float(rd.get(rdkey))
            if v is not None:
                data[key] = v

        # Temperaturen Inverter
        for key, rdkey in [
            ("tempInternal",  "internalTemperature"),
            ("tempModule",    "moduleTemperature"),
            ("tempHeatSink",  "heatDissipationTemperature"),
        ]:
            v = _to_float(rd.get(rdkey))
            if v is not None:
                data[key] = v

        # Grid / Status
        data["gridStatus"]         = _GRID_STATUS.get(str(rd.get("gridStatus", "")), rd.get("gridStatus"))
        data["runModel"]           = _RUN_MODE.get(str(rd.get("runModel", "")), rd.get("runModel"))
        data["inverterWorkStatus"] = _INV_STATUS.get(str(rd.get("workStatus", "")), rd.get("workStatus"))

        # Grid Messung
        for key, rdkey in [
            ("gridVoltage",   "rvoltage"),
            ("gridCurrent",   "rcurrent"),
            ("gridFrequency", "gridFrequencyR"),
            ("busVoltage",    "busVoltage"),
            ("pv1Voltage",    "pv1Voltage"),
            ("pv2Voltage",    "pv2Voltage"),
            ("pv3Voltage",    "pv3Voltage"),
            ("pv1Current",    "pv1Current"),
            ("pv2Current",    "pv2Current"),
            ("pv3Current",    "pv3Current"),
        ]:
            v = _to_float(rd.get(rdkey))
            if v is not None:
                data[key] = v

        # Charge/Discharge Limit aus running_data (zuverlässiger als Points 3800/3900)
        cl = _to_float(rd.get("chargingLimitCurrent"))
        dl = _to_float(rd.get("dischargeLimitCurrent"))
        if cl is not None and cl > 0:
            data["chargeCurrentLimit"]    = cl
        if dl is not None and dl > 0:
            data["dischargeCurrentLimit"] = dl

        # Fallback: SOC/Power aus running_data wenn getLastPowerDataBySn nichts liefert
        if data.get("soc") is None:
            soc_rd = rd.get("batterySoc")
            if soc_rd is not None:
                data["soc"] = str(soc_rd)
        if data.get("realTimePower") is None:
            bp = _to_float(rd.get("batteryPower"))
            if bp is not None:
                data["realTimePower"] = bp

        # Extra fields from running_data
        bv = _to_float(rd.get("batteryVoltage"))
        if bv is not None:
            data["batteryVoltage"] = bv
        bc = rd.get("batteryCount")
        if bc is not None:
            data["batteryCount"] = bc
        if data.get("soh") is None:
            soh_rd = _to_float(rd.get("batterySoh"))
            if soh_rd is not None:
                data["soh"] = soh_rd
        if data.get("temp") is None:
            t_rd = _to_float(rd.get("batteryTemperature"))
            if t_rd is not None:
                data["temp"] = t_rd

    # Battery status — recalculated after running_data applied
    try:
        power = float(data.get("realTimePower") or 0)
        data["batteryStatus"] = (
            "Charging"    if power >  10 else
            "Discharging" if power < -10 else
            "Standby"
        )
    except (ValueError, TypeError):
        pass

    # ── v2 API Felder ─────────────────────────────────────────
    _COMM_STATUS   = {"0": "Normal", "1": "Abnormal"}
    _METER_TYPE    = {"1": "CT", "2": "Smart Meter"}
    _ON_OFF        = {"0": "Off", "1": "On"}
    _POWER_LIMIT   = {"0": "Not Limited", "1": "Limiting"}

    if src.v2_realtime_data:
        for key, v2key in [
            ("backupLoadPower",          "backupLoadPower"),
            ("thirdPartyInvPower",       "thirdPartyInvPower"),
            ("inverterTotalPower",       "inverterTotalPower"),
            ("reactivePower",            "reactivePower"),
            ("apparentPower",            "apparentPower"),
            ("onGridDischargeDepth",     "onGridDischargeDepth"),
            ("offGridDischargeDepth",    "offGridDischargeDepth"),
        ]:
            v = _to_float(src.v2_realtime_data.get(v2key))
            if v is not None:
                data[key] = v

        # String-mapped fields from v2 realtime
        raw = src.v2_realtime_data
        if raw.get("bmsCommunicationStatus") is not None:
            data["bmsCommunicationStatus"] = _COMM_STATUS.get(str(raw["bmsCommunicationStatus"]), raw["bmsCommunicationStatus"])
        if raw.get("bmsSoftwareVersion") is not None:
            data["bmsSoftwareVersion"] = raw["bmsSoftwareVersion"]
        if raw.get("meterType") is not None:
            data["meterType"] = _METER_TYPE.get(str(raw["meterType"]), raw["meterType"])
        if raw.get("meterCommunicationStatus") is not None:
            data["meterCommunicationStatus"] = _COMM_STATUS.get(str(raw["meterCommunicationStatus"]), raw["meterCommunicationStatus"])

    if src.v2_status_data:
        sd = src.v2_status_data
        if sd.get("sparePower") is not None:
            data["sparePower"] = _ON_OFF.get(str(sd["sparePower"]), sd["sparePower"])
        if sd.get("powerLimit") is not None:
            data["powerLimitActive"] = _POWER_LIMIT.get(str(sd["powerLimit"]), sd["powerLimit"])

    # ── Alarm-Text Dekodierung ────────────────────────────────
    _ALARM_BITS_1 = {
        "3201": "Cell voltage consistency warning",
        "3202": "MOSFET high temperature",
        "3203": "Cell low temperature",
        "3204": "Cell high temperature",
        "3205": "Cell low voltage",
        "3206": "Cell high voltage",
        "3207": "Pack low voltage",
        "3208": "Pack high voltage",
    }
    _ALARM_BITS_2 = {
        "3305": "Internal communication error",
        "3306": "Discharge overcurrent",
        "3307": "Charge overcurrent",
        "3308": "Cell temperature consistency warning",
    }
    alarm_texts = []
    for pid, label in {**_ALARM_BITS_1, **_ALARM_BITS_2}.items():
        if str(rt.get(pid, "0")) == "1":
            alarm_texts.append(label)
    # Tower Alarm-Bits
    _ALARM_BITS_TOWER = {
        "5001": "Voltage spread alarm",
        "5002": "Temperature spread alarm",
        "5003": "Low insulation alarm",
        "5101": "AFE communication error",
        "5102": "BMS communication error",
        "5104": "System fault",
    }
    for pid, label in _ALARM_BITS_TOWER.items():
        if str(rt.get(pid, "0")) == "1":
            alarm_texts.append(label)
    data["alarmText"] = ", ".join(alarm_texts) if alarm_texts else "OK"

    # ── stationName als Gerätename ─────────────────────────────
    data["stationName"] = src.device_info.get("stationName") or                                           src.storage_info.get("stationName") or                                           "Dyness Battery"

    # ── Voltage Limits ─────────────────────────────────────────
    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
        cv = _to_float(rt.get("3600"))
        dv = _to_float(rt.get("3700"))
        if cv is not None and cv > 0:
            data["chargeVoltageLimit"] = cv
        if dv is not None and dv > 0:
            data["dischargeVoltageLimit"] = dv

    # ── Cell-Nummer mit Max/Min Spannung ───────────────────────
    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
        data["cellVoltageMaxModule"] = rt.get("1401")
        data["cellVoltageMaxCell"]   = rt.get("1402")
        data["cellVoltageMinModule"] = rt.get("1601")
        data["cellVoltageMinCell"]   = rt.get("1602")

    # ── Balancing Status ───────────────────────────────────────
    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
        bal = rt.get("4000")
        if bal is not None:
            data["balancingStatus"] = str(bal) != "0"

    # ── Cygni battery energy totals (realTime/data points) ────
    if schema == SCHEMA_CYGNI:
        rt = src.realtime_data
        if rt:
            bct = _to_float(rt.get("195-196"))
            bdt = _to_float(rt.get("198-199"))
            bcd = _to_float(rt.get("197"))
            bdd = _to_float(rt.get("200"))
            if bct is not None:
                data["batteryChargeTotal"]    = bct
            if bdt is not None:
                data["batteryDischargeTotal"] = bdt
            if bcd is not None:
                data["batteryChargeToday"]    = bcd
            if bdd is not None:
                data["batteryDischargeToday"] = bdd

    # ── Modul-Daten anhängen ──────────────────────────────────
    n_modules = max(len(src.module_sns), 1)
    data["module_data"]  = src.module_data
    data["moduleCount"]  = len(src.module_sns)

    # ── usableKwh / remainingKwh Berechnung ──────────────────
    # Stack100: bereits via Point 1600/1700 gesetzt → überspringen.
    # DL5: Strategie 1 (Ah-basiert) deaktiviert — Ah-Werte aus 13600/13800
    #   repräsentieren nur einen Teilbereich der Kapazität und unterschätzen
    #   systematisch (z.B. 3.5 kWh statt 10.24 kWh). batteryCapacity × SOC zuverlässiger.
    # Alle anderen: Strategie 1 (Ah) wenn verfügbar, sonst SOC-Fallback.
    if schema not in (SCHEMA_STACK100,):
        try:
            mod_data = data.get("module_data", {})
            total_remain_kwh = 0.0
            total_usable_kwh = 0.0
            valid_modules    = 0
            if schema != SCHEMA_DL5:
                for mod in mod_data.values():
                    remain_ah = _to_float(mod.get("remain_ah"))
                    total_ah  = _to_float(mod.get("total_ah"))
                    voltage   = _to_float(mod.get("voltage"))
                    if (remain_ah is not None and total_ah is not None
                            and voltage is not None
                            and total_ah > 0 and voltage > 10):
                        total_remain_kwh += remain_ah * voltage / 1000
                        total_usable_kwh += total_ah  * voltage / 1000
                        valid_modules    += 1
            if valid_modules > 0 and total_usable_kwh > 0:
                data["usableKwh"]    = round(total_usable_kwh, 3)
                data["remainingKwh"] = round(total_remain_kwh, 3)
                _LOGGER.debug(
                    "Dyness: usableKwh=%.3f remainingKwh=%.3f (aus %d Modulen via Ah)",
                    total_usable_kwh, total_remain_kwh, valid_modules,
                )
            else:
                # Fallback: batteryCapacity × SOH × SOC
                bc  = _to_float(data.get("batteryCapacity"))
                soc = _to_float(data.get("soc"))
                soh = _to_float(data.get("soh"))
                if bc is not None and soc is not None:
                    soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
                    usable    = round(bc * soh_factor, 3)
                    remaining = round(usable * (soc / 100), 3)
                    data["usableKwh"]    = usable
                    data["remainingKwh"] = remaining
                    _LOGGER.debug(
                        "Dyness: usableKwh=%.3f remainingKwh=%.3f "
                        "(SOC-Fallback: bc=%.3f × soh=%.1f%% × soc=%.1f%%)",
                        usable, remaining, bc, soh_factor * 100, soc,
                    )
        except (ValueError, TypeError):  # pragma: no cover - inputs pass through _to_float
            pass
    return data, alarm_texts


def parse_module_points(sn: str, mid: str, pts: dict) -> dict:
    """Parst Sub-Modul Datenpunkte.

    Smart Detection:
    - Stack100:  Point 11000 (Modul-Nr) + 10010 (Sub-SN) → 16 Zellen (11200-12700)
    - Tower T14: Point 10000 fehlt, Point 11200 vorhanden → 30 Zellen (11200-14100)
    - DL5.0C:    Point 10000 vorhanden, Point 10300 vorhanden → 16 Zellen (10300-11800)
    """
    def g(key): return pts.get(key) if pts.get(key) not in (None, "") else None

    d = {"sn": sn, "module_id": mid}
    has_module_sn = pts.get("10000") is not None
    is_stack100   = pts.get("10010") is not None and pts.get("11000") is not None
    is_tower      = not has_module_sn and not is_stack100 and pts.get("11200") is not None
    is_dl5        = has_module_sn and not is_stack100 and pts.get("10300") is not None
    # Tower Pro TP7 Sub-Module haben das gleiche Schema wie Stack100 (10010+11000),
    # aber 30 Zellen (Point 11100 = 30) statt 16 und andere Temperaturen.
    cell_count_pt = _to_float(pts.get("11100")) if is_stack100 else None
    is_tp7_module = is_stack100 and cell_count_pt is not None and int(cell_count_pt) == 30

    if is_stack100 and not is_tp7_module:
        # Stack100: physische Zellanzahl aus Point 11100 lesen und speichern.
        # cell_count wird in sensor.py genutzt um nur vorhandene Zellen zu registrieren.
        phys_cells = int(cell_count_pt) if cell_count_pt is not None else 16
        d["cell_count"] = phys_cells
        # 16 Zellen, Points 11200-12700 (Schritte 100)
        # Temperaturen: 14300-14600 (4 Sensoren)
        cells = []
        for i in range(1, 17):
            pid = str(11100 + i * 100)  # 11200, 11300, ..., 12700
            v = _to_float(pts.get(pid))
            d[f"cell_{i:02d}"] = v
            if v is not None and v > 0:
                cells.append(v)
        # Temperaturen
        temps = [_to_float(pts.get(str(14300 + i * 100))) for i in range(4)]
        temps_valid = [t for t in temps if t is not None and t > 0]
        if temps_valid:
            d["cell_temp_1"] = temps_valid[0] if len(temps_valid) > 0 else None
            d["cell_temp_2"] = temps_valid[1] if len(temps_valid) > 1 else None
        d["module_number"] = _to_float(pts.get("11000"))

    elif is_tp7_module:
        # Tower Pro TP7 Sub-Module: 30 Zellen, Points 11200-14100 (Schritte 100)
        # Temperaturen: 14300-15000 (bis zu 8 Sensoren, aktive per Point 14200)
        # SOC, SOH, Spannung, Strom, Zyklen nur auf Master-Ebene verfügbar — nicht pro Modul
        d["is_tp7"] = True
        cells = []
        for i in range(1, 31):
            pid = str(11100 + i * 100)  # 11200, ..., 14100
            v = _to_float(pts.get(pid))
            d[f"cell_{i:02d}"] = v
            if v is not None and v > 0:
                cells.append(v)
        temps_valid = []
        for i in range(8):
            t = _to_float(pts.get(str(14300 + i * 100)))
            if t is not None and t > 0:
                temps_valid.append(t)
        if temps_valid:
            d["cell_temp_1"] = temps_valid[0]
            d["cell_temp_2"] = temps_valid[1] if len(temps_valid) > 1 else None
        d["module_number"] = _to_float(pts.get("11000"))

    elif is_tower:
        # Tower T14: 30 Zellen, Points 11200-14100
        d["cell_temp_1"] = _to_float(g("14300"))
        d["cell_temp_2"] = _to_float(g("14400"))
        cells = []
        for i in range(1, 31):
            pid = str(11100 + i * 100)
            v = _to_float(pts.get(pid))
            d[f"cell_{i:02d}"] = v
            if v is not None and v > 0:
                cells.append(v)

    elif is_dl5:
        # DL5.0C / PowerBox Pro: 16 Zellen, Points 10300-11800
        # SOC/SOH nur wenn plausibel (≤ 100%) — PowerBox Pro liefert hier andere Werte
        soc_raw = _to_float(g("14000"))
        soh_raw = _to_float(g("14100"))
        # Point 14000 / 14100 können entweder SOC/SOH in % (≤100)
        # oder Ah-Kapazitätswerte sein (>100, z.B. 132 Ah, 200 Ah).
        # Powerbox Pro liefert Ah-Werte → als remain_ah/total_ah speichern.
        if soc_raw is not None and soc_raw <= 100:
            d["soc"] = soc_raw
        if soh_raw is not None and soh_raw <= 100:
            d["soh"] = soh_raw
        # Ah-Kapazität: 14000/14100 bevorzugt (direkte Messung vom BMS),
        # 13600/13800 als Fallback (alternative Einheit, weniger zuverlässig).
        cap14000 = _to_float(g("14000"))
        cap14100 = _to_float(g("14100"))
        if cap14000 is not None and cap14000 > 100:
            # Ah-Werte (nicht SOC%) → für kWh-Berechnung verwenden
            d["remain_ah"] = cap14000
            d["total_ah"]  = cap14100 if cap14100 is not None else None
        else:
            # Fallback: Point 13600/13800
            d["remain_ah"] = _to_float(g("13600"))
            d["total_ah"]  = _to_float(g("13800"))
        d["cycle_count"] = _to_float(g("13900"))
        d["bms_temp"]    = _to_float(g("12400"))
        d["cell_temp_1"] = _to_float(g("12500"))
        d["cell_temp_2"] = _to_float(g("12600"))
        d["voltage"]     = _to_float(g("13500"))
        d["current"]     = _to_float(g("13400"))
        # Firmware per Sub-Modul (Point 10100) — PowerBox G2 hat zwei verschiedene Versionen
        fw = pts.get("10100")
        if fw:
            d["firmwareVersion"] = str(fw)
        cells = []
        for i in range(1, 17):
            pid = str(10200 + i * 100)
            v = _to_float(pts.get(pid))
            d[f"cell_{i:02d}"] = v
            if v is not None and v > 0:
                cells.append(v)
        alarm = any(int(pts.get(str(14300 + i * 100)) or 0) != 0 for i in range(16))
        d["has_alarm"] = alarm
    else:
        cells = []

    if cells:
        d["cell_voltage_max"]       = max(cells)
        d["cell_voltage_min"]       = min(cells)
        d["cell_voltage_spread_mv"] = round((max(cells) - min(cells)) * 1000, 1)

    return d
