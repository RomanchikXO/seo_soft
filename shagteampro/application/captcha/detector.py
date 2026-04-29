from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaptchaChallenge:
    """Найденная на странице капча и локатор элемента для взаимодействия."""

    selector: str
    locator: object


class CaptchaDetector:
    """Определяет наличие капчи на странице Playwright по устойчивому набору селекторов."""

    DEFAULT_SELECTORS = (
        "#js-button.CheckboxCaptcha-Button",
        "input#js-button",
        ".CheckboxCaptcha-Button",
        "[role='checkbox'][aria-labelledby='checkbox-label']",
        "form[action*='showcaptcha']",
        "[class*='CheckboxCaptcha']",
    )

    def __init__(self, selectors: tuple[str, ...] | None = None) -> None:
        self._selectors = selectors or self.DEFAULT_SELECTORS

    @property
    def selector(self) -> str:
        """Возвращает CSS-селектор, объединяющий все известные признаки капчи."""
        return ", ".join(self._selectors)

    def detect(self, page, wait_ms: int = 0) -> CaptchaChallenge | None:
        """Возвращает капчу, если она видима или стала видимой за время ожидания."""
        locator = page.locator(self.selector).first
        try:
            if wait_ms > 0:
                locator.wait_for(state="visible", timeout=wait_ms)
            elif locator.count() == 0 or not locator.is_visible():
                return None
        except Exception:
            return None
        return CaptchaChallenge(selector=self.selector, locator=locator)
