"""Small image checks on screen frames: did the screen change, and where is the pointer."""

import io

from PIL import Image, ImageChops


def _gray(jpeg: bytes) -> Image.Image:
    return Image.open(io.BytesIO(jpeg)).convert("L")


def changed(before: bytes | None, after: bytes | None, ignore: tuple = (), threshold: float = 1.0,
            spots: int = 6) -> bool:
    """True when the screen content changed, including small things such as a checkmark.

    `ignore` lists screen points (fractions) where the pointer was or went: those spots are masked, so pointer
    moves never count. The status bar (clock) is ignored too. `spots` is how many changed pixels (at half
    resolution) count as a change; raise it to ignore a blinking text cursor.
    """
    if not before or not after:
        return True
    a, b = _gray(before), _gray(after)
    if a.size != b.size:
        return True
    small = ImageChops.difference(a.resize((32, 64), Image.BILINEAR), b.resize((32, 64), Image.BILINEAR)).tobytes()
    if sum(small) / len(small) > threshold:  # a big change: new screen, scrolled list, keyboard
        return True
    w, h = a.size[0] // 2, a.size[1] // 2  # fine pass for small changes, at half resolution
    diff = ImageChops.difference(a.resize((w, h), Image.BILINEAR), b.resize((w, h), Image.BILINEAR)).tobytes()
    masks = [(px * w, py * h) for px, py in ignore]
    hits = 0
    for i, v in enumerate(diff):
        if v > 25:
            x, y = i % w, i // w
            if y > 0.06 * h and all((x - mx) ** 2 + (y - my) ** 2 > (0.05 * w) ** 2 for mx, my in masks):
                hits += 1
    return hits >= spots


def same_screen(a: bytes | None, b: bytes | None, limit: float = 8.0) -> bool:
    """True when two frames show the same screen of the same app: only small things differ (a typed word, the
    cursor). Measured: back in the same chat after a trip to Safari 0.2-2.4, another app 25-120."""
    if not a or not b:
        return True
    x, y = _gray(a), _gray(b)
    if x.size != y.size:
        return False
    small = ImageChops.difference(x.resize((32, 64), Image.BILINEAR), y.resize((32, 64), Image.BILINEAR)).tobytes()
    return sum(small) / len(small) <= limit


