# 프롬프트 템플릿 (출처 인용)

여기 있는 템플릿들은 **공개된 유명 프롬프트 엔지니어링 기법을 인용**한 것이다.
중앙 에이전트가 하위 에이전트를 만들 때, 이 템플릿에 역할/작업/맥락 값을 채워
`runtime_agents/<id>.md` 파일을 생성하고 그것으로 에이전트를 구성한다.

| 템플릿 | 기법 | 출처 |
|---|---|---|
| `costar.md` | CO-STAR 프레임워크 | Sheila Teo, 싱가포르 제1회 GPT-4 프롬프트 엔지니어링 대회 우승작 (2023) |
| `react.md` | ReAct (추론+행동) | Yao et al., 2022 — arXiv:2210.03629 |
| `expert.md` | 전문가 페르소나 + CoT | Awesome ChatGPT Prompts (Fatih Kadir Akın); Wei et al., 2022 — arXiv:2201.11903 |
| `default.md` | Zero-shot CoT (폴백) | Kojima et al., 2022 — arXiv:2205.11916 |

## 새 템플릿 추가
`.md` 파일을 만들고 frontmatter에 `name`, `description`, `source`(출처 인용), `placeholders`를 적은 뒤
본문에 `{{role}}`, `{{task}}`, `{{context}}` 플레이스홀더를 넣으면 된다.
생성 시 출처(`source`)는 런타임 md 파일에 주석으로 인용되어 남는다.
