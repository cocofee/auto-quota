from __future__ import annotations

import pytest
import sqlite3
from pathlib import Path

from src import match_core, match_pipeline, query_builder, quota_db
from src import experience_db
from src.param_validator import ParamValidator


def _patch_quota_db_path(monkeypatch, db_path: Path):
    monkeypatch.setattr(quota_db.config, "get_current_province", lambda: "test-province")
    monkeypatch.setattr(quota_db.config, "get_quota_db_path", lambda province=None: db_path)


def test_build_alternatives_skips_invalid_candidates():
    candidates = [
        {"name": "missing-id", "param_score": 0.9},
        {"quota_id": "C1-1-1", "name": "有效定额", "param_score": 0.8, "param_match": True},
    ]

    alternatives = match_pipeline._build_alternatives(candidates, top_n=3)

    assert len(alternatives) == 1
    assert alternatives[0]["quota_id"] == "C1-1-1"
    assert alternatives[0]["name"] == "有效定额"


def test_build_search_result_ignores_invalid_candidates():
    candidates = [
        {"param_match": True, "param_score": 0.95},
        {
            "quota_id": "C2-1-2",
            "name": "有效候选",
            "param_match": True,
            "param_score": 0.82,
            "param_detail": "参数匹配",
        },
    ]

    result = match_pipeline._build_search_result_from_candidates({"name": "item"}, candidates)

    assert result["quotas"]
    assert result["quotas"][0]["quota_id"] == "C2-1-2"
    assert result["quotas"][0]["name"] == "有效候选"


def test_build_search_result_all_invalid_returns_no_match():
    result = match_pipeline._build_search_result_from_candidates(
        {"name": "item"},
        [{"param_match": True, "param_score": 0.95}],
    )

    assert result["quotas"] == []
    assert result["no_match_reason"] == "搜索无匹配结果"


def test_validate_experience_params_exact_still_checks_when_family_missing(monkeypatch):
    exp_result = {
        "quotas": [
            {"quota_id": "Q-1", "name": "配电箱安装 规格(回路以内) 4"},
        ]
    }
    item = {"name": "配电箱安装", "description": "回路数:7回路"}

    class FakeRuleValidator:
        rules = {"enabled": True}
        family_index = {}

    parse_calls = []

    def fake_parse(text: str):
        parse_calls.append(text)
        return {"circuits": 7} if "7回路" in text else {"circuits": 4}

    def fake_params_match(bill_params: dict, quota_params: dict):
        return False, 0.0

    monkeypatch.setattr(match_core.text_parser, "parse", fake_parse)
    monkeypatch.setattr(match_core.text_parser, "params_match", fake_params_match)

    validated = match_core._validate_experience_params(
        exp_result,
        item,
        rule_validator=FakeRuleValidator(),
        is_exact=True,
    )

    assert validated is None
    assert len(parse_calls) >= 2


def test_validate_experience_params_exact_relaxed_when_family_available(monkeypatch):
    """方法1(family)验证通过后，方法2仍然执行但用宽松模式：
    只拦截硬参数超档(score=0.0)，放行软参数差异(score>0.0)。"""
    exp_result = {
        "quotas": [
            {"quota_id": "Q-1", "name": "配电箱安装 规格(回路以内) 8"},
        ]
    }
    item = {"name": "配电箱安装", "description": "回路数:7回路"}

    class FakeRuleValidator:
        rules = {"enabled": True}
        family_index = {"Q-1": {"tiers": [4, 8]}}

        @staticmethod
        def _extract_param_value(bill_text: str, family: dict):
            return 7

        @staticmethod
        def _find_correct_tier(bill_value: int, tiers: list[int]):
            return 8

        @staticmethod
        def _find_quota_by_tier(family: dict, tier: int):
            return "Q-1"

    # 方法2现在会执行（不再跳过），但宽松模式下软差异不拦截
    validated = match_core._validate_experience_params(
        exp_result,
        item,
        rule_validator=FakeRuleValidator(),
        is_exact=True,
    )

    assert validated == exp_result


def test_normalize_bill_name_preserves_special_lamp_semantics():
    normalized = query_builder._normalize_bill_name("LED紫外杀菌灯 36W 220V")

    assert "紫外" in normalized
    assert "杀菌" in normalized
    assert "普通灯具安装" not in normalized


