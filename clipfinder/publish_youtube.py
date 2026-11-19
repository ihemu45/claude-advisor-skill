#!/usr/bin/env python3
"""
publish_youtube.py - upload every APPROVED clip in the queue to your YouTube channel
as a Short, then write submit_queue.md with the links to paste into each campaign.

One-time setup (10 minutes, see README "Autopilot"):
  1. Google Cloud console -> new project -> enable "YouTube Data API v3"
  2. OAuth consent screen -> External -> add your own Google account as a test user
  3. Credentials -> Create credentials -> OAuth client ID -> Desktop app -> Download JSON
  4. save it as client_secret.json next to this file
  5. pip install google-api-python-client google-auth-oauthlib
The first run opens a browser window once to let the app post to your channel; the
grant is saved in youtube_token.json so later runs are silent.

  python3 publish_youtube.py              # upload approved clips (privacy from config, default private)
  python3 publish_youtube.py --dry-run    # show what would be uploaded
  python3 publish_youtube.py --public     # override: publish as public
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import autoclip  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_service(client_secret: str, token_path: str):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit("Missing packages. Run: pip install google-api-python-client google-auth-oauthlib")
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secret):
                sys.exit(f"No {client_secret}. Follow the one-time setup at the top of publish_youtube.py.")
            flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as fh_:
            fh_.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload(service, path: str, title: str, description: str, tags: list[str], privacy: str) -> str:
    from googleapiclient.http import MediaFileUpload
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30], "categoryId": "20"},  # 20 = Gaming
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, chunksize=-1, resumable=True, mimetype="video/mp4")
    req = service.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _status, resp = req.next_chunk()
    return resp["id"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--public", action="store_true", help="publish as public regardless of config")
    a = ap.parse_args()

    cfg = autoclip.load_config(a.config)
    yt = cfg.get("youtube", {})
    privacy = "public" if a.public else yt.get("privacy", "private")
    client_secret = os.path.join(HERE, yt.get("client_secret", "client_secret.json"))
    token_path = os.path.join(HERE, "youtube_token.json")
    store = autoclip.Store(cfg["workdir"])

    approved = [c for c in store.queue if c["status"] == "approved"]
    if not approved:
        print("Nothing approved. Approve clips on the review page first.")
        return 0
    print(f"{len(approved)} approved clip(s), privacy={privacy}")
    if a.dry_run:
        for c in approved:
            print(f"  would upload {os.path.basename(c['file'])}: {c['hook']}")
        return 0

    service = get_service(client_secret, token_path)
    submit_path = os.path.join(store.workdir, "submit_queue.md")
    done = 0
    for c in approved:
        if not os.path.exists(c["file"]):
            print(f"  missing file, skipping: {c['file']}")
            continue
        hook = c["caption"].splitlines()[0] if c.get("caption") else c["hook"]
        title = f"{hook} #Shorts"
        tags = [t.lstrip("#") for t in c["caption"].split() if t.startswith("#")] + ["GTA6", "Shorts"]
        try:
            vid = upload(service, c["file"], title, c["caption"], tags, privacy)
        except Exception as e:
            print(f"  upload failed for {os.path.basename(c['file'])}: {e}")
            continue
        url = f"https://youtube.com/shorts/{vid}"
        c["status"] = "published"
        c["url"] = url
        c["published_at"] = dt.datetime.now().isoformat(timespec="seconds")
        c["privacy"] = privacy
        autoclip._json_save(store.queue_path, store.queue)
        with open(submit_path, "a", encoding="utf-8") as fh_:
            fh_.write(f"- {c['published_at']} | {c['channel']} | {c['campaign'] or 'no campaign set'} | {url} | submit at: {c['campaign_url'] or 'n/a'}\n")
        print(f"  uploaded {url}  ({privacy})")
        done += 1
    print(f"done: {done} uploaded. Links to paste into campaigns: {submit_path}")
    if privacy == "private":
        print("Clips are private. Flip each to public in YouTube Studio, or set \"privacy\": \"public\" in config.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
