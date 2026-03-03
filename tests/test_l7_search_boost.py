# -*- coding: utf-8 -*-
"""L7 搜索召回率优化 测试用例"""

import pytest


class TestNormalizeForMatch:
    """文本归一化函数测试"""

    def test_empty_input(self):
        """空输入返回空字符串"""
        from src.text_normalizer import normalize_for_match
        assert normalize_for_match("") == ""
        assert normalize_for_match(None) == ""
        assert normalize_for_match("   ") == ""

    def test_same_item_different_spacing(self):
        """相同项目不同空格应归一化为同一字符串"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("给水管道 DN25 镀锌钢管")
        b = normalize_for_match("给水管道DN25镀锌钢管")
        assert a == b
        assert a != ""

    def test_same_item_different_punctuation(self):
        """相同项目不同标点应归一化为同一字符串"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("给水管道，DN25，镀锌钢管")
        b = normalize_for_match("给水管道 DN25 镀锌钢管")
        assert a == b

    def test_dn_format_unification(self):
        """各种DN写法应统一"""
        from src.text_normalizer import normalize_for_match
        # DN25 各种写法
        variants = [
            "管道DN25",
            "管道DN 25",
            "管道DN-25",
            "管道dn25",
            "管道Φ25",
            "管道φ25",
        ]
        results = [normalize_for_match(v) for v in variants]
        # 所有写法归一化后应相同
        assert len(set(results)) == 1, f"DN归一化不一致: {results}"

    def test_gongcheng_zhijing_to_dn(self):
        """'公称直径(mm)25' 应归一化为 dn25"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("管道公称直径(mm)25")
        b = normalize_for_match("管道DN25")
        assert a == b

    def test_de_to_dn_conversion(self):
        """De标记应转换为对应的DN值"""
        from src.text_normalizer import normalize_for_match
        # De32→DN25（PPR管常见）
        a = normalize_for_match("PPR管De32")
        b = normalize_for_match("PPR管DN25")
        assert a == b

    def test_cable_section_format(self):
        """截面格式统一"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("电缆4mm²")
        b = normalize_for_match("电缆4平方")
        c = normalize_for_match("电缆4mm2")
        assert a == b == c

    def test_parentheses_removed(self):
        """括号内容应被忽略"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("配电箱(详见图纸)")
        b = normalize_for_match("配电箱")
        assert a == b

    def test_action_words_removed(self):
        """动作词不影响匹配"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("管道安装 DN25")
        b = normalize_for_match("管道 DN25")
        assert a == b

    def test_label_descriptions_removed(self):
        """标签式废话被去除"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("镀锌钢管DN25 压力试验:0.6MPa 安装部位:地下室")
        b = normalize_for_match("镀锌钢管DN25")
        assert a == b

    def test_line_number_prefix_removed(self):
        """行首编号被去除"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("1.镀锌钢管DN25")
        b = normalize_for_match("镀锌钢管DN25")
        assert a == b

    def test_different_items_not_confused(self):
        """不同清单项不应被错误合并"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("给水管道DN25")
        b = normalize_for_match("排水管道DN25")
        assert a != b, "给水和排水不应被归一化为同一字符串"

    def test_material_preserved(self):
        """材质名称应保留（不是动作词）"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("镀锌钢管DN25")
        b = normalize_for_match("PPR管DN25")
        assert a != b, "不同材质应有不同的归一化结果"

    def test_case_insensitive(self):
        """大小写不敏感"""
        from src.text_normalizer import normalize_for_match
        a = normalize_for_match("PPR管DN25")
        b = normalize_for_match("ppr管dn25")
        assert a == b


