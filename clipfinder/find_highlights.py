#!/usr/bin/env python3
"""
find_highlights.py - find the best moments of a long stream from chat activity,
then cut them into short clips with FFmpeg.

Idea: when something wild happens on stream, chat explodes a few seconds later.
We count chat messages in fixed windows (default 10 s), compare each window with
its own neighbourhood (rolling median + MAD), boost windows full of hype words
or "CLIP IT" requests, pick the top peaks, and start every clip ~20 s before
the spike so the actual moment is inside the clip.

Supported chat exports (format is auto-detected):
  * TwitchDownloader JSON          -> {"comments": [{"content_offset_seconds": .., "message": {"body": ..}}]}
  * yt-dlp live chat replay JSONL  -> <video>.live_chat.json (one JSON object per line)
  * plain CSV                      -> seconds,message   (header optional)

Only the Python standard library is needed. FFmpeg/ffprobe must be on PATH if
you pass --video.

Examples
  # just analyse chat and write a review sheet + ffmpeg commands
  python3 find_highlights.py --chat vod_chat.json --out run1

  # analyse, cut 45 s vertical clips and thumbnails from the VOD
  python3 find_highlights.py --chat vod_chat.json --video vod.mp4 --vertical center --thumbs --out run1
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shlex
import statistics
import subprocess
import sys
from dataclasses import dataclass, asdict
from typing import Iterable

# --------------------------------------------------------------------------- #
# chat loading
# --------------------------------------------------------------------------- #

# reaction vocabulary, case-insensitive
HYPE_RE = re.compile(
    r"(?:\b(?:lmao+|lmfao+|lol+|lul+|omegalul|kekw|pog+|poggers|pogchamp|omg+|wtf|"
    r"no\s*way|what\s+just|wha+t{2,}|bruh+|insane|crazy|holy|nooo+|yooo+|ayo+|"
    r"hahah*a*)\b|[😂💀🤣😭🔥👀😱]|\?{2,}|!{2,})",
    re.IGNORECASE,
)
# shouted one-worders only count when actually shouted
CAPS_RE = re.compile(r"\b(?:WHAT|BRO|WOW|NAH|DEAD|RIP|GG|W|L|NO|YES|HOW)\b")
CLIP_RE = re.compile(r"\bclip(?:ped|per|ping|s)?\b|clip\s*it|clip\s*that", re.IGNORECASE)


def is_hype(text: str) -> bool:
    if HYPE_RE.search(text) or CAPS_RE.search(text):
        return True
    letters = [c for c in text if c.isalpha()]
    # an all-caps message of 4+ letters is a shout
    return len(letters) >= 4 and all(c.isupper() for c in letters)


@dataclass
class ChatMsg:
    t: float      # seconds from VOD start
    text: str


def _yt_runs_to_text(runs) -> str:
    parts = []
    for r in runs or []:
        if isinstance(r, dict):
            if "text" in r:
                parts.append(str(r["text"]))
            elif "emoji" in r:
                e = r["emoji"] or {}
                sc = e.get("shortcuts") or []
                parts.append(sc[0] if sc else (e.get("emojiId") or ""))
    return "".join(parts)


def _walk_find_messages(node, found: list):
    """Recursively find chat renderers in a yt-dlp live_chat action."""
    if isinstance(node, dict):
        if "message" in node and isinstance(node["message"], dict) and "runs" in node["message"]:
            found.append(_yt_runs_to_text(node["message"]["runs"]))
            return
        for v in node.values():
            _walk_find_messages(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk_find_messages(v, found)


def load_ytdlp_live_chat(path: str) -> list[ChatMsg]:
    out: list[ChatMsg] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            rca = obj.get("replayChatItemAction") or {}
            off = obj.get("videoOffsetTimeMsec") or rca.get("videoOffsetTimeMsec")
            if off is None:
                continue
            try:
                t = float(off) / 1000.0
            except (TypeError, ValueError):
                continue
            found: list[str] = []
            _walk_find_messages(rca.get("actions") or obj.get("actions") or [], found)
            for txt in found:
                out.append(ChatMsg(t, txt))
    return out


def load_twitchdownloader(obj: dict) -> list[ChatMsg]:
    out: list[ChatMsg] = []
    for c in obj.get("comments", []):
        t = c.get("content_offset_seconds")
        if t is None:
            continue
        msg = c.get("message") or {}
        body = msg.get("body")
        if body is None and "fragments" in msg:
            body = "".join(f.get("text", "") for f in msg["fragments"])
        out.append(ChatMsg(float(t), body or ""))
    return out


def load_csv(path: str) -> list[ChatMsg]:
    out: list[ChatMsg] = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        rdr = csv.reader(fh)
        for row in rdr:
            if len(row) < 2:
                continue
            try:
                t = float(row[0])
            except ValueError:
                continue  # header or junk
            out.append(ChatMsg(t, ",".join(row[1:])))
    return out


def load_chat(path: str, offset: float = 0.0) -> tuple[list[ChatMsg], str]:
    """Return (messages sorted by time, detected format name)."""
    ext = os.path.splitext(path)[1].lower()
    msgs: list[ChatMsg] = []
    fmt = "unknown"
    if ext == ".csv":
        msgs, fmt = load_csv(path), "csv"
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                obj = json.load(fh)
            if isinstance(obj, dict) and "comments" in obj:
                msgs, fmt = load_twitchdownloader(obj), "twitchdownloader"
            elif isinstance(obj, list):
                # list of {"t"/"seconds": .., "text"/"message": ..}
                for it in obj:
                    if not isinstance(it, dict):
                        continue
                    t = it.get("t", it.get("seconds", it.get("content_offset_seconds")))
                    txt = it.get("text", it.get("message", it.get("body", "")))
                    if t is not None:
                        msgs.append(ChatMsg(float(t), str(txt)))
                fmt = "json-list"
        except json.JSONDecodeError:
            if "replayChatItemAction" in head or "videoOffsetTimeMsec" in head:
                msgs, fmt = load_ytdlp_live_chat(path), "yt-dlp"
            else:
                raise SystemExit(f"Could not detect chat format of {path}")
    if offset:
        msgs = [ChatMsg(m.t + offset, m.text) for m in msgs]
    msgs = [m for m in msgs if m.t >= 0]
    msgs.sort(key=lambda m: m.t)
    return msgs, fmt


# --------------------------------------------------------------------------- #
# binning + scoring
# --------------------------------------------------------------------------- #

@dataclass
class Bin:
    index: int
    start: float
    count: int = 0
    hype: int = 0
    clipreq: int = 0
    samples: list = None
    baseline: float = 0.0
    z: float = 0.0
    score: float = float("-inf")


def bin_messages(msgs: list[ChatMsg], bin_size: float, max_samples: int = 6) -> list[Bin]:
    if not msgs:
        return []
    n = int(msgs[-1].t // bin_size) + 1
    bins = [Bin(i, i * bin_size, samples=[]) for i in range(n)]
    plain: dict[int, list[str]] = {}
    for m in msgs:
        b = bins[int(m.t // bin_size)]
        b.count += 1
        hype_hit = is_hype(m.text)
        is_clip = bool(CLIP_RE.search(m.text))
        if hype_hit:
            b.hype += 1
        if is_clip:
            b.clipreq += 1
        txt = m.text.strip()[:80]
        if not txt:
            continue
        # review sheet shows reaction messages first, filler only if there is room
        if (hype_hit or is_clip) and len(b.samples) < max_samples:
            b.samples.append(txt)
        elif not (hype_hit or is_clip):
            plain.setdefault(b.index, [])
            if len(plain[b.index]) < max_samples:
                plain[b.index].append(txt)
    for b in bins:
        for txt in plain.get(b.index, []):
            if len(b.samples) >= max_samples:
                break
            b.samples.append(txt)
    return bins


def score_bins(bins: list[Bin], window: int = 30, min_messages: int = 5,
               hype_weight: float = 2.0, clip_weight: float = 3.0) -> None:
    counts = [b.count for b in bins]
    n = len(bins)
    for i, b in enumerate(bins):
        lo, hi = max(0, i - window), min(n, i + window + 1)
        neigh = counts[lo:hi]
        base = statistics.median(neigh)
        mad = statistics.median(abs(x - base) for x in neigh)
        spread = max(1.4826 * mad, math.sqrt(base + 1.0), 1.0)
        b.baseline = base
        b.z = (b.count - base) / spread
        if b.count < min_messages:
            b.score = float("-inf")
            continue
        hype_frac = b.hype / b.count if b.count else 0.0
        b.score = b.z + hype_weight * hype_frac + (clip_weight if b.clipreq else 0.0)


def pick_peaks(bins: list[Bin], top: int, min_gap: float, min_score: float) -> list[Bin]:
    ranked = sorted((b for b in bins if b.score != float("-inf") and b.score >= min_score),
                    key=lambda b: b.score, reverse=True)
    picked: list[Bin] = []
    for b in ranked:
        if all(abs(b.start - p.start) >= min_gap for p in picked):
            picked.append(b)
        if len(picked) >= top:
            break
    picked.sort(key=lambda b: b.start)
    return picked


# --------------------------------------------------------------------------- #
# ffmpeg helpers
# --------------------------------------------------------------------------- #

def hms(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def probe_duration(video: str) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video],
            capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except Exception:
        return None


def vertical_filter(mode: str) -> tuple[list[str], str]:
    """Return (extra ffmpeg args, description)."""
    if mode == "center":
        return (["-vf", "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos"],
                "centre crop to 9:16, 1080x1920")
    if mode == "blur":
        fc = ("[0:v]split[a][b];"
              "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
              "[b]scale=1080:-2:flags=lanczos[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2")
        return (["-filter_complex", fc], "full frame over blurred 9:16 background")
    return ([], "original framing")


def build_clip_cmd(video: str, start: float, duration: float, out_path: str,
                   vertical: str, copy: bool) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.3f}", "-i", video, "-t", f"{duration:.3f}"]
    if copy:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        vf, _ = vertical_filter(vertical)
        cmd += vf
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    cmd.append(out_path)
    return cmd


def build_thumb_cmd(video: str, at: float, out_path: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{at:.3f}", "-i", video, "-frames:v", "1", "-q:v", "3", out_path]


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chat", required=True, help="chat export (TwitchDownloader JSON, yt-dlp live_chat.json, or CSV)")
    ap.add_argument("--video", help="VOD file to cut clips from (optional)")
    ap.add_argument("--out", default="highlights_out", help="output directory")
    ap.add_argument("--bin", type=float, default=10.0, help="window size in seconds (default 10)")
    ap.add_argument("--lead", type=float, default=20.0, help="seconds to start before the spike (default 20)")
    ap.add_argument("--duration", type=float, default=45.0, help="clip length in seconds (default 45)")
    ap.add_argument("--top", type=int, default=10, help="how many peaks to keep (default 10)")
    ap.add_argument("--min-gap", type=float, default=90.0, help="min seconds between picked peaks (default 90)")
    ap.add_argument("--min-score", type=float, default=2.5, help="min robust-z score to count as a spike (default 2.5)")
    ap.add_argument("--min-messages", type=int, default=5, help="ignore windows with fewer messages (default 5)")
    ap.add_argument("--window", type=int, default=30, help="baseline neighbourhood, in bins each side (default 30)")
    ap.add_argument("--offset", type=float, default=0.0, help="seconds to add to every chat timestamp (sync fix)")
    ap.add_argument("--vertical", choices=["none", "center", "blur"], default="none", help="reframe to 9:16")
    ap.add_argument("--copy", action="store_true", help="stream-copy cut (fast, keyframe-inaccurate, no reframe)")
    ap.add_argument("--thumbs", action="store_true", help="also save a JPEG at each spike")
    ap.add_argument("--dry-run", action="store_true", help="do not run ffmpeg, only write the commands")
    args = ap.parse_args(argv)

    msgs, fmt = load_chat(args.chat, args.offset)
    if not msgs:
        print("No chat messages found.", file=sys.stderr)
        return 2
    bins = bin_messages(msgs, args.bin)
    score_bins(bins, window=args.window, min_messages=args.min_messages)
    peaks = pick_peaks(bins, top=args.top, min_gap=args.min_gap, min_score=args.min_score)

    os.makedirs(args.out, exist_ok=True)
    vid_dur = probe_duration(args.video) if args.video else None
    span = msgs[-1].t - msgs[0].t
    print(f"chat format: {fmt} | messages: {len(msgs)} | span: {hms(span)} | bins: {len(bins)} | peaks: {len(peaks)}")

    # per-bin activity for plotting / tuning
    with open(os.path.join(args.out, "activity.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["bin_start_s", "time", "messages", "hype", "clip_requests", "baseline", "z", "score"])
        for b in bins:
            w.writerow([f"{b.start:.0f}", hms(b.start), b.count, b.hype, b.clipreq,
                        f"{b.baseline:.1f}", f"{b.z:.2f}",
                        "" if b.score == float("-inf") else f"{b.score:.2f}"])

    rows = []
    sh_lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
    for i, b in enumerate(peaks, 1):
        spike_t = b.start
        start = max(0.0, spike_t - args.lead)
        dur = args.duration
        if vid_dur is not None and start + dur > vid_dur:
            dur = max(1.0, vid_dur - start)
        clip_name = f"clip_{i:02d}_{hms(spike_t).replace(':', '')}.mp4"
        thumb_name = f"clip_{i:02d}_{hms(spike_t).replace(':', '')}.jpg"
        clip_path = os.path.join(args.out, clip_name)
        thumb_path = os.path.join(args.out, thumb_name)
        row = {
            "rank": i, "spike_at": hms(spike_t), "spike_s": round(spike_t, 1),
            "clip_start": hms(start), "clip_start_s": round(start, 1), "clip_len_s": round(dur, 1),
            "messages": b.count, "baseline": round(b.baseline, 1), "z": round(b.z, 2),
            "hype": b.hype, "clip_requests": b.clipreq, "score": round(b.score, 2),
            "file": clip_name if args.video else "", "samples": b.samples,
        }
        rows.append(row)
        video_arg = args.video or "VOD.mp4"
        sh_lines.append(f"# {i:02d}  spike {hms(spike_t)}  score {b.score:.2f}  ({b.count} msgs, baseline {b.baseline:.0f})")
        sh_lines.append(shlex.join(build_clip_cmd(video_arg, start, dur, clip_path, args.vertical, args.copy)))
        if args.thumbs:
            sh_lines.append(shlex.join(build_thumb_cmd(video_arg, spike_t + args.bin / 2, thumb_path)))
        sh_lines.append("")

        if args.video and not args.dry_run:
            cmd = build_clip_cmd(args.video, start, dur, clip_path, args.vertical, args.copy)
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[{i:02d}] ffmpeg failed: {r.stderr.strip()[:400]}", file=sys.stderr)
                row["file"] = ""
            else:
                print(f"[{i:02d}] wrote {clip_name}  (spike {hms(spike_t)}, score {b.score:.2f})")
            if args.thumbs:
                subprocess.run(build_thumb_cmd(args.video, spike_t + args.bin / 2, thumb_path),
                               capture_output=True, text=True)

    with open(os.path.join(args.out, "ffmpeg_commands.sh"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(sh_lines))
    os.chmod(os.path.join(args.out, "ffmpeg_commands.sh"), 0o755)

    with open(os.path.join(args.out, "highlights.json"), "w", encoding="utf-8") as fh:
        json.dump({"chat_file": args.chat, "chat_format": fmt, "video": args.video,
                   "settings": {k: v for k, v in vars(args).items()}, "highlights": rows},
                  fh, indent=2, ensure_ascii=False)

    with open(os.path.join(args.out, "highlights.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[k for k in rows[0].keys() if k != "samples"] if rows else ["rank"])
        w.writeheader()
        for r in rows:
            w.writerow({k: v for k, v in r.items() if k != "samples"})

    md = [f"# Highlights for `{os.path.basename(args.chat)}`", "",
          f"{len(msgs)} chat messages over {hms(span)}; window {args.bin:.0f}s; "
          f"clips start {args.lead:.0f}s before each spike and run {args.duration:.0f}s.", "",
          "| # | Spike at | Clip start | Len | Msgs | Base | z | Hype | Clip req | Score | File |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['rank']} | {r['spike_at']} | {r['clip_start']} | {r['clip_len_s']:.0f}s | {r['messages']} | "
                  f"{r['baseline']} | {r['z']} | {r['hype']} | {r['clip_requests']} | {r['score']} | {r['file']} |")
    md.append("")
    md.append("## What chat said at each spike")
    md.append("")
    for r in rows:
        md.append(f"**{r['rank']}. {r['spike_at']}** (score {r['score']})")
        for s in r["samples"]:
            md.append(f"- {s}")
        md.append("")
    md.append("Review every clip before posting. Chat spikes also happen for raids, giveaways, "
              "and stream starts, which are not clips.")
    with open(os.path.join(args.out, "highlights.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))

    print(f"wrote {args.out}/highlights.md, highlights.csv, highlights.json, activity.csv, ffmpeg_commands.sh")
    if not peaks:
        print("No spikes cleared --min-score; try lowering it or --min-messages.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
