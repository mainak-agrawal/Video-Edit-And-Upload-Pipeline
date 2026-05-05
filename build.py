"""
build.py
────────
Builds the Video Edit & Upload Pipeline into a standalone Windows .exe
using PyInstaller. The resulting executable includes Python, all packages,
and the pipeline scripts — no Python installation needed on the target machine.

Prerequisites:
    pip install pyinstaller

Usage:
    python build.py

Output:
    dist/VideoEditPipeline.exe
"""

import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    # Pipeline modules that are imported at runtime via importlib
    # (because they have hyphens in their names). These must be bundled as data.
    pipeline_scripts = [
        'merge-videos.py',
        'merge-videos-long.py',
        'extract-thumbnail.py',
        'upload-to-youtube.py',
    ]

    # Credentials files are NOT bundled — user must place them next to the exe.
    # (client_secrets.json, oauth_token.json)

    # Build --add-data arguments (source;destination on Windows)
    add_data_args = []
    for script in pipeline_scripts:
        script_path = os.path.join(SCRIPT_DIR, script)
        if os.path.exists(script_path):
            add_data_args += ['--add-data', f'{script_path};.']

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onedir',
        '--windowed',
        '--name', 'VideoEditPipeline',
        # Icon (uncomment and provide .ico if you have one):
        # '--icon', 'app.ico',
        # Hidden imports for packages used by the pipeline scripts
        '--hidden-import', 'google.auth',
        '--hidden-import', 'google.auth.transport',
        '--hidden-import', 'google.auth.transport.requests',
        '--hidden-import', 'google.oauth2',
        '--hidden-import', 'google.oauth2.credentials',
        '--hidden-import', 'google_auth_oauthlib',
        '--hidden-import', 'google_auth_oauthlib.flow',
        '--hidden-import', 'googleapiclient',
        '--hidden-import', 'googleapiclient.discovery',
        '--hidden-import', 'googleapiclient.errors',
        '--hidden-import', 'googleapiclient.http',
        '--hidden-import', 'httplib2',
        *add_data_args,
        os.path.join(SCRIPT_DIR, 'app.py'),
    ]

    print("Building with PyInstaller...")
    print(f"Command: {' '.join(cmd)}\n")
    subprocess.run(cmd, check=True)
    print("\nBuild complete! Output: dist/VideoEditPipeline/VideoEditPipeline.exe")
    print("  Place client_secrets.json (and oauth_token.json if you have one)")
    print("  in the dist/VideoEditPipeline/ folder before running.")


if __name__ == '__main__':
    main()
