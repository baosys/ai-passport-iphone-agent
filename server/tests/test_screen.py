import json
import signal

import hidagent.screen as screen_module
from hidagent.screen import Screen, _remote

CONFIG = {"name": "iPhone Agent", "fps": 8, "quality": 75}


def left_behind(tmp_path, config=CONFIG, pid=4242, port=50123):
    state = tmp_path / "receiver.json"
    state.write_text(json.dumps({"pid": pid, "port": port, "config": config}))
    return state


def running(monkeypatch, pid=4242, command="uxplay -n iPhone Agent -vs ... tcpserversink port=50123 sync=false"):
    killed = []
    monkeypatch.setattr(screen_module, "_alive", lambda p: p == pid)
    monkeypatch.setattr(screen_module, "_command", lambda p: command if p == pid else "")
    monkeypatch.setattr(screen_module.os, "kill", lambda p, sig: killed.append((p, sig)))
    return killed


def test_a_receiver_left_by_the_last_run_is_reused(tmp_path, monkeypatch):
    killed = running(monkeypatch)
    screen = Screen(state=left_behind(tmp_path))
    assert screen._adopt() == 50123
    assert screen.pid == 4242 and screen._running() and killed == []


def test_a_receiver_with_other_settings_is_replaced(tmp_path, monkeypatch):
    killed = running(monkeypatch)
    screen = Screen(state=left_behind(tmp_path, {**CONFIG, "name": "Old name"}))
    assert screen._adopt() is None
    assert killed == [(4242, signal.SIGTERM)] and screen.pid is None


def test_a_reused_pid_is_not_mistaken_for_the_receiver(tmp_path, monkeypatch):
    killed = running(monkeypatch, command="/usr/bin/some-other-program")
    screen = Screen(state=left_behind(tmp_path))
    assert screen._adopt() is None and killed == []


def test_no_state_or_a_broken_one_starts_fresh(tmp_path, monkeypatch):
    running(monkeypatch)
    assert Screen(state=tmp_path / "receiver.json")._adopt() is None
    (tmp_path / "receiver.json").write_text("{")
    assert Screen(state=tmp_path / "receiver.json")._adopt() is None


def test_stopping_the_server_leaves_the_receiver_running(tmp_path, monkeypatch):
    killed = running(monkeypatch)
    screen = Screen(state=left_behind(tmp_path))
    screen._adopt()
    screen.stop()
    assert killed == [] and screen._running()


def test_restart_stops_the_receiver_for_the_supervisor_to_replace(tmp_path, monkeypatch):
    killed = running(monkeypatch)
    screen = Screen(state=left_behind(tmp_path))
    screen._adopt()
    screen.restart()
    assert killed == [(4242, signal.SIGTERM)]


def test_live_needs_a_phone_connected_and_a_frame(tmp_path, monkeypatch):
    running(monkeypatch)
    screen = Screen(state=left_behind(tmp_path))
    screen._adopt()
    monkeypatch.setattr(screen, "_clients", lambda: 2)
    assert screen.mirroring and not screen.live  # mirroring a still screen: no frame yet
    screen.frame = b"jpeg"
    monkeypatch.setattr(screen, "_clients", lambda: 0)
    assert not screen.live  # the pipeline is up but no phone is connected
    monkeypatch.setattr(screen, "_clients", lambda: 2)
    assert screen.live


def test_remote_peers():
    assert _remote("192.168.1.23:7000")
    assert _remote("[fe80::1]:7000")
    assert not _remote("127.0.0.1:50123")
    assert not _remote("[::1]:50123")


class Ran:
    def __init__(self, stdout):
        self.stdout = stdout


def test_the_relay_address_is_the_local_end_of_the_mirroring_connection(monkeypatch):
    def run(args, **_):
        if args[0] == "ifconfig":
            assert args[1] == "en14"
            return Ran("en14: flags=8863<UP>\n\tinet6 fe80::6b%en14\n\tinet 169.254.217.189 netmask 0xffff0000\n")
        return Ran("p79007\nn127.0.0.1:62175->127.0.0.1:64699\nn[fe80:23::6b:56a9]:62178->[fe80:23::1ce8]:62875\n")

    monkeypatch.setattr(screen_module.subprocess, "run", run)
    monkeypatch.setattr(screen_module.socket, "if_indextoname", lambda i: {0x23: "en14"}[i])
    screen = Screen()
    screen.pid = 79007
    assert screen.local_ip() == "169.254.217.189"
    ipv4 = Ran("p1\nn192.168.1.4:7000->192.168.1.9:5000\n")
    monkeypatch.setattr(screen_module.subprocess, "run", lambda *a, **k: ipv4)
    assert screen.local_ip() == "192.168.1.4"


def test_a_routable_ipv6_mirror_takes_the_ipv4_address_of_its_interface(monkeypatch):
    ifconfig = ("lo0: flags=8049<UP>\n\tinet 127.0.0.1 netmask 0xff000000\n"
                "en0: flags=8963<UP>\n\tinet6 fdec:5623:deb5:8900:8d3:d086:8f08:5f9a prefixlen 64\n"
                "\tinet 192.168.88.18 netmask 0xffffff00\n"
                "utun5: flags=8051<UP>\n\tinet 198.18.0.1 --> 198.18.0.1 netmask 0xfffe0000\n")

    def run(args, **_):
        if args[0] == "ifconfig":
            return Ran(ifconfig)
        return Ran("p1\nn[fdec:5623:deb5:8900:8d3:d086:8f08:5f9a]:55049->[fdec:5623:deb5:8900:18a1::ca19]:64696\n")

    monkeypatch.setattr(screen_module.subprocess, "run", run)
    screen = Screen()
    screen.pid = 1
    assert screen.local_ip() == "192.168.88.18"
