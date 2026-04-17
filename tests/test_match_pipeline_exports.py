import src.match_pipeline as match_pipeline
from src.ambiguity_gate import analyze_ambiguity


def test_match_pipeline_facade_reexports_analyze_ambiguity():
    assert match_pipeline.analyze_ambiguity is analyze_ambiguity
