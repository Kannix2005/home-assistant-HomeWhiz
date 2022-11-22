from dataclasses import dataclass
from enum import Enum
from typing import Optional

from homeassistant.components import bluetooth
from bluetooth_sensor_state_data import BluetoothData
from home_assistant_bluetooth import BluetoothServiceInfo
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SENSOR_TYPE,
    Platform,
)

class HomeWhizDevice():
    def __init__(self) -> None:
        self._washer_state = WasherState()
        self._washer_state.device_state = DeviceState(-1)
        self._washer_state.device_sub_state = DeviceSubState(-1)
        self._washer_state.delay_minutes = 0
        self._washer_state.duration_minutes = 0
        self._washer_state.remaining_minutes = 0
        self._washer_state.rinse_hold = 0
        self._washer_state.spin = 0
        self._washer_state.temperature = 0
        
        self._address = ""
        self._ble_device: BLEDevice | None = None
        
        self.last_service_info: BluetoothServiceInfo | None = None


class DeviceState(Enum):
    ON = 10
    OFF = 20
    RUNNING = 30
    PAUSED = 40
    TIME_DELAY_ACTIVE = 60
    UNKNOWN = -1

    @classmethod
    def _missing_(cls, value: object):
        return cls.UNKNOWN


class DeviceSubState(Enum):
    WASHING = 1
    SPIN = 2
    WATER_INTAKE = 3
    PREWASH = 4
    RINSING = 5
    SOFTENER = 6
    PROGRAM_STARTED = 7
    TIME_DELAY_ENABLED = 8
    PAUSED = 9
    ANALYSING = 10
    DOOR_LOCKED = 11
    OPENING_DOOR = 12
    LOCKING_DOOR = 13
    REMOVE_LAUNDRY = 15
    RINSE_HOLD = 17
    ADD_LAUNDRY = 19
    REMOTE_ANTICREASE = 20
    UNKNOWN = -1

    @classmethod
    def _missing_(cls, value: object):
        return cls.UNKNOWN


@dataclass
class WasherState:
    device_state: DeviceState
    device_sub_state: DeviceSubState
    temperature: int
    spin: int
    rinse_hold: bool
    duration_minutes: int
    remaining_minutes: int
    delay_minutes: Optional[int]

class ScannerHelper:
    async def scan(self, hass):
        devices = await bluetooth.async_get_scanner(hass).discover()
        return [d for d in devices if d.name.startswith("HwZ")]


class MessageAccumulator:
    expected_index = 0
    accumulated = []

    def accumulate_message(self, message: bytearray):
        message_index = message[4]
        if message_index == 0:
            self.accumulated = message[7:]
            self.expected_index = 1
        elif self.expected_index == 1:
            full_message = self.accumulated + message[7:]
            self.expected_index = 0
            return full_message


def clamp(value: int):
    return value if value < 128 else value - 128


def parse_message(message: bytearray):
    return WasherState(
        device_state=DeviceState(message[35]),
        device_sub_state=DeviceSubState(message[50]),
        temperature=clamp(message[37]),
        spin=clamp(message[38]) * 100,
        rinse_hold=clamp(message[38]) == 17,
        duration_minutes=message[44] * 60 + message[45],
        remaining_minutes=message[46] * 60 + message[47],
        delay_minutes=None if message[48] == 128 else message[48] * 60 + message[49]
    )


SERVICE_DATA_ORDER = (
    "0000fd3d-0000-1000-8000-00805f9b34fb",
    "00000d00-0000-1000-8000-00805f9b34fb",
)

def parse_advertisement_data(device: BLEDevice, advertisement_data: AdvertisementData):
    _mgr_datas = list(advertisement_data.manufacturer_data.values())
    service_data = advertisement_data.service_data

    if not service_data:
        return None

    _service_data = None
    for uuid in SERVICE_DATA_ORDER:
        if uuid in service_data:
            _service_data = service_data[uuid]
            break
    if not _service_data:
        _service_data = list(advertisement_data.service_data.values())[0]
    if not _service_data:
        return None

    _mfr_data = _mgr_datas[0] if _mgr_datas else None

    data = _parse_data(_service_data, _mfr_data)
    
    
    