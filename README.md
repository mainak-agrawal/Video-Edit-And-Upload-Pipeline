# Video Edit And Upload Pipeline

Automates merging multiple video clips with crossfade transitions, extracting a thumbnail, and uploading the result to YouTube.

> **Platform:** Tested on Windows only.

---

## Prerequisites

### 1. Python
Install Python 3.8+ from https://www.python.org/downloads/  
Make sure `python` is on your PATH (check the *"Add Python to PATH"* option during install).

### 2. FFmpeg
FFmpeg is used for all video processing.

Install via [Chocolatey](https://chocolatey.org/install) (run in an elevated terminal):

```bat
choco install ffmpeg
```

This automatically installs `ffmpeg` and `ffprobe` and adds them to your system PATH.

### 3. Python dependencies
Only required for the YouTube upload script:

```bat
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

### 4. YouTube API credentials (one-time setup)
Required only for `upload-to-youtube.py` / `upload-to-youtube.bat`:

1. Go to https://console.cloud.google.com/ and create or select a project.
2. Enable the **YouTube Data API v3** for the project.
3. Go to **APIs & Services → OAuth consent screen**. Set the app to **External** and add the Google account you want to upload from under **Test users**. (While the app is in testing mode, only listed test users can authorise it.)
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**.
   - Application type: **Desktop app**
5. Download the JSON file and save it as `client_secrets.json` in this folder.
6. Run the upload script once — a browser window will open asking you to sign in and grant permission. After approval, `oauth_token.json` is saved locally and reused for future runs.

> `client_secrets.json` and `oauth_token.json` are excluded from version control via `.gitignore`. Never commit these files.

---

## Input Files

All scripts expect exactly three video files named `1.mp4`, `2.mp4`, and `3.mp4` to be present in the **same folder as the scripts**. No other path configuration is needed — the scripts resolve files relative to their own location.

| File | Description |
|------|-------------|
| `1.mp4` | First video clip |
| `2.mp4` | Second video clip (main content, typically the longest) |
| `3.mp4` | Third video clip |

**Original context:** This project was built to assemble YouTube videos made up of a title/opening slide (`1.mp4`), the main recorded content (`2.mp4`), and an exit/thank-you slide (`3.mp4`).

That said, there is no hard dependency on this structure — any three video files can be merged with crossfade transitions using this project, as long as they are named `1.mp4`, `2.mp4`, and `3.mp4`.

---

## Scripts

### `merge-videos.py`
Merges `1.mp4`, `2.mp4`, and `3.mp4` into a single `final_output.mp4` with smooth crossfade transitions between clips.

- Normalises all clips to the resolution and frame rate of `2.mp4`.
- Uses a keyframe-aware approach to avoid the common stream-copy freeze/jerk artefacts at cut points.
- Outputs: `final_output.mp4`

### `merge-videos-long.py` *(experimental)*
An improved variant of `merge-videos.py` that fixes audio sync and frame rate issues discovered in the original script. Trade-off is a significantly longer processing time due to more extensive re-encoding.

- Not currently the default in `run-pipeline.py` — run it manually via `merge-videos-long.bat` if needed.
- Outputs: `final_output.mp4`

### `extract-thumbnail.py`
Extracts a single frame from `1.mp4` at 85% through the clip as a high-quality PNG thumbnail. This targets the point where any intro animation is fully settled.

- Outputs: `thumbnail.png`

### `upload-to-youtube.py`
Uploads `final_output.mp4` to YouTube as a **private** video and sets `thumbnail.png` as the thumbnail.

- The video title is automatically set to the name of the folder this script lives in (e.g. folder `My Trip 2025` → title `My Trip 2025`). Override with `TITLE_OVERRIDE` inside the script.
- Uploaded with: empty description, no tags, not for kids, no age restriction.
- Requires `client_secrets.json` and will create `oauth_token.json` on first run (see setup above).

### `run-pipeline.py`
Master orchestrator that runs all three pipeline steps in order:

1. `merge-videos.py` — merge clips
2. `extract-thumbnail.py` — extract thumbnail
3. `upload-to-youtube.py` — upload to YouTube

All output is logged to the console with timestamps and saved to a `log_<YYYYMMDD_HHMMSS>.txt` file.

---

## Running with Batch Files

Each script has a corresponding `.bat` file for easy double-click execution. They all change to the script's directory automatically before running.

| Batch file | What it runs |
|------------|--------------|
| `run-pipeline.bat` | Full pipeline (merge → thumbnail → upload) |
| `merge-videos.bat` | Merge step only |
| `merge-videos-long.bat` | Merge step (long video variant) |
| `extract-thumbnail.bat` | Thumbnail extraction only |
| `upload-to-youtube.bat` | YouTube upload only |

Double-click any `.bat` file or run it from a terminal. The window stays open after completion so you can review the output.

---

## Output Files

| File | Produced by |
|------|-------------|
| `final_output.mp4` | `merge-videos.py` / `merge-videos-long.py` |
| `thumbnail.png` | `extract-thumbnail.py` |
| `log_<timestamp>.txt` | `run-pipeline.py` |
| `oauth_token.json` | `upload-to-youtube.py` (first run only) |
