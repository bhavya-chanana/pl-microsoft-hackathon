"""Pick the tactical model: learned weights when present, the baseline otherwise.

``MATCHLENS_EVALUATOR`` forces a choice: ``baseline`` or ``learned``.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from matchlens.tacticsml.contract import TacticalModel

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_tactical_model() -> TacticalModel:
    choice = os.getenv("MATCHLENS_EVALUATOR", "auto").lower()
    if choice in ("auto", "learned"):
        try:
            from matchlens.tacticsml.learned import LearnedModel, weights_available

            if weights_available():
                return LearnedModel.load()
            if choice == "learned":
                log.warning("MATCHLENS_EVALUATOR=learned but no exported weights; using the baseline")
        except ImportError:
            if choice == "learned":
                log.warning("learned tactical model unavailable; using the baseline")
    from matchlens.tacticsml.baseline import BaselineModel

    return BaselineModel()
