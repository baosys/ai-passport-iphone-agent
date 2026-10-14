import math
import re

import pytest

from hidagent.agent import KEY_NAMES, circle, parse_actions, parse_command, parse_plan


def test_parse_single_action():
    acts = parse_actions("THINK: The Search pill is visible, so I tap it.\nDO: tap 500 814")
    assert acts == [{"thought": "The Search pill is visible, so I tap it.", "thought_zh": "",
                     "action": "tap", "x": 500, "y": 814}]


def test_parse_several_actions_keeps_thought_on_first():
    acts = parse_actions("THINK: Type and search.\nDO: type Whiteboard\nDO: key enter")
    assert [a["action"] for a in acts] == ["type", "key"]
    assert acts[0]["text"] == "Whiteboard" and acts[1]["combo"] == "enter"
    assert "thought" not in acts[1]


def test_parse_caps_at_six_actions():
    reply = "THINK: draw\n" + "\n".join(f"DO: tap {i} {i}" for i in range(10))
    assert len(parse_actions(reply)) == 6


def test_parse_path_and_circle():
    assert parse_command("path 100 200 300 400 500 600")["points"] == [[100, 200], [300, 400], [500, 600]]
    assert parse_command("circle 500 450 200") == {"action": "circle", "x": 500, "y": 450, "r": 200}


@pytest.mark.parametrize("line", ["fly 1 2", "tap 500", "path 1 2 3"])
def test_parse_rejects_bad_commands(line):
    with pytest.raises(ValueError):
        parse_command(line)


def test_parse_requires_a_do_line():
    with pytest.raises(ValueError):
        parse_actions("THINK: I am not sure what to do.")


def test_parse_plan_with_and_without_languages():
    assert parse_plan("EN:\n1. Tap Search\nZH:\n1. 点搜索") == ("1. Tap Search", "1. 点搜索")
    assert parse_plan("1. Tap Search") == ("1. Tap Search", "1. Tap Search")


def test_circle_is_closed_and_round_on_a_tall_screen():
    aspect = 0.46  # width / height of a portrait iPhone frame
    pts = circle(0.5, 0.5, 0.2, aspect)
    assert pts[0] == pytest.approx(pts[-1])
    for x, y in pts:  # equal radius in pixels: y offsets shrink by the aspect ratio
        assert math.hypot(x - 0.5, (y - 0.5) / aspect) == pytest.approx(0.2)


def test_actor_may_not_repeat_a_move_that_changed_nothing():
    import asyncio

    from hidagent.agent import Agent
    from hidagent.debug import Trace

    class Screen:
        live, frame, aspect = True, b"jpeg", 0.46

    class Actor:
        model = "fake"
        def __init__(self):
            self.prompts = []
        async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
            self.prompts.append(text)
            return "THINK: again\nDO: swipe 382 253 382 150" if len(self.prompts) == 1 else "THINK: other\nDO: tap 1 2"

    actor = Actor()
    agent = Agent(phone=None, screen=Screen(), actor=actor, planner=actor, trace=Trace(None))
    agent.steps = [{"action": "swipe", "x1": 382, "y1": 253, "x2": 382, "y2": 150, "changed": False}]
    acts = asyncio.run(agent._next_actions())
    assert acts[0]["action"] == "tap"
    assert "changed nothing" in actor.prompts[1]


def test_repeating_a_move_that_keeps_changing_the_screen_is_not_stuck():
    from hidagent.agent import Agent
    from hidagent.debug import Trace

    agent = Agent(phone=None, screen=None, actor=None, planner=None, trace=Trace(None))
    swipe = {"action": "swipe", "x1": 500, "y1": 800, "x2": 500, "y2": 400}
    agent.steps = [{**swipe, "changed": True}, {**swipe, "changed": True}]
    assert not agent._repeating(dict(swipe))
    agent.steps[-1]["changed"] = False  # reached the end of the list
    assert agent._repeating(dict(swipe))


