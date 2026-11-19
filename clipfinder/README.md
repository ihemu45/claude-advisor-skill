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
| `--bin` | auto | Window size. Picked from chat speed (10 s busy, up to 60 s quiet); set it to override |
| `--lead` | 20 s | How far before the spike the clip starts |
| `--duration` | 45 s | 30 to 60 s is the short-form sweet spot |
| `--top` | 10 | Candidates to cut |
| `--min-gap` | 90 s | Prevents three clips of one moment. Lower for short test videos |
| `--min-score` | 2.5 | Raise if you get too many weak spikes |
| `--min-messages` | 5 | Counted per person, spam collapsed. Raise for huge chats, lower for tiny ones |
| `--skip-start`, `--skip-end` | 60 s | Ignore the stream's opening and closing minute (greetings, goodbyes) |
| `--vertical` | none | `center` crops the middle; `blur` keeps the full frame over a blurred background |
| `--offset` | 0 | If chat timestamps are out of sync with the video |
| `--copy` | off | Instant cuts with no re-encode, but no reframe and keyframe-inaccurate |
| `--dry-run` | off | Only write `ffmpeg_commands.sh`, cut nothing |

`activity.csv` has every window's count and score if you want to plot a stream's
whole arc or tune thresholds.

How a spike is scored: messages are counted per person (at most 3 per person per
window, identical repeats once), so one spammer cannot make a spike. A window where
one message is most of the traffic is flagged `spam?` and a window produced by one or
two chatters is flagged `few people`; both are halved. Reaction words and genuine
"clip it" shouts add to the score; clippers asking "how do I clip" do not.

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

---

## Autopilot: the bots

Three scripts turn the manual steps above into a schedule plus one tap per clip.

```
autoclip.py          the watcher: new streams -> chat -> spikes -> only those 45 s downloaded -> clips + captions -> queue
review_server.py     the page you look at: video, what chat said, caption, Approve / Reject
publish_youtube.py   uploads approved clips to your YouTube channel as Shorts, writes the links to paste into campaigns
install_schedule.sh  runs the watcher every 2 hours on your Mac (launchd), even with Terminal closed
```

What stays yours: tapping Approve (10 seconds a clip), and pasting the posted link
into the campaign page (30 seconds a clip). Everything else runs on its own.

### Set up once

1. Copy the config and edit the channel list, hashtags, and campaign links:

```bash
cp config.example.json config.json
```

2. Optional, for burned-in captions:

```bash
pip install faster-whisper
```

3. Optional, for YouTube uploads (skip if you will post by hand or through OpenShorts):
   - console.cloud.google.com, create a project, enable **YouTube Data API v3**
   - OAuth consent screen: External, add your own Google account under Test users
   - Credentials, Create credentials, **OAuth client ID**, type **Desktop app**, download the JSON
   - save it as `client_secret.json` in this folder, then:

```bash
pip install google-api-python-client google-auth-oauthlib
```

   The first publish opens a browser window once to approve posting to your channel.
   Uploads are **private** by default so nothing goes live unseen; set `"privacy": "public"`
   in config.json once you trust it. The free YouTube quota allows about six uploads a day.

### Daily use

```bash
python3 autoclip.py run
```

```bash
python3 review_server.py
```

Approve the good ones, press **Publish approved clips to YouTube**, then open
`submit_queue.md` in the data folder and paste each link into its campaign page.
For TikTok, use the **Download clip** link on the card and the caption box; upload
at tiktok.com/upload.

### Hands-off schedule

```bash
./install_schedule.sh
```

The watcher then runs every two hours while the Mac is awake. You only open the
review page when you like. Remove it with `./uninstall_schedule.sh`.

### Testing without the internet

```bash
python3 make_test_data.py testdata
```

```bash
python3 autoclip.py --config config.json run --local-chat testdata/chat_twitchdownloader.json --local-video testdata/test_stream.mp4 --stream-id test1 --title "test"
```

### Config keys that matter

| Key | Default | Meaning |
|---|---|---|
| `channels[]` | Tomo | One entry per streamer: `url` is the channel's `/streams` tab; `campaign_url`, `hashtags`, `credit`, `required_tags` feed the caption |
| `min_confidence` | `maybe` | Cut only `maybe` and `strong` spikes; set `strong` for fewer, better clips |
| `top` | 6 | Max clips per stream |
| `max_streams_per_run` | 2 | Newest streams handled per run |
| `max_stream_age_days`, `min_stream_minutes` | 7, 20 | Ignore old or tiny streams |
| `vertical` | `blur` | `blur` keeps the full frame; `center` crops |
| `captions` | true | Burn captions when faster-whisper is installed |
| `llm_hooks` | false | Let Claude write the hook line; needs an Anthropic API key, otherwise rules are used |
| `youtube.privacy` | `private` | `private` until you trust it, then `public` |

Rights still apply: only add channels whose campaign allows clipping them. The
watcher cannot tell when another streamer appears on screen, so the approve step
is where that gets caught.
