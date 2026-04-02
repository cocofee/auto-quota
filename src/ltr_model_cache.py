from __future__ import annotations

import threading
from pathlib import Path


class LTRModelCache:
    _model = None
    _model_path: str | None = None
    _lock = threading.Lock()

    @classmethod
    def get_model(cls, model_path):
        normalized_path = str(Path(model_path).expanduser())
        if cls._model is not None and cls._model_path == normalized_path:
            return cls._model

        with cls._lock:
            if cls._model is None or cls._model_path != normalized_path:
                cls._model = cls._load_model(normalized_path)
                cls._model_path = normalized_path
        return cls._model

    @classmethod
    def _load_model(cls, model_path):
        import lightgbm as lgb

        return lgb.Booster(model_file=model_path)
