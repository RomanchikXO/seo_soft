from __future__ import annotations

import re
from pathlib import Path


class TextImportError(Exception):
    pass


class TextImporter:
    """Импортирует ключевые фразы из текстовых файлов.

    Фразы могут быть разделены различными разделителями: переводом строки,
    запятой, точкой с запятой, табуляцией или вертикальной чертой. Пробелы
    внутри фразы сохраняются (фраза может состоять из нескольких слов).
    """

    SUPPORTED_EXTENSIONS = {".txt"}
    SPLIT_PATTERN = re.compile(r"[\r\n,;\t|]+")
    ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "latin-1")

    def import_phrases(self, file_path: str | Path) -> list[str]:
        path = Path(file_path)
        if path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            raise TextImportError("Поддерживаются только txt файлы.")

        try:
            raw_text = self._read_text(path)
        except Exception as error:  # pragma: no cover - safety for IO errors
            raise TextImportError(f"Не удалось прочитать файл: {error}") from error

        seen: set[str] = set()
        result: list[str] = []
        for chunk in self.SPLIT_PATTERN.split(raw_text):
            phrase = chunk.strip()
            if phrase and phrase not in seen:
                seen.add(phrase)
                result.append(phrase)
        return result

    def _read_text(self, path: Path) -> str:
        data = path.read_bytes()
        for encoding in self.ENCODINGS:
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")
