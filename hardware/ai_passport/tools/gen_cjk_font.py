#!/usr/bin/env python3
"""Generate assets/fonts/agent_cjk_14.c: the Chinese glyphs for the 14 px UI text.

The UI keeps Montserrat 14 for Latin text and LVGL symbols and falls back to this font for everything
else (see agent_ui.c). It covers every character of GB2312, the character set of simplified Chinese:
6763 hanzi, full-width punctuation and forms, plus a few common typographic marks outside GB2312.

    python3 tools/gen_cjk_font.py /path/to/SourceHanSansSC-Regular.otf

Source font: Source Han Sans SC Regular (https://github.com/adobe-fonts/source-han-sans, SIL Open Font
License 1.1). Converter: lv_font_conv 1.5.3, run through npx (needs Node.js).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "fonts" / "agent_cjk_14.c"
CONVERTER = "lv_font_conv@1.5.3"
# Common in Chinese text but outside GB2312: en/em dash, curly quotes, ellipsis, middle dot, euro sign.
EXTRA = [0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x00B7, 0x20AC]


def gb2312_codepoints() -> list[int]:
    """Every non-ASCII character GB2312 defines (rows 0xA1-0xF7, cells 0xA1-0xFE)."""
    points = set()
    for hi in range(0xA1, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                points.add(ord(bytes([hi, lo]).decode("gb2312")))
            except UnicodeDecodeError:
                pass  # unassigned cell
    return sorted(points)


def ranges(points: list[int]) -> str:
    """Compress sorted code points into lv_font_conv's "a-b,c" range syntax."""
    out, start, prev = [], points[0], points[0]
    for p in points[1:] + [None]:
        if p is not None and p == prev + 1:
            prev = p
            continue
        out.append(f"0x{start:X}" if start == prev else f"0x{start:X}-0x{prev:X}")
        if p is not None:
            start = prev = p
    return ",".join(out)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    font = Path(sys.argv[1]).resolve()
    points = sorted(set(gb2312_codepoints()) | set(EXTRA))
    print(f"{len(points)} code points", file=sys.stderr)
    subprocess.run(
        ["npx", "--yes", CONVERTER,
         "--font", font.name, "--range", ranges(points),
         "--size", "14", "--bpp", "4", "--format", "lvgl", "--no-compress",
         "--lv-font-name", "agent_cjk_14", "--lv-include", "lvgl.h",
         "--output", str(OUTPUT)],
        cwd=font.parent,  # the generated header records --font: keep local paths out of it
        check=True)
    # Same for --output, which has to be absolute because of cwd.
    text = OUTPUT.read_text(encoding="utf-8").replace(str(OUTPUT), str(OUTPUT.relative_to(ROOT)), 1)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
