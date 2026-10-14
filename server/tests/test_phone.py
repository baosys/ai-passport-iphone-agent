import asyncio
import json

import hidagent.phone as phone_module
from hidagent import textinput, vision
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


def test_home_is_cmd_h_then_esc_to_close_a_search_left_open(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_module, "HOME_SETTLE", 0)
    monkeypatch.setattr(phone_module, "SEARCH_ANIMATION", 0)
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.go_home())
    assert device.sent == ["key cmd+h", "key esc", "key esc"]


def test_open_app_searches_from_home_and_takes_the_input_method_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_module, "HOME_SETTLE", 0)
    monkeypatch.setattr(phone_module, "SEARCH_ANIMATION", 0)
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.open_app("Settings"))
    assert device.sent == ["key cmd+h", "key esc", "key esc", "key cmd+space", "key cmd+a", "type Settings",
                           "key space", "key enter"]


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
        asyncio.run(phone.go_back())
    except RuntimeError:
        pass
    assert device.sent[-1] == "release"


class FakeScreen:
    """A live mirror whose frame is whatever `vision.changed` is told to report."""

    live, frame = True, b"frame"

    async def settled(self, **_):
        pass

    async def fresh(self, since, timeout=0.8):
        return b"after"


def test_open_app_presses_cmd_space_again_when_search_did_not_open(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_module, "HOME_SETTLE", 0)
    seen = iter([False, True])  # iOS ignores the first cmd+space while search is still closing
    monkeypatch.setattr(vision, "changed", lambda *a, **k: next(seen))
    phone, device = make_phone(tmp_path)
    phone.screen = FakeScreen()
    asyncio.run(phone.open_app("weixin"))
    assert device.sent[3:] == ["key cmd+space", "key cmd+space", "key cmd+a", "type weixin", "key space", "key enter"]


def test_ascii_text_is_typed_directly(tmp_path):
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.type("hello"))
    assert device.sent == ["type hello"]


def test_chinese_is_typed_as_pinyin_without_a_clipboard(tmp_path, monkeypatch):
    monkeypatch.setenv("HIDAGENT_PASTE", "0")
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.type("你好，世界"))
    assert device.sent == ["type nihao", "key space", "type ,", "type shijie", "key space"]


def test_chinese_is_pasted_when_the_clipboard_works(tmp_path, monkeypatch):
    phone, device = make_phone(tmp_path)
    phone.screen = FakeScreen()
    copied = []

    async def copy(text):
        copied.append(text)
        return True

    monkeypatch.setattr(textinput, "copy", copy)
    monkeypatch.setattr(phone_module, "PASTE_SYNC", 0)
    monkeypatch.setattr(vision, "changed", lambda *a, **k: True)
    asyncio.run(phone.type("郭兴华"))
    assert copied == ["郭兴华"] and device.sent == ["key cmd+v"]


def test_pinyin_follows_a_paste_that_changed_nothing(tmp_path, monkeypatch):
    phone, device = make_phone(tmp_path)
    phone.screen = FakeScreen()

    async def copy(text):
        return True

    monkeypatch.setattr(textinput, "copy", copy)
    monkeypatch.setattr(phone_module, "PASTE_SYNC", 0)
    monkeypatch.setattr(vision, "changed", lambda *a, **k: False)
    asyncio.run(phone.type("郭兴华"))
    assert device.sent == ["key cmd+v", "type guoxinghua", "key space"]


def test_tap_finds_the_pointer_and_fixes_the_miss_by_default(tmp_path, monkeypatch):
    phone, device = make_phone(tmp_path, w=650, h=1430)
    phone.screen = FakeScreen()
    monkeypatch.setattr(phone_module, "POINTER_LAG", 0)
    monkeypatch.setattr(vision, "pointer", lambda *a, **k: (0.527, 0.885))  # landed low and right
    asyncio.run(phone.tap(0.5, 0.831))
    assert device.sent[-1] == "click"
    *_, fix = moves(device.sent)
    assert fix == (round((0.5 - 0.527) * 650), round((0.831 - 0.885) * 1430))


def test_tap_without_a_live_mirror_does_not_wait_for_the_pointer(tmp_path):
    phone, device = make_phone(tmp_path)
    phone.screen = FakeScreen()
    phone.screen.live = False
    asyncio.run(phone.tap(0.5, 0.5))
    assert device.sent == ["release", "home", f"move {round(0.5 * 470)} {round(0.5 * 1024)}", "click"]


def test_a_tap_near_the_bottom_goes_down_to_the_edge_then_a_short_way_up(tmp_path):
    phone, device = make_phone(tmp_path, w=650, h=1430)
    asyncio.run(phone.tap(0.874, 0.943, aim=False))
    assert moves(device.sent) == [(round(0.874 * 650), round(0.8 * 1430)), (0, round(EDGE_PUSH * 1430)),
                                  (0, -round((0.995 - 0.943) * 1430))]
    asyncio.run(phone.tap(0.5, 0.5, aim=False))
    assert device.sent[-3:] == ["home", f"move {round(0.5 * 650)} {round(0.5 * 1430)}", "click"]  # re-homed
