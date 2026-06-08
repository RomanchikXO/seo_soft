from __future__ import annotations

from shagteampro.infrastructure.parsers.yandex_organization_parser import YandexOrganizationParser


class YandexOrganizationService:
    """Слой приложения для автозаполнения данных карточки по ссылке организации."""

    def __init__(self, parser: YandexOrganizationParser) -> None:
        self._parser = parser

    def autofill_from_url(self, url: str) -> dict[str, str]:
        normalized_url = (url or "").strip()
        if not normalized_url:
            raise ValueError("Передайте ссылку на организацию Яндекс.Карт.")
        return self._parser.parse(normalized_url)
