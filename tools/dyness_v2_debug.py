"""
Dyness v2 API Debugger for Cygni HS/HA inverters.

Calls GetRealTimeDataBySN and GetStatusInfBySN directly and prints raw JSON.
Use this to verify v2 API access and see what field names are returned.

Usage:
    pip install requests
    python dyness_v2_debug.py
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import hashlib, hmac, base64, json, requests
from email.utils import formatdate

# ===== FILL IN YOUR CREDENTIALS =====
API_ID     = "YOUR_API_ID"
API_SECRET = "YOUR_API_SECRET"
DEVICE_SN  = "YOUR_DEVICE_SN"   # e.g. "CY123456789"
# =====================================

# EU endpoint — change if in another region
API_BASE_V2 = "https://eu-openapi.dyness.com/openapi/emsdevice"

# Other regions:
# API_BASE_V2 = "https://apacopen-api.dyness.com/openapi/emsdevice"  # Asia-Pacific
# API_BASE_V2 = "https://open-api.dyness.com/openapi/emsdevice"      # fallback


SEP = "=" * 60


def _sign(secret, method, md5, date, path):
    sts = f"{method}\n{md5}\napplication/json\n{date}\n{path}"
    return base64.b64encode(
        hmac.new(secret.encode(), sts.encode(), "sha1").digest()
    ).decode()


def _post(path, body_dict):
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    body = json.dumps(body_dict, separators=(',', ':'), sort_keys=True)
    md5  = base64.b64encode(hashlib.md5(body.encode()).digest()).decode()
    sig  = _sign(API_SECRET, "POST", md5, date, path)
    headers = {
        "Content-Type":  "application/json;charset=UTF-8",
        "Content-MD5":   md5,
        "Date":          date,
        "Authorization": f"API {API_ID}:{sig}",
    }
    url = f"{API_BASE_V2}{path}"
    print(f"  POST {url}")
    print(f"  Body: {body}")
    r = requests.post(url, headers=headers, data=body.encode(), timeout=15)
    print(f"  HTTP {r.status_code}")
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def call(label, path, body):
    print(f"\n{SEP}")
    print(f"  {label}")
    print(SEP)
    result = _post(path, body)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    if "YOUR_API_ID" in API_ID:
        print("ERROR: Fill in API_ID, API_SECRET and DEVICE_SN at the top of this script.")
        sys.exit(1)

    print(f"\nDevice SN : {DEVICE_SN}")
    print(f"API Base  : {API_BASE_V2}")

    rt = call(
        "GetRealTimeDataBySN  (v2/cygni/realtime)",
        "/v2/cygni/GetRealTimeDataBySN",
        {"sn": DEVICE_SN},
    )

    call(
        "GetStatusInfBySN  (v2/cygni/status)",
        "/v2/cygni/GetStatusInfBySN",
        {"sn": DEVICE_SN},
    )

    # If realtime call succeeded, show which integration fields will be populated
    if rt.get("code") == "00000" and rt.get("data"):
        d = rt["data"]
        MAPPED = {
            "backupLoadPower", "thirdPartyInvPower", "inverterTotalPower",
            "reactivePower", "apparentPower", "sparePower",
            "powerLimitActive", "onGridDischargeDepth", "offGridDischargeDepth",
        }
        print(f"\n{SEP}")
        print("  Integration field mapping (v2 realtime):")
        print(SEP)
        for k in MAPPED:
            val = d.get(k, "<MISSING>")
            print(f"  {k:<30} = {val}")

        print(f"\n{SEP}")
        print("  All fields returned by v2 realtime:")
        print(SEP)
        for k, v in d.items():
            print(f"  {k:<30} = {v}")
