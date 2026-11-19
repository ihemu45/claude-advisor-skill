#!/usr/bin/env python3
"""
add_captions.py - transcribe a clip with faster-whisper (if installed) and burn
big, readable captions into it with FFmpeg. You can also pass an existing SRT.

  python3 add_captions.py clip.mp4                       # transcribe + burn -> clip_captioned.mp4
  python3 add_captions.py clip.mp4 --srt clip.srt        # burn an existing SRT
  python3 add_captions.py clip.mp4 --model small --lang en

Install the optional transcriber once:  pip install faster-whisper
(CPU works; a GPU makes it much faster. The 'small' model is a good default.)
"""
import argparse
import os
import subprocess
import sys


def srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(video: str, srt_path: str, model: str, lang: str | None, max_words: int) -> None:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        sys.exit("faster-whisper is not installed. Run: pip install faster-whisper  "
                 "(or pass --srt with an existing subtitle file)")
    wm = WhisperModel(model, device="auto", compute_type="auto")
    segments, _ = wm.transcribe(video, language=lang, word_timestamps=True, vad_filter=True)
    # regroup words into short caption chunks (short-form style: 3-5 words per line)
    chunks = []
    cur, cur_start = [], None
    for seg in segments:
        for w in (seg.words or []):
            if cur_start is None:
                cur_start = w.start
            cur.append(w.word.strip())
            if len(cur) >= max_words or w.word.strip().endswith((".", "!", "?")):
                chunks.append((cur_start, w.end, " ".join(cur)))
                cur, cur_start = [], None
        if cur and cur_start is not None:
            chunks.append((cur_start, seg.end, " ".join(cur)))
            cur, cur_start = [], None
    with open(srt_path, "w", encoding="utf-8") as fh:
        for i, (a, b, text) in enumerate(chunks, 1):
            fh.write(f"{i}\n{srt_time(a)} --> {srt_time(max(b, a + 0.3))}\n{text}\n\n")


def burn(video: str, srt_path: str, out: str, font: str, size: int, margin_v: int) -> None:
    style = (f"FontName={font},FontSize={size},Bold=1,PrimaryColour=&H00FFFFFF,"
             f"OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=1,Outline=3,Shadow=0,"
             f"Alignment=2,MarginV={margin_v}")
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video,
           "-vf", f"subtitles='{srt_escaped}':force_style='{style}'",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--srt", help="use this SRT instead of transcribing")
    ap.add_argument("--out", help="output file (default <video>_captioned.mp4)")
    ap.add_argument("--model", default="small", help="faster-whisper model: tiny/base/small/medium/large-v3")
    ap.add_argument("--lang", default=None, help="language code, e.g. en (default: auto)")
    ap.add_argument("--max-words", type=int, default=4, help="words per caption chunk (default 4)")
    ap.add_argument("--font", default="DejaVu Sans")
    ap.add_argument("--size", type=int, default=22)
    ap.add_argument("--margin-v", type=int, default=100, help="distance from bottom edge, in ASS units")
    a = ap.parse_args()

    stem = os.path.splitext(a.video)[0]
    out = a.out or f"{stem}_captioned.mp4"
    srt = a.srt or f"{stem}.srt"
    if not a.srt:
        transcribe(a.video, srt, a.model, a.lang, a.max_words)
        print(f"wrote {srt}")
    burn(a.video, srt, out, a.font, a.size, a.margin_v)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
