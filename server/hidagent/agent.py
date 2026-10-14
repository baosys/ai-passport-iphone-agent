"""See-think-act loop: a planner model writes the plan, an actor model picks each step."""

import asyncio
import base64
import itertools
import json
import logging
import math
import re
import time

from . import shortcuts, vision
from .debug import Trace
from .device import DeviceError
from .jev import NO, SURE, Jev
from .llm import LLM
from .phone import Phone
from .routines import Routines, asks, locate, look, patch, same, target
from .screen import Screen, frame_size

log = logging.getLogger(__name__)

PLANNER_PROMPT = """You plan tasks on an iPhone. Given the goal and the current screen,
write a short numbered plan (at most 6 steps) of what to do on screen. Plain text only, in English.
Plan only what the goal needs, from what you can see; never invent accounts, emails, logins or
screens you have not seen. Later steps can stay general (e.g. "Draw the face on the canvas").
The phone has a hardware keyboard, and keys are much faster than taps: plan with them where they work.
To open an app, write "Open <app name>": the agent opens apps through search in one step, never by paging
through home screens. Going to the home screen is one fast step too.
When the goal reads information in one app and sends it in another (e.g. look up the weather, then message it to
someone), plan the reading first, with a step to note what was read, then the sending."""

ACTOR_PROMPT = """You operate an iPhone through a mouse pointer and a hardware keyboard. You see the current screen.
Reply in this format, nothing else:
PAGE: <the app and page on screen, in a few words, e.g. "Settings > General" or "WeChat chat with Zhang San">
THINK: <plain English a non-technical user understands: first check your last action against the
       screen (did it do what you intended?), then what you do now and why>
DO: <action>

Give several DO lines (up to 6, run in order) whenever the next actions do not depend on seeing the
result: e.g. ticking several visible checkboxes or days, several drawing strokes, typing then pressing enter.
Give ONE DO line when the next action depends on what this one opens or changes.

Coordinates are integer pixels of the screenshot (x left->right, y top->bottom); its size is given with each request.
Aim at the visual center of the element you want, not its edge or its label.
Look before you act: describe what is really on the screen now, not what you expect.
Each recent action says whether the screen changed after it. If it did NOT change, the action
missed or had no effect: do not assume it worked; aim again more precisely or try another way.
Keys are fast: no pointer travel, no aiming. A tap costs a few seconds. Whenever keys reach the same result,
use them instead of taps:
- Open any app: open NAME (one DO line, from any screen: it goes home, searches NAME and opens the top result).
  Letters type fastest: use the English name (open Settings, open WeChat), or pinyin for a Chinese-only name.
- Go to the home screen: home (keyboard, fast).
- A focused text field (blinking cursor, or highlighted) takes typing directly: do not tap it first.
- key esc closes menus and many alerts; check the result. In search, the first esc clears the text, the second
  closes search.
- Full Keyboard Access: if a colored focus ring outlines one item, the user turned it on (Settings ->
  Accessibility -> Keyboards). Then key tab / shift+tab moves the ring to the next / previous item, arrow keys
  move it within a group, and key space activates the ringed item. Use it when the target is a move or two
  from the ring. With no ring, or when key tab changed nothing, it is off: tap instead.
The agent only remembers recent actions, not earlier screens. When the goal needs something you read here later
(e.g. read the weather, then message it to someone), keep it with note TEXT before you leave the screen: notes stay
with you for the whole task. Write the facts themselves (dates, numbers, names, steps), in the language the message
will use, not a description of the screen.
The on-screen keyboard never appears, so its keys (Send, Search, Go, Done) are missing: press them with
key enter. To send a chat message (WeChat, Messages, ...), type it and then key enter; do not look for a send button.
Type the whole message in ONE type action on one line (separate points with ；or ; — a line break would send it
early), composed from your notes. After key enter, check that it shows as a sent bubble in the chat.
Swipes have no momentum: content moves exactly as far as the finger and stops. Prefer short swipes
(a tenth to a quarter of the screen height) and check the result; repeat if needed rather than one big swipe.
Time or number wheels (e.g. an alarm's time): first tap the number in the wheel's center row and type
the digits (e.g. type 0700); check the result. Only if typing does not work, swipe inside that column:
measure the row spacing from two neighbouring numbers and swipe k times that spacing to move k rows.
Looking for an item in a list: scroll less than one screen at a time so nothing is skipped, and read every
visible item. If you reached both ends of the list and the item is not there, stop with fail and name the
closest options you saw; do not keep scrolling.
Only tap things you can actually see on this screenshot; never guess where a button might be.
If you are unsure what an element does, tap it alone (one DO line) and check the result next round.
Actions:
  tap X Y
  swipe X1 Y1 X2 Y2         (finger moves from X1,Y1 to X2,Y2: "swipe down" means Y2 > Y1)
  path X1 Y1 X2 Y2 X3 Y3 …  (one continuous stroke through 2-20 points, for drawing)
  circle X Y R              (draw a circle centered at X Y; R is the radius in pixels)
  type TEXT                 (any text, Chinese too, into the focused field; if no field is focused, tap it first)
  key COMBO                 (one of: enter esc backspace tab space up down left right, or a combo
                             joined with +: cmd+space, cmd+h, cmd+a, shift+tab)
  open NAME                 (open an app by name through search)
  note TEXT                 (keep facts read off the screen for later steps; does nothing on the phone)
  home | back | wait        (home: the home screen, by keyboard; back: a swipe in from the left edge)
  replan                    (the plan no longer fits the screen)
  done ANSWER               (the goal is reached; ANSWER is the result for the user)
  fail REASON

Example:
PAGE: Home screen
THINK: My last action showed the home screen as intended. I open the App Store by name, which is faster than
finding its icon.
DO: open App Store"""

