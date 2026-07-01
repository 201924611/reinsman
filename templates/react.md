---
name: react
description: ReAct (Reasoning + Acting) iterative pattern — suited to tasks solved step by step using tools
source: "ReAct: Synergizing Reasoning and Acting in Language Models — Yao et al., 2022 (arXiv:2210.03629)"
placeholders: role, task, context
---
You are a {{role}}. Carry out the task below using the ReAct method:
reach the answer by iterating **Thought → Action → Observation**.

Task: {{task}}
Context: {{context}}

At each step:
- **Thought**: Reason about what you need to do right now.
- **Action**: Use a tool or take a concrete action.
- **Observation**: Observe the result and feed it into the next Thought.

Once you have enough information, stop and present the final result with a one-paragraph summary.
