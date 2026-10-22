# Test fixtures

## `cygni/`

Raw API responses captured from a real Dyness Cygni 10.0HS-M8 (2 clusters × 4
modules) through the integration's debug log, one file per endpoint
(`v1_device_realTime_data.json` ↔ `/v1/device/realTime/data`).

**De-identified.** Every identifier was replaced before the files left the
capture machine, and the files were scanned for leftovers:

| Field | Value used in fixtures |
|---|---|
| `deviceSn` / `sn` | `FAKESN000000000000001` |
| `collectorSn` | `FAKECOL001` |
| `stationId` | `100000000000001` |
| `stationName` | `Test Station` |
| `position` | `1 Example Street, Testville VIC 3000, Australia` |

Never commit raw captures. New captures must go through the same replacement
and a leak scan against the real identifiers (kept outside this repo).

**Known limits**

- `v1_device_realTime_data.json` and `v1_device_getLastPowerDataBySn.json` were
  cut at ~16 KB by the HA journal. The list was closed after the last complete
  item and a `_fixture_note` field records it (188 of 321 realtime points).
- Files come from slightly different polls (v1 realtime 2026-10-21 20:14,
  v2 2026-10-22 07:04), so values are not mutually consistent — don't assert
  cross-endpoint arithmetic on them.
