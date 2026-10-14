import asyncio
import io
import json

import httpx
from PIL import Image, ImageDraw
from test_routines import Model, Phone, Screen, _frame, _run

from hidagent.agent import Agent
from hidagent.debug import Trace
from hidagent.jev import Jev, Triage
from hidagent.routines import Routines


class FakeJev(Jev):
    def __init__(self, triage: Triage | None = None, box=None):
        super().__init__("key")
        self.answer, self.box, self.asked = triage, box, []

    async def triage(self, task, clauses, remembered):
        self.asked.append(("triage", task, clauses, remembered))
        return self.answer

    async def find(self, screen, look):
        self.asked.append(("find",))
        return self.box


def _moved_frame() -> bytes:
    """The button of _frame(), 40 pixels lower."""
    img = Image.new("RGB", (100, 200), "black")
    ImageDraw.Draw(img).rectangle((40, 130, 60, 150), fill="white")
    out = io.BytesIO()
    img.save(out, "JPEG")
    return out.getvalue()


def _agent(routines, screen, actor, planner, jev) -> Agent:
    return Agent(Phone(screen), screen, actor, planner, Trace(None), routines=routines, jev=jev)


def _learned(tmp_path) -> Routines:
    """Routines that know "点中间的按钮" from a run of the models."""
    routines = Routines(tmp_path / "routines.json")
    _run(_agent(routines, Screen(_frame()), Model("THINK: tap\nDO: tap 50 100"), Model("1. Tap"), Jev(None)),
         "点中间的按钮")
    assert routines.find("点中间的按钮")["steps"][0]["look"]
    return routines


def test_a_reworded_task_replays_the_routine_jev_matches(tmp_path):
    routines = _learned(tmp_path)
    jev = FakeJev(Triage(["other"], "点中间的按钮", False))
    actor, planner = Model(), Model()
    agent = _run(_agent(routines, Screen(_frame()), actor, planner, jev), "按一下中间那个按钮")
    assert actor.calls == planner.calls == 0
    assert agent.phone.taps == [(0.5, 0.5)]
    assert jev.asked[0] == ("triage", "按一下中间那个按钮", ["按一下中间那个按钮"], ["点中间的按钮"])
    assert routines.find("点中间的按钮")["ok"] == 2 and routines.find("按一下中间那个按钮") is None


def test_jev_can_turn_down_a_shortcut_the_regex_would_take(tmp_path):
    jev = FakeJev(Triage(["other"], None, False))
    actor, planner = Model("THINK: finished\nDO: done"), Model("1. Open it")
    agent = _run(_agent(Routines(None), Screen(_frame()), actor, planner, jev), "打开微信")
    assert planner.calls == 1 and "open" not in [s["action"] for s in agent.steps]


def test_jev_finds_a_button_that_moved_and_the_pixels_confirm_it(tmp_path):
    routines = _learned(tmp_path)
    jev = FakeJev(Triage(["other"], None, False), box=(0.4, 0.62, 0.6, 0.78))
    actor, planner = Model(), Model()
    agent = _run(_agent(routines, Screen(_moved_frame()), actor, planner, jev), "点中间的按钮")
    assert actor.calls == planner.calls == 0
    [(x, y)] = agent.phone.taps
    assert x == 0.5 and abs(y - 0.7) <= 0.03  # the 20 px button matches the 10 px band over a few rows
    assert agent.steps[0]["found"] and agent.steps[-1]["action"] == "done"


def test_a_found_spot_whose_pixels_differ_is_not_pressed(tmp_path):
    routines = _learned(tmp_path)
    jev = FakeJev(Triage(["other"], None, False), box=(0.4, 0.05, 0.6, 0.2))  # Jev points at an empty area
    actor, planner = Model("THINK: tap the button\nDO: tap 50 140"), Model("1. Tap")
    agent = _run(_agent(routines, Screen(_moved_frame()), actor, planner, jev), "点中间的按钮")
    assert agent.steps[0]["action"] == "resume" and planner.calls == 1
    assert agent.phone.taps == [(0.5, 0.7)]  # pressed by the models, not the replay


def test_ask_sends_images_with_the_header_and_reads_the_answers():
    seen = {}

    def reply(request: httpx.Request) -> httpx.Response:
        seen["headers"], seen["body"] = request.headers, json.loads(request.content)
        return httpx.Response(200, json={"model": "jev-1.14.0", "answers": {"q": {"type": "noul", "noul": 0.9}}})

    jev = Jev("secret", transport=httpx.MockTransport(reply))
    answers = asyncio.run(jev.ask({"screen": "ref"}, {"q": {"type": "noul", "instructions": "?"}},
                                  images={"ref": _frame()}))
    assert answers == {"q": {"type": "noul", "noul": 0.9}}
    assert seen["headers"]["authorization"] == "Bearer secret"
    assert seen["headers"]["typesafe-danger-zone-images"] == "true"
    assert seen["body"]["model"] == "jev-preview" and seen["body"]["assets"]["ref"]["format"] == "jpg"
    img = Image.open(io.BytesIO(__import__("base64").b64decode(seen["body"]["assets"]["ref"]["data_base64"])))
    assert min(img.size) >= 256  # the 100x200 frame was scaled up to the API's smallest size


def test_ask_gives_none_when_jev_is_off_or_failing():
    assert asyncio.run(Jev(None).ask("x", {})) is None
    failing = Jev("secret", transport=httpx.MockTransport(lambda r: httpx.Response(529, json={})))
    assert asyncio.run(failing.ask("x", {"q": {"type": "noul", "instructions": "?"}})) is None


def test_triage_acts_only_on_sure_answers():
    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "assets" not in body and body["model"] == "jev-latest"
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {
            "c0": {"type": "choice", "choice": "open", "confidence": 0.9,
                   "probabilities": {"open": 0.95, "home": 0, "back": 0, "other": 0.05}},
            "c1": {"type": "choice", "choice": "home", "confidence": 0.3,
                   "probabilities": {"open": 0.2, "home": 0.6, "back": 0, "other": 0.2}},
            "asks": {"type": "noul", "noul": 0.1},
            "routine": {"type": "choice", "choice": "r1", "confidence": 0.9,
                        "probabilities": {"r0": 0.02, "r1": 0.97, "none": 0.01}}}})

    jev = Jev("secret", transport=httpx.MockTransport(reply))
    t = asyncio.run(jev.triage("打开微信，回桌面", ["打开微信", "回桌面"], ["打开设置", "打开微信，回到桌面"]))
    assert t == Triage(["open", "other"], "打开微信，回到桌面", False)