# not part of the action
META = ("t", "thought", "thought_zh", "page", "think_s", "act_s", "settle_s", "shot", "_shot", "changed", "seen",
        "found", "tier")
REMEMBERED = ("cue", "look", "page")  # what a routine keeps about a step besides the action

# action name -> argument names; numeric arguments are screen coordinates: 0-1000 inside the agent, screenshot
# pixels when talking to the actor (most vision models answer in pixels whatever the prompt asks for)
ACTIONS = {"tap": ("x", "y"), "swipe": ("x1", "y1", "x2", "y2"), "circle": ("x", "y", "r"), "path": ("points",),
           "type": ("text",), "key": ("combo",), "open": ("app",), "note": ("text",), "done": ("answer",),
           "fail": ("reason",),
           "home": (), "back": (), "wait": (), "replan": ()}
TEXT_ARGS = ("text", "combo", "app", "answer", "reason")
NO_PHONE = ("note", "done", "fail")  # actions that do nothing on the phone
# who decided a step, cheapest first: plain code (shortcuts), a remembered routine, Jev, the actor, the planner
TIERS = ("code", "routine", "jev", "actor", "planner")

# key names both boards know (hardware/ai_passport/main/agent_keymap.c, hardware/hid_bridge/hid_bridge.ino);
# a single printable ASCII character (h, a, 1, ...) also works. "delete" is AI Passport only.
KEY_NAMES = frozenset({"cmd", "ctrl", "alt", "shift", "enter", "esc", "tab", "backspace", "delete", "space",
                       "up", "down", "left", "right"})
KEY_ALIASES = {"return": "enter", "escape": "esc", "command": "cmd", "⌘": "cmd", "control": "ctrl",
               "option": "alt", "opt": "alt", "⌥": "alt", "⇧": "shift", "spacebar": "space",
               "uparrow": "up", "downarrow": "down", "leftarrow": "left", "rightarrow": "right",
               "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right"}


def parse_combo(text: str) -> str:
    """Normalize a key combo for the board, e.g. "Cmd + Space" -> "cmd+space"; unknown keys raise ValueError."""
    keys = []
    for token in text.strip().strip('"').lower().split("+"):
        token = re.sub(r"\s+", "", token)
        token = KEY_ALIASES.get(token, token)
        if token not in KEY_NAMES and not (len(token) == 1 and " " < token < "\x7f"):
            raise ValueError(f"unknown key {token!r} in: {text[:100]}")
        keys.append(token)
    return "+".join(keys)


