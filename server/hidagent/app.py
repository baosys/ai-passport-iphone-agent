"""Web API + control page. Run with `python -m hidagent`."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import Agent
from .debug import Trace
from .device import Device, DeviceError
from .jev import Jev
from .llm import LLM
from .mirror import Mirror
from .phone import Phone
from .relay import Relay
from .routines import Routines
from .screen import Screen

DATA_DIR = Path(os.environ.get("HIDAGENT_DATA", Path.home() / ".hidagent"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEBUG = os.environ.get("HIDAGENT_DEBUG") == "1"

device = Device()
AIRPLAY_NAME = os.environ.get("AIRPLAY_NAME", "iPhone Agent")
screen = Screen(AIRPLAY_NAME, state=DATA_DIR / "receiver.json")
relay = Relay(int(os.environ.get("PORT", 8890)), screen.local_ip)
phone = Phone(device, DATA_DIR / "calibration.json", screen, relay=relay)
agent = Agent(phone, screen, LLM.from_env("ACTOR"), LLM.from_env("PLANNER"),
              Trace(DATA_DIR / "traces" if DEBUG else None),
              max_steps=int(os.environ.get("HIDAGENT_MAX_STEPS", 100)), routines=Routines(DATA_DIR / "routines.json"),
              jev=Jev.from_env())
mirror = Mirror(phone, screen, agent.actor, DATA_DIR / "mirror.json", AIRPLAY_NAME,
                busy=lambda: agent.running and not agent.blind)  # a task waiting for the mirror lets it reconnect


@asynccontextmanager
async def lifespan(_):
    server = await device.serve("0.0.0.0", int(os.environ.get("DEVICE_PORT", 7300)))
    screen.start()
    awake = asyncio.create_task(device.keep_awake(screen=screen))
    watch = asyncio.create_task(mirror.watch())
    yield
    watch.cancel()
    await mirror.stop()
    awake.cancel()
    screen.stop()
    server.close()


app = FastAPI(lifespan=lifespan)
class Static(StaticFiles):
    """Static files that browsers re-check on every load, so a new version shows up on refresh."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", Static(directory=Path(__file__).parent / "static"), name="static")


class Point(BaseModel):
    x: float
    y: float


class Tap(Point):
    aim: bool = True  # find the pointer on screen and fix the miss before pressing


class Swipe(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class Scroll(Point):
    lines: int


class Text(BaseModel):
    text: str


async def act(coro):
    try:
        await coro
    except (DeviceError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/status")
def status():
    return {"device": device.connected, "screen": screen.live, "calibrated": phone.calibrated,
            "debug": DEBUG, "trace": str(agent.trace.dir or ""), "aspect": round(screen.aspect, 4),
            "models": {"planner": agent.planner.model, "actor": agent.actor.model, "jev": agent.jev.enabled},
            "agent": {"running": agent.running, "task": agent.task, "steps": agent.steps},
            "mirror": mirror.status()}


@app.get("/api/frame.jpg")
def frame():
    if not screen.live:
        raise HTTPException(404, "no live screen: start screen mirroring on the phone")
    return Response(screen.frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/steps/{index}.jpg")
def step_shot(index: int):
    if index not in agent.shots:
        raise HTTPException(404)
    return Response(agent.shots[index], media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/tap")
async def tap(p: Tap):
    return await act(phone.tap(p.x, p.y, p.aim))


@app.post("/api/swipe")
async def swipe(s: Swipe):
    return await act(phone.swipe(s.x1, s.y1, s.x2, s.y2))


@app.post("/api/scroll")
async def scroll(s: Scroll):
    return await act(phone.scroll(s.x, s.y, s.lines))


@app.post("/api/type")
async def type_text(t: Text):
    return await act(phone.type(t.text))


@app.post("/api/key")
async def key(t: Text):
    return await act(phone.key(t.text))


@app.post("/api/open")
async def open_app(t: Text):
    return await act(phone.open_app(t.text))


@app.post("/api/home")
async def home():
    return await act(phone.go_home())


@app.post("/api/back")
async def back():
    return await act(phone.go_back())


@app.post("/api/raw")
async def raw(t: Text):
    """Debug only: send one protocol line to the board as-is."""
    if not DEBUG:
        raise HTTPException(404)
    return await act(device.send(t.text))


@app.post("/api/calibrate/start")
async def calibrate_start():
    """Calibrate the pointer. {"auto": true}: done by itself; otherwise the user clicks the pointer next."""
    try:
        return {"ok": True, "auto": await phone.start_calibration(), "span": phone.span}
    except (DeviceError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e


@app.post("/api/calibrate/finish")
def calibrate_finish(p: Point):
    phone.finish_calibration(p.x, p.y)
    return {"ok": True, "span": phone.span}


class Auto(BaseModel):
    auto: bool


@app.get("/api/mirror")
def mirror_status():
    return {**mirror.status(), "spots": mirror.spots}


@app.post("/api/mirror")
def mirror_auto(a: Auto):
    """Turn reconnecting Screen Mirroring by itself on or off."""
    mirror.set_auto(a.auto)
    return {"ok": True, **mirror.status()}


@app.post("/api/mirror/learn")
async def mirror_learn():
    """With mirroring on: find the Control Center taps that turn it on, for replaying blind later."""
    try:
        return {"ok": True, "spots": await mirror.run(mirror.learn())}
    except (DeviceError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e


@app.post("/api/mirror/reconnect")
async def mirror_reconnect():
    """Replay the learned taps now; {"ok": true} once mirroring is back."""
    try:
        return {"ok": await mirror.run(mirror.reconnect())}
    except (DeviceError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e


@app.post("/api/agent")
async def agent_start(t: Text):
    if mirror.running:
        raise HTTPException(409, "turning Screen Mirroring back on: try again in a moment")
    try:
        agent.start(t.text)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@app.post("/api/agent/stop")
async def agent_stop():
    """Emergency stop: cancel the agent and release the mouse button."""
    agent.stop()
    await mirror.stop()
    await asyncio.sleep(0.1)  # let a gesture in flight finish its own release
    return await act(phone.reset()) if device.connected else {"ok": True}


@app.get("/api/routines")
def routines():
    """Tasks the agent remembers how to do, and how often replaying them worked."""
    return [{"task": r["task"], "steps": len(r["steps"]), "ok": r["ok"], "fails": r["fails"]}
            for r in agent.routines.data.values()]


@app.post("/api/routines/forget")
def routines_forget(t: Text):
    """Forget how a task was done, so the models work it out afresh next time."""
    return {"ok": agent.routines.forget(t.text)}


@app.post("/api/agent/clear")
def agent_clear():
    """Forget the last run (steps, screenshots, plan) so the page starts clean."""
    try:
        agent.clear()
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@app.get("/clip")
def relay_clip():
    """The text the phone's paste Shortcut copies, served once (relay.py)."""
    text = phone.relay.take()
    if text is None:
        raise HTTPException(404)
    return PlainTextResponse(text, headers={"Cache-Control": "no-store"})


@app.get("/{code}")
def relay_page(code: str):
    """The one-time page the phone opens in Safari to copy text it cannot type (relay.py)."""
    page = phone.relay.page(code) if code.isdigit() else None
    if page is None:
        raise HTTPException(404)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.post("/{code}/copied")
async def relay_copied(code: str, request: Request):
    if not phone.relay.done(code, (await request.body()) == b"1"):
        raise HTTPException(404)
    return {"ok": True}
