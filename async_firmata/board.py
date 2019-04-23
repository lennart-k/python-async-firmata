from collections import defaultdict
import asyncio
from io import BytesIO
from functools import partial, wraps, reduce
import serial_asyncio
from serial.serialutil import SerialException

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
        asyncio.run_coroutine_threadsafe(self.board.on_connected(), loop=self.board.loop)

    def data_received(self, packet):
        packet = bytearray(packet)
        self._buffer.extend(packet)

        if self._buffer[0] < START_SYSEX and len(self._buffer) >= 3 and self.board._ready.is_set():
            command = self._buffer.pop(0)
            message_type = command & 0xF0
            pin = command & 0x0F
            lsb = self._buffer.pop(0)
            msb = self._buffer.pop(0)
            value = msb << 7 | lsb

            if message_type == ANALOG_MESSAGE:
                return asyncio.ensure_future(self.board.handle_analog_message(pin, value))
            elif message_type == DIGITAL_MESSAGE:
                return asyncio.ensure_future(self.board.handle_digital_message(pin, value))

        while SYSEX_END in self._buffer and START_SYSEX in self._buffer:
            del self._buffer[:self._buffer.index(START_SYSEX)+1]
            sysex_message = self._buffer[:self._buffer.index(SYSEX_END)]
            del self._buffer[:self._buffer.index(SYSEX_END)+1]
            command = sysex_message.pop(0)
            asyncio.ensure_future(self.board.handle_sysex_command(command, sysex_message))


    def connection_lost(self, exc):
        self._connected.clear()
        if hasattr(self.board, "connection_lost"):
            asyncio.ensure_future(self.board.connection_lost())
            asyncio.run_coroutine_threadsafe(self.board.on_connection_lost(), loop=self.board.loop)

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

    def __init__(self, reader: asyncio.ReadTransport, writer: asyncio.WriteTransport, loop: asyncio.AbstractEventLoop = None):
        self._cmd_handlers = defaultdict(set)
        self.reader = reader
        self.writer = writer
        self._ready = asyncio.Event()
        self._pin_specs = []
        self.loop = loop or asyncio.get_event_loop()

    async def setup(self):
        self.add_command_handler(SYSEX_STRING, self.handle_string_message)  # Error messages
        self.add_command_handler(PIN_STATE_RESPONSE, self.handle_pin_state)
        self.add_command_handler(ANALOG_MESSAGE, self.handle_analog_message)  # Analog values (pin, lsb, msb)
        self.add_command_handler(DIGITAL_MESSAGE, self.handle_digital_message)

        await self.fetch_firmware_info()
        await self.fetch_capabilities()
        await self.fetch_analog_mapping()

        self._ready.set()
        asyncio.run_coroutine_threadsafe(self.on_ready(), loop=self.loop)

    async def on_ready(self):
        """
        Implement your own handler
        """

    async def on_value_change(self, pin: Pin, type: (ANALOG, DIGITAL), value: int):
        """
        Implement your own handler
        """

    async def on_close(self):
        """
        Implement your own handler
        """

    async def on_connected(self):
        """
        Implement your own handler
        """

    async def on_connection_lost(self):
        """
        Implement your own handler
        """


    async def handle_pin_state(self, pin: int, mode: int, *state: bytearray) -> None:
        """
        Handle pin state
        """
        if mode == ANALOG_INPUT:
            pin: Pin = self.analog[pin]
        else:
            pin: Pin = self.digital[pin]
        value = reduce(lambda x, y: x+y, (val*(2**(index*7)) for index, val in enumerate(state))) / (2**pin.capabilities[mode]-1)
        pin.value = value

    async def handle_analog_message(self, pin: int, value: int):
        if pin < len(self.analog):
            await self.analog[pin]._update_analog(value)

    async def handle_digital_message(self, pin: int, value: bool):
        if pin < len(self.digital):
            await self.digital[pin]._update_digital(value)

    async def handle_string_message(self, *data):
        raise FirmataException("".join(list(char_generator(list(data)))))

    async def fetch_firmware_info(self):
        firmware_info_fetched = asyncio.Event()
        self.add_command_handler(SYSEX_FIRMWARE_INFO, set_event(self.handle_firmware_response, async_event=firmware_info_fetched))
        await self.send_sysex_command(SYSEX_FIRMWARE_INFO)
        await firmware_info_fetched.wait()

    async def handle_firmware_response(self, *data):
        data = list(data)
        version = data.pop(0), data.pop(0)
        self.firmware = Firmware("".join(list(char_generator(data))), version)

    async def fetch_capabilities(self):
        capabilities_fetched = asyncio.Event()
        self.add_command_handler(CAPABILITY_RESPONSE, set_event(self.handle_capability_response, async_event=capabilities_fetched))
        await self.send_sysex_command(CAPABILITY_QUERY)
        await capabilities_fetched.wait()

    async def handle_capability_response(self, *data):
        buffer = []

        for byte in data:
            if byte == SYSEX_REALTIME:
                self._pin_specs.append(buffer.copy())
                buffer.clear()
            else:
                buffer.append(byte)

    async def fetch_analog_mapping(self):
        analog_mapping_fetched = asyncio.Event()
        self.add_command_handler(ANALOG_MAPPING_RESPONSE, set_event(self.handle_analog_mapping_response, async_event=analog_mapping_fetched))
        await self.send_sysex_command(ANALOG_MAPPING_QUERY)
        await analog_mapping_fetched.wait()

    async def handle_analog_mapping_response(self, *data):
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
        return await self.writer.write(bytearray([START_SYSEX, command]+(data or [])+[SYSEX_END]))

    async def send_data(self, data: bytearray):
        return await self.writer.write(data)

    async def handle_sysex_command(self, command, data):
        for handler in self._cmd_handlers[command]:
            asyncio.ensure_future(handler(*data))

    async def close(self):
        asyncio.run_coroutine_threadsafe(self.on_close(), loop=self.board.loop)
        self._ready.clear()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

class SerialBoard(Board):
    def __init__(self, port, loop, timeout: int = None, revive_connection: bool = False):
        self.port = port
        self.loop = loop
        self.timeout = timeout
        self.revive_connection = revive_connection


    async def setup(self):
        reader, writer = await serial_asyncio.create_serial_connection(self.loop, partial(SerialIO, board=self), self.port, baudrate=57600, timeout=self.timeout)
        super().__init__(reader=reader, writer=writer, loop=self.loop)
        await super().setup()

    async def connection_lost(self) -> None:
        if self.revive_connection:
            while True:
                await asyncio.sleep(5)
                try:
                    await self.setup()
                except SerialException as e:
                    continue
                break

class ArduinoUno(SerialBoard):
    async def fetch_analog_mapping(self):
        await self.handle_analog_mapping_response(127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 127, 0, 1, 2, 3, 4, 5)

