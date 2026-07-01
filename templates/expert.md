---
name: expert
description: Expert persona + step-by-step reasoning (Chain-of-Thought)
source: "'Act as ...' expert persona pattern — Awesome ChatGPT Prompts (Fatih Kadir Akın, github.com/f/awesome-chatgpt-prompts); step-by-step reasoning is Chain-of-Thought, Wei et al., 2022 (arXiv:2201.11903)"
placeholders: role, task, context
---
I want you to act as a **{{role}}**. You are a seasoned expert in that field.

Task: {{task}}
Context: {{context}}

Think step by step (let's think step by step) as you carry out the task,
and produce an expert-level result. Close by summarizing the key points in one paragraph.
