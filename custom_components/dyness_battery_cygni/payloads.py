"""Request bodies for the Dyness write endpoints (pure, validated).

On/off fields use 1 = On. The PDF spec (v1.0 p.30) says 0 = On for the TOU
fields; hardware does the opposite (fixed in 2.5.6). The peak/load fields
follow the same convention but are not yet verified on hardware.
"""
from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

WORK_MODES = {"0", "2", "3"}   # Self-use, Backup, TOU. Off-grid (1) must be set on the inverter.


def _on_off(value) -> str:
    return "1" if value else "0"


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
        "peakControlEnable": _on_off(data["enabled"]),
        "triggerSoc":        str(data["trigger_soc"]),
        "timeRange":         f"{data['start_time']}-{data['end_time']}",
        "peakControlPower":  str(data["power"]),
    }


def load_control(device_sn: str | None, data: dict) -> dict:
    return {
        "deviceSn":              device_sn,
        "loadSwitch":            _on_off(data["load_switch"]),
        "forceCloseTime":        f"{data['force_close_start']}-{data['force_close_end']}",
        "forceCloseOffGridOnly": _on_off(data["force_close_off_grid_only"]),
        "alwaysCloseOnGridMode": _on_off(data["always_close_on_grid"]),
        "relayCloseSoc":         str(data["relay_close_soc"]),
        "relayOpenSoc":          str(data["relay_open_soc"]),
        "pvPowerThreshold":      str(data["pv_power_threshold"]),
    }
