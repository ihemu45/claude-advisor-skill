#!/usr/bin/env python3
"""
make_test_data.py - build a synthetic "stream" so the pipeline can be tested
without downloading anything: a 4-minute test video (with a burned-in clock)
and a fake chat log with three planted spikes, written in all three supported
formats (TwitchDownloader JSON, yt-dlp live_chat JSONL, CSV).

Planted spikes (seconds): 60 (hype words), 150 (plain burst), 200 ("CLIP IT").
"""
import csv
import json
import os
import random
import subprocess
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "testdata"
DURATION = 240  # seconds
os.makedirs(OUT, exist_ok=True)
random.seed(7)

# ---------------- video ----------------
video = os.path.join(OUT, "test_stream.mp4")
drawtext = "drawtext=text='%{pts\\:hms}':fontsize=64:fontcolor=white:box=1:boxcolor=black@0.6:x=40:y=40"
base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(DURATION)]
enc = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video]
r = subprocess.run(base + ["-vf", drawtext] + enc, capture_output=True, text=True)
if r.returncode != 0:  # no font available -> skip the clock overlay
    subprocess.run(base + enc, check=True)
    print("video written without clock overlay (no font found)")
else:
    print("video written with clock overlay")

# ---------------- chat ----------------
boring = ["hi", "first", "gg", "nice", "what game is this", "ez", "hello from brazil",
          "song name?", "is this live", "W", "L", "ok", "lets go", "cool", "F"]
hype = ["LMAOOO", "NO WAY", "WHAT JUST HAPPENED", "HAHAHAHA", "KEKW", "OMG", "😂😂😂",
        "💀💀", "BRO", "INSANE", "LUL", "YOOOO", "nooooo"]
clip = ["CLIP IT", "someone clip that", "CLIP CLIP CLIP", "clip it now"]

msgs = []  # (t, text)
t = 0.0
while t < DURATION:
    # baseline ~0.8 msg/s, exponential inter-arrival
    t += random.expovariate(0.8)
    if t < DURATION:
        msgs.append((t, random.choice(boring)))

def burst(center, width, n, pool, extra=None):
    for _ in range(n):
        tt = random.gauss(center, width)
        if 0 <= tt < DURATION:
            msgs.append((tt, random.choice(pool)))
    if extra:
        for _ in range(extra[0]):
            tt = random.gauss(center, width)
            if 0 <= tt < DURATION:
                msgs.append((tt, random.choice(extra[1])))

burst(63, 4, 70, hype)              # reaction a few seconds after a 60 s event
burst(152, 3, 45, boring + hype)    # medium burst, mixed
burst(203, 4, 90, hype, (12, clip)) # biggest, with clip requests
# noise the finder must ignore: a stream-start greeting burst and one spammer at ~105 s
for _ in range(30):
    msgs.append((random.uniform(0, 8), random.choice(["hi", "first", "hello", "yo"])))
SPAM = [(random.uniform(102, 108), "TOMO CAN YOU ADD SUBWAY SURFERS PLEASE??? WE ARE BORED!!!") for _ in range(25)]
msgs.sort()
# authors: 50 regular chatters; spam messages all come from one account
authored = [(t, txt, f"user{i % 50}") for i, (t, txt) in enumerate(msgs)]
authored += [(t, txt, "spammer1") for t, txt in SPAM]
authored.sort()
msgs = [(t, txt) for t, txt, _ in authored]

# TwitchDownloader-style JSON
td = {"streamer": {"name": "test_streamer", "id": 1},
      "video": {"start": 0, "end": DURATION, "length": DURATION},
      "comments": [{"_id": str(i), "content_offset_seconds": round(tt, 3),
                    "commenter": {"display_name": authored[i][2], "_id": authored[i][2]},
                    "message": {"body": txt, "fragments": [{"text": txt}]}}
                   for i, (tt, txt) in enumerate(msgs)]}
with open(os.path.join(OUT, "chat_twitchdownloader.json"), "w", encoding="utf-8") as fh:
    json.dump(td, fh, ensure_ascii=False)

# yt-dlp live_chat.json (JSONL)
with open(os.path.join(OUT, "test_stream.live_chat.json"), "w", encoding="utf-8") as fh:
    for i, (tt, txt) in enumerate(msgs):
        runs = [{"text": txt}]
        if "😂" in txt:
            runs = [{"text": txt.replace("😂", "")}, {"emoji": {"emojiId": "😂", "shortcuts": [":face_with_tears_of_joy:"]}}]
        obj = {"replayChatItemAction": {
                   "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
                       "message": {"runs": runs}, "authorName": {"simpleText": authored[i][2]},
                       "id": str(i), "timestampUsec": str(int(tt * 1e6))}}}}],
                   "videoOffsetTimeMsec": str(int(tt * 1000))},
               "videoOffsetTimeMsec": str(int(tt * 1000)), "isLive": False}
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

# CSV
with open(os.path.join(OUT, "chat.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["seconds", "message"])
    for tt, txt in msgs:
        w.writerow([f"{tt:.3f}", txt])

print(f"{len(msgs)} chat messages written in 3 formats under {OUT}/")
print("planted spikes at ~60s, ~150s, ~200s; noise: greeting burst at 0-8s, one spammer at ~105s")