def test_normalize_bill_name_skips_mapping_for_excluded_lamp_terms():
    name = "道路灯杆照明"
    normalized = query_builder._normalize_bill_name(name)

    assert normalized == name


def test_import_history_schema_migration_sql_is_applied_when_file_path_missing():
    db = quota_db.QuotaDB.__new__(quota_db.QuotaDB)

    class FakeCursor:
        def __init__(self):
            self.sql_calls = []
            self._last_sql = ""

        def execute(self, sql: str, _params=None):
            self.sql_calls.append(sql.strip())
            self._last_sql = sql.strip()
            return self

        def fetchall(self):
            if self._last_sql.startswith("PRAGMA table_info(import_history)"):
                # 模拟旧表结构：没有 file_path
                return [(0, "id"), (1, "file_name"), (2, "file_size")]
            return []

    cursor = FakeCursor()
    db._migrate_import_history_schema(cursor)

    merged_sql = "\n".join(cursor.sql_calls)
    assert "CREATE TABLE import_history_new" in merged_sql
    assert "INSERT OR REPLACE INTO import_history_new" in merged_sql
    assert "file_name AS file_path" in merged_sql
    assert "DROP TABLE import_history" in merged_sql
    assert "ALTER TABLE import_history_new RENAME TO import_history" in merged_sql


def test_record_import_and_get_history_use_file_path(monkeypatch):
    db = quota_db.QuotaDB.__new__(quota_db.QuotaDB)

    source = Path(__file__).resolve()
    expected_path = str(source)

    captured = {"write_sql": "", "write_params": None, "read_sql": ""}

    class FakeWriteCursor:
        def execute(self, sql: str, params=None):
            captured["write_sql"] = sql
            captured["write_params"] = params
            return self

    class FakeWriteConn:
        def cursor(self):
            return FakeWriteCursor()

        def commit(self):
            return None

        def close(self):
            return None

    class FakeReadCursor:
        def execute(self, sql: str):
            captured["read_sql"] = sql
            return self

        def fetchall(self):
            return [{
                "file_path": expected_path,
                "file_name": source.name,
                "file_size": 1,
                "file_mtime": 1.0,
                "specialty": "安装",
                "quota_count": 12,
                "imported_at": 2.0,
            }]

    class FakeReadConn:
        def cursor(self):
            return FakeReadCursor()

        def close(self):
            return None

    db.init_db = lambda: None
    db._connect = lambda row_factory=False: FakeReadConn() if row_factory else FakeWriteConn()

    db.record_import(expected_path, "安装", 12)
    history = db.get_import_history()

    assert "file_path, file_name" in captured["write_sql"]
    assert captured["write_params"][0] == expected_path
    assert captured["write_params"][1] == source.name
    assert "SELECT file_path, file_name" in captured["read_sql"]
    assert history[0]["file_path"] == expected_path
    assert history[0]["file_name"] == source.name


# ============================================================
# P1: 辅助库级联搜索应按 hybrid_score 排序，不是 score
# ============================================================

def test_cascade_search_aux_sorts_by_hybrid_score(monkeypatch):
    """辅助库搜索结果应按 hybrid_score 降序排序，高分项排前面。"""
    # 构造两个假辅助搜索器，第二个返回更高分的候选
    class FakeAux:
        def __init__(self, province, results):
            self.province = province
            self._results = results

        def search(self, query, top_k=None, books=None):
            return self._results

    aux1 = FakeAux("省份A", [
        {"quota_id": "A-1", "name": "低分定额", "hybrid_score": 0.3},
    ])
    aux2 = FakeAux("省份B", [
        {"quota_id": "B-1", "name": "高分定额", "hybrid_score": 0.9},
    ])

    # 构造一个假的主搜索器，挂载两个辅助库
    # 主搜索器需要有 search 方法（现在主库和辅助库并行搜索，不再互斥）
    class FakeSearcher:
        aux_searchers = [aux1, aux2]
        uses_standard_books = True

        class bm25_engine:
            quota_books = []

        def search(self, query, top_k=None, books=None):
            # 主库返回一个低分结果，验证辅助库高分结果排在前面
            return [{"quota_id": "M-1", "name": "主库定额", "hybrid_score": 0.1}]

    # 非安装分类（不以C开头）
    classification = {"primary": "A01", "fallbacks": []}
    result = match_core.cascade_search(FakeSearcher(), "测试查询", classification)

    # 高分项应排在第一位（辅助库B-1得分0.9最高）
    assert result[0]["quota_id"] == "B-1"
    assert result[0]["hybrid_score"] == 0.9
    # 辅助库和主库结果都应该出现在合并结果中
    all_ids = {r["quota_id"] for r in result}
    assert "A-1" in all_ids
    assert "B-1" in all_ids
    assert "M-1" in all_ids


