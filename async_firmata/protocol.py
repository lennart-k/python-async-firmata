import asyncio


from async_firmata.const import *

class Protocol(asyncio.Protocol):
    """asyncio Protocol responsible for data transport"""
    _connected: asyncio.Event
    _buffer: bytearray
    transport: asyncio.Transport

    def __init__(self, board) -> None:
        self.board = board
        self.loop = board.loop
        self._connected = asyncio.Event()

    def connection_made(self, transport: asyncio.Transport) -> None:
        """
        Handles a new connection
        Saves the transport and sets the connected event
        """
        self.transport = transport
        self._buffer = bytearray()
        self._connected.set()

    def data_received(self, data: bytearray) -> None:
        """
        Handles new packets
        """
        self._buffer.extend(data)

        if data[0] < SYSEX_START:
            while data:
                message_type = data[0] & 0xF0
                if message_type == ANALOG_MESSAGE:
                    asyncio.ensure_future(
                        self.board.handle_analog_message(data[0] & 0x0F, data[1], data[2]))
                    data = data[3:]
                elif message_type == DIGITAL_MESSAGE:
                    print(data)
                    asyncio.ensure_future(
                        self.board.handle_digital_message(data[0] & 0x0F, data[1], data[2]))
                    data = data[3:]
                else:
                    break

        while SYSEX_END in self._buffer and SYSEX_START in self._buffer:
            del self._buffer[:self._buffer.index(SYSEX_START)+1]
            sysex_message = self._buffer[:self._buffer.index(SYSEX_END)]
            del self._buffer[:self._buffer.index(SYSEX_END)+1]

            asyncio.ensure_future(self.board.handle_sysex_command(sysex_message[0], sysex_message[1:]))

    def connection_lost(self, exc: Exception) -> None:
        self._connected.clear()

    async def write(self, packet: bytearray) -> None:
        await self._connected.wait()
        return self.transport.write(packet)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def close(self) -> None:
        if self.connected:
            self.transport.close()