def test_precise_tap_flag():
    assert parse_command("tap 120 640 precise") == {"action": "tap", "x": 120, "y": 640, "precise": True}
    assert "precise" not in parse_command("tap 120 640")


def _jpeg(w, h):  # just enough JPEG header for the frame size
    return b"\xff\xd8\xff\xc0\x00\x11\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03"


def test_frame_size_from_the_jpeg_header():
    from hidagent.screen import frame_size

    assert frame_size(_jpeg(500, 1080)) == (500, 1080)
    assert frame_size(b"not a jpeg") is None and frame_size(None) is None


def test_actor_speaks_screenshot_pixels_and_the_agent_keeps_0_to_1000():
    import asyncio

    from hidagent.agent import Agent
    from hidagent.debug import Trace

    class Screen:
        live, frame = True, _jpeg(500, 1080)

    class Actor:
        model = "fake"
        prompt = ""
        async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
            self.prompt = text
            return "THINK: search\nDO: tap 250 898\nDO: circle 250 540 100"

    actor = Actor()
    agent = Agent(phone=None, screen=Screen(), actor=actor, planner=actor, trace=Trace(None))
    agent.steps = [{"action": "swipe", "x1": 500, "y1": 800, "x2": 500, "y2": 400, "changed": True}]
    tap, ring = asyncio.run(agent._next_actions())
    assert "500 pixels wide and 1080 pixels tall" in actor.prompt
    assert '"x1": 250, "y1": 864, "x2": 250, "y2": 432' in actor.prompt  # history in the actor's pixels
    assert (tap["x"], tap["y"]) == (500, 831)
    assert (ring["x"], ring["y"], ring["r"]) == (500, 500, 200)  # radius stays a share of the width


def test_parse_open_app_by_search():
    acts = parse_actions("THINK: Open Notes through search.\nDO: key cmd+space\nDO: type Notes\nDO: key enter")
    assert [(a["action"], a.get("combo", a.get("text"))) for a in acts] == [
        ("key", "cmd+space"), ("type", "Notes"), ("key", "enter")]


def test_parse_open_app_by_name():
    assert [(a["action"], a["app"]) for a in parse_actions("THINK: Open Notes.\nDO: open Notes")] == [("open", "Notes")]
    assert parse_command('open "App Store"') == {"action": "open", "app": "App Store"}


@pytest.mark.parametrize(("line", "combo"), [
    ("key Cmd + Space", "cmd+space"), ("key cmd+H", "cmd+h"), ("key return", "enter"), ("key Escape", "esc"),
    ('key "shift+tab"', "shift+tab"), ("key command+option+left arrow", "cmd+alt+left"), ("key ⌘+a", "cmd+a"),
])
def test_parse_normalizes_key_combos(line, combo):
    assert parse_command(line) == {"action": "key", "combo": combo}


@pytest.mark.parametrize("line", ["key", "key cmd+", "key home", "key enter enter", "key 返回"])
def test_parse_rejects_unknown_keys(line):
    with pytest.raises(ValueError):
        parse_command(line)


def _firmware_key_names() -> tuple[set[str], set[str]]:
    from pathlib import Path

    hw = Path(__file__).resolve().parents[2] / "hardware"
    passport = set(re.findall(r'\{\s*"(\w+)"', (hw / "ai_passport/main/agent_keymap.c").read_text()))
    bridge = set(re.findall(r'k == "(\w+)"', (hw / "hid_bridge/hid_bridge.ino").read_text()))
    return passport, bridge


def test_key_names_exist_in_both_firmwares():
    passport, bridge = _firmware_key_names()
    assert KEY_NAMES <= passport
    assert KEY_NAMES - {"delete"} <= bridge


