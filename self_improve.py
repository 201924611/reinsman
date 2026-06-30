"""자기개선 메타루프 — 에이전트가 자기 eval/trace를 읽고 '자기 프롬프트' 개선안을 제안한다.

안전장치(중요):
- propose(): 라이브 파일을 **절대 건드리지 않는다**. self_improve/<id>/ 에 후보(proposal)만 저장.
- apply(): **명시적 호출 시에만** 라이브 파일을 바꾼다. 바꾸기 전 backups/ 에 현재본을 백업.
- revert(): 최신 백업으로 즉시 롤백.
- A/B: 라이브 변경 없이 overrides.system_prompt 로 현행 vs 후보를 비교(server에서).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

import config
import evaluation

SI_DIR = config.ROOT / "self_improve"
BACKUP_DIR = SI_DIR / "backups"
SI_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def target_path(target: str) -> Path:
    """개선 대상 파일 경로. 'orchestrator'면 agents/, 그 외엔 templates/<target>.md."""
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
너는 자율 에이전트의 시스템 프롬프트를 개선하는 프롬프트 엔지니어다.
아래 [현재 프롬프트]와 [최근 실행 평가 요약]을 보고, **반복되는 약점**을 진단해 프롬프트를 개선하라.

규칙:
- frontmatter(--- 사이)는 보존한다. name/model은 그대로, description만 필요시 다듬어라.
- 기존 핵심 원칙·안전장치는 삭제하지 마라. 약점을 보완하는 '구체적이고 실행가능한' 지침을 추가/수정만.
- 평가 근거에서 반복되는 비판(예: 효율 과소비, 조기종료, 구조 보존 편향, 검증 누락 등)을 **직접 겨냥**하라.
- 분량을 과하게 늘리지 마라. 모호한 표현 금지. 한국어 유지.

[대상] {target}

[현재 프롬프트]
{current}

[최근 실행 평가 요약 (eval judge 점수 + 근거)]
{digest}

아래 형식으로만 출력하라(다른 텍스트 절대 금지):
<<<CHANGELOG>>>
- (무엇을 왜 바꿨는지 3~6줄, 각 줄은 어떤 약점을 겨냥했는지 명시)
<<<REVISED_PROMPT>>>
(개선된 프롬프트 전문 — frontmatter 포함, 이 마커 사이 내용이 그대로 파일이 된다)
<<<END>>>
"""


def _parse(text: str) -> tuple[str, str]:
    cl = re.search(r"<<<CHANGELOG>>>(.*?)<<<REVISED_PROMPT>>>", text, re.DOTALL)
    rp = re.search(r"<<<REVISED_PROMPT>>>(.*?)<<<END>>>", text, re.DOTALL)
    return (cl.group(1).strip() if cl else ""), (rp.group(1).strip() if rp else "")


async def propose(target: str = "orchestrator", n: int = 8, model: str | None = None) -> dict:
    """최근 eval을 분석해 대상 프롬프트의 개선안을 만든다. 라이브 파일은 안 건드린다."""
    path = target_path(target)
    if not path.exists():
        return {"error": f"대상 파일 없음: {path}"}
    current = path.read_text(encoding="utf-8")

    evals = _recent_evals(n)
    digest_lines = []
    for e in evals:
        j = e.get("judge", {})
        t = e.get("totals", {})
        digest_lines.append(
            f"- 『{(e.get('goal') or '')[:45]}』 종합 {j.get('overall')} "
            f"(완료{j.get('completion')}/품질{j.get('quality')}/안전{j.get('safety')}/효율{j.get('efficiency')}) "
            f"| 턴{t.get('num_turns')}·도구{t.get('tool_calls')} | {(j.get('rationale') or '')[:180]}"
        )
    digest = "\n".join(digest_lines) or "(eval 데이터 없음 — 일반 원칙으로 개선)"

    prompt = _REFLECT_PROMPT.format(target=target, current=current, digest=digest)
    options = ClaudeAgentOptions(
        system_prompt="너는 신중한 프롬프트 엔지니어다. 지정된 마커 형식만 출력한다.",
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
        return {"error": f"reflector 실패: {e}"}

    changelog, revised = _parse("\n".join(chunks))
    if not revised:
        return {"error": "개선안 파싱 실패(마커 없음)", "raw": "\n".join(chunks)[:600]}

    pid = "si-" + uuid.uuid4().hex[:8]
    d = SI_DIR / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "proposal.md").write_text(revised, encoding="utf-8")
    (d / "analysis.md").write_text(
        f"# 자기개선 제안 {pid}\n\n- target: {target}\n- 기반 eval 수: {len(evals)}\n- 생성: {_now()}\n\n"
        f"## 변경 요약(changelog)\n{changelog}\n", encoding="utf-8")
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
    """후보를 라이브 파일에 적용한다(사람 승인 게이트). 적용 전 자동 백업."""
    d = SI_DIR / pid
    prop = d / "proposal.md"
    if not prop.exists():
        return {"error": "없는 제안"}
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
            "note": "서버 재시작 후 적용됨(프롬프트는 _make_options에서 매 실행 로드되므로 즉시 반영)"}


def revert(target: str = "orchestrator") -> dict:
    """대상의 최신 백업으로 롤백."""
    backups = sorted(BACKUP_DIR.glob(f"{target}-*.md"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        return {"error": "백업 없음"}
    latest = backups[0]
    target_path(target).write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
    return {"reverted": target, "from": latest.name}
