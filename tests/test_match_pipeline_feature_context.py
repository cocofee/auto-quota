import src.match_pipeline as match_pipeline
from src.match_pipeline import _build_classification, _build_item_context
from src.text_parser import TextParser


parser = TextParser()


def test_build_item_context_propagates_canonical_features():
    name = "\u7535\u529b\u7535\u7f06\u6577\u8bbe"
    description = "WDZN-BYJ 3x4+2x2.5"
    params = parser.parse(f"{name} {description}")
    context_prior = {"context_hints": ["\u6865\u67b6"]}
    canonical_features = parser.parse_canonical(
        f"{name} {description}",
        specialty="C4",
        context_prior=context_prior,
        params=params,
    )

    context = _build_item_context({
        "name": name,
        "description": description,
        "section": "\u7535\u6c14\u5de5\u7a0b",
        "specialty": "C4",
        "params": params,
        "context_prior": context_prior,
        "canonical_features": canonical_features,
    })

    assert context["canonical_features"]["entity"] == "\u7535\u7f06"
    assert "\u7535\u7f06" in context["search_query"]
    assert "\u6865\u67b6" in context["search_query"]
    assert "\u7535\u7f06" in context["full_query"]
    assert context["canonical_query"]["raw_query"] == f"{name} {description}"
    assert context["canonical_query"]["search_query"] == context["search_query"]
    assert context["canonical_query"]["validation_query"] == context["full_query"]
    assert context["canonical_query"]["normalized_query"] == context["normalized_query"]
    assert context["query_route"]["route"] == "installation_spec"


def test_build_item_context_backfills_params_for_openclaw_items():
    item = {
        "name": "管道支架",
        "description": "1.材质:管道支架\n2.管架形式:按需制作\n3.防腐油漆:除锈后刷防锈漆二道,再刷灰色调和漆二道",
        "section": "给排水管道",
        "specialty": "C10",
    }

    context = _build_item_context(item)

    assert item["params"]["support_scope"] == "管道支架"
    assert item["params"]["support_action"] == "制作"
    assert context["canonical_features"]["family"] == "pipe_support"
    assert "管道支架制作安装" in context["search_query"]


def test_build_classification_backfills_borrow_priority_when_specialty_exists():
    classification = _build_classification(
        {"specialty": "C10"},
        name="管道支架",
        desc="",
        section="给排水管道",
    )

    assert classification["primary"] == "C10"
    assert classification["confidence"] == "high"
    assert classification["fallbacks"][:3] == ["C9", "C8", "C13"]
    assert classification["route_mode"] == "strict"
    assert classification["allow_cross_book_escape"] is False
    assert classification["hard_book_constraints"] == ["C10"]
    assert classification["search_books"][0] == "C10"


def test_build_classification_passes_context_and_canonical_features(monkeypatch):
    captured = {}

    def fake_classify(name, desc, section_title=None, province=None, bill_code=None,
                      context_prior=None, canonical_features=None, sheet_name=None):
        captured["name"] = name
        captured["desc"] = desc
        captured["section_title"] = section_title
        captured["sheet_name"] = sheet_name
        captured["province"] = province
        captured["bill_code"] = bill_code
        captured["context_prior"] = context_prior
        captured["canonical_features"] = canonical_features
        return {
            "primary": "C9",
            "fallbacks": ["C10"],
            "search_books": ["C9", "C10"],
            "route_mode": "strict",
            "allow_cross_book_escape": False,
        }

    monkeypatch.setattr(match_pipeline, "classify_specialty", fake_classify)

    classification = _build_classification(
        {
            "code": "030101006009",
            "context_prior": {"system_hint": "消防"},
            "canonical_features": {"family": "valve_body"},
            "sheet_name": "06娑堥槻宸ョ▼",
        },
        name="阀门",
        desc="消防系统",
        section="消防工程",
        province="北京",
    )

    assert classification["primary"] == "C9"
    assert captured["sheet_name"] == "06娑堥槻宸ョ▼"
    assert captured["section_title"] == "消防工程"
    assert captured["context_prior"]["system_hint"] == "消防"
    assert captured["canonical_features"]["family"] == "valve_body"


def test_build_item_context_backfills_project_and_bill_titles_into_context_prior():
    item = {
        "name": "复合管",
        "description": "介质:给水 材质、规格:钢塑复合压力给水管 DN25",
        "project_name": "4-2单元-给排水工程",
        "bill_name": "给排水工程(单位工程)",
    }

    context = _build_item_context(item)

    assert context["context_prior"]["project_name"] == "4-2单元-给排水工程"
    assert context["context_prior"]["bill_name"] == "给排水工程(单位工程)"
    assert context["context_prior"]["unified_plan"]["primary_book"] == "C10"
    assert context["context_prior"]["unified_plan"]["hard_books"] == []


