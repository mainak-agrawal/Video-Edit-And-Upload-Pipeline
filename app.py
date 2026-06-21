"""
app.py
──────
Desktop UI for the Video Edit & Upload Pipeline.

Provides a drag-and-drop / browse interface to select the three input
MP4 files (intro, main content, outro) without needing to manually rename
them to 1.mp4, 2.mp4, 3.mp4.

Run:
    python app.py

Build standalone exe:
    python build.py
"""

import sys
import os
import io
import shutil
import subprocess
import threading
import re
import unicodedata
from datetime import datetime

import json

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QTextEdit, QProgressBar,
    QGroupBox, QFrame, QLineEdit, QDialog, QDialogButtonBox, QCheckBox,
)
from PySide6.QtCore import Qt, Signal, QObject, QPoint
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QPainter, QColor, QPen


YOUTUBE_TITLE_MAX_LEN = 100
YOUTUBE_DESCRIPTION_MAX_BYTES = 5000
YOUTUBE_DESCRIPTION_FORBIDDEN_CHARS = {'<', '>'}


# When frozen by PyInstaller, work from the directory containing the .exe.
# When running as a script, work from the script's directory.
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_FILE = os.path.join(SCRIPT_DIR, 'pipeline_settings.json')


def _load_settings() -> dict:
    """Load persisted settings from pipeline_settings.json."""
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_settings(settings: dict) -> None:
    """Persist settings to pipeline_settings.json."""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Worker signal bridge (thread-safe communication from worker thread to UI)
# ─────────────────────────────────────────────────────────────────────────────

class WorkerSignals(QObject):
    log_message = Signal(str)
    progress = Signal(int)
    finished = Signal(bool, str)  # success, message


# ─────────────────────────────────────────────────────────────────────────────
# Drop zone widget — accepts drag-and-drop of a single .mp4 file
# ─────────────────────────────────────────────────────────────────────────────

