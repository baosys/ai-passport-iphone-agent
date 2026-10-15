import asyncio
import json
import logging

import pytest
from test_routines import Model, Screen, _frame, _run

from hidagent import vault
from hidagent.agent import Agent, parse_command
from hidagent.debug import Trace
from hidagent.routines import Routines, normalize
from hidagent.vault import RedactFilter, Vault

PIN = "246810"


class Phone:
    def __init__(self, screen: Screen):
        self.screen, self.taps, self.typed, self.points, self.loud = screen, [], [], [], []

    async def tap(self, x, y):
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


def test_keypad_taps_each_digit_quietly_then_moves_away():
    agent, _ = _agent(Model("THINK: the keypad is up\nDO: keypad WECHAT_PAY 0 120 90 200", "THINK: paid\nDO: done"))
    _run(agent, "Pay Alice, password {WECHAT_PAY}")
    # screenshot = 100x200 px, so the actor's pixels scale by 10 and 5 into 0-1000 units
    assert agent.phone.taps == [tuple(round(c, 3) for c in s) for s in vault.keypad(PIN, (0, 0.6, 0.9, 1.0))]
    assert not any(agent.phone.loud)
    x, y = agent.phone.points[-1]
    assert y < 0.6  # above the keypad: the last digit pressed is not under the pointer
    assert PIN not in json.dumps(agent.steps)


@pytest.mark.parametrize("task, reply, why", [
    ("Pay Alice, password {WECHAT_PAY}", "DO: secret ALIPAY", "names no {ALIPAY}"),
    ("Pay Alice, password {ALIPAY}", "DO: secret ALIPAY", "No stored secret {ALIPAY}"),
    ("Pay Alice, password {WECHAT_PAY}", "DO: secret WECHAT_PAY\nDO: tap 50 50\nDO: secret WECHAT_PAY",
     "entered once"),
])
def test_hands_over_instead_of_guessing(task, reply, why):
    agent, _ = _agent(Model("THINK: password\n" + reply))
    _run(agent, task)
    last = agent.steps[-1]
    assert last["action"] == "ask" and why in last["reason"]
    assert len(agent.phone.typed) == (1 if "entered once" in why else 0)


def test_keypad_refuses_a_secret_that_is_not_digits():
    agent, _ = _agent(Model("THINK: keypad\nDO: keypad WECHAT_PAY 0 120 90 200"),
                      env={"HIDAGENT_SECRET_WECHAT_PAY": "letters!"})
    _run(agent, "Pay with {WECHAT_PAY}")
    assert agent.steps[-1]["action"] == "ask" and agent.phone.taps == []


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
