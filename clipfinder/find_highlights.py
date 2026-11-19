#!/usr/bin/env python3
"""
find_highlights.py - find the best moments of a long stream from chat activity,
then cut them into short clips with FFmpeg.

Idea: when something wild happens on stream, chat explodes a few seconds later.
We count *people reacting* in fixed windows (auto-sized from how busy the chat
is), compare each window with its own neighbourhood (rolling median + MAD),
boost windows full of hype words or "CLIP IT" reactions, ignore spam and the
stream's first/last minute, pick the top peaks, and start every clip ~20 s
before the spike so the actual moment is inside the clip.

Supported chat exports (format is auto-detected):
  * TwitchDownloader JSON          -> {"comments": [{"content_offset_seconds": .., "message": {"body": ..}}]}
  * yt-dlp live chat replay JSONL  -> <video>.live_chat.json (one JSON object per line)
  * plain CSV                      -> seconds,message   or with header: seconds,author,message

Only the Python standard library is needed. FFmpeg/ffprobe must be on PATH if
you pass --video.

Examples
  # just analyse chat and write a review sheet + ffmpeg commands
  python3 find_highlights.py --chat vod_chat.json --out run1

  # analyse, cut 45 s vertical clips and thumbnails from the VOD
  python3 find_highlights.py --chat vod_chat.json --video vod.mp4 --vertical blur --thumbs --out run1
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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

# --------------------------------------------------------------------------- #
# reaction vocabulary
# --------------------------------------------------------------------------- #

# reaction words, case-insensitive
HYPE_RE = re.compile(
    r"(?:\b(?:lmao+|lmfao+|lol+|lul+|omegalul|kekw|pog+|poggers|pogchamp|omg+|wtf|"
    r"no\s*way|what\s+just|wha+t{2,}|bruh+|insane|crazy|holy|nooo+|yooo+|ayo+|"
    r"hahah*a*)\b|[😂💀🤣😭🔥👀😱]|:(?:face_with_tears_of_joy|skull|rolling_on_the_floor_laughing|"
    r"loudly_crying_face|fire|eyes|face_screaming_in_fear):|\?{2,}|!{2,})",
    re.IGNORECASE,
)
# shouted one-worders only count when actually shouted
CAPS_RE = re.compile(r"\b(?:WHAT|BRO|WOW|NAH|DEAD|RIP|GG|W|L|NO|YES|HOW)\b")
# "clip it!" as a reaction ...
CLIP_POS_RE = re.compile(
    r"\bclip\s*(?:it|that|this|him|her|dat)\b|\bclipped\b|\bsomeone\s+clip\b|\bclip\s+clip\b|^\W*clip\W*$",
    re.IGNORECASE,
)
# ... but not clippers asking about clipping
CLIP_NEG_RE = re.compile(
    r"\b(?:how|can i|can you|could i|may i|limit|campaign|clipper|clippers|clipping|my clips?|"
    r"allowed|permission|reset|wanna|want to|wanted|tried|trying|gonna|removed)\b",
    re.IGNORECASE,
)
SYSTEM_RE = re.compile(r"^live chat replay is on", re.IGNORECASE)


def is_hype(text: str) -> bool:
    if HYPE_RE.search(text) or CAPS_RE.search(text):
        return True
    letters = [c for c in text if c.isalpha()]
    # an all-caps message of 4+ letters is a shout
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def is_clip_request(text: str) -> bool:
    return bool(CLIP_POS_RE.search(text)) and not CLIP_NEG_RE.search(text)


def norm_text(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"(.)\1{2,}", r"\1\1", s)   # hiiiiii -> hii, !!!!! -> !!
    s = re.sub(r"\s+", " ", s)
    return s


# --------------------------------------------------------------------------- #
# chat loading
# --------------------------------------------------------------------------- #

@dataclass
class ChatMsg:
    t: float            # seconds from VOD start
    text: str
    author: str = ""    # empty when the export has no author info


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
            author = ""
            an = node.get("authorName")
            if isinstance(an, dict):
                author = an.get("simpleText") or ""
            author = node.get("authorExternalChannelId") or author
            found.append((_yt_runs_to_text(node["message"]["runs"]), author))
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
            found: list[tuple[str, str]] = []
            _walk_find_messages(rca.get("actions") or obj.get("actions") or [], found)
            for txt, author in found:
                out.append(ChatMsg(t, txt, author))
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
        cm = c.get("commenter") or {}
        author = str(cm.get("_id") or cm.get("display_name") or cm.get("name") or "")
        out.append(ChatMsg(float(t), body or "", author))
    return out


def load_csv(path: str) -> list[ChatMsg]:
    out: list[ChatMsg] = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return out
    header = [h.strip().lower() for h in rows[0]]
    t_i = a_i = m_i = None
    try:
        float(rows[0][0])
        has_header = False
    except (ValueError, IndexError):
        has_header = True
    if has_header:
        for i, h in enumerate(header):
            if h in ("seconds", "t", "time", "offset", "content_offset_seconds") and t_i is None:
                t_i = i
            elif h in ("author", "user", "username", "name", "commenter") and a_i is None:
                a_i = i
            elif h in ("message", "text", "body", "msg") and m_i is None:
                m_i = i
        rows = rows[1:]
    if t_i is None:
        t_i = 0
    for row in rows:
        if len(row) < 2:
            continue
        try:
            t = float(row[t_i])
        except (ValueError, IndexError):
            continue
        author = row[a_i].strip() if a_i is not None and a_i < len(row) else ""
        if m_i is not None and m_i < len(row):
            text = row[m_i]
        else:
            rest = [v for i, v in enumerate(row) if i not in (t_i, a_i)]
            text = ",".join(rest)
        out.append(ChatMsg(t, text, author))
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
                for it in obj:
                    if not isinstance(it, dict):
                        continue
                    t = it.get("t", it.get("seconds", it.get("content_offset_seconds")))
                    txt = it.get("text", it.get("message", it.get("body", "")))
                    author = str(it.get("author", it.get("user", "")) or "")
                    if t is not None:
                        msgs.append(ChatMsg(float(t), str(txt), author))
                fmt = "json-list"
        except json.JSONDecodeError:
            if "replayChatItemAction" in head or "videoOffsetTimeMsec" in head:
                msgs, fmt = load_ytdlp_live_chat(path), "yt-dlp"
            else:
                raise SystemExit(f"Could not detect chat format of {path}")
    if offset:
        msgs = [ChatMsg(m.t + offset, m.text, m.author) for m in msgs]
    msgs = [m for m in msgs if m.t >= 0 and not SYSTEM_RE.match(m.text)]
    msgs.sort(key=lambda m: m.t)
    return msgs, fmt


# --------------------------------------------------------------------------- #
# binning + scoring
# --------------------------------------------------------------------------- #

@dataclass
class Bin:
    index: int
    start: float
    raw: int = 0            # every message
    count: int = 0          # people-weighted count (spam collapsed)
    people: int = 0         # distinct chatters
    hype: int = 0
    clipreq: int = 0
    dominant: float = 0.0   # share of the single most repeated message
    samples: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    baseline: float = 0.0
    z: float = 0.0
    score: float = float("-inf")


def choose_bin(msgs: list[ChatMsg]) -> float:
    """Pick a window so a typical window holds ~6 messages: 10 s for busy chats, up to 60 s for quiet ones."""
    span = max(1.0, msgs[-1].t - msgs[0].t)
    rate = len(msgs) / span
    if rate <= 0:
        return 10.0
    b = 6.0 / rate
    b = 5.0 * round(b / 5.0)
    return float(min(60.0, max(10.0, b)))


def bin_messages(msgs: list[ChatMsg], bin_size: float, max_samples: int = 6,
                 per_person_cap: int = 3) -> list[Bin]:
    if not msgs:
        return []
    n = int(msgs[-1].t // bin_size) + 1
    bins = [Bin(i, i * bin_size) for i in range(n)]
    acc = defaultdict(lambda: {"texts_by_ident": defaultdict(set), "texts": Counter(),
                               "hype": set(), "clip": set(), "react": [], "plain": []})
    for m in msgs:
        i = int(m.t // bin_size)
        d = acc[i]
        bins[i].raw += 1
        nt = norm_text(m.text)
        if not nt:
            continue
        # identity: the author if we know one, else the message text itself
        ident = m.author if m.author else "txt:" + nt
        pair = (ident, nt)
        new_pair = nt not in d["texts_by_ident"][ident]
        d["texts_by_ident"][ident].add(nt)
        d["texts"][nt] += 1
        hype_hit, clip_hit = is_hype(m.text), is_clip_request(m.text)
        if hype_hit:
            d["hype"].add(pair)
        if clip_hit:
            d["clip"].add(pair)
        if new_pair:
            txt = m.text.strip()[:80]
            (d["react"] if (hype_hit or clip_hit) else d["plain"]).append(txt)
    for i, d in acc.items():
        b = bins[i]
        b.count = sum(min(per_person_cap, len(ts)) for ts in d["texts_by_ident"].values())
        b.people = len(d["texts_by_ident"])
        b.hype = len(d["hype"])
        b.clipreq = len(d["clip"])
        b.dominant = (max(d["texts"].values()) / b.raw) if b.raw else 0.0
        seen: set[str] = set()
        for txt in d["react"] + d["plain"]:
            k = norm_text(txt)
            if k in seen:
                continue
            seen.add(k)
            b.samples.append(txt)
            if len(b.samples) >= max_samples:
                break
    return bins


def score_bins(bins: list[Bin], window: int = 30, min_messages: int = 5,
               hype_weight: float = 2.0, clip_weight: float = 3.0,
               skip_start: float = 60.0, skip_end: float = 60.0, bin_size: float = 10.0) -> None:
    counts = [b.count for b in bins]
    n = len(bins)
    end_t = bins[-1].start + bin_size if bins else 0.0
    for i, b in enumerate(bins):
        lo, hi = max(0, i - window), min(n, i + window + 1)
        neigh = counts[lo:hi]
        base = statistics.median(neigh)
        mad = statistics.median(abs(x - base) for x in neigh)
        spread = max(1.4826 * mad, math.sqrt(base + 1.0), 1.0)
        b.baseline = base
        b.z = (b.count - base) / spread
        if b.start < skip_start or b.start + bin_size > end_t - skip_end:
            b.flags.append("edge")
            b.score = float("-inf")
            continue
        if b.count < min_messages:
            b.score = float("-inf")
            continue
        hype_frac = b.hype / b.count if b.count else 0.0
        score = b.z + hype_weight * hype_frac + (clip_weight if b.clipreq else 0.0)
        if b.raw >= 5 and b.dominant >= 0.6:
            b.flags.append("spam?")
            score *= 0.5
        if b.people <= 2 and b.raw >= 5:
            b.flags.append("few people")
            score *= 0.5
        b.score = score


def confidence(score: float) -> str:
    return "strong" if score >= 6 else ("maybe" if score >= 4 else "weak")


def pick_peaks(bins: list[Bin], top: int, min_gap: float, min_score: float) -> list[Bin]:
    ranked = sorted((b for b in bins if b.score != float("-inf") and b.score >= min_score),
                    key=lambda b: b.score, reverse=True)
    picked: list[Bin] = []
    for b in ranked:
        if all(abs(b.start - p.start) >= min_gap for p in picked):
            picked.append(b)
        if len(picked) >= top:
            break
    picked.sort(key=lambda b: b.score, reverse=True)   # best first: watch clip_01 before clip_10
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
    ap.add_argument("--bin", type=float, default=None, help="window size in seconds (default: auto from chat speed, 10-60)")
    ap.add_argument("--lead", type=float, default=20.0, help="seconds to start before the spike (default 20)")
    ap.add_argument("--duration", type=float, default=45.0, help="clip length in seconds (default 45)")
    ap.add_argument("--top", type=int, default=10, help="how many peaks to keep (default 10)")
    ap.add_argument("--min-gap", type=float, default=90.0, help="min seconds between picked peaks (default 90)")
    ap.add_argument("--min-score", type=float, default=2.5, help="min score to count as a spike (default 2.5)")
    ap.add_argument("--min-messages", type=int, default=5, help="ignore windows with fewer (people-weighted) messages (default 5)")
    ap.add_argument("--window", type=int, default=30, help="baseline neighbourhood, in bins each side (default 30)")
    ap.add_argument("--skip-start", type=float, default=60.0, help="ignore the first N seconds of the stream (default 60)")
    ap.add_argument("--skip-end", type=float, default=60.0, help="ignore the last N seconds of the stream (default 60)")
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
    bin_size = args.bin if args.bin else choose_bin(msgs)
    bins = bin_messages(msgs, bin_size)
    score_bins(bins, window=args.window, min_messages=args.min_messages,
               skip_start=args.skip_start, skip_end=args.skip_end, bin_size=bin_size)
    peaks = pick_peaks(bins, top=args.top, min_gap=args.min_gap, min_score=args.min_score)

    os.makedirs(args.out, exist_ok=True)
    vid_dur = probe_duration(args.video) if args.video else None
    span = msgs[-1].t - msgs[0].t
    authors_known = any(m.author for m in msgs)
    print(f"chat format: {fmt} | messages: {len(msgs)} | span: {hms(span)} | "
          f"window: {bin_size:.0f}s{' (auto)' if not args.bin else ''} | bins: {len(bins)} | "
          f"authors: {'yes' if authors_known else 'no'} | peaks: {len(peaks)}")

    # per-bin activity for plotting / tuning
    with open(os.path.join(args.out, "activity.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["bin_start_s", "time", "messages_raw", "messages_weighted", "people", "hype",
                    "clip_requests", "dominant_share", "baseline", "z", "score", "flags"])
        for b in bins:
            w.writerow([f"{b.start:.0f}", hms(b.start), b.raw, b.count, b.people, b.hype, b.clipreq,
                        f"{b.dominant:.2f}", f"{b.baseline:.1f}", f"{b.z:.2f}",
                        "" if b.score == float("-inf") else f"{b.score:.2f}", " ".join(b.flags)])

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
            "messages_raw": b.raw, "messages_weighted": b.count, "people": b.people,
            "baseline": round(b.baseline, 1), "z": round(b.z, 2),
            "hype": b.hype, "clip_requests": b.clipreq, "score": round(b.score, 2),
            "confidence": confidence(b.score),
            "flags": " ".join(b.flags), "file": clip_name if args.video else "", "samples": b.samples,
        }
        rows.append(row)
        video_arg = args.video or "VOD.mp4"
        sh_lines.append(f"# {i:02d}  spike {hms(spike_t)}  score {b.score:.2f}  "
                        f"({b.people} people, {b.raw} msgs, baseline {b.baseline:.0f})")
        sh_lines.append(shlex.join(build_clip_cmd(video_arg, start, dur, clip_path, args.vertical, args.copy)))
        if args.thumbs:
            sh_lines.append(shlex.join(build_thumb_cmd(video_arg, spike_t + bin_size / 2, thumb_path)))
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
                subprocess.run(build_thumb_cmd(args.video, spike_t + bin_size / 2, thumb_path),
                               capture_output=True, text=True)

    with open(os.path.join(args.out, "ffmpeg_commands.sh"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(sh_lines))
    os.chmod(os.path.join(args.out, "ffmpeg_commands.sh"), 0o755)

    settings = {k: v for k, v in vars(args).items()}
    settings["bin_effective"] = bin_size
    with open(os.path.join(args.out, "highlights.json"), "w", encoding="utf-8") as fh:
        json.dump({"chat_file": args.chat, "chat_format": fmt, "video": args.video,
                   "settings": settings, "highlights": rows}, fh, indent=2, ensure_ascii=False)

    fieldnames = [k for k in (rows[0].keys() if rows else ["rank"]) if k != "samples"]
    with open(os.path.join(args.out, "highlights.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: v for k, v in r.items() if k != "samples"})

    md = [f"# Highlights for `{os.path.basename(args.chat)}`", "",
          f"{len(msgs)} chat messages over {hms(span)}; window {bin_size:.0f}s; "
          f"first/last {args.skip_start:.0f}s ignored; clips start {args.lead:.0f}s before each spike "
          f"and run {args.duration:.0f}s.", "",
          "Rows are ordered best first.", "",
          "| # | Spike at | Clip start | Len | People | Msgs | Hype | Clip req | Score | Confidence | Flags | File |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['rank']} | {r['spike_at']} | {r['clip_start']} | {r['clip_len_s']:.0f}s | {r['people']} | "
                  f"{r['messages_raw']} | {r['hype']} | {r['clip_requests']} | {r['score']} | {r['confidence']} | "
                  f"{r['flags']} | {r['file']} |")
    md.append("")
    md.append("## What chat said at each spike")
    md.append("")
    for r in rows:
        md.append(f"**{r['rank']}. {r['spike_at']}** ({r['confidence']}, score {r['score']}, {r['people']} people"
                  f"{', ' + r['flags'] if r['flags'] else ''})")
        for s in r["samples"]:
            md.append(f"- {s}")
        md.append("")
    md.append("Review every clip before posting. 'spam?' means one message was most of the window; "
              "'few people' means one or two chatters produced it. Raids and giveaways also spike chat.")
    with open(os.path.join(args.out, "highlights.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))

    print(f"wrote {args.out}/highlights.md, highlights.csv, highlights.json, activity.csv, ffmpeg_commands.sh")
    if not peaks:
        print("No spikes cleared --min-score; try lowering it or --min-messages.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
