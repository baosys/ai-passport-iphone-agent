import asyncio
import io
import json

import httpx
from PIL import Image, ImageDraw
from test_routines import Model, Phone, Screen, _frame, _run

from hidagent.agent import Agent, _key, parse_actions
from hidagent.debug import Trace
from hidagent.jev import QUICK, Jev, Triage
from hidagent.routines import Routines


class FakeJev(Jev):
    def __init__(self, triage: Triage | None = None, box=None, page: float | None = None, done: float | None = None):
        super().__init__("key")
        self.answer, self.box, self.on_page, self.finished, self.asked = triage, box, page, done, []

    async def triage(self, task, clauses, remembered):
        self.asked.append(("triage", task, clauses, remembered))
        return self.answer

    async def find(self, screen, look):
        self.asked.append(("find",))
        return self.box

    async def page(self, screen, page):
        self.asked.append(("page", page))
        return self.on_page

    async def done(self, screen, task):
        self.asked.append(("done", task))
        return self.finished


class SlowModel(Model):
    """A model that takes a moment to answer, so a quicker Jev can get in first."""

    async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
        await asyncio.sleep(0.2)
        return await super().chat(system, text, image, max_tokens, temperature)


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
    _run(_agent(routines, Screen(_frame()), Model("PAGE: Button page\nTHINK: tap\nDO: tap 50 100"), Model("1. Tap"),
                Jev(None)), "点中间的按钮")
    assert routines.find("点中间的按钮")["steps"][0]["look"]
    return routines


def test_the_actor_names_the_page_and_every_action_of_the_round_carries_it():
    acts = parse_actions("PAGE: Settings > General\nTHINK: open About\nDO: tap 10 20\nDO: key enter")
    assert [a["page"] for a in acts] == ["Settings > General"] * 2
    assert _key(acts[0]) == {"action": "tap", "x": 10, "y": 20}  # the page is not part of the action
    assert "page" not in parse_actions("THINK: x\nDO: wait")[0]


def test_a_learned_step_remembers_its_page(tmp_path):
    [step] = _learned(tmp_path).find("点中间的按钮")["steps"]
    assert step["page"] == "Button page"


def test_replay_presses_when_jev_sees_the_page(tmp_path):
    routines = _learned(tmp_path)
    jev = FakeJev(Triage(["other"], None, False), page=0.9, done=0.9)
    actor, planner = Model(), Model()
    agent = _run(_agent(routines, Screen(_frame()), actor, planner, jev), "点中间的按钮")
    assert actor.calls == planner.calls == 0 and agent.phone.taps == [(0.5, 0.5)]
    assert ("page", "Button page") in jev.asked and ("done", "点中间的按钮") in jev.asked
    assert agent.steps[0]["page"] == "Button page" and agent.steps[-1]["action"] == "done"
    assert routines.find("点中间的按钮")["steps"][0]["page"] == "Button page"


def test_a_clear_no_on_the_page_hands_over_even_when_the_pixels_match(tmp_path):
    routines = _learned(tmp_path)
    jev = FakeJev(Triage(["other"], None, False), page=0.1)
    actor, planner = Model("THINK: tap the button\nDO: tap 50 100"), Model("1. Tap")
    agent = _run(_agent(routines, Screen(_frame()), actor, planner, jev), "点中间的按钮")
    assert agent.steps[0]["action"] == "resume" and "another page" in agent.steps[0]["reason"]
    assert planner.calls == 1 and agent.phone.taps == [(0.5, 0.5)]  # pressed by the models, not the replay


def test_a_clear_no_on_the_page_skips_the_search_for_a_moved_button(tmp_path):
    routines = _learned(tmp_path)
    jev = FakeJev(Triage(["other"], None, False), box=(0.4, 0.62, 0.6, 0.78), page=0.05)
    agent = _run(_agent(routines, Screen(_moved_frame()), Model(), Model("1. Tap"), jev), "点中间的按钮")
    assert agent.steps[0]["action"] == "resume" and ("find",) not in jev.asked


def test_an_unsure_or_silent_jev_leaves_the_page_to_the_pixels(tmp_path):
    for page in (0.5, None):
        routines = _learned(tmp_path)
        jev = FakeJev(Triage(["other"], None, False), page=page)
        actor, planner = Model(), Model()
        agent = _run(_agent(routines, Screen(_frame()), actor, planner, jev), "点中间的按钮")
        assert actor.calls == planner.calls == 0 and agent.phone.taps == [(0.5, 0.5)]
        assert agent.steps[-1]["action"] == "done"


