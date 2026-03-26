from pathlib import Path

from src.bill_reader import _build_source_file_metadata


def test_build_source_file_metadata_strips_common_upload_noise():
    metadata = _build_source_file_metadata(
        Path(r"F:\jarvis\给排水\[安徽]4-2单元-给排水工程(单位工程)_wx_zip(2).xls")
    )

    assert metadata["source_file_name"] == "[安徽]4-2单元-给排水工程(单位工程)_wx_zip(2).xls"
    assert metadata["source_file_stem"] == "4-2单元-给排水工程(单位工程)"
    assert metadata["source_file_title"] == "4-2单元-给排水工程(单位工程)"
    assert metadata["project_name"] == "4-2单元-给排水工程(单位工程)"
    assert metadata["bill_name"] == "4-2单元-给排水工程(单位工程)"
