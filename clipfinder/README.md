# Stream clip finder

Turn a long gaming stream (Twitch VOD or YouTube live replay) into a handful of
short vertical clips, using the chat as the highlight detector. When something
wild happens, chat explodes a few seconds later. This tool finds those spikes,
backs up 20 seconds so the actual moment is in frame, cuts the clip with FFmpeg,
reframes it to 9:16, and (optionally) burns in captions.

Everything here runs on your own computer. It needs only Python 3.10+ and FFmpeg.
No API keys, no paid services.

```
find_highlights.py   chat spikes -> review sheet -> clips + thumbnails
add_captions.py      transcribe a clip (faster-whisper) and burn in big captions
make_test_data.py    synthetic stream + fake chat so you can test without downloads
run_demo.sh          one-command end-to-end self-test
```

---

## 1. What is automated and what is not

| Step | Automated? | Notes |
|---|---|---|
| Download VOD + chat log | One command | TwitchDownloader or yt-dlp, see section 4 |
| Find the best moments | Yes | `find_highlights.py` |
| Cut clips, reframe to 9:16, thumbnails | Yes | same script |
| Captions | Yes | `add_captions.py`, needs `pip install faster-whisper` once |
| Review clips before posting | **Manual, do not skip** | Chat also spikes for raids, giveaways, stream start |
| Hook text, title, hashtags | Manual or ask Claude | Paste the review sheet into Claude for hooks |
| Upload to TikTok / Reels / Shorts | Manual | Or let OpenShorts publish if you connect it |
| Submit clip links to a campaign | Manual | Each campaign has its own form or Discord |
| Create accounts and payout method | Manual, once | Section 2 |

Realistic time per stream once set up: download 10 to 30 minutes (network bound),
analysis under a minute, cutting 10 clips a few minutes, review and posting 30 to 60 minutes.

## 2. Accounts you need

