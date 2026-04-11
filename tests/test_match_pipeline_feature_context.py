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
    assert classification["fallbacks"][:3] == ["C9", "C13", "C12"]
    assert classification["route_mode"] == "strict"
    assert classification["allow_cross_book_escape"] is False
    assert classification["hard_book_constraints"] == ["C10"]
    assert classification["search_books"][0] == "C10"
    assert "C8" not in classification["search_books"]


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


def test_build_item_context_passes_primary_subject_into_unified_planner(monkeypatch):
    captured = {}

    def fake_plan(**kwargs):
        captured["context_prior"] = dict(kwargs.get("context_prior") or {})
        return {"primary_book": "", "preferred_books": [], "hard_books": [], "route_mode": "open"}

    monkeypatch.setattr(match_pipeline, "resolve_plugin_hints", lambda **kwargs: {})
    monkeypatch.setattr(match_pipeline, "build_unified_search_plan", fake_plan)

    _build_item_context(
        {
            "name": "组串式逆变器",
            "description": "规格型号:150KW 安装点离地高度:屋面支架安装 布置场地:光伏场区 其他技术要求:符合设计及施工规范要求",
            "context_prior": {},
        }
    )

    assert captured["context_prior"]["primary_subject"] == "组串式逆变器"


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


def test_annotate_candidate_scope_signals_marks_main_install_book():
    candidates = match_pipeline._annotate_candidate_scope_signals(
        {"province": "上海市安装工程预算定额(2016)"},
        [
            {"quota_id": "03-2-5-38", "name": "衬微晶板"},
            {"quota_id": "01-12-7-12", "name": "墙饰面 基层 细木工板"},
        ],
    )

    assert candidates[0]["candidate_scope_match"] == 1.0
    assert candidates[0]["candidate_scope_conflict"] is False
    assert candidates[1]["candidate_scope_match"] == 0.0
    assert candidates[1]["candidate_scope_conflict"] is True


def test_build_classification_backfills_search_books_from_unified_plan_when_classification_is_empty(monkeypatch):
    def fake_classify(*args, **kwargs):
        return {"primary": None, "fallbacks": []}

    monkeypatch.setattr(match_pipeline, "classify_specialty", fake_classify)

    classification = _build_classification(
        {
            "specialty": "A",
            "unified_plan": {
                "primary_book": "A",
                "preferred_books": ["A"],
                "hard_books": [],
                "route_mode": "moderate",
                "allow_cross_book_escape": True,
                "reason_tags": ["province_plugin", "seed_specialty"],
                "plugin_hints": {"source": "manual_curated"},
            },
        },
        name="墙面装饰板",
        desc="轻钢龙骨 木饰面",
        section="",
        province="上海市安装工程预算定额(2016)",
    )

    assert classification["primary"] in {"", None}
    assert classification["candidate_books"] == []
    assert classification["search_books"] == []
    assert classification["hard_search_books"] == []
    assert classification["advisory_search_books"] == []
    assert classification["route_mode"] == "open"


def test_build_classification_does_not_backfill_from_generated_benchmark_plan_only(monkeypatch):
    def fake_classify(*args, **kwargs):
        return {"primary": None, "fallbacks": []}

    monkeypatch.setattr(match_pipeline, "classify_specialty", fake_classify)

    classification = _build_classification(
        {
            "specialty": "A",
            "unified_plan": {
                "primary_book": "A",
                "preferred_books": ["A"],
                "hard_books": [],
                "route_mode": "moderate",
                "allow_cross_book_escape": True,
                "reason_tags": ["province_plugin", "seed_specialty"],
                "plugin_hints": {"source": "generated_benchmark_knowledge"},
            },
        },
        name="墙面装饰板",
        desc="轻钢龙骨 木饰面",
        section="",
        province="上海市安装工程预算定额(2016)",
    )

    assert classification["primary"] in {"", None}
    assert classification["search_books"] == []


