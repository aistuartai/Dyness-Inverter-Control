"""Pure helper functions: request signing, schema detection, intervals."""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import timedelta
from unittest.mock import patch

import pytest

from custom_components.dyness_battery_cygni import (
    SCHEMA_CYGNI,
    SCHEMA_DL5,
    SCHEMA_JUNIOR,
    SCHEMA_POWERBOX_G2,
    SCHEMA_POWERBOX_PRO,
    SCHEMA_POWERDEPOT,
    SCHEMA_STACK100,
    SCHEMA_TOWER,
    SCHEMA_UNKNOWN,
    _build_headers,
    _detect_schema,
    _get_md5,
    _get_signature,
    _is_success,
    _scan_interval_for_modules,
    _to_float,
)
from custom_components.dyness_battery_cygni.config_flow import _build_headers_cf, _select_bms_devices

DATE = "Thu, 22 Oct 2026 08:00:00 GMT"


# ── Signing (API spec v1.0 p.3-8) ────────────────────────────────────────────

def test_content_md5_is_base64_of_raw_digest() -> None:
    body = '{"deviceSn":"FAKESN000000000000001"}'
    assert _get_md5(body) == base64.b64encode(hashlib.md5(body.encode()).digest()).decode()


def test_signature_matches_spec_string_to_sign() -> None:
    # Spec: base64(HMAC-SHA1(secret, "POST\n{md5}\napplication/json\n{date}\n{path}")).
    md5 = _get_md5("{}")
    expected = base64.b64encode(
        hmac.new(b"secret", f"POST\n{md5}\napplication/json\n{DATE}\n/v1/x".encode(), "sha1").digest()
    ).decode()
    assert _get_signature("secret", md5, DATE, "/v1/x") == expected


def test_headers_sign_application_json_but_send_charset() -> None:
    # The signed content type is "application/json"; the header carries ";charset=UTF-8".
    # Same as the spec's Java example — the server accepts it.
    with patch("custom_components.dyness_battery_cygni._get_gmt_time", return_value=DATE):
        headers = _build_headers("id", "secret", "{}", "/v1/x")
    assert headers["Content-Type"] == "application/json;charset=UTF-8"
    assert headers["Date"] == DATE
    assert headers["Authorization"] == f"API id:{_get_signature('secret', _get_md5('{}'), DATE, '/v1/x')}"


def test_config_flow_signs_identically_to_coordinator() -> None:
    with patch("custom_components.dyness_battery_cygni._get_gmt_time", return_value=DATE), \
         patch("custom_components.dyness_battery_cygni.config_flow.formatdate", return_value=DATE):
        assert _build_headers_cf("id", "secret", "{}", "/v1/x") == _build_headers("id", "secret", "{}", "/v1/x")


# ── Response helpers ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(("result", "ok"), [
    ({"code": "200"}, True),
    ({"code": 200}, True),
    ({"code": "0"}, True),
    ({"code": 0}, True),
    ({"code": "500"}, False),
    ({"code": "401"}, False),
    ({}, False),
])
def test_is_success(result: dict, ok: bool) -> None:
    assert _is_success(result) is ok


@pytest.mark.parametrize(("raw", "expected"), [
    ("12.5", 12.5), (3, 3.0), ("-557", -557.0), (None, None), ("", None), ("--", None), ("abc", None),
])
def test_to_float(raw, expected) -> None:
    assert _to_float(raw) == expected


# ── Schema detection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(("model", "schema"), [
    ("Cygni 10.0HS-M8", SCHEMA_CYGNI),      # prefix match "CYGNI"
    ("TOWER-T14", SCHEMA_TOWER),
    ("Tower Pro TP7", SCHEMA_TOWER),
    ("STACK100-12S", SCHEMA_STACK100),      # prefix match on an unlisted variant
    ("DL5.0C", SCHEMA_DL5),
    ("PowerBox G2", SCHEMA_POWERBOX_G2),
    ("PowerBox Pro", SCHEMA_POWERBOX_PRO),
    ("PowerHaus", SCHEMA_POWERBOX_PRO),
    ("PowerDepot G2", SCHEMA_POWERDEPOT),
    ("Junior Box", SCHEMA_JUNIOR),
])
def test_detect_schema_by_model_name(model: str, schema: str) -> None:
    assert _detect_schema(model, {}) == schema


@pytest.mark.parametrize(("points", "schema"), [
    ({"1400": "1", "2400": "1"}, SCHEMA_TOWER),
    ({"1400": "1", "2700": "1"}, SCHEMA_TOWER),
    ({"800": "1"}, SCHEMA_JUNIOR),
    ({"13400": "1"}, SCHEMA_POWERDEPOT),
    ({"12400": "1"}, SCHEMA_POWERDEPOT),
    ({"42": "1"}, SCHEMA_UNKNOWN),
])
def test_detect_schema_point_fallback_for_unknown_models(points: dict, schema: str) -> None:
    assert _detect_schema("Mystery 9000", points) == schema


def test_detect_schema_handles_missing_model_name() -> None:
    assert _detect_schema(None, {}) == SCHEMA_UNKNOWN


# ── Poll interval ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("modules", "user_min", "minutes"), [
    (0, 5, 5), (2, 5, 5), (3, 5, 10), (4, 2, 10), (5, 5, 15), (8, 5, 15),
    (2, 15, 15),   # user floor wins when higher
    (8, 15, 15),
])
def test_scan_interval_for_modules(modules: int, user_min: int, minutes: int) -> None:
    assert _scan_interval_for_modules(modules, user_min) == timedelta(minutes=minutes)


# ── Config-flow device filtering ─────────────────────────────────────────────

def test_select_bms_devices_prefers_bms_suffix() -> None:
    devices = [{"deviceSn": "A-BMS"}, {"deviceSn": "A-MOD1"}, {"deviceSn": "B-BDU"}]
    assert _select_bms_devices(devices) == [{"deviceSn": "A-BMS"}, {"deviceSn": "B-BDU"}]


def test_select_bms_devices_falls_back_to_all() -> None:
    devices = [{"deviceSn": "FAKESN000000000000001"}]
    assert _select_bms_devices(devices) == devices
