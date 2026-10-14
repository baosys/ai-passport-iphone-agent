import asyncio
import io

import pytest
from PIL import Image, ImageDraw

from hidagent.agent import Agent
from hidagent.debug import Trace
from hidagent.routines import MAX_FAILS, Routines, asks, normalize, patch, same


def _frame(button: str = "white", page: int = 0) -> bytes:
    """A 100x200 screen with a button in the middle; `page` paints something else elsewhere."""
    img = Image.new("RGB", (100, 200), "black")
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 90, 60, 110), fill=button)
    if page:
        draw.rectangle((0, 160, 100, 200), fill="gray")
    out = io.BytesIO()
    img.save(out, "JPEG")
    return out.getvalue()


class Screen:
    live, aspect = True, 0.5

    def __init__(self, frame: bytes):
        self.frame = frame

    async def settled(self, **wait):
        pass


class Phone:
    def __init__(self, screen: Screen):
        self.screen, self.taps = screen, []

    async def tap(self, x, y):
        self.taps.append((round(x, 2), round(y, 2)))
        self.screen.frame = _frame(page=1)  # the tap opens the next page


class Model:
    model = "fake"

    def __init__(self, *replies: str):
        self.replies, self.calls = list(replies), 0

    async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
        self.calls += 1
        return self.replies.pop(0) if self.replies else "THINK: finished\nDO: done"


def _run(agent: Agent, task: str) -> Agent:
    async def go():
        agent.start(task)
        await agent._job
    asyncio.run(go())
    return agent


def _agent(routines: Routines, screen: Screen, actor: Model, planner: Model) -> Agent:
    return Agent(Phone(screen), screen, actor, planner, Trace(None), routines=routines)


def test_normalize_ignores_politeness_punctuation_and_case():
    assert normalize("请帮我打开 设置。") == normalize("打开设置") == "打开设置"
    assert normalize("Open the App Store!") == normalize("open the app store")


def test_patch_sees_the_button_and_not_the_rest_of_the_screen():
    at = (0.5, 0.5)
    assert same(patch(_frame(), at), patch(_frame(page=1), at))  # a change elsewhere does not matter
    assert not same(patch(_frame(), at), patch(_frame("black"), at))  # the button itself is gone


def test_a_finished_task_is_remembered_then_replayed_without_any_model(tmp_path):
    routines = Routines(tmp_path / "routines.json")
    actor, planner = Model("THINK: tap the button\nDO: tap 50 100"), Model("1. Tap the button")
    agent = _run(_agent(routines, Screen(_frame()), actor, planner), "点中间的按钮")
    assert agent.steps[-1]["action"] == "done"
    [step] = routines.find("点中间的按钮")["steps"]
    assert (step["action"], step["x"], step["y"]) == ("tap", 500, 500) and step["cue"]

    again = Routines(tmp_path / "routines.json")  # read back from disk
    actor, planner = Model(), Model()
    agent = _run(_agent(again, Screen(_frame()), actor, planner), "请点中间的按钮。")
    assert actor.calls == planner.calls == 0
    assert agent.phone.taps == [(0.5, 0.5)]
    assert [s["action"] for s in agent.steps] == ["tap", "done"]
    assert again.find("点中间的按钮")["ok"] == 2


def test_replay_hands_over_to_the_models_when_the_spot_looks_different(tmp_path):
    routines = Routines(tmp_path / "routines.json")
    _run(_agent(routines, Screen(_frame()), Model("THINK: tap\nDO: tap 50 100"), Model("1. Tap")), "点按钮")

    actor, planner = Model("THINK: the button is red now, tap it anyway\nDO: tap 50 100"), Model("1. Tap")
    agent = _run(_agent(routines, Screen(_frame("red")), actor, planner), "点按钮")
    actions = [s["action"] for s in agent.steps]
    assert actions[0] == "resume" and actions[-1] == "done"
    assert planner.calls == 1 and actor.calls == 2
    assert routines.find("点按钮")["fails"] == 0  # the models finished it: the new way is remembered


def test_a_routine_that_keeps_breaking_is_not_replayed(tmp_path):
    routines = Routines(tmp_path / "routines.json")
    routines.learn("点按钮", [{"action": "tap", "x": 500, "y": 500}], "")
    for _ in range(MAX_FAILS):
        assert routines.find("点按钮") is not None
        routines.broke("点按钮")
    assert routines.find("点按钮") is None
    routines.learn("点按钮", [{"action": "tap", "x": 500, "y": 500}], "")
    assert routines.find("点按钮") is not None


def test_a_question_is_replayed_up_to_the_answer_then_read_by_the_model(tmp_path):
    routines = Routines(tmp_path / "routines.json")
    routines.learn("看看按钮下面写了什么", [{"action": "tap", "x": 500, "y": 500}], "old answer")
    assert asks("看看按钮下面写了什么")
    actor, planner = Model("THINK: read it\nDO: done new answer"), Model("1. Read it")
    agent = _run(_agent(routines, Screen(_frame()), actor, planner), "看看按钮下面写了什么")
    assert agent.phone.taps == [(0.5, 0.5)] and actor.calls == 1
    assert agent.steps[-1] == {**agent.steps[-1], "action": "done", "answer": "new answer"}


@pytest.mark.parametrize(("task", "question"), [
    ("今天天气怎么样", True), ("微信里有没有新消息", True), ("电量还剩多少？", True), ("What time is it", True),
    ("打开设置，点通用", False), ("给张三发消息说晚上好", False), ("Open the App Store", False),
])
def test_questions_are_told_apart_from_chores(task, question):
    assert asks(task) is question
