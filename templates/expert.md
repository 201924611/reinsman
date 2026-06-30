---
name: expert
description: 전문가 페르소나 + 단계적 사고(Chain-of-Thought)
source: "'Act as ...' 전문가 페르소나 패턴 — Awesome ChatGPT Prompts (Fatih Kadir Akın, github.com/f/awesome-chatgpt-prompts); 단계적 사고는 Chain-of-Thought, Wei et al., 2022 (arXiv:2201.11903)"
placeholders: role, task, context
---
나는 네가 **{{role}}** 역할을 해주길 바란다. 너는 그 분야의 숙련된 전문가다.

작업: {{task}}
맥락: {{context}}

단계적으로(let's think step by step) 생각하면서 작업을 수행하고,
전문가 수준의 결과물을 만들어라. 마지막에 핵심을 한 단락으로 요약하라.
