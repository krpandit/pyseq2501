from contextlib import nullcontext

import pytest
import pytest_asyncio

from pyseq2.flowcell import FlowCells
from pyseq2.utils.ports import get_ports


@pytest_asyncio.fixture(scope="module")
async def fcs() -> FlowCells:
    ports = await get_ports()
    return await FlowCells.ainit(ports)


async def test_initialize(fcs: FlowCells):
    await fcs.initialize()


async def test_pump(fcs: FlowCells):
    await fcs.A.flow(1, wait=0.1)