# PROVENANCE — This launch was run by the agent, not written about it

> **TL;DR:** The promotional campaign for agent-core was itself submitted to agent-core as a single goal:
> *"Plan a never-before-seen way to promote this project and build every publishable asset."*
> The agent researched, self-critiqued, wrote these files, and left a machine-verifiable trail.
> **Don't trust it — verify it, then reproduce it.**

agent-core is a self-hosted harness: you POST one `goal` over HTTP and a 24/7 orchestrator drives it to
done, spawning sub-agents and even **writing its own tools** when it lacks one. Most launch posts are a
*human writing about* a tool. This one is different: the tool **ran its own launch as a task**, and the
artifacts below are the execution log — not marketing copy you have to take on faith.

---

## What you can verify (no trust required)

### 1. The work products
Every asset for this launch lives in ``:
- `LEDGER.md` — the resumable state ledger the agent updated after every step (survives session/turn limits).
- `research.md` — the "already-done promo methods" baseline (novelty gate input).
- `candidates.md` — candidate methods scored ★1–3, each self-rebutted by a skeptic pass (with web checks).
- `plan.md` — the adopted plan (channels, schedule, success metrics).
- `channels/` — the final, publish-ready copy per platform.
- `PUBLIC_LEDGER.md` — the build-in-public log, authored by the agent.

### 2. The machine trail
agent-core records every run to `traces/<task>.json` (token usage, tool chain, sub-agent session groups)
and can auto-score it to `evals/<task>.json` (LLM-judge on completion/quality/safety/efficiency + rule metrics).
A sanitized snapshot of a real run is in `docs/evidence/` (personal data scrubbed). Changelog bots can't
produce this — they summarize commits; they don't execute open-ended goals end-to-end.

### 3. Reproduce it yourself (one command)
Run agent-core locally (subscription login = no extra cost) and hand it the same goal:

```powershell
# Windows PowerShell
./assets/reproduce.ps1
```
```bash
# macOS/Linux
bash ./assets/reproduce.sh
```

The script POSTs the launch goal to your local server and polls the result. You get your own trace, your
own eval score, and your own asset folder — same mechanism, your machine.

---

## Why this is hard to copy
Reproducible, agent-authored provenance requires an **autonomous goal-executor with built-in trace/eval**,
not a prompt or a content bot. To imitate the *angle*, a competitor needs the same three verified
capabilities agent-core already has:

1. **Self-tooling** — if a needed tool is missing, the agent writes it in code and it must pass a
   3-stage auto-gate (static danger scan → self-test → live MCP load) plus kill-switch/arm/audit before
   it's registered and reused. *Live evidence: an `int_to_roman` tool was auto-generated, passed the gate,
   and a sub-agent called it to return 2026 → MMXXVI — zero human code.*
2. **Context isolation** (`run_isolated`) — each step runs in a fresh sub-agent so per-turn context stays
   flat. *Measured on an identical task: cost $3.74 → $1.42 (−62%), per-turn context held ~30k instead of
   climbing, with no ceiling on task length.* (numbers from trace/eval instrumentation.)
3. **24/7 goal orchestration + Obsidian-graph memory + built-in observability** — one goal driven to done,
   auto-resume on turn exhaustion, wiki-linked knowledge graph, and every run traced + auto-scored.

## Honest limits
Channel breadth (multi-channel messaging frontends) and MCP-ecosystem size trail mature harnesses. The edge
here is not breadth — it's **depth of autonomy**, and the fact that this very page is its receipt.

---
_Repo: https://github.com/201924611/auto_-orchestration • License: MIT_
