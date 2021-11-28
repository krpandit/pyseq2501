import re
from concurrent.futures import Future
from logging import getLogger

from src.instruments import FPGAControlled
from src.utils.com import COM, CmdParse, ok_if_match

logger = getLogger(__name__)


Y_OFFSET = int(7e6)


class TDICmd:
    @staticmethod
    def read_y(resp: str) -> int:
        match = re.match(r"^TDIYERD (\d+)$", resp)
        assert match is not None
        return int(match.group(1)) - Y_OFFSET

    # fmt: off
    GET_ENCODER_Y = CmdParse(              "TDIYERD"                            , read_y)
    SET_ENCODER_Y =          lambda x:    f"TDIYEWR {x + Y_OFFSET}"

    SET_TRIGGER   = CmdParse(lambda x:    f"TDIYPOS {x + Y_OFFSET - 80000}", ok_if_match("TDIYPOS"))
    WHATISTHIS             = lambda n:    f"TDIYARM2 {n} 1"
    ARM_TRIGGER            = lambda n, y: f"TDIYARM3 {n} {y + Y_OFFSET - 10000} 1"
    # fmt: on


# TDIYPOS == Set when to send first trigger aka starting position

# TDIYARM2 2816 Number of lines to capture. 22 frames V=0.115 Also with ZARM and ZYT ZFREQCL 10000
# Seems like Y scan with Z step occuring every end of scan.

# TDIYWAIT then immediately send Y move commands Seems to block thread until done.
#

# TDIYARM3 nlines  ZFREQCL 7500
# pos = TDIYPOS + 20813


class TDI(FPGAControlled):
    cmd = TDICmd

    def __init__(self, fpga_com: COM) -> None:
        super().__init__(fpga_com)
        self._position: int
        # self.fcom.repl(TDICmd.GET_ENCODER_Y).add_done_callback(
        #     lambda x: setattr(self, "_position", x.result())
        # )

    @property
    def encoder_pos(self):
        return self._position

    @encoder_pos.setter
    def encoder_pos(self, pos: int):
        self.fcom.repl(TDICmd.SET_ENCODER_Y(pos))
        self._position = pos

    def prepare_for_imaging(self, n_px_y: int, pos: int) -> Future[list[str]]:
        self.encoder_pos = pos
        return self.fcom.repl([TDICmd.SET_TRIGGER(pos), TDICmd.ARM_TRIGGER(n_px_y, pos)])

    # def TDIYPOS(self, y_pos) -> Future[bool]:
    #     """Set the y position for TDI imaging.

    #     **Parameters:**
    #      - y_pos (int): The initial y position of the image.

    #     """
    #     return self.fcom.repl(TDICmd.SET_Y_POS(y_pos))

    # def TDIYARM3(self, n_triggers, y_pos) -> Future[str]:
    #     """Arm the y stage triggers for TDI imaging.

    #     **Parameters:**
    #      - n_triggers (int): Number of triggers to send to the cameras.
    #      - y_pos (int): The initial y position of the image.

    #     """
    #     return self.fcom.repl(TDICmd.ARM(n_triggers, y_pos))
