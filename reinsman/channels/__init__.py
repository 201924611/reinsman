"""Channel (messenger) adapter layer.

The 'Channels' pillar of the harness architecture. Holds thin bridges that connect an external
conversation front-end (Telegram, etc.) to the reinsman HTTP API (goal/tasks).
Kept separate from the engine (server/orchestrator), so the front-end is swappable.
"""
