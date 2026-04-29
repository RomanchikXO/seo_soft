from __future__ import annotations

from pathlib import Path

from shagteampro.infrastructure.importers.excel_importer import ExcelImporter


class ImportService:
    def __init__(self, excel_importer: ExcelImporter) -> None:
        self._excel_importer = excel_importer

    def import_keywords_from_excel(self, file_path: str | Path) -> list[str]:
        return self._excel_importer.import_phrases(file_path)
