"""Config Flow für Dyness Battery Integration."""
import asyncio

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DynessApiClient
from .const import DOMAIN, CONF_SCAN_INTERVAL_MINUTES, _BMS_SUFFIXES, _is_success

# API Domains je Region
API_REGIONS = {
    "europe": "https://open-api.dyness.com",
    "apac":   "https://apacopen-api.dyness.com",
}


async def _fetch_device_list(hass, api_id: str, api_secret: str, api_base: str) -> list:
    """Gibt alle Geräte auf dem Account zurück."""
    client = DynessApiClient(async_get_clientsession(hass), api_id, api_secret, api_base)
    try:
        async with asyncio.timeout(10):
            result = await client.call("/v1/device/storage/list", {})
        if _is_success(result):
            return (result.get("data", {}) or {}).get("list", [])
    except Exception:
        pass
    return []


def _select_bms_devices(device_list: list) -> list:
    """Filtert BMS/BDU Geräte — ein Eintrag pro physischem Gerät."""
    bms_devices = [
        d for d in device_list
        if str(d.get("deviceSn", "")).endswith(_BMS_SUFFIXES)
    ]
    # Fallback: alle Geräte wenn kein BMS-Suffix gefunden
    return bms_devices if bms_devices else device_list


SCAN_INTERVAL_OPTIONS = ["2", "3", "5", "10", "15"]

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("api_id"): str,
    vol.Required("api_secret"): str,
    vol.Required("region", default="europe"): vol.In(["europe", "apac"]),
    vol.Required(CONF_SCAN_INTERVAL_MINUTES, default="5"): vol.In(SCAN_INTERVAL_OPTIONS),
})


class DynessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow für Dyness Battery."""

    VERSION = 1

    def __init__(self):
        self._api_id = None
        self._api_secret = None
        self._api_base = None
        self._devices = []      # alle gefundenen Geräte
        self._region = "europe"
        self._scan_interval_minutes = "5"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DynessOptionsFlowHandler()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Schritt 1: API-Zugangsdaten + Region."""
        errors = {}

        if user_input is not None:
            self._api_id     = user_input["api_id"]
            self._api_secret = user_input["api_secret"]
            self._region     = user_input["region"]
            self._api_base   = API_REGIONS[self._region]
            self._scan_interval_minutes = user_input.get(CONF_SCAN_INTERVAL_MINUTES, 5)

            device_list = await _fetch_device_list(
                self.hass, self._api_id, self._api_secret, self._api_base
            )
            self._devices = _select_bms_devices(device_list)

            if not self._devices:
                errors["base"] = "discovery_failed"
            elif len(self._devices) == 1:
                # Nur ein Gerät → direkt anlegen
                return await self._create_entry_for_device(self._devices[0])
            else:
                # Mehrere Geräte → Auswahl anbieten
                return await self.async_step_select_device()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_select_device(self, user_input=None) -> ConfigFlowResult:
        """Schritt 2: Gerät auswählen (bei mehreren Geräten auf dem Account)."""
        errors = {}

        if user_input is not None:
            sn = user_input["device_sn"]
            device = next(
                (d for d in self._devices if d.get("deviceSn") == sn), None
            )
            if device:
                return await self._create_entry_for_device(device)
            errors["base"] = "device_not_found"

        # Auswahl-Liste aufbauen
        device_options = {
            d["deviceSn"]: (
                f"{d.get('deviceModelName', 'Dyness')} — "
                f"{d.get('stationName', d['deviceSn'])} "
                f"({d.get('workStatus', '?')})"
            )
            for d in self._devices
            if d.get("deviceSn")
        }

        schema = vol.Schema({
            vol.Required("device_sn"): vol.In(device_options),
        })

        return self.async_show_form(
            step_id="select_device",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "count": str(len(self._devices)),
            },
        )

    async def _create_entry_for_device(self, device: dict) -> ConfigFlowResult:
        """Legt einen Config Entry für ein Gerät an."""
        sn = device.get("deviceSn", "")
        station_name = device.get("stationName") or "Dyness Battery"

        # unique_id = api_id + deviceSN → jedes Gerät separat konfigurierbar
        await self.async_set_unique_id(f"{self._api_id}_{sn}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=station_name,
            data={
                "api_id":     self._api_id,
                "api_secret": self._api_secret,
                "api_base":   self._api_base,
                "device_sn":  sn,
                "dongle_sn":  device.get("collectorSn") or None,
                CONF_SCAN_INTERVAL_MINUTES: self._scan_interval_minutes,
            },
        )

    async def async_step_manual(self, user_input=None) -> ConfigFlowResult:
        """Fallback: Manuelle SN-Eingabe."""
        errors = {}

        if user_input is not None:
            sn     = user_input["device_sn"]
            region = user_input.get("region", "europe")
            await self.async_set_unique_id(f"{user_input['api_id']}_{sn}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Dyness Battery",
                data={
                    "api_id":     user_input["api_id"],
                    "api_secret": user_input["api_secret"],
                    "api_base":   API_REGIONS.get(region, API_REGIONS["europe"]),
                    "device_sn":  sn,
                    "dongle_sn":  user_input.get("dongle_sn") or None,
                },
            )

        schema = vol.Schema({
            vol.Required("api_id",     default=self._api_id     or ""): str,
            vol.Required("api_secret", default=self._api_secret or ""): str,
            vol.Required("region",     default="europe"): vol.In(["europe", "apac"]),
            vol.Required("device_sn"): str,
            vol.Optional("dongle_sn", default=""): str,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
        )


class DynessOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow — allows changing polling interval after initial setup."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = str(int(
            self.config_entry.options.get(CONF_SCAN_INTERVAL_MINUTES)
            or self.config_entry.data.get(CONF_SCAN_INTERVAL_MINUTES)
            or 5
        ))

        schema = vol.Schema({
            vol.Required(CONF_SCAN_INTERVAL_MINUTES, default=current): vol.In(SCAN_INTERVAL_OPTIONS),
        })

        return self.async_show_form(step_id="init", data_schema=schema)
