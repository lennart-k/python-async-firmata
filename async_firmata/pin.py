from async_firmata.const import *
from async_firmata.exceptions import CapabilityNotAvailable

from collections import defaultdict
import asyncio

from typing import Union, Coroutine, Callable

class Pin:
    _mode: int
    _value: int

    def __init__(self, board, id: int, type: str, capabilities: list) -> None:
        self.id = id
        self.type = type
        self.board = board
        self.loop = board.loop
        self.callback = None

        self._value = 0
        self._mode = None
        self.capabilities = capabilities
        self.reporting = False

    @property
    def mode(self) -> int:
        return self._mode

    async def digital_write(self, value: bool) -> None:
        """
        Write a digital value to a pin
        """
        if CAPABILITY_DIGITAL_OUTPUT in self.capabilities:
            self.value = value
            return await self.board.send_packet(
                [SET_DIGITAL_PIN_VALUE, self.id, value])
        if CAPABILITY_ANALOG_OUTPUT in self.capabilities:
            return await self.analog_write(1)
        raise CapabilityNotAvailable()

    async def analog_write(self, value: int) -> None:
        """
        Write an analog value to a pin ranging from 0 to 1
        """
        if PWM in self.capabilities:
            converted_value = int(round(value*(
                (1 << self.capabilities[CAPABILITY_ANALOG_OUTPUT])-1)))
            await self.board.send_packet([
                ANALOG_MESSAGE+self.id,
                converted_value % 128,
                converted_value >> 7])
            self.value = value
        else:
            raise CapabilityNotAvailable()

    async def pin_mode(self, mode: int) -> None:
        """
        Sets the pin's mode
        Raises CapabilityNotAvailable
        """
        if mode not in self.capabilities:
            raise CapabilityNotAvailable(
                f"The capability {hex(mode)} is not available")
        pin_number = (self.id
                      if not self.type == ANALOG
                      else len(self.board.digital)+self.id)
        await self.board.send_packet([SET_PIN_MODE, pin_number, mode])
        self._mode = mode

    async def set_reporting(self, value: bool) -> None:
        """
        Send a report request.
        This is needed if you want to read inputs
        """
        if self.type == ANALOG:
            await self.board.send_packet(
                bytearray([REPORT_ANALOG_PIN+self.id, value]))
        if self.type == DIGITAL:
            port = self.id // 8

            await self.board.send_packet(
                bytearray([REPORT_DIGITAL_PORT+port, value]))

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, value):
        """
        Setter for the value property
        This calls the callback routine if the value changes
        """
        old_val = self._value
        self._value = value
        if self.callback and old_val != self._value:
            if not asyncio.iscoroutinefunction(self.callback):
                self.loop.run_in_executor(
                    None, lambda: self.callback(self, value))
            else:
                asyncio.ensure_future(self.callback(self, value))

    def set_callback(self, callback: Union[Callable, Coroutine] = None):
        """
        Sets the callback function for the Pin
        The callback function will get called when the value changes
        """
        self.callback = callback
