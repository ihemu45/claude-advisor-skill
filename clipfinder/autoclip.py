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
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import find_highlights as fh  # noqa: E402
import hooks  # noqa: E402

CONF_LEVELS = {"weak": 0, "maybe": 1, "strong": 2}


def platform_of(url: str) -> str:
    u = (url or "").lower()
    if "twitch.tv" in u:
        return "twitch"
    if "kick.com" in u:
        return "kick"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "other"


# --------------------------------------------------------------------------- #
# config / state
# --------------------------------------------------------------------------- #

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh_:
        cfg = json.load(fh_)
    cfg.setdefault("workdir", os.path.join(HERE, "autoclip_data"))
    cfg["workdir"] = os.path.expanduser(cfg["workdir"])
    cfg["_path"] = os.path.abspath(path)
    cfg.setdefault("max_streams_per_run", 2)
    cfg.setdefault("top", 6)
    cfg.setdefault("min_confidence", "maybe")
    cfg.setdefault("lead", 20)
    cfg.setdefault("duration", 45)
    cfg.setdefault("vertical", "blur")
    cfg.setdefault("captions", True)
    cfg.setdefault("min_stream_minutes", 20)
    cfg.setdefault("max_stream_age_days", 7)
    cfg.setdefault("video_format", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b")
    cfg.setdefault("audio_fallback", True)
    cfg.setdefault("audio_min_db_above", 6.0)
    cfg.setdefault("llm_hooks", False)
    cfg.setdefault("auto_publish", {"enabled": False, "min_confidence": "strong", "max_per_run": 3,
                                    "hours": [9, 23], "notify": True})
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
    """Recent past broadcasts of a channel: YouTube /streams, Twitch /videos?filter=archives, Kick /videos.
    Live or upcoming entries have no duration and are skipped later."""
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit), "--no-warnings",
           "--print", "%(id)s\t%(duration)s\t%(live_status)s\t%(upload_date)s\t%(url)s\t%(title)s", channel_url]
    r = run(cmd, timeout=180)
    out = []
    plat = platform_of(channel_url)
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        vid, dur, live, upd, url, title = parts[0], parts[1], parts[2], parts[3], parts[4], "\t".join(parts[5:])
        try:
            dur_s = float(dur) if dur not in ("NA", "None", "") else None
        except ValueError:
            dur_s = None
        if url in ("NA", "None", ""):
            url = {"youtube": f"https://www.youtube.com/watch?v={vid}",
                   "twitch": f"https://www.twitch.tv/videos/{vid}"}.get(plat, vid)
        out.append({"id": f"{plat}:{vid}" if plat != "youtube" else vid, "duration": dur_s, "live_status": live,
                    "upload_date": upd, "title": title, "url": url, "platform": plat})
    if r.returncode != 0 and not out:
        raise RuntimeError(f"yt-dlp could not list {channel_url}: {r.stderr.strip()[-400:]}")
    return out


def download_chat(url: str, dest_base: str) -> str | None:
    """Download the chat replay only. YouTube: yt-dlp live_chat. Twitch: TwitchDownloaderCLI if installed.
    Kick has no chat export, so it returns None and the audio detector takes over."""
    plat = platform_of(url)
    if plat == "youtube":
        cmd = ["yt-dlp", "--write-subs", "--sub-langs", "live_chat", "--skip-download",
               "--no-warnings", "-o", dest_base, url]
        run(cmd, timeout=1800)
        path = dest_base + ".live_chat.json"
        return path if os.path.exists(path) and os.path.getsize(path) > 0 else None
    if plat == "twitch":
        cli = shutil.which("TwitchDownloaderCLI") or shutil.which("twitchdownloadercli")
        m = re.search(r"/videos/(\d+)", url)
        if cli and m:
            path = dest_base + ".twitchchat.json"
            run([cli, "chatdownload", "--id", m.group(1), "-o", path, "--collision", "Overwrite"], timeout=1800)
            return path if os.path.exists(path) and os.path.getsize(path) > 0 else None
    return None


