# async-firmata

## An asynchronous interface for the Firmata protocol

async-firmata is a Firmata library built with asynchronous programming in mind

## Features

- asynchronous
- digital_write
- analog_write
- pin value reporting

## Installation

You can currently install the latest version for Python >= 3.6 with
```bash
python -m pip install git+https://github.com/lennart-k/python-async-firmata
```

## Example

```py
import asyncio

from async_firmata.board import SerialFirmataBoard
from async_firmata.pin import Pin
from async_firmata.const import *


loop = asyncio.get_event_loop()

board = SerialFirmataBoard(
    "/dev/ttyUSB0",
    loop=loop)


async def value_change(pin: Pin, value: float) -> None:
    await board.digital[3].analog_write(value)


async def main() -> None:
    await board.setup()
    await board.digital[3].pin_mode(ANALOG_OUTPUT)
    await board.analog[1].pin_mode(ANALOG_INPUT)
    board.analog[1].set_callback(value_change)
    await board.analog[1].set_reporting(True)

loop.call_soon(lambda: loop.create_task(main()))

try:
    loop.run_forever()
except KeyboardInterrupt:
    pass
finally:
    loop.run_until_complete(board.close())
    loop.close()
```