def test_prompt_keys_work_on_both_boards():
    from hidagent.agent import ACTOR_PROMPT, PLANNER_PROMPT

    passport, bridge = _firmware_key_names()
    combos = re.findall(r"\bkey ([a-z]+(?:\+[a-z]+)*)", ACTOR_PROMPT + PLANNER_PROMPT)
    combos += re.findall(r"\b((?:cmd|shift|ctrl|alt)\+[a-z]+)", ACTOR_PROMPT + PLANNER_PROMPT)
    assert "cmd+space" in combos and "cmd+h" in combos and "shift+tab" in combos
    for combo in combos:
        for token in combo.split("+"):
            assert len(token) == 1 or (token in passport and token in bridge), combo


def test_round_timing_is_logged_and_kept_out_of_action_keys():
    import asyncio
    import io

    from PIL import Image

    from hidagent.agent import Agent, _key
    from hidagent.debug import Trace

    jpeg = io.BytesIO()
    Image.new("RGB", (46, 100)).save(jpeg, "JPEG")

    class Screen:
        live, frame, aspect = True, jpeg.getvalue(), 0.46
        async def settled(self, **wait):
            pass

    class Phone:
        def __init__(self):
            self.sent = []
        async def key(self, combo):
            self.sent.append(("key", combo))
        async def type(self, text):
            self.sent.append(("type", text))

    class Actor:
        model = "fake"
        async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
            return "THINK: open Notes\nDO: key cmd+space\nDO: type Notes\nDO: key enter"

    phone = Phone()
    agent = Agent(phone=phone, screen=Screen(), actor=Actor(), planner=None, trace=Trace(None))
    assert asyncio.run(agent._step()) is False
    assert phone.sent == [("key", "cmd+space"), ("type", "Notes"), ("key", "enter")]
    first, last = agent.steps[0], agent.steps[-1]
    assert "think_s" in first and all("act_s" in s for s in agent.steps)
    assert "settle_s" in last and last["changed"] is False  # the fake screen never changes
    assert _key(last) == {"action": "key", "combo": "enter"}


@pytest.mark.parametrize(("task", "acts", "rest"), [
    ("打开微信", [{"action": "open", "app": "微信"}], ""),
    ("请帮我打开 App Store。", [{"action": "open", "app": "App Store"}], ""),
    ("open Settings app", [{"action": "open", "app": "Settings"}], ""),
    ("回到主屏幕", [{"action": "home"}], ""),
    ("go home", [{"action": "home"}], ""),
    ("打开微信，给张三发消息", [{"action": "open", "app": "微信"}], "给张三发消息"),
    ("回到桌面然后打开设置", [{"action": "home"}, {"action": "open", "app": "设置"}], ""),
    ("打开微信给张三发消息", [], "打开微信给张三发消息"),  # no clause boundary: too risky to guess the name
    ("打开设置里的Wi-Fi", [], "打开设置里的Wi-Fi"),
    ("给张三发消息，然后打开微信", [], "给张三发消息，然后打开微信"),  # only leading shortcuts run
    ("open the authentication settings in Safari", [], "open the authentication settings in Safari"),
])
def test_shortcuts_split_the_task(task, acts, rest):
    from hidagent.shortcuts import split

    assert split(task) == (acts, rest)


def test_search_term_types_hanzi_as_pinyin():
    from hidagent.shortcuts import search_term

    assert search_term("微信") == "weixin" and search_term("App Store") == "App Store"


def test_shortcut_task_runs_without_any_model_and_without_a_screen():
    import asyncio

    from hidagent.agent import Agent
    from hidagent.debug import Trace

    class Screen:
        live, frame = False, None

    class Phone:
        opened = []
        async def open_app(self, name):
            self.opened.append(name)

    async def run():
        agent = Agent(phone=Phone(), screen=Screen(), actor=None, planner=None, trace=Trace(None))
        agent.start("打开微信")
        await agent._job
        return agent

    agent = asyncio.run(run())
    assert Phone.opened == ["weixin"]
    assert [s["action"] for s in agent.steps] == ["open", "done"]
