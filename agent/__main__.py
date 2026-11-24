"""CLI: python -m agent --out site/data [--previous-url URL] [--sample] [--no-research]"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, pipeline, research, sources


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent", description="dealgrep hourly research agent")
    ap.add_argument("--out", default="site/data", help="output directory for deals.json / meta.json / runs.json")
    ap.add_argument("--previous-url", default=os.environ.get("PREVIOUS_DATA_URL"), help="base URL of the live site's data/ folder, used to carry history forward")
    ap.add_argument("--sample", action="store_true", help="use bundled fixtures instead of the network and mark the output as sample data")
    ap.add_argument("--no-research", action="store_true", help="skip the Claude web-research step")
    ap.add_argument("--sources", default=str(config.SOURCES_FILE), help="path to sources.json")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    now = datetime.now(timezone.utc)
    out_dir = Path(args.out)
    print(f"[dealgrep] run at {pipeline.iso(now)} sample={args.sample} out={out_dir}")

    previous, runs, origin = ([], [], "none") if args.sample else pipeline.load_previous(out_dir, args.previous_url)
    print(f"[dealgrep] previous state: {len(previous)} deals from {origin}")

    src_cfg = sources.load_sources(args.sources)
    raw, reports = sources.run_sources(src_cfg, sample=args.sample)
    for r in reports:
        status = "ok" if r["ok"] else ("skip" if r.get("skipped") else "FAIL")
        detail = r.get("skipped") or r.get("error") or ""
        print(f"[source] {r['id']:<28} {status:<4} {r['count']:>4} items {r['ms']:>6} ms  {detail}")

    if args.no_research:
        research_raw, research_report = [], {"enabled": False, "skipped": "--no-research", "categories": [], "found": 0, "errors": [], "input_tokens": 0, "output_tokens": 0, "web_searches": 0, "model": config.RESEARCH_MODEL}
    else:
        research_raw, research_report = research.run_research(sample=args.sample, now=now)
    if research_report.get("enabled"):
        print(f"[research] {research_report['found']} deals from {research_report['categories']} via {research_report.get('model')} "
              f"(in={research_report['input_tokens']} out={research_report['output_tokens']} searches={research_report['web_searches']})")
        for e in research_report.get("errors", []):
            print(f"[research] error: {e}")
    else:
        print(f"[research] skipped: {research_report.get('skipped')}")

    now_iso = pipeline.iso(now)
    fresh = [d for d in (pipeline.normalize(r, now_iso) for r in raw + research_raw) if d]
    deals, new_count = pipeline.merge(previous, fresh, now)
    deals = pipeline.finalize(deals, now)
    duration_ms = int((time.monotonic() - t0) * 1000)
    meta = pipeline.build_meta(deals, now=now, sample=args.sample, source_reports=reports, research_report=research_report, new_count=new_count, duration_ms=duration_ms)
    summary = pipeline.run_summary(meta)
    runs = ([summary] + runs)[: config.RUN_LOG_LIMIT]

    pipeline.write_json(out_dir / "deals.json", {"generated_at": now_iso, "sample": args.sample, "deals": deals})
    pipeline.write_json(out_dir / "meta.json", meta)
    pipeline.write_json(out_dir / "runs.json", runs)
    print(f"[dealgrep] wrote {len(deals)} deals ({new_count} new) in {duration_ms} ms -> {out_dir}")

    ok_sources = sum(1 for r in reports if r["ok"])
    if ok_sources == 0 and not research_report.get("found") and not previous:
        print("[dealgrep] WARNING: every source failed and there is no previous data", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
