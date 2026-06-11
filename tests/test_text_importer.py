from __future__ import annotations

from pathlib import Path

import pytest

from shagteampro.infrastructure.importers.text_importer import TextImportError, TextImporter


def test_text_importer_rejects_unknown_extension(tmp_path: Path) -> None:
    importer = TextImporter()
    excel_file = tmp_path / "keys.xlsx"
    excel_file.write_bytes(b"dummy")
    with pytest.raises(TextImportError):
        importer.import_phrases(excel_file)


def test_text_importer_splits_on_various_separators(tmp_path: Path) -> None:
    importer = TextImporter()
    text_file = tmp_path / "keys.txt"
    text_file.write_text(
        "купить телефон, ремонт ноутбука; кофе на вынос\nдоставка пиццы|такси москва\t  кофе на вынос ",
        encoding="utf-8",
    )

    result = importer.import_phrases(text_file)

    assert result == [
        "купить телефон",
        "ремонт ноутбука",
        "кофе на вынос",
        "доставка пиццы",
        "такси москва",
    ]


def test_text_importer_reads_cp1251(tmp_path: Path) -> None:
    importer = TextImporter()
    text_file = tmp_path / "keys.txt"
    text_file.write_bytes("ключ один\nключ два".encode("cp1251"))

    result = importer.import_phrases(text_file)

    assert result == ["ключ один", "ключ два"]
