# -*- coding: utf-8 -*-
"""Compatibility facade for the split match pipeline package."""

import threading

import config

from src.adaptive_strategy import AdaptiveStrategy
from src.candidate_arbiter import arbitrate_candidates
from src.ltr_ranker import rerank_candidates_with_ltr
from src.match_core import try_experience_exact_match, try_experience_match
from src.param_validator import ParamValidator
from src.province_plugins import resolve_plugin_hints
from src.review_checkers import (
    check_category_mismatch,
    check_connection_mismatch,
    check_electric_pair,
    check_elevator_floor,
    check_elevator_type,
    check_material_mismatch,
    check_parameter_deviation,
    check_pipe_usage,
    check_sleeve_mismatch,
    extract_description_lines,
)
from src.specialty_classifier import classify as classify_specialty
from src.unified_planner import build_unified_search_plan

from . import classifiers as _classifiers
from . import gates as _gates
from . import orchestrator as _orchestrator
from . import pickers as _pickers
from . import reasons as _reasons
from . import reconcilers as _reconcilers
from . import scope as _scope

_ADAPTIVE_STRATEGY = AdaptiveStrategy()
_RULE_INJECTION_VALIDATOR = None
_RULE_INJECTION_VALIDATOR_LOCK = threading.Lock()
_PRICE_VALIDATOR = None
_PRICE_VALIDATOR_LOCK = threading.Lock()
_PRICE_VALIDATOR_LAST_FAILURE_AT = None
_PRICE_VALIDATOR_RETRY_INTERVAL_SECONDS = 30.0

for _module in (_gates, _pickers, _classifiers, _reasons, _reconcilers, _scope, _orchestrator):
    for _name in dir(_module):
        if _name.startswith('__'):
            continue
        if _name.startswith('_') or _name in {'DEFAULT_ALTERNATIVE_COUNT'}:
            globals()[_name] = getattr(_module, _name)

__all__ = [
    name
    for name in globals()
    if not name.startswith('__')
]
