import hidagent.relay as relay_module
from hidagent.relay import Relay


def test_offer_serves_the_text_once_on_a_digit_only_address(monkeypatch):
    monkeypatch.setenv("HIDAGENT_RELAY_HOST", "192.168.1.5")
    relay = Relay(8890)
    url = relay.offer("<还款>")
    host, code = url.split("/")
    assert host == "192.168.1.5:8890" and code.isdigit() and len(code) == 6
    assert relay.page("000000") is None and not relay.loaded.is_set()
    page = relay.page(code)
    assert "&lt;还款&gt;" in page and relay.loaded.is_set()
    assert relay.done(code, True) and relay.copied.is_set()
    assert relay.page(code) is None  # copied: never served again


def test_a_failed_copy_keeps_the_text_for_another_tap(monkeypatch):
    monkeypatch.setenv("HIDAGENT_RELAY_HOST", "10.0.0.2")
    relay = Relay(8890)
    code = relay.offer("你好").split("/")[1]
    assert relay.done(code, False) and not relay.copied.is_set()
    assert relay.page(code) is not None


def test_an_offer_expires(monkeypatch):
    monkeypatch.setenv("HIDAGENT_RELAY_HOST", "10.0.0.2")
    relay = Relay(8890)
    code = relay.offer("你好").split("/")[1]
    monkeypatch.setattr(relay_module, "EXPIRES", 0)
    assert relay.page(code) is None and not relay.done(code, True)