def pointer(before: bytes, after: bytes, near: tuple[float, float], shift: float,
            reach: tuple[float, float] = (0.12, 0.08)) -> tuple[float, float] | None:
    """Where the pointer is in `before`, given it moved right by `shift` (fraction of width) in `after`.

    Only the pointer moved between the two frames, so the changed pixels within `reach` of `near` are the
    pointer at both spots; their centroid sits shift/2 to the right of the first one. None if unclear:
    nothing moved, or the change is too big or too spread out to be one small pointer.
    """
    a, b = _gray(before), _gray(after)
    if a.size != b.size:
        return None
    w, h = a.size
    rx, ry = reach
    left, top = max(0, int((near[0] - rx) * w)), max(int(0.05 * h), int((near[1] - ry) * h))  # skip the status bar
    box = (left, top, min(w, int((near[0] + rx) * w)), min(h, int((near[1] + ry) * h)))
    if box[2] <= box[0] or box[3] <= box[1]:  # near is off the screen
        return None
    diff = ImageChops.difference(a.crop(box), b.crop(box)).tobytes()
    bw = box[2] - box[0]
    hits = [(i % bw, i // bw) for i, v in enumerate(diff) if v > 30]
    if not 15 <= len(hits) <= len(diff) // 4:  # nothing moved, or much more than a pointer
        return None
    xs, ys = [x for x, _ in hits], [y for _, y in hits]
    if max(xs) - min(xs) > 0.15 * w or max(ys) - min(ys) > 0.07 * h:  # not one compact spot
        return None
    cx = sum(x for x, _ in hits) / len(hits) + left
    cy = sum(y for _, y in hits) / len(hits) + top
    return cx / w - shift / 2, cy / h


KEYPAD_WIDTH = 500  # frames are measured at this width: the size the mirror sends of a portrait iPhone
DIGIT_CELLS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 10)  # 1-9 row by row, then 0; 9 is the blank key, 11 delete


def _measure(jpeg: bytes) -> Image.Image:
    img = _gray(jpeg)
    if img.size[0] > KEYPAD_WIDTH:
        img = img.resize((KEYPAD_WIDTH, round(img.size[1] * KEYPAD_WIDTH / img.size[0])), Image.BILINEAR)
    return img


def _ink(img: Image.Image, box: tuple[float, float, float, float], contrast: int = 110):
    """Pixels in box that stand out from its background (dark on a light key, light on a dark one):
    (count, centroid x, centroid y), or (0, None, None)."""
    w, h = img.size
    left, top = max(0, int(box[0])), max(0, int(box[1]))
    right, bottom = min(w, int(box[2])), min(h, int(box[3]))
    if right - left < 3 or bottom - top < 3:
        return 0, None, None
    crop = img.crop((left, top, right, bottom))
    hist = crop.histogram()
    half, seen, median = crop.size[0] * crop.size[1] / 2, 0, 0
    while seen + hist[median] < half:  # the background: the median grey level
        seen += hist[median]
        median += 1
    data, cw = crop.tobytes(), crop.size[0]
    hits = [i for i, v in enumerate(data) if abs(v - median) > contrast]
    if not hits:
        return 0, None, None
    return (len(hits), left + sum(i % cw for i in hits) / len(hits) + 0.5,
            top + sum(i // cw for i in hits) / len(hits) + 0.5)


def keypad_grid(jpeg: bytes, box: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    """The 3x4 digit keypad (1 2 3 / 4 5 6 / 7 8 9 / blank 0 delete) near box (screen fractions, any two opposite
    corners), fitted to the keys' glyphs on the screen; None when there is none: then nothing may be tapped.

    Each digit key and the delete key must hold one glyph near its centre and the blank key none. The model's box
    is only a start: a box off by up to about a quarter of a key is moved onto the keys.
    """
    img = _measure(jpeg)
    w, h = img.size
    x1, x2 = sorted((box[0] * w, box[2] * w))
    y1, y2 = sorted((box[1] * h, box[3] * h))
    if x2 - x1 < 0.3 * w or y2 - y1 < 0.1 * h:
        return None
    for _ in range(4):  # move the grid onto the glyphs; a couple of rounds settle it
        cw, ch = (x2 - x1) / 3, (y2 - y1) / 4
        cells = [_ink(img, (x1 + (i % 3) * cw, y1 + (i // 3) * ch, x1 + (i % 3 + 1) * cw, y1 + (i // 3 + 1) * ch))
                 for i in range(12)]
        if any(cells[i][0] == 0 for i in (*DIGIT_CELLS, 11)):
            return None
        cols = [sum(cells[i][1] for i in DIGIT_CELLS if i % 3 == c) / sum(1 for i in DIGIT_CELLS if i % 3 == c)
                for c in range(3)]
        rows = [(r, sum(cells[i][2] for i in DIGIT_CELLS if i // 3 == r) / sum(1 for i in DIGIT_CELLS if i // 3 == r))
                for r in range(4)]
        new_cw = min(1.25 * cw, max(0.8 * cw, (cols[2] - cols[0]) / 2))
        mean_r, mean_y = 1.5, sum(y for _, y in rows) / 4
        slope = sum((r - mean_r) * (y - mean_y) for r, y in rows) / sum((r - mean_r) ** 2 for r, _ in rows)
        new_ch = min(1.25 * ch, max(0.8 * ch, slope))
        nx1, ny1 = cols[1] - 1.5 * new_cw, mean_y - 2 * new_ch
        moved = abs(nx1 - x1) + abs(ny1 - y1) + abs(3 * new_cw - (x2 - x1)) + abs(4 * new_ch - (y2 - y1))
        x1, y1, x2, y2 = nx1, ny1, nx1 + 3 * new_cw, ny1 + 4 * new_ch
        if moved < 1:
            break
    cw, ch = (x2 - x1) / 3, (y2 - y1) / 4
    cells = [_ink(img, (x1 + (i % 3) * cw, y1 + (i // 3) * ch, x1 + (i % 3 + 1) * cw, y1 + (i // 3 + 1) * ch))
             for i in range(12)]
    area = cw * ch
    for i in (*DIGIT_CELLS, 11):
        n, cx, cy = cells[i]
        if not (6 <= n <= 0.25 * area) or abs(cx - (x1 + (i % 3 + 0.5) * cw)) > 0.2 * cw \
                or abs(cy - (y1 + (i // 3 + 0.5) * ch)) > 0.22 * ch:
            return None
    digit_ink = sorted(cells[i][0] for i in DIGIT_CELLS)
    if cells[9][0] > digit_ink[len(digit_ink) // 2] / 2:  # the blank key left of 0 holds nothing
        return None
    return x1 / w, y1 / h, x2 / w, y2 / h


def dots(before: bytes, after: bytes, band: tuple[float, float]) -> int:
    """How many new small dark-or-light spots (password dots) `after` has over `before` between the screen heights
    band (fractions). A changed area too big for a dot counts as many, so a new screen is never a few dots."""
    a, b = _measure(before), _measure(after)
    if a.size != b.size:
        return 99
    w, h = a.size
    top, bottom = max(0, int(band[0] * h)), min(h, int(band[1] * h))
    if bottom - top < 2:
        return 0
    diff = ImageChops.difference(a.crop((0, top, w, bottom)), b.crop((0, top, w, bottom))).tobytes()
    bh = bottom - top
    lit = bytearray(1 if v > 60 else 0 for v in diff)
    scale = (w / KEYPAD_WIDTH) ** 2
    found = 0
    for start in range(len(lit)):
        if not lit[start]:
            continue
        lit[start], stack, size, xs, ys = 0, [start], 0, [], []
        while stack:  # flood fill one spot
            i = stack.pop()
            size += 1
            x, y = i % w, i // w
            xs.append(x)
            ys.append(y)
            for j in (i - 1 if x else -1, i + 1 if x < w - 1 else -1, i - w if y else -1, i + w if y < bh - 1 else -1):
                if j >= 0 and lit[j]:
                    lit[j] = 0
                    stack.append(j)
        if size < 12 * scale:
            continue  # noise
        span = max(max(xs) - min(xs), max(ys) - min(ys)) + 1
        found += 1 if size <= 600 * scale and span <= 30 * (w / KEYPAD_WIDTH) else 99
    return found
