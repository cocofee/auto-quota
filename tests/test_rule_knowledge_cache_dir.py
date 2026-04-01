import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.rule_knowledge import RuleKnowledge


def test_rule_knowledge_redirects_chroma_onnx_cache_to_project_db():
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    original = ONNXMiniLM_L6_V2.DOWNLOAD_PATH
    try:
        ONNXMiniLM_L6_V2.DOWNLOAD_PATH = Path.home() / ".cache" / "chroma" / "onnx_models" / ONNXMiniLM_L6_V2.MODEL_NAME
        RuleKnowledge._patch_chroma_default_onnx_cache_dir()
        assert ONNXMiniLM_L6_V2.DOWNLOAD_PATH == config.get_chroma_onnx_cache_dir(ONNXMiniLM_L6_V2.MODEL_NAME)
    finally:
        ONNXMiniLM_L6_V2.DOWNLOAD_PATH = original