def parse_command(line: str) -> dict:
    """One DO line, e.g. "tap 152 362" -> {"action": "tap", "x": 152, "y": 362}."""
    name, _, rest = line.strip().partition(" ")
    name = name.lower()
    if name not in ACTIONS:
        raise ValueError(f"unknown action: {line[:100]}")
    keys = ACTIONS[name]
    if name == "key":
        return {"action": name, "combo": parse_combo(rest)}
    if keys and keys[0] in TEXT_ARGS:
        return {"action": name, keys[0]: rest.strip().strip('"')}
    numbers = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", rest)]
    if keys == ("points",):
        if len(numbers) < 4 or len(numbers) % 2:
            raise ValueError(f"path needs 2+ x y pairs: {line[:100]}")
        return {"action": name, "points": [numbers[i:i + 2] for i in range(0, len(numbers), 2)][:20]}
    if len(numbers) != len(keys):
        raise ValueError(f"{name} needs {len(keys)} numbers: {line[:100]}")
    action = {"action": name, **dict(zip(keys, numbers, strict=True))}
    if name == "tap" and "precise" in rest.lower():
        action["precise"] = True
    return action


def parse_actions(text: str) -> list[dict]:
    """Parse a [PAGE /] THINK / DO… reply (an optional ZH line is kept); the thought goes on the first action, the
    page (what the actor saw on screen) on all of them."""
    thought = re.search(r"^\s*THINK\s*[:：]\s*(.*?)\s*$", text, re.M | re.I)
    page = re.search(r"^\s*PAGE\s*[:：]\s*(.*?)\s*$", text, re.M | re.I)
    zh = re.search(r"^\s*ZH\s*[:：]\s*(.*?)\s*$", text, re.M | re.I)
    acts = [parse_command(line) for line in re.findall(r"^\s*DO\s*[:：]\s*(.*?)\s*$", text, re.M | re.I)][:6]
    if not acts:
        raise ValueError(f"no DO line in reply: {text[:200]}")
    acts[0] = {"thought": thought.group(1) if thought else "", "thought_zh": zh.group(1) if zh else "", **acts[0]}
    if page and page.group(1):
        acts = [{**a, "page": page.group(1)[:100]} for a in acts]
    return acts


def parse_plan(text: str) -> tuple[str, str]:
    """Split an optional EN: / ZH: plan reply; a plain reply is used for both."""
    m = re.search(r"EN\s*[:：](.*?)ZH\s*[:：](.*)", text, re.S | re.I)
    return (m.group(1).strip(), m.group(2).strip()) if m else (text.strip(), text.strip())


def scale(act: dict, sx: float, sy: float) -> dict:
    """The action with its x coordinates (and circle radius) times sx and its y coordinates times sy."""
    out = dict(act)
    for k in ("x", "x1", "x2", "r", "y", "y1", "y2"):
        if k in out:
            out[k] = round(out[k] * (sy if k.startswith("y") else sx))
    if "points" in out:
        out["points"] = [[round(x * sx), round(y * sy)] for x, y in out["points"]]
    return out


def _key(step: dict) -> dict:
    """The action itself, without thoughts, timings and other bookkeeping."""
    return {k: v for k, v in step.items() if k not in META}


class BlindError(RuntimeError):
    """The screen feed is gone; acting now would be guessing."""


