from __future__ import annotations

from dataclasses import dataclass

from shagteampro.application.captcha.solvers.image_coordinate import ImageCoordinateCaptchaSolver


@dataclass(frozen=True)
class BotlabCaptchaSolver(ImageCoordinateCaptchaSolver):
    """Решатель Yandex SmartCaptcha через сервис BotLab (https://ru.botlab.me).

    BotLab использует JSON-API: POST `/create` с заголовком `X-API-Key` и телом
    `{"type":"SmartCaptcha","click":<b64>,"task":<b64>}` создаёт задачу, а POST
    `/result` с телом `{"id":<id>}` опрашивается до готовности. Ответ содержит
    строку координат вида `coordinates:x=12,y=34;...`.
    """

    name: str = "botlab"
    service_label: str = "BotLab"
    base_url: str = "https://api.botlab.me"

    def _create_task(self, page, main_b64: str, task_b64: str) -> str | None:
        """Создаёт задачу SmartCaptcha в BotLab и возвращает её идентификатор."""
        try:
            resp = page.context.request.post(
                f"{self.base_url}/create",
                headers={"X-API-Key": self.token},
                data={"type": "SmartCaptcha", "click": main_b64, "task": task_b64},
            )
            if not resp.ok:
                self._log(f"Ошибка BotLab create: {resp.status} {resp.status_text}")
                return None
            data = resp.json()
            if str(data.get("status")) == "1" and data.get("response"):
                return str(data["response"])
            self._log(f"Ответ с ошибкой от BotLab: {data}")
            return None
        except Exception as error:
            self._log(f"Исключение при POST в BotLab: {error}")
            return None

    def _wait_for_result(self, page, task_id: str) -> str | None:
        """Опрашивает BotLab до готовности решения и возвращает строку с координатами."""
        for _ in range(self.poll_attempts):
            try:
                resp = page.context.request.post(
                    f"{self.base_url}/result",
                    data={"id": task_id},
                )
                if resp.ok:
                    data = resp.json()
                    response_value = str(data.get("response", ""))
                    if str(data.get("status")) == "1":
                        return response_value
                    if response_value == "CAPCHA_NOT_READY":
                        page.wait_for_timeout(self.poll_interval_ms)
                        continue
                    self._log(f"Ошибка из BotLab при опросе результата: {data}")
                    return None
            except Exception as error:
                self._log(f"Исключение при опросе результата: {error}")
            page.wait_for_timeout(self.poll_interval_ms)
        return None
