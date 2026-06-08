from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class YandexOrganizationParser:
    """Парсер карточки организации в Яндекс.Картах через Playwright."""

    _ALLOWED_HOST_SUFFIXES = (
        "yandex.ru",
        "yandex.by",
        "yandex.kz",
        "yandex.uz",
        "yandex.com",
        "yandex.com.tr",
    )
    _COUNTRY_MARKERS = {"россия", "российская федерация"}

    def parse(self, url: str) -> dict[str, str]:
        normalized_url = self._normalize_url(url)
        self._validate_url(normalized_url)

        raw = self._extract_raw_data(normalized_url)
        city, street, house = self._split_address(raw["address"])

        return {
            "organization": raw["organization"],
            "address": raw["address"],
            "city": city,
            "street": street,
            "house": house,
            "coordinates": raw["coordinates"],
            "yandex_org_url": raw["resolved_url"],
        }

    @staticmethod
    def _normalize_url(url: str) -> str:
        value = (url or "").strip()
        if value.startswith("//"):
            return f"https:{value}"
        parsed = urlparse(value)
        if parsed.scheme:
            return value
        if value.startswith(("yandex.", "www.yandex.", "maps.yandex.")):
            return f"https://{value}"
        return value

    def _validate_url(self, url: str) -> None:
        if not url:
            raise ValueError("Ссылка на организацию не может быть пустой.")

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Ссылка должна начинаться с http:// или https://.")

        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            raise ValueError("Не удалось определить домен в ссылке.")
        if not any(host == suffix or host.endswith(f".{suffix}") for suffix in self._ALLOWED_HOST_SUFFIXES):
            raise ValueError("Поддерживаются только ссылки Яндекса.")

    def _extract_raw_data(self, url: str) -> dict[str, str]:
        from playwright.sync_api import sync_playwright

        self._prepare_runtime_browsers_path()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(locale="ru-RU")
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_selector("h1", timeout=12000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)

                organization = self._read_first_text(
                    page,
                    (
                        "h1.orgpage-header-view__header",
                        "h1.orgpage-header-view__title",
                        "h1",
                    ),
                )
                address = self._read_first_text(
                    page,
                    (
                        ".business-contacts-view__address-link",
                        ".orgpage-contacts-view__address",
                        "[itemprop='address']",
                    ),
                )
                if not address:
                    address = self._read_first_attribute(
                        page,
                        ("meta[itemprop='address']",),
                        "content",
                    )

                resolved_url = page.url
                coordinates = self._extract_coordinates(page, resolved_url)
            finally:
                context.close()
                browser.close()

        return {
            "organization": organization,
            "address": address,
            "coordinates": coordinates,
            "resolved_url": resolved_url,
        }

    @staticmethod
    def _read_first_text(page, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                text = (locator.inner_text(timeout=2000) or "").strip()
            except Exception:
                continue
            if text:
                return YandexOrganizationParser._clean_text(text)
        return ""

    @staticmethod
    def _read_first_attribute(page, selectors: tuple[str, ...], attribute_name: str) -> str:
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                value = (locator.get_attribute(attribute_name, timeout=2000) or "").strip()
            except Exception:
                continue
            if value:
                return YandexOrganizationParser._clean_text(value)
        return ""

    @staticmethod
    def _extract_coordinates(page, resolved_url: str) -> str:
        from_url = YandexOrganizationParser._coordinates_from_url(resolved_url)
        if from_url:
            return from_url

        for selector, attribute in (
            ("meta[itemprop='image']", "content"),
            ("link[rel='canonical']", "href"),
        ):
            value = YandexOrganizationParser._read_first_attribute(page, (selector,), attribute)
            coordinates = YandexOrganizationParser._coordinates_from_url(value)
            if coordinates:
                return coordinates

        try:
            page_html = page.content()
        except Exception:
            page_html = ""
        match = re.search(
            r'"coordinates"\s*:\s*\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]',
            page_html,
        )
        if not match:
            return ""
        lon = match.group(1)
        lat = match.group(2)
        return f"{lon}, {lat}"

    @staticmethod
    def _coordinates_from_url(url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)

        query_pairs = parse_qs(parsed.query)
        ll_values = query_pairs.get("ll")
        if ll_values:
            return YandexOrganizationParser._normalize_ll(ll_values[0])

        # Иногда параметры карты оказываются во fragment после '#'
        fragment = parsed.fragment
        if fragment:
            fragment_pairs = parse_qs(fragment)
            fragment_ll = fragment_pairs.get("ll")
            if fragment_ll:
                return YandexOrganizationParser._normalize_ll(fragment_ll[0])
            fragment_match = re.search(r"(?:^|[?&])ll=([^&]+)", fragment)
            if fragment_match:
                return YandexOrganizationParser._normalize_ll(fragment_match.group(1))
        return ""

    @staticmethod
    def _normalize_ll(value: str) -> str:
        cleaned = (value or "").strip().replace("%2C", ",")
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        if len(parts) < 2:
            return ""
        return f"{parts[0]}, {parts[1]}"

    @staticmethod
    def _split_address(address: str) -> tuple[str, str, str]:
        normalized = YandexOrganizationParser._clean_text(address)
        if not normalized:
            return "", "", ""

        parts = [part.strip() for part in normalized.split(",") if part.strip()]
        if parts and parts[0].lower() in YandexOrganizationParser._COUNTRY_MARKERS:
            parts = parts[1:]
        if not parts:
            return "", "", ""

        city = YandexOrganizationParser._resolve_city(parts)
        house_index = YandexOrganizationParser._resolve_house_index(parts)

        street = ""
        house = ""
        if house_index is not None:
            house = parts[house_index]
            if house_index - 1 >= 0:
                street = parts[house_index - 1]
        elif len(parts) >= 2:
            street = parts[-1]

        if street == city and len(parts) > 1:
            street = parts[1]
        if house == city:
            house = ""

        return city, street, house

    @staticmethod
    def _resolve_city(parts: list[str]) -> str:
        city_markers = ("город", "г.", "г ", "поселок", "посёлок", "деревня", "село", "станица")
        for part in parts:
            lowered = part.lower()
            if lowered.startswith(city_markers):
                return part

        if len(parts) >= 2 and any(marker in parts[0].lower() for marker in ("область", "край", "республика")):
            return parts[1]
        return parts[0]

    @staticmethod
    def _resolve_house_index(parts: list[str]) -> int | None:
        explicit_markers = ("д.", "дом", "стр.", "строение", "корп", "литер", "вл.", "к.")
        for index in range(len(parts) - 1, -1, -1):
            lowered = parts[index].lower()
            if any(marker in lowered for marker in explicit_markers):
                return index
            if any(char.isdigit() for char in lowered):
                return index
        return None

    @staticmethod
    def _prepare_runtime_browsers_path() -> None:
        browsers_path = Path.home() / ".shagteampro" / "playwright-browsers"
        browsers_path.mkdir(parents=True, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(value.split())
