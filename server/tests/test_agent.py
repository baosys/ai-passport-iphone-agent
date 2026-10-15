import math
import re

import pytest

from hidagent.agent import KEY_NAMES, circle, parse_actions, parse_command, parse_plan, scale


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


def test_scroll_distance_is_scaled_like_a_height():
    act = parse_command("scroll 200 600 -300")
    assert act == {"action": "scroll", "x": 200, "y": 600, "dy": -300}
    assert scale(act, 2, 0.5) == {"action": "scroll", "x": 400, "y": 300, "dy": -150}


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


def test_a_task_waits_for_a_dropped_mirror_and_lets_it_reconnect(monkeypatch):
    import asyncio

    import hidagent.agent as agent_module
    from hidagent.agent import Agent, BlindError
    from hidagent.debug import Trace

    class Screen:
        live, frame = False, b"jpeg"
        async def settled(self, **wait):
            pass

    class Actor:
        model = "fake"
        async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
            return "ok"

    screen = Screen()
    agent = Agent(phone=None, screen=screen, actor=Actor(), planner=None, trace=Trace(None))
    seen = []

    async def mirror_comes_back():
        await asyncio.sleep(0.1)
        seen.append(agent.blind)  # the mirror watcher may tap now
        screen.live = True

    async def go():
        back = asyncio.create_task(mirror_comes_back())
        reply = await agent._ask("actor", agent.actor, "", "")
        await back
        return reply

    assert asyncio.run(go())[0] == "ok" and seen == [True] and not agent.blind
    screen.live = False
    monkeypatch.setattr(agent_module, "BLIND_WAIT", 0.1)
    with pytest.raises(BlindError):
        asyncio.run(agent._ask("actor", agent.actor, "", ""))


def test_typed_chinese_is_shown_whole_and_marked_as_typed():
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
            return "THINK: send\nDO: key enter"

    text = "更正：汇丰 Pulse 信用卡可以用人民币还款。" * 8  # far past what one history line used to keep
    actor = Actor()
    agent = Agent(phone=None, screen=Screen(), actor=actor, planner=actor, trace=Trace(None))
    agent.steps = [{"action": "type", "text": text, "changed": True}]
    asyncio.run(agent._next_actions())
    assert text in actor.prompts[0]
    assert f"all {len(text)} characters are in the field exactly" in actor.prompts[0]


def _screen(shade):  # a real JPEG frame of one flat shade
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("L", (100, 200), shade).save(out, "JPEG")
    return out.getvalue()


def _agent_with(moves):
    from hidagent.agent import Agent
    from hidagent.debug import Trace

    agent = Agent(phone=None, screen=None, actor=None, planner=None, trace=Trace(None))
    for i, (frame, step) in enumerate(moves):
        agent.steps.append(step)
        agent.shots[i] = frame
    return agent


def test_going_round_in_circles_is_a_loop():
    chats, chat = _screen(250), _screen(20)
    open_chat, back = {"action": "tap", "x": 316, "y": 198}, {"action": "tap", "x": 54, "y": 74}
    moves = [(chats, open_chat), (chat, back), (chats, {**open_chat, "x": 290}), (chat, back)]
    agent = _agent_with(moves)
    assert agent._cycling({**open_chat, "x": 340}, chats)  # the third time into the same chat from the list
    assert not agent._cycling({"action": "tap", "x": 500, "y": 600}, chats)  # another chat
    assert not _agent_with(moves[:2])._cycling(open_chat, chats)  # the second time is not a circle yet


def test_tapping_keypad_digits_on_one_screen_is_no_loop():
    pad = _screen(128)
    one, two = {"action": "tap", "x": 200, "y": 700}, {"action": "tap", "x": 500, "y": 700}
    agent = _agent_with([(pad, one), (pad, two), (pad, one), (pad, two)])
    assert not agent._cycling(one, pad)
    run = _agent_with([(pad, one), (_screen(20), two), (pad, one), (pad, one)])
    assert not run._cycling(one, pad)  # the same tap right again (+ + +) is _repeating's to judge


def test_actor_sees_where_each_step_ran_and_the_earlier_steps():
    import asyncio

    from hidagent.agent import Agent
    from hidagent.debug import Trace

    class Screen:
        live, frame = True, _jpeg(1000, 1000)

    class Actor:
        model = "fake"
        prompt = ""
        async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
            self.prompt = text
            return "THINK: next\nDO: tap 1 2"

    actor = Actor()
    agent = Agent(phone=None, screen=Screen(), actor=actor, planner=actor, trace=Trace(None))
    agent.steps = ([{"action": "tap", "x": 500, "y": 580, "page": "WeChat transfer receipt", "changed": True},
                    {"action": "type", "text": "secret", "page": "Password", "changed": True}]
                   + [{"action": "back", "page": "WeChat chat", "changed": True}] * 2
                   + [{"action": "tap", "x": 300, "y": 200, "page": "WeChat chat list", "changed": True}] * 8)
    asyncio.run(agent._next_actions())
    earlier = actor.prompt.split("Earlier steps")[1].split("Recent actions")[0]
    assert "- on WeChat transfer receipt: tap" in earlier and "- on WeChat chat: back (x2)" in earlier
    assert "secret" not in earlier  # typed text stays out of the summary
    assert 'on WeChat chat list: {"action": "tap"' in actor.prompt


def test_a_stuck_replan_sees_the_steps_taken():
    import asyncio

    from hidagent.agent import Agent
    from hidagent.debug import Trace

    class Screen:
        live, frame = True, _jpeg(1000, 1000)

    class Planner:
        model = "fake"
        prompt = ""
        async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
            self.prompt = text
            return "1. Open the NICE chat"

    planner = Planner()
    agent = Agent(phone=None, screen=Screen(), actor=planner, planner=planner, trace=Trace(None))
    agent.task = "accept the transfer, then pay"
    agent.steps = [{"action": "tap", "x": 500, "y": 580, "page": "WeChat transfer receipt"},
                   {"action": "loop", "reason": "tap again"}]
    asyncio.run(agent._replan(stuck=True))
    assert "- on WeChat transfer receipt: tap" in planner.prompt and "leave what they already did" in planner.prompt
