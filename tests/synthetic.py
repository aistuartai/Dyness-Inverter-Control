"""Synthetic API data for the Dyness models we have no real capture of.

SYNTHETIC — hand-built from the point ids the integration's parser reads
(``__init__._async_update_data`` / ``_parse_module_points``), not from real
devices. Values are plausible, not measured. They exist to exercise each
schema's parsing path; when a real capture for a model turns up, it replaces
the synthetic one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from .conftest import READ_ENDPOINTS, URL_PREFIX

OK = "200"


def _cells(start: int, count: int, base: float = 3.30, step: int = 100) -> dict[str, str]:
    """``count`` cell voltages at point ids start, start+step, ... spread over 40 mV."""
    return {str(start + i * step): f"{base + (i % 5) * 0.01:.3f}" for i in range(count)}


@dataclass
class SyntheticDevice:
    model: str
    master_sn: str
    master_points: dict[str, str]
    modules: dict[str, dict[str, str]] = field(default_factory=dict)
    station_capacity: str = "5.12"
    work_status: str = "Normal"
    power: dict = field(default_factory=lambda: {
        "soc": "65", "realTimePower": "-1200", "realTimeCurrent": "-23.4", "createTime": "2026-10-22 12:00:00",
    })
    firmware: str | None = "1.0.0"

    # ── response builders ───────────────────────────────────────────────
    def _device(self) -> dict:
        return {
            "deviceSn": self.master_sn, "collectorSn": None, "deviceModelName": self.model,
            "deviceName": self.model, "stationName": "Test Station", "workStatus": self.work_status,
            "deviceCommunicationStatus": "Online", "firmwareVersion": self.firmware,
        }

    def _realtime(self, sn: str) -> dict:
        if sn == self.master_sn:
            points = dict(self.master_points)
            if self.modules:
                points["SUB"] = ",".join([*self.modules, f"{self.master_sn}-BMS"])
        else:
            points = self.modules.get(sn, {})
        return {"code": OK, "data": [{"pointId": k, "pointValue": v} for k, v in points.items()]}

    def response(self, path: str, body: dict) -> dict:
        sn = body.get("deviceSn")
        match path:
            case "/v1/device/storage/list":
                return {"code": OK, "data": {"list": [self._device()]}}
            case "/v1/device/household/storage/detail":
                return {"code": OK, "data": self._device()}
            case "/v1/device/bindSn":
                return {"code": "500", "data": None, "info": "The device is bound by someone else"}
            case "/v1/station/info":
                return {"code": OK, "data": {"batteryCapacity": self.station_capacity}}
            case "/v1/device/realTime/data":
                return self._realtime(sn)
            case "/v1/device/getLastRunningDataBySn":
                return {"code": OK, "data": {}}          # battery-only systems: no inverter data
            case "/v1/device/getLastPowerDataBySn":
                return {"code": OK, "data": [self.power]}
            case _:                                       # v2 is Cygni-only
                return {"code": "404", "data": None, "info": "Not Found"}

    def register(self, aioclient_mock: AiohttpClientMocker) -> None:
        for path in READ_ENDPOINTS:
            async def handler(method, url, data, _path=path):
                return AiohttpClientMockResponse(method, url, json=self.response(_path, json.loads(data)))
            aioclient_mock.post(f"{URL_PREFIX}{path}", side_effect=handler)


# ── Models ──────────────────────────────────────────────────────────────────

def tower_t14() -> SyntheticDevice:
    module = {**_cells(11200, 30), "14300": "23.5", "14400": "24.1"}
    return SyntheticDevice(
        model="TOWER-T14", master_sn="FAKETWR0001",
        master_points={
            "1400": "1", "1500": "98", "1600": "11.2", "1700": "14.2", "1800": "120", "1900": "3500",
            "2400": "3.35", "2700": "3.31", "3000": "24", "3300": "21",
            "5001": "0", "5002": "1", "5003": "0", "5101": "0", "5102": "0", "5104": "0", "9999999": "1",
        },
        modules={"FAKETWR0001-M1": module, "FAKETWR0001-M2": module},
    )


def tower_pro_tp7() -> SyntheticDevice:
    module = {"10010": "FAKETP7-SUB", "11000": "1", "11100": "30", **_cells(11200, 30),
              "14200": "2", "14300": "22.0", "14400": "22.6"}
    return SyntheticDevice(
        model="Tower Pro TP7", master_sn="FAKETP70001",
        master_points={
            "1500": "100", "1600": "6.1", "1700": "7.1", "1800": "40", "1900": "900",
            "2400": "3.34", "2700": "3.30", "3000": "21", "3300": "20",
            "4400": "0", "4402": "0", "4403": "0", "4500": "0", "4600": "0", "4700": "0", "4800": "0", "4900": "0",
        },
        modules={"FAKETP70001-M1": module, "FAKETP70001-M2": {**module, "11000": "2"}},
    )


def stack100() -> SyntheticDevice:
    module = {"10010": "FAKESTK-SUB", "11000": "1", "11100": "16", **_cells(11200, 16),
              "14300": "25.0", "14400": "25.5", "14500": "0", "14600": "0"}
    return SyntheticDevice(
        model="STACK100-8S", master_sn="FAKESTK0001",
        master_points={
            "1100": "409.6", "1500": "99", "1600": "60.5", "1700": "81.9", "1800": "15", "1900": "1200",
            "2000": "100", "2100": "100", "2400": "3.34", "2500": "3", "2600": "7", "2700": "3.30",
            "2800": "5", "2900": "12", "3000": "26", "3300": "25", "4000": "1",
            "5001": "0", "5002": "0", "5003": "0", "5101": "0", "5102": "0", "5104": "0", "9999999": "0",
        },
        modules={"FAKESTK0001-M1": module, "FAKESTK0001-M2": {**module, "11000": "2"}},
    )


def _dl5_style_module(soc: str = "95", soh: str = "100") -> dict[str, str]:
    return {
        "10000": "FAKEMODSN", "10100": "V2.1", **_cells(10300, 16), "12400": "27.0", "12500": "25.0",
        "12600": "25.4", "13400": "-10.2", "13500": "52.1", "13600": "95", "13800": "100", "13900": "210",
        "14000": soc, "14100": soh,
    }


def dl5() -> SyntheticDevice:
    return SyntheticDevice(
        model="DL5.0C", master_sn="FAKEDL50001",
        master_points={
            "600": "52.1", "1200": "100", "1300": "3.34", "1401": "1", "1402": "4", "1500": "3.30",
            "1601": "2", "1602": "9", "1800": "25", "2300": "30", "2800": "28", "3000": "26",
            "3200": "0", "3300": "0", "3600": "57.6", "3700": "44.8", "3800": "100", "3900": "100",
            "4000": "0", "4100": "0", "7100": "800", "7200": "4.1", "7300": "760", "7400": "3.8",
        },
        modules={"FAKEDL50001-M1": _dl5_style_module(), "FAKEDL50001-M2": _dl5_style_module("93", "99")},
    )


def junior_box() -> SyntheticDevice:
    return SyntheticDevice(
        model="Junior Box", master_sn="FAKEJNR0001",
        master_points={
            "600": "51.8", "800": "65", "1200": "100", "1300": "3.33", "1500": "3.31", "1800": "24",
            "2300": "29", "2800": "27", "3000": "27", "3200": "0", "3300": "0", "3600": "57.6",
            "3700": "44.8", "4000": "1", "4100": "0", "7100": "300", "7200": "2.0", "7300": "290", "7400": "1.9",
        },
    )


def powerbox_pro() -> SyntheticDevice:
    module = {**_dl5_style_module(), "14000": "132", "14100": "200"}   # Ah values, not %
    return SyntheticDevice(
        model="PowerBox Pro", master_sn="FAKEPBP0001", station_capacity="10.24",
        master_points={
            "600": "52.0", "700": "-15.0", "800": "70", "1200": "97", "1300": "3.34", "1401": "1",
            "1402": "3", "1500": "3.31", "1601": "2", "1602": "8", "1800": "26", "2000": "24",
            "2300": "31", "3000": "28", "3200": "0", "3300": "0", "3600": "57.6", "3700": "44.8",
            "3800": "200", "3900": "200", "4100": "0",
        },
        modules={"FAKEPBP0001-M1": module, "FAKEPBP0001-M2": module},
    )


def powerbox_g2() -> SyntheticDevice:
    return SyntheticDevice(
        model="PowerBox G2", master_sn="FAKEPG20001", station_capacity="10.24", firmware=None,
        master_points={
            "13500": "52.3", "13900": "88", "12400": "28.0", "12500": "25.0", "12600": "25.6",
            "12700": "26.1", "12800": "0", **_cells(10300, 16),
            "18600": "100", "18700": "57.6", "18800": "44.8", "19200": "100",
        },
    )


def powerdepot_g2() -> SyntheticDevice:
    return SyntheticDevice(
        model="PowerDepot G2", master_sn="FAKEPDG0001", station_capacity="5.12", work_status="Fault",
        power={"soc": "80", "realTimePower": "0", "realTimeCurrent": "0", "createTime": "2026-10-22 12:00:00"},
        master_points={
            "400": "3", "600": "52.2", "700": "0", "800": "80", "1200": "99", "1300": "3.34", "1401": "2",
            "1402": "5", "1500": "3.30", "1601": "1", "1602": "11", "1800": "26", "1901": "2", "2000": "23",
            "2101": "3", "2300": "30", "2800": "27", "3000": "26", "3200": "0", "3201": "0", "3202": "0",
            "3300": "0", "3400": "0", "3500": "0", "3600": "57.6", "3700": "44.8", "3800": "100", "3900": "100",
        },
        modules={"FAKEPDG0001-M1": _dl5_style_module(), "FAKEPDG0001-M2": _dl5_style_module()},
    )


def unknown_model() -> SyntheticDevice:
    return SyntheticDevice(model="Mystery 9000", master_sn="FAKEUNK0001", master_points={"42": "1"})


MODELS = {
    "tower_t14": tower_t14,
    "tower_pro_tp7": tower_pro_tp7,
    "stack100": stack100,
    "dl5": dl5,
    "junior_box": junior_box,
    "powerbox_pro": powerbox_pro,
    "powerbox_g2": powerbox_g2,
    "powerdepot_g2": powerdepot_g2,
    "unknown": unknown_model,
}
