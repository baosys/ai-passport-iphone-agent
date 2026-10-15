import asyncio
import io
import json
import logging

import pytest
from PIL import Image, ImageDraw, ImageFont
from test_routines import Model, Screen, _frame, _run

from hidagent import agent as agent_module
from hidagent import vault, vision
from hidagent.agent import Agent, parse_command
from hidagent.debug import Trace
from hidagent.routines import Routines, normalize
from hidagent.vault import RedactFilter, Vault

PIN = "246810"


class Phone:
    def __init__(self, screen: Screen):
        self.screen, self.taps, self.typed, self.points, self.loud = screen, [], [], [], []

    async def tap(self, x, y, aim=True):
        self.taps.append((round(x, 3), round(y, 3)))
        self.loud.append(not vault.hidden())

    async def point(self, x, y, aim=False):
        self.points.append((round(x, 3), round(y, 3)))

    async def type(self, text):
        self.typed.append(("type", text))

    async def type_secret(self, text):
        self.typed.append(("secret", text))


class Recorder(Model):
    def __init__(self, *replies: str):
        super().__init__(*replies)
        self.prompts = []

    async def chat(self, system, text, image=None, max_tokens=600, temperature=0):
        self.prompts.append(text)
        return await super().chat(system, text, image, max_tokens, temperature)


def _agent(actor: Model, env: dict | None = None, trace: Trace | None = None, routines: Routines | None = None):
    screen = Screen(_frame())
    planner = Recorder("EN:\n1. Pay\nZH:\n1. 付款")
    agent = Agent(Phone(screen), screen, actor, planner, trace or Trace(None), routines=routines,
                  vault=Vault({"HIDAGENT_SECRET_WECHAT_PAY": PIN} if env is None else env))
    return agent, planner


def test_parse_secret_and_keypad():
    assert parse_command("secret {wechat_pay}") == {"action": "secret", "name": "WECHAT_PAY"}
    assert parse_command("keypad WECHAT_PAY 100 600 900 950") == {
        "action": "keypad", "name": "WECHAT_PAY", "x1": 100, "y1": 600, "x2": 900, "y2": 950}
    with pytest.raises(ValueError):
        parse_command("keypad WECHAT_PAY 100 600")


def test_keypad_key_centres():
    box = (0.0, 0.6, 0.9, 1.0)  # 3 columns of 0.3, 4 rows of 0.1
    spots = vault.keypad("1590", box)
    assert spots == pytest.approx([(0.15, 0.65), (0.45, 0.75), (0.75, 0.85), (0.45, 0.95)])
    assert vault.keypad("1", (0.9, 1.0, 0.0, 0.6)) == pytest.approx([(0.15, 0.65)])  # corners in any order
    with pytest.raises(ValueError):
        vault.keypad("12a", box)


def test_redact_longest_first_and_skips_short_values():
    v = Vault({"HIDAGENT_SECRET_A": "secret12", "HIDAGENT_SECRET_B": "secret1234", "HIDAGENT_SECRET_C": "12"})
    v.load()
    assert v.redact("pay secret1234 then secret12, code 12") == "pay {B} then {A}, code 12"
    assert v.scrub({"x": ["secret12", ("secret12",)], "n": 3}) == {"x": ["{A}", ("{A}",)], "n": 3}


def test_keychain_comes_before_the_environment():
    env = {"HIDAGENT_SECRET_A": "from-env", "HIDAGENT_SECRET_B": "env-only", "HIDAGENT_SECRET_A_MAX": "50"}
    v = Vault(env, keychain=lambda name: "from-keychain" if name == "A" else None)
    v.load("pay {A} and {B}")
    assert v.get("A") == "from-keychain" and v.get("B") == "env-only"
    assert v.get("A_MAX") is None and v.limits == {"A": 50.0}  # a cap is not a secret


def test_keychain_is_read_for_names_in_the_task():
    asked = []
    v = Vault({}, keychain=lambda name: asked.append(name) or ("987654" if name == "ALIPAY" else None))
    v.load("pay with {ALIPAY}")
    assert asked == ["ALIPAY"] and v.get("ALIPAY") == "987654"


