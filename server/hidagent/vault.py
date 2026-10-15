"""Secrets a task uses (e.g. a payment password) without any model, trace or log seeing them.

A secret lives only on this computer: on macOS a Keychain item of service "hidagent" and account NAME (looked up
first), or HIDAGENT_SECRET_<NAME> in the environment (server/.env). HIDAGENT_SECRET_<NAME>_MAX caps the amount a
task that uses it may state. A task names it as {NAME}; the models see only that
placeholder and enter it with the `secret NAME` or `keypad NAME …` actions, which the agent runs itself. Any value
that still shows up in text (typed into the task, echoed by a model) is replaced by its placeholder before it goes
to a model, a trace or a log.
"""

import contextlib
import contextvars
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping

log = logging.getLogger(__name__)

PREFIX = "HIDAGENT_SECRET_"
SERVICE = "hidagent"  # security add-generic-password -s hidagent -a NAME -w
KEYCHAIN_NAMES = "HIDAGENT_KEYCHAIN_SECRETS"  # Keychain names to read up front, so they are redacted when typed raw
PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]{0,63})\}")
SHORTEST = 4  # shorter values are not redacted from text: they would match all over it
ACTIONS = ("secret", "keypad")  # actions that enter a secret; never remembered as a routine
LIMIT = "_MAX"  # HIDAGENT_SECRET_<NAME>_MAX: the largest amount a task using {NAME} may state
NUMBER = r"(\d[\d,]*(?:\.\d+)?)"
AMOUNT = re.compile(rf"(?:[¥￥$]|\b(?:rmb|cny|usd))\s*{NUMBER}|{NUMBER}\s*(?:元|块|(?:rmb|yuan|cny|usd|dollars?)\b)",
                    re.IGNORECASE)

_hidden = contextvars.ContextVar("hidagent_hidden", default=False)


def hidden() -> bool:
    """True while a secret goes to the board: commands and pointer positions are not logged."""
    return _hidden.get()


@contextlib.contextmanager
def hush():
    token = _hidden.set(True)
    try:
        yield
    finally:
        _hidden.reset(token)


def names(text: str) -> list[str]:
    """The {NAME} placeholders in the text, in order, each once."""
    return list(dict.fromkeys(PLACEHOLDER.findall(text or "")))


def keypad(digits: str, box: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    """The centres of the keys to press for `digits` on a 3x4 keypad (1 2 3 / 4 5 6 / 7 8 9 / _ 0 ⌫) filling box
    (left, top, right, bottom, any two opposite corners, screen fractions)."""
    spots = []
    for c in digits:
        if c not in "0123456789":
            raise ValueError("a digit keypad takes digits only")
        spots.append(keypad_key(10 if c == "0" else int(c) - 1, box))
    return spots


def keypad_key(i: int, box: tuple[float, float, float, float]) -> tuple[float, float]:
    """The centre of key i (0-11, row by row: 9 is the blank left of 0, 11 delete) of a 3x4 keypad filling box."""
    x1, x2 = sorted(box[0::2])
    y1, y2 = sorted(box[1::2])
    return x1 + (i % 3 + 0.5) * (x2 - x1) / 3, y1 + (i // 3 + 0.5) * (y2 - y1) / 4


def amounts(text: str) -> list[float]:
    """The money amounts the text states with a currency sign or word (¥20, 20元, 20 yuan, $5), in order."""
    return [float((m.group(1) or m.group(2)).replace(",", "")) for m in AMOUNT.finditer(text or "")]


def _keychain(name: str) -> str | None:
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout.rstrip("\n") or None) if r.returncode == 0 else None


class Vault:
    def __init__(self, env: Mapping[str, str] | None = None, keychain: Callable[[str], str | None] | None = None):
        """env: where HIDAGENT_SECRET_* are read (os.environ when None); keychain: a lookup by name, or None."""
        self.env, self.keychain = env, keychain
        self.values: dict[str, str] = {}
        self.limits: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "Vault":
        mac = sys.platform == "darwin" and shutil.which("security")
        return cls(None, _keychain if mac else None)

    def load(self, task: str = "") -> None:
        """Read the stored secrets afresh: from the Keychain the ones the task names or HIDAGENT_KEYCHAIN_SECRETS
        lists, then the environment's for any name the Keychain does not hold."""
        env = os.environ if self.env is None else self.env
        values = {}
        if self.keychain:
            listed = [n.strip() for n in env.get(KEYCHAIN_NAMES, "").split(",") if n.strip()]
            for name in dict.fromkeys(names(task) + listed):
                if value := self.keychain(name):
                    values[name] = value
        limits = {}
        for key, value in env.items():
            if not key.startswith(PREFIX) or not value:
                continue
            name = key[len(PREFIX):]
            if name.endswith(LIMIT):
                try:
                    limits[name[:-len(LIMIT)]] = float(value.replace(",", ""))
                except ValueError:  # an unreadable cap still caps: nothing goes through until it is fixed
                    log.warning("%s is not a number: tasks using {%s} are refused", key, name[:-len(LIMIT)])
                    limits[name[:-len(LIMIT)]] = 0.0
            else:
                values.setdefault(name, value)
        for name, value in values.items():
            if len(value) < SHORTEST:
                log.warning("secret %s is shorter than %d characters: it is not redacted from text", name, SHORTEST)
        self.values, self.limits = values, limits

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def over_limit(self, task: str) -> str | None:
        """Why the task may not use its secrets, when one has a _MAX cap: the task must state its amount, and
        no amount it states may be above the cap."""
        for name in names(task):
            if (cap := self.limits.get(name)) is None:
                continue
            stated = amounts(task)
            if not stated:
                return (f"{{{name}}} is capped at {cap:g} ({PREFIX}{name}{LIMIT}): say the amount in the task, "
                        "like ¥20 or 20元")
            if max(stated) > cap:
                return f"{max(stated):g} is above the {cap:g} cap on {{{name}}} ({PREFIX}{name}{LIMIT})"
        return None

    def redact(self, text: str) -> str:
        """The text with every stored value replaced by its {NAME}, longest first."""
        if not text or not isinstance(text, str):
            return text
        for name, value in sorted(self.values.items(), key=lambda kv: -len(kv[1])):
            if len(value) >= SHORTEST:
                text = text.replace(value, "{" + name + "}")
        return text

    def scrub(self, obj):
        """redact() through every string in nested dicts, lists and tuples."""
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self.scrub(v) for v in obj)
        return obj

    def guard_logs(self) -> None:
        """Redact log lines on every handler there is now (root's and uvicorn's), a backstop behind hush()."""
        loggers = [logging.getLogger(), *(lg for lg in logging.root.manager.loggerDict.values()
                                          if isinstance(lg, logging.Logger))]
        for handler in {h for lg in loggers for h in lg.handlers}:
            if not any(isinstance(f, RedactFilter) for f in handler.filters):
                handler.addFilter(RedactFilter(self))


class RedactFilter(logging.Filter):
    def __init__(self, vault: Vault):
        super().__init__()
        self.vault = vault

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if (clean := self.vault.redact(message)) != message:
            record.msg, record.args = clean, None
        return True