class AudioPeak:
    """Looks enough like a find_highlights Bin for process_stream to use it."""
    def __init__(self, start: float, score: float, db: float, base_db: float):
        self.start = start
        self.score = score
        self.people = 0
        self.flags = ["audio"]
        self.samples = [f"audio spike: {db:.1f} dB, {db - base_db:+.1f} dB above the stream's usual level"]


def audio_levels(media: str, bin_size: float, sample_rate: int = 48000) -> list[tuple[float, float]]:
    """(window_start_seconds, RMS dB) for every window of the media's audio, via ffmpeg astats."""
    n = int(sample_rate * bin_size)
    af = (f"aresample={sample_rate},aformat=channel_layouts=mono,asetnsamples=n={n}:p=0,"
          f"astats=metadata=1:reset=1,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-")
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-i", media, "-vn", "-af", af, "-f", "null", "-"],
            timeout=3600)
    out, t = [], None
    for line in r.stdout.splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([0-9.]+)", line)
            t = float(m.group(1)) if m else None
        elif "RMS_level=" in line and t is not None:
            v = line.split("=", 1)[1].strip()
            try:
                db = float(v)
            except ValueError:
                db = -90.0  # "-inf" = silence
            out.append((t, max(db, -90.0)))
    return out


def audio_peaks(media: str, bin_size: float, top: int, min_gap: float, min_db_above: float,
                skip_start: float = 60.0, skip_end: float = 60.0, window: int = 30) -> list[AudioPeak]:
    """Loudest moments relative to the stream's own level: the streamer shouting, laughing, chaos."""
    levels = audio_levels(media, bin_size)
    if len(levels) < 10:
        return []
    vals = [db for _, db in levels]
    end_t = fh.probe_duration(media) or (levels[-1][0] + bin_size)  # ffmpeg emits a tiny trailing window
    scored = []
    for i, (t, db) in enumerate(levels):
        if t < skip_start or t + bin_size > end_t - skip_end:
            continue
        lo, hi = max(0, i - window), min(len(vals), i + window + 1)
        neigh = vals[lo:hi]
        base = statistics.median(neigh)
        mad = statistics.median(abs(x - base) for x in neigh)
        spread = max(1.4826 * mad, 1.5)
        z = (db - base) / spread
        if db - base >= min_db_above and z >= 2.5:
            scored.append(AudioPeak(t, round(z, 2), db, base))
    scored.sort(key=lambda p: p.score, reverse=True)
    picked: list[AudioPeak] = []
    for p in scored:
        if all(abs(p.start - q.start) >= min_gap for q in picked):
            picked.append(p)
        if len(picked) >= top:
            break
    return picked


def download_audio(url: str, dest_base: str) -> str | None:
    """Smallest stream that carries audio: audio-only when the site offers it, else the worst video."""
    cmd = ["yt-dlp", "--no-warnings", "-f", "ba/wv*+ba/w", "-o", dest_base + ".%(ext)s", url]
    run(cmd, timeout=3600)
    for ext in ("m4a", "webm", "mp4", "opus", "aac", "mp3", "ts", "mkv"):
        p = dest_base + "." + ext
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
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


def overlay_filter(overlay: dict | None) -> tuple[list[str], str]:
    """Campaign overlay (a PNG the campaign supplies). Returns (extra inputs, filter tail) for a 1080x1920 frame.
    overlay = {"png": "path.png", "position": "top|bottom|top-left|top-right|bottom-left|bottom-right", "width": 1080, "margin": 40}"""
    if not overlay or not overlay.get("png") or not os.path.exists(os.path.expanduser(overlay["png"])):
        return [], ""
    png = os.path.expanduser(overlay["png"])
    w = int(overlay.get("width", 1080))
    m = int(overlay.get("margin", 40))
    pos = overlay.get("position", "top")
    xy = {"top": f"(W-w)/2:{m}", "bottom": f"(W-w)/2:H-h-{m}", "top-left": f"{m}:{m}", "top-right": f"W-w-{m}:{m}",
          "bottom-left": f"{m}:H-h-{m}", "bottom-right": f"W-w-{m}:H-h-{m}"}.get(pos, f"(W-w)/2:{m}")
    return ["-i", png], f"[ov]scale={w}:-1[ovs];[v][ovs]overlay={xy}[vout]"


