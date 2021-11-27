import re
from concurrent.futures import Future
from logging import getLogger

from src.instruments import FPGAControlled, Movable
from src.utils.com import CmdParse, ok_if_match

logger = getLogger("objective")


Y_OFFSET = int(7e6)


class ObjCmd:
    @staticmethod
    def get_pos(resp: str) -> int:
        match = re.match(r"^ZDACR (\d+)$", resp)
        assert match is not None
        return int(match.group(1))

    # Callable[[Annotated[int, "mm/s"]], str]
    # fmt: off
    SET_VELO = CmdParse(lambda x: f"ZSTEP {1288471 * x}", ok_if_match("ZSTEP"))
    SET_POS  = CmdParse(lambda x: f"ZMV {x}"            , ok_if_match("ZMV"))
    GET_POS  = CmdParse(           "ZDACR"              , get_pos)
    SET_TRIGGER = lambda x: f"ZTRG {x}"
    ARM_TRIGGER = "ZYT 0 3"
    # fmt: on


class Objective(FPGAControlled, Movable):
    STEPS_PER_UM = 262
    RANGE = (0, 65535)
    HOME = 65535

    cmd = ObjCmd

    def initialize(self) -> Future[bool]:
        return self.fcom.repl(ObjCmd.SET_VELO(5))