class Agent:
    def __init__(self, phone: Phone, screen: Screen, actor: LLM, planner: LLM, trace: Trace,
                 max_steps: int = 100, max_failures: int = 3,  # max_steps 0: until done, fail or stop
                 routines: Routines | None = None, jev: Jev | None = None):
        self.phone, self.screen, self.actor, self.planner = phone, screen, actor, planner
        self.routines = routines or Routines(None)
        self.jev = jev or Jev(None)
        self.trace = trace
        self.max_steps, self.max_failures = max_steps, max_failures
        self.task, self.plan, self.steps = "", "", []
        self.shots: dict[int, bytes] = {}  # step index -> screenshot the model decided on
        self._pointer = (0.0, 0.0)  # last pointer position (screen fractions)
        self._job: asyncio.Task | None = None
        self._quick: list[dict] = []  # leading actions of the task that need no model (see shortcuts.py)
        self._replayed: list[dict] = []  # actions replayed from a remembered routine (see routines.py)
        self._rest = ""  # the part of the task the shortcuts leave to the models
        self._routine: dict | None = None  # the remembered routine this task means
        self._asks = False  # the task wants an answer read off the screen
        self._jev_s = 0.0  # seconds Jev spent on the triage

    @property
    def running(self) -> bool:
        return self._job is not None and not self._job.done()

    def start(self, task: str) -> None:
        if self.running:
            raise RuntimeError("agent already running")
        quick, rest = shortcuts.split(task)
        if rest and not self.screen.live:  # keyboard shortcuts alone work blind; the models need to see
            raise RuntimeError("no phone screen: start Screen Mirroring first")
        self.task, self.plan, self.steps, self.shots = task, "", [], {}
        self._quick, self._replayed, self._rest = quick, [], rest
        self._routine, self._asks = self.routines.find(task), asks(task)
        self._pointer, self._jev_s = (0.0, 0.0), 0.0
        self.trace.start(task)
        self._job = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self.running:
            self._job.cancel()

    def clear(self) -> None:
        if self.running:
            raise RuntimeError("stop the agent first")
        self.task, self.plan, self.steps, self.shots = "", "", [], {}

    def _record(self, shot: bytes | None = None, **step) -> None:
        if shot:
            self.shots[len(self.steps)] = shot
        self.steps.append({"t": time.time(), "shot": bool(shot), **step})
        self.trace.write(kind="step", **step)
        log.info("agent: %s", step)

    async def _ask(self, role: str, llm: LLM, system: str, prompt: str, temperature: float = 0,
                   max_tokens: int = 600) -> tuple[str, float, bytes | None]:
        if not self.screen.live:  # never let the model act blind
            raise BlindError("lost the phone screen: reconnect Screen Mirroring")
        frame, started = self.screen.frame, time.time()
        reply = await llm.chat(system, prompt, frame, max_tokens=max_tokens, temperature=temperature)
        seconds = round(time.time() - started, 2)
        self.trace.write(frame, kind="llm", role=role, model=llm.model, prompt=prompt,
                         reply=reply, seconds=seconds)
        return reply, seconds, frame

    async def _run(self) -> None:
        failures = 0
        try:
            await self._triage()
            if await self._shortcuts() or await self._replay():
                return
            await self._replan()
            for _ in range(self.max_steps) if self.max_steps > 0 else itertools.count():
                try:
                    if await self._step():
                        self._learn()
                        return
                    failures = 0
                except BlindError:
                    raise
                except Exception as e:  # a bad reply or a dropped link: recover, don't crash
                    self._record(action="hiccup", reason=str(e)[:300])
                    board_drop = isinstance(e, DeviceError) and await self.phone.device.wait_connected(15)
                    failures += 0 if board_drop else 1  # the board came back: not the agent's fault
                    if failures >= self.max_failures:
                        self._record(action="fail", reason=f"{failures} errors in a row")
                        return
                    await self.screen.settled()
                    await self._replan(stuck=True)
            self._record(action="fail", reason="step limit reached")
        except asyncio.CancelledError:
            self._record(action="stopped")
        except Exception as e:  # e.g. the planner itself is unreachable
            self._record(action="error", reason=repr(e))
        finally:
            self._tally()

    def _tally(self) -> None:
        """Log and trace which tier decided how many steps and how long they took, and whether a replay or a
        shortcut had to hand over to the models."""
        tiers: dict[str, dict] = {}
        for s in self.steps:
            if tier := s.get("tier"):
                t = tiers.setdefault(tier, {"steps": 0, "seconds": 0.0})
                t["steps"] += 1
                t["seconds"] = round(t["seconds"] + sum(s.get(k, 0) for k in ("think_s", "act_s", "settle_s")), 2)
        if self._jev_s:
            tiers.setdefault("jev", {"steps": 0, "seconds": 0.0})["seconds"] += round(self._jev_s, 2)
        last = self.steps[-1]["action"] if self.steps else None
        # the cheap tiers started the task but the models had to finish it
        escalated = "planner" in tiers and bool(self._quick or self._replayed or self._routine)
        self.trace.write(kind="tiers", tiers=tiers, escalated=escalated, outcome=last)
        log.info("agent: tiers %s, escalated=%s, outcome %s", tiers, escalated, last)

    async def _triage(self) -> None:
        """Let Jev decide what needs no model: shortcut clauses, a routine remembered under other words, and
        whether the task asks a question. Without Jev the regex reading made in start() stands."""
        if not self.jev.enabled:
            return
        clauses = shortcuts.clauses(self.task)
        remembered = [] if self._routine else self.routines.tasks()
        started = time.time()
        t = await self.jev.triage(self.task, clauses, remembered)
        self._jev_s = time.time() - started
        if t is None:
            return
        quick = []
        for clause, kind in zip(clauses, t.kinds, strict=False):  # clauses past MAX_CLAUSES: "other"
            if (act := shortcuts.parse(clause, kind)) is None:
                break
            quick.append(act)
        self._quick, self._asks = quick, t.asks
        self._rest = "" if len(quick) == len(clauses) else "，".join(clauses[len(quick):])
        if self._routine is None and t.routine:
            self._routine = self.routines.find(t.routine)
        if self._rest and not self.screen.live:
            raise BlindError("no phone screen: start Screen Mirroring first")
        log.info("agent: jev triage in %.2fs: %s, rest %r, routine %r", time.time() - started, quick, self._rest,
                 self._routine and self._routine["task"])

    async def _shortcuts(self) -> bool:
        """Run the task's shortcut actions without asking a model. Returns True when they were the whole task."""
        for act in self._quick:
            started = time.time()
            await self._do(act)
            self._record(**act, tier="code", thought="Shortcut: done by keyboard without asking the model.",
                         thought_zh="快捷操作：直接用键盘完成，没有调用模型。", act_s=round(time.time() - started, 2))
        if not self._quick or self._rest:
            if self._quick and self.screen.live:
                await self._settle()  # let the app come up before the planner looks
            return False
        self._record(action="done", answer="", tier="code")
        return True

    async def _replay(self) -> bool:
        """Replay the routine remembered for this task, checking each press against last time.

        Returns True when the replay finished the task; False when there is none, when the screen differs from
        last time (the models take over from there), or when the task asks a question that needs a fresh look.
        """
        routine = self._routine
        if routine is None:
            return False
        steps = routine["steps"]
        for n, step in enumerate(steps):
            act = {k: v for k, v in step.items() if k not in REMEMBERED}
            frame, found = None, False
            if "cue" in step:
                page = self._ask_page(step.get("page"))  # asked while the pixels are checked
                try:
                    frame = await self._looks_like(step["cue"], target(act), page)
                    if (p := page and await page) is not None and p < NO:
                        return self._resume(f"step {n + 1} of {len(steps)}: Jev sees another page than "
                                            f"{step['page']!r}; the model takes over")
                finally:
                    if page:
                        page.cancel()
                if frame is None and (moved := await self._find_again(step, act)):
                    act, frame, found = *moved, True
                if frame is None:
                    return self._resume(f"step {n + 1} of {len(steps)} looks different from last time; the model "
                                        "takes over")
            started = time.time()
            await self._do(act)
            if found:
                self._record(frame, **act, seen=True, found=True, tier="jev", act_s=round(time.time() - started, 2),
                             thought="Replay: the button had moved; Jev found it again, and its pixels match.",
                             thought_zh="重放：按钮换了位置，Jev 重新找到了它，像素也对得上。", **_page(step))
            else:
                self._record(frame, **act, seen=frame is not None, tier="routine",
                             act_s=round(time.time() - started, 2),
                             thought="Replay: done the way that worked last time, without asking the model.",
                             thought_zh="重放：照上次成功的做法，没有调用模型。", **_page(step))
            self._replayed.append(act)
            if path := _pointer_path(act):
                self._pointer = path[-1]
            following = steps[n + 1]["action"] if n + 1 < len(steps) else None
            if following is None:
                break  # the last step: nothing more to check before it
            if act["action"] == "key" and following == "type":
                await self._settle(quiet=0.15, least=0.1, most=1.0)
            elif act["action"] not in ("type", "key") or following not in ("type", "key"):
                await self._settle()
        if self._asks:  # the answer has to be read off today's screen, once it is there
            await self._settle()
            return False
        if self.jev.enabled:  # a quick look whether the steps did it; only a clear no hands over
            await self._settle()
            if (frame := self.screen.frame) and (p := await self.jev.done(frame, self.task)) is not None and p < NO:
                return self._resume(f"all {len(steps)} steps ran, but Jev sees the task not done; the model takes "
                                    "over")
        self.routines.replayed(routine["task"])
        self._record(action="done", answer=routine["answer"], tier="routine")
        return True

    def _resume(self, reason: str) -> bool:
        """Stop replaying: the routine broke, and the models take over from the current screen."""
        self.routines.broke(self._routine["task"])
        self._record(action="resume", reason=reason, tier="routine")
        return False

    def _ask_page(self, page: str | None) -> asyncio.Task | None:
        """Start asking Jev whether the screen shows `page` (the page a remembered step ran on)."""
        if not page or not self.jev.enabled or not (frame := self.screen.frame):
            return None
        return asyncio.create_task(self.jev.page(frame, page))

    async def _looks_like(self, cue: list[int], at: tuple[float, float], page: asyncio.Task | None = None,
                          tries: int = 3) -> bytes | None:
        """The current frame if the spot about to be pressed looks as it did last time, waiting a little for it.

        `page`, Jev's answer whether this is the right page, cuts the wait short when it is a clear no.
        """
        for attempt in range(tries):
            frame = self.screen.frame
            if frame and same(patch(frame, at), cue):
                return frame
            if attempt < tries - 1:
                started = time.time()
                if page is not None and not page.done():
                    await asyncio.wait({page}, timeout=0.5)
                if page is not None and page.done() and (p := page.result()) is not None and p < NO:
                    return None
                await asyncio.sleep(max(0.0, 0.5 - (time.time() - started)))
                await self._settle()
        return None

    async def _find_again(self, step: dict, act: dict) -> tuple[dict, bytes] | None:
        """The action moved to where Jev finds the remembered control now, if the pixels there match last time."""
        if not self.jev.enabled or "look" not in step or not (frame := self.screen.frame):
            return None
        box = await self.jev.find(frame, base64.b64decode(step["look"]))
        old = target(act)
        if box is None or (spot := locate(frame, step["cue"], box, old[0])) is None:
            return None
        return _moved(act, round((spot[0] - old[0]) * 1000), round((spot[1] - old[1]) * 1000)), frame

    def _learn(self) -> None:
        """Remember how a finished task was done, for replaying it next time."""
        last = self.steps[-1]
        if last["action"] != "done" or any(s["action"] == "note" for s in self.steps):
            return  # a task that carried notes typed what it read today: replaying that text would send stale news
        keep = []
        for i, s in enumerate(self.steps):
            if i < len(self._quick) or s["action"] not in ACTIONS or s["action"] in ("done", "fail", "replan", "wait"):
                continue  # shortcuts run by themselves next time; the rest is not an action
            act, at = _key(s), target(s)
            if at and s.get("changed") is False:
                continue  # a press that did nothing
            if at and ("think_s" in s or s.get("seen")) and i in self.shots:  # the shot was taken right before it
                if (cue := patch(self.shots[i], at)) is not None:
                    act["cue"] = cue
                    if picture := look(self.shots[i], at):
                        act["look"] = picture
                    act.update(_page(s))
            keep.append(act)
        # a reworded task that the models finished updates the routine it was matched to
        self.routines.learn(self._routine["task"] if self._routine else self.task, keep, last.get("answer", ""))

    async def _step(self) -> bool:
        """One see-think-act round (one or more actions). Returns True when the task is over."""
        acts = await self._next_or_done()
        if acts is None:
            return True
        seen = acts[0]["_shot"]
        spots = [self._pointer]  # where the pointer was and goes: pointer moves are not screen changes
        stuck = self._repeating(acts[0])
        settle = 0.0  # seconds spent waiting for the screen, on top of the model's think_s and each act_s
        for i, act in enumerate(acts):
            name = act["action"]
            act["tier"] = "actor"
            if name == "replan" or stuck:
                self._record(act.pop("_shot"), **act)
                await self._replan(stuck=stuck)
                return False
            if name not in NO_PHONE:
                started = time.time()
                await self._do(act)
                act["act_s"] = round(time.time() - started, 2)
                spots += _pointer_path(act)
                self._pointer = spots[-1]
            self._record(act.pop("_shot"), **act)
            if name in ("done", "fail"):
                return True
            if name == "note":
                continue
            following = acts[i + 1]["action"] if i < len(acts) - 1 else None
            if name == "tap" and following in ("type", "key"):  # typing into a field that a missed tap never opened
                settle += await self._settle()
                if not vision.changed(seen, self.screen.frame, ignore=tuple(spots)):
                    self._finish(acts[:i + 1], settle, changed=False)
                    return False  # skip the rest; the actor looks again
            elif name == "key" and following == "type":  # e.g. cmd+space: let the search field open before typing
                settle += await self._settle(quiet=0.15, least=0.1, most=1.0)
        if all(a["action"] == "note" for a in acts):
            return False  # nothing happened on the phone: no waiting, and no "screen did NOT change"
        settle += await self._settle()
        # feedback for the next round
        self._finish(acts, settle, changed=vision.changed(seen, self.screen.frame, ignore=tuple(spots)))
        return False

    async def _next_or_done(self) -> list[dict] | None:
        """The actor's next actions; None when Jev, asked meanwhile, is sure the last round finished the task.

        Jev is asked only after a round that changed the screen, and never for a question, whose answer has to come
        from the actor. Whichever answers first wins, so the check costs no time when it does not pay off.
        """
        frame = self.screen.frame
        last = self._last_move()
        if not (self.jev.enabled and not self._asks and last.get("changed") is True and last["action"] != "type"
                and frame):  # typed text still waits for its enter: the task cannot be done yet
            return await self._next_actions()
        started = time.time()
        actor = asyncio.create_task(self._next_actions())
        jev = asyncio.create_task(self.jev.done(frame, self.task))
        try:
            finished, _ = await asyncio.wait({actor, jev}, return_when=asyncio.FIRST_COMPLETED)
            if jev in finished and (p := jev.result()) is not None and p >= SURE:
                actor.cancel()
                self._record(frame, action="done", answer="", tier="jev", think_s=round(time.time() - started, 2),
                             thought="Jev sees the task done on this screen, so the model was not asked again.",
                             thought_zh="Jev 看屏幕判断任务已完成，没有再问模型。")
                return None
            return await actor
        finally:
            jev.cancel()
            actor.cancel()

    async def _settle(self, **wait) -> float:
        started = time.time()
        await self.screen.settled(**wait)
        return time.time() - started

    def _finish(self, acts: list[dict], settle: float, changed: bool) -> None:
        """Note the round's settle time and screen change on its last step, and log where the time went."""
        self._last_move().update(settle_s=round(settle, 2), changed=changed)
        think, act = acts[0].get("think_s", 0), sum(a.get("act_s", 0) for a in acts)
        log.info("agent: round of %d (%s): think %.2fs, act %.2fs, settle %.2fs, total %.2fs, changed=%s",
                 len(acts), " ".join(a["action"] for a in acts), think, act, settle, think + act + settle, changed)
        self.trace.write(kind="timing", actions=[a["action"] for a in acts], think_s=think, act_s=round(act, 2),
                         settle_s=round(settle, 2), changed=changed)

    def _missed(self) -> bool:
        """The previous round changed nothing on screen, so a tap may have landed off target."""
        return self._last_move().get("changed") is False

    def _last_move(self) -> dict:
        """The latest step that was not a note (a note changes nothing on the phone)."""
        return next((s for s in reversed(self.steps) if s["action"] != "note"), {})

    def _repeating(self, act: dict, limit: int = 3) -> bool:
        """True when the actor picks the same action as its last limit-1 moves and the last one did nothing.

        Repeating a move that still changes the screen (e.g. scrolling down a long list) is progress, not a loop.
        """
        moves = [s for s in self.steps if "plan" not in s][-(limit - 1):]
        return (len(moves) == limit - 1 and all(_key(m) == _key(act) for m in moves)
                and moves[-1].get("changed") is not True)

    async def _replan(self, stuck: bool = False) -> None:
        prompt = f"Goal: {self.task}"
        if done := self._quick + self._replayed:
            prompt += "\nAlready done: " + ", ".join(" ".join(map(str, a.values())) for a in done)
        if stuck:
            last = self.steps[-1] if self.steps else {}
            prompt += (f"\n\nThe previous plan got stuck (last event: {last.get('action')} {last.get('reason', '')}):"
                       f"\n{self.plan}\nLook at the current screen and plan from here, another way if needed.")
        reply, seconds, frame = await self._ask("planner", self.planner, PLANNER_PROMPT, prompt)
        self.plan, plan_zh = parse_plan(reply)
        self._record(frame, action="replan (stuck)" if stuck else "plan", plan=self.plan, plan_zh=plan_zh,
                     think_s=seconds, tier="planner")

    async def _next_actions(self) -> list[dict]:
        # actions and their effect only: echoing earlier thoughts makes the model repeat them instead of looking
        hidden = ("t", "plan", "plan_zh", "think_s", "act_s", "settle_s", "shot", "thought", "thought_zh", "page",
                  "changed", "tier")
        effect = {True: " -> screen changed", False: " -> screen did NOT change"}
        w, h = frame_size(self.screen.frame) or (1000, 1000)
        history = "\n".join(json.dumps(scale({k: v for k, v in s.items() if k not in hidden}, w / 1000, h / 1000))[:300]
                            + effect.get(s.get("changed"), "")
                            for s in self.steps[-8:] if "plan" not in s and s["action"] != "note") or "(none)"
        notes = "\n".join(f"- {s['text']}" for s in self.steps if s["action"] == "note")[-6000:]
        prompt = (f"Goal: {self.task}\n\nPlan:\n{self.plan}\n\n"
                  + (f"Your notes so far (facts you read on earlier screens):\n{notes}\n\n" if notes else "")
                  + f"Recent actions:\n{history}\n\nThe screenshot is {w} pixels wide and {h} pixels tall. "
                  "Next action?")
        for attempt in range(3):  # retry unparseable replies, a little less deterministically
            reply, seconds, frame = await self._ask("actor", self.actor, ACTOR_PROMPT, prompt, 0.4 * attempt,
                                                    max_tokens=1500)  # room for a message composed from notes
            try:
                said = parse_actions(reply)
            except ValueError as e:
                log.warning("unparseable actor reply (%s): %s", e, reply[:200])
                continue
            acts = [scale(a, 1000 / w, 1000 / h) for a in said]
            if self._missed() and _key(acts[0]) == _key(self._last_move()):  # same move that just did nothing
                log.info("actor repeated an action that changed nothing; asking for another")
                prompt += (f"\n\nYou just proposed {json.dumps(_key(said[0]))} again, but it changed nothing last "
                           "time. Choose a different action (another position, a shorter swipe, or another way).")
                continue
            acts[0]["think_s"] = seconds
            return [{**a, "_shot": frame} for a in acts]  # every action is drawn on the same screenshot
        raise ValueError(f"actor gave no valid action: {reply[:200]}")

    async def _do(self, a: dict) -> None:
        p = lambda k: a[k] / 1000  # noqa: E731
        match a["action"]:
            case "tap": await self.phone.tap(p("x"), p("y"))  # always aimed: the pointer is checked before pressing
            case "swipe": await self.phone.swipe(p("x1"), p("y1"), p("x2"), p("y2"))
            case "path": await self.phone.drag([(x / 1000, y / 1000) for x, y in a["points"]])
            case "circle": await self.phone.drag(circle(p("x"), p("y"), p("r"), self.screen.aspect))
            case "type": await self.phone.type(a["text"])
            case "key": await self.phone.key(a["combo"])
            case "open": await self.phone.open_app(shortcuts.search_term(a["app"]))
            case "home": await self.phone.go_home()
            case "back": await self.phone.go_back()
            case "wait" | "note": pass
            case other: raise ValueError(f"unknown action {other!r}")