# ============================================================
# P2: 经验库"缺版本号"应降级为 stale，不应直通
# ============================================================

@pytest.mark.parametrize("current_ver,record_ver,desc", [
    ("v2026.1", "", "经验记录缺版本号"),
    ("", "", "两端都缺版本号"),
])
def test_experience_search_missing_version_returns_stale(
    monkeypatch, current_ver, record_ver, desc
):
    """经验记录或当前定额库缺版本号时，match_type 应为 stale 而非 exact。"""
    monkeypatch.setattr(
        experience_db.config, "get_current_quota_version",
        lambda province=None: current_ver
    )

    # 构造一个最小化的 ExperienceDB 实例，跳过真实初始化
    db = experience_db.ExperienceDB.__new__(experience_db.ExperienceDB)
    db.province = "test"

    # 模拟 _find_exact_match 返回一条带指定版本号的记录
    fake_record = {
        "id": "rec-1",
        "bill_text": "测试清单",
        "quota_ids": "Q-1",
        "quota_names": "测试定额",
        "confidence": 95,
        "confirm_count": 3,
        "authority": "confirmed",
        "quota_db_version": record_ver,
    }
    monkeypatch.setattr(db, "_find_exact_match", lambda *a, **kw: dict(fake_record))

    # 模拟 _normalize_record_quota_fields 为空操作（就地修改，返回 record 本身）
    monkeypatch.setattr(db, "_normalize_record_quota_fields", lambda r: r)

    # 模拟 collection.count() 返回 0（跳过向量搜索）
    class FakeCollection:
        def count(self):
            return 0

    # collection 是 @property，需要通过 monkeypatch 替换类属性
    monkeypatch.setattr(
        type(db), "collection",
        property(lambda self: FakeCollection())
    )

    results = db.search_similar("测试清单", top_k=3, min_confidence=60)

    # 缺版本号应降级为 stale，不应直通
    assert len(results) == 1
    assert results[0]["match_type"] == "stale"


def test_experience_similar_missing_version_returns_stale(monkeypatch):
    """similar 路径下，缺版本号的权威经验也应降级为 stale。"""
    monkeypatch.setattr(
        experience_db.config, "get_current_quota_version",
        lambda province=None: "v2026.1"
    )

    db = experience_db.ExperienceDB.__new__(experience_db.ExperienceDB)
    db.province = "test"
    db._find_exact_match = lambda *a, **kw: None
    db._normalize_record_quota_fields = lambda r: r

    class FakeEmbedding:
        def tolist(self):
            return [[0.1, 0.2]]

    class FakeModel:
        def encode(self, _texts, **kwargs):
            return FakeEmbedding()

    class FakeCollection:
        def count(self):
            return 1

        def query(self, **_kwargs):
            return {"ids": [["1"]], "distances": [[0.1]]}

    class FakeCursor:
        def execute(self, _sql, _params=None):
            return self

        def fetchall(self):
            return [{
                "id": 1,
                "province": "test",
                "confidence": 90,
                "layer": "authority",
                "quota_ids": "Q-1",
                "quota_names": "测试定额",
                "quota_db_version": "",  # 缺版本号
            }]

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    db._model = FakeModel()
    db._connect = lambda row_factory=False: FakeConn()
    monkeypatch.setattr(type(db), "collection", property(lambda self: FakeCollection()))

    results = db.search_similar("测试清单", top_k=3, min_confidence=60, province="test")

    assert len(results) == 1
    assert results[0]["match_type"] == "stale"


# ============================================================
# P2: 多辅助库去重键应包含来源库，不同库的相同 quota_id 不互相覆盖
# ============================================================

