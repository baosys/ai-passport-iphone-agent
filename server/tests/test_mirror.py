import asyncio
import json

import pytest

import hidagent.mirror as mirror_module
from hidagent.mirror import BACKOFF, CLOSE, Mirror, parse_spot
from hidagent.phone import EDGE_PUSH, Phone

W, H = 470, 1024
SPOTS = {"page": [0.94, 0.44], "button": [0.4, 0.42], "receiver": [0.41, 0.44]}
# a 300x650 baseline JPEG header: enough for frame_size()
FRAME = b"\xff\xd8\xff\xc0\x00\x11\x08" + (650).to_bytes(2, "big") + (300).to_bytes(2, "big") + b"\x03" + b"\xff\xd9"


class FakeDevice:
    """Records the protocol lines; `on_click` lets a test react to taps (mirroring coming back)."""

    links, connected, last = 1, True, 0.0

    def __init__(self):
        self.sent = []
        self.on_click = None

    async def send(self, line: str) -> str:
        self.sent.append(line)
        if line == "click" and self.on_click:
            self.on_click(self.clicks())
        return "ok"

    def clicks(self) -> list[tuple[int, int]]:
        """Where the pointer was at each click, following home and relative moves; iOS stops it at the edges."""
        x = y = 0
        out = []
        for line in self.sent:
            if line == "home":
                x = y = 0
            elif line.startswith("move"):
                dx, dy = map(int, line.split()[1:])
                x, y = min(max(x + dx, 0), W), min(max(y + dy, 0), H)
            elif line == "click":
                out.append((x, y))
        return out


class FakeScreen:
    def __init__(self, live=False):
        self.mirroring, self.frame, self.updated = live, FRAME, 0.0
        self.restarts = 0

    def restart(self):
        self.restarts += 1
        self.mirroring = False

    @property
    def live(self):
        return self.mirroring and self.frame is not None

    async def settled(self, **_):
        pass


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.asked = list(replies), []

    async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
        self.asked.append(text)
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    for name in ("POLL", "GRACE", "WAKE", "STEP", "POKE"):
        monkeypatch.setattr(mirror_module, name, 0)
    monkeypatch.setattr(mirror_module, "CONNECT_WAIT", 0.05)
    monkeypatch.setattr(mirror_module, "BACKOFF", (0, 0, 0))
    monkeypatch.setattr("hidagent.phone.TAP_GAP", 0)


def make(tmp_path, live=False, spots=SPOTS, llm=None, busy=lambda: False):
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"w": W, "h": H, "seen": {"x": [], "y": []}}))
    if spots:
        (tmp_path / "mirror.json").write_text(json.dumps({"auto": True, "spots": spots}))
    device, screen = FakeDevice(), FakeScreen(live)
    phone = Phone(device, cal, screen)
    return Mirror(phone, screen, llm or FakeLLM([]), tmp_path / "mirror.json", "iPhone Agent", busy), device, screen


def px(spot):
    return round(spot[0] * W), round(spot[1] * H)


def test_reconnect_opens_control_center_and_replays_the_learned_taps(tmp_path):
    mirror, device, screen = make(tmp_path)
    device.on_click = lambda clicks: setattr(screen, "mirroring", len(clicks) >= 3)  # the receiver tap
    assert asyncio.run(mirror.reconnect()) is True
    assert device.sent[:2] == ["release", "home"]  # start with no button held, from the corner
    press = device.sent.index("press")
    assert device.sent[press - 1] == f"move 0 {-round(EDGE_PUSH * H)}"  # pinned past the top edge, then down
    assert device.clicks() == [px(SPOTS["page"]), px(SPOTS["button"]), px(SPOTS["receiver"]), px(CLOSE), px(CLOSE)]
    assert mirror.state == "idle"


def test_reconnect_never_taps_the_receiver_once_mirroring_is_back(tmp_path):
    mirror, device, screen = make(tmp_path)
    device.on_click = lambda clicks: setattr(screen, "mirroring", len(clicks) >= 2)  # e.g. it came back by itself
    assert asyncio.run(mirror.reconnect()) is True
    assert device.clicks() == [px(SPOTS["page"]), px(SPOTS["button"]), px(CLOSE), px(CLOSE)]  # a tap would stop it


def test_reconnect_reports_a_miss_and_still_closes_control_center(tmp_path):
    mirror, device, _ = make(tmp_path)
    assert asyncio.run(mirror.reconnect()) is False
    assert device.clicks()[-2:] == [px(CLOSE), px(CLOSE)]


def test_reconnect_skips_the_page_tap_when_control_center_has_one_page(tmp_path):
    mirror, device, _ = make(tmp_path, spots={**SPOTS, "page": None})
    asyncio.run(mirror.reconnect())
    assert device.clicks()[:2] == [px(SPOTS["button"]), px(SPOTS["receiver"])]


def test_reconnect_needs_learned_taps(tmp_path):
    mirror, _, _ = make(tmp_path, spots=None)
    with pytest.raises(RuntimeError, match="learn"):
        asyncio.run(mirror.reconnect())


def run_watch(mirror, until, timeout=2.0):
    async def go():
        task = asyncio.create_task(mirror.watch())
        try:
            for _ in range(int(timeout / 0.01)):
                if until():
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())


def test_watch_retries_with_backoff_then_gives_up(tmp_path):
    mirror, device, _ = make(tmp_path)
    run_watch(mirror, lambda: mirror.state == "gave up")
    assert mirror.state == "gave up"
    assert mirror.attempts == len(mirror_module.BACKOFF) != len(BACKOFF)  # the patched schedule was followed
    assert device.sent.count("press") == mirror.attempts  # one Control Center swipe per attempt