def reframe(src: str, dest: str, vertical: str, overlay: dict | None = None) -> bool:
    vf, _ = fh.vertical_filter(vertical)
    ov_in, ov_tail = overlay_filter(overlay)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src] + ov_in
    if ov_tail:
        # rebuild the vertical filter as a graph that ends in [v], then stamp the overlay on it
        if vertical == "blur":
            graph = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
                     "[b]scale=1080:-2:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
        elif vertical == "center":
            graph = "[0:v]crop=ih*9/16:ih,scale=1080:1920:flags=lanczos[v]"
        else:
            graph = "[0:v]null[v]"
        graph += ";[1:v]null[ov];" + ov_tail
        cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "0:a?"]
    else:
        cmd += vf
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dest]
    r = run(cmd, timeout=1800)
    if r.returncode != 0:
        print(r.stderr.strip()[-400:], file=sys.stderr)
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

    safe_id = vid.replace(":", "_")
    if not local_chat and url:
        log(store, f"  step 1/3: downloading chat replay ({platform_of(url)})...")
    chat = local_chat or (download_chat(url, os.path.join(sdir, safe_id)) if url else None)
    detector, n_msgs, bin_size, peaks = "chat", 0, 10.0, []
    if chat:
        peaks, bin_size, n_msgs = analyse(chat, cfg)
        log(store, f"  chat: {n_msgs} messages, window {bin_size:.0f}s, {len(peaks)} usable spike(s)")
    if not chat or (not peaks and n_msgs < 200):
        if not cfg["audio_fallback"]:
            log(store, "  no usable chat and audio fallback is off, skipping")
            store.mark(vid, channel=channel["name"], title=title, result="no_chat")
            return 0
        if not local_video:
            log(store, "  step 1/3: no chat export here, downloading the audio track instead "
                       "(a few minutes for a long stream, no output while it runs)...")
        media = local_video or download_audio(url, os.path.join(sdir, safe_id + "_audio"))
        if media:
            log(store, f"  step 2/3: scanning audio for loud moments ({os.path.getsize(media) // 1_000_000} MB)...")
        if not media:
            log(store, "  no chat and the audio could not be downloaded, skipping")
            store.mark(vid, channel=channel["name"], title=title, result="no_media")
            return 0
        detector, bin_size = "audio", 10.0
        peaks = audio_peaks(media, bin_size, top=int(cfg["top"]), min_gap=90.0,
                            min_db_above=float(cfg["audio_min_db_above"]))
        floor = CONF_LEVELS.get(cfg["min_confidence"], 1)
        peaks = [p for p in peaks if CONF_LEVELS[fh.confidence(p.score)] >= floor]
        log(store, f"  audio: {len(peaks)} loud moment(s) (no chat replay on {platform_of(url) if url else 'local'})")
        if media != local_video and os.path.exists(media):
            os.remove(media)
    after = float(channel.get("clip_after_s", 0) or 0)
    if after:
        peaks = [p for p in peaks if p.start >= after]   # e.g. a campaign that only accepts footage after 2:56:29
    if not peaks:
        store.mark(vid, channel=channel["name"], title=title, result="no_spikes", messages=n_msgs, detector=detector)
        return 0

    made = 0
    log(store, f"  step 3/3: downloading and cutting {len(peaks)} clip window(s), about a minute each...")
    for i, p in enumerate(peaks, 1):
        start = max(0.0, p.start - float(cfg["lead"]))
        dur = float(cfg["duration"])
        clip_id = f"{vid}_{i:02d}_{fh.hms(p.start).replace(':', '')}"
        raw = os.path.join(store.workdir, "clips", clip_id + "_raw.mp4")
        final = os.path.join(store.workdir, "clips", clip_id + ".mp4")
        thumb = os.path.join(store.workdir, "clips", clip_id + ".jpg")

        overlay = channel.get("overlay")
        if local_video:
            cmd = fh.build_clip_cmd(local_video, start, dur, raw, "none", copy=False)
            ok = run(cmd, timeout=1800).returncode == 0 and reframe(raw, final, cfg["vertical"], overlay)
        else:
            ok = download_section(url, start, dur, raw, cfg["video_format"]) and reframe(raw, final, cfg["vertical"], overlay)
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
            game=channel.get("game", "GTA 6"), use_llm=bool(cfg.get("llm_hooks")))
        with open(os.path.splitext(final)[0] + ".caption.txt", "w", encoding="utf-8") as fh_:
            fh_.write(caption)
        store.add_clip({
            "id": clip_id, "stream_id": vid, "stream_title": title, "stream_url": url,
            "channel": channel["name"], "campaign": channel.get("campaign", ""),
            "campaign_url": channel.get("campaign_url", ""),
            "detector": detector, "platform": platform_of(url) if url else "local",
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


def fresh_enough(stream: dict, cfg: dict, channel: dict | None = None) -> bool:
    channel = channel or {}
    if stream.get("duration") is None:
        return False  # live or upcoming
    if stream["duration"] < float(cfg["min_stream_minutes"]) * 60:
        return False
    max_age = int(channel.get("max_stream_age_days", cfg["max_stream_age_days"]))
    upd = stream.get("upload_date")
    if upd and upd not in ("NA", "None"):
        try:
            d = dt.datetime.strptime(upd, "%Y%m%d")
            if (dt.datetime.now() - d).days > max_age:
                return False
        except ValueError:
            pass
    pat = channel.get("title_must_match")
    if pat and not re.search(pat, stream.get("title", ""), re.IGNORECASE):
        return False
    return True


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def pick_channel(cfg: dict, name: str | None) -> dict:
    chans = cfg.get("channels") or [{"name": "Stream", "handle": ""}]
    if name:
        for c in chans:
            if c["name"].lower() == name.lower() or c.get("handle", "").lower() == name.lower():
                return c
        raise SystemExit(f"No channel named '{name}' in config. Names: {', '.join(c['name'] for c in chans)}")
    enabled = [c for c in chans if c.get("enabled", True)]
    return (enabled or chans)[0]


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg["workdir"])
    total = 0
    try:
        if args.local_chat or args.local_video:
            ch = pick_channel(cfg, args.channel)
            stream = {"id": args.stream_id or "local", "title": args.title or "local test", "url": ""}
            if store.processed(stream["id"]) and not args.force:
                log(store, f"{stream['id']} already processed; add --force to redo it")
            else:
                total += process_stream(store, cfg, ch, stream, local_chat=args.local_chat, local_video=args.local_video)
        elif args.video:
            ch = pick_channel(cfg, args.channel)
            if not args.channel:
                log(store, f"no --channel given, using '{ch['name']}' for captions and rules; "
                           f"pass --channel NAME to choose (names: {', '.join(c['name'] for c in cfg['channels'])})")
            vid = args.video.split("v=")[-1].split("&")[0].split("/")[-1]
            stream = {"id": vid, "title": args.title or vid, "url": args.video}
            if store.processed(vid) and not args.force:
                log(store, f"{vid} already processed; add --force to redo it")
            else:
                total += process_stream(store, cfg, ch, stream)
        else:
            for ch in cfg["channels"]:
                if not ch.get("enabled", True):
                    continue
                try:
                    streams = list_streams(ch["url"], limit=int(ch.get("list_limit", 8)))
                except Exception as e:
                    log(store, f"[{ch['name']}] listing failed: {e}")
                    continue
                todo = [s for s in streams if not store.processed(s["id"]) and fresh_enough(s, cfg, ch)]
                log(store, f"[{ch['name']}] {len(streams)} recent, {len(todo)} new finished stream(s)")
                for s in todo[: int(cfg["max_streams_per_run"])]:
                    try:
                        total += process_stream(store, cfg, ch, s)
                    except Exception as e:
                        log(store, f"  failed: {e}\n{traceback.format_exc()}")
                        store.mark(s["id"], channel=ch["name"], title=s.get("title", ""), result=f"error: {e}")
    finally:
        if total and cfg["auto_publish"].get("enabled"):
            auto_publish(store, cfg)
        pending = sum(1 for c in store.queue if c["status"] == "pending")
        log(store, f"done: {total} new clip(s); {pending} waiting for review -> python3 review_server.py")
    return 0


def auto_publish(store: Store, cfg: dict) -> None:
    """Approve the best new clips on their own and hand them to publish_youtube.py.
    Needs youtube_token.json to exist already (run publish_youtube.py once by hand to create it)."""
    ap_cfg = cfg["auto_publish"]
    hours = ap_cfg.get("hours") or [0, 24]
    now_h = dt.datetime.now().hour
    if not (int(hours[0]) <= now_h < int(hours[1])):
        log(store, f"auto-publish: outside publish hours {hours[0]}:00-{hours[1]}:00, clips stay queued "
                   f"(Reach campaigns want the link submitted within 30 min of posting)")
        return
    floor = CONF_LEVELS.get(ap_cfg.get("min_confidence", "strong"), 2)
    limit = int(ap_cfg.get("max_per_run", 3))
    picked = 0
    for c in sorted(store.queue, key=lambda c: -c["score"]):
        if c["status"] != "pending" or CONF_LEVELS.get(c["confidence"], 0) < floor:
            continue
        if c.get("flags") and "spam?" in c["flags"]:
            continue
        c["status"] = "approved"
        c["auto_approved"] = True
        picked += 1
        if picked >= limit:
            break
    _json_save(store.queue_path, store.queue)
    if not picked:
        log(store, "auto-publish: nothing met the bar this run")
        return
    if not os.path.exists(os.path.join(HERE, "youtube_token.json")):
        log(store, f"auto-publish: {picked} clip(s) approved but youtube_token.json is missing; "
                   f"run `python3 publish_youtube.py` once by hand to sign in, then they go out automatically")
        return
    log(store, f"auto-publish: uploading {picked} clip(s)")
    r = subprocess.run([sys.executable, os.path.join(HERE, "publish_youtube.py"), "--config", cfg["_path"]],
                       capture_output=True, text=True)
    uploaded = []
    for line in (r.stdout + r.stderr).strip().splitlines():
        log(store, "  " + line)
        if "uploaded https://" in line:
            uploaded.append(line.split("uploaded ", 1)[1].split()[0])
    if uploaded and ap_cfg.get("notify", True):
        notify(f"{len(uploaded)} clip(s) uploaded to YouTube",
               "Submit the links on the campaign pages within 30 minutes. They are in submit_queue.md.")


def notify(title: str, text: str) -> None:
    """macOS banner so you know a clip went out and the 30-minute submit clock started."""
    if sys.platform != "darwin":
        return
    try:
        script = f'display notification "{text}" with title "{title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except Exception:
        pass


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
    r.add_argument("--local-chat", help="test mode: a local chat export (omit to test the audio detector)")
    r.add_argument("--local-video", help="test mode: a local video file")
    r.add_argument("--stream-id", default=None)
    r.add_argument("--title", default=None)
    r.add_argument("--channel", default=None, help="with --video: which config channel this stream belongs to, e.g. SypherPK")
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
