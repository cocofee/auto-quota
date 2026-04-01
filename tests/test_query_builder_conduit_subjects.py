from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_keeps_explicit_electrical_sc_conduit_generic():
    query = build_quota_query(
        parser,
        "电气配管",
        "规格:SC32 敷设方式:暗敷设",
        specialty="C4",
    )

    assert "焊接钢管敷设" not in query
    assert "钢管敷设" not in query
    assert "SC钢管" not in query
    assert "电线管敷设" in query
    assert "SC" in query
    assert "公称直径 32" in query


def test_build_quota_query_keeps_non_electrical_sc_pipe_out_of_conduit_generic():
    query = build_quota_query(
        parser,
        "焊接钢管",
        "材质、规格:SC32 连接形式:螺纹连接",
    )

    assert "焊接钢管敷设" in query
    assert "电线管敷设" not in query


def test_build_quota_query_can_consume_plugin_family_hints_for_ambiguous_sc_conduit():
    query = build_quota_query(
        parser,
        "电气配管",
        "规格:SC32 敷设方式:暗敷设",
        specialty="C4",
        context_prior={
            "system_hint": "电气",
            "plugin_hints": {
                "preferred_quota_names": ["波纹电线管敷设 内径 32"],
            },
        },
    )

    assert "波纹电线管" in query


def test_build_quota_query_does_not_reappend_steel_canonical_name_for_ambiguous_conduit():
    canonical_features = parser.parse_canonical(
        "电气配管 规格:SC32 敷设方式:暗敷设",
        specialty="C4",
    )
    query = build_quota_query(
        parser,
        "电气配管",
        "规格:SC32 敷设方式:暗敷设",
        specialty="C4",
        canonical_features=canonical_features,
    )

    assert "SC钢管配管" not in query
    assert "电线管敷设" in query
