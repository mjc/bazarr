from datetime import datetime
from typing import Literal


def date(time: datetime | int | Literal[False] = False, asdays: bool = False, short: bool = False) -> str: ...
