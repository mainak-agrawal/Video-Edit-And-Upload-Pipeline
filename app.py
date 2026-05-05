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
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QTextEdit, QProgressBar,
    QGroupBox, QFrame, QLineEdit,
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont


# When frozen by PyInstaller, work from the directory containing the .exe.
# When running as a script, work from the script's directory.
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


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
        self.setMinimumHeight(80)
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
        self.setMinimumSize(700, 650)

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
        title_layout.addWidget(self.title_input)
        main_layout.addWidget(title_group)

        # ── File selection group ─────────────────────────────────────────────
        files_group = QGroupBox("Select Input Videos")
        files_layout = QHBoxLayout(files_group)
        files_layout.setSpacing(12)

        self.drop1 = DropZone("1 — Intro")
        self.drop2 = DropZone("2 — Main Content")
        self.drop3 = DropZone("3 — Outro")

        files_layout.addWidget(self.drop1)
        files_layout.addWidget(self.drop2)
        files_layout.addWidget(self.drop3)

        main_layout.addWidget(files_group)

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

        title = self.title_input.text().strip()
        if not title:
            self._append_log("[ERROR] Please enter a title for the upload.")
            return

        # Disable button
        self.run_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_output.clear()

        # Run in background thread
        self._worker_thread = threading.Thread(
            target=self._pipeline_worker,
            args=(paths[0], paths[1], paths[2], title),
            daemon=True,
        )
        self._worker_thread.start()

    def _pipeline_worker(self, file1: str, file2: str, file3: str, title: str):
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

            # Set title via environment variable for upload step
            os.environ['PIPELINE_TITLE'] = title

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
                ("Step 3 — Upload to YouTube", upload_to_youtube.main, 80),
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

            # Clean up the temporary 1.mp4, 2.mp4, 3.mp4 copies
            for name in ('1.mp4', '2.mp4', '3.mp4'):
                tmp = os.path.join(SCRIPT_DIR, name)
                if os.path.exists(tmp):
                    os.remove(tmp)
            self._signals.log_message.emit("Cleaned up temporary input copies (1/2/3.mp4)")

            self._signals.finished.emit(True, "Pipeline completed successfully!")

        except Exception as e:
            self._signals.finished.emit(False, f"Error: {e}")
        finally:
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
        except Exception as e:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            raise RuntimeError(f"{step_name} failed: {e}") from e
        finally:
            stream.flush()
            sys.stdout = old_stdout
            sys.stderr = old_stderr

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
