"""Self-benchmark suite runner — the harness measures itself and keeps the receipts.

Runs every goal in benchmarks/suite/ against a live reinsman server, scores each
run with the built-in LLM judge (POST /tasks/{id}/evaluate), appends the results
to benchmarks/ledger.json, and regenerates the human-readable LEDGER.md and the
progress chart curve.svg. Nothing here fabricates numbers: every ledger entry
points at task ids whose full traces live in traces/.

Usage:
    python benchmarks/run_suite.py              # full run (server must be up)
    python benchmarks/run_suite.py --dry-run    # plumbing check: parse suite,
                                                # re-render ledger outputs, no API calls

Env: REINSMAN_URL (default http://127.0.0.1:8848)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
SUITE_DIR = BENCH_DIR / "suite"
LEDGER_JSON = BENCH_DIR / "ledger.json"
LEDGER_MD = BENCH_DIR / "LEDGER.md"
CURVE_SVG = BENCH_DIR / "curve.svg"
BASE_URL = os.environ.get("REINSMAN_URL", "http://127.0.0.1:8848").rstrip("/")
POLL_SECONDS = 15
TERMINAL = {"done", "incomplete", "error", "cancelled"}


def parse_suite() -> list[dict]:
    """Parse suite/*.md files: --- frontmatter (id/axis/timeout_minutes) + goal body."""
    goals = []
    for path in sorted(SUITE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta: dict = {"id": path.stem, "axis": "?", "timeout_minutes": 15}
        body = text
        if text.startswith("---"):
            _, fm, body = text.split("---", 2)
            for line in fm.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        meta["timeout_minutes"] = int(meta["timeout_minutes"])
        meta["goal"] = body.strip()
        goals.append(meta)
    return goals


def api(method: str, path: str, payload: dict | None = None, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"content-type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def run_goal(item: dict) -> dict:
    print(f"[suite] {item['id']}: submitting...")
    task_id = api("POST", "/goal", {"goal": item["goal"], "variant": "benchmark"})["task_id"]
    deadline = time.time() + item["timeout_minutes"] * 60
    status = "running"
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        status = api("GET", f"/tasks/{task_id}")["status"]
        if status in TERMINAL:
            break
    else:
        status = "timeout"
    print(f"[suite] {item['id']}: {status}; judging...")
    judge = {}
    if status in ("done", "incomplete"):
        try:
            judge = api("POST", f"/tasks/{task_id}/evaluate").get("judge", {}) or {}
        except urllib.error.URLError as e:
            print(f"[suite] {item['id']}: evaluate failed: {e}")
    return {
        "id": item["id"],
        "axis": item["axis"],
        "task_id": task_id,
        "status": status,
        # a run that produced nothing scores 0 — failure is part of the record
        "overall": judge.get("overall") if judge.get("overall") is not None else 0.0,
        "completion": judge.get("completion"),
        "quality": judge.get("quality"),
        "safety": judge.get("safety"),
        "efficiency": judge.get("efficiency"),
    }


def harness_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=BENCH_DIR.parent,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def load_ledger() -> list[dict]:
    if LEDGER_JSON.exists():
        return json.loads(LEDGER_JSON.read_text(encoding="utf-8"))
    return []


def render_md(runs: list[dict]) -> None:
    lines = [
        "# 📈 Self-Benchmark Ledger",
        "",
        "The harness re-runs the fixed goal suite in [`suite/`](suite/) on a schedule,",
        "scores itself with its own LLM judge, and appends the result here. Task ids",
        "map to replayable traces. Failures stay on the record. See",
        "[`run_suite.py`](run_suite.py) — no number in this file is hand-written.",
        "",
        "![progress](curve.svg)",
        "",
        "| # | date (UTC) | harness | suite overall | " + " | ".join(
            r["id"] for r in (runs[-1]["results"] if runs else [])) + " |",
    ]
    if runs:
        ncols = 4 + len(runs[-1]["results"])
        lines.append("|" + "---|" * ncols)
        for i, run in enumerate(runs, 1):
            per_goal = " | ".join(
                f"{r['overall']:.2f} ({r['task_id']})" for r in run["results"])
            lines.append(
                f"| {i} | {run['run_at'][:16]} | `{run['harness_sha']}` "
                f"| **{run['suite_overall']:.3f}** | {per_goal} |")
    else:
        lines.append("|---|---|---|---|")
        lines.append("| — | no runs yet | — | — |")
    LEDGER_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_svg(runs: list[dict]) -> None:
    w, h, pad = 640, 240, 40
    pts = [(i, run["suite_overall"]) for i, run in enumerate(runs)]
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        'font-family="ui-monospace, Consolas, monospace" font-size="11">',
        f'<rect width="{w}" height="{h}" fill="#0d1117" rx="8"/>',
        f'<text x="{pad}" y="24" fill="#8b949e" font-size="13">suite overall (0–1) — '
        'self-measured, one point per benchmark run</text>',
    ]
    for frac in (0.0, 0.5, 1.0):
        y = h - pad - frac * (h - 2 * pad)
        svg.append(f'<line x1="{pad}" y1="{y:.0f}" x2="{w - 16}" y2="{y:.0f}" '
                   'stroke="#21262d"/>')
        svg.append(f'<text x="8" y="{y + 4:.0f}" fill="#484f58">{frac:.1f}</text>')
    if pts:
        step = (w - pad - 24) / max(len(pts) - 1, 1)
        coords = [(pad + i * step if len(pts) > 1 else (w + pad - 24) / 2,
                   h - pad - v * (h - 2 * pad)) for i, v in pts]
        if len(coords) > 1:
            path = " ".join(f"{x:.0f},{y:.0f}" for x, y in coords)
            svg.append(f'<polyline points="{path}" fill="none" stroke="#58a6ff" '
                       'stroke-width="2"/>')
        for x, y in coords:
            svg.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="3.5" fill="#58a6ff"/>')
    else:
        svg.append(f'<text x="{w / 2}" y="{h / 2}" text-anchor="middle" '
                   'fill="#484f58">no runs yet</text>')
    svg.append("</svg>")
    CURVE_SVG.write_text("\n".join(svg) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="parse suite + re-render outputs from the existing ledger; no API calls")
    args = ap.parse_args()

    suite = parse_suite()
    if not suite:
        print("no goals found in benchmarks/suite/", file=sys.stderr)
        return 1
    print(f"[suite] {len(suite)} goals: " + ", ".join(g["id"] for g in suite))

    runs = load_ledger()
    if args.dry_run:
        render_md(runs)
        render_svg(runs)
        print(f"[dry-run] outputs re-rendered from {len(runs)} recorded run(s). No API calls made.")
        return 0

    results = [run_goal(item) for item in suite]
    entry = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness_sha": harness_sha(),
        "results": results,
        "suite_overall": round(sum(r["overall"] for r in results) / len(results), 3),
    }
    runs.append(entry)
    LEDGER_JSON.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    render_md(runs)
    render_svg(runs)
    print(f"[suite] run recorded: overall {entry['suite_overall']} @ {entry['harness_sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
