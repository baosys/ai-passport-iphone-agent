"""Exact text for the phone, through Safari and its clipboard.

The board types ASCII only, and the pinyin keyboard picks the first candidate, so typed Chinese comes out with wrong
homophones. Universal Clipboard needs this computer signed in to the phone's Apple ID. Without it, the server offers
the text on a one-time page: the phone opens Safari, types the page's address (digits only, which the pinyin
keyboard leaves alone in the address bar), taps the page once, which copies the text, and swipes back to the app
to paste it. About ten seconds for any length, every character exact.

Faster, once set up on the phone: a Shortcut that fetches http://<this computer>.local:8890/clip and copies the
result, run by a mouse button the board clicks (AssistiveTouch maps the button to the Shortcut). The text is held
for one fetch only; about a second, without leaving the app.
"""

import asyncio
import html
import os
import secrets
import socket
import time

EXPIRES = 60  # seconds an offered text stays available


def lan_ip() -> str | None:
    """This computer's address on the network the phone uses (HIDAGENT_RELAY_HOST overrides it)."""
    if host := os.environ.get("HIDAGENT_RELAY_HOST"):
        return host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent: this only picks the outgoing interface
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>AI Passport</title>
<style>html,body{{margin:0;height:100%}}button{{width:100%;height:100%;border:0;font-size:32px;color:#fff;
background:#0a84ff}}textarea{{position:absolute;left:-9999px;top:0}}</style>
<textarea id="t" readonly>{text}</textarea><button id="b">点按复制 · Tap to copy</button>
<script>
b.onclick = () => {{
  t.readOnly = false; t.focus(); t.setSelectionRange(0, t.value.length); t.select();
  const ok = document.execCommand("copy"); t.blur(); t.readOnly = true;
  b.textContent = ok ? "已复制 · Copied" : "复制失败 · Copy failed"; b.style.background = ok ? "#34c759" : "#ff3b30";
  fetch(location.pathname + "/copied", {{method: "POST", body: ok ? "1" : "0"}});
}};
</script>"""


class Relay:
    def __init__(self, port: int, host=None):
        self.port = port
        self._host = host  # returns the address the phone reaches this computer on, or None
        self.code = ""
        self.text = ""
        self.offered = 0.0
        self.loaded = asyncio.Event()
        self.copied = asyncio.Event()
        self.clip, self.clip_at = "", 0.0  # text for the Shortcut's single fetch of /clip
        self.fetched = asyncio.Event()

    def offer(self, text: str) -> str | None:
        """Hold `text` on a new one-time page; its address as typed into Safari, or None without a network."""
        ip = os.environ.get("HIDAGENT_RELAY_HOST") or (self._host and self._host()) or lan_ip()
        if ip is None:
            return None
        self.code, self.text, self.offered = f"{secrets.randbelow(900000) + 100000}", text, time.time()
        self.loaded.clear()
        self.copied.clear()
        return f"{ip}:{self.port}/{self.code}"

    def _live(self, code: str) -> bool:
        return bool(self.text) and secrets.compare_digest(code, self.code) and time.time() - self.offered < EXPIRES

    def page(self, code: str) -> str | None:
        if not self._live(code):
            return None
        self.loaded.set()
        return PAGE.format(text=html.escape(self.text))

    def done(self, code: str, ok: bool) -> bool:
        """The page reports its copy. A good copy ends the offer: the text is not served again."""
        if not self._live(code):
            return False
        if ok:
            self.text = ""
            self.copied.set()
        return True

    def hold(self, text: str) -> None:
        """Hold `text` for the next fetch of /clip."""
        self.clip, self.clip_at = text, time.time()
        self.fetched.clear()

    def take(self) -> str | None:
        """The held text, once; None when nothing is held or it expired."""
        text, self.clip = self.clip, ""
        if not text or time.time() - self.clip_at > EXPIRES:
            return None
        self.fetched.set()
        return text
