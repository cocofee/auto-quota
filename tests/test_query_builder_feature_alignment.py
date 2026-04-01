from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_appends_feature_alignment_terms():
    name = "电力电缆敷设"
    description = "WDZN-BYJ 3x4+2x2.5"
    params = parser.parse(f"{name} {description}")
    context_prior = {"context_hints": ["桥架"]}
    canonical_features = parser.parse_canonical(
        f"{name} {description}",
        specialty="C4",
        context_prior=context_prior,
        params=params,
    )

    query = build_quota_query(
        parser,
        name,
        description,
        specialty="C4",
        bill_params=params,
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    assert "电缆" in query
    assert "桥架" in query
    assert "3x4" in query


def test_build_quota_query_suppresses_misleading_support_alignment_for_device_subject():
    name = "组串式逆变器"
    description = "规格型号:150KW 安装点离地高度:屋面支架安装 布置场地:光伏场区 其他技术要求:符合设计及施工规范要求"
    params = parser.parse(f"{name} {description}")
    context_prior = {
        "primary_subject": "组串式逆变器",
        "primary_query_profile": {
            "primary_subject": "组串式逆变器",
            "primary_text": "组串式逆变器 规格型号:150KW",
        },
        "context_hints": ["支吊架"],
        "prior_family": "pipe_support",
    }
    canonical_features = {
        "family": "pipe_support",
        "entity": "支吊架",
        "system": "给排水",
        "canonical_name": "管道支架制作安装",
    }

    query = build_quota_query(
        parser,
        name,
        description,
        specialty="C2",
        bill_params=params,
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    assert "组串式逆变器" in query
    assert "支吊架" not in query
    assert "给排水" not in query
