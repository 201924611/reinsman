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
