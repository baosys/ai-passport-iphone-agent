import asyncio
import json

from hidagent.phone import EDGE_PUSH, Phone


class FakeDevice:
    """Records the protocol lines the phone sends; every command succeeds."""

    links = 1

    def __init__(self):
        self.sent = []

    async def send(self, line: str) -> str:
        self.sent.append(line)
        return "ok"


def make_phone(tmp_path, w=470, h=1024):
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"w": w, "h": h, "seen": {"x": [], "y": []}}))
    device = FakeDevice()
    return Phone(device, cal), device


def moves(lines):
    return [tuple(map(int, line.split()[1:])) for line in lines if line.startswith("move")]


def test_home_swipe_starts_pinned_on_the_bottom_edge(tmp_path):
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.go_home())
    press = device.sent.index("press")
    # Before pressing, the pointer is pushed down past the edge by a quarter of the screen height.
    assert device.sent[press - 1] == f"move 0 {round(EDGE_PUSH * 1024)}"
    # Then one upward move from the bottom edge (y = 1.0) to y = 0.6, and the button is let go.
    (dx, dy), = moves(device.sent[press:])
    assert dx == 0 and dy == round(0.6 * 1024) - 1024
    assert device.sent[-1] == "release"


def test_back_swipe_starts_pinned_on_the_left_edge(tmp_path):
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.go_back())
    press = device.sent.index("press")
    assert device.sent[press - 1] == f"move {-round(EDGE_PUSH * 470)} 0"
    (dx, dy), = moves(device.sent[press:])
    assert dx == round(0.6 * 470) and dy == 0
    assert device.sent[-1] == "release"


def test_release_follows_press_even_when_the_swipe_fails(tmp_path):
    phone, device = make_phone(tmp_path)

    async def fail_on_move_after_press(line):
        device.sent.append(line)
        if line.startswith("move") and "press" in device.sent:
            raise RuntimeError("link lost")
        return "ok"

    device.send = fail_on_move_after_press
    try:
        asyncio.run(phone.go_home())
    except RuntimeError:
        pass
    assert device.sent[-1] == "release"
