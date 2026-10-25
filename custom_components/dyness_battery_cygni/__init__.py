"""Dyness Inverter Control integration for Home Assistant."""
import asyncio
import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import payloads
from .api import DynessApiClient
from .const import (  # noqa: F401 — re-exported for the platform modules
    _BMS_SUFFIXES,
    CONF_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MISPLATFORMED_ENTITY_KEYS,
    PLATFORMS,
    SCHEMA_CYGNI,
    SCHEMA_DL5,
    SCHEMA_JUNIOR,
    SCHEMA_POWERBOX_G2,
    SCHEMA_POWERBOX_PRO,
    SCHEMA_POWERDEPOT,
    SCHEMA_STACK100,
    SCHEMA_TOWER,
    SCHEMA_UNKNOWN,
    STALE_ENTITY_KEYS,
    _detect_schema,
    _is_success,
    _scan_interval_for_modules,
    _to_float,
)
from .parsers import ParseInput, build_data, flatten_v2, parse_module_points, realtime_points

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions once, independent of config entries."""

    async def _handle_set_tou_schedule(call: ServiceCall) -> None:
        coordinators = {
            e.entry_id: e.runtime_data
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        }
        entry_id = call.data.get("config_entry_id")
        if entry_id:
            coordinator = coordinators.get(entry_id)
            if coordinator is None:
                raise ServiceValidationError(f"No loaded Dyness device for config entry {entry_id}")
        elif len(coordinators) == 1:
            coordinator = next(iter(coordinators.values()))
        elif not coordinators:
            raise ServiceValidationError("No Dyness device is loaded")
        else:
            raise ServiceValidationError(
                "Several Dyness devices are set up — pass config_entry_id to choose one"
            )
        await coordinator.async_set_tou_schedule(call.data.get("groups", []))

    hass.services.async_register(DOMAIN, "set_tou_schedule", _handle_set_tou_schedule)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # ── Veraltete Entitäten aus der Entity-Registry entfernen ────────────────
    await _async_cleanup_stale_entities(hass, entry)

    # Read scan interval from options (set via Configure), fallback to data (set at setup)
    scan_interval_minutes = int(
        entry.options.get(CONF_SCAN_INTERVAL_MINUTES)
        or entry.data.get(CONF_SCAN_INTERVAL_MINUTES)
        or 5
    )

    coordinator = DynessDataCoordinator(
        hass,
        entry,
        entry.data["api_id"],
        entry.data["api_secret"],
        entry.data["api_base"],
        device_sn=entry.data.get("device_sn"),
        dongle_sn=entry.data.get("dongle_sn"),
        scan_interval_minutes=scan_interval_minutes,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Löscht veraltete Entitäten aus der Entity-Registry.

    Prüft alle registrierten Entitäten dieser Integration und entfernt jene,
    deren unique_id auf einen veralteten Sensor-Key hinweist (STALE_ENTITY_KEYS).
    Funktioniert sowohl für Pack-Level als auch für Modul-Sensoren.
    """
    from homeassistant.helpers import entity_registry as er

    entity_registry = er.async_get(hass)
    stale_entities = [
        entity
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if any(
            entity.unique_id == f"{entry.entry_id}_{key}"          # Pack-Level
            or entity.unique_id.endswith(f"_{key}")                 # Modul-Level (entry_id_mid_key)
            for key in STALE_ENTITY_KEYS
        )
        or entity.unique_id.removeprefix(f"{entry.entry_id}_")
        in MISPLATFORMED_ENTITY_KEYS.get(entity.domain, set())
    ]
    if stale_entities:
        _LOGGER.info(
            "Dyness: Bereinige %d veraltete Entität(en): %s",
            len(stale_entities),
            [e.unique_id for e in stale_entities],
        )
        for entity in stale_entities:
            entity_registry.async_remove(entity.entity_id)
    else:
        _LOGGER.debug("Dyness: Keine veralteten Entitäten gefunden.")


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