class DropZone(QFrame):
    file_selected = Signal(str)

    def __init__(self, label_text: str, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(110)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._update_style(False)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel(label_text)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.title_label.font()
        font.setBold(True)
        font.setPointSize(10)
        self.title_label.setFont(font)

        self.file_label = QLabel("Drag & drop an .mp4 file here\nor click Browse")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.setStyleSheet("color: #888;")

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setFixedWidth(100)
        self.browse_btn.clicked.connect(self._browse)

        layout.addWidget(self.title_label)
        layout.addWidget(self.file_label)
        layout.addWidget(self.browse_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._file_path = None

    @property
    def file_path(self):
        return self._file_path

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select MP4 File", "",
            "MP4 Videos (*.mp4);;All Files (*)"
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._file_path = path
        filename = os.path.basename(path)
        self.file_label.setText(f"✓ {filename}")
        self.file_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        self._update_style(True)
        self.file_selected.emit(path)

    def _update_style(self, has_file: bool):
        if has_file:
            self.setStyleSheet("""
                DropZone {
                    border: 2px solid #4caf50;
                    border-radius: 8px;
                    background-color: #f1f8e9;
                }
            """)
        else:
            self.setStyleSheet("""
                DropZone {
                    border: 2px dashed #aaa;
                    border-radius: 8px;
                    background-color: #fafafa;
                }
            """)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if len(urls) == 1 and urls[0].toLocalFile().lower().endswith('.mp4'):
                event.acceptProposedAction()
                self.setStyleSheet("""
                    DropZone {
                        border: 2px solid #1976d2;
                        border-radius: 8px;
                        background-color: #e3f2fd;
                    }
                """)

    def dragLeaveEvent(self, event):
        self._update_style(self._file_path is not None)

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith('.mp4'):
                self._set_file(path)
                event.acceptProposedAction()


# ─────────────────────────────────────────────────────────────────────────────
# Range slider — two-handle slider for selecting a time range
# ─────────────────────────────────────────────────────────────────────────────

class RangeSlider(QWidget):
    """Horizontal slider with a left and right handle. Paints gray|green|gray."""

    range_changed = Signal(float, float)  # start_seconds, end_seconds

    _HANDLE_R = 8
    _TRACK_H = 6
    _MIN_GAP = 10.0   # handles may not come closer than this (seconds)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.setMinimumWidth(120)
        self._total = 0.0
        self._start = 0.0
        self._end = 0.0
        self._dragging = None   # None | 'left' | 'right'

    # ── public API ────────────────────────────────────────────────────────────

    def set_duration(self, total_seconds: float):
        self._total = float(total_seconds)
        self._start = 0.0
        self._end = self._total
        self.update()
        self.range_changed.emit(self._start, self._end)

    @property
    def start_seconds(self) -> float:
        return self._start

    @property
    def end_seconds(self) -> float:
        return self._end

    def set_start(self, seconds: float):
        if self._total <= 0:
            return
        s = max(0.0, min(float(seconds), self._end - self._MIN_GAP))
        if abs(s - self._start) < 0.01:
            return
        self._start = s
        self.update()
        self.range_changed.emit(self._start, self._end)

    def set_end(self, seconds: float):
        if self._total <= 0:
            return
        e = min(self._total, max(float(seconds), self._start + self._MIN_GAP))
        if abs(e - self._end) < 0.01:
            return
        self._end = e
        self.update()
        self.range_changed.emit(self._start, self._end)

    # ── coordinate helpers ───────────────────────────────────────────────────

    def _track_range(self):
        r = self._HANDLE_R
        return r, self.width() - r

    def _seconds_to_x(self, sec: float) -> int:
        x0, x1 = self._track_range()
        if self._total <= 0:
            return x0
        return int(x0 + (sec / self._total) * (x1 - x0))

    def _x_to_seconds(self, x: int) -> float:
        x0, x1 = self._track_range()
        span = x1 - x0
        if span <= 0 or self._total <= 0:
            return 0.0
        return max(0.0, min(1.0, (x - x0) / span)) * self._total

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        h = self.height()
        r = self._HANDLE_R
        th = self._TRACK_H
        cy = h // 2
        x0, x1 = self._track_range()
        ty = cy - th // 2

        if self._total <= 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#e0e0e0'))
            p.drawRoundedRect(x0, ty, x1 - x0, th, th / 2, th / 2)
            p.end()
            return

        lx = self._seconds_to_x(self._start)
        rx = self._seconds_to_x(self._end)

        p.setPen(Qt.PenStyle.NoPen)

        # Left gray zone
        if lx > x0:
            p.setBrush(QColor('#bdbdbd'))
            p.drawRoundedRect(x0, ty, lx - x0, th, th / 2, th / 2)

        # Green (selected) zone
        if rx > lx:
            p.setBrush(QColor('#4caf50'))
            p.drawRect(lx, ty, rx - lx, th)

        # Right gray zone
        if x1 > rx:
            p.setBrush(QColor('#bdbdbd'))
            p.drawRoundedRect(rx, ty, x1 - rx, th, th / 2, th / 2)

        # Handles (white fill, dark border)
        p.setPen(QPen(QColor('#424242'), 2))
        p.setBrush(QColor('#ffffff'))
        p.drawEllipse(QPoint(lx, cy), r, r)
        p.drawEllipse(QPoint(rx, cy), r, r)

        p.end()

    # ── mouse interaction ─────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if self._total <= 0:
            return
        x = int(event.position().x())
        lx = self._seconds_to_x(self._start)
        rx = self._seconds_to_x(self._end)
        hit = self._HANDLE_R + 4
        dl, dr = abs(x - lx), abs(x - rx)
        if dl <= hit and dr <= hit:
            self._dragging = 'left' if dl <= dr else 'right'
        elif dl <= hit:
            self._dragging = 'left'
        elif dr <= hit:
            self._dragging = 'right'
        if self._dragging:
            self.setCursor(Qt.CursorShape.SizeHorCursor)

    def mouseMoveEvent(self, event):
        if not self._dragging or self._total <= 0:
            return
        sec = self._x_to_seconds(int(event.position().x()))
        if self._dragging == 'left':
            new_s = max(0.0, min(sec, self._end - self._MIN_GAP))
            if abs(new_s - self._start) > 0.01:
                self._start = new_s
                self.update()
                self.range_changed.emit(self._start, self._end)
        else:
            new_e = min(self._total, max(sec, self._start + self._MIN_GAP))
            if abs(new_e - self._end) > 0.01:
                self._end = new_e
                self.update()
                self.range_changed.emit(self._start, self._end)

    def mouseReleaseEvent(self, _event):
        self._dragging = None
        self.setCursor(Qt.CursorShape.ArrowCursor)


# ─────────────────────────────────────────────────────────────────────────────
# TimeControl — MM:SS display with minute/second increment buttons
# ─────────────────────────────────────────────────────────────────────────────

class TimeControl(QWidget):
    """
    Layout: [▲/▼ min] [MM:SS display] [▲/▼ sec]
    The display is read-only (no direct typing) but the cursor can be moved
    inside it to reveal wide minute values.
    """

    time_changed = Signal(float)   # emits new time in seconds

    _MIN_GAP = 10.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._seconds = 0.0
        self._min_limit = 0.0
        self._max_limit = 0.0
        self._other_seconds = 0.0
        self._is_start = True

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        # ── minute arrows ─────────────────────────────────────────────────
        min_col = QVBoxLayout()
        min_col.setSpacing(1)
        min_col.setContentsMargins(0, 0, 0, 0)
        self._min_up = QPushButton("▲")
        self._min_down = QPushButton("▼")
        for b in (self._min_up, self._min_down):
            b.setFixedSize(22, 17)
            b.setStyleSheet(
                "font-size: 9px; padding: 0; "
                "border: 1px solid #aaa; border-radius: 2px; background: #f0f0f0;"
            )
        self._min_up.clicked.connect(self._on_min_up)
        self._min_down.clicked.connect(self._on_min_down)
        min_col.addWidget(self._min_up)
        min_col.addWidget(self._min_down)

        # ── MM:SS display ─────────────────────────────────────────────────
        self._display = QLineEdit("00:00")
        self._display.setReadOnly(True)
        self._display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Wide enough for 3-digit minutes ("999:59"), 4-digit accessible via cursor
        self._display.setMinimumWidth(68)
        self._display.setMaximumWidth(90)
        self._display.setFixedHeight(38)
        self._display.setStyleSheet("""
            QLineEdit {
                font-family: Consolas, monospace;
                font-size: 13px;
                background-color: #f5f5f5;
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 0 4px;
            }
        """)

        # ── second arrows ─────────────────────────────────────────────────
        sec_col = QVBoxLayout()
        sec_col.setSpacing(1)
        sec_col.setContentsMargins(0, 0, 0, 0)
        self._sec_up = QPushButton("▲")
        self._sec_down = QPushButton("▼")
        for b in (self._sec_up, self._sec_down):
            b.setFixedSize(22, 17)
            b.setStyleSheet(
                "font-size: 9px; padding: 0; "
                "border: 1px solid #aaa; border-radius: 2px; background: #f0f0f0;"
            )
        self._sec_up.clicked.connect(self._on_sec_up)
        self._sec_down.clicked.connect(self._on_sec_down)
        sec_col.addWidget(self._sec_up)
        sec_col.addWidget(self._sec_down)

        row.addLayout(min_col)
        row.addWidget(self._display)
        row.addLayout(sec_col)

    # ── public API ────────────────────────────────────────────────────────────

    def configure(self, is_start: bool, min_limit: float, max_limit: float):
        self._is_start = is_start
        self._min_limit = float(round(min_limit))
        self._max_limit = float(round(max_limit))

    def set_other(self, other_seconds: float):
        self._other_seconds = float(round(other_seconds))

    def set_seconds(self, seconds: float, emit: bool = True):
        self._seconds = float(seconds)
        self._refresh()
        if emit:
            self.time_changed.emit(self._seconds)

    def get_seconds(self) -> float:
        return self._seconds

    # ── internals ─────────────────────────────────────────────────────────────

    def _refresh(self):
        total = int(round(self._seconds))
        mins, secs = total // 60, total % 60
        self._display.setText(f"{mins:02d}:{secs:02d}")

    def _try_set(self, new_sec: float) -> bool:
        ns = float(new_sec)
        if ns < self._min_limit - 0.001 or ns > self._max_limit + 0.001:
            return False
        ns = max(self._min_limit, min(ns, self._max_limit))
        if self._is_start:
            if ns > self._other_seconds - self._MIN_GAP:
                return False
        else:
            if ns < self._other_seconds + self._MIN_GAP:
                return False
        self.set_seconds(ns)
        return True

    def _on_min_up(self):
        self._try_set(self._seconds + 60)

    def _on_min_down(self):
        self._try_set(self._seconds - 60)

    def _on_sec_up(self):
        cur = int(round(self._seconds))
        # seconds field wraps 59→0 without affecting minutes
        new_s = (cur % 60 + 1) % 60
        self._try_set((cur // 60) * 60 + new_s)

    def _on_sec_down(self):
        cur = int(round(self._seconds))
        # seconds field wraps 0→59 without affecting minutes
        new_s = (cur % 60 - 1) % 60
        self._try_set((cur // 60) * 60 + new_s)


# ─────────────────────────────────────────────────────────────────────────────
# CropControlWidget — combines TimeControl + RangeSlider + TimeControl
# ─────────────────────────────────────────────────────────────────────────────

class CropControlWidget(QWidget):
    """Full crop row: left TimeControl | RangeSlider | right TimeControl."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total = 0.0
        self._busy = False  # re-entrancy guard

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(10)

        self._start_ctrl = TimeControl()
        self._slider = RangeSlider()
        self._end_ctrl = TimeControl()

        row.addWidget(self._start_ctrl)
        row.addWidget(self._slider, stretch=1)
        row.addWidget(self._end_ctrl)

        self._slider.range_changed.connect(self._on_slider_changed)
        self._start_ctrl.time_changed.connect(self._on_start_ctrl_changed)
        self._end_ctrl.time_changed.connect(self._on_end_ctrl_changed)

    # ── public API ────────────────────────────────────────────────────────────

    def set_duration(self, total_seconds: float):
        self._total = float(total_seconds)
        total_int = float(round(total_seconds))  # whole-second boundary for controls
        self._busy = True
        try:
            self._slider.set_duration(self._total)
            self._start_ctrl.configure(True, 0.0, total_int)
            self._start_ctrl.set_other(total_int)
            self._start_ctrl.set_seconds(0.0, emit=False)
            self._end_ctrl.configure(False, 0.0, total_int)
            self._end_ctrl.set_other(0.0)
            self._end_ctrl.set_seconds(total_int, emit=False)
        finally:
            self._busy = False

    def get_crop_start(self) -> float:
        return self._slider.start_seconds

    def get_crop_end(self) -> float:
        return self._slider.end_seconds

    def is_modified(self) -> bool:
        """Returns True if the crop range differs from the full video defaults."""
        return (self._slider.start_seconds > 0.5 or
                abs(self._slider.end_seconds - self._total) > 0.5)

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_slider_changed(self, start: float, end: float):
        if self._busy:
            return
        self._busy = True
        try:
            self._start_ctrl.set_other(end)
            self._start_ctrl.set_seconds(start, emit=False)
            self._end_ctrl.set_other(start)
            self._end_ctrl.set_seconds(end, emit=False)
        finally:
            self._busy = False

    def _on_start_ctrl_changed(self, seconds: float):
        if self._busy:
            return
        self._busy = True
        try:
            self._end_ctrl.set_other(seconds)
            self._slider.blockSignals(True)
            self._slider.set_start(seconds)
            self._slider.blockSignals(False)
        finally:
            self._busy = False

    def _on_end_ctrl_changed(self, seconds: float):
        if self._busy:
            return
        self._busy = True
        try:
            self._start_ctrl.set_other(seconds)
            self._slider.blockSignals(True)
            self._slider.set_end(seconds)
            self._slider.blockSignals(False)
        finally:
            self._busy = False


# ─────────────────────────────────────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Edit & Upload Pipeline")
        self.setMinimumSize(700, 870)
        self._title_input_default_style = ""

        self._worker_thread = None
        self._signals = WorkerSignals()
        self._signals.log_message.connect(self._append_log)
        self._signals.progress.connect(self._update_progress)
        self._signals.finished.connect(self._on_finished)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # ── Title ────────────────────────────────────────────────────────────
        title = QLabel("Video Edit & Upload Pipeline")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)

        # ── Title input ───────────────────────────────────────────────────
        title_group = QGroupBox("Upload Title")
        title_layout = QHBoxLayout(title_group)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Enter the YouTube video title...")
        self.title_input.setMinimumHeight(30)
        self.title_input.textChanged.connect(self._on_title_changed)
        title_layout.addWidget(self.title_input)

        self.title_error_label = QLabel("")
        self.title_error_label.setStyleSheet("color: #d32f2f; font-size: 11px;")
        self.title_error_label.setVisible(False)
        title_layout.addWidget(self.title_error_label)

        self._title_input_default_style = self.title_input.styleSheet()
        main_layout.addWidget(title_group)

        # ── Description input ─────────────────────────────────────────────
        desc_group = QGroupBox("Description (Optional)")
        desc_layout = QVBoxLayout(desc_group)
        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText(
            "Enter the YouTube video description... "
            "(supports hashtags, links, emojis, etc.)"
        )
        self.desc_input.setMinimumHeight(80)
        self.desc_input.setMaximumHeight(120)
        self.desc_input.setFont(QFont("Segoe UI", 10))
        self.desc_input.textChanged.connect(self._on_description_changed)
        desc_layout.addWidget(self.desc_input)

        self.desc_error_label = QLabel("")
        self.desc_error_label.setStyleSheet("color: #d32f2f; font-size: 11px;")
        self.desc_error_label.setVisible(False)
        desc_layout.addWidget(self.desc_error_label)

        self._desc_input_default_style = self.desc_input.styleSheet()
        main_layout.addWidget(desc_group)

        # ── File selection group ─────────────────────────────────────────────
        files_group = QGroupBox("Select Input Videos")
        files_layout = QHBoxLayout(files_group)
        files_layout.setSpacing(12)

        self.drop1 = DropZone("1 — Intro")
        self.drop2 = DropZone("2 — Main Content")
        self.drop3 = DropZone("3 — Outro")
        self.drop2.file_selected.connect(self._on_main_video_selected)
        self.drop3.file_selected.connect(self._on_outro_selected)

        files_layout.addWidget(self.drop1)
        files_layout.addWidget(self.drop2)
        files_layout.addWidget(self.drop3)

        main_layout.addWidget(files_group)

        # ── Crop section (hidden until main video is loaded) ──────────────────
        self._crop_group = QGroupBox("Crop Main Content Video (Optional)")
        _crop_inner = QVBoxLayout(self._crop_group)
        _crop_inner.setContentsMargins(8, 8, 8, 8)
        self._crop_widget = CropControlWidget()
        _crop_inner.addWidget(self._crop_widget)
        self._crop_group.setVisible(False)
        main_layout.addWidget(self._crop_group)

        # ── Pre-load saved default outro ─────────────────────────────────────
        settings = _load_settings()
        default_outro = settings.get('default_outro_path', '')
        if default_outro and os.path.isfile(default_outro):
            self.drop3._set_file(default_outro)

        # ── Run button ───────────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton("▶  Run Pipeline")
        self.run_btn.setFixedHeight(40)
        self.run_btn.setStyleSheet("""
            QPushButton {
                background-color: #1976d2;
                color: white;
                font-size: 13px;
                font-weight: bold;
                border-radius: 6px;
                padding: 0 24px;
            }
            QPushButton:hover { background-color: #1565c0; }
            QPushButton:disabled { background-color: #bbb; }
        """)
        self.run_btn.clicked.connect(self._run_pipeline)
        btn_layout.addStretch()
        btn_layout.addWidget(self.run_btn)
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        # ── Progress bar ─────────────────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v%")
        main_layout.addWidget(self.progress_bar)

        # ── Log output ───────────────────────────────────────────────────────
        log_group = QGroupBox("Pipeline Output")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", 9))
        self.log_output.setMinimumHeight(200)
        log_layout.addWidget(self.log_output)
        main_layout.addWidget(log_group)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_outro_selected(self, path: str):
        """Prompt user to save the chosen outro as the default."""
        settings = _load_settings()
        # Skip prompt if this path is already the saved default.
        if settings.get('default_outro_path') == path:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Save Default Outro")
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        info = QLabel(
            f"<b>{os.path.basename(path)}</b><br>"
            "<span style='color:#555;font-size:11px;'>"
            f"{path}</span>"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        checkbox = QCheckBox("Set this outro video as default for future sessions")
        checkbox.setChecked(True)
        layout.addWidget(checkbox)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted and checkbox.isChecked():
            settings['default_outro_path'] = path
            _save_settings(settings)

    def _on_main_video_selected(self, path: str):
        """Called when the main content video (2.mp4) is browsed/dropped.
        Reads the video duration via ffprobe and shows the crop controls."""
        try:
            result = subprocess.check_output(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-show_format', path],
                stderr=subprocess.DEVNULL,
            )
            duration = float(json.loads(result.decode('utf-8'))['format']['duration'])
            self._crop_widget.set_duration(duration)
            self._crop_group.setVisible(True)
        except Exception:
            self._crop_group.setVisible(False)

    def _run_pipeline(self):
        # Validate all files are selected
        paths = [self.drop1.file_path, self.drop2.file_path, self.drop3.file_path]
        missing = []
        if not paths[0]:
            missing.append("1 — Intro")
        if not paths[1]:
            missing.append("2 — Main Content")
        if not paths[2]:
            missing.append("3 — Outro")

        if missing:
            self._append_log(f"[ERROR] Please select all 3 files. Missing: {', '.join(missing)}")
            return

        title = self.title_input.text()
        is_valid, title_error = self._validate_title(title)
        if not is_valid:
            self._set_title_error(title_error)
            self._append_log(f"[ERROR] {title_error}")
            return

        description = self.desc_input.toPlainText()
        desc_valid, desc_error = self._validate_description(description)
        if not desc_valid:
            self._set_desc_error(desc_error)
            self._append_log(f"[ERROR] {desc_error}")
            return

        self._clear_title_error()
        self._clear_desc_error()
        title = title.strip()

        # ── Crop confirmation ────────────────────────────────────────────────
        crop_start: float | None = None
        crop_end:   float | None = None
        if self._crop_group.isVisible() and self._crop_widget.is_modified():
            cs = self._crop_widget.get_crop_start()
            ce = self._crop_widget.get_crop_end()

            def _fmt(sec: float) -> str:
                s = int(round(sec))
                return f"{s // 60:02d}:{s % 60:02d}"

            dlg = QDialog(self)
            dlg.setWindowTitle("Confirm Crop")
            dlg.setMinimumWidth(420)
            dlg_layout = QVBoxLayout(dlg)
            dlg_layout.setSpacing(14)
            dlg_layout.setContentsMargins(16, 16, 16, 16)

            msg = QLabel(
                f"Are you sure you want to upload the cropped video between "
                f"<b>{_fmt(cs)}</b> and <b>{_fmt(ce)}</b>?"
            )
            msg.setWordWrap(True)
            dlg_layout.addWidget(msg)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            yes_btn = QPushButton("Yes, crop and upload")
            yes_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1976d2; color: white;
                    font-weight: bold; border-radius: 4px; padding: 6px 18px;
                }
                QPushButton:hover { background-color: #1565c0; }
            """)
            no_btn = QPushButton("No, cancel")
            no_btn.setStyleSheet("padding: 6px 18px; border-radius: 4px;")
            yes_btn.clicked.connect(dlg.accept)
            no_btn.clicked.connect(dlg.reject)
            btn_row.addWidget(yes_btn)
            btn_row.addWidget(no_btn)
            dlg_layout.addLayout(btn_row)

            if dlg.exec() != QDialog.DialogCode.Accepted:
                return   # user pressed "No, cancel" — leave everything intact

            crop_start = cs
            crop_end = ce

        # Disable button
        self.run_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_output.clear()

        # Run in background thread
        _cs, _ce = crop_start, crop_end
        self._worker_thread = threading.Thread(
            target=lambda: self._pipeline_worker(
                paths[0], paths[1], paths[2], title, description,
                crop_start=_cs, crop_end=_ce,
            ),
            daemon=True,
        )
        self._worker_thread.start()

    def _pipeline_worker(self, file1: str, file2: str, file3: str, title: str, description: str,
                          crop_start: float | None = None, crop_end: float | None = None):
        """Runs in a background thread. Copies files, executes pipeline in-process."""
        try:
            self._signals.log_message.emit("Preparing input files...")
            self._signals.progress.emit(5)

            # Copy selected files to working directory as 1.mp4, 2.mp4, 3.mp4
            targets = [
                (file1, os.path.join(SCRIPT_DIR, '1.mp4')),
                (file2, os.path.join(SCRIPT_DIR, '2.mp4')),
                (file3, os.path.join(SCRIPT_DIR, '3.mp4')),
            ]

            for src, dst in targets:
                # Skip copy if source IS the destination
                if os.path.abspath(src) == os.path.abspath(dst):
                    self._signals.log_message.emit(f"  {os.path.basename(dst)} already in place")
                    continue
                self._signals.log_message.emit(f"  Copying {os.path.basename(src)} → {os.path.basename(dst)}")
                shutil.copy2(src, dst)

            self._signals.progress.emit(10)

            # Change to script directory so relative paths in modules work
            original_cwd = os.getcwd()
            os.chdir(SCRIPT_DIR)

            # Import pipeline modules (deferred import so PyInstaller picks them up).
            # When frozen, the .py scripts are bundled in sys._MEIPASS.
            import importlib
            import importlib.util

            def _import_script(name):
                """Import a script by name, handling both frozen and normal modes."""
                # In frozen mode, scripts are in the PyInstaller temp dir
                if getattr(sys, 'frozen', False):
                    base_path = sys._MEIPASS
                else:
                    base_path = SCRIPT_DIR
                file_path = os.path.join(base_path, f'{name}.py')
                spec = importlib.util.spec_from_file_location(name, file_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod

            merge_videos = _import_script('merge-videos')
            extract_thumbnail = _import_script('extract-thumbnail')
            upload_to_youtube = _import_script('upload-to-youtube')

            _cs, _ce = crop_start, crop_end
            steps = [
                ("Step 1 — Merge Videos",
                 lambda: merge_videos.main(crop_start=_cs, crop_end=_ce), 30),
                ("Step 2 — Extract Thumbnail", extract_thumbnail.main, 60),
                ("Step 3 — Upload to YouTube",
                 lambda: upload_to_youtube.main(title=title, description=description), 80),
            ]

            for step_name, step_fn, progress_pct in steps:
                self._signals.log_message.emit(f"\n{'=' * 60}")
                self._signals.log_message.emit(f"  {step_name}")
                self._signals.log_message.emit('=' * 60)
                self._signals.progress.emit(progress_pct)

                # Capture stdout from each step and relay to UI
                self._run_step_capturing_output(step_name, step_fn)

            os.chdir(original_cwd)
            self._signals.progress.emit(100)

            # Rename final_output.mp4 → "Final <original name of file2>"
            final_output = os.path.join(SCRIPT_DIR, 'final_output.mp4')
            if os.path.exists(final_output):
                original_name = os.path.splitext(os.path.basename(file2))[0]
                renamed = os.path.join(SCRIPT_DIR, f"Final {original_name}.mp4")
                # Avoid overwriting an existing file from a previous run
                if os.path.exists(renamed):
                    os.remove(renamed)
                os.rename(final_output, renamed)
                self._signals.log_message.emit(f"Renamed final_output.mp4 → Final {original_name}.mp4")

            self._signals.finished.emit(True, "Pipeline completed successfully!")

        except Exception as e:
            self._signals.finished.emit(False, f"Error: {e}")
        finally:
            # Always clean up temporary 1.mp4, 2.mp4, 3.mp4 copies
            for name in ('1.mp4', '2.mp4', '3.mp4'):
                tmp = os.path.join(SCRIPT_DIR, name)
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
            self._signals.log_message.emit("Cleaned up temporary input copies (1/2/3.mp4)")
            os.chdir(original_cwd if 'original_cwd' in dir() else SCRIPT_DIR)

    def _run_step_capturing_output(self, step_name: str, step_fn):
        """Run a pipeline step function, capturing its print output line by line."""
        # Use a custom stream that emits each line to the UI
        class SignalStream(io.TextIOBase):
            def __init__(self, signal):
                self._signal = signal
                self._buffer = ""

            def write(self, text):
                self._buffer += text
                while '\n' in self._buffer:
                    line, self._buffer = self._buffer.split('\n', 1)
                    if line.strip():
                        self._signal.emit(line)
                return len(text)

            def flush(self):
                if self._buffer.strip():
                    self._signal.emit(self._buffer)
                    self._buffer = ""

        stream = SignalStream(self._signals.log_message)
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = stream
        sys.stderr = stream
        try:
            step_fn()
        except SystemExit as e:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            if e.code and e.code != 0:
                raise RuntimeError(f"{step_name} failed (exit code {e.code})") from e
        except Exception as e:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            raise RuntimeError(f"{step_name} failed: {e}") from e
        finally:
            stream.flush()
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _normalize_title_for_validation(self, title: str) -> str:
        if title is None:
            title = ""
        normalized = unicodedata.normalize('NFKC', str(title))
        normalized = ''.join(
            ch for ch in normalized
            if unicodedata.category(ch) not in {'Cc', 'Cf', 'Cs', 'Co', 'Cn'}
        )
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    def _validate_title(self, title: str):
        normalized = self._normalize_title_for_validation(title)
        if not normalized:
            return False, "Please enter a valid upload title (not empty)."
        if len(normalized) > YOUTUBE_TITLE_MAX_LEN:
            return False, (
                f"Title is too long ({len(normalized)} chars). "
                f"YouTube allows up to {YOUTUBE_TITLE_MAX_LEN} characters."
            )
        return True, ""

    def _validate_description(self, description: str):
        if not description:
            return True, ""
        forbidden = [ch for ch in description if ch in YOUTUBE_DESCRIPTION_FORBIDDEN_CHARS]
        if forbidden:
            unique = sorted(set(forbidden))
            return False, (
                f"Description contains forbidden character(s): {' '.join(repr(c) for c in unique)}  "
                f"(YouTube does not allow < or > in descriptions.)"
            )
        byte_len = len(description.encode('utf-8'))
        if byte_len > YOUTUBE_DESCRIPTION_MAX_BYTES:
            return False, (
                f"Description is too long ({byte_len} bytes). "
                f"YouTube allows up to {YOUTUBE_DESCRIPTION_MAX_BYTES} bytes."
            )
        return True, ""

    def _set_desc_error(self, message: str):
        self.desc_input.setStyleSheet(
            "QTextEdit { border: 2px solid #d32f2f; border-radius: 4px; background-color: #ffebee; }"
        )
        self.desc_error_label.setText(message)
        self.desc_error_label.setVisible(True)

    def _clear_desc_error(self):
        self.desc_input.setStyleSheet(self._desc_input_default_style)
        self.desc_error_label.setVisible(False)
        self.desc_error_label.setText("")

    def _on_description_changed(self):
        description = self.desc_input.toPlainText()
        is_valid, desc_error = self._validate_description(description)
        if is_valid:
            self._clear_desc_error()
        else:
            self._set_desc_error(desc_error)

    def _set_title_error(self, message: str):
        self.title_input.setStyleSheet(
            "QLineEdit { border: 2px solid #d32f2f; border-radius: 4px; background-color: #ffebee; }"
        )
        self.title_error_label.setText(message)
        self.title_error_label.setVisible(True)

    def _clear_title_error(self):
        self.title_input.setStyleSheet(self._title_input_default_style)
        self.title_error_label.setVisible(False)
        self.title_error_label.setText("")

    def _on_title_changed(self, text: str):
        is_valid, title_error = self._validate_title(text)
        if is_valid:
            self._clear_title_error()
        else:
            self._set_title_error(title_error)

    # ── Slots ────────────────────────────────────────────────────────────────

    def _append_log(self, text: str):
        self.log_output.append(text)
        # Auto-scroll to bottom
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _update_progress(self, value: int):
        self.progress_bar.setValue(value)

    def _on_finished(self, success: bool, message: str):
        self.run_btn.setEnabled(True)
        if success:
            self._append_log(f"\n✓ {message}")
            self.progress_bar.setFormat("Complete!")
        else:
            self._append_log(f"\n✗ {message}")
            self.progress_bar.setFormat("Failed")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
