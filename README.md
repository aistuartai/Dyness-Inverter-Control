# Dyness Inverter Control

[![Version](https://img.shields.io/badge/version-2.8.0-blue)](https://github.com/aistuartai/Dyness-Inverter-Control/releases)
[![Tests](https://github.com/aistuartai/Dyness-Inverter-Control/actions/workflows/tests.yml/badge.svg)](https://github.com/aistuartai/Dyness-Inverter-Control/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

> A fork of [shopf/dyness_battery](https://github.com/shopf/dyness_battery), extended with full inverter control for the **Dyness Cygni 10.0HS-M8** (and Cygni HS series generally).
>
> ⚠️ Tested on a single setup. Use at your own risk — verify every setting before applying changes to live hardware.
>
> Requires **Home Assistant 2025.2 or newer**.

---

## What this adds

On top of all upstream sensors, this fork adds **write control** via the Dyness v2 API — configuring the inverter directly from Home Assistant without touching the Dyness app.

All settings use a **two-step Stage → Confirm** flow to prevent accidental writes to live hardware. Staging shows a persistent notification preview of the proposed changes before anything is sent to the inverter.

### Configurable Polling Interval

Set how often HA polls the Dyness cloud API — configurable at setup and at any time via **Settings → Integrations → Dyness Inverter Control → Configure**.

| Option | Calls/hour (1 module) | Notes |
|--------|----------------------|-------|
| 2 min | ~40 | Aggressive — monitor logs for 429 errors |
| 3 min | ~27 | Recommended for 1 module |
| **5 min** | ~16 | **Default** |
| 10 min | ~8 | Conservative |
| 15 min | ~5 | Minimum recommended for 3+ modules |

With 3+ modules the interval is automatically raised (to 10 or 15 min) regardless of your setting, to stay within the Dyness API rate limit of ~60 calls/hour.

Changing the interval triggers an automatic integration reload — no HA restart needed.

### TOU (Time of Use) Schedule

Configure up to **4 charge/discharge groups** entirely within HA:

| Control | Options |
|---------|---------|
| Enabled | On / Off per group |
| Start / End Time | Time picker |
| Mode | Charge / Discharge |
| Days | Every Day, Weekdays, Weekends, Mon–Sun individually |
| Power Limit | 0 – 10,000 W (100 W steps) |
| DOD Limit | Enable switch + 0–95% slider (1% steps) |
| Max Charge SOC | Enable switch + 10–100% slider (1% steps) |

> **Note:** The Dyness API has no read-back endpoint for TOU schedules. Values shown are what HA last sent — they are restored across restarts via `RestoreEntity`. Changes made in the Dyness app are **not** seen by HA, and every TOU write sends all four groups, so confirming from HA overwrites app changes.
>
> **Tip:** a Discharge group discharges at a *fixed* power; it does not follow the house load. To keep grid import near zero in an evening window, Peak Control (load-following) is the better tool.

![TOU Schedule Dashboard](docs/dashboard_tou.png)

### Battery Limits

| Setting | Range |
|---------|-------|
| On-Grid Discharge Depth | 0–95% |
| Off-Grid Discharge Depth | 0–95% |
| On-Grid Charge Limit | 10–100% |
| Off-Grid Charge Limit | 10–100% |

The inverter reports its actual discharge depths, so the two DOD settings carry `inverter_value` and `in_sync` attributes, and the Stage preview shows *(inverter currently N%)* next to any value that differs — usually because it was changed in the Dyness app. Charge limits have no read-back.

### Peak Control *(feature-gated)*

Enable only if your inverter supports peak shaving (toggle the **Feature Supported** switch first):

| Setting | Options |
|---------|---------|
| Peak Control Enabled | On / Off |
| Trigger SOC | 0–100% |
| Time Range | Start + End time |
| Power Limit | 0–10,000 W (10 W steps) |

### Load Control *(feature-gated)*

Enable only if a load relay is connected (toggle the **Feature Supported** switch first):

| Setting | Options |
|---------|---------|
| Load Switch | On / Off |
| Force Close Window | Start + End time |
| Force Close Off-Grid Only | On / Off |
| Always Close On Grid | On / Off |
| Relay Close SOC | 0–100% |
| Relay Open SOC | 0–100% |
| PV Power Threshold | 0–10,000 W |

![Battery Settings Dashboard](docs/dashboard_battery_settings.png)

---

## Example Dashboards

Two ready-to-import dashboards, matching the screenshots above, are included in [`docs/dashboards/`](docs/dashboards/):

| Dashboard | File | Shows |
|---|---|---|
| TOU Settings | [tou-dashboard.yaml](docs/dashboards/tou-dashboard.yaml) | All 4 TOU groups (Mushroom-styled controls), stage/confirm buttons, system status gauges |
| Battery Settings | [settings-dashboard.yaml](docs/dashboards/settings-dashboard.yaml) | Battery Limits, and feature-gated Peak Control / Load Control sections that only reveal their settings once **Feature Supported** is switched on |

### Required frontend add-ons (HACS)

Both dashboards use two custom card types that are **not** built into Home Assistant — install these via HACS → Frontend first, or the cards will show as "custom element doesn't exist":

| Add-on | HACS repository | Used for |
|---|---|---|
| Mushroom | [piitaya/lovelace-mushroom](https://github.com/piitaya/lovelace-mushroom) | Entity/select/number/template cards throughout both dashboards |
| button-card | [custom-cards/button-card](https://github.com/custom-cards/button-card) | The gradient Stage/Confirm/Enable/Disable action buttons |

### Importing

1. Install the two HACS frontend add-ons above and reload your browser.
2. In HA: **Settings → Dashboards → + Add Dashboard → New dashboard from scratch**, give it a name, then open it.
3. Click the pencil (Edit Dashboard) → **⋮ → Raw configuration editor**.
4. Delete the placeholder content and paste in the contents of the `.yaml` file.
5. Save. Repeat for the second dashboard if you want both.

### Entity ID prefix

Every entity in these dashboards is prefixed with `garage_dyness_cygni_` — `garage` is simply the name I gave my own device when I set it up, and `dyness_cygni` is the integration's domain. Your entities will use whatever name you gave your device instead of `garage`. After pasting the YAML in, use the Raw configuration editor's find-and-replace (or open the file in a text editor first) to swap `garage_dyness_cygni` for your own device's prefix — check **Developer Tools → States** and filter for `dyness_cygni` to find it.

### Optional extras on the TOU dashboard

The TOU dashboard also includes a handful of buttons and sliders that call into **personal automation examples of mine** (a GloBird "ZeroHero" TOU-scheduling setup and a manual battery boost-charge routine) — these are not part of the integration and are not included in this repo. Without matching helpers, the buttons below simply do nothing when pressed (they don't error):

| Entity | Type | Used by |
|---|---|---|
| `input_number.tou_import_power` / `tou_export_power` / `tou_boost_power` / `tou_boost_duration` | Helper (Number) | Power/duration sliders on the Enable Import/Export and Boost Charge cards |
| `input_button.tou_apply_globird_zerohero_optimal` / `tou_export_enable_6_9pm` / `tou_import_disable` / `tou_export_disable` / `tou_disable_all_groups` / `battery_boost_charge_5000w` / `abort_boost_charge` | Helper (Button) | The gradient action buttons in those same sections |
| `automation.dyness_battery_boost_charge_5000w_1hr` | Automation | "Boost Charge Running" status tile |

Either create matching helpers (**Settings → Devices & Services → Helpers → + Create Helper**) and wire up your own automations behind them, or just delete those specific cards from the Raw configuration editor after importing — the core TOU group controls (Groups 1–4, Stage & Preview, Confirm & Apply) work standalone with no extra setup.

## Installation (this fork)

Requires Home Assistant **2025.2** or newer.

1. In HACS → **Integrations** → **⋮** → **Custom repositories**
2. Add: `https://github.com/aistuartai/Dyness-Inverter-Control` — Category: **Integration**
3. Install **Dyness Inverter Control**, restart HA
4. **Settings** → **Devices & Services** → **Add Integration** → search **Dyness Inverter Control**
5. Enter your **API ID** and **API Secret** from [ems.dyness.com](https://ems.dyness.com/login) → Developer Center → API Management

---

## Known quirks

| Issue | Detail |
|-------|--------|
| API boolean inversion | The PDF documents `0=On, 1=Off` for the TOU group switches; the hardware is `1=On, 0=Off`. Peak/Load Control use the same convention but have not yet been verified on hardware |
| No TOU read-back | `/v2/GetWorkModeSetting` does not exist — entities persist last-sent values via `RestoreEntity` |
| Status flag conventions differ | v2 realtime `bmsCommunicationStatus` uses 1 = Normal, while the meter flag and the status endpoint use 0 = Normal |
| Grid power sign | HA shows grid power positive when **importing**; the Dyness app shows it positive when exporting |
| Peak/Load feature gates | These features are not available on all inverter configurations — enable the **Feature Supported** switch only if your hardware supports it |

---

## Upgrading to 2.8.0

- **Requires Home Assistant 2025.2+.**
- **Peak Control / Load Control sliders now work.** Before 2.8.0, changing Peak Trigger SOC, Peak Power, Relay Close/Open SOC or PV Threshold in the UI usually had no effect on what Confirm sent. Re-check those values before your next Confirm.
- **Nine duplicate entities are removed automatically** — nameless `number.<device>[_2-4]` and `time.<device>[_2-5]` entities that older versions created on the wrong platform. Check dashboards/automations if you referenced them.
- **"Power Limit Active" is now "Export Limit"** (Enabled / Disabled — it reports whether the export limit is switched on). Existing installs keep their entity id (`sensor.<device>_power_limit_active`, as used in the example dashboard); fresh installs get `sensor.<device>_export_limit`.
- **BMS Communication Status** now reads *Normal* on a healthy battery (it was inverted), and **module count** includes every cluster.
- **`set_tou_schedule`** accepts an optional `config_entry_id` (needed only with more than one device) and requires `HH:mm` times.
- Requests now time out individually after 20 s, so a slow Dyness endpoint no longer takes every sensor unavailable.

---

## Development

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) and run on Linux/macOS (WSL on Windows). API fixtures in `tests/fixtures/` are real responses with every identifier replaced; other models use clearly labelled synthetic data.

```bash
pip install -r requirements_test.txt
pytest --cov
```

On Windows, `scripts/test.ps1 setup` creates a WSL environment and `scripts/test.ps1` runs the suite. GitHub Actions runs the tests, ruff, hassfest and HACS validation on every push.

---

## Credits

All core architecture, API handling, schema detection, and multi-device support is the work of **[shopf](https://github.com/shopf/dyness_battery)**. This fork adds Cygni-specific sensor patches and the full write-control layer on top.

---

## License

MIT — see [LICENSE](LICENSE). Original copyright © shopf.
