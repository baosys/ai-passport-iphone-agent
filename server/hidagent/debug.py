"""Debug traces: one folder per agent run with every model call and the screenshot it saw."""

import json
import time
from pathlib import Path

from .vault import hidden


class Trace:
    def __init__(self, root: Path | None):
        self.root, self.dir, self._n = root, None, 0
        self.scrub = lambda record: record  # the agent sets its vault's: no secret value in any record

    @property
    def enabled(self) -> bool:
        return self.root is not None

    def start(self, task: str) -> None:
        if not self.enabled:
            return
        self.dir = self.root / time.strftime("%Y%m%d-%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._n = 0
        self.write(kind="task", task=task)

    def write(self, frame: bytes | None = None, **record) -> None:
        if not self.dir:
            return
        record = self.scrub(record)
        self._n += 1
        if frame and not hidden():  # no screenshot while a secret goes in: the pointer may rest on a digit
            record["frame"] = f"{self._n:03d}.jpg"
            (self.dir / record["frame"]).write_bytes(frame)
        with open(self.dir / "steps.jsonl", "a") as f:
            f.write(json.dumps({"n": self._n, "t": time.time(), **record}, ensure_ascii=False) + "\n")
