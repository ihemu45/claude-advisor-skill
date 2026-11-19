#!/usr/bin/env python3
"""
autoclip.py - the watcher bot. Runs on a schedule, needs no one at the keyboard.

For every channel in config.json it:
  1. lists the channel's recent finished streams (yt-dlp, no download),
  2. skips streams it has already handled,
  3. downloads only the chat replay (fast),
  4. finds the chat spikes with find_highlights,
  5. downloads only the 45-second windows around the good spikes (not the whole stream),
  6. reframes them to 9:16, burns captions if faster-whisper is installed,
  7. writes a hook + caption for each clip,
  8. drops them in a review queue (queue.json) for a one-tap approve in review_server.py.

Usage
  python3 autoclip.py run                      # process new streams for all channels
  python3 autoclip.py run --video URL          # process one specific stream URL now
  python3 autoclip.py run --local-chat chat.json --local-video vod.mp4 --stream-id test --title "Test"
  python3 autoclip.py status                   # show what is queued / processed
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import find_highlights as fh  # noqa: E402
import hooks  # noqa: E402

CONF_LEVELS = {"weak": 0, "maybe": 1, "strong": 2}


# --------------------------------------------------------------------------- #
# config / state
# --------------------------------------------------------------------------- #

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh_:
        cfg = json.load(fh_)
    cfg.setdefault("workdir", os.path.join(HERE, "autoclip_data"))
    cfg["workdir"] = os.path.expanduser(cfg["workdir"])
    cfg.setdefault("max_streams_per_run", 2)
    cfg.setdefault("top", 6)
    cfg.setdefault("min_confidence", "maybe")
    cfg.setdefault("lead", 20)
    cfg.setdefault("duration", 45)
    cfg.setdefault("vertical", "blur")
    cfg.setdefault("captions", True)
    cfg.setdefault("min_stream_minutes", 20)
    cfg.setdefault("max_stream_age_days", 7)
    cfg.setdefault("video_format", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b")
    cfg.setdefault("llm_hooks", False)
    cfg.setdefault("channels", [])
    return cfg


def _json_load(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh_:
        return json.load(fh_)


def _json_save(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh_:
        json.dump(data, fh_, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


class Store:
    def __init__(self, workdir: str):
        self.workdir = workdir
        for sub in ("streams", "clips", "logs"):
            os.makedirs(os.path.join(workdir, sub), exist_ok=True)
        self.state_path = os.path.join(workdir, "state.json")
        self.queue_path = os.path.join(workdir, "queue.json")
        self.state = _json_load(self.state_path, {"processed": {}})
        self.queue = _json_load(self.queue_path, [])

    def processed(self, vid: str) -> bool:
        return vid in self.state["processed"]

    def mark(self, vid: str, **info) -> None:
        info["at"] = dt.datetime.now().isoformat(timespec="seconds")
        self.state["processed"][vid] = info
        _json_save(self.state_path, self.state)

    def add_clip(self, entry: dict) -> None:
        self.queue.append(entry)
        _json_save(self.queue_path, self.queue)


def log(store: Store | None, msg: str) -> None:
    line = f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if store:
        with open(os.path.join(store.workdir, "logs", "autoclip.log"), "a", encoding="utf-8") as fh_:
            fh_.write(line + "\n")


# --------------------------------------------------------------------------- #
# yt-dlp helpers
# --------------------------------------------------------------------------- #

def run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def list_streams(channel_url: str, limit: int = 8) -> list[dict]:
    """Recent entries of a channel's /streams tab. Live or upcoming ones have no duration."""
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit),
           "--print", "%(id)s\t%(duration)s\t%(live_status)s\t%(upload_date)s\t%(title)s", channel_url]
    r = run(cmd, timeout=180)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        vid, dur, live, upd, title = parts[0], parts[1], parts[2], parts[3], "\t".join(parts[4:])
        try:
            dur_s = float(dur) if dur not in ("NA", "None", "") else None
        except ValueError:
            dur_s = None
        out.append({"id": vid, "duration": dur_s, "live_status": live, "upload_date": upd, "title": title,
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    if r.returncode != 0 and not out:
        raise RuntimeError(f"yt-dlp could not list {channel_url}: {r.stderr.strip()[-400:]}")
    return out


def download_chat(url: str, dest_base: str) -> str | None:
    """Download the live chat replay only. Returns the path, or None if the stream has no replay."""
    cmd = ["yt-dlp", "--write-subs", "--sub-langs", "live_chat", "--skip-download",
           "--no-warnings", "-o", dest_base, url]
    r = run(cmd, timeout=1800)
    path = dest_base + ".live_chat.json"
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    return None


def download_section(url: str, start: float, duration: float, dest: str, fmt: str) -> bool:
    """Download just [start, start+duration] of the stream, re-encoded at the cut points."""
    end = start + duration
    cmd = ["yt-dlp", "--no-warnings", "-f", fmt, "--merge-output-format", "mp4",
           "--download-sections", f"*{start:.1f}-{end:.1f}", "--force-keyframes-at-cuts",
           "-o", dest, url]
    r = run(cmd, timeout=1800)
    ok = r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0
    if not ok:
        print(r.stderr.strip()[-600:], file=sys.stderr)
    return ok


def reframe(src: str, dest: str, vertical: str) -> bool:
    vf, _ = fh.vertical_filter(vertical)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src] + vf + [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dest]
    r = run(cmd, timeout=1800)
    return r.returncode == 0 and os.path.exists(dest)


def thumbnail(src: str, at: float, dest: str) -> None:
    run(fh.build_thumb_cmd(src, at, dest), timeout=300)


def maybe_caption(src: str) -> str:
    """Burn captions in if faster-whisper is installed; otherwise return the input unchanged."""
    try:
        import faster_whisper  # noqa: F401
        import add_captions
    except ImportError:
        return src
    stem = os.path.splitext(src)[0]
    srt, out = stem + ".srt", stem + "_cap.mp4"
    try:
        add_captions.transcribe(src, srt, model="small", lang=None, max_words=4)
        add_captions.burn(src, srt, out, font="DejaVu Sans", size=22, margin_v=100)
        return out if os.path.exists(out) else src
    except Exception as e:  # captions are a bonus, never a blocker
        print(f"captions skipped: {e}", file=sys.stderr)
        return src


# --------------------------------------------------------------------------- #
# the pipeline for one stream
# --------------------------------------------------------------------------- #

def analyse(chat_path: str, cfg: dict) -> tuple[list, float, int]:
    msgs, _fmt = fh.load_chat(chat_path)
    if not msgs:
        return [], 0.0, 0
    bin_size = fh.choose_bin(msgs)
    bins = fh.bin_messages(msgs, bin_size)
    fh.score_bins(bins, bin_size=bin_size)
    peaks = fh.pick_peaks(bins, top=int(cfg["top"]), min_gap=90.0, min_score=2.5)
    floor = CONF_LEVELS.get(cfg["min_confidence"], 1)
    peaks = [p for p in peaks if CONF_LEVELS[fh.confidence(p.score)] >= floor]
    return peaks, bin_size, len(msgs)


def process_stream(store: Store, cfg: dict, channel: dict, stream: dict,
                   local_chat: str | None = None, local_video: str | None = None) -> int:
    vid, title, url = stream["id"], stream.get("title", ""), stream.get("url", "")
    sdir = os.path.join(store.workdir, "streams")
    log(store, f"[{channel['name']}] {vid} | {title}")

    chat = local_chat or download_chat(url, os.path.join(sdir, vid))
    if not chat:
        log(store, "  no chat replay available, skipping")
        store.mark(vid, channel=channel["name"], title=title, result="no_chat")
        return 0

    peaks, bin_size, n_msgs = analyse(chat, cfg)
    log(store, f"  {n_msgs} chat messages, window {bin_size:.0f}s, {len(peaks)} usable spike(s)")
    if not peaks:
        store.mark(vid, channel=channel["name"], title=title, result="no_spikes", messages=n_msgs)
        return 0

    made = 0
    for i, p in enumerate(peaks, 1):
        start = max(0.0, p.start - float(cfg["lead"]))
        dur = float(cfg["duration"])
        clip_id = f"{vid}_{i:02d}_{fh.hms(p.start).replace(':', '')}"
        raw = os.path.join(store.workdir, "clips", clip_id + "_raw.mp4")
        final = os.path.join(store.workdir, "clips", clip_id + ".mp4")
        thumb = os.path.join(store.workdir, "clips", clip_id + ".jpg")

        if local_video:
            cmd = fh.build_clip_cmd(local_video, start, dur, final, cfg["vertical"], copy=False)
            ok = run(cmd, timeout=1800).returncode == 0
        else:
            ok = download_section(url, start, dur, raw, cfg["video_format"]) and reframe(raw, final, cfg["vertical"])
            if os.path.exists(raw):
                os.remove(raw)
        if not ok:
            log(store, f"  clip {i}: download/encode failed, skipping")
            continue
        thumbnail(final, min(dur - 1, float(cfg["lead"]) + bin_size / 2), thumb)
        if cfg["captions"]:
            final_cap = maybe_caption(final)
            if final_cap != final:
                final = final_cap

        hook, caption = hooks.make_caption(
            streamer=channel["name"], handle=channel.get("handle", ""), stream_title=title,
            chat_samples=p.samples, hashtags=channel.get("hashtags", ""),
            credit=channel.get("credit", ""), required_tags=channel.get("required_tags", ""),
            use_llm=bool(cfg.get("llm_hooks")))
        with open(os.path.splitext(final)[0] + ".caption.txt", "w", encoding="utf-8") as fh_:
            fh_.write(caption)
        store.add_clip({
            "id": clip_id, "stream_id": vid, "stream_title": title, "stream_url": url,
            "channel": channel["name"], "campaign": channel.get("campaign", ""),
            "campaign_url": channel.get("campaign_url", ""),
            "spike_at": fh.hms(p.start), "clip_start_s": round(start, 1), "clip_len_s": dur,
            "score": round(p.score, 2), "confidence": fh.confidence(p.score),
            "people": p.people, "flags": " ".join(p.flags), "chat": p.samples,
            "file": final, "thumb": thumb, "hook": hook, "caption": caption,
            "status": "pending", "created": dt.datetime.now().isoformat(timespec="seconds"),
        })
        made += 1
        log(store, f"  clip {i}: {fh.hms(p.start)} {fh.confidence(p.score)} -> {os.path.basename(final)}")

    store.mark(vid, channel=channel["name"], title=title, result="clips", clips=made, messages=n_msgs)
    return made


def fresh_enough(stream: dict, cfg: dict) -> bool:
    if stream.get("duration") is None:
        return False  # live or upcoming
    if stream["duration"] < float(cfg["min_stream_minutes"]) * 60:
        return False
    upd = stream.get("upload_date")
    if upd and upd not in ("NA", "None"):
        try:
            d = dt.datetime.strptime(upd, "%Y%m%d")
            if (dt.datetime.now() - d).days > int(cfg["max_stream_age_days"]):
                return False
        except ValueError:
            pass
    return True


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_run(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg["workdir"])
    total = 0
    try:
        if args.local_chat:
            ch = cfg["channels"][0] if cfg["channels"] else {"name": "Local", "handle": ""}
            stream = {"id": args.stream_id or "local", "title": args.title or "local test", "url": ""}
            if store.processed(stream["id"]) and not args.force:
                log(store, f"{stream['id']} already processed; add --force to redo it")
            else:
                total += process_stream(store, cfg, ch, stream, local_chat=args.local_chat, local_video=args.local_video)
        elif args.video:
            ch = cfg["channels"][0] if cfg["channels"] else {"name": "Stream", "handle": ""}
            vid = args.video.split("v=")[-1].split("&")[0].split("/")[-1]
            stream = {"id": vid, "title": args.title or vid, "url": args.video}
            if store.processed(vid) and not args.force:
                log(store, f"{vid} already processed; add --force to redo it")
            else:
                total += process_stream(store, cfg, ch, stream)
        else:
            for ch in cfg["channels"]:
                try:
                    streams = list_streams(ch["url"])
                except Exception as e:
                    log(store, f"[{ch['name']}] listing failed: {e}")
                    continue
                todo = [s for s in streams if not store.processed(s["id"]) and fresh_enough(s, cfg)]
                log(store, f"[{ch['name']}] {len(streams)} recent, {len(todo)} new finished stream(s)")
                for s in todo[: int(cfg["max_streams_per_run"])]:
                    try:
                        total += process_stream(store, cfg, ch, s)
                    except Exception as e:
                        log(store, f"  failed: {e}\n{traceback.format_exc()}")
                        store.mark(s["id"], channel=ch["name"], title=s.get("title", ""), result=f"error: {e}")
    finally:
        pending = sum(1 for c in store.queue if c["status"] == "pending")
        log(store, f"done: {total} new clip(s); {pending} waiting for review -> python3 review_server.py")
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg["workdir"])
    by = {}
    for c in store.queue:
        by[c["status"]] = by.get(c["status"], 0) + 1
    print(f"workdir: {store.workdir}")
    print(f"streams processed: {len(store.state['processed'])}")
    print("clips: " + (", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "none"))
    for c in store.queue[-10:]:
        print(f"  {c['status']:9} {c['confidence']:6} {c['channel']} {c['spike_at']}  {c['hook']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="process new streams")
    r.add_argument("--video", help="one stream URL instead of scanning channels")
    r.add_argument("--local-chat", help="test mode: a local chat export")
    r.add_argument("--local-video", help="test mode: a local video file")
    r.add_argument("--stream-id", default=None)
    r.add_argument("--title", default=None)
    r.add_argument("--force", action="store_true", help="redo a stream that was already processed")
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("status", help="show the queue")
    s.set_defaults(func=cmd_status)
    args = ap.parse_args()
    if not os.path.exists(args.config):
        print(f"No config at {args.config}. Copy config.example.json to config.json and edit it.", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
