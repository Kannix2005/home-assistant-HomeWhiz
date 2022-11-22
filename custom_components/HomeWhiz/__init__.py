import asyncio
import logging

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.components import bluetooth
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.loader import async_get_custom_components
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SENSOR_TYPE,
    Platform,
)

from .const import DOMAIN, COORDINATORS, MAC
from .homewhiz import HomeWhizDevice, ScannerHelper, MessageAccumulator, parse_message, WasherState

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    assert entry.unique_id is not None
    hass.data.setdefault(DOMAIN, {})

    if CONF_ADDRESS not in entry.data and CONF_MAC in entry.data:
        mac = entry.data[CONF_MAC]
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ADDRESS: mac},
        )
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), False
    )
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find HomeWhiz entity with address {address}"
        )
        
    device = HomeWhizDevice()
    device._ble_device = ble_device
    device._address = address
        
    coordinator = hass.data[DOMAIN][entry.entry_id] = HomewhizDataUpdateCoordinator(
        hass,
        _LOGGER,
        ble_device,
        device,
        entry.unique_id,
        entry.data.get(CONF_NAME, entry.title),
    )
        
    entry.async_on_unload(coordinator.async_start())
    
    if not await coordinator.async_wait_ready():
        raise ConfigEntryNotReady(f"HomeWhiz with {address} not ready")
    
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.SENSOR]
    )

    return True

        
    _LOGGER.info("Start scanning")
    scanner = ScannerHelper()
    devices = await scanner.scan(hass)
    _LOGGER.info("Found {} device(s)".format(len(devices)))
    hass.data[DOMAIN].setdefault(COORDINATORS, [])
    for device in devices:
        coordinator = HomewhizDataUpdateCoordinator(hass, device)
        hass.data[DOMAIN][COORDINATORS].append(coordinator)
        hass.create_task(coordinator.connect())

    await async_get_custom_components(hass)
    hass.config_entries.async_setup_platforms(entry, [Platform.SENSOR])
    return True

async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [Platform.SENSOR]
    )

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.config_entries.async_entries(DOMAIN):
            hass.data.pop(DOMAIN)

    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


class HomewhizDataUpdateCoordinator(DataUpdateCoordinator[WasherState]):
    def __init__(
            self,
            hass: HomeAssistant,
            logger: logging.Logger,
            ble_device: BLEDevice,
            device: homewhiz.HomeWhizDevice,
            base_unique_id: str,
            device_name: str,
    ) -> None:
        super().__init__(hass, logger, ble_device.address, bluetooth.BluetoothScanningMode.PASSIVE, False)
        self.device = device
        self.ble_device = ble_device
        self.data: dict[str, Any] = {}
        self.device_name = device_name
        self.base_unique_id = base_unique_id
        
        
        self.accumulator = MessageAccumulator()

    async def connect(self):
        await self.connect_internal()
        self.client.set_disconnected_callback(lambda client: self.hass.create_task(self.reconnect(client)))
        await self.start_listening()
        return True

    async def start_listening(self):
        await self.client.start_notify("0000ac02-0000-1000-8000-00805f9b34fb",
                                       lambda sender, message: self.hass.create_task(
                                           self.handle_notify(sender, message)))
        await self.client.write_gatt_char("0000ac01-0000-1000-8000-00805f9b34fb",
                                          bytearray.fromhex("02 04 00 04 00 1a 01 03"),
                                          response=False)

    @callback
    async def handle_notify(self, sender: int, message: bytearray):
        _LOGGER.debug(f"message {message}")
        if len(message) < 10:
            return
        full_message = self.accumulator.accumulate_message(message)
        if full_message is not None:
            data = parse_message(full_message)
            _LOGGER.debug(f"data {data}")
            self.async_set_updated_data(data)

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        if adv := switchbot.parse_advertisement_data(
            service_info.device, service_info.advertisement
        ):
            self.data = parse_message(adv.data)
            if "modelName" in self.data:
                self._ready_event.set()
            _LOGGER.debug("%s: Switchbot data: %s", self.ble_device.address, self.data)
            self.device.update_from_advertisement(adv)
        super()._async_handle_bluetooth_event(service_info, change)



    @callback
    async def reconnect(self, client: BleakClient):
        _LOGGER.debug("Disconnected, reconnecting")
        await self.connect_internal()
        await self.client.stop_notify("0000ac02-0000-1000-8000-00805f9b34fb")
        await self.start_listening()

    async def connect_internal(self):
        while not self.client.is_connected:
            _LOGGER.debug("Trying to connect")
            try:
                await self.client.connect()
                _LOGGER.debug("connected")
            except Exception as e:
                _LOGGER.warning(e)
                await asyncio.sleep(1)


