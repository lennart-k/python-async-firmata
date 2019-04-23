import asyncio
from collections import defaultdict

from .const import *
from .exceptions import CapabilityNotAvailable


class Pin:
    def __init__(self, board, spec: list, pin_id: int, type: str, mode: int = None):
        self.board = board
        self.spec = spec
        self.id = pin_id
        self.value = 0
        self.type = type
        self._mode = mode
        self.capabilities = defaultdict(int)
        self.reporting = False

        capabilities = spec[:]
        while capabilities:
            key, value = capabilities.pop(0), capabilities.pop(0)
            self.capabilities[key] = value

    @property
    def mode(self) -> int:
        return self._mode
    
    async def analog_write(self, value: int):
        if CAPABILITY_ANALOG_OUTPUT in self.capabilities:
            converted_value = int(round(value*(2**self.capabilities[CAPABILITY_ANALOG_OUTPUT]-1)))
            await self.board.send_data([ANALOG_MESSAGE+self.id, converted_value % 128, converted_value >> 7])
            self.value = value

    async def digital_write(self, value: bool):
        if CAPABILITY_DIGITAL_OUTPUT in self.capabilities:
            await self.board.send_data([SET_DIGITAL_PIN_VALUE, self.id, value])
            self.value = value

    async def pin_mode(self, mode: int):
        if mode in self.capabilities:
            await self.board.send_data([SET_PIN_MODE, self.id, mode])
            self._mode = mode
        else:
            raise CapabilityNotAvailable()

    async def set_reporting(self, value: bool):
        """
        Send a report request.
        This is needed if you want to read inputs
        """
        if self.type == ANALOG:
            await self.board.send_data(bytearray([REPORT_ANALOG_PIN+self.id, value]))
        if self.type == DIGITAL:
            await self.board.send_data(bytearray([REPORT_DIGITAL_PIN+self.id, value]))

    async def digital_read(self):
        """
        Returns a digital value
        For analog pins it returns if the analog value is >= half of the maximum value
        """
        if self.mode == UNAVAILABLE:
            raise IOError("Pin {id} is unavailable".format(id=self.id))
        if self.type == ANALOG:
            return int(self.value >= 2**self.capabilities[ANALOG_INPUT]/2)
        return self.value

    async def analog_read(self):
        """
        This does not trigger a read
        It just returns the value updated by analog reports or set by you
        That means you need to do Pin.set_reporting(True) to read input
        """
        return self.value

    async def _update_analog(self, value: int):
        if not value == self.value:
            self.value = value
            asyncio.run_coroutine_threadsafe(self.board.on_value_change(self, self.type, value), loop=self.board.loop)

    async def _update_digital(self, value: bool):
        if not value == self.value:
            self.value = value
            asyncio.run_coroutine_threadsafe(self.board.on_value_change(self, self.type, value), loop=self.board.loop)

    def __repr__(self):
        return "<Pin id={id} mode={mode} value={value}>".format(id=self.id, mode=self.mode, value=self.value)
