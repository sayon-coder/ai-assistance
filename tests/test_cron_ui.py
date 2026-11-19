"""Cron Jobs UI: natural-language fill and explicit job creation."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from talcon.apps import AppEntry
from talcon.cron import CronScheduler, CronStore
from talcon.ui.cron_jobs import CronJobsPage


class FakeCatalogue:
    def entries(self):
        return [
            AppEntry(
                name="Google Chrome", kind="shortcut",
                path="C:/ProgramData/Microsoft/Windows/Start Menu/Programs/Google Chrome.lnk",
            ),
        ]


class FakeRuntime:
    def __init__(self, tmp_path):
        self.modes_data = {"study": {"title": "Study Mode", "urls": [], "apps": []}}
        self.apps = FakeCatalogue()
        self.commands = SimpleNamespace(app_aliases={})
        self.store = CronStore(tmp_path / "cron_jobs.json")
        self.cron = CronScheduler(self, store=self.store)
        self.speech = SimpleNamespace()

    def mode_names(self):
        return list(self.modes_data)

    def mode_title(self, name):
        return self.modes_data[name].get("title", name)


def test_quick_description_fills_the_form(qapp, tmp_path):
    page = CronJobsPage(FakeRuntime(tmp_path))
    try:
        page.quick_input.setText("Open Chrome every day at 9 AM")
        page._fill_from_text()
        assert page.action_combo.currentData() == "open_app"
        assert page.app_name.text().lower() == "chrome"
        assert page.schedule_combo.currentData() == "daily"
        assert page.daily_time.time().hour() == 9
        assert page.name_input.text()
        assert page.error_label.isVisible() is False
    finally:
        page.deleteLater()


def test_authorized_job_is_saved_with_a_canonical_app(qapp, tmp_path):
    runtime = FakeRuntime(tmp_path)
    page = CronJobsPage(runtime)
    try:
        page.quick_input.setText("Open Chrome every day at 9 AM")
        page._fill_from_text()
        page.name_input.setText("Morning browser")
        page._save()
        jobs = runtime.store.snapshot()
        assert len(jobs) == 1
        job = jobs[0]
        assert job["name"] == "Morning browser"
        assert job["action"]["type"] == "open_app"
        assert job["action"]["app"]["name"] == "Google Chrome"
        assert job["schedule"] == {"kind": "daily", "time": "09:00"}
        assert job["next_run"]
        assert page.table.rowCount() == 1
    finally:
        page.deleteLater()


def test_choose_application_uses_the_picker_descriptor(qapp, tmp_path, monkeypatch):
    from talcon.ui import cron_jobs as cron_ui

    selected = AppEntry(
        name="Google Chrome", kind="shortcut",
        path="C:/ProgramData/Microsoft/Windows/Start Menu/Programs/Google Chrome.lnk",
    ).to_dict()

    class FakePicker:
        DialogCode = SimpleNamespace(Accepted=1)

        def __init__(self, runtime, parent=None):
            pass

        def exec(self):
            return self.DialogCode.Accepted

        @property
        def selected_app(self):
            return dict(selected)

    monkeypatch.setattr(cron_ui, "ApplicationPicker", FakePicker)
    runtime = FakeRuntime(tmp_path)
    page = CronJobsPage(runtime)
    try:
        page.action_combo.setCurrentIndex(page.action_combo.findData("open_app"))
        page._choose_app()
        assert page.app_name.text() == "Google Chrome"
        page.name_input.setText("Browser via picker")
        page._save()
        assert runtime.store.snapshot()[0]["action"]["app"]["name"] == "Google Chrome"
        assert page.error_label.isVisible() is False
    finally:
        page.deleteLater()


def test_pause_button_changes_persisted_state(qapp, tmp_path):
    runtime = FakeRuntime(tmp_path)
    page = CronJobsPage(runtime)
    try:
        page.quick_input.setText("Open Chrome every day at 9 AM")
        page._fill_from_text()
        page.name_input.setText("Morning browser")
        page._save()
        page.table.selectRow(0)
        page._toggle_selected()
        assert runtime.store.snapshot()[0]["enabled"] is False
        assert runtime.store.snapshot()[0]["last_detail"] == "Paused."
        page._toggle_selected()
        assert runtime.store.snapshot()[0]["enabled"] is True
    finally:
        page.deleteLater()


def test_one_time_job_rejects_a_past_date(qapp, tmp_path):
    runtime = FakeRuntime(tmp_path)
    page = CronJobsPage(runtime)
    try:
        page.name_input.setText("Past job")
        page.app_name.setText("Google Chrome")
        page.schedule_combo.setCurrentIndex(0)
        past = dt.datetime.now().astimezone() - dt.timedelta(days=1)
        from PySide6.QtCore import QDate, QTime
        page.once_date.setDate(QDate(past.year, past.month, past.day))
        page.once_time.setTime(QTime(past.hour, past.minute))
        page._save()
        assert runtime.store.snapshot() == []
        assert "future" in page.error_label.text().lower()
    finally:
        page.deleteLater()


def test_editing_a_paused_job_keeps_it_paused(qapp, tmp_path):
    runtime = FakeRuntime(tmp_path)
    page = CronJobsPage(runtime)
    try:
        page.quick_input.setText("Open Chrome every day at 9 AM")
        page._fill_from_text()
        page.name_input.setText("Morning browser")
        page._save()
        page.table.selectRow(0)
        page._toggle_selected()
        assert runtime.store.snapshot()[0]["enabled"] is False

        page._edit_selected()
        page.name_input.setText("Morning browser renamed")
        page._save()
        saved = runtime.store.snapshot()[0]
        assert saved["name"] == "Morning browser renamed"
        assert saved["enabled"] is False
    finally:
        page.deleteLater()
