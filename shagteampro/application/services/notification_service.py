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

TELEGRAM_MESSAGE_LIMIT = 4096


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
        chat_ids = self.parse_chat_ids(chat_id)
        if not token or not chat_ids:
            self._log("NotificationService: Telegram не настроен, уведомление пропущено.")
            return False

        if proxy and not self.is_valid_proxy(proxy):
            self._log(f"NotificationService: некорректный прокси '{proxy}', отправка без прокси.")
            proxy = ""

        text = self.build_optimization_message(summary)
        if background:
            thread = threading.Thread(
                target=self._send_to_all,
                args=(token, chat_ids, text, proxy),
                name="telegram-notify",
                daemon=True,
            )
            thread.start()
            return True
        return self._send_to_all(token, chat_ids, text, proxy)

    def _send_to_all(self, token: str, chat_ids: list[str], text: str, proxy: str) -> bool:
        """Отправляет сообщение (или несколько частей) всем получателям. True, если хотя бы один успех."""
        chunks = self.split_message_for_telegram(text)
        any_sent = False
        for chat_id in chat_ids:
            chat_sent = all(
                self._notifier.send_message(token, chat_id, chunk, proxy) for chunk in chunks
            )
            if chat_sent:
                any_sent = True
            else:
                self._log(f"NotificationService: не удалось отправить уведомление для chat_id '{chat_id}'.")
        return any_sent

    @staticmethod
    def parse_chat_ids(raw: object) -> list[str]:
        """Разбирает строку получателей в список chat_id.

        Идентификаторы можно перечислять через запятую, точку с запятой, пробелы
        или переводы строк. Дубликаты убираются с сохранением порядка.
        """
        text = str(raw or "")
        seen: set[str] = set()
        result: list[str] = []
        for chunk in re.split(r"[\s,;]+", text):
            chat_id = chunk.strip()
            if chat_id and chat_id not in seen:
                seen.add(chat_id)
                result.append(chat_id)
        return result

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

        total_done = search_done + maps_done
        total_target = search_target + maps_target

        started_display, finished_display, duration_display = NotificationService._extract_timing(summary)

        cards = summary.get("cards", [])
        typed_cards = cards if isinstance(cards, list) else []

        completed_cards, partial_cards, idle_cards = NotificationService._count_card_states(typed_cards)

        if summary.get("stopped_by_user"):
            header = "<b>🛑 Оптимизация остановлена пользователем</b>"
        else:
            header = "<b>✅ Оптимизация завершена</b>"
        lines = [header]
        if started_display:
            lines.append(f"🟢 Начало работы: <i>{html.escape(started_display)}</i>")
        lines.append(f"🔴 Завершение: <i>{html.escape(finished_display)}</i>")
        if duration_display:
            lines.append(f"⏱ Затрачено времени: <b>{html.escape(duration_display)}</b>")
        lines.extend([
            "",
            f"📇 Обработано карточек: <b>{processed}</b>",
            f"🔍 Переходы в поиске: <b>{search_done}/{search_target}</b>"
            f"{NotificationService._percent_suffix(search_done, search_target)}",
            f"🗺 Переходы в карты: <b>{maps_done}/{maps_target}</b>"
            f"{NotificationService._percent_suffix(maps_done, maps_target)}",
            f"🎯 Всего действий: <b>{total_done}/{total_target}</b>"
            f"{NotificationService._percent_suffix(total_done, total_target)}",
            f"⚠️ Не выполнено действий: <b>{total_failed}</b>",
            "",
            "<b>📊 Карточки по статусу:</b>",
            f"✅ Полностью: <b>{completed_cards}</b>",
            f"🟡 Частично: <b>{partial_cards}</b>",
            f"⛔️ Без результата: <b>{idle_cards}</b>",
        ])
        action_totals = summary.get("total_action_counts", {})
        typed_action_totals = action_totals if isinstance(action_totals, dict) else {}

        card_lines = NotificationService._build_card_lines(typed_cards)
        search_lines = NotificationService._build_mode_lines(typed_cards, mode="search", label="поиск")
        maps_lines = NotificationService._build_mode_lines(typed_cards, mode="maps", label="карты")
        action_lines = NotificationService._build_action_total_lines(typed_action_totals)
        failed_lines = NotificationService._build_failed_lines(typed_cards)
        key_failure_lines = NotificationService._build_key_failure_lines(summary)

        if action_lines:
            lines.append("")
            lines.append("<b>🎯 Целевые действия:</b>")
            lines.extend(action_lines)

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

        if key_failure_lines:
            lines.append("")
            lines.append("<b>⚠️ Неудачи по ключам:</b>")
            lines.extend(key_failure_lines)

        return "\n".join(lines)

    @staticmethod
    def split_message_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
        """Разбивает текст на части, не превышающие лимит длины сообщения Telegram."""
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        current_lines: list[str] = []
        current_length = 0

        for line in text.split("\n"):
            prefix = 1 if current_lines else 0
            projected = current_length + prefix + len(line)
            if current_lines and projected > limit:
                chunks.append("\n".join(current_lines))
                current_lines = [line]
                current_length = len(line)
                continue
            if len(line) > limit:
                if current_lines:
                    chunks.append("\n".join(current_lines))
                    current_lines = []
                    current_length = 0
                chunks.extend(line[index : index + limit] for index in range(0, len(line), limit))
                continue
            current_lines.append(line)
            current_length = projected

        if current_lines:
            chunks.append("\n".join(current_lines))
        return chunks or [text[:limit]]

    @staticmethod
    def _percent_suffix(done: int, target: int) -> str:
        """Возвращает строку вида ' (80%)' для пары done/target или '' если цели нет."""
        if target <= 0:
            return ""
        percent = int(round(min(done, target) * 100 / target))
        return f" ({percent}%)"

    @staticmethod
    def _parse_iso(value: object) -> datetime.datetime | None:
        """Парсит ISO-строку времени, возвращая None при ошибке/отсутствии."""
        if not value:
            return None
        try:
            return datetime.datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Форматирует длительность в человекочитаемый вид (ч/мин/сек)."""
        total = max(0, int(round(seconds)))
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        parts: list[str] = []
        if hours:
            parts.append(f"{hours} ч")
        if minutes:
            parts.append(f"{minutes} мин")
        parts.append(f"{secs} сек")
        return " ".join(parts)

    @staticmethod
    def _extract_timing(summary: dict[str, object]) -> tuple[str, str, str]:
        """Возвращает отображаемые строки (начало, завершение, длительность).

        Если время начала/завершения не передано — завершение берется как
        текущий момент, а начало и длительность опускаются (обратная совместимость).
        """
        time_format = "%d.%m.%Y %H:%M:%S"
        started_dt = NotificationService._parse_iso(summary.get("started_at"))
        finished_dt = NotificationService._parse_iso(summary.get("finished_at"))
        if finished_dt is None:
            finished_dt = datetime.datetime.now()

        started_display = started_dt.strftime(time_format) if started_dt else ""
        finished_display = finished_dt.strftime(time_format)

        duration_seconds = summary.get("duration_seconds")
        if duration_seconds is None and started_dt is not None:
            duration_seconds = (finished_dt - started_dt).total_seconds()

        duration_display = ""
        if duration_seconds is not None:
            try:
                duration_display = NotificationService._format_duration(float(duration_seconds))
            except (TypeError, ValueError):
                duration_display = ""

        return started_display, finished_display, duration_display

    @staticmethod
    def _count_card_states(cards: list[dict[str, object]]) -> tuple[int, int, int]:
        """Считает карточки по статусу: полностью / частично / без результата.

        Учитываются только карточки, у которых была хотя бы одна цель.
        """
        completed = 0
        partial = 0
        idle = 0
        for card in cards:
            if not isinstance(card, dict):
                continue
            search_target = int(card.get("search_target", 0) or 0)
            maps_target = int(card.get("maps_target", 0) or 0)
            total_target = search_target + maps_target
            if total_target <= 0:
                continue
            search_done = int(card.get("search_performed", 0) or 0)
            maps_done = int(card.get("maps_performed", 0) or 0)
            total_done = min(search_done, search_target) + min(maps_done, maps_target)
            if total_done <= 0:
                idle += 1
            elif total_done >= total_target:
                completed += 1
            else:
                partial += 1
        return completed, partial, idle

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
        effect_key = f"{mode}_effect_keys"
        for card in cards:
            if not isinstance(card, dict):
                continue
            target = int(card.get(target_key, 0) or 0)
            if target <= 0:
                continue
            done = int(card.get(done_key, 0) or 0)
            title = str(card.get("organization") or card.get("card_name") or "Без названия").strip()
            title = html.escape(title) or "Без названия"

            percent = NotificationService._percent_suffix(done, target)
            effect_keys = card.get(effect_key, [])
            keys_count = len(effect_keys) if isinstance(effect_keys, list) else 0
            keys_suffix = f" · результативных ключей: {keys_count}" if keys_count else ""
            lines.append(f"• <b>{title}</b>: {label} {done}/{target}{percent}{keys_suffix}")
        return lines

    _ACTION_DISPLAY: list[tuple[str, str]] = [
        ("Показать телефон", "📞 Показать телефон"),
        ("Сайт", "🌐 Сайт"),
        ("Маршрут", "🧭 Маршрут"),
        ("мессенджер", "💬 Мессенджеры"),
        ("Записаться", "📝 Запись"),
    ]

    @staticmethod
    def _build_action_total_lines(action_totals: dict[str, object]) -> list[str]:
        """Формирует строки статистики по целевым действиям в фиксированном порядке."""
        lines: list[str] = []
        shown_keys: set[str] = set()
        for key, label in NotificationService._ACTION_DISPLAY:
            count = int(action_totals.get(key, 0) or 0)
            if count <= 0:
                continue
            shown_keys.add(key)
            lines.append(f"• {label}: <b>{count}</b>")
        for key, value in action_totals.items():
            if key in shown_keys:
                continue
            count = int(value or 0)
            if count <= 0:
                continue
            lines.append(f"• {html.escape(str(key))}: <b>{count}</b>")
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
    def _mode_label(mode: object) -> str:
        """Возвращает подпись режима для отчёта."""
        if mode == "search":
            return "поиск"
        if mode == "maps":
            return "карты"
        return html.escape(str(mode or ""))

    @staticmethod
    def _build_key_failure_lines(summary: dict[str, object]) -> list[str]:
        """Формирует строки отчёта по ключам с хотя бы одной неудачной попыткой."""
        reports = summary.get("key_failure_reports")
        if not isinstance(reports, list) or not reports:
            reports = summary.get("exhausted_keys", [])
        if not isinstance(reports, list):
            return []

        return [
            (
                f"• <b>{html.escape(str(entry.get('card_name') or 'Без названия'))}</b> · "
                f"{html.escape(str(entry.get('phrase') or entry.get('key_id') or ''))} · "
                f"{NotificationService._mode_label(entry.get('mode'))} · "
                f"<b>{int(entry.get('failures', 0) or 0)}</b> неудач"
            )
            for entry in reports
            if isinstance(entry, dict) and int(entry.get("failures", 0) or 0) > 0
        ]

    @staticmethod
    def _default_log(message: str) -> None:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] [NotificationService] {message}", flush=True)
