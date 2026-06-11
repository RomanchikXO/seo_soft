from __future__ import annotations

from pathlib import Path

from shagteampro.infrastructure.importers.excel_importer import ExcelImporter
from shagteampro.infrastructure.importers.text_importer import TextImporter


class ImportService:
    def __init__(
        self,
        excel_importer: ExcelImporter,
        text_importer: TextImporter | None = None,
    ) -> None:
        self._excel_importer = excel_importer
        self._text_importer = text_importer or TextImporter()

    def import_keywords_from_excel(self, file_path: str | Path) -> list[str]:
        return self._excel_importer.import_phrases(file_path)

    def import_keywords(self, file_path: str | Path) -> list[str]:
        """Импортирует ключевые фразы из Excel или текстового файла по расширению."""
        suffix = Path(file_path).suffix.lower()
        if suffix in self._excel_importer.SUPPORTED_EXTENSIONS:
            return self._excel_importer.import_phrases(file_path)
        if suffix in self._text_importer.SUPPORTED_EXTENSIONS:
            return self._text_importer.import_phrases(file_path)
        raise ValueError("Поддерживаются файлы Excel (xlsx, xls) и текстовые файлы (txt).")
