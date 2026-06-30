"""영속 지식 저장소 (LLM-Wiki 스타일).

서브에이전트가 수집/합성한 데이터를 구조화된 마크다운 위키로 영구 보관한다.
runtime_agents/(임시·삭제)와 달리 이 저장소는 git으로 추적되어 유지된다.

원형: 사용자 제공 'knowledge-policy' 템플릿을 agent-core에 맞게 단순·견고화한 것.
(영감: Andrej Karpathy의 LLM-Wiki/'외부 뇌' 개념 + 강화학습식 보상 정책 아이디어.
 여기서 'RL'은 학습된 모델이 아니라 피드백 로그 기반 경량 휴리스틱이다.)
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
from applog import get_logger

logger = get_logger()

KB_DIR = config.ROOT / "knowledge"
RAW_DIR = KB_DIR / "00_Raw"
WIKI_DIR = KB_DIR / "10_Wiki"
META_DIR = KB_DIR / "20_Meta"
GRAPH_PATH = META_DIR / "Graph.json"
INDEX_PATH = META_DIR / "Index.md"
POLICY_PATH = META_DIR / "Policy.md"

STD_CATEGORIES = ["Projects", "Topics", "Decisions", "Skills"]
REFACTOR_THRESHOLD = 12  # 한 폴더 문서 수가 이를 넘으면 세분화 제안

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


# 도메인 실행 노하우(how-to)는 Skills/ 아래로 강제하는 결정적 가드용 키워드.
# 하위폴더명이 여기 들어가면 상위가 Skills가 아니어도 Skills로 교정한다.
DOMAIN_SKILL_SUBS = {
    "웹디자인", "웹프론트엔드", "웹백엔드", "웹아키텍처", "웹",
    "db", "디비", "데이터베이스", "데이터", "모바일", "디자인시스템",
    "프론트엔드", "백엔드", "아키텍처", "인프라", "데브옵스", "보안",
}


def _find_subfolder(name: str) -> str | None:
    """10_Wiki 안에 주어진 이름의 하위 폴더가 이미 있으면 그 상대경로를 반환(없으면 None)."""
    if not WIKI_DIR.exists():
        return None
    for p in sorted(WIKI_DIR.rglob(name)):
        if p.is_dir():
            return p.relative_to(WIKI_DIR).as_posix()
    return None


def _norm_category(category: str) -> str:
    """카테고리를 10_Wiki 하위 상대경로로 정규화 + 결정적 가드(LLM 오분류 교정).
    가드는 LLM 재량이 아니라 코드가 결정한다:
      1) 도메인 how-to 키워드(DOMAIN_SKILL_SUBS)면 상위를 Skills/로 강제.
      2) 같은 이름의 하위폴더가 이미 다른 위치에 있으면 그 기존 위치로 합친다
         (Topics/웹프론트엔드 vs Skills/웹프론트엔드 분열 방지)."""
    c = (category or "Topics").strip().strip("/").replace("\\", "/")
    c = re.sub(r"^10_Wiki/", "", c)
    c = c or "Topics"
    parts = [p for p in c.split("/") if p]
    if len(parts) >= 2:
        top, sub, tail = parts[0], parts[1], parts[2:]
        # 가드1: 도메인 실행 노하우 → Skills/ 강제
        if sub.lower() in {s.lower() for s in DOMAIN_SKILL_SUBS} and top != "Skills":
            fixed = "/".join(["Skills", sub, *tail])
            logger.info(f"[kb] category 정규화(도메인→Skills): {c} → {fixed}")
            return fixed
        # 가드2: 같은 하위폴더가 이미 어딘가 있으면 그쪽으로 합침
        existing = _find_subfolder(sub)
        if existing and existing != "/".join(parts[:2]):
            fixed = "/".join([existing, *tail])
            logger.info(f"[kb] category 정규화(기존 폴더 우선): {c} → {fixed}")
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
    lines = ["# 📇 Knowledge Index", "", f"_자동 생성: {_now_iso()}_", ""]
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
        lines.append("_아직 저장된 지식이 없습니다._")
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
    """지식 한 건을 위키 규격으로 저장하고 Index/Graph를 갱신한다."""
    _ensure_dirs()
    with _lock:
        rel_cat = _norm_category(category)
        folder = WIKI_DIR / rel_cat
        folder.mkdir(parents=True, exist_ok=True)
        slug = _slug(title)
        path = folder / f"{slug}.md"
        doc_id = uuid.uuid4().hex
        tags = tags or []
        related = related or []

        # 원본 보관 (Source of Truth)
        raw_link = raw_source
        if raw_text:
            raw_day = RAW_DIR / _today()
            raw_day.mkdir(parents=True, exist_ok=True)
            (raw_day / f"{slug}.md").write_text(raw_text, encoding="utf-8")
            raw_link = f"[[00_Raw/{_today()}/{slug}]]"

        fm_tags = "[" + ", ".join(tags) + "]"
        related_links = ", ".join(f"[[{r}]]" for r in related) if related else "(없음)"
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
            f"## 📌 한 줄 통찰\n> {summary}\n\n"
            f"## 📖 구조화된 지식\n{content}\n\n"
            f"## ⚠️ 모순 및 업데이트\n{contradictions or '(없음)'}\n\n"
            f"## 🔗 지식 연결\n"
            f"- **Parent:** [[10_Wiki/{rel_cat}]]\n"
            f"- **Related:** {related_links}\n"
            f"- **Raw Source:** {raw_link or '(없음)'}\n"
        )
        path.write_text(doc, encoding="utf-8")
        _update_graph(slug, title, rel_cat, related)
        _rebuild_index()

        count = len(list(folder.glob("*.md")))
        suggestion = None
        if count > REFACTOR_THRESHOLD:
            suggestion = (
                f"'{rel_cat}' 폴더 문서가 {count}개입니다. 하위 카테고리 세분화를 권장합니다."
            )
        logger.info(f"[kb] 저장: 10_Wiki/{rel_cat}/{slug}.md (related={len(related)})")
        return {
            "path": str(path.relative_to(config.ROOT)).replace("\\", "/"),
            "id": doc_id,
            "category": rel_cat,
            "suggestion": suggestion,
        }


def list_entries() -> list[str]:
    _ensure_dirs()
    return sorted(m.relative_to(KB_DIR).as_posix() for m in WIKI_DIR.rglob("*.md"))


def record_feedback(note: str) -> None:
    """사용자 피드백을 Policy.md에 누적한다 (다음 분류 시 참고)."""
    _ensure_dirs()
    with _lock:
        with POLICY_PATH.open("a", encoding="utf-8") as f:
            f.write(f"- {_now_iso()} {note}\n")


def git_sync(message: str) -> str:
    """knowledge/ 변경을 git에 커밋(옵션). KB_GIT_SYNC=true 일 때만 동작."""
    if not config.KB_GIT_SYNC:
        return "git sync 비활성화 (KB_GIT_SYNC=false)"
    try:
        run = lambda *a: subprocess.run(a, cwd=str(config.ROOT), check=True, capture_output=True)
        run("git", "add", "knowledge")
        run("git", "commit", "-m", f"[knowledge-policy] {message}")
        if config.KB_GIT_PUSH:
            run("git", "push", "origin", "main")
        logger.info(f"[kb] git sync 완료: {message}")
        return "git sync 완료"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[kb] git sync 실패: {e}")
        return f"git sync 실패: {e}"
