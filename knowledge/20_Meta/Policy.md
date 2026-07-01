# Classification Policy (feedback-driven reward log)

<!-- record_feedback() appends one line at a time. -->
<!-- Each line logs a user feedback signal (keep / move / edit / praise) that acts as a
     reward, used to adjust future classification. Conceptually this is a contextual-bandit
     feedback log (cf. Sutton & Barto, "Reinforcement Learning: An Introduction") — it is a
     lightweight heuristic over logged feedback, not a trained model. -->

## Reward weights (conceptual)
- Categorization Accuracy
- Graph Connectivity (≥ 2 relevant links per document recommended)
- User Satisfaction (feedback)

## How the loop actually runs (code)
This file is the human-readable log. The **machine state** lives in `policy.json`
(`{categories: {cat: {n, reward}}, corrections: {from: {to: count}}}`), updated by
`record_feedback(note, category=, signal=, moved_to=)`:
- `signal` (approved/praised/kept/edited/rejected/moved) adds a reward to that category.
- `moved_to` teaches a correction; once a category is corrected ≥ 2 times to the same target,
  `save_knowledge` **auto-redirects** it there (`_apply_learned_correction`).
Inspect it via `GET /knowledge/policy`.
