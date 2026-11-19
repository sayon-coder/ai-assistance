"""Cron Jobs dashboard: create, edit, pause, resume and delete local jobs."""
from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QDate, QTimer, Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout,
    QWidget,
)

from ..app_catalog import resolve_application
from ..cron import (
    CronError, describe_schedule, local_now, parse_natural_job, validate_job,
)
from . import theme
from .app_picker import ApplicationPicker
from .components import Card, Heading, footnote, muted, row

WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
STATUS_LABELS = {
    "never": "Never run",
    "running": "Running",
    "success": "Success",
    "failure": "Failed",
    "missed": "Missed",
}
CATCH_UP_LABELS = (
    ("Run once after sleep or restart", "run_once"),
    ("Skip missed runs", "skip"),
)


def _moment(value: str):
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def _short_moment(value: str) -> str:
    moment = _moment(value)
    if moment is None:
        return "—"
    return moment.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")


def _stretch() -> QWidget:
    holder = QWidget()
    holder.setFixedWidth(1)
    return holder


def _labelled(text: str, widget: QWidget) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    label = QLabel(text)
    label.setObjectName("Muted")
    layout.addWidget(label)
    layout.addWidget(widget)
    return holder


class CronJobsPage(QWidget):
    """The dedicated Cron Jobs sidebar section."""

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.cron = getattr(runtime, "cron", None)
        self._editing_id = ""
        self._selected_app: dict | None = None
        self._selected_job_id = ""
        self.setObjectName("Page")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(Heading(
            "Cron Jobs",
            "Schedule applications and modes once, daily, weekly or at a custom interval. "
            "Jobs run silently while TALCON is running, including when its window is hidden.",
        ))

        layout.addWidget(self._build_form())
        layout.addWidget(self._build_jobs_card())
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event) -> None:
        # No page-wide polling while the user is looking at another section.
        self._timer.stop()
        super().hideEvent(event)

    # -- construction ------------------------------------------------------
    def _build_form(self) -> QWidget:
        card = Card(
            "Create a scheduled job",
            "Creating a job authorises exactly the action shown below. Scheduled runs never "
            "ask for permission again and never speak or show notifications.",
        )
        self.form_title = QLabel("New job")
        self.form_title.setObjectName("CardTitle")
        card.add(self.form_title)

        self.quick_input = QLineEdit()
        self.quick_input.setPlaceholderText("Open Chrome every day at 9 AM")
        self.quick_input.returnPressed.connect(self._fill_from_text)
        quick = QPushButton("Fill from description")
        quick.clicked.connect(self._fill_from_text)
        card.add(_labelled(
            "Natural-language description (optional)", self.quick_input
        ))
        card.add(row(quick, _stretch(), spacing=8))
        card.add(footnote(
            "Type a sentence such as “Open Chrome every day at 9 AM”, then press "
            "Fill from description. It only fills the fields below; review them and "
            "press Authorize to create the job."
        ))

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("For example: Morning browser")
        card.add(_labelled("Job name", self.name_input))

        self.action_combo = QComboBox()
        self.action_combo.addItem("Open an application", "open_app")
        self.action_combo.addItem("Close an application gracefully", "close_app")
        self.action_combo.addItem("Activate a TALCON mode", "activate_mode")
        self.action_combo.currentIndexChanged.connect(self._sync_action_fields)
        card.add(_labelled("Action", self.action_combo))

        self.app_name = QLineEdit()
        self.app_name.setPlaceholderText("Application name, for example Google Chrome")
        choose_app = QPushButton("Choose application…")
        choose_app.clicked.connect(self._choose_app)
        self.app_row = row(self.app_name, choose_app, spacing=8)
        card.add(self.app_row)

        self.mode_combo = QComboBox()
        for name in getattr(self.runtime, "mode_names", lambda: [])():
            title = (
                self.runtime.mode_title(name)
                if hasattr(self.runtime, "mode_title") else name.replace("_", " ").title()
            )
            self.mode_combo.addItem(title, name)
        self.mode_row = _labelled("Mode", self.mode_combo)
        card.add(self.mode_row)
        self._sync_action_fields()

        self.schedule_combo = QComboBox()
        self.schedule_combo.addItem("One time", "once")
        self.schedule_combo.addItem("Daily", "daily")
        self.schedule_combo.addItem("Weekly", "weekly")
        self.schedule_combo.addItem("Custom interval", "interval")
        self.schedule_combo.currentIndexChanged.connect(self._sync_schedule_fields)
        card.add(_labelled("Repeats", self.schedule_combo))

        self.schedule_stack = QStackedWidget()
        self.schedule_stack.addWidget(self._once_widget())
        self.schedule_stack.addWidget(self._daily_widget())
        self.schedule_stack.addWidget(self._weekly_widget())
        self.schedule_stack.addWidget(self._interval_widget())
        card.add(self.schedule_stack)

        self.catch_combo = QComboBox()
        for label, value in CATCH_UP_LABELS:
            self.catch_combo.addItem(label, value)
        card.add(_labelled("Catch-up policy", self.catch_combo))

        self.create_button = QPushButton("Authorize and create job")
        self.create_button.setObjectName("Primary")
        self.create_button.clicked.connect(self._save)
        self.cancel_button = QPushButton("Cancel edit")
        self.cancel_button.clicked.connect(self._cancel_edit)
        self.cancel_button.setVisible(False)
        card.add(row(self.create_button, self.cancel_button, _stretch(), spacing=8))

        self.error_label = QLabel("")
        self.error_label.setObjectName("Bad")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        card.add(self.error_label)
        local = local_now()
        zone = local.tzname() or "local time"
        offset = local.strftime("%z")
        offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
        card.add(footnote(
            f"All schedules use this PC's Windows timezone: {zone} (UTC{offset}). "
            "A job may open or close applications only while your Windows desktop session is active."
        ))
        card.add(footnote(
            "Cron runs while TALCON is running, including when this window is hidden in the tray. "
            "Choosing Close exits TALCON and stops the scheduler; missed runs follow the catch-up "
            "policy the next time TALCON starts."
        ))
        card.add(footnote(
            "Examples: “Open Chrome every day at 9 AM” · “Close Notepad tomorrow at 6 PM” · "
            "“Open Calculator every 2 hours” · “Activate study mode every weekday at 8 AM”."
        ))
        return card

    def _once_widget(self) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.once_date = QDateEdit()
        self.once_date.setCalendarPopup(True)
        self.once_date.setDate(QDate.currentDate().addDays(1))
        self.once_time = QTimeEdit()
        self.once_time.setTime(QTime(9, 0))
        layout.addWidget(_labelled("Date", self.once_date), 1)
        layout.addWidget(_labelled("Time", self.once_time), 1)
        return holder

    def _daily_widget(self) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        self.daily_time = QTimeEdit()
        self.daily_time.setTime(QTime(9, 0))
        layout.addWidget(_labelled("Every day at", self.daily_time), 1)
        layout.addStretch(1)
        return holder

    def _weekly_widget(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.weekly_time = QTimeEdit()
        self.weekly_time.setTime(QTime(9, 0))
        layout.addWidget(_labelled("At", self.weekly_time))
        day_row = QHBoxLayout()
        day_row.setSpacing(10)
        self.weekday_boxes: list[QCheckBox] = []
        for index, label in enumerate(WEEKDAY_LABELS):
            box = QCheckBox(label)
            box.setChecked(index < 5)
            self.weekday_boxes.append(box)
            day_row.addWidget(box)
        day_row.addStretch(1)
        layout.addLayout(day_row)
        return holder

    def _interval_widget(self) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.interval_value = QSpinBox()
        self.interval_value.setRange(1, 720)
        self.interval_value.setValue(2)
        self.interval_unit = QComboBox()
        self.interval_unit.addItem("minutes", 1)
        self.interval_unit.addItem("hours", 60)
        self.interval_unit.addItem("days", 1440)
        self.interval_unit.setCurrentIndex(1)
        layout.addWidget(_labelled("Every", self.interval_value))
        layout.addWidget(_labelled("Unit", self.interval_unit))
        layout.addStretch(1)
        return holder

    def _build_jobs_card(self) -> QWidget:
        card = Card(
            "Scheduled jobs",
            "Results are recorded here silently. Editing a job always requires an explicit "
            "Save action; scheduled runs themselves never open a dialog.",
        )
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("Muted")
        self.summary_label.setWordWrap(True)
        card.add(self.summary_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("Name", "Action", "Recurrence", "Next run", "Last run", "Status", "State")
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(230)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in range(1, 7):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_details)
        card.add(self.table)

        self.pause_button = QPushButton("Pause selected")
        self.pause_button.clicked.connect(self._toggle_selected)
        edit_button = QPushButton("Edit selected")
        edit_button.clicked.connect(self._edit_selected)
        delete_button = QPushButton("Delete selected")
        delete_button.setObjectName("Danger")
        delete_button.clicked.connect(self._delete_selected)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)
        card.add(row(
            self.pause_button, edit_button, delete_button, refresh_button,
            _stretch(), spacing=8,
        ))

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(125)
        self.details.setPlaceholderText("Select a job to see its last result and recent runs.")
        card.add(self.details)
        return card

    # -- form helpers ------------------------------------------------------
    def _schedule_kind(self) -> str:
        return str(self.schedule_combo.currentData() or "once")

    def _sync_action_fields(self) -> None:
        mode = str(self.action_combo.currentData() or "open_app") == "activate_mode"
        self.app_row.setVisible(not mode)
        self.mode_row.setVisible(mode)

    def _sync_schedule_fields(self) -> None:
        mapping = {"once": 0, "daily": 1, "weekly": 2, "interval": 3}
        self.schedule_stack.setCurrentIndex(mapping.get(self._schedule_kind(), 0))

    def _show_error(self, message: str) -> None:
        self.error_label.setText(str(message))
        self.error_label.setVisible(True)

    def _clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.setVisible(False)

    def _cron(self):
        if self.cron is None:
            raise CronError("The Cron Jobs scheduler is not available.")
        return self.cron

    # -- data --------------------------------------------------------------
    def refresh(self) -> None:
        if self.cron is None:
            self.summary_label.setText("Cron Jobs is unavailable in this build.")
            self.table.setRowCount(0)
            return
        jobs = self.cron.snapshot()
        self.summary_label.setText(self.cron.status_text())
        current = self._selected_job_id
        self.table.setRowCount(len(jobs))
        for row_index, job in enumerate(jobs):
            action = job.get("action") or {}
            kind = action.get("type")
            if kind == "open_app":
                app = action.get("app") or {}
                action_label = f"Open {app.get('name') or action.get('name') or 'application'}"
            elif kind == "close_app":
                action_label = f"Close {action.get('name') or 'application'}"
            else:
                action_label = f"Activate {action.get('mode', 'mode')} mode"
            enabled = bool(job.get("enabled"))
            status = str(job.get("last_status") or "never")
            state = "Active" if enabled else (
                "Completed" if job.get("schedule", {}).get("kind") == "once"
                and status in {"success", "failure", "missed"} else "Paused"
            )
            values = (
                str(job.get("name") or ""),
                action_label,
                describe_schedule(job.get("schedule") or {}),
                _short_moment(job.get("next_run") or "") if enabled else "Paused",
                _short_moment(job.get("last_run") or ""),
                STATUS_LABELS.get(status, status.title()),
                state,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, job.get("id"))
                if column == 5:
                    colour_key = {
                        "success": "completed", "failure": "failed",
                        "missed": "not running", "running": "running",
                        "never": "idle",
                    }.get(status, value)
                    item.setForeground(QColor(theme.status_colour(colour_key)))
                self.table.setItem(row_index, column, item)
            if job.get("id") == current:
                self.table.selectRow(row_index)
        if self.table.currentRow() < 0 and jobs:
            self.table.selectRow(0)
        self._show_details()

    def _selected_job(self):
        if self.cron is None:
            return None
        row_index = self.table.currentRow()
        if row_index < 0:
            return None
        item = self.table.item(row_index, 0)
        if item is None:
            return None
        job_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self._selected_job_id = job_id
        return self.cron.store.get(job_id) if hasattr(self.cron, "store") else None

    def _show_details(self) -> None:
        job = self._selected_job()
        if job is None:
            self.details.setPlainText("")
            return
        lines = [
            f"{job.get('name')} — {STATUS_LABELS.get(job.get('last_status'), job.get('last_status'))}",
            f"Last execution: {_short_moment(job.get('last_run') or '')}",
            f"Detail: {job.get('last_detail') or '—'}",
            f"Catch-up: {'Run once if missed' if job.get('catch_up') == 'run_once' else 'Skip missed runs'}",
        ]
        history = list(job.get("history") or [])[-8:]
        if history:
            lines.append("Recent runs:")
            for entry in reversed(history):
                lines.append(
                    f"  {_short_moment(entry.get('occurrence') or '')} · "
                    f"{STATUS_LABELS.get(entry.get('status'), entry.get('status'))} · "
                    f"{entry.get('detail') or ''}"
                )
        self.details.setPlainText("\n".join(lines))
        self.pause_button.setText(
            "Resume selected" if not job.get("enabled") else "Pause selected"
        )

    # -- actions -----------------------------------------------------------
    def _choose_app(self) -> None:
        picker = ApplicationPicker(self.runtime, parent=self)
        if picker.exec() == ApplicationPicker.DialogCode.Accepted:
            selected = picker.selected_app
            if selected:
                self._selected_app = selected
                self.app_name.setText(str(selected.get("name") or selected.get("path") or ""))
                self.app_name.setToolTip(str(selected.get("path") or ""))

    def _resolve_app(self, name: str) -> dict:
        catalog = getattr(self.runtime, "apps", None)
        if catalog is None:
            raise CronError("Application discovery is unavailable.")
        try:
            entries = list(catalog.entries())
        except Exception as exc:
            raise CronError(f"Could not read installed applications: {exc}") from exc
        aliases = getattr(getattr(self.runtime, "commands", None), "app_aliases", {}) or {}
        result = resolve_application(name, entries, aliases)
        if result.status == "match" and result.entry is not None:
            return result.entry.to_dict()
        if result.status == "ambiguous" and result.candidates:
            names = ", ".join(entry.name for entry in result.candidates[:4])
            raise CronError(f"That name matches more than one app: {names}. Choose one.")
        raise CronError(f"No installed application matches “{name}”.")

    def _fill_from_text(self) -> None:
        self._clear_error()
        try:
            parsed = parse_natural_job(self.quick_input.text())
        except CronError as exc:
            self._show_error(str(exc))
            return
        self._selected_app = None
        self.name_input.setText(str(parsed.get("name") or ""))
        action = parsed.get("action") or {}
        action_type = str(action.get("type") or "open_app")
        index = self.action_combo.findData(action_type)
        if index >= 0:
            self.action_combo.setCurrentIndex(index)
        if action_type == "activate_mode":
            wanted = str(action.get("mode") or "").lower()
            for i in range(self.mode_combo.count()):
                data = str(self.mode_combo.itemData(i) or "").lower()
                if data == wanted or self.mode_combo.itemText(i).lower() == wanted:
                    self.mode_combo.setCurrentIndex(i)
                    break
            else:
                self._show_error(f"No mode named “{wanted}” is available. Choose one below.")
        else:
            self.app_name.setText(str(action.get("name") or ""))
        self._apply_schedule(parsed.get("schedule") or {})
        self.catch_combo.setCurrentIndex(
            self.catch_combo.findData(parsed.get("catch_up") or "run_once")
        )
        self.create_button.setFocus()

    def _apply_schedule(self, schedule: dict) -> None:
        kind = str(schedule.get("kind") or "once")
        index = self.schedule_combo.findData(kind)
        if index >= 0:
            self.schedule_combo.setCurrentIndex(index)
        if kind == "once":
            moment = _moment(schedule.get("run_at") or "")
            if moment:
                self.once_date.setDate(QDate(moment.year, moment.month, moment.day))
                self.once_time.setTime(QTime(moment.hour, moment.minute))
        elif kind in {"daily", "weekly"}:
            text = str(schedule.get("time") or "09:00")
            hour, minute = (int(part) for part in text.split(":")[:2])
            target = self.daily_time if kind == "daily" else self.weekly_time
            target.setTime(QTime(hour, minute))
            if kind == "weekly":
                selected = set(int(day) for day in schedule.get("weekdays") or [])
                for i, box in enumerate(self.weekday_boxes):
                    box.setChecked(i in selected)
        elif kind == "interval":
            minutes = int(schedule.get("minutes") or 60)
            if minutes % 1440 == 0:
                amount, unit_index = minutes // 1440, 2
            elif minutes % 60 == 0:
                amount, unit_index = minutes // 60, 1
            else:
                amount, unit_index = minutes, 0
            self.interval_value.setValue(max(1, min(720, amount)))
            self.interval_unit.setCurrentIndex(unit_index)

    def _collect_schedule(self) -> dict:
        kind = self._schedule_kind()
        if kind == "once":
            date = self.once_date.date()
            time = self.once_time.time()
            naive = _dt.datetime(date.year(), date.month(), date.day(),
                                 time.hour(), time.minute())
            return {
                "kind": "once",
                "run_at": naive.astimezone().isoformat(),
            }
        if kind == "daily":
            time = self.daily_time.time()
            return {"kind": "daily", "time": f"{time.hour():02d}:{time.minute():02d}"}
        if kind == "weekly":
            days = [i for i, box in enumerate(self.weekday_boxes) if box.isChecked()]
            if not days:
                raise CronError("Choose at least one weekday.")
            time = self.weekly_time.time()
            return {
                "kind": "weekly", "weekdays": days,
                "time": f"{time.hour():02d}:{time.minute():02d}",
            }
        unit = int(self.interval_unit.currentData() or 60)
        minutes = int(self.interval_value.value()) * unit
        return {
            "kind": "interval", "minutes": minutes,
            "start_at": (local_now() + _dt.timedelta(minutes=minutes)).isoformat(),
        }

    def _save(self) -> None:
        self._clear_error()
        try:
            cron = self._cron()
            name = " ".join(self.name_input.text().split())
            if not name:
                raise CronError("Give the job a name.")
            action_type = str(self.action_combo.currentData() or "open_app")
            if action_type == "activate_mode":
                mode = str(self.mode_combo.currentData() or "")
                if not mode:
                    raise CronError("Choose a mode.")
                action = {"type": "activate_mode", "mode": mode}
            else:
                name_text = " ".join(self.app_name.text().split())
                if not name_text:
                    raise CronError("Choose the application for this job.")
                if action_type == "open_app":
                    descriptor = self._selected_app
                    if not descriptor or (
                        name_text != str(descriptor.get("name") or descriptor.get("path") or "")
                    ):
                        descriptor = self._resolve_app(name_text)
                    action = {"type": "open_app", "app": descriptor}
                else:
                    action = {"type": "close_app", "name": name_text}
            enabled = True
            if self._editing_id and hasattr(cron, "store"):
                existing = cron.store.get(self._editing_id)
                if existing is not None and not existing.get("enabled"):
                    # Keep an explicitly paused job paused.  A completed
                    # one-time job becomes active again when rescheduled.
                    enabled = existing.get("last_detail") != "Paused."
            raw = {
                "name": name,
                "enabled": enabled,
                "action": action,
                "schedule": self._collect_schedule(),
                "catch_up": str(self.catch_combo.currentData() or "run_once"),
            }
            # Validate before touching disk, so the form keeps a useful error.
            validate_job(raw, now=local_now())
            if self._editing_id:
                cron.update_job(self._editing_id, raw)
            else:
                cron.create_job(raw)
        except (CronError, ValueError) as exc:
            self._show_error(str(exc))
            return
        except Exception as exc:
            self._show_error(f"Could not save the job: {exc}")
            return
        self._reset_form()
        self.refresh()

    def _reset_form(self) -> None:
        self._editing_id = ""
        self._selected_app = None
        self.name_input.clear()
        self.quick_input.clear()
        self.app_name.clear()
        self.form_title.setText("New job")
        self.create_button.setText("Authorize and create job")
        self.cancel_button.setVisible(False)
        self._clear_error()

    def _cancel_edit(self) -> None:
        self._reset_form()

    def _edit_selected(self) -> None:
        job = self._selected_job()
        if job is None:
            self._show_error("Select a job first.")
            return
        self._clear_error()
        self._editing_id = str(job.get("id"))
        self.form_title.setText(f"Editing: {job.get('name')}")
        self.create_button.setText("Save changes")
        self.cancel_button.setVisible(True)
        self.name_input.setText(str(job.get("name") or ""))
        action = job.get("action") or {}
        kind = str(action.get("type") or "open_app")
        index = self.action_combo.findData(kind)
        if index >= 0:
            self.action_combo.setCurrentIndex(index)
        self._selected_app = None
        if kind == "open_app":
            app = action.get("app") or {}
            self._selected_app = dict(app) if isinstance(app, dict) else None
            self.app_name.setText(str(app.get("name") or action.get("name") or ""))
        elif kind == "close_app":
            self.app_name.setText(str(action.get("name") or ""))
        else:
            wanted = str(action.get("mode") or "")
            mode_index = self.mode_combo.findData(wanted)
            if mode_index >= 0:
                self.mode_combo.setCurrentIndex(mode_index)
        self._apply_schedule(job.get("schedule") or {})
        self.catch_combo.setCurrentIndex(
            self.catch_combo.findData(job.get("catch_up") or "run_once")
        )

    def _toggle_selected(self) -> None:
        job = self._selected_job()
        if job is None or self.cron is None:
            self._show_error("Select a job first.")
            return
        try:
            self.cron.set_enabled(str(job.get("id")), not bool(job.get("enabled")))
        except CronError as exc:
            self._show_error(str(exc))
            return
        self._clear_error()
        self.refresh()

    def _delete_selected(self) -> None:
        job = self._selected_job()
        if job is None or self.cron is None:
            self._show_error("Select a job first.")
            return
        answer = QMessageBox.question(
            self, "Delete scheduled job",
            f"Delete “{job.get('name')}”? This stops future runs.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.cron.delete_job(str(job.get("id")))
        self._selected_job_id = ""
        if self._editing_id == str(job.get("id")):
            self._reset_form()
        self.refresh()
