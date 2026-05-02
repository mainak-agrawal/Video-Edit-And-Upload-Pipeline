"""
upload_to_youtube.py
────────────────────
Uploads final_output.mp4 to YouTube and sets thumbnail.png as its thumbnail.
- Title is automatically set to the name of the folder this script lives in.
- Uploaded as Private, empty description, Not for kids, no age restriction.

SETUP (one-time):
─────────────────
1. Go to https://console.cloud.google.com/
2. Create a project (or select an existing one).
3. Enable the "YouTube Data API v3" for the project.
4. Go to "APIs & Services" → "Credentials" → "Create Credentials"
   → "OAuth 2.0 Client ID" → Application type: "Desktop app".
5. Download the JSON file and save it as  client_secrets.json
   in the same folder as this script.
6. Run this script once — a browser window will open asking you to
   log in with the YouTube account you want to upload to and grant
   permission.  After that a token file (oauth_token.json) is saved
   locally so you won't need to log in again.
7. Install dependencies:
      pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

═══════════════════════════════════════════════════════════════════════════════
EDIT THE SETTINGS BELOW BEFORE RUNNING  (if needed)
═══════════════════════════════════════════════════════════════════════════════
"""

# ── Files ────────────────────────────────────────────────────────────────────
VIDEO_FILE     = 'final_output.mp4'
THUMBNAIL_FILE = 'thumbnail.png'
CLIENT_SECRETS = 'client_secrets.json'   # OAuth credentials downloaded from GCP
TOKEN_FILE     = 'oauth_token.json'      # Created automatically after first login

# ── Video metadata ────────────────────────────────────────────────────────────
# Title is derived automatically from the folder name — no need to set it here.
# (e.g. if the script is in  C:\Videos\My Trip 2025\  the title will be
#  "My Trip 2025")
# Override by replacing None with a string: TITLE_OVERRIDE = 'My Custom Title'
TITLE_OVERRIDE = None

DESCRIPTION = ''          # Empty description as required
TAGS        = []           # No tags

# Fixed per requirements — do not change these three.
PRIVACY     = 'private'   # Upload as Private
MADE_FOR_KIDS = False      # Not for kids
CONTAINS_SYNTHETIC_MEDIA = False  # No AI-generated content declaration needed

# Category ID — 22 = "People & Blogs", common default.
# Full list: https://developers.google.com/youtube/v3/docs/videoCategories/list
CATEGORY_ID = '22'

# ═════════════════════════════════════════════════════════════════════════════
# Nothing below this line normally needs to be changed.
# ═════════════════════════════════════════════════════════════════════════════

import os
import sys
import json
import time

try:
    import googleapiclient.discovery
    import googleapiclient.errors
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    sys.exit(
        "Missing dependencies. Run:\n"
        "  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
    )

# youtube.upload is sufficient for inserting a video and setting its thumbnail.
# Requesting the broader youtube scope caused Google to silently downgrade it
# and the OAuth library would then raise a scope-mismatch error.
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
]

CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB resumable-upload chunks


def get_authenticated_service():
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing access token ...")
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRETS):
                sys.exit(
                    f"ERROR: '{CLIENT_SECRETS}' not found.\n"
                    "Download your OAuth 2.0 Desktop credentials JSON from "
                    "https://console.cloud.google.com/ and save it as "
                    f"'{CLIENT_SECRETS}' next to this script."
                )
            print("Opening browser for YouTube login ...")
            # OAUTHLIB_RELAX_TOKEN_SCOPE=1 tells requests-oauthlib not to raise
            # an error when Google returns a subset of the requested scopes.
            # This can happen in Testing mode where Google quietly narrows scopes.
            os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
        print(f"Token saved to '{TOKEN_FILE}'.")

    return googleapiclient.discovery.build('youtube', 'v3', credentials=creds)


def upload_video(youtube):
    if not os.path.exists(VIDEO_FILE):
        sys.exit(f"ERROR: Video file '{VIDEO_FILE}' not found.")

    file_size_mb = os.path.getsize(VIDEO_FILE) / (1024 * 1024)
    print(f"\nUploading '{VIDEO_FILE}'  ({file_size_mb:.1f} MB) ...")

    # Derive title from the folder containing this script unless overridden.
    if TITLE_OVERRIDE:
        title = TITLE_OVERRIDE
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        title = os.path.basename(script_dir)
    print(f"  Title (from folder name): {title!r}")

    body = {
        'snippet': {
            'title':       title,
            'description': DESCRIPTION,
            'tags':        TAGS,
            'categoryId':  CATEGORY_ID,
        },
        'status': {
            'privacyStatus':    PRIVACY,          # private
            'madeForKids':      MADE_FOR_KIDS,    # not for kids
            # selfDeclaredMadeForKids is the upload-time declaration;
            # madeForKids is what YouTube stores after processing.
            # Setting both ensures the correct value is applied immediately.
            'selfDeclaredMadeForKids': MADE_FOR_KIDS,
            # contentRating left empty = no age restriction (not 18+).
            # Explicitly setting an empty dict removes any default restriction.
            'contentRating': {},
        },
    }

    media = MediaFileUpload(
        VIDEO_FILE,
        mimetype='video/mp4',
        chunksize=CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(
        part='snippet,status',
        body=body,
        media_body=media,
    )

    response = None
    retry_count = 0
    max_retries = 10

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                bar = ('█' * (pct // 5)).ljust(20)
                print(f"\r  [{bar}] {pct}%", end='', flush=True)
        except googleapiclient.errors.HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retry_count < max_retries:
                wait = 2 ** retry_count
                print(f"\n  Server error {e.resp.status}, retrying in {wait}s ...")
                time.sleep(wait)
                retry_count += 1
            else:
                raise

    print(f"\n  Upload complete.")
    video_id = response['id']
    print(f"  Video ID : {video_id}")
    print(f"  URL      : https://www.youtube.com/watch?v={video_id}")
    return video_id


def set_thumbnail(youtube, video_id):
    if not os.path.exists(THUMBNAIL_FILE):
        print(f"\nWARNING: '{THUMBNAIL_FILE}' not found — skipping thumbnail.")
        return

    print(f"\nSetting thumbnail from '{THUMBNAIL_FILE}' ...")
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(THUMBNAIL_FILE, mimetype='image/png'),
    ).execute()
    print("  Thumbnail set successfully.")


def main():
    print("=" * 60)
    print("YouTube Uploader")
    print("=" * 60)
    # Title is printed inside upload_video() once derived from folder name.
    print(f"  Privacy : {PRIVACY}")
    print(f"  Video   : {VIDEO_FILE}")
    print(f"  Thumb   : {THUMBNAIL_FILE}")
    print("=" * 60)

    youtube   = get_authenticated_service()
    video_id  = upload_video(youtube)
    set_thumbnail(youtube, video_id)

    print(f"\nDone! Watch at: https://www.youtube.com/watch?v={video_id}")


if __name__ == '__main__':
    main()