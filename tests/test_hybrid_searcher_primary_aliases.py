from src.hybrid_searcher import HybridSearcher


def test_select_retrieval_aliases_keeps_generic_alias_for_numeric_subject():
    aliases = HybridSearcher._select_retrieval_aliases(
        [
            "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5 \u529f\u7387150KW",
            "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5 150KW",
            "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5",
            "\u5149\u4f0f\u9006\u53d8\u5668",
        ],
        max_count=2,
    )

    assert aliases == [
        "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5 \u529f\u7387150KW",
        "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5",
    ]


def test_build_quota_style_query_variants_include_inverter_power_buckets():
    variants = HybridSearcher._build_quota_style_query_variants(
        query_features={"numeric_params": {"kw": 150}},
        primary_query_profile={
            "primary_subject": "\u7ec4\u4e32\u5f0f\u9006\u53d8\u5668",
            "quota_aliases": [
                "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5 \u529f\u7387150KW",
                "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5 150KW",
                "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5",
            ],
        },
    )

    assert "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5 \u529f\u7387150kW" in variants
    assert "\u5149\u4f0f\u9006\u53d8\u5668\u5b89\u88c5 \u529f\u7387\u2264250kW" in variants


def test_build_query_variants_include_quota_aliases_from_primary_query_profile():
    searcher = HybridSearcher.__new__(HybridSearcher)
    variants = searcher._build_query_variants(
        "组串式逆变器 规格型号:150KW 布置场地:光伏",
        [],
        query_features={},
        route_profile={},
        primary_query_profile={
            "primary_text": "组串式逆变器 规格型号:150KW",
            "primary_subject": "组串式逆变器",
            "quota_aliases": ["光伏逆变器安装", "逆变器安装 150KW"],
        },
    )

    variant_queries = [row["query"] for row in variants]
    assert "光伏逆变器安装" in variant_queries or "逆变器安装 150KW" in variant_queries


def test_build_prior_query_variants_include_quota_aliases_from_primary_query_profile():
    variants = HybridSearcher._build_prior_query_variants(
        "search query",
        full_query="full query",
        item={
            "name": "组串式逆变器",
            "description": "规格型号:150KW 布置场地:光伏场区",
            "canonical_query": {
                "search_query": "组串式逆变器 规格型号:150KW 布置场地:光伏",
                "primary_query_profile": {
                    "primary_text": "组串式逆变器 规格型号:150KW",
                    "primary_subject": "组串式逆变器",
                    "quota_aliases": ["光伏逆变器安装"],
                },
            },
        },
    )

    assert "光伏逆变器安装" in variants


def test_build_query_variants_include_quota_style_diameter_variants():
    searcher = HybridSearcher.__new__(HybridSearcher)
    variants = searcher._build_query_variants(
        "热镀锌钢管 DN200 沟槽连接",
        [],
        query_features={"system": "给排水", "numeric_params": {"dn": 200}},
        route_profile={},
        primary_query_profile={
            "primary_subject": "热镀锌钢管",
            "quota_aliases": ["热浸锌镀锌钢管"],
        },
    )

    variant_queries = [row["query"] for row in variants]
    assert any("公称直径(mm以内) 200" in query for query in variant_queries)


def test_build_query_variants_include_quota_style_sweep_bucket_variants():
    searcher = HybridSearcher.__new__(HybridSearcher)
    variants = searcher._build_query_variants(
        "清扫口 DN32",
        [],
        query_features={"system": "给排水", "numeric_params": {"dn": 32}},
        route_profile={},
        primary_query_profile={
            "primary_subject": "清扫口",
            "quota_aliases": ["地面扫除口安装"],
        },
    )

    variant_queries = [row["query"] for row in variants]
    assert "地面扫除口安装 50mm以内" in variant_queries


def test_build_prior_query_variants_include_quota_style_capacity_variants():
    variants = HybridSearcher._build_prior_query_variants(
        "UPS调试",
        item={
            "name": "不间断电源系统调试",
            "description": "电源容量:15kVA以下",
            "canonical_features": {"system": "电气", "numeric_params": {"kva": 15}},
            "canonical_query": {
                "primary_query_profile": {
                    "primary_subject": "不间断电源系统调试",
                    "quota_aliases": ["保安电源系统调试"],
                },
            },
        },
    )

    assert "保安电源系统调试 不间断电源容量15kVA以下" in variants