| Account | Why | Cost | Needed when |
|---|---|---|---|
| Whop (runs Content Rewards) | Where most paid clipping campaigns are listed and paid out | Free; 10% fee on CPM payouts | Before you post anything for money |
| Vyro (MrBeast's clipping marketplace) | Second campaign source, often higher rates | Free | Same |
| Promote.fun | Third campaign source | Free | Optional |
| Discord | Most campaigns coordinate rules, approvals, and disputes in a Discord server | Free | Same |
| TikTok, Instagram, YouTube | Clips must be posted from accounts you control; campaigns pay on views of those posts | Free | Before first post |
| Payout method | Whop and Vyro pay to a connected bank or PayPal. Check that your country is supported before investing time | Free | Before first payout |
| claude.ai | You already have it. Used for hooks, titles, and optionally the OpenShorts connector | Existing | Optional |
| OpenShorts | Hosted AI clipping with a Claude connector. Draws from a minutes balance, free tier is small | Free tier, then paid | Optional alternative to this tool |
| Fiverr or Upwork seller profile | For selling editing as a service, the predictable-income path | Free, platform takes 10 to 20% | Later |

Not needed: a Twitch account (public VODs download without one), a GTA 6 copy or
console (only if you want to clip your own gameplay instead of licensed streams).

## 3. Software on your computer

| Tool | Purpose | Install |
|---|---|---|
| Python 3.10+ | runs the scripts | python.org or your package manager |
| FFmpeg + ffprobe | cutting, cropping, captions | `winget install ffmpeg`, `brew install ffmpeg`, or `apt install ffmpeg` |
| TwitchDownloader CLI | Twitch VOD + chat download | github.com/lay295/TwitchDownloader releases |
| yt-dlp | YouTube VOD + live chat replay download | `pip install yt-dlp` |
| faster-whisper (optional) | captions | `pip install faster-whisper` |
| CapCut (optional) | final polish, trending templates | capcut.com |

Disk: a 6-hour 1080p VOD is 10 to 20 GB. Keep 50 GB free. Any laptop from the last
five years is fine; a GPU only speeds up captions.

## 4. First run, step by step

**A. Prove the pipeline works (no downloads needed)**

```bash
./run_demo.sh
```

Builds a 4-minute synthetic stream with three planted chat spikes, finds them,
and writes vertical clips plus a review sheet to `demo_out/`.

**B. Get a real VOD and its chat**

Twitch (VOD id is the number in the VOD URL):

```bash
TwitchDownloaderCLI chatdownload --id 123456789 -o chat.json
```

```bash
TwitchDownloaderCLI videodownload --id 123456789 -q 1080p60 -o vod.mp4
```

YouTube live replay:

```bash
yt-dlp --write-subs --sub-langs live_chat --skip-download -o "vod" "https://www.youtube.com/watch?v=XXXX"
```

```bash
yt-dlp -f "bv*[height<=1080]+ba" --merge-output-format mp4 -o vod.mp4 "https://www.youtube.com/watch?v=XXXX"
```

Check `--help` if a flag has changed since this was written. Download only
streams you have permission to clip (section 6).

**C. Find and cut**

```bash
python3 find_highlights.py --chat chat.json --video vod.mp4 --vertical center --thumbs --top 12 --out run1
```

Open `run1/highlights.md`. Each row is a candidate with the time, how much chat
spiked, what chat said, and the clip file. Delete the duds.

**D. Captions (optional)**

```bash
python3 add_captions.py run1/clip_03_014520.mp4
```

**E. Post and submit**

Post the keepers from your own TikTok / Reels / Shorts accounts following the
campaign's rules (required hashtags, mentions, no reposting across accounts),
then submit the post links where the campaign asks.

## 5. Tuning knobs

| Flag | Default | When to change |
|---|---|---|
| `--bin` | 10 s | Smaller for very fast chats, larger for slow ones |
| `--lead` | 20 s | How far before the spike the clip starts |
| `--duration` | 45 s | 30 to 60 s is the short-form sweet spot |
| `--top` | 10 | Candidates to cut |
| `--min-gap` | 90 s | Prevents three clips of one moment. Lower for short test videos |
| `--min-score` | 2.5 | Raise if you get too many weak spikes |
| `--min-messages` | 5 | Raise for huge chats, lower for tiny ones |
| `--vertical` | none | `center` crops the middle; `blur` keeps the full frame over a blurred background |
| `--offset` | 0 | If chat timestamps are out of sync with the video |
| `--copy` | off | Instant cuts with no re-encode, but no reframe and keyframe-inaccurate |
| `--dry-run` | off | Only write `ffmpeg_commands.sh`, cut nothing |

`activity.csv` has every window's count and score if you want to plot a stream's
whole arc or tune thresholds.

## 6. Rules that get accounts banned or payouts denied

- **Streamer permission.** A streamer's VOD is their copyright, including their
  webcam and voice. Clip only streamers who run or allow a campaign, or who have
  publicly said clipping is fine.
- **Rockstar's policy.** Gameplay footage is tolerated for fan use and standard
  platform monetization. Isolated cutscenes get pulled, and in-game licensed
  music triggers Content ID claims that take the ad revenue.
- **Platform reuse rules.** YouTube can demonetize a whole channel for clips with
  no added value. Add captions, a hook, and your own framing. Never buy views;
  campaigns audit for it.
- **Campaign rules.** Required tags, posting windows, one account per clipper,
  minimum view thresholds, and per-clip caps are all common. Read them before
  cutting anything.

## 7. Money, so expectations are set

Listed campaign rates cluster around $1 per 1,000 qualified views, with per-clip
caps commonly near $200 and a 10% fee on payout. To clear roughly $1,100 a month
(about ₹1 lakh) you need about 1.3 million qualified views a month, which means
at least seven clips a month that each max out a $200 cap. Treat the first month
as learning what moments and hooks perform, not as income.

The more predictable path is selling editing to small streamers on Fiverr or
Upwork and using this tool to deliver in a tenth of the time.
