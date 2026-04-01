from tools.auto_mine_synonyms import DEFAULT_OUTPUT as AUTO_SYNONYM_DEFAULT_OUTPUT
from tools.auto_mine_synonyms import build_arg_parser as build_auto_synonym_arg_parser
from tools.build_parser_gap_action_plan import (
    DEFAULT_OUTPUT,
    build_arg_parser,
    build_parser_gap_action_plan,
)


def _sample_report():
    return {
        "parser_gaps": {
            "parser_gap_case_count": 7,
            "top_parser_missing_params": [
                {"key": "item_count", "count": 4},
                {"key": "cable_cores", "count": 2},
            ],
            "top_parser_missing_patterns": [
                {
                    "key": "item_count:50",
                    "count": 3,
                    "examples": [
                        {
                            "province": "广东",
                            "bill_name": "紧急呼叫扬声器",
                            "bill_text": "扬声器数量≤50台",
                            "expected_quota_name": "背景音乐系统调试 分区试响 扬声器数量≤50台",
                        }
                    ],
                },
                {
                    "key": "item_count:10",
                    "count": 1,
                    "examples": [
                        {
                            "province": "北京",
                            "bill_name": "声光报警器",
                            "bill_text": "数量≤10台",
                            "expected_quota_name": "报警系统调试 数量≤10台",
                        }
                    ],
                },
                {
                    "key": "cable_cores:5",
                    "count": 2,
                    "examples": [
                        {
                            "province": "江西",
                            "bill_name": "电缆",
                            "bill_text": "WDZN-BYJ 3x4+2x2.5",
                            "expected_quota_name": "电缆敷设 5芯",
                        }
                    ],
                },
            ],
        }
    }


def test_build_parser_gap_action_plan_groups_patterns_by_param():
    plan = build_parser_gap_action_plan(
        _sample_report(),
        top_patterns_per_param=2,
        examples_per_pattern=1,
    )

    assert plan["meta"]["parser_gap_case_count"] == 7
    assert plan["meta"]["param_count"] == 2
    assert plan["next_actions"][0]["param_key"] == "item_count"

    item_count = next(row for row in plan["action_items"] if row["param_key"] == "item_count")
    assert item_count["missing_case_count"] == 4
    assert item_count["parser_touchpoints"][0] == "src/text_parser.py:_extract_item_count"
    assert item_count["top_patterns"][0]["pattern_key"] == "item_count:50"
    assert item_count["top_patterns"][0]["examples"][0]["province"] == "广东"


def test_build_parser_gap_action_plan_falls_back_when_param_count_missing():
    report = {
        "parser_gaps": {
            "parser_gap_case_count": 1,
            "top_parser_missing_params": [],
            "top_parser_missing_patterns": [
                {"key": "switch_gangs:3", "count": 2, "examples": []},
            ],
        }
    }

    plan = build_parser_gap_action_plan(report)
    item = plan["action_items"][0]

    assert item["param_key"] == "switch_gangs"
    assert item["missing_case_count"] == 2
    assert "src/text_parser.py:_extract_switch_gangs" in item["parser_touchpoints"]


def test_build_parser_gap_action_plan_parser_accepts_bare_output_flag():
    args = build_arg_parser().parse_args(["--preview", "--output"])

    assert args.preview is True
    assert args.output == str(DEFAULT_OUTPUT)


def test_auto_synonym_parser_accepts_bare_output_flag():
    args = build_auto_synonym_arg_parser().parse_args(["--preview", "--output"])

    assert args.preview is True
    assert args.output == str(AUTO_SYNONYM_DEFAULT_OUTPUT)
