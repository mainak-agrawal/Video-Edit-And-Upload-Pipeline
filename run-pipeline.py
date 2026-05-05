"""
run_pipeline.py
───────────────
Master orchestrator. Runs the three pipeline steps in order:
  1. merge_videos.py      — joins 1.mp4 + 2.mp4 + 3.mp4 with crossfade transitions
  2. extract_thumbnail.py — extracts a thumbnail from 1.mp4
  3. upload_to_youtube.py — uploads final_output.mp4 + thumbnail.png to YouTube

All output (from this script AND from each child script) is written to:
  • the console  (with timestamps)
  • log_<YYYYMMDD_HHMMSS>.txt  (created in the same folder as this script)
"""

import subprocess
import sys
import os
import threading
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# Resolve paths relative to this script's location so the pipeline works
# regardless of which directory you launch it from.
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    {
        'name':        'Step 1 — Merge Videos',
        'description': 'Joins 1.mp4, 2.mp4, and 3.mp4 with crossfade transitions '
                       'and produces final_output.mp4',
        'script':      os.path.join(SCRIPT_DIR, 'merge-videos.py'),
        'next':        'extract-thumbnail.py will extract a high-quality frame '
                       'from 1.mp4 as the YouTube thumbnail.',
    },
    {
        'name':        'Step 2 — Extract Thumbnail',
        'description': 'Extracts a frame near the end of 1.mp4 (when the title '
                       'animation is fully rendered) and saves it as thumbnail.png',
        'script':      os.path.join(SCRIPT_DIR, 'extract-thumbnail.py'),
        'next':        'upload-to-youtube.py will upload final_output.mp4 with '
                       'thumbnail.png to YouTube as a private video.',
    },
    {
        'name':        'Step 3 — Upload to YouTube',
        'description': 'Uploads final_output.mp4 as a private YouTube video and '
                       'sets thumbnail.png as its thumbnail',
        'script':      os.path.join(SCRIPT_DIR, 'upload-to-youtube.py'),
        'next':        None,   # last step
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Logging — every message goes to stdout AND the log file simultaneously.
# ─────────────────────────────────────────────────────────────────────────────

_log_file = None   # opened in main()

def log(message='', level='INFO'):
    """Write a timestamped line to console and log file."""
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [{level}] {message}'
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + '\n')
        _log_file.flush()

def log_raw(line):
    """Write a raw line (from child process stdout/stderr) with a timestamp prefix."""
    ts        = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted = f'[{ts}] [CHILD] {line}'
    print(formatted, flush=True)
    if _log_file:
        _log_file.write(formatted + '\n')
        _log_file.flush()

def log_banner(title):
    border = '=' * 64
    log('')
    log(border)
    log(f'  {title}')
    log(border)


# ─────────────────────────────────────────────────────────────────────────────
# Child-process runner — streams stdout+stderr line by line in real time.
# ─────────────────────────────────────────────────────────────────────────────

def _stream_reader(stream, stop_event):
    """Read lines from a stream and log them until EOF or stop_event is set."""
    try:
        for raw_line in iter(stream.readline, ''):
            if stop_event.is_set():
                break
            stripped = raw_line.rstrip('\n').rstrip('\r')
            if stripped:          # skip blank lines for cleaner logs
                log_raw(stripped)
    except Exception:
        pass
    finally:
        stream.close()


def run_script(step):
    """
    Launch a child Python script as a subprocess.
    Stream its stdout+stderr live to console and log file.
    Returns True on success, False on failure.
    """
    script = step['script']

    if not os.path.exists(script):
        log(f'Script not found: {script}', level='ERROR')
        return False

    log(f'Launching: {sys.executable} {script}')

    proc = subprocess.Popen(
        [sys.executable, script],
        cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # merge stderr into stdout so order is preserved
        text=True,
        bufsize=1,                  # line-buffered
        encoding='utf-8',
        errors='replace',
    )

    stop_event = threading.Event()
    reader     = threading.Thread(
        target=_stream_reader,
        args=(proc.stdout, stop_event),
        daemon=True,
    )
    reader.start()

    try:
        proc.wait()
    except KeyboardInterrupt:
        log('KeyboardInterrupt — terminating child process ...', level='WARN')
        proc.terminate()
        proc.wait()
        stop_event.set()
        reader.join(timeout=5)
        raise

    stop_event.set()
    reader.join(timeout=10)

    return proc.returncode == 0


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global _log_file

    run_ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(SCRIPT_DIR, f'log_{run_ts}.txt')

    _log_file = open(log_path, 'w', encoding='utf-8')

    log_banner('VIDEO PIPELINE - STARTING')
    log(f'Log file : {log_path}')
    log(f'Work dir : {SCRIPT_DIR}')
    log(f'Python   : {sys.executable}')
    log()
    log(f'Pipeline has {len(STEPS)} steps:')
    for i, step in enumerate(STEPS, 1):
        log(f'  {i}. {step["name"]}')

    overall_start = datetime.now()
    failed_step   = None

    for i, step in enumerate(STEPS, 1):
        step_start = datetime.now()

        # ── Pre-step banner ──────────────────────────────────────────────────
        log_banner(f'{step["name"]}  ({i}/{len(STEPS)})')
        log(f'What this step does : {step["description"]}')
        if step['next']:
            log(f'After this step     : {step["next"]}')
        else:
            log('After this step     : Pipeline complete.')
        log(f'Script              : {step["script"]}')
        log('')
        log(f'Starting {step["name"]} ...')

        # ── Run ──────────────────────────────────────────────────────────────
        success = run_script(step)

        # ── Post-step result ─────────────────────────────────────────────────
        elapsed = (datetime.now() - step_start).total_seconds()
        log('')
        if success:
            log(f'{step["name"]} completed successfully in {elapsed:.1f}s')
        else:
            log(f'{step["name"]} FAILED after {elapsed:.1f}s', level='ERROR')
            log('Pipeline aborted. Fix the error above and re-run.', level='ERROR')
            failed_step = step['name']
            break

        if step['next']:
            log('')
            log(f'Next up: {step["next"]}')

    # ── Final summary ────────────────────────────────────────────────────────
    total_elapsed = (datetime.now() - overall_start).total_seconds()
    log_banner('PIPELINE SUMMARY')

    if failed_step:
        log(f'Result  : FAILED at "{failed_step}"', level='ERROR')
    else:
        log('Result  : ALL STEPS COMPLETED SUCCESSFULLY')

    log(f'Duration: {total_elapsed:.1f}s  ({total_elapsed/60:.1f} min)')
    log(f'Log saved to: {log_path}')

    _log_file.close()

    sys.exit(0 if not failed_step else 1)


if __name__ == '__main__':
    main()