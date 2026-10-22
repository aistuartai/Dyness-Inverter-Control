"""Direct tests of the pure parsers: missing, zero and odd inputs.

The end-to-end model tests feed complete data; these cover the other side of
every guard (point absent, value 0, unknown code) without spinning up HA.
"""
from __future__ import annotations

import pytest

from custom_components.dyness_battery_cygni.parsers import (
    ParseInput,
    build_data,
    flatten_v2,
    parse_module_points,
    realtime_points,
)

MODELS = ["STACK100-8S", "Junior Box", "DL5.0C", "PowerBox Pro", "TOWER-T14", "PowerBox G2",
          "PowerDepot G2", "Cygni 10.0HS-M8", "Mystery 9000"]

# Every master point id any schema reads.
ALL_POINTS = [
    "170", "171", "172", "400", "600", "700", "800", "1100", "1200", "1300", "1401", "1402", "1500", "1600",
    "1601", "1602", "1700", "1800", "1900", "1901", "2000", "2003", "2004", "2005", "2010", "2011", "2100",
    "2101", "2300", "2400", "2500", "2600", "2700", "2800", "2900", "3000", "3200", "3201", "3202", "3300",
    "3400", "3500", "3600", "3700", "3800", "3900", "4000", "4100", "4400", "7100", "7200", "7300", "7400",
    "12400", "12500", "12600", "12700", "12800", "13500", "13900", "18600", "18700", "18800", "19200",
    "195-196", "197", "198-199", "200", "9999999",
]


def _src(model: str, **kw) -> ParseInput:
    return ParseInput(device_sn="SN", device_info={"deviceModelName": model}, **kw)


@pytest.mark.parametrize("model", MODELS)
def test_no_points_no_crash(model: str) -> None:
    data, alarms = build_data(_src(model), {})
    assert alarms == [] and data["alarmText"] == "OK"
    assert data["batteryCapacity"] is None


@pytest.mark.parametrize("model", MODELS)
def test_zero_points_skip_limits_and_capacity(model: str) -> None:
    rt = {pid: "0" for pid in ALL_POINTS}
    data, _ = build_data(_src(model, realtime_data=rt, station_info={"batteryCapacity": "5.12"},
                              module_sns=["M1"]), {})
    for key in ("chargeCurrentLimit", "dischargeCurrentLimit", "chargeVoltageLimit", "dischargeVoltageLimit"):
        assert key not in data, key


@pytest.mark.parametrize("model", MODELS)
def test_equal_min_max_temperatures_collapse(model: str) -> None:
    rt = {pid: "25" for pid in ALL_POINTS}
    data, _ = build_data(_src(model, realtime_data=rt), {"soc": "50"})
    assert "tempMin" not in data or data["tempMin"] != data.get("tempMax")
    assert "tempBmsMin" not in data or data["tempBmsMin"] != data.get("tempBmsMax")


def test_power_records_all_without_soc_fall_back_to_last() -> None:
    data, _ = build_data(_src("Junior Box"), [{"soc": None, "realTimePower": "1"}, {"soc": None, "realTimePower": "2"}])
    assert data["realTimePower"] == "2"


def test_empty_power_list() -> None:
    data, _ = build_data(_src("Junior Box"), [])
    assert data["batteryStatus"] == "Standby"


def test_comm_status_falls_back_to_device_info() -> None:
    src = ParseInput(device_info={"deviceModelName": "Junior Box", "deviceCommunicationStatus": "Online"})
    assert build_data(src, {})[0]["deviceCommunicationStatus"] == "Online"


def test_station_name_fallbacks() -> None:
    assert build_data(ParseInput(storage_info={"stationName": "Shed"}), {})[0]["stationName"] == "Shed"
    assert build_data(ParseInput(), {})[0]["stationName"] == "Dyness Battery"


@pytest.mark.parametrize(("power", "status"), [("11", "Charging"), ("-11", "Discharging"), ("10", "Standby"),
                                               ("abc", None)])
def test_battery_status_thresholds(power: str, status) -> None:
    data, _ = build_data(_src("Junior Box"), {"realTimePower": power})
    assert data.get("batteryStatus") == status


# ── getLastRunningDataBySn ───────────────────────────────────────────────────

