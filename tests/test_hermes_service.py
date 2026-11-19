"""Hermes service discovery tests (no network, no real installation needed)."""
from __future__ import annotations

from pathlib import Path

from talcon import hermes_service


def test_launcher_prefers_the_windowless_vbs(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    service = home / "gateway-service"
    service.mkdir(parents=True)
    (service / "Hermes_Gateway.vbs").write_text("' stub", encoding="utf-8")
    (service / "Hermes_Gateway.cmd").write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(hermes_service, "home_candidates", lambda: [home])

    assert hermes_service.hermes_home() == home
    assert hermes_service.installed() is True
    assert hermes_service.launcher().name == "Hermes_Gateway.vbs"


def test_missing_installation_is_reported_honestly(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes_service, "home_candidates", lambda: [tmp_path / "nope"])
    assert hermes_service.hermes_home() is None
    assert hermes_service.installed() is False
    started, detail = hermes_service.ensure_running(port=1, wait_seconds=0.2)
    assert started is False
    assert "not installed" in detail.lower()


def test_starting_is_not_attempted_on_a_foreign_host(monkeypatch):
    # ``probe`` must never launch anything for a host that is not loopback; the
    # client refuses it before a socket opens, so no process is spawned.
    report = hermes_service.probe("192.0.2.10", 8642, start_if_down=False)
    assert report["reachable"] is False
    assert report["listening"] is False


def test_probe_reports_the_port_state_without_raising():
    report = hermes_service.probe("127.0.0.1", 1, start_if_down=False)
    assert report["listening"] is False
    assert report["reachable"] is False
    assert report["port"] == 1
    assert isinstance(Path(report["home"]), Path)
