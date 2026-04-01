# -*- coding: utf-8 -*-
"""Shared helpers for explicit family picker scoring."""

from __future__ import annotations


def pick_best_candidate(scored: list[tuple[tuple[int, float, float], dict]]) -> dict | None:
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def score_candidate(candidate: dict, score: int) -> tuple[tuple[int, float, float], dict]:
    return (
        (
            score,
            float(candidate.get("param_score", 0.0)),
            float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0))),
        ),
        candidate,
    )
