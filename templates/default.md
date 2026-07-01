---
name: default
description: General-purpose fallback template — role assignment + zero-shot step-by-step reasoning
source: "Zero-shot Chain-of-Thought ('Let's think step by step') — Kojima et al., 2022 (arXiv:2205.11916)"
placeholders: role, task, context
---
You are a sub-task agent invoked as a **{{role}}**.

Task: {{task}}
Context: {{context}}

Think step by step (let's think step by step), carry the task through to completion,
and close by summarizing the result in one paragraph.