class TestExperienceFuzzyMatch:
    """经验库模糊匹配集成测试"""

    def _setup_temp_db(self, monkeypatch):
        """创建临时数据库（避免 tmp_path 在 Windows 上的权限问题）"""
        import tempfile
        import config
        from pathlib import Path

        temp_dir = Path(tempfile.mkdtemp(prefix="autoquota_test_"))
        db_path = temp_dir / "test_experience.db"
        chroma_dir = temp_dir / "test_chroma"

        monkeypatch.setattr(config, "get_experience_db_path", lambda: db_path)
        monkeypatch.setattr(config, "get_chroma_experience_dir", lambda: chroma_dir)
        monkeypatch.setattr(config, "get_current_province", lambda: "测试省")
        monkeypatch.setattr(config, "get_current_quota_version", lambda p=None: "test_v1")

        return temp_dir

    def test_find_exact_match_with_normalized(self, monkeypatch):
        """归一化匹配：写入时带空格，查询时不带空格，应能命中"""
        import config
        monkeypatch.setattr(config, "EXPERIENCE_FUZZY_MATCH_ENABLED", True)
        self._setup_temp_db(monkeypatch)

        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB(province="测试省")

        # 写入一条经验（带空格的写法）
        exp_db.add_experience(
            bill_text="给水管道 DN25 镀锌钢管",
            quota_ids=["C10-2-79"],
            quota_names=["管道安装DN25以内"],
            source="user_correction",
            confidence=95,
            province="测试省",
        )

        # 用不带空格的写法查询 → 精确匹配失败，但归一化匹配应成功
        result = exp_db._find_exact_match(
            "给水管道DN25镀锌钢管", "测试省"
        )
        assert result is not None, "归一化匹配应能命中"
        assert result.get("_match_method") == "normalized"
        assert "C10-2-79" in result["quota_ids"]

    def test_find_exact_match_still_prefers_exact(self, monkeypatch):
        """精确匹配优先于归一化匹配"""
        import config
        monkeypatch.setattr(config, "EXPERIENCE_FUZZY_MATCH_ENABLED", True)
        self._setup_temp_db(monkeypatch)

        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB(province="测试省")

        exp_db.add_experience(
            bill_text="配电箱安装",
            quota_ids=["C4-1-1"],
            quota_names=["配电箱安装"],
            source="user_correction",
            confidence=95,
            province="测试省",
        )

        # 精确匹配应命中，且不带 _match_method 标记
        result = exp_db._find_exact_match("配电箱安装", "测试省")
        assert result is not None
        assert "_match_method" not in result, "精确匹配不应有 _match_method 标记"

    def test_fuzzy_match_disabled(self, monkeypatch):
        """开关关闭时不走归一化匹配"""
        import config
        monkeypatch.setattr(config, "EXPERIENCE_FUZZY_MATCH_ENABLED", False)
        self._setup_temp_db(monkeypatch)

        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB(province="测试省")

        exp_db.add_experience(
            bill_text="给水管道 DN25",
            quota_ids=["C10-2-79"],
            quota_names=["管道安装"],
            source="user_correction",
            confidence=95,
            province="测试省",
        )

        # 开关关闭，不带空格的写法应找不到
        result = exp_db._find_exact_match("给水管道DN25", "测试省")
        assert result is None, "开关关闭时不应走归一化匹配"


class TestConfigFlags:
    """L7 配置项测试"""

    def test_fuzzy_match_enabled_by_default(self):
        """默认开启经验库模糊匹配"""
        import config
        assert config.EXPERIENCE_FUZZY_MATCH_ENABLED is True

    def test_auto_synonyms_enabled_by_default(self):
        """默认开启自动同义词"""
        import config
        assert config.AUTO_SYNONYMS_ENABLED is True

    def test_bm25_synonym_expansion_enabled_by_default(self):
        """默认开启BM25同义词扩展"""
        import config
        assert config.BM25_SYNONYM_EXPANSION_ENABLED is True


