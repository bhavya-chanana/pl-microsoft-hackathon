"""Match a coach's free-text brief to the built-in recipes' mechanisms, for when no model is reachable.

Offline the board cannot write new ideas from a brief, but it can still honour it: recipes whose mechanism the
brief names come first and are always kept. Words are matched whole, case-insensitively.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from matchlens.tactics.intent import TacticIntent

BRIEF_WORDS: dict[str, tuple[str, ...]] = {
    "press_trap": ("press", "pressing", "gegenpress", "counter-press", "counterpress", "squeeze", "high line",
                   "win the ball", "trap"),
    "low_block": ("protect", "park", "low block", "back five", "back four", "defend deep", "see out", "hold on",
                  "sit back", "compact", "hold the lead"),
    "counter": ("counter", "counter-attack", "on the break", "transition"),
    "overload": ("overload", "overlap", "wide", "width", "wing", "wings", "cross", "flank", "fullback"),
    "isolation": ("isolate", "isolation", "1v1", "one on one", "dribble"),
    "third_man": ("third man", "third-man", "combination", "one-two", "link up"),
    "switch": ("switch", "weak side", "far side", "diagonal", "change the point"),
    "run_in_behind": ("behind", "through ball", "runner", "stretch", "long ball", "direct"),
    "cutback": ("cutback", "cut back", "byline", "pull back"),
    "set_piece": ("corner", "corners", "set piece", "set-piece", "free kick", "near post", "far post"),
}


def match_brief(prompt: str | None) -> list[str]:
    """Mechanisms the brief names, most mentioned first (ties in table order). Empty for no brief or no match."""
    text = (prompt or "").lower()
    hits = {m: sum(len(re.findall(rf"(?<![\w-]){re.escape(w)}(?![\w-])", text)) for w in words)
            for m, words in BRIEF_WORDS.items()}
    return sorted((m for m, n in hits.items() if n), key=lambda m: -hits[m])


def steer(recipes: Sequence[TacticIntent], matched: Sequence[str]) -> list[TacticIntent]:
    """Recipes whose mechanism the brief named first (in the brief's order), the rest as they were."""
    order = {m: i for i, m in enumerate(matched)}
    return sorted(recipes, key=lambda r: order.get(r.mechanism.value, len(order)))
