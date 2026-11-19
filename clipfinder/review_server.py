#!/usr/bin/env python3
"""
review_server.py - the one page you look at. Shows every clip the watcher queued,
with the video, what chat said, and the caption. Tap Approve or Reject.
Approved clips are what publish_youtube.py uploads.

  python3 review_server.py            # then open http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import autoclip  # noqa: E402

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Clip review</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px}
 h1{font-size:20px;margin:0 0 12px} .sub{color:#9a9a9a;margin-bottom:16px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
 .card{background:#1c1c1e;border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:8px}
 video{width:100%;aspect-ratio:9/16;background:#000;border-radius:8px;max-height:480px}
 .meta{font-size:13px;color:#bbb} .conf-strong{color:#4cd964} .conf-maybe{color:#ffcc00} .conf-weak{color:#ff6b6b}
 textarea{width:100%;min-height:92px;background:#000;color:#eee;border:1px solid #333;border-radius:8px;padding:8px;font-size:14px}
 .row{display:flex;gap:8px} button{flex:1;padding:12px;border:0;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer}
 .ok{background:#30d158;color:#000} .no{background:#3a3a3c;color:#eee} .pub{background:#0a84ff;color:#fff;width:100%}
 .chat{font-size:12px;color:#8e8e93;white-space:pre-wrap} .tag{font-size:12px;padding:2px 8px;border-radius:999px;background:#2c2c2e;display:inline-block}
 a{color:#0a84ff} .done{opacity:.6} pre{background:#000;padding:8px;border-radius:8px;font-size:12px;white-space:pre-wrap}
</style></head><body>
<h1>Clip review</h1>
<div class="sub">__SUMMARY__</div>
<div class="row" style="margin-bottom:16px"><button class="pub" onclick="publish()">Publish approved clips to YouTube</button></div>
<pre id="out" style="display:none"></pre>
<div class="grid">__CARDS__</div>
<script>
async function decide(id, d){
  const cap=document.getElementById('cap-'+id).value;
  await fetch('/api/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,decision:d,caption:cap})});
  location.reload();
}
async function publish(){
  const o=document.getElementById('out'); o.style.display='block'; o.textContent='Publishing... this can take a minute per clip.';
  const r=await fetch('/api/publish',{method:'POST'}); o.textContent=await r.text();
}
</script></body></html>"""

CARD = """<div class="card __DONE__">
 <video controls preload="metadata" src="/media/__FILE__"></video>
 <div class="meta"><b>__CHANNEL__</b> · spike at __SPIKE__ · <span class="conf-__CONF__">__CONF__</span> · __PEOPLE__ people __FLAGS__ <span class="tag">__STATUS__</span></div>
 <div class="chat">__CHAT__</div>
 <textarea id="cap-__ID__">__CAPTION__</textarea>
 __ACTIONS__
 <div class="meta">__LINKS__</div>
</div>"""


def render(store: autoclip.Store) -> str:
    items = sorted(store.queue, key=lambda c: ({"pending": 0, "approved": 1, "published": 2, "rejected": 3}.get(c["status"], 9), -c["score"]))
    cards = []
    for c in items:
        fid = os.path.basename(c["file"])
        actions = ""
        if c["status"] == "pending":
            actions = (f'<div class="row"><button class="ok" onclick="decide(\'{c["id"]}\',\'approved\')">Approve</button>'
                       f'<button class="no" onclick="decide(\'{c["id"]}\',\'rejected\')">Reject</button></div>')
        elif c["status"] == "approved":
            actions = f'<div class="row"><button class="no" onclick="decide(\'{c["id"]}\',\'pending\')">Undo approve</button></div>'
        links = []
        if c.get("url"):
            links.append(f'<a href="{html.escape(c["url"])}" target="_blank">Posted video</a>')
        if c.get("campaign_url"):
            links.append(f'<a href="{html.escape(c["campaign_url"])}" target="_blank">Submit on campaign page</a>')
        links.append(f'<a href="/media/{urllib.parse.quote(fid)}" download>Download clip</a>')
        cards.append(CARD.replace("__DONE__", "done" if c["status"] in ("published", "rejected") else "")
                     .replace("__FILE__", urllib.parse.quote(fid)).replace("__CHANNEL__", html.escape(c["channel"]))
                     .replace("__SPIKE__", c["spike_at"]).replace("__CONF__", c["confidence"])
                     .replace("__PEOPLE__", str(c.get("people", ""))).replace("__FLAGS__", html.escape(c.get("flags", "")))
                     .replace("__STATUS__", c["status"]).replace("__CHAT__", html.escape("\n".join(c.get("chat", [])[:5])))
                     .replace("__ID__", c["id"]).replace("__CAPTION__", html.escape(c["caption"]))
                     .replace("__ACTIONS__", actions).replace("__LINKS__", " · ".join(links)))
    by = {}
    for c in store.queue:
        by[c["status"]] = by.get(c["status"], 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(by.items())) or "No clips yet. Run: python3 autoclip.py run"
    return PAGE.replace("__SUMMARY__", summary).replace("__CARDS__", "".join(cards) or "<p>Nothing to review.</p>")


class Handler(BaseHTTPRequestHandler):
    cfg_path = ""
    lock = threading.Lock()

    def _store(self) -> autoclip.Store:
        cfg = autoclip.load_config(self.cfg_path)
        return autoclip.Store(cfg["workdir"])

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            return self._send(200, render(self._store()).encode("utf-8"))
        if path.startswith("/media/"):
            name = os.path.basename(urllib.parse.unquote(path[len("/media/"):]))
            store = self._store()
            full = os.path.join(store.workdir, "clips", name)
            if not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain")
            ctype = "video/mp4" if name.endswith(".mp4") else "image/jpeg"
            size = os.path.getsize(full)
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else 0
                end = int(b) if b else size - 1
            with open(full, "rb") as fh_:
                fh_.seek(start)
                data = fh_.read(end - start + 1)
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/queue":
            return self._send(200, json.dumps(self._store().queue, indent=2).encode("utf-8"), "application/json")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if path == "/api/decide":
            req = json.loads(body or b"{}")
            with self.lock:
                store = self._store()
                for c in store.queue:
                    if c["id"] == req.get("id"):
                        if req.get("decision") in ("approved", "rejected", "pending"):
                            c["status"] = req["decision"]
                        if isinstance(req.get("caption"), str) and req["caption"].strip():
                            c["caption"] = req["caption"].strip()
                            c["hook"] = c["caption"].splitlines()[0]
                        break
                autoclip._json_save(store.queue_path, store.queue)
            return self._send(200, b'{"ok":true}', "application/json")
        if path == "/api/publish":
            cmd = [sys.executable, os.path.join(HERE, "publish_youtube.py"), "--config", self.cfg_path]
            r = subprocess.run(cmd, capture_output=True, text=True)
            return self._send(200, (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).encode("utf-8"), "text/plain; charset=utf-8")
        return self._send(404, b"not found", "text/plain")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    Handler.cfg_path = a.config
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Review page: {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
