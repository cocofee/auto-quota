from src.adaptive_strategy import AdaptiveStrategy
from src.match_engine import _annotate_adaptive_strategies


def test_annotate_adaptive_strategies_assigns_item_strategy():
    items = [
        {
            "name": "镀锌钢管",
            "specialty": "C10",
            "params": {
                "dn": "100",
                "material": "镀锌钢",
            },
        },
        {
            "name": "消火栓钢管（含管件）或沟槽连接钢管【镀锌】包括支架及附件做法、安装高度、系统说明、接口要求、保温防腐与调试内容",
            "specialty": "C10",
            "params": {
                "dn": "100",
                "material": "镀锌钢",
                "connection": "沟槽",
                "pressure": "1.6MPa",
                "coating": "热浸锌",
                "usage": "消防",
            },
        },
    ]

    counts = _annotate_adaptive_strategies(
        items,
        selector=AdaptiveStrategy(historical_hit_rates={"C10": 0.92}),
    )

    assert items[0]["adaptive_strategy"] == "fast"
    assert items[1]["adaptive_strategy"] == "deep"
    assert counts["fast"] == 1
    assert counts["deep"] == 1
