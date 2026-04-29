from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from shagteampro.infrastructure.importers.excel_importer import ExcelImportError, ExcelImporter


def test_excel_importer_rejects_unknown_extension(tmp_path: Path) -> None:
    importer = ExcelImporter()
    text_file = tmp_path / "keys.txt"
    text_file.write_text("k1\nk2", encoding="utf-8")
    with pytest.raises(ExcelImportError):
        importer.import_phrases(text_file)


def test_excel_importer_normalizes_and_deduplicates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    importer = ExcelImporter()
    excel_file = tmp_path / "keys.xlsx"
    excel_file.write_bytes(b"dummy")

    dataframe = pd.DataFrame({0: [" alpha ", "beta", "alpha", None]})
    monkeypatch.setattr(pd, "read_excel", lambda *_args, **_kwargs: dataframe)

    result = importer.import_phrases(excel_file)
    assert result == ["alpha", "beta"]
