"""Request bodies for the Dyness write endpoints (pure, validated).

On/off fields do NOT share one convention:

- TOU group fields (state, dodState, socMaxChargeState): 1 = On. The PDF spec
  (v1.0 p.30) says 0 = On, but the hardware is the opposite (fixed in 2.5.6).
- Peak and Load Control fields: 0 = On ("0: Open / 1: Close", spec p.33/p.35).
  Confirmed for peakControlEnable on a Cygni 2026-10-25: sending 1 turned Peak
  Control off. The load-control fields follow the spec but are unverified.
"""
from __future__ import annotations

import re

from homeassistant.exceptions import HomeAssistantError

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")   # spec p30: HH:mm, nothing else

WORK_MODES = {"0", "2", "3"}   # Self-use, Backup, TOU. Off-grid (1) must be set on the inverter.


def _on_off(value) -> str:
    """TOU fields: 1 = On."""
    return "1" if value else "0"


def _open_close(value) -> str:
    """Peak / Load Control fields: 0 = Open (on), 1 = Close (off)."""
    return "0" if value else "1"


def work_mode(device_sn: str | None, mode: str) -> dict:
    if mode not in WORK_MODES:
        raise HomeAssistantError(
            f"Work mode '{mode}' not allowed. Permitted values: {WORK_MODES}. "
            "Off-grid mode (1) must be set manually on the inverter."
        )
    return {"deviceSn": device_sn, "workMode": mode, "workGroups": []}


def tou_schedule(device_sn: str | None, groups: list) -> dict:
    """Full TOU schedule. Always switches the inverter to TOU mode (workMode=3)."""
    if not isinstance(groups, list) or not groups:
        raise HomeAssistantError("groups must be a non-empty list")

    api_groups = []
    for g in groups:
        group_num = int(g["group"])
        if group_num not in (1, 2, 3, 4):
            raise HomeAssistantError(f"group must be 1-4, got {group_num}")
        power = int(g.get("power", 0))
        if not 0 <= power <= 10000:
            raise HomeAssistantError(f"power must be 0-10000W, got {power}")
        dod = int(g.get("dod", 20))
        if not 0 <= dod <= 95:
            raise HomeAssistantError(f"dod must be 0-95%, got {dod}")
        for key in ("start_time", "end_time"):
            if not _HHMM.match(str(g[key])):
                raise HomeAssistantError(f"{key} must be HH:mm, got {g[key]!r}")
        soc_max = int(g.get("soc_max", 100))
        if not 10 <= soc_max <= 100:
            raise HomeAssistantError(f"soc_max must be 10-100%, got {soc_max}")
        api_groups.append({
            "batteryWorkGroup":    group_num,
            "state":               _on_off(g.get("enabled", True)),
            "startTime":           str(g["start_time"]),
            "endTime":             str(g["end_time"]),
            "power":               str(power),
            "mode":                "0" if str(g.get("mode", "charge")).lower() == "charge" else "1",
            "week":                str(g.get("days", "0,1,2,3,4,5,6")),
            "dodState":            _on_off(g.get("dod_enabled", True)),
            "dod":                 str(dod),
            "socMaxChargeState":   _on_off(g.get("soc_max_enabled", True)),
            "batterySocMaxCharge": str(soc_max),
        })
    return {"deviceSn": device_sn, "workMode": "3", "workGroups": api_groups}


def battery_setting(device_sn: str | None, data: dict) -> dict:
    return {
        "deviceSn":                  device_sn,
        "onGridDischargeDod":        str(data["on_grid_dod"]),
        "offGridDischargeDod":       str(data["off_grid_dod"]),
        "batteryChargeLimit":        str(data["charge_limit"]),
        "batteryOffGridChargeLimit": str(data["off_grid_charge_limit"]),
    }


def peak_control(device_sn: str | None, data: dict) -> dict:
    return {
        "deviceSn":          device_sn,
        "peakControlEnable": _open_close(data["enabled"]),
        "triggerSoc":        str(data["trigger_soc"]),
        "timeRange":         f"{data['start_time']}-{data['end_time']}",
        "peakControlPower":  str(data["power"]),
    }


def load_control(device_sn: str | None, data: dict) -> dict:
    return {
        "deviceSn":              device_sn,
        "loadSwitch":            _open_close(data["load_switch"]),
        "forceCloseTime":        f"{data['force_close_start']}-{data['force_close_end']}",
        "forceCloseOffGridOnly": _open_close(data["force_close_off_grid_only"]),
        "alwaysCloseOnGridMode": _open_close(data["always_close_on_grid"]),
        "relayCloseSoc":         str(data["relay_close_soc"]),
        "relayOpenSoc":          str(data["relay_open_soc"]),
        "pvPowerThreshold":      str(data["pv_power_threshold"]),
    }