def test_build_classification_uses_project_and_bill_titles_for_soft_book_routing():
    item = {
        "name": "复合管",
        "description": "介质:给水 材质、规格:钢塑复合压力给水管 1.6MPA DN25",
        "project_name": "4-2单元-给排水工程",
        "bill_name": "给排水工程(单位工程)",
        "context_prior": {
            "project_name": "4-2单元-给排水工程",
            "bill_name": "给排水工程(单位工程)",
        },
        "canonical_features": {"family": "pipe_support", "entity": "pipe", "system": "给排水"},
    }

    classification = _build_classification(
        item,
        name=item["name"],
        desc=item["description"],
        section="",
        sheet_name="",
    )

    assert classification["primary"] == "C10"
    assert classification["route_mode"] == "moderate"
    assert classification["allow_cross_book_escape"] is True
    assert "C10" in classification["hard_book_constraints"]
    assert classification["search_books"][0] == "C10"


def test_build_item_context_exposes_unified_plan_aliases(monkeypatch):
    def fake_plan(**kwargs):
        return {
            "primary_book": "C10",
            "preferred_books": ["C10", "C9"],
            "hard_books": ["C10"],
            "route_mode": "strict",
            "search_aliases": ["排气阀安装"],
        }

    monkeypatch.setattr(match_pipeline, "build_unified_search_plan", fake_plan)

    context = _build_item_context(
        {
            "name": "自动排气阀",
            "description": "型号、规格：DN25",
            "context_prior": {},
        }
    )

    assert context["unified_plan"]["primary_book"] == "C10"
    assert context["unified_plan"]["search_aliases"] == ["排气阀安装"]


def test_build_classification_can_override_seeded_specialty_with_strong_evidence(monkeypatch):
    def fake_classify(name, desc, section_title=None, province=None, bill_code=None,
                      context_prior=None, canonical_features=None, sheet_name=None):
        return {
            "primary": "C12",
            "fallbacks": ["C10"],
            "confidence": "high",
            "search_books": ["C12", "C10"],
            "hard_book_constraints": ["C12"],
            "route_mode": "strict",
            "allow_cross_book_escape": False,
            "routing_evidence": {"C12": ["item_override:防结露保温"]},
            "book_scores": {"C12": 6.0},
        }

    monkeypatch.setattr(match_pipeline, "classify_specialty", fake_classify)

    classification = _build_classification(
        {"specialty": "C10"},
        name="防结露保温",
        desc="绝热材料:橡塑管壳",
        section="",
    )

    assert classification["primary"] == "C12"
    assert classification["route_mode"] == "strict"
    assert classification["hard_book_constraints"] == ["C12"]


def test_build_classification_keeps_seeded_specialty_without_strong_override(monkeypatch):
    def fake_classify(name, desc, section_title=None, province=None, bill_code=None,
                      context_prior=None, canonical_features=None, sheet_name=None):
        return {
            "primary": "C12",
            "fallbacks": ["C10"],
            "confidence": "medium",
            "search_books": ["C12", "C10"],
            "hard_book_constraints": [],
            "route_mode": "moderate",
            "allow_cross_book_escape": True,
            "routing_evidence": {"C12": ["keyword:保温"]},
            "book_scores": {"C12": 1.2},
        }

    monkeypatch.setattr(match_pipeline, "classify_specialty", fake_classify)

    classification = _build_classification(
        {"specialty": "C10"},
        name="普通管道",
        desc="保温做法详见图纸",
        section="",
    )

    assert classification["primary"] == "C10"
    assert classification["route_mode"] == "moderate"
    assert classification["allow_cross_book_escape"] is True
    assert classification["hard_book_constraints"] == []


def test_build_classification_relaxes_bare_seeded_specialty_without_supporting_context():
    classification = _build_classification(
        {"specialty": "C8"},
        name="自动排气阀",
        desc="型号、规格：DN25",
        section="",
    )

    assert classification["primary"] == "C8"
    assert classification["route_mode"] == "moderate"
    assert classification["allow_cross_book_escape"] is True
    assert classification["hard_book_constraints"] == []
    assert classification["routing_evidence"]["C8"] == ["soft_item_specialty"]


def test_build_classification_drops_nonstandard_seeded_specialty_without_support():
    classification = _build_classification(
        {"specialty": "A"},
        name="防潮层、保护层",
        desc="材料:压延膜 层数:一道（镀锌铁丝绑扎）",
        section="",
    )

    assert classification["primary"] == "C12"
    assert classification["route_mode"] == "strict"
    assert classification["hard_book_constraints"] == ["C12"]
