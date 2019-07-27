from collections import defaultdict
import serial_asyncio
import asyncio
from functools import wraps
import logging

from async_firmata.protocol import Protocol
from async_firmata.const import *
from async_firmata.firmware import Firmware
from async_firmata.pin import Pin

from typing import List

LOGGER = logging.getLogger("async_firmata")

def char_generator(data: bytearray) -> str:
    """Converts an MSB-LSB array to a string"""
    out = ""
    while data:
        lsb = data.pop(0)
        msb = data.pop(0) if data else 0
        out += chr(msb << 7 | lsb)
    return out

def set_event(f, async_event):
    """Wrapper for a function that sets async_event"""
    @wraps(f)
    async def wrapper(*args, **kwargs):
        result = await f(*args, **kwargs)
        async_event.set()
        return result
    return wrapper


class FirmataBoard:
    _cmd_handlers: defaultdict
    _pin_specs: list
    firmware: Firmware
    digital: List[Pin]
    analog: List[Pin]

    def __init__(self, reader: asyncio.ReadTransport,
                       writer: asyncio.WriteTransport,
                       loop: asyncio.AbstractEventLoop = None) -> None:
        self.reader = reader
        self.writer = writer
        self._cmd_handlers = defaultdict(set)
        self._pin_specs = list()
        self.loop = loop or asyncio.get_event_loop()

    def add_command_handler(self, command: str, handler):
        """Adds a command handler"""
        self._cmd_handlers[command].add(handler)

    def remove_command_handler(self, command: str, handler):
        """Removes a command handler"""
        self._cmd_handlers[command].remove(handler)

    async def handle_sysex_command(self, command, data):
        """Dispatches a SysEx command"""
        LOGGER.debug("SysEx command received: %s %s", command, data)
        for handler in self._cmd_handlers[command]:
            asyncio.ensure_future(handler(data))

    async def send_packet(self, packet: bytearray) -> None:
        """Sends a packet"""
        logging.debug(" ".join(["{:02x}".format(byte).upper() for byte in packet]))
        return await self.writer.write(packet)

    async def send_sysex_command(self, command: int, data: bytearray = None) -> None:
        """
        Sends a SYSEX command
        This just wraps the packet into SYSEX_START and SYSEX_END
        """
        return await self.send_packet([SYSEX_START, command, *(data or []), SYSEX_END])

    async def fetch_firmware_info(self) -> None:
        """Requests firmware information and waits until it's received"""
        firmware_info_fetched = asyncio.Event()
        self.add_command_handler(SYSEX_FIRMWARE_INFO,
                                 set_event(self.handle_firmware_info,
                                 async_event=firmware_info_fetched))
        await self.send_sysex_command(SYSEX_FIRMWARE_INFO)
        await firmware_info_fetched.wait()

    async def handle_firmware_info(self, data: bytearray) -> None:
        """Handler for firmware info"""
        self.firmware = Firmware(
            name=char_generator(data[2:]),
            version=tuple(data[0:2])
        )
    
    async def fetch_capabilities(self):
        """Fetches the board's capabilities"""
        capabilities_fetched = asyncio.Event()
        self.add_command_handler(CAPABILITY_RESPONSE,
                                 set_event(self.handle_capability_response,
                                 async_event=capabilities_fetched))
        await self.send_sysex_command(CAPABILITY_QUERY)
        await capabilities_fetched.wait()

    async def handle_capability_response(self, data: bytearray):
        """
        Handles the board's capability response
        """
        pin_buffer = []
        for byte in data:
            if byte == SYSEX_REALTIME:
                self._pin_specs.append(pin_buffer.copy())
                pin_buffer.clear()
            else:
                pin_buffer.append(byte)
        print(self._pin_specs)

    async def fetch_analog_mapping(self):
        """Fetches information about the board's analog mapping"""
        analog_mapping_fetched = asyncio.Event()
        self.add_command_handler(ANALOG_MAPPING_RESPONSE,
                                 set_event(self.handle_analog_mapping_response,
                                 async_event=analog_mapping_fetched))
        await self.send_sysex_command(ANALOG_MAPPING_QUERY)
        await analog_mapping_fetched.wait()

    async def handle_analog_mapping_response(self, data: bytearray) -> None:
        """Handles the analog mapping response"""
        self.analog = []
        self.digital = []

        for index, value in enumerate(data):
            spec = self._pin_specs[index]
            capabilities = {spec[i]: spec[i+1] for i in range(0, len(spec), 2)}
            if not value == SYSEX_REALTIME:
                self.analog.append(Pin(self, value, ANALOG, capabilities))
            else:
                self.digital.append(Pin(self, index, DIGITAL, capabilities))

    async def handle_string_message(self, data: bytearray) -> None:
        """Logs a string message which generally is an error"""
        raise logging.error("String message: %s", char_generator(data))

    async def handle_analog_message(self, pin_id: int, lsb, msb) -> None:
        """Handles an analog message"""
        value = (msb << 7) + lsb
        resolution = self.analog[pin_id].capabilities[ANALOG_INPUT]
        converted_value = round(value/(1 << resolution), 4)
        pin = self.analog[pin_id]
        if pin.mode is ANALOG_INPUT:
            pin.value = converted_value

    async def handle_digital_message(self, port: int, lsb, msb) -> None:
        """Handles a digital message"""
        mask = (msb << 7) + lsb
        for index, pin in enumerate(self.digital[port*8:port*8+8]):
            value = bool(mask & (1 << index))
            if pin.mode is DIGITAL_INPUT:
                pin.value = value

    async def setup(self) -> None:
        """Coroutine to setup the board"""
        # Error messages
        self.add_command_handler(SYSEX_STRING, self.handle_string_message)

        await self.fetch_firmware_info()
        await self.fetch_capabilities()
        await self.fetch_analog_mapping()

    async def close(self) -> None:
        self.writer.close()

class SerialFirmataBoard(FirmataBoard):
    """
    Serial Firmata board which takes a url to create a connection
    """
    def __init__(self, port: str,
                       loop: asyncio.AbstractEventLoop = None,
                       timeout: int = None,
                       revive_connection: bool = False) -> None:
        
        self.port = port
        self.loop = loop
        self.timeout = timeout
        self.revive_connection = revive_connection

    async def setup(self) -> None:
        reader, writer = await serial_asyncio.create_serial_connection(
            self.loop,
            lambda: Protocol(board=self), self.port, baudrate=57600,
            timeout=self.timeout
            )
        super().__init__(reader=reader, writer=writer, loop=self.loop)
        await super().setup()
