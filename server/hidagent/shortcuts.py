"""Tasks plain enough to run without asking a model: open an app, go home, go back.

"打开微信" runs as one keyboard sequence, with no planner or actor call. A compound task such as
"打开微信，给张三发消息" opens the app the same way and leaves the rest to the models.
"""

import re

from pypinyin import lazy_pinyin

from .textinput import _hanzi

POLITE = r"(?:请|帮我|给我|麻烦|please\s+)*"
OPEN = re.compile(rf"^{POLITE}(?:打开|启动|开启|open|launch|start)\s*(?P<app>.+?)(?:\s*(?:app|应用|软件))?$", re.I)
HOME = re.compile(rf"^{POLITE}(?:(?:回到|返回|回|去|go\s+(?:to\s+)?(?:the\s+)?)?"
                  r"(?:主屏幕|主屏|桌面|主页|home(?:\s+screen)?)|go\s+home)$", re.I)
BACK = re.compile(rf"^{POLITE}(?:返回(?:上一页|上一级)?|后退|go\s+back|back)$", re.I)
# where a compound task's first clause ends
SPLIT = re.compile(r"\s*(?:[，,；;。]|然后|接着|之后|\band\s+then\b|\bthen\b)\s*", re.I)
# a name that is really a place in an app ("设置里的Wi-Fi") or a whole request ("微信给张三发消息"): leave it to
# the models; a missed shortcut only costs time, a wrong one opens the wrong thing
NOT_A_NAME = re.compile(r"[的里给发搜找查看听玩买把在到并]|\s(?:and|in|to|on|for|with)\s", re.I)
MAX_HANZI = 6


def search_term(name: str) -> str:
    """What to type into search for an app: its name, with hanzi as pinyin (the keyboard turns it back)."""
    return "".join(lazy_pinyin(name.strip())).strip()


def _clause(text: str) -> dict | None:
    text = text.strip().rstrip("。.!！")
    if HOME.match(text):
        return {"action": "home"}
    if BACK.match(text):
        return {"action": "back"}
    m = OPEN.match(text)
    app = m.group("app").strip(" “”\"'") if m else ""
    if app and len(app) <= 24 and sum(map(_hanzi, app)) <= MAX_HANZI and not NOT_A_NAME.search(app):
        return {"action": "open", "app": app}
    return None


def split(task: str) -> tuple[list[dict], str]:
    """The task's leading shortcut actions, and the rest of it ("" when shortcuts cover all of it)."""
    acts, rest = [], task.strip()
    while rest:
        head, *tail = SPLIT.split(rest, maxsplit=1)
        act = _clause(head)
        if act is None:
            break
        acts.append(act)
        rest = tail[0].strip() if tail else ""
    return acts, rest