class DynessDataCoordinator(DataUpdateCoordinator):

    def __init__(self, hass, config_entry, api_id, api_secret, api_base,
                 device_sn=None, dongle_sn=None, scan_interval_minutes=5):
        self._user_scan_interval = scan_interval_minutes
        super().__init__(hass, _LOGGER, config_entry=config_entry, name=DOMAIN,
                         update_interval=timedelta(minutes=scan_interval_minutes))
        self.api_id     = api_id
        self.api_secret = api_secret
        self.api_base   = api_base
        self.device_sn  = device_sn
        self.dongle_sn  = dongle_sn
        self._client = DynessApiClient(async_get_clientsession(hass), api_id, api_secret, api_base)

        self.station_info  = {}
        self.device_info   = {}
        self.storage_info  = {}
        self.realtime_data = {}
        self.module_data: dict[str, dict] = {}  # mid → Sensordaten
        self.running_data: dict = {}             # getLastRunningDataBySn
        self.v2_realtime_data: dict = {}         # GetRealTimeDataBySN (v2)
        self.v2_status_data: dict = {}           # GetStatusInfBySN (v2)
        self._v2_unavailable: bool = False       # True after first 404 — skip until reset
        self._last_alarm_texts: list = []        # track alarm state to avoid repeated notifications

        self._bound: bool = False
        self._bound_sns: set = set()  # Bereits gebundene Sub-Modul SNs
        self._module_sns: list[str] = []
        self._storage_list_cycle: int = 0  # Zähler für storage/list Throttling
        self._v2_cycle: int = 0            # Zähler für v2-API Throttling

    # ── Control methods ───────────────────────────────────────────────────────

    async def _write(self, path: str, body: dict, label: str) -> None:
        result = await self._client.call(path, body)
        if not _is_success(result):
            info = result.get("info", result)
            hint = (
                " — Dyness rejected one of the values (e.g. TOU power above 2000 W)."
                if info == "Request parameter exception" else ""
            )
            raise HomeAssistantError(f"Dyness {label} failed: {info}{hint}")
        _LOGGER.info("Dyness %s: success (device %s)", label, self.device_sn)
        await self.async_request_refresh()

    async def async_set_work_mode(self, mode: str) -> None:
        """Set inverter work mode. mode: '0'=Self-use, '2'=Backup, '3'=TOU."""
        body = payloads.work_mode(self.device_sn, mode)
        old_mode = (self.data or {}).get("runModel", "unknown")
        _LOGGER.info(
            "Dyness SetWorkMode: %s → mode %s (device %s)", old_mode, mode, self.device_sn
        )
        await self._write("/v2/SetWorkModeSetting", body, "SetWorkModeSetting")

    async def async_set_tou_schedule(self, groups: list) -> None:
        """Send full TOU schedule. Always switches to TOU mode (workMode=3)."""
        body = payloads.tou_schedule(self.device_sn, groups)
        _LOGGER.info(
            "Dyness SetTOUSchedule: sending %d group(s) to device %s",
            len(body["workGroups"]), self.device_sn,
        )
        await self._write("/v2/SetWorkModeSetting", body, "SetTOUSchedule")

    async def async_set_battery_setting(self, data: dict) -> None:
        """Set battery discharge depth and charge limits."""
        _LOGGER.info("Dyness SetBatterySetting: %s (device %s)", data, self.device_sn)
        await self._write("/v2/SetBatterySetting", payloads.battery_setting(self.device_sn, data), "SetBatterySetting")

    async def async_set_peak_control(self, data: dict) -> None:
        """Set peak control / peak shaving settings."""
        _LOGGER.info("Dyness SetPeakControlSetting: %s (device %s)", data, self.device_sn)
        await self._write(
            "/v2/SetPeakControlSetting", payloads.peak_control(self.device_sn, data), "SetPeakControlSetting"
        )

    async def async_set_load_control(self, data: dict) -> None:
        """Set load relay control settings."""
        _LOGGER.info("Dyness SetLoadControlSetting: %s (device %s)", data, self.device_sn)
        await self._write(
            "/v2/SetLoadControlSetting", payloads.load_control(self.device_sn, data), "SetLoadControlSetting"
        )

    def _update_scan_interval(self):
        """Passt das Scan-Intervall dynamisch an die Modulanzahl an."""
        n = len(self._module_sns)
        new_interval = _scan_interval_for_modules(n, self._user_scan_interval)
        if self.update_interval != new_interval:
            self.update_interval = new_interval
            _LOGGER.info(
                "Dyness: %d Modul(e) erkannt → Scan-Intervall auf %d Min gesetzt",
                n, int(new_interval.total_seconds() / 60)
            )

    async def _async_update_data(self):
        try:
            async with asyncio.timeout(90):

                # ── Auto-Discovery BMS SN (einmalig) ─────────────────────
                if not self.device_sn:
                    try:
                        sl_result = await self._client.call("/v1/device/storage/list", {})
                        if _is_success(sl_result):
                            device_list = (sl_result.get("data", {}) or {}).get("list", [])
                            bms = (
                                next((d for d in device_list
                                      if str(d.get("deviceSn", "")).endswith(_BMS_SUFFIXES)), None)
                                or (device_list[0] if device_list else None)
                            )
                            if bms:
                                self.device_sn = bms.get("deviceSn", "")
                                _LOGGER.info("Dyness: BMS SN ermittelt: %s", self.device_sn)
                            else:
                                raise UpdateFailed(
                                    "Dyness: Keine Geräte auf diesem API-Account. "
                                    "Bitte API-Zugangsdaten prüfen."
                                )
                    except UpdateFailed:
                        raise
                    except Exception as e:
                        raise UpdateFailed(f"Dyness: BMS-Erkennung fehlgeschlagen: {e}") from e

                # ── Gerät binden (einmalig) ───────────────────────────────
                if not self._bound:
                    try:
                        bind_body = {"deviceSn": self.device_sn}
                        if self.dongle_sn:
                            bind_body["collectorSn"] = self.dongle_sn
                        bind_result = await self._client.call("/v1/device/bindSn", bind_body)
                        bind_code = str(bind_result.get("code", ""))
                        if bind_code in ("0", "200", "500") or bind_result.get("code") in (0, 500):
                            self._bound = True
                            if bind_code == "500" or bind_result.get("code") == 500:
                                _LOGGER.debug("Dyness bindSn: bereits gebunden – OK")
                            else:
                                _LOGGER.debug("Dyness bindSn erfolgreich")
                        else:
                            _LOGGER.warning(
                                "Dyness bindSn: Code %s – Integration läuft trotzdem weiter.",
                                bind_code
                            )
                            self._bound = True
                    except UpdateFailed:
                        raise
                    except Exception as e:
                        _LOGGER.warning("Dyness bindSn nicht erreichbar: %s", e)
                        self._bound = True

                # ── Statische Daten (einmalig) ────────────────────────────
                if not self.station_info:
                    try:
                        result = await self._client.call(
                            "/v1/station/info", {"deviceSn": self.device_sn}
                        )
                        if _is_success(result):
                            self.station_info = result.get("data", {}) or {}
                    except Exception as e:
                        _LOGGER.warning("Dyness station/info nicht erreichbar: %s", e)

                # ── device_info + storage/list (alle 3 Zyklen) ───────────
                # Beide Endpunkte werden im gleichen Zyklus aktualisiert.
                # deviceCommunicationStatus kommt je nach Gerät aus storage/list
                # ODER aus household/storage/detail — beide müssen aktuell sein.
                # Fix: device_info nicht mehr nur einmalig laden sondern ebenfalls
                # alle 3 Zyklen — verhindert veralteten Communication Status.
                self._storage_list_cycle = (self._storage_list_cycle + 1) % 3
                if self._storage_list_cycle == 0 or not self.device_info or not self.storage_info:
                    try:
                        body = {"deviceSn": self.device_sn}
                        if self.dongle_sn:
                            body["collectorSn"] = self.dongle_sn
                        result = await self._client.call(
                            "/v1/device/household/storage/detail", body
                        )
                        if _is_success(result):
                            self.device_info = result.get("data", {}) or {}
                    except Exception as e:
                        _LOGGER.warning("Dyness household/storage/detail nicht erreichbar: %s", e)

                    try:
                        result = await self._client.call("/v1/device/storage/list", {})
                        if _is_success(result):
                            device_list = (result.get("data", {}) or {}).get("list", [])
                            match = next(
                                (d for d in device_list if d.get("deviceSn") == self.device_sn),
                                device_list[0] if device_list else {}
                            )
                            self.storage_info = match
                    except Exception as e:
                        _LOGGER.warning("Dyness storage/list nicht erreichbar: %s", e)

                # ── realTime/data BMS (bei jedem Update) ──────────────────
                try:
                    body = {"deviceSn": self.device_sn}
                    if self.dongle_sn:
                        body["collectorSn"] = self.dongle_sn
                    rt_result = await self._client.call("/v1/device/realTime/data", body)
                    if _is_success(rt_result):
                        raw = rt_result.get("data", []) or []
                        self.realtime_data = realtime_points(raw)
                        _LOGGER.debug("Dyness realTime/data: %d Punkte", len(self.realtime_data))

                        # ── Sub-Modul Discovery via SUB Point ─────────────
                        # Sub-Modul Discovery — bei jedem Update prüfen ob neue Module dazugekommen sind
                        sub_raw = self.realtime_data.get("SUB", "")
                        if sub_raw:
                            candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
                            candidates = [
                                s for s in candidates
                                if not s.endswith(_BMS_SUFFIXES)
                            ]
                            if len(candidates) > 1:
                                if set(candidates) != set(self._module_sns):
                                    _LOGGER.info(
                                        "Dyness: Sub-Module aktualisiert: %s → %s",
                                        self._module_sns, candidates
                                    )
                                    self._module_sns = candidates
                                    self._update_scan_interval()
                            elif not self._module_sns:
                                _LOGGER.debug(
                                    "Dyness: Einzelnes Sub-Modul — kein separater Abruf (%s)",
                                    candidates
                                )
                    else:
                        _LOGGER.debug(
                            "Dyness realTime/data: Code %s – %s",
                            rt_result.get("code"), rt_result.get("info")
                        )
                except Exception as e:
                    _LOGGER.warning("Dyness realTime/data nicht erreichbar: %s", e)

                # ── Per-Modul realTime/data ───────────────────────────────
                new_module_data: dict[str, dict] = {}
                for sn in self._module_sns:
                    try:
                        # Sub-Modul binden falls noch nicht gebunden
                        if sn not in self._bound_sns:
                            bind_res = await self._client.call(
                                "/v1/device/bindSn", {"deviceSn": sn}
                            )
                            bind_code = str(bind_res.get("code", ""))
                            if bind_code in ("0", "200", "500") or bind_res.get("code") in (0, 500):
                                self._bound_sns.add(sn)
                                _LOGGER.info("Dyness Sub-Modul gebunden: %s", sn)
                            else:
                                _LOGGER.warning(
                                    "Dyness Sub-Modul Binding fehlgeschlagen: %s Code %s",
                                    sn, bind_code
                                )
                                continue  # Abruf überspringen wenn Binding fehlschlägt
                        m_result = await self._client.call(
                            "/v1/device/realTime/data", {"deviceSn": sn}
                        )
                        if _is_success(m_result):
                            m_raw = m_result.get("data", []) or []
                            m_pts = realtime_points(m_raw)
                            mid = sn  # volle SN als Key — konsistent mit Entity unique_id und known_module_ids
                            new_module_data[mid] = parse_module_points(sn, mid, m_pts)
                            _LOGGER.debug("Dyness Modul %s: %d Punkte", mid, len(m_pts))
                        else:
                            _LOGGER.warning("Dyness Modul %s: Code %s", sn, m_result.get("code"))
                    except Exception as e:
                        _LOGGER.warning("Dyness Modul %s nicht erreichbar: %s", sn, e)
                if new_module_data:
                    self.module_data = new_module_data

                # ── getLastRunningDataBySn (bei jedem Update) ─────────────
                try:
                    run_body = {"deviceSn": self.device_sn}
                    if self.dongle_sn:
                        run_body["collectorSn"] = self.dongle_sn
                    run_result = await self._client.call(
                        "/v1/device/getLastRunningDataBySn", run_body
                    )
                    if _is_success(run_result):
                        self.running_data = run_result.get("data", {}) or {}
                        all_null = all(
                            v is None or v == ""
                            for v in self.running_data.values()
                        )
                        if all_null:
                            _LOGGER.debug(
                                "Dyness getLastRunningDataBySn: Alle %d Felder null "
                                "— kein Wechselrichter verbunden (normal bei reinen "
                                "Batteriespeichern wie Junior Box oder Powerbox G2)",
                                len(self.running_data)
                            )
                        else:
                            _LOGGER.debug(
                                "Dyness getLastRunningDataBySn: %d Felder, "
                                "firmwareVersion=%s",
                                len(self.running_data),
                                self.running_data.get("firmwareVersion")
                            )
                    else:
                        _LOGGER.debug(
                            "Dyness getLastRunningDataBySn: Code %s – %s",
                            run_result.get("code"), run_result.get("info")
                        )
                except Exception as e:
                    _LOGGER.warning("Dyness getLastRunningDataBySn nicht erreichbar: %s", e)

                # ── v2 API Daten (alle 3 Zyklen, skip if unavailable) ────
                self._v2_cycle = (self._v2_cycle + 1) % 3
                if not self._v2_unavailable and (self._v2_cycle == 0 or not self.v2_realtime_data):
                    v2_body = {"deviceSn": self.device_sn}
                    try:
                        v2_result = await self._client.call(
                            "/v2/GetRealTimeDataBySN", v2_body
                        )
                        if _is_success(v2_result):
                            # Response is nested (gridInfo, batteryInfo, ...): flatten into one dict
                            self.v2_realtime_data = flatten_v2(v2_result.get("data", {}) or {})
                        elif v2_result.get("status") == 404 or v2_result.get("code") == "404":
                            _LOGGER.info("Dyness v2 API not available on this server/account — skipping v2 sensors")
                            self._v2_unavailable = True
                    except Exception as e:
                        _LOGGER.debug("Dyness v2 GetRealTimeDataBySN: %s", e)

                    if not self._v2_unavailable:
                        try:
                            v2_result = await self._client.call(
                                "/v2/GetStatusInfBySN", v2_body
                            )
                            if _is_success(v2_result):
                                self.v2_status_data = v2_result.get("data", {}) or {}
                        except Exception as e:
                            _LOGGER.debug("Dyness v2 GetStatusInfBySN: %s", e)

                # ── Leistungsdaten (bei jedem Update) ────────────────────
                # UpdateFailed nur noch bei Totalausfall.
                # Bei Teilerfolg (z.B. realTime/data OK, getLastPowerDataBySn fehlerhaft)
                # letzten gültigen Stand behalten statt alle Sensoren unavailable zu machen.
                body = {"pageNo": 1, "pageSize": 1, "deviceSn": self.device_sn}
                if self.dongle_sn:
                    body["collectorSn"] = self.dongle_sn
                result = await self._client.call(
                    "/v1/device/getLastPowerDataBySn", body
                )
                code = str(result.get("code", ""))
                _power_data_ok = code in ("0", "200") or result.get("code") == 0
                if not _power_data_ok:
                    _LOGGER.warning(
                        "Dyness getLastPowerDataBySn fehlgeschlagen – Code %s: %s (deviceSn=%s) "
                        "— behalte letzten Stand",
                        code, result.get("info"), self.device_sn,
                    )
                    # Totalausfall: auch realTime/data leer → jetzt UpdateFailed
                    if not self.realtime_data and not self.running_data:
                        raise UpdateFailed(
                            f"Dyness API Fehler (Code {code}): {result.get('info', 'Unbekannt')} "
                            f"– deviceSn={self.device_sn}"
                        )
                    # Teilerfolg: anderen Daten wurden bereits aktualisiert → weiter
                    data = {}
                else:
                    data = result.get("data", {})

                data, alarm_texts = build_data(ParseInput(
                    device_sn=self.device_sn,
                    station_info=self.station_info,
                    device_info=self.device_info,
                    storage_info=self.storage_info,
                    realtime_data=self.realtime_data,
                    running_data=self.running_data,
                    v2_realtime_data=self.v2_realtime_data,
                    v2_status_data=self.v2_status_data,
                    module_sns=self._module_sns,
                    module_data=self.module_data,
                ), data)
                self._notify_alarms(alarm_texts)
                return data

        except UpdateFailed:
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.warning("Dyness API Timeout – wird beim nächsten Update erneut versucht")
            raise UpdateFailed("Dyness API Timeout") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Dyness Verbindungsfehler: %s", err)
            raise UpdateFailed(f"Verbindungsfehler zur Dyness API: {err}") from err
        except Exception as err:
            _LOGGER.error("Dyness unerwarteter Fehler: %s", err, exc_info=True)
            raise UpdateFailed(f"Unerwarteter Fehler: {err}") from err


    def _notify_alarms(self, alarm_texts: list[str]) -> None:
        """Raise a persistent notification when the set of active alarms changes; clear it when none remain."""
        if alarm_texts:
            if alarm_texts != self._last_alarm_texts:
                self._last_alarm_texts = alarm_texts
                self.hass.async_create_task(
                    self.hass.services.async_call(
                        "persistent_notification", "create", {
                            "title": "⚠️ Dyness Battery Alarm",
                            "message": (
                                f"Active alarms detected on {self.device_sn}:\n"
                                + "\n".join(f"• {t}" for t in alarm_texts)
                                + "\n\nPlease contact Dyness support if the issue persists."
                            ),
                            "notification_id": f"dyness_alarm_{self.device_sn}",
                        }
                    )
                )
        else:
            if self._last_alarm_texts:
                self._last_alarm_texts = []
                self.hass.async_create_task(
                    self.hass.services.async_call(
                        "persistent_notification", "dismiss", {
                            "notification_id": f"dyness_alarm_{self.device_sn}",
                        }
                    )
                )
