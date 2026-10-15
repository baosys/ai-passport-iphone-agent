import asyncio
import json

import pytest

import hidagent.phone as phone_module
from hidagent import textinput, vision
from hidagent.phone import EDGE_PUSH, Phone, wheel_steps


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


def test_open_app_adds_no_space_after_a_chinese_name(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_module, "HOME_SETTLE", 0)
    monkeypatch.setattr(phone_module, "SEARCH_ANIMATION", 0)
    monkeypatch.setenv("HIDAGENT_PASTE", "0")
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.open_app("小红书"))
    assert device.sent[4:] == ["key cmd+a", "type xiaohongshu", "key space", "key enter"]


def test_back_swipe_starts_pinned_on_the_left_edge(tmp_path):
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.go_back())
    press = device.sent.index("press")
    assert device.sent[press - 1] == f"move {-round(EDGE_PUSH * 470)} 0"
    (dx, dy), = moves(device.sent[press:])
    assert dx == round(0.6 * 470) and dy == 0
    assert device.sent[-1] == "release"


def test_the_board_scrolls_smoothly_in_one_command_without_pressing(tmp_path):
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.wheel(0.5, 0.5, 0.2))
    assert "press" not in device.sent
    assert device.sent[-1] == "wheel 50 428"
    asyncio.run(phone.wheel(0.5, 0.5, -3))  # never more than WHEEL_MOST at once
    assert device.sent[-1] == "wheel -112 650"


def test_older_firmware_scrolls_report_by_report(tmp_path):
    phone, device = make_phone(tmp_path)

    async def send(line):
        if line.startswith("wheel"):
            raise phone_module.DeviceError("err unknown command")
        device.sent.append(line)
        return "ok"

    device.send = send
    asyncio.run(phone.wheel(0.5, 0.5, -0.06))  # 15 units
    asyncio.run(phone.wheel(0.5, 0.5, -0.06))  # not asked again
    scrolls = [line for line in device.sent if line.startswith("scroll")]
    assert scrolls[:7] == ["scroll -1 0", "scroll -2 0", "scroll -3 0", "scroll -3 0", "scroll -3 0", "scroll -2 0",
                           "scroll -1 0"]
    assert sum(int(line.split()[1]) for line in scrolls) == -30


@pytest.mark.parametrize("units", range(1, 120))
def test_wheel_steps_add_up_and_stay_gentle(units):
    steps = wheel_steps(units)
    assert sum(steps) == units and max(steps) <= phone_module.WHEEL_STEP
    assert all(abs(a - b) <= 1 for a, b in zip(steps, steps[1:], strict=False))


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


def test_open_app_leaves_a_widget_page_that_ignores_cmd_space(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_module, "HOME_SETTLE", 0)
    seen = iter([False, False, True])
    monkeypatch.setattr(vision, "changed", lambda *a, **k: next(seen))
    phone, device = make_phone(tmp_path)
    phone.screen = FakeScreen()
    swipes = []

    async def swipe(*args, **kwargs):
        swipes.append(args)

    phone.swipe = swipe
    asyncio.run(phone.open_app("weixin"))
    assert swipes == [(0.9, 0.75, 0.1, 0.75)]
    assert device.sent[3:] == ["key cmd+space"] * 3 + ["key cmd+a", "type weixin", "key space", "key enter"]


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
    asyncio.run(phone.type("郭兴华"))  # no clipboard reached the phone a moment ago: do not wait for it again
    assert device.sent[3:] == ["type guoxinghua", "key space"]


def test_signs_without_a_pinyin_key_are_typed_in_words(tmp_path, monkeypatch):
    monkeypatch.setenv("HIDAGENT_PASTE", "0")
    phone, device = make_phone(tmp_path)
    asyncio.run(phone.type("晴 18–32°，2026-10-15"))
    assert device.sent == ["type qing", "key space", "type  18", "type dao", "key space", "type 32", "type du",
                           "key space", "type ,2026-10-15"]


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


