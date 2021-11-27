#%%
import sys
from pathlib import Path


sys.path.append("c:\\Users\\sbsuser\\Desktop\\goff-rotation")
from src.imaging.laser import Laser

ports = {
    "xstage": "COM20",
    "ystage": "COM21",
    "pumpa": "COM13",
    "pumpb": "COM9",
    "valvea24": "COM7",
    "valvea10": "COM10",
    "valveb24": "COM5",
    "valveb10": "COM6",
    "fpgacommand": "COM14",
    "fpgaresponse": "COM23",
    "laser1": "COM15",
    "laser2": "COM17",
    "arm9chem": "COM12",
}


from rich import print
from rich.logging import RichHandler

print("[green]Holding breath...")

import logging

logging.basicConfig(
    level="NOTSET",
    format="[yellow]%(name)-10s[/] %(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)],
)

from src.imaging.ystage import YStage

test = YStage("COM21")
test.move(0)
# %%
logger = logging.getLogger()
from src.utils.com import COM

fpga = COM("fpga", "COM14", "COM23", logger=logger)
# %%

laser = Laser("COM15")
laser.status
# %%