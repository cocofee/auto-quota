import ast
from pathlib import Path


def test_match_agent_has_no_top_level_statements_after_return_results():
    source = Path("src/match_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    match_agent = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "match_agent"
    )

    first_return_index = None
    for index, statement in enumerate(match_agent.body):
        if not isinstance(statement, ast.Return):
            continue
        value = statement.value
        assert isinstance(value, ast.Name)
        assert value.id == "results"
        first_return_index = index
        break

    assert first_return_index is not None
    assert match_agent.body[first_return_index + 1:] == []
