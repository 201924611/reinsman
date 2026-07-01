# Prompt Templates (with source citations)

The templates here **cite well-known, publicly documented prompt engineering techniques**.
When the central agent creates a sub-agent, it fills these templates with role/task/context values,
generates a `runtime_agents/<id>.md` file, and uses that to configure the agent.

| Template | Technique | Source |
|---|---|---|
| `costar.md` | CO-STAR framework | Sheila Teo, winning entry of Singapore's 1st GPT-4 Prompt Engineering Competition (2023) |
| `react.md` | ReAct (Reasoning + Acting) | Yao et al., 2022 — arXiv:2210.03629 |
| `expert.md` | Expert persona + CoT | Awesome ChatGPT Prompts (Fatih Kadir Akın); Wei et al., 2022 — arXiv:2201.11903 |
| `default.md` | Zero-shot CoT (fallback) | Kojima et al., 2022 — arXiv:2205.11916 |

## Adding a new template
Create a `.md` file, add `name`, `description`, `source` (a source citation), and `placeholders` to the frontmatter,
then put the `{{role}}`, `{{task}}`, and `{{context}}` placeholders in the body.
When an agent is created, the `source` is preserved as a citation comment in the runtime md file.
