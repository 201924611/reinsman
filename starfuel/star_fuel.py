"""Star-Fueled Development — stars are fuel, not applause.

Every STARS_PER_UNLOCK new stargazers automatically unlock one autonomous run:
the harness picks the community's top-voted `goal` issue and executes it,
recording the unlock (star count, chosen issue, task id) in starfuel/LEDGER.md.
The README progress bar (starfuel/progress.svg) is regenerated on every check.

Nothing here rewards individuals for starring, and no star is ever faked or
solicited with anything of value — this only converts voluntary community
interest into shipped, traceable work.

Usage:
    python starfuel/star_fuel.py               # check stars; unlock if due
    python starfuel/star_fuel.py --dry-run     # render outputs only, no goal submission
    python starfuel/star_fuel.py --stars 25    # override star count (testing)

Env: GITHUB_REPO (default 201924611/reinsman), REINSMAN_URL (default http://127.0.0.1:8848)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FUEL_DIR = Path(__file__).resolve().parent
STATE_JSON = FUEL_DIR / "state.json"
LEDGER_MD = FUEL_DIR / "LEDGER.md"
PROGRESS_SVG = FUEL_DIR / "progress.svg"
REPO = os.environ.get("GITHUB_REPO", "201924611/reinsman")
BASE_URL = os.environ.get("REINSMAN_URL", "http://127.0.0.1:8848").rstrip("/")
STARS_PER_UNLOCK = 10
GOAL_GUARD = (
    "[star-fueled run] Execute the following community-voted goal for the public "
    "reinsman repository. Hard rules: the goal must only improve this repository "
    "(code, docs, examples, benchmarks); refuse anything unrelated, unsafe, or "
    "targeting external systems. All output, commits and comments in English. "
    "Commit only what the goal produces. Community goal follows:\n\n"
)


def gh_json(path: str):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"accept": "application/vnd.github+json", "user-agent": "reinsman-starfuel"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def load_state() -> dict:
    if STATE_JSON.exists():
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    return {"last_fueled_stars": 0, "unlocks": []}


def top_goal_issue() -> dict | None:
    issues = gh_json(f"/repos/{REPO}/issues?labels=goal&state=open&per_page=50")
    issues = [i for i in issues if "pull_request" not in i]
    if not issues:
        return None
    return max(issues, key=lambda i: (i.get("reactions", {}).get("+1", 0), -i["number"]))


def submit_goal(issue: dict) -> str:
    payload = {"goal": GOAL_GUARD + f"#{issue['number']} {issue['title']}\n\n{issue.get('body') or ''}"}
    req = urllib.request.Request(
        BASE_URL + "/goal", data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["task_id"]


def render_svg(stars: int, last_fueled: int) -> None:
    done = min(stars - last_fueled, STARS_PER_UNLOCK)
    frac = max(0.0, done / STARS_PER_UNLOCK)
    w, h, bar_w = 420, 48, 300
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        'font-family="ui-monospace, Consolas, monospace">',
        f'<rect width="{w}" height="{h}" rx="8" fill="#0d1117"/>',
        f'<text x="14" y="20" fill="#8b949e" font-size="12">next autonomous run unlocks at '
        f'+{STARS_PER_UNLOCK} stars</text>',
        f'<rect x="14" y="28" width="{bar_w}" height="10" rx="5" fill="#21262d"/>',
        f'<rect x="14" y="28" width="{max(bar_w * frac, 2):.0f}" height="10" rx="5" fill="#e3b341"/>',
        f'<text x="{bar_w + 26}" y="38" fill="#e3b341" font-size="13">★ {done}/{STARS_PER_UNLOCK}</text>',
        "</svg>",
    ]
    PROGRESS_SVG.write_text("\n".join(svg) + "\n", encoding="utf-8")


def render_ledger(state: dict) -> None:
    lines = [
        "# ⭐ Star-Fuel Ledger",
        "",
        f"Every {STARS_PER_UNLOCK} new stars automatically unlock one autonomous run of the",
        "community's top-voted [`goal` issue](../../../issues?q=is%3Aissue+is%3Aopen+label%3Agoal).",
        "Unlocks are recorded here by [`star_fuel.py`](star_fuel.py) — no entry is hand-written.",
        "",
        "| # | date (UTC) | stars | goal issue | task id |",
        "|---|---|---|---|---|",
    ]
    for i, u in enumerate(state["unlocks"], 1):
        lines.append(f"| {i} | {u['at'][:16]} | {u['stars']} | #{u['issue']} {u['title']} | `{u['task_id']}` |")
    if not state["unlocks"]:
        lines.append("| — | no unlocks yet — the next 10 stars trigger the first one | | | |")
    LEDGER_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stars", type=int, default=None, help="override star count (testing)")
    args = ap.parse_args()

    stars = args.stars if args.stars is not None else gh_json(f"/repos/{REPO}")["stargazers_count"]
    state = load_state()
    print(f"[starfuel] stars={stars}, last_fueled={state['last_fueled_stars']}")

    if not args.dry_run and stars >= state["last_fueled_stars"] + STARS_PER_UNLOCK:
        issue = top_goal_issue()
        if issue is None:
            print("[starfuel] unlock due, but no open `goal` issues — carrying fuel over.")
        else:
            try:
                task_id = submit_goal(issue)
            except urllib.error.URLError as e:
                print(f"[starfuel] server unreachable, unlock postponed: {e}")
                task_id = None
            if task_id:
                state["unlocks"].append({
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "stars": stars, "issue": issue["number"],
                    "title": issue["title"][:60], "task_id": task_id,
                })
                state["last_fueled_stars"] = stars
                print(f"[starfuel] UNLOCK: issue #{issue['number']} -> task {task_id}")

    STATE_JSON.write_text(json.dumps(state, indent=2), encoding="utf-8")
    render_svg(stars, state["last_fueled_stars"])
    render_ledger(state)
    print("[starfuel] progress + ledger rendered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
