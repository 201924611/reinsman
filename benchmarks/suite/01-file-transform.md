---
id: file-transform
axis: data
timeout_minutes: 10
---

Read the CSV file at benchmarks/suite/fixtures/sales.csv (relative to the repo root).
Compute total revenue per region. Write a markdown report to workspace/bench/report.md
containing: a one-line summary, then a table of regions sorted by total revenue
(descending) with columns Region | Total. Round totals to whole numbers.
Do not modify the fixture. Keep it to a single pass — no build loop.
