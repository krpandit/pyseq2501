# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from logging import getLogger
from pathlib import Path
from typing import Any, Awaitable, Callable
from typing import Literal as L
from typing import Optional, TypeVar, cast

import numpy as np
from pydantic import BaseModel
from tifffile import TiffWriter

from .base.instruments_types import SerialPorts
from .imaging.camera.dcam import Cameras, Mode, UInt16Array
from .imaging.fpga import FPGA
from .imaging.laser import Laser, Lasers
from .imaging.xstage import XStage
from .imaging.ystage import YStage
from .utils.log import init_log
from .utils.utils import IS_FAKE, Singleton

logger = getLogger(__name__)
executor = ThreadPoolExecutor()


class Position(BaseModel):
    x: int
    y: int
    z_tilt: tuple[int, int, int]
    z_obj: int

    @classmethod
    def default(cls) -> Position:
        return Position(x=0, y=0, z_tilt=(0, 0, 0), z_obj=0)


class OpticState(BaseModel):
    laser_onoff: tuple[bool, bool]
    lasers: tuple[int, int]
    shutter: bool
    od: tuple[float, float]

    @classmethod
    def default(cls) -> OpticState:
        return OpticState(laser_onoff=(True, True), lasers=(5, 5), shutter=False, od=(0.0, 0.0))


class State(Position, OpticState):  # type: ignore
    @classmethod
    def default(cls) -> State:
        return State(**Position.default().dict(), **OpticState.default().dict())


# Due to the optical arrangement, the actual channel ordering
# is not in order of increasing wavelength.
CHANNEL = {0: 1, 1: 2, 2: 0, 3: 3}
T = TypeVar("T")