def _page(step: dict) -> dict:
    """The step's page description, as keyword arguments (none when it has no page)."""
    return {"page": step["page"]} if step.get("page") else {}


def _moved(act: dict, dx: int, dy: int) -> dict:
    """The pointer action shifted by (dx, dy) in 0-1000 screen units."""
    act = dict(act)
    if act["action"] in ("tap", "circle"):
        act["x"], act["y"] = act["x"] + dx, act["y"] + dy
    elif act["action"] == "swipe":
        act.update(x1=act["x1"] + dx, y1=act["y1"] + dy, x2=act["x2"] + dx, y2=act["y2"] + dy)
    elif act["action"] == "path":
        act["points"] = [[x + dx, y + dy] for x, y in act["points"]]
    return act


def _pointer_path(act: dict) -> list[tuple[float, float]]:
    """Screen points (fractions) the pointer passes through for one action."""
    f = lambda x, y: (x / 1000, y / 1000)  # noqa: E731
    match act["action"]:
        case "tap": return [f(act["x"], act["y"])]
        case "swipe": return [f(act["x1"], act["y1"]), f(act["x2"], act["y2"])]
        case "path": return [f(*p) for p in act["points"]]
        case "circle": return [f(act["x"] + act["r"], act["y"])]
        case "back": return [(0.005, 0.45), (0.6, 0.45)]
        case _: return []


def circle(x: float, y: float, r: float, aspect: float, n: int = 24) -> list[tuple[float, float]]:
    """Points around a circle; r is a fraction of the width, aspect = width / height."""
    return [(x + r * math.cos(2 * math.pi * i / n), y + r * aspect * math.sin(2 * math.pi * i / n))
            for i in range(n + 1)]
