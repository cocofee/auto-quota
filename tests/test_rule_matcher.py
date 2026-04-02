from src.rule_matcher import apply_rule_constraints, try_rule_match


def test_apply_rule_constraints_filters_disabled_and_dedupes():
    rules = apply_rule_constraints(
        [
            {"name": "first", "builder": lambda _: "A"},
            {"name": "first", "builder": lambda _: "B"},
            {"name": "second", "builder": lambda _: "C", "enabled": False},
            {"name": "third", "builder": lambda _: "D"},
        ],
        {"third": False},
    )

    assert [rule["name"] for rule in rules] == ["first"]


def test_try_rule_match_returns_first_successful_rule():
    match = try_rule_match(
        {"value": "hit"},
        [
            {"name": "empty", "builder": lambda item: ""},
            {"name": "first", "builder": lambda item: item["value"], "apply_synonyms": False},
            {"name": "later", "builder": lambda item: "later"},
        ],
    )

    assert match == {
        "name": "first",
        "query": "hit",
        "apply_synonyms": False,
    }
