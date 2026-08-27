"""What the app has been doing, kept in memory for the Log tab.

The worker's lines arrive whether or not a window is open, so the buffer lives with
the app, not the window: open the tab and the last few hundred lines are there. It is
not a file — a person wanting a record of every sync has the Activity feed on the
website, which is the bundle's history; this is for "what is it doing right now".
"""

from collections import deque
from datetime import datetime

KEEP = 500


class Log:
    def __init__(self, keep: int = KEEP) -> None:
        self.lines: deque[str] = deque(maxlen=keep)

    def add(self, text: str, at: datetime | None = None) -> str:
        line = f"{(at or datetime.now()):%H:%M:%S}  {text}"
        self.lines.append(line)
        return line

    def text(self) -> str:
        return "\n".join(self.lines)

    def clear(self) -> None:
        self.lines.clear()
