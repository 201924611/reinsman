# 🧾 The Self-Run Launch

**agent-core's own launch was run *by* agent-core** — the promotion was submitted to the tool as a single
goal and executed autonomously (research → self-critique → asset build), leaving a machine-verifiable trail.
Most launch posts are *a human writing about* a tool; this one is the tool **running its own launch as a
task**. Don't trust the claims — **verify them, then reproduce them.**

## Read the trail
| File | What it is |
|---|---|
| [`PROVENANCE.md`](PROVENANCE.md) | The story + exactly what you can verify (no trust required) |
| [`PUBLIC_LEDGER.md`](PUBLIC_LEDGER.md) | The build log the agent wrote, checkpoint by checkpoint |
| [`candidates.md`](candidates.md) | Every promo idea scored ★1–3, each self-rebutted by a skeptic pass (with web checks) |
| [`research.md`](research.md) | The "already-done promo methods" baseline (novelty-gate input) |
| [`plan.md`](plan.md) | The adopted plan (channels, schedule, success metrics) |
| [`VERIFICATION.md`](VERIFICATION.md) | Self-verification: every factual claim ↔ the real repo |
| [`reproduce.ps1`](reproduce.ps1) / [`reproduce.sh`](reproduce.sh) | One command: re-run this launch and inspect *your own* trace |

## Reproduce it (don't take our word)
```bash
python -m agent_core          # start the server (port 8848)
# then, in another shell:
bash docs/self-run-launch/reproduce.sh      # or: pwsh docs/self-run-launch/reproduce.ps1
```
It POSTs the same launch goal, polls to done, and points you at the run's `trace`/`eval` — the tool chain,
sub-agents, tokens, and LLM-judge score for the run that produced these files.

> Note: the original run executed in the maintainer's private dev workspace; the artifacts are published
> here as-is. Star/user counts are deliberately **not** claimed — only measured facts (from trace/eval) are.
