# Example goals — what to throw at the harness

Copy-paste any of these into the chat UI at `http://127.0.0.1:8848` (or POST them to
`/goal`). Ordered from "30 seconds" to "walk away and come back". Every run leaves a
trace in `traces/` you can replay, and `POST /tasks/{id}/evaluate` scores it.

## Warm-ups (sanity checks, < 1 min)

```text
Create hello.txt in the workspace containing one line: the current date and a haiku about harnesses.
```

```text
List every file in your workspace as a tree and tell me which ones you could delete safely and why. Don't delete anything.
```

## Single-pass work (a few minutes)

```text
Read the CSV at benchmarks/suite/fixtures/sales.csv and write workspace/analysis.md: totals per region and per product, one insight you find non-obvious, and one chart described in ASCII.
```

```text
Write a Python script workspace/tools/dedupe.py that removes duplicate lines from a file while preserving order, with a --dry-run flag. Run it on a test file you create, and show the output.
```

## Verify-loop work (the harness iterates until its judge passes, ~10–20 min)

```text
Build a single-file landing page at workspace/site/index.html for an imaginary product called "Quiet Metrics" — dark theme, hero, three feature cards, responsive at 360px and 1280px. Iterate until it looks right and prove it with screenshots.
```

```text
Build a self-contained HTML mortgage calculator (workspace/calc/index.html): loan amount, rate, term inputs; monthly payment and total interest outputs; no external requests. Use the build loop with at most 3 rounds.
```

## Long-leash work (hand it a mission, come back later)

```text
Research how the top 5 Python CLI tools structure their --help output, write your findings to the knowledge base, then propose (don't apply) a redesigned help screen for this project.
```

```text
Pick the weakest score in benchmarks/ledger.json, diagnose why that run scored low by reading its trace, and write an improvement proposal as a new `goal` issue draft in workspace/proposals/.
```

## What NOT to expect (honest limits)

- It drives **Claude only** (Agent SDK) — bring an API key or a Claude Code CLI login.
- The verify loop's observer is **screenshots** today: web/file artifacts iterate well;
  other artifact types run single-pass until roadmap item 4 lands.
- Goals touching systems outside the workspace are the orchestrator's call to refuse —
  see [SECURITY.md](../SECURITY.md) for the autonomy/permission model.
