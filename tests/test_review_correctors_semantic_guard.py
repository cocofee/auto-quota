# -*- coding: utf-8 -*-

from unittest.mock import patch

from src.review_correctors import correct_error


def test_correct_error_rejects_semantically_incompatible_correction():
    item = {"name": "测试清单", "description": ""}
    error = {"type": "category_mismatch"}

    def fake_corrector(_item, _error, _dn, province, _conn):
        return ("Q-1", "错误纠正定额")

    with patch("src.review_correctors._CORRECTOR_DISPATCH", {"category_mismatch": fake_corrector}):
        with patch("src.review_correctors.validate_correction", return_value=True):
            with patch("src.review_correctors._is_relevant_correction", return_value=True):
                with patch("src.review_correctors._is_semantically_compatible_correction", return_value=False):
                    result = correct_error(
                        item,
                        error,
                        dn=None,
                        province="主库",
                        conn=None,
                    )

    assert result is None
