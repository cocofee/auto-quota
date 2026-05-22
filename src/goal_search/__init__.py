"""Independent goal-mode quota search.

This package is intentionally separate from the production matching pipeline.
It reads existing quota/experience assets but does not mutate them or affect
HybridSearcher decisions.
"""

from .searcher import GoalSearchHit, GoalSearchItem, GoalSearcher

__all__ = ["GoalSearchHit", "GoalSearchItem", "GoalSearcher"]
