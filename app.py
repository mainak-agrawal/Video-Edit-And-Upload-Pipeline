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
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont


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
        self.drop3.file_selected.connect(self._on_outro_selected)

        files_layout.addWidget(self.drop1)
        files_layout.addWidget(self.drop2)
        files_layout.addWidget(self.drop3)

        main_layout.addWidget(files_group)

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

        # Disable button
        self.run_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_output.clear()

        # Run in background thread
        self._worker_thread = threading.Thread(
            target=self._pipeline_worker,
            args=(paths[0], paths[1], paths[2], title, description),
            daemon=True,
        )
        self._worker_thread.start()

    def _pipeline_worker(self, file1: str, file2: str, file3: str, title: str, description: str):
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

            steps = [
                ("Step 1 — Merge Videos", merge_videos.main, 30),
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
