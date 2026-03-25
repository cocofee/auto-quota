from pathlib import Path

import src.method_cards as method_cards_mod
import src.rule_knowledge as rule_knowledge_mod


def test_rule_knowledge_soft_disable_filters_search_and_allows_reactivation(tmp_path, monkeypatch):
    monkeypatch.setattr(rule_knowledge_mod.config, "COMMON_DB_DIR", tmp_path / "common")
    monkeypatch.setattr(rule_knowledge_mod.config, "DB_DIR", tmp_path / "db")
    monkeypatch.setattr(rule_knowledge_mod.RuleKnowledge, "_update_vector_index", lambda self: None)
    monkeypatch.setattr(rule_knowledge_mod.RuleKnowledge, "_remove_from_vector_index", lambda self, rule_id: None)

    kb = rule_knowledge_mod.RuleKnowledge(province="Beijing2024")
    write_result = kb.add_rule_text(
        content="给水管 DN25 镀锌钢管安装按对应定额执行",
        province="Beijing2024",
        specialty="C10",
        chapter="给排水",
    )
    rule_id = int(write_result["rule_id"])
    kb._vector_disabled = True

    before = kb.search_rules("给水管 DN25 镀锌钢管", top_k=5, province="Beijing2024")
    assert any(str(item.get("id")).replace("rule_", "") == str(rule_id) for item in before)

    assert kb.soft_disable_rule(rule_id, reason="obsolete", actor="admin") is True

    after_disable = kb.search_rules("给水管 DN25 镀锌钢管", top_k=5, province="Beijing2024")
    assert all(str(item.get("id")).replace("rule_", "") != str(rule_id) for item in after_disable)

    reactivate_result = kb.add_rule_text(
        content="给水管 DN25 镀锌钢管安装按对应定额执行",
        province="Beijing2024",
        specialty="C10",
        chapter="给排水",
    )
    assert reactivate_result["rule_id"] == rule_id
    assert reactivate_result["reactivated"] is True

    after_reactivate = kb.search_rules("给水管 DN25 镀锌钢管", top_k=5, province="Beijing2024")
    assert any(str(item.get("id")).replace("rule_", "") == str(rule_id) for item in after_reactivate)


def test_method_cards_soft_disable_filters_search_and_allows_reactivation(tmp_path, monkeypatch):
    db_path = tmp_path / "method_cards.db"
    monkeypatch.setattr(method_cards_mod, "get_method_cards_db_path", lambda: Path(db_path))

    mc = method_cards_mod.MethodCards()
    write_result = mc.add_method_text(
        category="给水管审核方法",
        specialty="C10",
        method_text="先核对给水管关键词，再校验定额口径。",
        keywords=["给水管", "定额"],
        pattern_keys=["给水管_审核"],
        source_province="Beijing2024",
    )
    card_id = int(write_result["card_id"])

    before = mc.find_relevant(
        bill_name="给水管安装",
        bill_desc="定额审核 DN25",
        specialty="C10",
        province="Beijing2024",
        top_k=5,
    )
    assert any(int(item.get("id")) == card_id for item in before)

    assert mc.soft_disable_card(card_id, reason="outdated", actor="admin") is True

    after_disable = mc.find_relevant(
        bill_name="给水管安装",
        bill_desc="定额审核 DN25",
        specialty="C10",
        province="Beijing2024",
        top_k=5,
    )
    assert all(int(item.get("id")) != card_id for item in after_disable)

    reactivate_result = mc.add_method_text(
        category="给水管审核方法",
        specialty="C10",
        method_text="先核对给水管关键词，再校验定额口径。",
        keywords=["给水管", "定额"],
        pattern_keys=["给水管_审核"],
        source_province="Beijing2024",
    )
    assert reactivate_result["card_id"] == card_id
    assert reactivate_result["reactivated"] is True

    after_reactivate = mc.find_relevant(
        bill_name="给水管安装",
        bill_desc="定额审核 DN25",
        specialty="C10",
        province="Beijing2024",
        top_k=5,
    )
    assert any(int(item.get("id")) == card_id for item in after_reactivate)
