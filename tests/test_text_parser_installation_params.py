from src.text_parser import parser


def test_parse_extracts_total_cable_cores_from_complex_bundle():
    result = parser.parse("WDZN-BYJ 3x4+2x2.5 电缆敷设")

    assert result["cable_section"] == 4
    assert result["cable_cores"] == 5


def test_parse_extracts_port_count_for_switch():
    result = parser.parse("楼层交换机 24口千兆POE交换机")

    assert result["port_count"] == 24


def test_parse_does_not_treat_router_as_circuit_count():
    result = parser.parse("3路由交换机 24口")

    assert result.get("circuits") is None
    assert result["port_count"] == 24