def test_build_classification_prefers_broad_unified_plan_over_soft_standard_route(monkeypatch):
    def fake_classify(*args, **kwargs):
        return {
            "primary": "C11",
            "fallbacks": ["C4", "C13"],
            "search_books": ["C11", "C4", "C13"],
            "route_mode": "moderate",
            "allow_cross_book_escape": True,
            "hard_book_constraints": [],
        }

    monkeypatch.setattr(match_pipeline, "classify_specialty", fake_classify)

    classification = _build_classification(
        {
            "unified_plan": {
                "primary_book": "A",
                "preferred_books": ["A"],
                "hard_books": [],
                "route_mode": "moderate",
                "allow_cross_book_escape": True,
                "reason_tags": ["province_plugin", "seed_specialty"],
                "plugin_hints": {"source": "generated_benchmark_knowledge"},
            },
        },
        name="石材零星项目",
        desc="灰色石材门槛石",
        section="",
        province="上海市安装工程预算定额(2016)",
    )

    assert classification["primary"] == "C11"
    assert classification["candidate_books"][0] == "C11"
    assert classification["search_books"][0] == "C11"
    assert classification["route_mode"] == "moderate"


def test_build_classification_keeps_strict_standard_route_over_broad_unified_plan(monkeypatch):
    def fake_classify(*args, **kwargs):
        return {
            "primary": "C11",
            "fallbacks": ["C4", "C13"],
            "search_books": ["C11", "C4", "C13"],
            "route_mode": "strict",
            "allow_cross_book_escape": False,
            "hard_book_constraints": ["C11"],
        }

    monkeypatch.setattr(match_pipeline, "classify_specialty", fake_classify)

    classification = _build_classification(
        {
            "unified_plan": {
                "primary_book": "A",
                "preferred_books": ["A"],
                "hard_books": [],
                "route_mode": "moderate",
                "allow_cross_book_escape": True,
                "reason_tags": ["province_plugin", "seed_specialty"],
                "plugin_hints": {"source": "generated_benchmark_knowledge"},
            },
        },
        name="石材零星项目",
        desc="灰色石材门槛石",
        section="",
        province="上海市安装工程预算定额(2016)",
    )

    assert classification["primary"] == "C11"
    assert classification["search_books"][0] == "C11"
    assert classification["hard_book_constraints"] == ["C11"]
    assert classification["hard_search_books"] == ["C11"]


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
    assert "C8" not in classification["search_books"]


def test_build_classification_keeps_c8_borrow_for_strong_industrial_pipe_signal(monkeypatch):
    def fake_classify(name, desc, section_title=None, province=None, bill_code=None,
                      context_prior=None, canonical_features=None, sheet_name=None):
        return {
            "primary": "C10",
            "fallbacks": ["C9", "C8", "C13"],
            "search_books": ["C10", "C9", "C8", "C13"],
            "route_mode": "moderate",
            "allow_cross_book_escape": True,
            "hard_book_constraints": [],
        }

    monkeypatch.setattr(match_pipeline, "classify_specialty", fake_classify)

    classification = _build_classification(
        {"specialty": "C10"},
        name="焊接法兰阀门",
        desc="工业管道 蒸汽 DN50",
        section="",
    )

    assert classification["primary"] == "C10"
    assert "C8" in classification["search_books"]


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


def test_build_classification_does_not_treat_project_name_as_seed_support():
    classification = _build_classification(
        {
            "specialty": "C8",
            "context_prior": {
                "project_name": "普通项目名称",
                "bill_name": "普通清单标题",
            },
        },
        name="自动排气阀",
        desc="型号、规格：DN25",
        section="",
    )

    assert classification["primary"] == "C8"
    assert classification["route_mode"] == "moderate"
    assert classification["hard_book_constraints"] == []
    assert classification["routing_evidence"]["C8"] == ["soft_item_specialty"]


def test_build_classification_drops_incompatible_seeded_specialty_for_province():
    classification = _build_classification(
        {"specialty": "C10"},
        name="给水管",
        desc="DN32",
        section="",
        province="上海市园林工程预算定额(2016)",
    )

    assert classification["primary"] != "C10"


def test_build_classification_drops_standard_seed_in_power_sequence_province():
    classification = _build_classification(
        {"specialty": "C4"},
        name="母线试运",
        desc="母线电压等级:35kV 电气 高压",
        section="",
        province="电力技改序列定额（2020）",
    )

    assert classification["primary"] in {"", None}


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