def test_secret_is_typed_but_never_shown(tmp_path):
    actor = Recorder("THINK: the password field is open\nDO: secret WECHAT_PAY", "THINK: paid\nDO: done")
    agent, planner = _agent(actor, trace=Trace(tmp_path))
    _run(agent, "Pay Alice 1 yuan, password {WECHAT_PAY}")
    assert agent.phone.typed == [("secret", PIN)]
    assert agent.steps[-1]["action"] == "done"
    assert all(PIN not in p for p in actor.prompts + planner.prompts)
    assert "{WECHAT_PAY} (entered already)" in actor.prompts[-1]
    text = "".join(f.read_text() for f in tmp_path.rglob("steps.jsonl"))
    assert "secret" in text and PIN not in text


def test_type_with_a_placeholder_types_the_secret():
    agent, _ = _agent(Model("THINK: login\nDO: type {WECHAT_PAY}", "THINK: in\nDO: done"))
    _run(agent, "Log in with {WECHAT_PAY}")
    assert agent.phone.typed == [("secret", PIN)]
    assert agent.steps[1]["text"] == "{WECHAT_PAY}"


def test_a_password_typed_into_the_task_is_redacted_everywhere(tmp_path):
    actor = Recorder("THINK: ok\nDO: done")
    screen = Screen(_frame())
    planner = Recorder("1. Pay")
    agent = Agent(Phone(screen), screen, actor, planner, Trace(tmp_path),
                  vault=Vault({"HIDAGENT_SECRET_WECHAT_PAY": PIN}))
    _run(agent, f"Pay Alice 1 yuan, password {PIN}")
    assert agent.task == "Pay Alice 1 yuan, password {WECHAT_PAY}"
    assert planner.prompts and actor.prompts
    assert all(PIN not in p for p in actor.prompts + planner.prompts)
    text = "".join(f.read_text() for f in tmp_path.rglob("steps.jsonl"))
    assert "{WECHAT_PAY}" in text and PIN not in text


def test_a_value_echoed_by_a_model_is_redacted():
    agent, _ = _agent(Model(f"THINK: the password is {PIN}\nDO: done"))
    _run(agent, "Pay with {WECHAT_PAY}")
    assert PIN not in json.dumps(agent.steps)


@pytest.mark.parametrize("task, reply, why", [
    ("Pay Alice, password {WECHAT_PAY}", "DO: secret ALIPAY", "names no {ALIPAY}"),
    ("Pay Alice, password {ALIPAY}", "DO: secret ALIPAY", "No stored secret {ALIPAY}"),
    ("Pay Alice, password {WECHAT_PAY}", "DO: secret WECHAT_PAY\nDO: tap 50 50\nDO: secret WECHAT_PAY",
     "entered once"),
])
def test_stops_instead_of_guessing(task, reply, why):
    agent, _ = _agent(Model("THINK: password\n" + reply))
    _run(agent, task)
    last = agent.steps[-1]
    # handed over only when the secret is simply not stored; nobody may be at the phone for anything else
    assert last["action"] == ("ask" if "No stored" in why else "fail") and why in last["reason"]
    assert len(agent.phone.typed) == (1 if "entered once" in why else 0)


def test_keypad_refuses_a_secret_that_is_not_digits():
    agent, _ = _agent(Model("THINK: keypad\nDO: keypad WECHAT_PAY 0 120 90 200"),
                      env={"HIDAGENT_SECRET_WECHAT_PAY": "letters!"})
    _run(agent, "Pay with {WECHAT_PAY}")
    assert agent.steps[-1]["action"] == "fail" and agent.phone.taps == []


def test_tasks_with_secrets_are_never_learned(tmp_path):
    routines = Routines(tmp_path / "routines.json")
    agent, _ = _agent(Model("THINK: tap\nDO: tap 50 100", "THINK: keypad\nDO: keypad WECHAT_PAY 0 120 90 200",
                            "THINK: paid\nDO: done"), routines=routines)
    _run(agent, "Pay Alice, password {WECHAT_PAY}")
    assert agent.steps[-1]["action"] == "done"
    assert routines.data == {}
    routines.learn("Pay Bob", [{"action": "keypad", "name": "WECHAT_PAY", "x1": 0, "y1": 0, "x2": 1, "y2": 1}], "")
    routines.learn("Pay Bob {WECHAT_PAY}", [{"action": "tap", "x": 1, "y": 2}], "")
    assert routines.data == {}


def test_log_lines_are_redacted():
    v = Vault({"HIDAGENT_SECRET_WECHAT_PAY": PIN})
    v.load()
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "typed %s now", (PIN,), None)
    assert RedactFilter(v).filter(record) and record.getMessage() == "typed {WECHAT_PAY} now"