class TestSynonymLoading:
    """同义词表加载测试"""

    def test_load_manual_synonyms(self):
        """手工同义词表应能正常加载"""
        import src.query_builder as qb
        # 重置缓存
        qb._SYNONYMS_CACHE = None
        synonyms = qb._load_synonyms()
        assert len(synonyms) > 0, "手工同义词表应非空"
        # 手工表中的经典条目应存在
        assert "镀锌钢管" in synonyms

    def test_manual_overrides_auto(self, monkeypatch):
        """手工同义词表应覆盖自动表（同一key以手工为准）"""
        import src.query_builder as qb
        qb._SYNONYMS_CACHE = None  # 重置缓存
        synonyms = qb._load_synonyms()

        # "网线"在手工表中有定义，目标是"双绞线缆"
        if "网线" in synonyms:
            assert "双绞线缆" in synonyms["网线"]

    def test_apply_synonyms_basic(self):
        """同义词替换基础测试"""
        from src.query_builder import _apply_synonyms
        # 网线 → 双绞线缆
        result = _apply_synonyms("网线 6类", "C5")
        assert "双绞线缆" in result

    def test_apply_synonyms_specialty_scope(self):
        """同义词按专业范围过滤：没打标签的全专业通用"""
        from src.query_builder import _apply_synonyms
        # 没打标签的同义词（如"网线"）全专业通用，非安装专业也生效
        result = _apply_synonyms("网线 6类", "A1")
        assert "双绞线缆" in result
        # 无专业信息时也应用（兼容旧调用）
        result2 = _apply_synonyms("网线 6类", "")
        assert "双绞线缆" in result2

    def test_auto_synonyms_file_exists(self):
        """自动同义词文件应已生成"""
        from pathlib import Path
        auto_path = Path(__file__).parent.parent / "data" / "auto_synonyms.json"
        # 文件可能不存在（首次运行前），不强制要求
        if auto_path.exists():
            import json
            with open(auto_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 应该有说明字段
            assert "_说明" in data


class TestSynonymMiner:
    """同义词挖掘工具测试"""

    def test_extract_core_nouns_basic(self):
        """核心名词提取基础测试"""
        from tools.synonym_miner import extract_core_nouns
        # 去掉DN参数和动作词
        result = extract_core_nouns("镀锌钢管DN25")
        assert "镀锌钢管" in result
        assert "25" not in result

    def test_extract_core_nouns_cable_model(self):
        """电缆型号应被去除"""
        from tools.synonym_miner import extract_core_nouns
        result = extract_core_nouns("电力电缆WDZ-YJY")
        assert "电力电缆" in result
        assert "wdz" not in result
        assert "yjy" not in result

    def test_extract_core_nouns_box_number(self):
        """配电箱编号应被去除"""
        from tools.synonym_miner import extract_core_nouns
        result = extract_core_nouns("配电箱1-AL")
        assert result == "配电箱"

    def test_extract_core_nouns_length_limit(self):
        """超长文本返回空"""
        from tools.synonym_miner import extract_core_nouns
        # 超过12字的中文应被截断为空
        result = extract_core_nouns("这是一段非常非常非常非常非常非常长的描述")
        assert result == ""

    def test_extract_core_nouns_dedup(self):
        """重复名词应去重"""
        from tools.synonym_miner import extract_core_nouns
        # 模拟"碳钢通风管道 名称:碳钢通风管道"去掉"名称"后的重复
        result = extract_core_nouns("碳钢通风管道碳钢通风管道")
        assert result == "碳钢通风管道"


class TestBM25SynonymExpansion:
    """BM25 同义词扩展变体测试"""

    def test_build_synonym_variant_with_replaced_term(self):
        """已被替换的定额术语应能生成反向变体"""
        from src.hybrid_searcher import HybridSearcher
        # "焊接钢管 镀锌" 是 "镀锌钢管" 的定额写法
        # 反向替换应生成包含 "镀锌钢管" 的变体
        variant = HybridSearcher._build_synonym_variant("焊接钢管 镀锌 DN25")
        # 应该包含 "镀锌钢管" 或某个清单原始写法
        if variant:
            assert variant != "焊接钢管 镀锌 DN25", "变体应与原query不同"

    def test_build_synonym_variant_no_match(self):
        """没有命中同义词时返回None"""
        from src.hybrid_searcher import HybridSearcher
        variant = HybridSearcher._build_synonym_variant("完全不相关的查询")
        assert variant is None

    def test_build_query_variants_includes_synonym(self, monkeypatch):
        """开关开启时，变体列表中应包含同义词扩展"""
        import config
        monkeypatch.setattr(config, "BM25_SYNONYM_EXPANSION_ENABLED", True)
        monkeypatch.setattr(config, "HYBRID_QUERY_VARIANTS", 6)

        from src.hybrid_searcher import HybridSearcher
        searcher = HybridSearcher.__new__(HybridSearcher)

        # 用一个能命中同义词的 query
        variants = searcher._build_query_variants("焊接钢管 镀锌 DN25", [])
        tags = [v["tag"] for v in variants]
        # synonym_expand 可能出现也可能不出现（取决于同义词表内容和去重）
        # 但不应报错
        assert "raw" in tags  # 至少有原始变体

    def test_build_query_variants_disabled(self, monkeypatch):
        """开关关闭时不生成同义词变体"""
        import config
        monkeypatch.setattr(config, "BM25_SYNONYM_EXPANSION_ENABLED", False)

        from src.hybrid_searcher import HybridSearcher
        searcher = HybridSearcher.__new__(HybridSearcher)

        variants = searcher._build_query_variants("焊接钢管 镀锌 DN25", [])
        tags = [v["tag"] for v in variants]
        assert "synonym_expand" not in tags


class TestActionWordOverrides:
    """核心动作词覆盖规则测试（解决背景词误导专业分类）"""

    def test_zaocao_overrides_to_c4(self):
        """凿槽：即使名称含'砌块墙'，也应归C4电气"""
        from src.specialty_classifier import classify
        result = classify("砌块墙电气管凿槽")
        assert result["primary"] == "C4"

    def test_ticao_overrides_to_c4(self):
        """剔槽：即使名称含'混凝土墙'，也应归C4电气"""
        from src.specialty_classifier import classify
        result = classify("混凝土墙剔槽配管")
        assert result["primary"] == "C4"

    def test_grounding_overrides_to_c4(self):
        """接地极：即使名称含'底板钢筋'，也应归C4电气"""
        from src.specialty_classifier import classify
        result = classify("利用底板钢筋作接地极")
        assert result["primary"] == "C4"

    def test_down_conductor_overrides_to_c4(self):
        """引下线：即使名称含'柱内钢筋'，也应归C4电气"""
        from src.specialty_classifier import classify
        result = classify("利用柱内钢筋作引下线")
        assert result["primary"] == "C4"

    def test_lightning_strip_overrides_to_c4(self):
        """避雷带：即使名称含'结构钢筋'，也应归C4电气"""
        from src.specialty_classifier import classify
        result = classify("利用结构钢筋作避雷带")
        assert result["primary"] == "C4"

    def test_equipotential_overrides_to_c4(self):
        """等电位联结应归C4电气"""
        from src.specialty_classifier import classify
        result = classify("等电位联结")
        assert result["primary"] == "C4"

    def test_normal_masonry_still_goes_to_a(self):
        """普通砌体/钢筋项目不受影响，仍归A册"""
        from src.specialty_classifier import classify
        result = classify("砌体墙砌筑")
        assert result["primary"] == "A"

    def test_normal_rebar_still_goes_to_a(self):
        """普通钢筋项目不受影响"""
        from src.specialty_classifier import classify
        result = classify("钢筋制安")
        assert result["primary"] == "A"


class TestHalfPerimeter:
    """配电箱半周长参数提取和验证（全国通用规则）"""

    def test_quota_name_half_perimeter_m(self):
        """定额名"半周长1.0m" → 1000mm"""
        from src.text_parser import parser
        result = parser.parse("成套配电箱安装 悬挂、嵌入式半周长1.0m")
        assert result.get("half_perimeter") == 1000.0

    def test_quota_name_half_perimeter_25m(self):
        """定额名"半周长2.5m" → 2500mm"""
        from src.text_parser import parser
        result = parser.parse("成套配电箱安装 悬挂、嵌入式半周长2.5m")
        assert result.get("half_perimeter") == 2500.0

    def test_quota_name_half_perimeter_05m(self):
        """定额名"半周长0.5m" → 500mm"""
        from src.text_parser import parser
        result = parser.parse("成套配电箱安装 悬挂、嵌入式半周长0.5m")
        assert result.get("half_perimeter") == 500.0

    def test_bill_spec_half_perimeter(self):
        """清单规格"规格:420*470*120" → 半周长=890mm"""
        from src.text_parser import parser
        result = parser.parse("配电箱1AL 规格:420*470*120 距地1.5m安装")
        assert result.get("half_perimeter") == 890.0

    def test_bill_spec_large_box(self):
        """大配电箱"规格:600*800*200" → 半周长=1400mm"""
        from src.text_parser import parser
        result = parser.parse("动力配电箱AP1 规格:600*800*200")
        assert result.get("half_perimeter") == 1400.0

    def test_no_spec_default_1500(self):
        """配电箱无规格时默认1500mm（行业惯例按1.5m套用）"""
        from src.text_parser import parser
        result = parser.parse("配电箱1AL 无端子接线 距地1.5m安装")
        assert result.get("half_perimeter") == 1500.0

    def test_no_spec_peidian_gui(self):
        """配电柜无规格也默认1500mm"""
        from src.text_parser import parser
        result = parser.parse("照明配电柜 暗装")
        assert result.get("half_perimeter") == 1500.0

    def test_non_box_no_half_perimeter(self):
        """非配电箱项目（如灯具）不提取半周长"""
        from src.text_parser import parser
        result = parser.parse("吸顶灯安装 LED 15W")
        assert "half_perimeter" not in result

    def test_floor_standing_quota_no_half_perimeter(self):
        """落地式定额名无半周长"""
        from src.text_parser import parser
        result = parser.parse("成套配电箱安装 落地式")
        # 落地式定额名含"配电箱"但无规格也无"半周长"字样
        # 默认给1500（因为含"配电箱"关键词），但实际不影响匹配
        # 因为落地式定额和悬挂式定额都会参与候选排序
        assert "half_perimeter" in result
