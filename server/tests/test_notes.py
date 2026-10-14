import json

from test_routines import Model, Phone, Screen, _frame, _run

from hidagent.agent import Agent
from hidagent.debug import Trace
from hidagent.routines import Routines


class Typist(Phone):
    def __init__(self, screen: Screen):
        super().__init__(screen)
        self.typed, self.keys = [], []

    async def type(self, text):
        self.typed.append(text)

    async def key(self, combo):
        self.keys.append(combo)

    async def swipe(self, x1, y1, x2, y2):  # scrolls to another page each time
        self.page = 1 - getattr(self, "page", 0)
        self.screen.frame = _frame(page=self.page)

    async def open_app(self, name):
        self.keys.append(f"open {name}")


class Prompts(Model):
    def __init__(self, *replies: str):
        super().__init__(*replies)
        self.prompts = []

    async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
        self.prompts.append(text)
        return await super().chat(system, text, image, max_tokens, temperature)


def _agent(routines: Routines, actor: Model, trace: Trace | None = None) -> Agent:
    screen = Screen(_frame())
    return Agent(Typist(screen), screen, actor, Model("1. Read it\n2. Send it"), trace or Trace(None),
                 routines=routines)


def test_notes_carry_what_was_read_to_a_later_message_and_are_not_replayed(tmp_path):
    routines = Routines(tmp_path / "routines.json")
    actor = Prompts("THINK: read\nDO: note 周一 晴 20°C",
                    *["THINK: scroll\nDO: swipe 50 150 50 100"] * 9,
                    "THINK: send\nDO: type 华盛顿：周一 晴 20°C\nDO: key enter",
                    "THINK: sent\nDO: done 已发送")
    agent = _run(_agent(routines, actor), "查华盛顿天气发给 nice")
    assert agent.phone.typed == ["华盛顿：周一 晴 20°C"] and agent.phone.keys == ["enter"]
    # the note outlives the window of recent actions, and is not shown as an action that changed nothing
    assert "- 周一 晴 20°C" in actor.prompts[-1] and '"note"' not in actor.prompts[-1]
    assert "did NOT change" not in actor.prompts[1]
    assert routines.find("查华盛顿天气发给 nice") is None  # a replay would send today's weather next week


def test_each_step_says_which_tier_decided_it_and_the_run_is_tallied(tmp_path):
    trace = Trace(tmp_path)
    agent = _run(_agent(Routines(None), Model("THINK: tap\nDO: tap 50 100"), trace), "打开微信，点中间的按钮")
    tiers = [s.get("tier") for s in agent.steps]
    assert tiers[0] == "code" and "planner" in tiers and "actor" in tiers
    lines = [json.loads(line) for f in tmp_path.rglob("*.jsonl") for line in f.read_text().splitlines()]
    [tally] = [x for x in lines if x.get("kind") == "tiers"]
    assert tally["tiers"]["actor"]["steps"] == 2 and tally["outcome"] == "done" and tally["escalated"]
