"""Text the board's US keyboard cannot type: Chinese, entered by pasting or as pinyin.

The board types ASCII only. For anything else the server either puts the text on this computer's clipboard and
pastes it on the phone (macOS Universal Clipboard, same Apple ID and Handoff on both), or types its pinyin and
picks the first candidate with space, which needs the iOS Chinese Pinyin keyboard to be the active one.
"""

import asyncio
import logging
import os
import shutil

from pypinyin import lazy_pinyin

log = logging.getLogger(__name__)

# Full-width punctuation as the pinyin keyboard makes it from the ASCII key.
PUNCTUATION = str.maketrans({
    "，": ",", "。": ".", "？": "?", "！": "!", "：": ":", "；": ";", "、": "\\", "（": "(", "）": ")",
    "“": '"', "”": '"', "‘": "'", "’": "'", "《": "<", "》": ">", "【": "[", "】": "]", "…": "^", "　": " ",
})


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

    for ch in text.translate(PUNCTUATION):
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


def can_copy() -> bool:
    """Pasting is on by default where Universal Clipboard can carry the text (macOS); HIDAGENT_PASTE=0 turns it off."""
    setting = os.environ.get("HIDAGENT_PASTE", "")
    return setting != "0" and (setting == "1" or shutil.which("pbcopy") is not None)


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