def test_device_does_not_log_while_hushed(caplog):
    from hidagent.device import Device

    class Writer:
        def write(self, data):
            pass

        def is_closing(self):
            return False

        async def drain(self):
            pass

    class Reader:
        async def readline(self):
            return b"ok\n"

    async def go():
        device = Device()
        device._reader, device._writer = Reader(), Writer()
        with caplog.at_level(logging.DEBUG, logger="hidagent.device"):
            await device.send("tap 1")
            with vault.hush():
                await device.send(f"type {PIN}")
    asyncio.run(go())
    assert "tap 1" in caplog.text and PIN not in caplog.text


def test_a_task_with_a_secret_does_not_replay_a_routine(tmp_path):
    routines = Routines(tmp_path / "routines.json")
    routines.data[normalize("Pay Alice, password {WECHAT_PAY}")] = {
        "task": "Pay Alice, password {WECHAT_PAY}", "steps": [{"action": "type", "text": "old password"}],
        "answer": "", "ok": 3, "fails": 0}
    actor = Recorder("THINK: paid\nDO: done")
    agent, _ = _agent(actor, routines=routines)
    _run(agent, "Pay Alice, password {WECHAT_PAY}")
    assert agent.phone.typed == [] and actor.prompts


@pytest.mark.parametrize("text, found", [
    ("转给张三 ¥1,200.5 和 30元", [1200.5, 30]), ("Pay Alice 5 yuan, $7 or RMB 9", [5, 7, 9]),
    ("50块钱", [50]), ("password {WECHAT_PAY}, 2026年 3 times", []),
])
def test_amounts_need_a_currency(text, found):
    assert vault.amounts(text) == found


@pytest.mark.parametrize("task, refused", [
    ("Pay Alice ¥80 with {WECHAT_PAY}", None),
    ("Pay Alice ¥80 and then 300元 with {WECHAT_PAY}", "300 is above the 100 cap"),
    ("Pay Alice with {WECHAT_PAY}", "say the amount"),
    ("Pay Alice ¥500 with {ALIPAY}", None),  # no cap on that one
])
def test_a_capped_secret_needs_an_amount_within_its_cap(task, refused):
    agent, _ = _agent(Model("THINK: paid\nDO: done"),
                      env={"HIDAGENT_SECRET_WECHAT_PAY": PIN, "HIDAGENT_SECRET_WECHAT_PAY_MAX": "100",
                           "HIDAGENT_SECRET_ALIPAY": "135790"})
    if refused is None:
        _run(agent, task)
        assert agent.steps[-1]["action"] == "done"
    else:
        with pytest.raises(RuntimeError, match=refused):
            agent.start(task)
        assert not agent.running


def test_an_unreadable_cap_refuses_everything():
    v = Vault({"HIDAGENT_SECRET_WECHAT_PAY": PIN, "HIDAGENT_SECRET_WECHAT_PAY_MAX": "a lot"})
    v.load()
    assert "above the 0 cap" in v.over_limit("Pay ¥1 with {WECHAT_PAY}")


# A WeChat Pay sheet as the mirror shows it: 500x1080, the keypad at the bottom, six password boxes above it.
KEYS = (0, 774, 500, 1040)
W, H = 500, 1080


