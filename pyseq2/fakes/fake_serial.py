import asyncio
from asyncio import CancelledError, StreamReader, StreamWriter
from logging import getLogger
from typing import Callable, Literal, NoReturn

from pydantic import BaseModel

from pyseq2.base.instruments_types import SEPARATOR, SerialInstruments
from pyseq2.fakes.fake_handlers import fake_arm9, fake_fpga, fake_laser, fake_pump, fake_valve, fake_x, fake_y

handlers: dict[SerialInstruments, Callable[[str], str]] = {
    "x": fake_x,
    "y": fake_y,
    "laser_g": fake_laser,
    "laser_r": fake_laser,
    "arm9chem": fake_arm9,
    "pumpa": fake_pump,
    "pumpb": fake_pump,
    "valve_a1": fake_valve,
    "valve_a2": fake_valve,
    "valve_b1": fake_valve,
    "valve_b2": fake_valve,
    "fpga": fake_fpga,
}

logger = getLogger(__name__)


class FakeOptions(BaseModel):
    drop: bool = False
    delay: float = 0
    split_delay: float = 0


class FakeTransport(asyncio.Transport):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        protocol: asyncio.StreamReaderProtocol,
        name: SerialInstruments,
        test_params: FakeOptions,
    ):
        super().__init__()

        self._name = name
        self._loop = loop
        self._protocol = protocol
        self.f = handlers[name]
        self.loop = asyncio.get_event_loop()

        self.test_params = test_params
        self.sep = SEPARATOR.get(name, b"\n")

        self.q_rcvd: asyncio.Queue[bytes] = asyncio.Queue()

        loop.call_soon(protocol.connection_made, self)
        self.task = asyncio.create_task(self._process_forever())

    async def _process_forever(self) -> NoReturn:
        while True:
            try:
                cmd = await self.q_rcvd.get()
                self._protocol.data_received(cmd)  # Data received from the serial port.
                self.q_rcvd.task_done()
            except (CancelledError, RuntimeError) as e:
                raise e
            except BaseException as e:
                logger.critical(f"{type(e).__name__}: {e}")

    def write(self, data: bytes):
        if self.test_params.drop:
            return

        for i, res in enumerate(self.f(data.strip().decode("ISO-8859-1")).split("\n")):
            res = res.encode("ISO-8859-1") + self.sep
            if self.test_params.delay or (i > 0 and self.test_params.split_delay):
                self.loop.call_later(self.test_params.delay, self.q_rcvd.put_nowait, res)
                continue

            self.q_rcvd.put_nowait(res)


async def open_fake(
    url: str,
    name: SerialInstruments,
    baudrate: Literal[9600, 115200],
    test_params: FakeOptions,
) -> tuple[StreamReader, StreamWriter]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(loop=loop)
    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    writer = asyncio.StreamWriter(FakeTransport(loop, protocol, name, test_params), protocol, reader, loop)
    return reader, writer
