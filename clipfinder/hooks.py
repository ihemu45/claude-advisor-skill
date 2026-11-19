#!/usr/bin/env python3
"""
hooks.py - write the first line (the hook) and the full caption for a clip.

Default: rule-based from what chat said, no network, no cost.
Optional: set "llm_hooks": true in config.json and have an Anthropic API key
available (ANTHROPIC_API_KEY, or `ant auth login`) to let Claude write the hook
from the chat reactions instead. Falls back to the rules on any error.
"""
from __future__ import annotations

import os
import re

RULES = [
    (r"landing|landed|\blandy\b",          "Chat rated this GTA 6 landing 10/10 ✈️"),
    (r"drive into|drove|crash|wreck|totaled", "His first drive in GTA 6 lasted 10 seconds 💀"),
    (r"realistic|graphics|looks so|beautiful|gorgeous", "First time seeing GTA 6 graphics and chat lost it 😭"),
    (r"no way|what just happened|wha+t",  "WHAT just happened in GTA 6 💀"),
    (r"police|cops|wanted|5 stars",       "Five stars in GTA 6 went exactly how you'd expect 🚔"),
    (r"plane|jet|heli|fly|flying",        "GTA 6 flying physics had chat screaming ✈️"),
    (r"lucia|jason",                      "Chat was NOT ready for this GTA 6 moment 😳"),
    (r"lmao|lol|haha|kekw|lul|😂|face_with_tears",  "GTA 6 had him crying laughing 😂"),
    (r"insane|crazy|holy|bruh|💀|skull",  "GTA 6 is actually insane 💀"),
]
DEFAULT_HOOK = "{streamer}'s GTA 6 stream had chat in shambles 😭"


def rule_hook(streamer: str, chat_samples: list[str]) -> str:
    blob = " ".join(chat_samples).lower()
    for pattern, hook in RULES:
        if re.search(pattern, blob, re.IGNORECASE):
            return hook
    return DEFAULT_HOOK.format(streamer=streamer)


def llm_hook(streamer: str, stream_title: str, chat_samples: list[str]) -> str | None:
    """Ask Claude for one hook line. Returns None on any problem so the caller can fall back."""
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=200,  # one short line
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=("You write the first line of TikTok and YouTube Shorts captions for gaming clips. "
                    "Reply with exactly one line, at most 70 characters, no hashtags, no quotes, "
                    "no emoji except at most one at the end. Lead with the payoff, never with the game's name. "
                    "Never state anything as fact that the chat lines do not support."),
            messages=[{"role": "user", "content":
                       f"Streamer: {streamer}\nStream title: {stream_title}\n"
                       f"What chat said during the moment:\n- " + "\n- ".join(chat_samples[:8]) +
                       "\n\nWrite the hook."}],
        )
        if response.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        text = text.splitlines()[0].strip().strip('"').strip("'") if text else ""
        return text[:90] if text else None
    except (anthropic.APIStatusError, anthropic.APIConnectionError):
        return None
    except Exception:
        return None


def make_caption(streamer: str, handle: str, stream_title: str, chat_samples: list[str],
                 hashtags: str = "", credit: str = "", required_tags: str = "",
                 use_llm: bool = False) -> tuple[str, str]:
    """Return (hook, full caption)."""
    hook = None
    if use_llm:
        hook = llm_hook(streamer, stream_title, chat_samples)
    if not hook:
        hook = rule_hook(streamer, chat_samples)
    credit = credit or (f"clips from @{handle} live stream" if handle else f"clips from {streamer}'s live stream")
    tags = " ".join(t for t in [required_tags.strip(), hashtags.strip()] if t)
    caption = "\n".join(x for x in [hook, credit, tags] if x)
    return hook, caption


if __name__ == "__main__":
    import sys
    samples = sys.argv[1:] or ["W LANDING", "10/10", "YOOOO"]
    h, c = make_caption("Tomo", "Tomovids", "PLAYING GTA 6 FOR THE FIRST TIME", samples,
                        hashtags="#GTA6 #GTAVI #Tomo #gaming", use_llm=bool(os.environ.get("LLM_HOOKS")))
    print(c)
