"""Text the board's US keyboard cannot type: Chinese, entered by pasting or as pinyin.

The board types ASCII only. For anything else the server puts the text on this computer's clipboard and pastes it on
the phone (macOS Universal Clipboard, same Apple ID and Handoff on both), or copies it in Safari (relay.py), or as a
last resort types its pinyin and picks the first candidate with space, which needs the iOS Chinese Pinyin keyboard.
"""

import asyncio
import functools
import logging
import os
import re
import shutil
import subprocess

from pypinyin import lazy_pinyin

log = logging.getLogger(__name__)

# Full-width punctuation as the pinyin keyboard makes it from the ASCII key.
PUNCTUATION = str.maketrans({
    "，": ",", "。": ".", "？": "?", "！": "!", "：": ":", "；": ";", "、": "\\", "（": "(", "）": ")",
    "“": '"', "”": '"', "‘": "'", "’": "'", "《": "<", "》": ">", "【": "[", "】": "]", "…": "^", "　": " ",
})


# Signs with no key on the pinyin keyboard, said in words so they are not dropped: 18–32°C is typed as 18到32度.
RANGE = re.compile(r"(?<=\d)\s*[–—~～]\s*(?=[-−]?\d)")
DEGREES = re.compile(r"°\s*[CcFf]?|℃|℉")
DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "～": "~"})


def spoken(text: str) -> str:
    return DEGREES.sub("度", RANGE.sub("到", text)).translate(DASHES)


def _hanzi(ch: str) -> bool:
    return "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff"


def pinyin_lines(text: str) -> list[str]:
    """Protocol lines that enter `text` through the pinyin keyboard.

    Each run of hanzi is typed as one pinyin string (the keyboard matches whole words better than single
    characters) and committed with space; everything else is typed as is. Characters with no key are dropped.
    """
    lines, run, hanzi = [], "", False

    def flush():
        if run:
            lines.append(f"type {''.join(lazy_pinyin(run))}" if hanzi else f"type {run}")
            if hanzi:
                lines.append("key space")

    for ch in spoken(text).translate(PUNCTUATION):
        is_hanzi = _hanzi(ch)
        if not is_hanzi and not ch.isascii():
            log.warning("no key for %r, skipped", ch)
            continue
        if is_hanzi != hanzi:
            flush()
            run, hanzi = "", is_hanzi
        run += ch
    flush()
    return lines


@functools.cache
def _icloud() -> bool:
    """This Mac is signed in to iCloud, which Universal Clipboard needs."""
    try:
        out = subprocess.run(["defaults", "read", "MobileMeAccounts", "Accounts"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "AccountID" in out


def can_copy() -> bool:
    """Pasting is on by default where Universal Clipboard can carry the text: macOS signed in to iCloud.
    HIDAGENT_PASTE=1 turns it on, 0 off.

    Not a guess to check with a paste: without Universal Clipboard the phone pastes what its own clipboard holds,
    often text the relay left there, and that changes the screen just like the right text."""
    setting = os.environ.get("HIDAGENT_PASTE", "")
    if setting in ("0", "1"):
        return setting == "1"
    return shutil.which("pbcopy") is not None and _icloud()


async def copy(text: str) -> bool:
    """Put `text` on this computer's clipboard."""
    if not can_copy():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "pbcopy", stdin=asyncio.subprocess.PIPE, env={**os.environ, "LANG": "en_US.UTF-8"})
        await proc.communicate(text.encode("utf-8"))
    except OSError as e:
        log.warning("clipboard copy failed: %s", e)
        return False
    return proc.returncode == 0
