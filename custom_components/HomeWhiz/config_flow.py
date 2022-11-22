from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_flow
from homeassistant import config_entries

from homeassistant.components.bluetooth import (
    BluetoothServiceInfo,
    async_discovered_service_info,
)

from homeassistant.data_entry_flow import FlowResult
from homeassistant.components import onboarding

from .const import DOMAIN
from .homewhiz import HomeWhizDevice, ScannerHelper


async def _async_has_devices(hass: HomeAssistant) -> bool:
    scanner = ScannerHelper()
    devices = await scanner.scan(hass)
    return len(devices) > 0


config_entry_flow.register_discovery_flow(DOMAIN, "HomeWhiz", _async_has_devices)


@dataclasses.dataclass
class Discovery:
    """A discovered bluetooth device."""

    title: str
    discovery_info: BluetoothServiceInfo
    device: HomeWhizDevice


def _title(discovery_info: BluetoothServiceInfo, device: HomeWhizDevice) -> str:
    return device.title or device.get_device_name() or discovery_info.name



class HomeWhizConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    
    VERSION = 1    
    
    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfo | None = None
        self._discovered_device: HomeWhizDevice | None = None
        self._discovered_devices: dict[str, Discovery] = {}
    
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfo
    ) -> FlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        device = HomeWhizDevice()
        
        title = _title(discovery_info, device)
        self.context["title_placeholders"] = {"name": title}
        self._discovery_info = discovery_info
        self._discovered_device = device
        
        return await self.async_step_bluetooth_confirm()
    
    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm discovery."""
        if user_input is not None or not onboarding.async_is_onboarded(self.hass):
            return self._async_get_or_create_entry()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context["title_placeholders"],
        )
        
    def _async_get_or_create_entry(self):
        data = {}

        if entry_id := self.context.get("entry_id"):
            entry = self.hass.config_entries.async_get_entry(entry_id)
            assert entry is not None

            self.hass.config_entries.async_update_entry(entry, data=data)

            # Reload the config entry to notify of updated config
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(entry.entry_id)
            )

            return self.async_abort(reason="reauth_successful")

        return self.async_create_entry(
            title=self.context["title_placeholders"]["name"],
            data=data,
        )