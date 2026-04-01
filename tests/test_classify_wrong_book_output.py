# -*- coding: utf-8 -*-

from pathlib import Path

from tools.classify_wrong_book import _output_path


def test_output_path_appends_suffix_without_replacing_existing_name():
    prefix = Path("output/real_eval/cross5_smoke_20260330_no_post_anchor.wrong_book")

    assert str(_output_path(prefix, ".summary.json")).endswith(".wrong_book.summary.json")
    assert str(_output_path(prefix, ".details.jsonl")).endswith(".wrong_book.details.jsonl")