def test_running_data_fallbacks_and_codes() -> None:
    running = {
        "batterySoc": 55, "batteryPower": "-300", "batterySoh": "97", "batteryTemperature": "21.5",
        "gridPower": "0", "gridStatus": "9", "runModel": "1", "workStatus": "4",
        "chargingLimitCurrent": "0", "dischargeLimitCurrent": "64", "batteryVoltage": "397", "batteryCount": 4,
    }
    data, _ = build_data(_src("Mystery 9000", running_data=running), {})
    assert data["soc"] == "55"                       # power data had none
    assert data["realTimePower"] == -300.0
    assert (data["soh"], data["temp"]) == (97.0, 21.5)
    assert data["gridPower"] == 0                    # zero is not negated to -0
    assert data["gridStatus"] == "9"                 # unknown codes pass through
    assert data["runModel"] == "Feed-in Priority"    # spec p14 says 1 = off-grid (unverified)
    assert data["inverterWorkStatus"] == "Programming"
    assert "chargeCurrentLimit" not in data and data["dischargeCurrentLimit"] == 64.0


def test_running_grid_power_negated() -> None:
    data, _ = build_data(_src("Mystery 9000", running_data={"gridPower": "250"}), {})
    assert data["gridPower"] == -250.0               # API + = export; HA convention + = import


# ── v2 ───────────────────────────────────────────────────────────────────────

def test_v2_unknown_codes_pass_through() -> None:
    v2 = {"bmsCommunicationStatus": "7", "meterType": "0", "meterCommunicationStatus": "1", "bmsSoftwareVersion": "1.2"}
    status = {"sparePower": "0", "powerLimit": "2"}
    data, _ = build_data(_src("Cygni 10.0HS-M8", v2_realtime_data=v2, v2_status_data=status), {})
    assert data["bmsCommunicationStatus"] == "7"     # unknown codes pass through
    assert data["meterType"] == "Not Connected"      # spec p17
    assert data["meterCommunicationStatus"] == "Abnormal"
    assert data["bmsSoftwareVersion"] == "1.2"
    assert (data["sparePower"], data["powerLimitActive"]) == ("Off", "2")


@pytest.mark.parametrize(("raw", "label"), [("0", "Abnormal"), ("1", "Normal")])
def test_bms_communication_one_is_normal(raw: str, label: str) -> None:
    data, _ = build_data(_src("Cygni 10.0HS-M8", v2_realtime_data={"bmsCommunicationStatus": raw}), {})
    assert data["bmsCommunicationStatus"] == label


def test_v2_sparse_payloads_leave_fields_unset() -> None:
    src = _src("Cygni 10.0HS-M8", v2_realtime_data={"apparentPower": "5"}, v2_status_data={"runModel": "3"})
    data, _ = build_data(src, {})
    assert data["apparentPower"] == 5.0
    assert not {"bmsCommunicationStatus", "bmsSoftwareVersion", "meterType", "meterCommunicationStatus",
                "sparePower", "powerLimitActive"} & data.keys()


def test_flatten_v2_ignores_non_dict_values() -> None:
    assert flatten_v2({"a": {"x": 1}, "b": None, "c": "s", "d": {"y": 2}}) == {"x": 1, "y": 2}


def test_realtime_points_skips_malformed_items() -> None:
    assert realtime_points([{"pointId": "1", "pointValue": "a"}, {"x": 1}, "junk"]) == {"1": "a"}


# ── Alarms ───────────────────────────────────────────────────────────────────

def test_bms_alarm_bits_decoded() -> None:
    data, alarms = build_data(_src("Junior Box", realtime_data={"3201": "1", "3307": "1", "5104": "1"}), {})
    assert alarms == ["Cell voltage consistency warning", "Charge overcurrent", "System fault"]
    assert data["alarmText"] == ", ".join(alarms)


def test_tp7_alarm_flags() -> None:
    rt = {"4400": "1", "4402": "1", "4403": "0"}
    data, _ = build_data(_src("TOWER-PRO-TP7", realtime_data=rt), {})
    assert (data["alarmSpreadV"], data["alarmSpreadT"], data["alarmTotal"]) == (True, False, "1")


def test_powerdepot_status_paths() -> None:
    # The PowerDepot block reads batteryStatus as delivered in the power payload
    # (it is recalculated from realTimePower only afterwards).
    for status in ("Charging", "Discharging"):
        data, _ = build_data(_src("PowerDepot G2", storage_info={"workStatus": "Fault"}), {"batteryStatus": status})
        assert data["workStatus"] == status
    rt = {"3200": "1"}
    data, _ = build_data(_src("PowerDepot G2", realtime_data=rt, storage_info={"workStatus": "Fault"}), {})
    assert data["workStatus"] == "Fault" and data["alarmStatus"] is True


def test_powerdepot_capacity_falls_back_to_module_count() -> None:
    src = _src("PowerDepot G2", station_info={"batteryCapacity": "5"}, module_sns=["M1", "M2"])
    assert build_data(src, {})[0]["batteryCapacity"] == 10.0