def test_a_replay_jev_sees_unfinished_goes_to_the_models(tmp_path):
    routines = _learned(tmp_path)
    jev = FakeJev(Triage(["other"], None, False), page=0.9, done=0.1)
    actor, planner = Model("THINK: one more\nDO: done finished now"), Model("1. Finish")
    agent = _run(_agent(routines, Screen(_frame()), actor, planner, jev), "点中间的按钮")
    actions = [s["action"] for s in agent.steps]
    assert actions[:2] == ["tap", "resume"] and "not done" in agent.steps[1]["reason"]
    assert planner.calls == actor.calls == 1 and agent.steps[-1]["answer"] == "finished now"


def test_a_replay_ends_as_before_when_jev_is_sure_or_unsure_it_is_done(tmp_path):
    for done in (0.9, 0.5, None):
        routines = _learned(tmp_path)
        jev = FakeJev(Triage(["other"], None, False), done=done)
        agent = _run(_agent(routines, Screen(_frame()), Model(), Model(), jev), "点中间的按钮")
        assert [s["action"] for s in agent.steps] == ["tap", "done"]


def test_jev_ends_a_task_the_last_round_finished_without_asking_the_actor_again(tmp_path):
    routines = Routines(tmp_path / "routines.json")
    jev = FakeJev(Triage(["other"], None, False), done=0.95)
    actor, planner = SlowModel("PAGE: Button page\nTHINK: tap\nDO: tap 50 100"), Model("1. Tap")
    agent = _run(_agent(routines, Screen(_frame()), actor, planner, jev), "点中间的按钮")
    assert actor.calls == 1 and agent.steps[-1]["action"] == "done" and "Jev" in agent.steps[-1]["thought"]
    assert jev.asked.count(("done", "点中间的按钮")) == 1  # after the round that changed the screen, not before
    [step] = routines.find("点中间的按钮")["steps"]  # and the run is remembered as usual
    assert step["action"] == "tap" and step["page"] == "Button page"


def test_the_actor_goes_on_when_jev_does_not_see_the_task_done(tmp_path):
    jev = FakeJev(Triage(["other"], None, False), done=0.6)
    actor = SlowModel("THINK: tap\nDO: tap 50 100", "THINK: finished\nDO: done all good")
    agent = _run(_agent(Routines(None), Screen(_frame()), actor, Model("1. Tap"), jev), "点中间的按钮")
    assert actor.calls == 2 and agent.steps[-1]["answer"] == "all good"


def test_a_question_is_always_answered_by_the_actor(tmp_path):
    jev = FakeJev(done=0.99)  # no triage: the regex finds the question
    actor = SlowModel("THINK: tap\nDO: tap 50 100", "THINK: read it\nDO: done it says hello")
    agent = _run(_agent(Routines(None), Screen(_frame()), actor, Model("1. Tap"), jev), "看看按钮下面写了什么")
    assert agent.steps[-1]["answer"] == "it says hello"
    assert not [q for q in jev.asked if q[0] == "done"]


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


def test_page_and_done_read_the_yes_no_answer_quickly():
    seen = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        [name] = body["questions"]
        return httpx.Response(200, json={"model": "jev-1.14.0", "answers": {name: {"type": "noul", "noul": 0.15}}})

    jev = Jev("secret", transport=httpx.MockTransport(reply))
    assert asyncio.run(jev.page(_frame(), "Settings > General")) == 0.15
    assert asyncio.run(jev.done(_frame(), "打开通用")) == 0.15
    assert seen[0]["state"]["page"] == "Settings > General" and seen[1]["state"]["task"] == "打开通用"
    assert all(b["model"] == "jev-preview" and len(b["assets"]) == 1 for b in seen)
    assert QUICK < jev.timeout


def test_page_and_done_give_none_when_jev_fails_or_cannot_read_the_frame():
    failing = Jev("secret", transport=httpx.MockTransport(lambda r: httpx.Response(500, json={})))
    assert asyncio.run(failing.page(_frame(), "Home screen")) is None
    assert asyncio.run(failing.done(_frame(), "回桌面")) is None
    odd = Jev("secret", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"answers": {"other": {"noul": 0.9}}})))
    assert asyncio.run(odd.page(_frame(), "Home screen")) is None
    assert asyncio.run(odd.done(b"not a jpeg", "回桌面")) is None
