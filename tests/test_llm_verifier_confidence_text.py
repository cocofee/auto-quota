from src.llm_verifier import LLMVerifier


def test_confidence_text_uses_90_75_bands():
    verifier = LLMVerifier.__new__(LLMVerifier)

    assert verifier._confidence_text(90) == "★★★推荐(90%)"
    assert verifier._confidence_text(75) == "★★参考(75%)"
    assert verifier._confidence_text(74) == "★待审(74%)"
