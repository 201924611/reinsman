# Self‑tooling — an agent that builds the tools it's missing

Most agents are limited to the tools you hand them. reinsman can **notice it's
missing a tool, write that tool itself, verify it, register it, and reuse it in
later tasks** — its capability compounds over time, the way its knowledge store
already compounds knowledge.

Runs fully autonomously after a one‑time arm; there is **no per‑tool human
approval**. Instead, automated gates keep it from harming itself.

## The loop

```
① gap        the agent hits something its current tools can't do
② request    it calls request_tool(name, purpose, signature)
③ forge      a "tool‑smith" sub‑agent writes a single‑file @tool + a _selftest()
④ gate       automated checks must all pass (no human):
               • static safety scan  — blocks delete / process / network /
                 secret‑access / eval categories
               • self‑test           — the tool's own _selftest() must return True,
                 run in a separate process with a timeout
               • load check          — it must actually load as an MCP tool
⑤ register   on pass → tools/generated/<name>.py (persisted)
⑥ reuse      loaded into every later spawn/goal — even across sessions
```

Anything that fails a gate is **auto‑discarded** and logged; nothing partial is
kept.

## Safety (what replaces human approval)

- **Static scan** rejects tools that touch dangerous categories (file deletion,
  subprocess, network egress, credentials/secrets, `eval`/`exec`, OS‑level access).
  Generated tools are pure functions by default: input → compute → text out.
- **Self‑test in isolation** — a bad or hanging tool can't get in (separate
  process + timeout).
- **Kill switch** — `state/STOP` disables all self‑tooling instantly.
- **One‑time arm** — off by default; `state/self_tooling.json { "armed": true }`
  turns it on. Flip it off and it stops.
- **Audit log** — every attempt (registered / blocked / failed) is recorded with
  its reason in `state/self_tooling.json`.

## Why it matters

The reason we hand‑built features like `run_isolated` is that the agent couldn't
build them itself. Self‑tooling closes that gap for the class of tools that are
safe to synthesize — the agent extends its own toolbox instead of waiting on a
human, and every tool it makes is there for the next task.

> Scope note: this intentionally covers **pure, side‑effect‑free** tools only.
> Anything touching the filesystem destructively, the network, processes, or
> secrets stays a human job by design.