def test_powerdepot_aggregates_module_cycles_and_temps() -> None:
    data = {"soc": "50", "module_data": {"M1": {"cycleCount": 10, "temp": 20}, "M2": {"cycleCount": 20, "temp": 30}}}
    out, _ = build_data(_src("PowerDepot G2"), data)
    # module_data from the power payload is replaced by the coordinator's module data afterwards,
    # but the PowerDepot block reads it first.
    assert (out["cycleCount"], out["temp"]) == (15.0, 25.0)


def test_ah_strategy_ignored_for_low_voltage_modules() -> None:
    mods = {"M1": {"remain_ah": 50, "total_ah": 100, "voltage": 5}}
    src = _src("PowerBox Pro", station_info={"batteryCapacity": "10"}, module_data=mods)
    data, _ = build_data(src, {"soc": "50"})
    assert data["usableKwh"] == 10.0 and data["remainingKwh"] == 5.0


@pytest.mark.parametrize("soh", [None, "150"])
def test_capacity_soh_factor_ignores_missing_or_implausible_soh(soh) -> None:
    rt = {"2010": "50"} | ({"2011": soh} if soh else {})
    data, _ = build_data(_src("Cygni 10.0HS-M8", realtime_data=rt, station_info={"batteryCapacity": "10"}), {})
    assert data["usableKwh"] == 10.0


def test_cell_diff_ignores_bad_values() -> None:
    data, _ = build_data(_src("Junior Box", realtime_data={"1300": "0", "1500": "3.3"}), {})
    assert "cellVoltageDiffMv" not in data


def test_powerbox_g2_firmware_from_storage_list() -> None:
    src = ParseInput(device_info={"deviceModelName": "PowerBox G2", "firmwareVersion": None},
                     storage_info={"firmwareVersion": "2.0"})
    assert build_data(src, {})[0]["firmwareVersion"] == "2.0"


def test_running_data_does_not_override_values_already_set() -> None:
    running = {"batterySoc": 10, "batteryPower": "5", "batterySoh": "50", "batteryTemperature": "5"}
    rt = {"2010": "80", "2011": "99", "2003": "30", "172": "100"}          # Cygni points win
    data, _ = build_data(_src("Cygni 10.0HS-M8", realtime_data=rt, running_data=running), {})
    assert (data["soc"], data["soh"], data["temp"], data["realTimePower"]) == ("80", "99", "30", -100.0)
    assert "gridPower" not in data


def test_cygni_without_energy_totals() -> None:
    data, _ = build_data(_src("Cygni 10.0HS-M8", realtime_data={"2010": "50"}), {})
    assert not {"batteryChargeTotal", "batteryDischargeTotal", "batteryChargeToday",
                "batteryDischargeToday"} & data.keys()


@pytest.mark.parametrize(("point", "key"), [
    ("195-196", "batteryChargeTotal"), ("198-199", "batteryDischargeTotal"),
    ("197", "batteryChargeToday"), ("200", "batteryDischargeToday"),
])
def test_cygni_energy_totals_individually(point: str, key: str) -> None:
    data, _ = build_data(_src("Cygni 10.0HS-M8", realtime_data={point: "12.5"}), {})
    assert data[key] == 12.5


def test_stack100_module_single_temperature() -> None:
    d = parse_module_points("M", "M", {"10010": "x", "11000": "1", "11200": "3.3", "14300": "21"})
    assert (d["cell_temp_1"], d["cell_temp_2"]) == (21.0, None)


# ── Module points ────────────────────────────────────────────────────────────

def test_stack100_module_defaults_to_16_cells_without_point_11100() -> None:
    d = parse_module_points("M", "M", {"10010": "x", "11000": "1", "11200": "3.3"})
    assert d["cell_count"] == 16 and "cell_temp_1" not in d


def test_tp7_module_without_temperatures() -> None:
    d = parse_module_points("M", "M", {"10010": "x", "11000": "1", "11100": "30", "11200": "3.3"})
    assert d["is_tp7"] is True and "cell_temp_1" not in d


def test_tower_module() -> None:
    d = parse_module_points("M", "M", {"11200": "3.3", "11300": "3.4", "14300": "", "14400": "22"})
    assert (d["cell_temp_1"], d["cell_temp_2"]) == (None, 22.0)
    assert d["cell_voltage_spread_mv"] == 100.0


def test_dl5_module_amp_hour_fallback_and_alarm() -> None:
    d = parse_module_points("M", "M", {"10000": "s", "10300": "3.3", "13600": "80", "13800": "100",
                                       "14000": "", "14300": "1"})
    assert (d["remain_ah"], d["total_ah"], d["has_alarm"]) == (80.0, 100.0, True)
    assert "firmwareVersion" not in d


def test_unknown_module_layout() -> None:
    assert parse_module_points("M", "M", {"1": "2"}) == {"sn": "M", "module_id": "M"}
