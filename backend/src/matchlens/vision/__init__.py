"""Video auto-eventing: from broadcast-style footage to Matchlens events and tracking.

Players are detected and tracked in clip pixels, projected onto the pitch,
split into teams by kit colour, and turned into passes, carries and turnovers
from who is on the ball. The output is ordinary :class:`Event` and
:class:`TrackingFrame` objects, so a video run plays through the same stats
engine and agents as any other feed.

Local only: needs ``uv sync --group vision`` and footage under the gitignored
``backend/data/local/``. The demo image never ships it.
"""
