"""Persistent knowledge store (LLM-Wiki style).

Permanently archives the data that subagents collect/synthesize as a structured
markdown wiki. Unlike runtime_agents/ (temporary and deleted), this store is
tracked in git and preserved.

Conceptual basis (public academic knowledge): the agent–reward framing of
reinforcement learning (Sutton & Barto, "Reinforcement Learning: An Introduction",
2nd ed.) and the contextual multi-armed bandit view of picking a category to
maximize an immediate feedback reward. Here 'RL' is not a trained model but a
lightweight heuristic driven by a feedback log. Also inspired by the
'external brain / second brain' note-taking idea.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_core import config

from agent_core.applog import get_logger

logger = get_logger()

KB_DIR = config.ROOT / "knowledge"
RAW_DIR = KB_DIR / "00_Raw"
WIKI_DIR = KB_DIR / "10_Wiki"
META_DIR = KB_DIR / "20_Meta"
GRAPH_PATH = META_DIR / "Graph.json"
INDEX_PATH = META_DIR / "Index.md"
POLICY_PATH = META_DIR / "Policy.md"       # human-readable feedback log
POLICY_JSON = META_DIR / "policy.json"     # machine-readable feedback policy (bandit-style)

STD_CATEGORIES = ["Projects", "Topics", "Decisions", "Skills"]
REFACTOR_THRESHOLD = 12  # suggest splitting a folder once its document count exceeds this

# Reward assigned to each feedback signal. A lightweight contextual-bandit heuristic —
# not a trained model: feedback updates a per-category reward tally and learned corrections.
SIGNAL_REWARD = {
    "approved": 1.0, "praised": 1.0, "kept": 0.5,
    "edited": 0.0, "rejected": -1.0, "moved": -1.0,
}
CORRECTION_MIN = 2   # repeated corrections needed before a category is auto-redirected

_lock = threading.Lock()


def _ensure_dirs() -> None:
    for d in (RAW_DIR, WIKI_DIR, META_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for c in STD_CATEGORIES:
        (WIKI_DIR / c).mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slug(title: str) -> str:
    s = re.sub(r"[^\w가-힣 \-]", "", title).strip().replace(" ", "_")
    return (s or "untitled")[:80]


# Keywords for the deterministic guard that forces domain how-to knowledge under Skills/.
# If a subfolder name appears here, it is corrected to Skills even when its parent is not Skills.
DOMAIN_SKILL_SUBS = {
    "웹디자인", "웹프론트엔드", "웹백엔드", "웹아키텍처", "웹",
    "db", "디비", "데이터베이스", "데이터", "모바일", "디자인시스템",
    "프론트엔드", "백엔드", "아키텍처", "인프라", "데브옵스", "보안",
}


def _find_subfolder(name: str) -> str | None:
    """Return the relative path of a subfolder with the given name if one already exists under 10_Wiki (else None)."""
    if not WIKI_DIR.exists():
        return None
    for p in sorted(WIKI_DIR.rglob(name)):
        if p.is_dir():
            return p.relative_to(WIKI_DIR).as_posix()
    return None


def _norm_category(category: str) -> str:
    """Normalize a category into a relative path under 10_Wiki, plus a deterministic guard
    that corrects LLM misclassification. The guard is decided by code, not LLM discretion:
      1) If it is a domain how-to keyword (DOMAIN_SKILL_SUBS), force the parent to Skills/.
      2) If a subfolder of the same name already exists elsewhere, merge into that existing
         location (to avoid splits like Topics/웹프론트엔드 vs Skills/웹프론트엔드)."""
    c = (category or "Topics").strip().strip("/").replace("\\", "/")
    c = re.sub(r"^10_Wiki/", "", c)
    c = c or "Topics"
    parts = [p for p in c.split("/") if p]
    if len(parts) >= 2:
        top, sub, tail = parts[0], parts[1], parts[2:]
        # Guard 1: domain how-to knowledge -> force under Skills/
        if sub.lower() in {s.lower() for s in DOMAIN_SKILL_SUBS} and top != "Skills":
            fixed = "/".join(["Skills", sub, *tail])
            logger.info(f"[kb] category normalized (domain -> Skills): {c} -> {fixed}")
            return fixed
        # Guard 2: if the same subfolder already exists somewhere, merge into it
        existing = _find_subfolder(sub)
        if existing and existing != "/".join(parts[:2]):
            fixed = "/".join([existing, *tail])
            logger.info(f"[kb] category normalized (prefer existing folder): {c} -> {fixed}")
            return fixed
    return c


def _update_graph(slug: str, title: str, category: str, related: list[str]) -> None:
    graph = {"nodes": [], "edges": []}
    if GRAPH_PATH.exists():
        try:
            graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    nodes[slug] = {"id": slug, "title": title, "category": category}
    edges = graph.get("edges", [])
    for r in related:
        edges.append({"from": slug, "to": _slug(r)})
    graph["nodes"] = list(nodes.values())
    graph["edges"] = edges
    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")


def _rebuild_index() -> None:
    lines = ["# 📇 Knowledge Index", "", f"_Auto-generated: {_now_iso()}_", ""]
    dirs = sorted([p for p in WIKI_DIR.rglob("*") if p.is_dir()]) if WIKI_DIR.exists() else []
    has_any = False
    for cat_dir in dirs:
        md = sorted(cat_dir.glob("*.md"))
        if not md:
            continue
        has_any = True
        rel = cat_dir.relative_to(WIKI_DIR).as_posix()
        lines.append(f"## {rel}")
        for m in md:
            lines.append(f"- [[10_Wiki/{rel}/{m.stem}]]")
        lines.append("")
    if not has_any:
        lines.append("_No knowledge has been saved yet._")
    INDEX_PATH.write_text("\n".join(lines), encoding="utf-8")


def save_knowledge(
    title: str,
    summary: str,
    content: str,
    category: str = "Topics",
    *,
    tags: list[str] | None = None,
    related: list[str] | None = None,
    raw_text: str | None = None,
    raw_source: str = "",
    confidence: float = 0.7,
    contradictions: str = "",
) -> dict:
    """Save a single knowledge entry in wiki format and refresh the Index/Graph."""
    _ensure_dirs()
    with _lock:
        norm_cat = _norm_category(category)
        rel_cat = _apply_learned_correction(norm_cat)   # bandit loop: honor learned corrections
        folder = WIKI_DIR / rel_cat
        folder.mkdir(parents=True, exist_ok=True)
        slug = _slug(title)
        path = folder / f"{slug}.md"
        doc_id = uuid.uuid4().hex
        tags = tags or []
        related = related or []

        # Archive the raw original (Source of Truth)
        raw_link = raw_source
        if raw_text:
            raw_day = RAW_DIR / _today()
            raw_day.mkdir(parents=True, exist_ok=True)
            (raw_day / f"{slug}.md").write_text(raw_text, encoding="utf-8")
            raw_link = f"[[00_Raw/{_today()}/{slug}]]"

        fm_tags = "[" + ", ".join(tags) + "]"
        related_links = ", ".join(f"[[{r}]]" for r in related) if related else "(none)"
        doc = (
            f"---\n"
            f"id: {doc_id}\n"
            f'category: "[[10_Wiki/{rel_cat}]]"\n'
            f"confidence_score: {round(float(confidence), 2)}\n"
            f"tags: {fm_tags}\n"
            f"last_reinforced: {_today()}\n"
            f'github_commit: ""\n'
            f"---\n\n"
            f"# [[{title}]]\n\n"
            f"## 📌 One-line Insight\n> {summary}\n\n"
            f"## 📖 Structured Knowledge\n{content}\n\n"
            f"## ⚠️ Contradictions & Updates\n{contradictions or '(none)'}\n\n"
            f"## 🔗 Knowledge Links\n"
            f"- **Parent:** [[10_Wiki/{rel_cat}]]\n"
            f"- **Related:** {related_links}\n"
            f"- **Raw Source:** {raw_link or '(none)'}\n"
        )
        path.write_text(doc, encoding="utf-8")
        _update_graph(slug, title, rel_cat, related)
        _rebuild_index()

        count = len(list(folder.glob("*.md")))
        suggestion = None
        if count > REFACTOR_THRESHOLD:
            suggestion = (
                f"The '{rel_cat}' folder now has {count} documents. "
                f"Consider splitting it into sub-categories."
            )
        logger.info(f"[kb] saved: 10_Wiki/{rel_cat}/{slug}.md (related={len(related)})")
        return {
            "path": str(path.relative_to(config.ROOT)).replace("\\", "/"),
            "id": doc_id,
            "category": rel_cat,
            "redirected_from": norm_cat if rel_cat != norm_cat else None,
            "suggestion": suggestion,
        }


def list_entries() -> list[str]:
    _ensure_dirs()
    return sorted(m.relative_to(KB_DIR).as_posix() for m in WIKI_DIR.rglob("*.md"))


# ───────────────────────── feedback policy (bandit-style) ─────────────────────────
def _load_policy() -> dict:
    """Read the machine-readable feedback policy. Lock-free (caller manages locking)."""
    try:
        d = json.loads(POLICY_JSON.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("categories", {})   # {rel_cat: {"n": int, "reward": float}}
    d.setdefault("corrections", {})  # {from_rel_cat: {to_rel_cat: count}}
    return d


def _save_policy(pol: dict) -> None:
    META_DIR.mkdir(parents=True, exist_ok=True)
    POLICY_JSON.write_text(json.dumps(pol, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_learned_correction(category: str) -> str:
    """Redirect a category if past feedback repeatedly moved it elsewhere.
    Lock-free — call from within an already-held `_lock` (e.g. save_knowledge)."""
    corr = _load_policy()["corrections"].get(category, {})
    if not corr:
        return category
    target, count = max(corr.items(), key=lambda kv: kv[1])
    if target != category and count >= CORRECTION_MIN:
        logger.info(f"[kb] learned correction: {category} -> {target} (seen {count}x)")
        return target
    return category


def policy_scores() -> dict:
    """Average reward per category (higher = feedback preferred filing there) + learned corrections."""
    pol = _load_policy()
    scores = {c: round(s["reward"] / s["n"], 3)
              for c, s in pol["categories"].items() if s.get("n")}
    return {
        "scores": dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True)),
        "corrections": pol["corrections"],
    }


def record_feedback(note: str, *, category: str | None = None,
                    signal: str | None = None, moved_to: str | None = None) -> None:
    """Record user feedback and let it actually influence future classification.

    - Always appends a human-readable line to Policy.md (transparency).
    - When structured fields are given, updates policy.json (the bandit loop):
        * `signal`   -> reward for `category` (see SIGNAL_REWARD)
        * `moved_to` -> teaches that `category` should be redirected to `moved_to`;
          once seen CORRECTION_MIN times, save_knowledge auto-applies the redirect.
    `category`/`moved_to` are normalized to the same rel-path scheme save_knowledge uses.
    """
    _ensure_dirs()
    if category:
        category = _norm_category(category)
    if moved_to:
        moved_to = _norm_category(moved_to)
    tag = "".join(x for x in (
        f" cat={category}" if category else "",
        f" signal={signal}" if signal else "",
        f" ->{moved_to}" if moved_to else "",
    ))
    with _lock:
        with POLICY_PATH.open("a", encoding="utf-8") as f:
            f.write(f"- {_now_iso()}{tag} {note}\n")
        if category or signal or moved_to:
            pol = _load_policy()
            if category:
                c = pol["categories"].setdefault(category, {"n": 0, "reward": 0.0})
                eff = signal or ("moved" if moved_to else None)
                if eff:
                    c["n"] += 1
                    c["reward"] += SIGNAL_REWARD.get(eff, 0.0)
            if category and moved_to:
                dst = pol["corrections"].setdefault(category, {})
                dst[moved_to] = dst.get(moved_to, 0) + 1
                t = pol["categories"].setdefault(moved_to, {"n": 0, "reward": 0.0})
                t["n"] += 1
                t["reward"] += SIGNAL_REWARD["approved"]
            _save_policy(pol)


def git_sync(message: str) -> str:
    """Commit knowledge/ changes to git (optional). Only runs when KB_GIT_SYNC=true."""
    if not config.KB_GIT_SYNC:
        return "git sync disabled (KB_GIT_SYNC=false)"
    try:
        run = lambda *a: subprocess.run(a, cwd=str(config.ROOT), check=True, capture_output=True)
        run("git", "add", "knowledge")
        run("git", "commit", "-m", f"[kb] {message}")
        if config.KB_GIT_PUSH:
            run("git", "push", "origin", "main")
        logger.info(f"[kb] git sync complete: {message}")
        return "git sync complete"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[kb] git sync failed: {e}")
        return f"git sync failed: {e}"
