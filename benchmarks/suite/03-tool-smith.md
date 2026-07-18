---
id: tool-smith
axis: tooling
timeout_minutes: 10
---

Write a Python script at workspace/bench/wordfreq.py that takes a file path argument
and prints the top 5 most frequent words (case-insensitive, punctuation stripped) as
"word: count" lines, most frequent first. Then run it on
benchmarks/suite/fixtures/sample.txt and include the actual output in your final
result message. The script must run under Python 3.10+ with stdlib only.
Single pass — no build loop.