class Imager(metaclass=Singleton):
    UM_PER_PX = 0.375

    @classmethod
    async def ainit(cls, ports: dict[SerialPorts, str], init_cam: bool = True) -> Imager:
        to_init = (
            FPGA.ainit(ports["fpgacmd"], ports["fpgaresp"]),
            XStage.ainit(ports["x"]),
            YStage.ainit(ports["y"]),
            Laser.ainit("g", ports["laser_g"]),
            Laser.ainit("r", ports["laser_r"]),
        )

        if init_cam:
            fpga, x, y, laser_g, laser_r, cams = await asyncio.gather(*to_init, Cameras.ainit())
            return cls(fpga, x, y, Lasers(g=laser_g, r=laser_r), cams)

        fpga, x, y, laser_g, laser_r = await asyncio.gather(*to_init)
        return cls(fpga, x, y, Lasers(g=laser_g, r=laser_r), cams=None)

    def __init__(self, fpga: FPGA, x: XStage, y: YStage, lasers: Lasers, cams: Cameras | None) -> None:
        self.fpga = fpga
        self.tdi = self.fpga.tdi
        self.optics = self.fpga.optics
        self.lasers = lasers

        self.x = x
        self.y = y
        self.z_tilt = self.fpga.z_tilt
        self.z_obj = self.fpga.z_obj

        self.cams = cams
        self.lock = asyncio.Lock()

    @init_log(logger, info=True)
    async def initialize(self) -> None:
        todos = (
            self.x.initialize(),
            self.y.initialize(),
            self.z_tilt.initialize(),
            self.z_obj.initialize(),
            self.optics.initialize(),
        )

        async with self.lock:
            for i, f in enumerate(asyncio.as_completed(todos), 1):
                await f
                logger.info(f"Imager initialization [{i}/{len(todos)}] completed.")

    @property
    async def pos(self) -> Position:
        names = {
            "x": self.x.pos,
            "y": self.y.pos,
            "z_tilt": self.z_tilt.pos,
            "z_obj": self.z_obj.pos,
        }
        res = await asyncio.gather(*names.values())
        return Position(**dict(zip(names.keys(), res)))  # type: ignore

    @property
    async def state(self) -> State:
        logger.debug("Begin state run.")
        raw = {
            "on0": self.lasers[0].status,
            "on1": self.lasers[1].status,
            "p0": self.lasers[0].power,
            "p1": self.lasers[1].power,
            "sh": self.optics.shutter,
            "od0": self.optics[0].pos,
            "od1": self.optics[1].pos,
            "pos": self.pos,
        }

        raw = dict(zip(raw.keys(), await asyncio.gather(*raw.values())))

        optic_state = {
            "laser_onoff": (raw["on0"], raw["on1"]),
            "lasers": (raw["p0"], raw["p1"]),
            "shutter": raw["sh"],
            "od": (raw["od0"], raw["od1"]),
        }
        pos: Position = cast(Position, raw["pos"])
        logger.debug("End state run.")
        return State(**optic_state, **pos.dict())

    async def wait_ready(self) -> None:
        """Returns when no commands are pending return which indicates that all motors are idle.
        This is because all move commands are expected to return some value upon completion.
        """
        logger.info("Waiting for all motions to complete.")
        await self.x.com.wait()
        await self.y.com.wait()
        await self.fpga.com.wait()
        logger.info("All motions completed.")

    async def move(
        self,
        *,
        x: Optional[int] = None,
        y: Optional[int] = None,
        z_obj: Optional[int] = None,
        z_tilt: Optional[int | tuple[int, int, int]] = None,
        lasers: Optional[tuple[int | None, int | None]] = None,
        laser_onoff: Optional[tuple[bool | None, bool | None]] = None,
        shutter: Optional[bool] = None,
        od: Optional[tuple[float | None, float | None]] = None,
    ) -> None:
        async with self.lock:
            cmds: list[Awaitable[Any]] = []
            if x is not None:
                cmds.append(self.x.move(x))
            if y is not None:
                cmds.append(self.y.move(y))
            if z_obj is not None:
                cmds.append(self.z_obj.move(z_obj))
            if z_tilt is not None:
                cmds.append(self.z_tilt.move(z_tilt))
            if lasers is not None:
                cmds += [la.set_power(p) for la, p in zip(self.lasers, lasers) if p is not None]
            if laser_onoff is not None:
                cmds += [la.set_onoff(lo) for la, lo in zip(self.lasers, laser_onoff) if lo is not None]
            if shutter is not None:
                cmds.append(self.optics._open() if shutter else self.optics._close())
            if od is not None:
                cmds += [f.move(o) for f, o in zip(self.optics.filters, od) if o is not None]
            await asyncio.gather(*cmds)

    async def take(
        self,
        n_bundles: int,
        dark: bool = False,
        channels: list[int] = [0, 1, 2, 3],
        move_back_to_start: bool = True,
        event_queue: tuple[asyncio.Queue[T], Callable[[int], T]] | None = None,
    ) -> tuple[UInt16Array, State]:
        assert self.cams is not None
        if self.lock.locked():
            logger.info("Waiting for the previous imaging operation to complete.")

        if len(channels) != len(set(channels)):
            raise ValueError("Channels must be unique.")

        if min(channels) < 0 or max(channels) > 3:
            raise ValueError("Channels must be between 0 and 3.")

        async with self.lock:
            if not 0 < n_bundles < 1500:
                raise ValueError("n_bundles should be between 0 and 1500.")

            logger.info(f"Taking an image with {n_bundles} from channel(s) {channels}.")

            c = [CHANNEL[x] for x in sorted(channels)]
            if (0 in c or 1 in c) and (2 in c or 3 in c):
                cam = 2
            elif 0 in c or 1 in c:
                cam = 0
            elif 2 in c or 3 in c:
                cam = 1
            else:
                raise ValueError("Invalid channel(s).")

            # TODO: Compensate actual start position.
            n_bundles += 1  # To flush CCD.
            await self.wait_ready()

            state = await self.state
            pos = state.y
            n_px_y = n_bundles * self.cams.BUNDLE_HEIGHT
            # Need overshoot for TDI to function properly.
            end_y_pos = pos - self.calc_delta_pos(n_px_y) - 100000
            assert end_y_pos > -7e6

            await asyncio.gather(self.tdi.prepare_for_imaging(n_px_y, pos), self.y.set_mode("IMAGING"))
            cap = self.cams.capture(
                n_bundles, fut_capture=self.y.move(end_y_pos, slowly=True), cam=cam, event_queue=event_queue
            )

            if dark:
                imgs = await cap
            else:
                async with self.optics.open_shutter():
                    imgs = await cap

            logger.info(f"Done taking an image.")

            await self.y.move(pos if move_back_to_start else end_y_pos + 100000)  # Correct for overshoot.
            imgs = np.clip(np.flip(imgs, axis=1), 0, 4096)
            return (
                imgs[c if cam != 1 else [x - 2 for x in c], :-128, :],
                state,
            )  # Remove oversaturated first bundle.

    @staticmethod
    def calc_delta_pos(n_px_y: int) -> int:
        return int(n_px_y * Imager.UM_PER_PX * YStage.STEPS_PER_UM)

    async def autofocus(
        self, channel: L[0, 1, 2, 3] = 1, use_laplacian: bool = True
    ) -> tuple[int, np.ndarray[L[259], np.dtype[np.float64]], UInt16Array]:
        """Moves to z_max and takes 232 (2048 × 5) images while moving to z_min.
        Returns the z position of maximum intensity and the images.
        """
        assert self.cams is not None
        if self.lock.locked():
            logger.info("Waiting for previous imaging operation to complete.")

        async with self.lock:
            logger.info(f"Starting autofocus using data from {channel=}.")
            if channel not in (0, 1, 2, 3):
                raise ValueError(f"Invalid channel {channel}.")

            await self.wait_ready()

            n_bundles, height = 259, 64
            z_min, z_max = 2621, 60292
            cam = CHANNEL[channel] >> 1

            async with self.z_obj.af_arm(z_min=z_min, z_max=z_max, speed=0.1) as start_move:
                async with self.optics.open_shutter():
                    img = await self.cams.capture(
                        n_bundles,
                        (height, 4096),
                        fut_capture=start_move,
                        mode=Mode.FOCUS_SWEEP,
                        cam=cast(L[0, 1], cam),
                    )

            stack: UInt16Array = np.reshape(img[CHANNEL[channel] - 2 * cam], (n_bundles, height, 2048))
            if IS_FAKE() and (path := Path(__file__).parent / "imaging" / "afdata.npy").exists():
                stack = np.load(path)

            if use_laplacian:
                measure = np.var(self.laplacian(stack), axis=(1, 2))
            else:
                measure = np.mean(stack, axis=(1, 2))

            target = int(z_max - ((z_max - z_min) * np.argmax(measure) / n_bundles))  # type: ignore
            logger.info(f"Done autofocus. Optimum={target}")
            if not 10000 < target < 50000:
                logger.info(f"Target too close to edge, considering moving the tilt motors.")
            return (target, measure, stack)

    @staticmethod
    def laplacian(img: UInt16Array) -> UInt16Array:
        # https://stackoverflow.com/questions/43086557/convolve2d-just-by-using-numpy
        k = np.array([[0, -1, 0], [-1, 4, -1], [0, -1, 0]])
        shape = k.shape + (img.shape[0],) + tuple(np.subtract(img.shape[1:], k.shape) + 1)
        strides = (img.strides * 2)[1:]
        M: UInt16Array = np.lib.stride_tricks.as_strided(img, shape=shape, strides=strides)  # type: ignore
        return np.einsum("pq,pqbmn->bmn", k, M)

    @staticmethod
    def _save(path: str | Path, img: UInt16Array, state: Optional[State] = None) -> None:
        with TiffWriter(path, ome=True) as tif:
            tif.write(
                img,
                compression="ZLIB",
                resolution=(1 / (0.375 * 10**-4), 1 / (0.375 * 10**-4), "CENTIMETER"),  # 0.375 μm/px
                metadata={"axes": "CYX" if len(img.shape) == 3 else "ZCYX", "SignificantBits": 12},
            )

    @staticmethod
    async def save(path: str | Path, img: UInt16Array, state: Optional[State] = None) -> None:
        """
        Based on 2016-06
        http://www.openmicroscopy.org/Schemas/Documentation/Generated/OME-2016-06/ome.html

        Focus on the Image/Pixel attribute.
        https://github.com/cgohlke/tifffile/issues/92#issuecomment-879309911
        TODO: Find some way to embed metadata in the OME-XML https://github.com/cgohlke/tifffile/issues/65

        Args:
            path (str | Path): _description_
            img (UInt16Array): _description_
            state (State): _description_
        """
        if isinstance(path, Path):
            path = path.as_posix()

        if not (path.endswith(".tif") or path.endswith(".tiff")):
            logger.warning("File name does not end with a tif extension.")

        await asyncio.get_running_loop().run_in_executor(executor, Imager._save, path, img, state)
