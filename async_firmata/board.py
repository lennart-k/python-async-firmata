from collections import defaultdict
import asyncio
from io import BytesIO
from functools import partial, wraps, reduce
import serial_asyncio

from .const import *
from .firmware import Firmware
from .pin import Pin
from .exceptions import FirmataException


def char_generator(data):
    while True:
        if not data:
            break
        lsb = data.pop(0)
        msb = data.pop(0) if data else 0
        yield chr(msb << 7 | lsb)

def set_event(f, async_event):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        result = await f(*args, **kwargs)
        async_event.set()
        return result
    return wrapper


class SerialIO(asyncio.Protocol):
    _connected: asyncio.Event

    def __init__(self, board = None):
        self._connected = asyncio.Event()
        self.board = board
        self.transport = None
        self._buffer = bytearray()

    def connection_made(self, transport):
        self.transport = transport
        self._connected.set()
        print("CONNECTION MADE")
        print(self.transport)

    def data_received(self, packet):
        packet = bytearray(packet)
        self._buffer.extend(packet)
        while SYSEX_END in self._buffer and START_SYSEX in self._buffer:
            del self._buffer[:self._buffer.index(START_SYSEX)+1]
            sysex_message = self._buffer[:self._buffer.index(SYSEX_END)]
            del self._buffer[:self._buffer.index(SYSEX_END)+1]
            command = sysex_message.pop(0)
            print(hex(command))
            asyncio.ensure_future(self.board.handle_sysex_command(command, sysex_message))

    def connection_lost(self, exc):
        self._connected.clear()
        print("Serial IO closed!")

    def pause_writing(self):
        pass

    def resume_writing(self):
        pass

    async def write(self, packet):
        await self._connected.wait()
        return self.transport.write(packet)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()


class Board:
    """
    Base class for every Firmata board which directly takes the transports
    """

    _cmd_handlers: defaultdict
    firmware: Firmware = None
    analog: [Pin]
    digital: [Pin]
    _ready: asyncio.Event
    _pin_specs: list

    def __init__(self, reader: asyncio.ReadTransport, writer: asyncio.WriteTransport):
        self._cmd_handlers = defaultdict(set)
        self.reader = reader
        self.writer = writer
        self._ready = asyncio.Event()
        self._pin_specs = []

    async def setup(self):
        self.add_command_handler(SYSEX_STRING, self.on_string_message)  # Error messages
        self.add_command_handler(PIN_STATE_RESPONSE, self.on_pin_state)
        self.add_command_handler(ANALOG_MESSAGE, self.on_analog_message)  # Analog values (pin, lsb, msb)
        self.add_command_handler(DIGITAL_MESSAGE, self.on_digital_message)

        await self.fetch_firmware_info()
        await self.fetch_capabilities()
        await self.fetch_analog_mapping()

        self._ready.set()

    async def on_pin_state(self, pin, mode, *state):
        """
        Handle pin state
        """
        pin: Pin = self.pins[pin]
        value = reduce(lambda x, y: x+y, (val*(2**(index*7)) for index, val in enumerate(state))) / (2**pin.capabilities[mode]-1)
        pin.value = value

    async def on_analog_message(self, *data):
        print("ANALOG MESSAGE", data)

    async def on_digital_message(self, *data):
        print("DIGITAL MESSAGE", data)

    async def on_string_message(self, *data):
        raise FirmataException("".join(list(char_generator(list(data)))))

    async def fetch_firmware_info(self):
        firmware_info_fetched = asyncio.Event()
        self.add_command_handler(SYSEX_FIRMWARE_INFO, set_event(self.on_firmware_response, async_event=firmware_info_fetched))
        await self.send_sysex_command(SYSEX_FIRMWARE_INFO)
        await firmware_info_fetched.wait()

    async def on_firmware_response(self, *data):
        data = list(data)
        version = data.pop(0), data.pop(0)
        self.firmware = Firmware("".join(list(char_generator(data))), version)

    async def fetch_capabilities(self):
        capabilities_fetched = asyncio.Event()
        self.add_command_handler(CAPABILITY_RESPONSE, set_event(self.on_capability_response, async_event=capabilities_fetched))
        await self.send_sysex_command(CAPABILITY_QUERY)
        await capabilities_fetched.wait()

    async def on_capability_response(self, *data):
        buffer = []

        for byte in data:
            if byte == SYSEX_REALTIME:
                self._pin_specs.append(buffer.copy())
                buffer.clear()
            else:
                buffer.append(byte)

    async def fetch_analog_mapping(self):
        analog_mapping_fetched = asyncio.Event()
        self.add_command_handler(ANALOG_MAPPING_RESPONSE, set_event(self.on_analog_mapping_response, async_event=analog_mapping_fetched))
        await self.send_sysex_command(ANALOG_MAPPING_QUERY)
        await analog_mapping_fetched.wait()

    async def on_analog_mapping_response(self, *data):
        self.analog = []
        self.digital = []
        for index, value in enumerate(data):
            if not value == SYSEX_REALTIME:
                self.analog.append(Pin(self, self._pin_specs[index], value, ANALOG))

            else:
                self.digital.append(Pin(self, self._pin_specs[index], index, DIGITAL))

    def add_command_handler(self, command: str, handler):
        self._cmd_handlers[command].add(handler)

    async def send_sysex_command(self, command, data: bytearray = None):
        print("SYSEX SENT", [START_SYSEX, command]+(data or [])+[SYSEX_END])
        return await self.writer.write(bytearray([START_SYSEX, command]+(data or [])+[SYSEX_END]))

    async def send_data(self, data: bytearray):
        return await self.writer.write(data)

    async def handle_sysex_command(self, command, data):
        for handler in self._cmd_handlers[command]:
            asyncio.ensure_future(handler(*data))

    async def close(self):
        self._ready.clear()
        print("BOARD CLOSING")

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

class SerialBoard(Board):
    def __init__(self, port, loop):
        self.port = port
        self.loop = loop


    async def setup(self):
        reader, writer = await serial_asyncio.create_serial_connection(self.loop, partial(SerialIO, board=self), self.port, baudrate=57600)
        super().__init__(reader=reader, writer=writer)
        await super().setup()

class ArduinoUno(SerialBoard):
    async def fetch_analog_mapping(self):
        await self.on_analog_mapping_response(127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 0, 1, 2, 3, 4, 5)

