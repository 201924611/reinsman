"""Self-improvement meta-loop — the agent reads its own evals/traces and proposes
improvements to its own prompt.

Safeguards (important):
- propose(): **never touches** live files. Only saves a proposal to self_improve/<id>/.
- apply(): changes live files **only when explicitly called**. Backs up the current
  version to backups/ before changing.
- revert(): rolls back to the latest backup immediately.
- A/B: compares current vs candidate via overrides.system_prompt with no live changes
  (handled in server).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

from agent_core import config

from agent_core.observability import evaluation


SI_DIR = config.ROOT / "self_improve"
BACKUP_DIR = SI_DIR / "backups"
SI_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def target_path(target: str) -> Path:
    """Path of the file to improve. 'orchestrator' -> agents/, otherwise templates/<target>.md."""
    if target == "orchestrator":
        return config.ROOT / "agents" / "orchestrator.md"
    return config.TEMPLATES_DIR / f"{target}.md"


def _recent_evals(n: int) -> list[dict]:
    files = sorted(evaluation.EVALS_DIR.glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    out = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return out


_REFLECT_PROMPT = """\
You are a prompt engineer improving an autonomous agent's system prompt.
Read the [Current Prompt] and [Recent Execution Eval Summary] below, diagnose the
**recurring weaknesses**, and improve the prompt.

Rules:
- Preserve the frontmatter (between the --- markers). Keep name/model unchanged; only
  refine description if needed.
- Do not delete existing core principles or safeguards. Only add/edit 'concrete and
  actionable' guidance that addresses the weaknesses.
- **Directly target** the criticisms that recur in the eval rationales (e.g. wasteful
  efficiency, early termination, structure-preservation bias, missing verification).
- Do not inflate the length excessively. No vague wording. Keep the original language.

[Target] {target}

[Current Prompt]
{current}

[Recent Execution Eval Summary (eval judge scores + rationale)]
{digest}

Output only in the format below (absolutely no other text):
<<<CHANGELOG>>>
- (3-6 lines on what changed and why; each line states which weakness it targets)
<<<REVISED_PROMPT>>>
(The full improved prompt — including frontmatter; the content between these markers becomes the file verbatim)
<<<END>>>
"""


def _parse(text: str) -> tuple[str, str]:
    cl = re.search(r"<<<CHANGELOG>>>(.*?)<<<REVISED_PROMPT>>>", text, re.DOTALL)
    rp = re.search(r"<<<REVISED_PROMPT>>>(.*?)<<<END>>>", text, re.DOTALL)
    return (cl.group(1).strip() if cl else ""), (rp.group(1).strip() if rp else "")


async def propose(target: str = "orchestrator", n: int = 8, model: str | None = None) -> dict:
    """Analyze recent evals to produce an improvement proposal for the target prompt. Does not touch live files."""
    path = target_path(target)
    if not path.exists():
        return {"error": f"target file not found: {path}"}
    current = path.read_text(encoding="utf-8")

    evals = _recent_evals(n)
    digest_lines = []
    for e in evals:
        j = e.get("judge", {})
        t = e.get("totals", {})
        digest_lines.append(
            f"- \"{(e.get('goal') or '')[:45]}\" overall {j.get('overall')} "
            f"(completion {j.get('completion')}/quality {j.get('quality')}/safety {j.get('safety')}/efficiency {j.get('efficiency')}) "
            f"| turns {t.get('num_turns')} · tools {t.get('tool_calls')} | {(j.get('rationale') or '')[:180]}"
        )
    digest = "\n".join(digest_lines) or "(no eval data — improve using general principles)"

    prompt = _REFLECT_PROMPT.format(target=target, current=current, digest=digest)
    options = ClaudeAgentOptions(
        system_prompt="You are a careful prompt engineer. Output only the specified marker format.",
        model=model or config.AGENT_MODEL,
        permission_mode=config.PERMISSION_MODE,
        cwd=str(config.WORKSPACE_DIR),
        max_turns=2,
        allowed_tools=[],
    )
    chunks: list[str] = []
    try:
        async for m in query(prompt=prompt, options=options):
            if isinstance(m, AssistantMessage):
                for b in m.content:
                    if isinstance(b, TextBlock):
                        chunks.append(b.text)
    except Exception as e:  # noqa: BLE001
        return {"error": f"reflector failed: {e}"}

    changelog, revised = _parse("\n".join(chunks))
    if not revised:
        return {"error": "failed to parse proposal (markers missing)", "raw": "\n".join(chunks)[:600]}

    pid = "si-" + uuid.uuid4().hex[:8]
    d = SI_DIR / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "proposal.md").write_text(revised, encoding="utf-8")
    (d / "analysis.md").write_text(
        f"# Self-Improvement Proposal {pid}\n\n- target: {target}\n- evals analyzed: {len(evals)}\n- created: {_now()}\n\n"
        f"## Change Summary (changelog)\n{changelog}\n", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(
        {"id": pid, "target": target, "created": _now(), "n_evals": len(evals),
         "applied": False, "char_delta": len(revised) - len(current)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": pid, "target": target, "changelog": changelog,
            "char_delta": len(revised) - len(current), "live_file_changed": False}


def get_proposal(pid: str) -> dict | None:
    d = SI_DIR / pid
    if not (d / "proposal.md").exists():
        return None
    return {
        "id": pid,
        "proposal": (d / "proposal.md").read_text(encoding="utf-8"),
        "analysis": (d / "analysis.md").read_text(encoding="utf-8"),
        "meta": json.loads((d / "meta.json").read_text(encoding="utf-8")),
    }


def proposal_text(pid: str) -> str | None:
    p = SI_DIR / pid / "proposal.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def list_proposals() -> list[dict]:
    out = []
    for d in sorted(SI_DIR.glob("si-*")):
        mp = d / "meta.json"
        if mp.exists():
            try:
                out.append(json.loads(mp.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                pass
    return out


def apply(pid: str) -> dict:
    """Apply the candidate to the live file (human approval gate). Auto-backup before applying."""
    d = SI_DIR / pid
    prop = d / "proposal.md"
    if not prop.exists():
        return {"error": "unknown proposal"}
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    target = meta["target"]
    path = target_path(target)
    backup = BACKUP_DIR / f"{target}-{_stamp()}.md"
    if path.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(prop.read_text(encoding="utf-8"), encoding="utf-8")
    meta.update({"applied": True, "applied_at": _now(), "backup": backup.name})
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"applied": pid, "target": target, "backup": backup.name,
            "note": "Takes effect immediately (the prompt is loaded on every run in _make_options; no server restart needed)"}


def revert(target: str = "orchestrator") -> dict:
    """Roll back to the target's latest backup."""
    backups = sorted(BACKUP_DIR.glob(f"{target}-*.md"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        return {"error": "no backup found"}
    latest = backups[0]
    target_path(target).write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
    return {"reverted": target, "from": latest.name}
