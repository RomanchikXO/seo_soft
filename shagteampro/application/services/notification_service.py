from __future__ import annotations

import datetime
import html
import re
import threading
from typing import Callable

from shagteampro.application.services.settings_service import SettingsService
from shagteampro.infrastructure.notifications import TelegramNotifier

TELEGRAM_TOKEN_KEY = "telegram_token"
TELEGRAM_CHAT_ID_KEY = "telegram_chat_id"
TELEGRAM_PROXY_KEY = "telegram_proxy"

PROXY_PATTERN = re.compile(
    r"^(?:http|https)://"  # схема
    r"(?:[^\s:@/]+(?::[^\s:@/]*)?@)?"  # необязательные user:pass@
    r"[^\s:@/]+"  # host
    r":\d{1,5}/?$"  # :port
)


class NotificationService:
    """Готовит и отправляет уведомления о результатах работы приложения в Telegram.

    Сервис самодостаточен: данные подключения (токен бота и chat_id) берутся из
    глобальных настроек, а сборка текста сообщения отделена от его отправки, что
    упрощает тестирование. Отправка по умолчанию выполняется в фоновом потоке,
    чтобы не задерживать ответ основного сценария.
    """

    def __init__(
        self,
        settings_service: SettingsService,
        notifier: TelegramNotifier | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._settings_service = settings_service
        self._log = logger or self._default_log
        self._notifier = notifier or TelegramNotifier(logger=self._log)

    def notify_optimization_finished(
        self,
        summary: dict[str, object],
        background: bool = True,
    ) -> bool:
        """Отправляет статистику завершенной оптимизации в Telegram.

        Возвращает True, если отправка инициирована (есть валидные настройки).
        При background=True фактическая сетевая отправка идет в отдельном потоке.
        """
        token, chat_id, proxy = self._load_credentials()
        if not token or not chat_id:
            self._log("NotificationService: Telegram не настроен, уведомление пропущено.")
            return False

        if proxy and not self.is_valid_proxy(proxy):
            self._log(f"NotificationService: некорректный прокси '{proxy}', отправка без прокси.")
            proxy = ""

        text = self.build_optimization_message(summary)
        if background:
            thread = threading.Thread(
                target=self._notifier.send_message,
                args=(token, chat_id, text, proxy),
                name="telegram-notify",
                daemon=True,
            )
            thread.start()
            return True
        return self._notifier.send_message(token, chat_id, text, proxy)

    def _load_credentials(self) -> tuple[str, str, str]:
        settings = self._settings_service.load_settings()
        token = str(settings.get(TELEGRAM_TOKEN_KEY, "")).strip()
        chat_id = str(settings.get(TELEGRAM_CHAT_ID_KEY, "")).strip()
        proxy = str(settings.get(TELEGRAM_PROXY_KEY, "")).strip()
        return token, chat_id, proxy

    @staticmethod
    def is_valid_proxy(proxy: str) -> bool:
        """Проверяет формат прокси: scheme://[user:pass@]host:port (http/https)."""
        return bool(PROXY_PATTERN.match((proxy or "").strip()))

    @staticmethod
    def build_optimization_message(summary: dict[str, object]) -> str:
        """Собирает HTML-текст с итоговой статистикой оптимизации."""
        processed = int(summary.get("processed_cards", 0) or 0)
        search_done = int(summary.get("total_search_performed", 0) or 0)
        search_target = int(summary.get("total_search_target", 0) or 0)
        maps_done = int(summary.get("total_maps_performed", 0) or 0)
        maps_target = int(summary.get("total_maps_target", 0) or 0)
        total_failed = max(0, search_target - search_done) + max(0, maps_target - maps_done)

        timestamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

        lines = [
            "<b>✅ Оптимизация завершена</b>",
            f"<i>{html.escape(timestamp)}</i>",
            "",
            f"📇 Обработано карточек: <b>{processed}</b>",
            f"🔍 Переходы в поиске: <b>{search_done}/{search_target}</b>",
            f"🗺 Переходы в карты: <b>{maps_done}/{maps_target}</b>",
            f"⚠️ Не выполнено действий: <b>{total_failed}</b>",
        ]

        cards = summary.get("cards", [])
        typed_cards = cards if isinstance(cards, list) else []
        card_lines = NotificationService._build_card_lines(typed_cards)
        search_lines = NotificationService._build_mode_lines(typed_cards, mode="search", label="поиск")
        maps_lines = NotificationService._build_mode_lines(typed_cards, mode="maps", label="карты")
        failed_lines = NotificationService._build_failed_lines(typed_cards)

        if card_lines:
            lines.append("")
            lines.append("<b>По организациям:</b>")
            lines.extend(card_lines)

        if search_lines:
            lines.append("")
            lines.append("<b>🔍 Поиск по организациям:</b>")
            lines.extend(search_lines)

        if maps_lines:
            lines.append("")
            lines.append("<b>🗺 Карты по организациям:</b>")
            lines.extend(maps_lines)

        if failed_lines:
            lines.append("")
            lines.append("<b>⚠️ Не удалось выполнить:</b>")
            lines.extend(failed_lines)

        return "\n".join(lines)

    @staticmethod
    def _build_card_lines(cards: list[dict[str, object]]) -> list[str]:
        card_lines: list[str] = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            title = str(card.get("organization") or card.get("card_name") or "Без названия").strip()
            title = html.escape(title) or "Без названия"

            search_done = int(card.get("search_performed", 0) or 0)
            search_target = int(card.get("search_target", 0) or 0)
            maps_done = int(card.get("maps_performed", 0) or 0)
            maps_target = int(card.get("maps_target", 0) or 0)

            parts: list[str] = []
            if search_target:
                parts.append(f"поиск {search_done}/{search_target}")
            if maps_target:
                parts.append(f"карты {maps_done}/{maps_target}")
            if not parts:
                continue

            failed = max(0, search_target - search_done) + max(0, maps_target - maps_done)
            suffix = f" — не выполнено {failed}" if failed else ""
            card_lines.append(f"• <b>{title}</b>: {', '.join(parts)}{suffix}")
        return card_lines

    @staticmethod
    def _build_mode_lines(cards: list[dict[str, object]], mode: str, label: str) -> list[str]:
        lines: list[str] = []
        done_key = f"{mode}_performed"
        target_key = f"{mode}_target"
        for card in cards:
            if not isinstance(card, dict):
                continue
            target = int(card.get(target_key, 0) or 0)
            if target <= 0:
                continue
            done = int(card.get(done_key, 0) or 0)
            title = str(card.get("organization") or card.get("card_name") or "Без названия").strip()
            title = html.escape(title) or "Без названия"
            lines.append(f"• <b>{title}</b>: {label} {done}/{target}")
        return lines

    @staticmethod
    def _build_failed_lines(cards: list[dict[str, object]]) -> list[str]:
        lines: list[str] = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            search_target = int(card.get("search_target", 0) or 0)
            search_done = int(card.get("search_performed", 0) or 0)
            maps_target = int(card.get("maps_target", 0) or 0)
            maps_done = int(card.get("maps_performed", 0) or 0)

            search_failed = max(0, search_target - search_done)
            maps_failed = max(0, maps_target - maps_done)
            total_failed = search_failed + maps_failed
            if total_failed <= 0:
                continue

            title = str(card.get("organization") or card.get("card_name") or "Без названия").strip()
            title = html.escape(title) or "Без названия"

            failed_parts: list[str] = []
            if search_failed:
                failed_parts.append(f"поиск {search_failed}")
            if maps_failed:
                failed_parts.append(f"карты {maps_failed}")
            lines.append(f"• <b>{title}</b>: {', '.join(failed_parts)} (всего {total_failed})")
        return lines

    @staticmethod
    def _default_log(message: str) -> None:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] [NotificationService] {message}", flush=True)