def test_watch_stops_once_mirroring_is_back(tmp_path):
    mirror, device, screen = make(tmp_path)
    device.on_click = lambda clicks: setattr(screen, "mirroring", len(clicks) >= 3)
    run_watch(mirror, lambda: screen.mirroring and mirror.attempts == 0 and not mirror.running)
    assert device.sent.count("press") == 1
    assert mirror.attempts == 0


def test_watch_nudges_for_a_frame_when_the_phone_mirrors_a_still_screen(tmp_path):
    mirror, device, screen = make(tmp_path, live=True)
    screen.frame = None  # the server restarted: the phone still mirrors but sends nothing until the screen changes
    run_watch(mirror, lambda: "move 1 0" in device.sent)
    assert device.sent[:2] == ["move 1 0", "move -1 0"] and "press" not in device.sent
    assert mirror.attempts == 0 and screen.restarts == 0


def test_watch_restarts_a_receiver_that_sends_no_video(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror_module, "STALL", 0.05)
    mirror, device, screen = make(tmp_path, live=True)
    screen.frame = None  # connected, nudged, and still nothing
    run_watch(mirror, lambda: screen.restarts > 0)
    assert screen.restarts == 1 and "move 1 0" in device.sent


def test_watch_restarts_a_receiver_whose_video_froze(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror_module, "FROZEN", 20)
    mirror, device, screen = make(tmp_path, live=True, busy=lambda: True)  # even during a task: no taps needed
    screen.updated, device.last = 100.0, 119.0  # commands kept coming after the last frame, not long enough yet
    run_watch(mirror, lambda: False, timeout=0.1)
    assert screen.restarts == 0
    device.last = 121.0
    run_watch(mirror, lambda: screen.restarts > 0)
    assert screen.restarts == 1 and device.sent == []


def test_no_restart_without_a_nudge(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror_module, "STALL", 0.05)
    mirror, device, screen = make(tmp_path, live=True, busy=lambda: True)  # an agent task: hands off
    screen.frame = None
    run_watch(mirror, lambda: False, timeout=0.3)
    assert screen.restarts == 0 and device.sent == []


@pytest.mark.parametrize("why", ["agent running", "auto off", "not learned", "board gone"])
def test_watch_leaves_the_phone_alone(tmp_path, why):
    mirror, device, _ = make(tmp_path, spots=None if why == "not learned" else SPOTS,
                             busy=lambda: why == "agent running")
    if why == "auto off":
        mirror.set_auto(False)
    if why == "board gone":
        device.connected = False
    run_watch(mirror, lambda: False, timeout=0.2)
    assert device.sent == [] and mirror.attempts == 0


def test_stop_cancels_a_reconnect_in_flight(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror_module, "CONNECT_WAIT", 30)
    mirror, device, _ = make(tmp_path)

    async def go():
        job = asyncio.create_task(mirror.run(mirror.reconnect()))
        while "click" not in device.sent:
            await asyncio.sleep(0.01)
        await mirror.stop()
        with pytest.raises(RuntimeError, match="stopped"):
            await job
    asyncio.run(go())
    assert not mirror.running and mirror.state == "idle"


def test_run_refuses_while_an_agent_task_runs(tmp_path):
    mirror, device, _ = make(tmp_path, busy=lambda: True)
    with pytest.raises(RuntimeError, match="agent"):
        asyncio.run(mirror.run(mirror.reconnect()))
    assert device.sent == []


def test_learn_finds_each_tap_on_the_live_mirror_and_saves_them(tmp_path, monkeypatch):
    async def no_aim(self, x, y):
        pass
    monkeypatch.setattr(Phone, "_aim", no_aim)
    llm = FakeLLM(["282 286", "120 273", "123 286"])  # pixels of the 300x650 frame
    mirror, device, _ = make(tmp_path, live=True, spots=None, llm=llm)
    spots = asyncio.run(mirror.learn())
    assert spots == {"page": [0.94, 0.44], "button": [0.4, 0.42], "receiver": [0.41, 0.44]}
    assert "300x650" in llm.asked[0] and "iPhone Agent" in llm.asked[2]
    assert device.clicks()[-2:] == [px(CLOSE), px(CLOSE)]  # closed again without tapping the receiver
    assert len(device.clicks()) == 4
    again, _, _ = make(tmp_path, spots=None)
    assert again.spots == spots and again.learned


def test_learn_needs_a_live_mirror(tmp_path):
    mirror, device, _ = make(tmp_path, live=False, spots=None)
    with pytest.raises(RuntimeError, match="Screen Mirroring"):
        asyncio.run(mirror.learn())
    assert device.sent == []


def test_auto_setting_persists(tmp_path):
    mirror, _, _ = make(tmp_path)
    mirror.set_auto(False)
    again, _, _ = make(tmp_path, spots=None)
    assert again.auto is False and again.spots == SPOTS


def test_parse_spot():
    assert parse_spot("150 325", 300, 650) == [0.5, 0.5]
    assert parse_spot("(30, 65)", 300, 650) == [0.1, 0.1]
    assert parse_spot("none", 300, 650) is None
    assert parse_spot("900 100", 300, 650) is None
    assert parse_spot("?", 300, 650) is None


def test_an_emergency_stop_pauses_reconnecting(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror_module, "CONNECT_WAIT", 30)
    mirror, device, _ = make(tmp_path)

    async def go():
        task = asyncio.create_task(mirror.watch())
        while "click" not in device.sent:
            await asyncio.sleep(0.01)
        await mirror.stop()
        for _ in range(20):
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())
    assert mirror.state == "gave up" and mirror.attempts == 1
    assert device.sent.count("press") == 1