def test_dedup_preserves_same_quota_id_from_different_sources(monkeypatch):
    """不同来源库的相同 quota_id 应同时保留，不被去重。"""
    from src.match_core import _prepare_candidates

    # 构造两条来自不同库但 quota_id 相同的候选
    candidates = [
        {
            "quota_id": "C10-1-1",
            "name": "给水管道安装（省份A）",
            "hybrid_score": 0.8,
            "_source_province": "省份A",
        },
        {
            "quota_id": "C10-1-1",
            "name": "给水管道安装（省份B）",
            "hybrid_score": 0.7,
            "_source_province": "省份B",
        },
    ]

    # 模拟 cascade_search 返回这些候选
    class FakeSearcher:
        pass

    class FakeReranker:
        def rerank(self, query, cands):
            return cands

    class FakeValidator:
        def validate_candidates(self, query, cands, supplement_query=None, bill_params=None):
            return cands

    # 用 monkeypatch 替换 cascade_search，测试结束自动恢复
    monkeypatch.setattr(
        match_core, "cascade_search",
        lambda searcher, query, classification: list(candidates)
    )

    result = _prepare_candidates(
        FakeSearcher(), FakeReranker(), FakeValidator(),
        "给水管道", "给水管道 DN25", {"primary": "C10", "fallbacks": []}
    )
    # 两条来自不同库的相同 quota_id 都应保留
    assert len(result) == 2
    provinces = {r["_source_province"] for r in result}
    assert "省份A" in provinces
    assert "省份B" in provinces


# ============================================================
# P1: 品类冲突误报 — "法兰"和"管件"不应在互斥组中
# ============================================================

class TestCategoryConflictFalsePositiveFix:
    """回归测试：法兰/管件不再触发品类冲突误报。

    原因分析：
    - "法兰"既是独立产品也是连接方式修饰语（"法兰蝶阀"中法兰是连接方式，
      不应与"阀门"冲突）
    - "管件"是弯头/三通/异径管的上位泛称（"管件安装"匹配到"弯头"是合理的，
      不应报品类冲突）
    """

    def test_flanged_valve_no_conflict_with_valve_quota(self):
        """法兰蝶阀 vs 阀门安装定额：不应冲突（法兰是连接方式修饰语）"""
        penalty, detail = ParamValidator._check_category_conflict(
            "法兰蝶阀DN100", "阀门安装 法兰连接 DN100"
        )
        assert penalty == 0.0, f"法兰蝶阀不应与阀门安装冲突: {detail}"

    def test_pipe_fitting_no_conflict_with_elbow_quota(self):
        """管件安装 vs 弯头定额：不应冲突（弯头是管件的子类型）"""
        penalty, detail = ParamValidator._check_category_conflict(
            "管件安装", "弯头 碳钢 DN50"
        )
        assert penalty == 0.0, f"管件安装不应与弯头冲突: {detail}"

    def test_pipe_fitting_no_conflict_with_tee_quota(self):
        """管件安装 vs 三通定额：不应冲突（三通也是管件子类型）"""
        penalty, detail = ParamValidator._check_category_conflict(
            "管件安装 DN80", "三通 碳钢 DN80"
        )
        assert penalty == 0.0, f"管件安装不应与三通冲突: {detail}"

    def test_valve_still_conflicts_with_elbow(self):
        """阀门 vs 弯头：仍应冲突（真正的品类互斥）

        注意：方法用子串匹配(cat in text)，所以输入须包含冲突组的确切关键词
        """
        penalty, detail = ParamValidator._check_category_conflict(
            "阀门安装DN100", "弯头DN100"
        )
        assert penalty > 0, "阀门 vs 弯头应报品类冲突"

    def test_valve_still_conflicts_with_tee(self):
        """阀门 vs 三通：仍应冲突"""
        penalty, detail = ParamValidator._check_category_conflict(
            "阀门安装DN150", "三通DN150"
        )
        assert penalty > 0, "阀门 vs 三通应报品类冲突"

    def test_valve_still_conflicts_with_reducer(self):
        """阀门 vs 异径管：仍应冲突"""
        penalty, detail = ParamValidator._check_category_conflict(
            "阀门安装DN50", "异径管DN50×25"
        )
        assert penalty > 0, "阀门 vs 异径管应报品类冲突"

    def test_pump_still_conflicts_with_fan(self):
        """水泵 vs 风机：仍应冲突（其他冲突组不受影响）"""
        penalty, detail = ParamValidator._check_category_conflict(
            "水泵安装", "风机安装"
        )
        assert penalty > 0, "水泵 vs 风机应报品类冲突"
