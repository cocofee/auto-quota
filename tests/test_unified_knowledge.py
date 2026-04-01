from src.unified_knowledge import UnifiedKnowledgeRetriever


class _StubExperienceDB:
    def search_experience(self, *args, **kwargs):
        return [
            {
                "id": 11,
                "bill_text": "镀锌钢管安装 DN25 丝接",
                "quota_ids": ["C10-1-1"],
                "quota_names": ["镀锌钢管安装"],
                "confidence": 96,
                "specialty": "C10",
                "gate": "green",
                "layer": "authority",
            }
        ]


class _StubRuleKB:
    def search_rules(self, query, top_k=3, province=None):
        return [
            {
                "id": "rule_1",
                "province": province or "GD",
                "specialty": "C10",
                "chapter": "管道安装说明",
                "section": "",
                "content": "镀锌钢管安装包含切管、套丝等工作内容。",
            },
            {
                "id": "rule_2",
                "province": province or "GD",
                "specialty": "C10",
                "chapter": "管道安装",
                "section": "工程量计算规则",
                "content": "管道安装工程量按设计图示中心线长度计算，丝接管件另计。",
            },
        ]


class _StubMethodCards:
    def find_relevant(self, bill_name, bill_desc="", specialty=None, province=None, top_k=2):
        return [
            {
                "id": 7,
                "category": "给排水管道安装",
                "specialty": specialty or "C10",
                "_scope": "local",
                "source_province": province or "GD",
                "method_text": "先判材质，再判连接方式，最后按DN向上取档。",
                "universal_method": "同类管道优先按材质+连接方式切分候选。",
                "common_errors": "把PPR和镀锌钢管混用。",
            }
        ]


def test_unified_knowledge_splits_rules_and_explanations():
    retriever = UnifiedKnowledgeRetriever(
        province="广东2024",
        experience_db=_StubExperienceDB(),
        rule_kb=_StubRuleKB(),
        method_cards_db=_StubMethodCards(),
    )

    context = retriever.search_context(
        query_text="镀锌钢管安装 DN25 丝接",
        bill_name="镀锌钢管安装",
        bill_desc="DN25 丝接",
        specialty="C10",
        unit="m",
    )

    evidence = context["knowledge_evidence"]
    assert len(evidence["reference_cases"]) == 1
    assert evidence["reference_cases"][0]["record_id"] == "11"

    assert len(evidence["quota_rules"]) == 1
    assert evidence["quota_rules"][0]["id"] == "rule_2"
    assert evidence["quota_rules"][0]["rule_type"] == "quota_rule"

    assert len(evidence["quota_explanations"]) == 1
    assert evidence["quota_explanations"][0]["id"] == "rule_1"
    assert evidence["quota_explanations"][0]["rule_type"] == "quota_explanation"

    assert len(evidence["method_cards"]) == 1
    assert evidence["method_cards"][0]["id"] == "7"
    assert "材质" in evidence["method_cards"][0]["summary"]

    assert context["meta"]["reference_cases_count"] == 1
    assert context["meta"]["quota_rules_count"] == 1
    assert context["meta"]["quota_explanations_count"] == 1