class FakeRelay:
    def __init__(self, shortcut=False):
        self.loaded, self.copied, self.fetched = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.shortcut, self.clip = shortcut, ""

    def hold(self, text):
        self.clip = text
        if self.shortcut:  # the phone's Shortcut fetches it
            self.fetched.set()

    def offer(self, text):
        self.text = text
        self.loaded.set()
        self.copied.set()
        return "10.0.0.2:8890/123456"


def relay_phone(tmp_path, monkeypatch, pasted):
    monkeypatch.setenv("HIDAGENT_PASTE", "0")
    monkeypatch.setattr(phone_module, "SHORTCUT_FETCH", 0)
    monkeypatch.setattr(vision, "changed", lambda *a, **k: next(pasted))
    phone, device = make_phone(tmp_path)
    phone.screen, phone.relay = FakeScreen(), FakeRelay()
    calls = []

    async def open_app(name):
        calls.append(f"open {name}")

    async def tap(x, y, aim=True):
        calls.append(f"tap {x} {y}" + ("" if aim else " blind"))

    async def previous_app():
        calls.append("previous app")

    phone.open_app, phone.tap, phone.previous_app = open_app, tap, previous_app
    return phone, device, calls


def test_chinese_is_copied_in_safari_and_pasted_back_in_the_app(tmp_path, monkeypatch):
    phone, device, calls = relay_phone(tmp_path, monkeypatch, iter([True]))
    asyncio.run(phone.type("HSBC Pulse信用卡如何还款"))
    assert phone.relay.text == "HSBC Pulse信用卡如何还款"
    assert calls == ["open Safari", "tap 0.5 0.45 blind", "previous app"]  # no field tapped before: none to tap again
    assert device.sent == ["button 3", "key cmd+l", "type 10.0.0.2:8890/123456", "key enter", "key cmd+v"]


def test_the_field_is_tapped_again_after_the_round_trip(tmp_path, monkeypatch):
    phone, device, calls = relay_phone(tmp_path, monkeypatch, iter([False, True]))
    phone.last_tap = (0.4, 0.1)
    asyncio.run(phone.type("你好"))
    assert calls[-1] == "tap 0.4 0.1" and device.sent[-2:] == ["key cmd+v", "key cmd+v"]


def test_the_paste_shortcut_copies_without_leaving_the_app(tmp_path, monkeypatch):
    phone, device, calls = relay_phone(tmp_path, monkeypatch, iter([True]))
    phone.relay.shortcut = True
    monkeypatch.setattr(phone_module, "SHORTCUT_FETCH", 1)
    monkeypatch.setattr(phone_module, "SHORTCUT_COPY", 0)
    asyncio.run(phone.type("华盛顿未来一周"))
    assert phone.relay.clip == "华盛顿未来一周" and calls == []
    assert device.sent == ["button 3", "key cmd+v"]


def test_a_shortcut_that_never_runs_is_not_tried_again_soon(tmp_path, monkeypatch):
    phone, device, calls = relay_phone(tmp_path, monkeypatch, iter([True, True]))
    asyncio.run(phone.type("你好"))
    assert device.sent[0] == "button 3" and calls[0] == "open Safari" and phone.relay.clip == ""
    device.sent.clear()
    asyncio.run(phone.type("你好"))
    assert "button 3" not in device.sent


def test_older_firmware_without_the_button_command_falls_back_to_the_relay(tmp_path, monkeypatch):
    phone, device, calls = relay_phone(tmp_path, monkeypatch, iter([True]))

    async def send(line):
        if line.startswith("button"):
            raise phone_module.DeviceError("err unknown command")
        device.sent.append(line)

    device.send = send
    asyncio.run(phone.type("你好"))
    assert calls[0] == "open Safari" and phone._no_shortcut == float("inf")


def test_no_universal_clipboard_without_icloud(monkeypatch):
    monkeypatch.delenv("HIDAGENT_PASTE", raising=False)
    monkeypatch.setattr(textinput, "_icloud", lambda: False)
    assert not textinput.can_copy()
    monkeypatch.setenv("HIDAGENT_PASTE", "1")
    assert textinput.can_copy()
