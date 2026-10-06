import subprocess

import vwap_rsi_eval as E


def test_free_agent_ports_is_opt_in(monkeypatch):
    monkeypatch.delenv("MBT_FREE_PORTS", raising=False)

    def boom(*a, **k):
        raise AssertionError("must not touch processes without MBT_FREE_PORTS=1")
    monkeypatch.setattr(E.subprocess, "run", boom)
    E.free_agent_ports()


def test_free_agent_ports_terminates_then_kills_listeners(monkeypatch):
    monkeypatch.setenv("MBT_FREE_PORTS", "1")
    calls, logged = [], []

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))
        out = "p123\ncnode\np456\ncmetatester64.exe\n" if cmd[0] == "lsof" else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    monkeypatch.setattr(E.subprocess, "run", fake_run)
    monkeypatch.setattr(E.M, "log", lambda *a: logged.append(" ".join(map(str, a))))
    E.free_agent_ports()
    assert ["kill", "-TERM", "123", "456"] in calls
    assert ["kill", "-9", "123", "456"] in calls
    assert calls.index(["kill", "-TERM", "123", "456"]) < calls.index(["kill", "-9", "123", "456"])
    assert "123:node" in logged[0] and "456:metatester64.exe" in logged[0]
