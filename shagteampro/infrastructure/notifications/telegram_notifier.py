from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable


class TelegramNotifier:
    """Тонкий HTTP-клиент Telegram Bot API для отправки текстовых уведомлений.

    Использует стандартную библиотеку (urllib), чтобы не добавлять зависимостей.
    Любая ошибка сети/ответа гасится и возвращается False — отправка уведомления
    не должна влиять на основной сценарий приложения.
    """

    API_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, timeout: float = 10.0, logger: Callable[[str], None] | None = None) -> None:
        self._timeout = timeout
        self._log = logger or (lambda _message: None)

    def send_message(self, token: str, chat_id: str, text: str, proxy: str = "") -> bool:
        """Отправляет одно сообщение в чат. Возвращает True при успехе.

        proxy — необязательный URL прокси вида ``scheme://[user:pass@]host:port``
        (поддерживаются схемы http/https). Если задан, запрос идет через прокси.
        """
        token = (token or "").strip()
        chat_id = (chat_id or "").strip()
        if not token or not chat_id:
            self._log("TelegramNotifier: токен или chat_id не заданы, отправка пропущена.")
            return False

        url = self.API_URL_TEMPLATE.format(token=token)
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = self._build_opener(proxy)
        try:
            with opener.open(request, timeout=self._timeout) as response:
                ok = 200 <= int(response.status) < 300
                if ok:
                    self._log("TelegramNotifier: уведомление отправлено успешно.")
                else:
                    self._log(f"TelegramNotifier: Telegram вернул статус {response.status}.")
                return ok
        except urllib.error.HTTPError as error:
            details = ""
            try:
                details = error.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            self._log(f"TelegramNotifier: HTTP-ошибка {error.code}. {details}")
            return False
        except Exception as error:
            self._log(f"TelegramNotifier: ошибка отправки уведомления: {error}")
            return False

    @staticmethod
    def _build_opener(proxy: str) -> urllib.request.OpenerDirector:
        """Создает opener c прокси, если задан валидный URL, иначе прямой."""
        proxy = (proxy or "").strip()
        if not proxy:
            return urllib.request.build_opener()
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        return urllib.request.build_opener(handler)
