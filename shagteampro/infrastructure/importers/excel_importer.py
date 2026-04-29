from __future__ import annotations

from pathlib import Path

import pandas as pd


class ExcelImportError(Exception):
    pass


class ExcelImporter:
    SUPPORTED_EXTENSIONS = {".xlsx", ".xls"}

    def import_phrases(self, file_path: str | Path) -> list[str]:
        path = Path(file_path)
        if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            raise ExcelImportError("Поддерживаются только xlsx и xls файлы.")

        try:
            dataframe = pd.read_excel(path, dtype=str, header=None)
        except Exception as error:  # pragma: no cover - safety for backend parser errors
            raise ExcelImportError(f"Не удалось прочитать Excel: {error}") from error

        if dataframe.empty and not list(dataframe.columns):
            return []

        normalized = dataframe.fillna("").apply(lambda col: col.astype(str).str.strip())

        selected_column = None
        for column_name in normalized.columns:
            non_empty = normalized[column_name][normalized[column_name] != ""]
            if not non_empty.empty:
                selected_column = non_empty
                break

        if selected_column is None:
            return []

        seen: set[str] = set()
        result: list[str] = []
        for phrase in selected_column.tolist():
            if phrase and phrase not in seen:
                seen.add(phrase)
                result.append(phrase)
        return result