def _sheet(dots: int = 0, other: bool = False) -> bytes:
    img = Image.new("L", (W, H), 245)
    draw = ImageDraw.Draw(img)
    if other:  # the payment went through: another screen
        draw.rectangle((0, 0, W, 800), fill=60)
        draw.text((250, 900), "Paid", fill=0, font=ImageFont.load_default(size=60), anchor="mm")
    else:
        draw.rectangle((0, 300, W, H), fill=250)
        draw.rectangle((80, 696, 470, 740), outline=190)
        for k in range(min(dots, 6)):
            cx = 80 + (k + 0.5) * 390 / 6
            draw.ellipse((cx - 6, 712, cx + 6, 724), fill=10)
        draw.rectangle(KEYS, fill=228)
        font = ImageFont.load_default(size=34)
        cw, ch = (KEYS[2] - KEYS[0]) / 3, (KEYS[3] - KEYS[1]) / 4
        for i, label in enumerate("123456789 0<"):
            x, y = KEYS[0] + (i % 3 + 0.5) * cw, KEYS[1] + (i // 3 + 0.5) * ch
            if label == " ":
                continue
            draw.rectangle((x - cw / 2 + 3, y - ch / 2 + 3, x + cw / 2 - 3, y + ch / 2 - 3), fill=255)
            if label == "<":  # the delete key
                draw.polygon([(x - 22, y), (x - 10, y - 12), (x + 20, y - 12), (x + 20, y + 12), (x - 10, y + 12)],
                             outline=20, width=3)
            else:
                draw.text((x, y), label, fill=20, font=font, anchor="mm")
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()


class SheetScreen(Screen):
    aspect = W / H

    def __init__(self):
        super().__init__(_sheet())

    async def fresh(self, since, timeout=0.8):
        return self.frame


class SheetPhone(Phone):
    """Presses land on the sheet's keys: a digit adds a dot, delete takes one off, the sixth dot pays."""

    def __init__(self, screen: SheetScreen, drop=(), double=(), pays=6):
        super().__init__(screen)
        self.dots, self.presses, self.keys, self.pays = 0, 0, [], pays
        self.drop, self.double = list(drop), list(double)  # digit presses (counted from 1) that miss or count twice

    async def tap(self, x, y, aim=True):
        await super().tap(x, y, aim)
        col = int((x * W - KEYS[0]) / ((KEYS[2] - KEYS[0]) / 3))
        row = int((y * H - KEYS[1]) / ((KEYS[3] - KEYS[1]) / 4))
        if not (0 <= col < 3 and 0 <= row < 4) or self.dots >= self.pays:  # off the keys, or paid and gone
            return
        key = "123456789 0<"[row * 3 + col]
        self.keys.append(key)
        if key == "<":
            self.dots = max(0, self.dots - 1)
        elif key != " ":
            self.presses += 1
            if self.presses in self.drop:
                self.drop.remove(self.presses)
            else:
                self.dots += 2 if self.presses in self.double else 1
        self.screen.frame = _sheet(other=True) if self.dots >= self.pays else _sheet(self.dots)


def _sheet_agent(*replies, drop=(), double=(), pays=6, trace=None):
    screen = SheetScreen()
    agent = Agent(SheetPhone(screen, drop, double, pays), screen, Model(*replies), Model("1. Pay"),
                  trace or Trace(None), vault=Vault({"HIDAGENT_SECRET_WECHAT_PAY": PIN}))
    return agent


def _close(found, want, tolerance=0.012):
    return found is not None and all(abs(f - w) <= tolerance for f, w in zip(found, want, strict=True))


def test_keypad_grid_finds_the_keys_from_a_rough_box():
    frame, keys = _sheet(), (KEYS[0] / W, KEYS[1] / H, KEYS[2] / W, KEYS[3] / H)
    assert _close(vision.keypad_grid(frame, keys), keys)
    assert _close(vision.keypad_grid(frame, (0.03, 0.70, 0.97, 0.97)), keys)  # loose
    assert _close(vision.keypad_grid(frame, (0, 0.74, 1, 0.98)), keys)  # a quarter key low
    assert vision.keypad_grid(frame, (0, 0.1, 1, 0.4)) is None  # the page above it
    assert vision.keypad_grid(_sheet(other=True), keys) is None  # no keypad on the screen


def test_dots_counts_the_new_password_dots():
    band = (0.6, 0.71)
    assert [vision.dots(_sheet(), _sheet(k), band) for k in range(4)] == [0, 1, 2, 3]
    assert vision.dots(_sheet(2), _sheet(5), band) == 3
    assert vision.dots(_sheet(), _sheet(other=True), band) >= 99  # a new screen is never a few dots


PAY = ("THINK: the keypad is up\nDO: keypad WECHAT_PAY 10 760 490 1030", "THINK: paid\nDO: done")


def test_keypad_finds_the_keys_and_checks_every_dot(tmp_path):
    agent = _sheet_agent(*PAY, trace=Trace(tmp_path))
    _run(agent, "Pay Alice ¥1 with {WECHAT_PAY}")
    assert agent.steps[-1]["action"] == "done"
    assert agent.phone.keys == ["<"] * 6 + list(PIN)  # the field is cleared, then each digit once
    assert not any(agent.phone.loud)
    assert agent.phone.points[-1][1] < 0.6  # resting above the keypad and its dots: no digit under the pointer
    keypad = next(s for s in agent.steps if s["action"] == "keypad")
    assert keypad["y1"] == pytest.approx(774 / 1.08, abs=2) and keypad["y2"] == pytest.approx(1040 / 1.08, abs=2)
    lines = [json.loads(line) for f in tmp_path.rglob("steps.jsonl") for line in f.read_text().splitlines()]
    used = [r for r in lines if r.get("kind") == "secret"]
    assert [(r["name"], r["outcome"]) for r in used] == [("WECHAT_PAY", "entered")]
    assert PIN not in json.dumps(lines) and PIN not in json.dumps(agent.steps)


def test_a_missed_key_clears_the_field_and_tries_once_more(monkeypatch):
    monkeypatch.setattr(agent_module, "DOT_WAIT", 0.05)
    agent = _sheet_agent(*PAY, drop=[3])
    _run(agent, "Pay Alice ¥1 with {WECHAT_PAY}")
    assert agent.steps[-1]["action"] == "done"
    assert agent.phone.keys == ["<"] * 6 + list(PIN[:3]) + ["<"] * 6 + list(PIN)


def test_an_extra_dot_also_counts_as_a_miss(monkeypatch):
    monkeypatch.setattr(agent_module, "DOT_WAIT", 0.05)
    agent = _sheet_agent(*PAY, double=[2])
    _run(agent, "Pay Alice ¥1 with {WECHAT_PAY}")
    assert agent.steps[-1]["action"] == "done"
    assert agent.phone.keys == ["<"] * 6 + list(PIN[:2]) + ["<"] * 6 + list(PIN)


def test_a_second_miss_ends_the_task(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "DOT_WAIT", 0.05)
    agent = _sheet_agent(*PAY, drop=[2, 4], trace=Trace(tmp_path))
    _run(agent, "Pay Alice ¥1 with {WECHAT_PAY}")
    last = agent.steps[-1]
    assert last["action"] == "fail" and "did not go in" in last["reason"]
    assert agent.phone.keys == ["<"] * 6 + list(PIN[:2]) + ["<"] * 6 + list(PIN[:2])  # no third try
    assert agent.actor.calls == 1  # nothing after the fail
    lines = [json.loads(line) for f in tmp_path.rglob("steps.jsonl") for line in f.read_text().splitlines()]
    assert [r["outcome"] for r in lines if r.get("kind") == "secret"][-1].startswith("try 2")
    assert PIN not in json.dumps(lines)


def test_a_box_with_no_keypad_is_sent_back_without_a_press():
    agent = _sheet_agent("THINK: keypad\nDO: keypad WECHAT_PAY 0 100 500 400", *PAY)
    _run(agent, "Pay Alice ¥1 with {WECHAT_PAY}")
    assert agent.steps[-1]["action"] == "done"
    assert any(s["action"] == "hiccup" and "no 3x4 digit keypad" in s.get("reason", "") for s in agent.steps)
    assert agent.phone.keys == ["<"] * 6 + list(PIN)  # the first box pressed nothing, and did not use it up


def test_a_sheet_that_goes_away_half_way_is_never_pressed_again():
    agent = _sheet_agent(*PAY, pays=3)  # the app takes 3 digits and moves on
    _run(agent, "Pay Alice ¥1 with {WECHAT_PAY}")
    last = agent.steps[-1]
    assert last["action"] == "fail" and "screen changed after 3 of 6" in last["reason"]
    assert agent.phone.keys == ["<"] * 6 + list(PIN[:3])  # no delete on whatever came up


def test_a_field_that_does_not_clear_stops_the_retry(monkeypatch):
    monkeypatch.setattr(agent_module, "DOT_WAIT", 0.05)
    agent = _sheet_agent(*PAY, drop=[3])

    async def stuck(grid, presses):  # the delete key does nothing
        pass
    monkeypatch.setattr(agent, "_clear_keypad", stuck)
    _run(agent, "Pay Alice ¥1 with {WECHAT_PAY}")
    last = agent.steps[-1]
    assert last["action"] == "fail" and "did not clear" in last["reason"]
    assert agent.phone.keys == list(PIN[:3])  # nothing typed onto the two dots left over


def test_no_screenshot_is_saved_while_a_secret_goes_in(tmp_path):
    trace = Trace(tmp_path)
    trace.start("t")
    trace.write(_frame(), kind="llm")
    with vault.hush():
        trace.write(_frame(), kind="llm")
    assert len(list(tmp_path.rglob("*.jpg"))) == 1
