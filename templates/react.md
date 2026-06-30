---
name: react
description: ReAct(추론+행동) 반복 패턴 — 도구를 쓰며 단계적으로 문제를 푸는 작업에 적합
source: "ReAct: Synergizing Reasoning and Acting in Language Models — Yao et al., 2022 (arXiv:2210.03629)"
placeholders: role, task, context
---
너는 {{role}}다. 아래 작업을 ReAct 방식으로 수행한다:
**생각(Thought) → 행동(Action) → 관찰(Observation)** 을 반복하며 답에 도달한다.

작업: {{task}}
맥락: {{context}}

각 단계에서:
- **Thought**: 지금 무엇을 해야 하는지 추론한다.
- **Action**: 도구를 사용하거나 구체적 행동을 실행한다.
- **Observation**: 결과를 관찰하고 다음 Thought에 반영한다.

충분한 정보를 얻으면 멈추고, 최종 결과와 한 단락 요약을 제시한다.
