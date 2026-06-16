from __future__ import annotations

from dataclasses import dataclass

from shagteampro.application.captcha.solvers.image_coordinate import ImageCoordinateCaptchaSolver


@dataclass(frozen=True)
class CapsolaCaptchaSolver(ImageCoordinateCaptchaSolver):
    """Решатель Yandex SmartCaptcha через API Capsola (https://capsola.cloud)."""

    name: str = "capsola"
    service_label: str = "Capsola"

    def _create_task(self, page, main_b64: str, task_b64: str) -> str | None:
        """Создаёт задачу SmartCaptcha в Capsola и возвращает её идентификатор."""
        try:
            resp = page.context.request.post(
                "https://api.capsola.cloud/create",
                data={
                    "type": "SmartCaptcha",
                    "api_key": self.token,
                    "image": main_b64,
                    "question": task_b64,
                },
            )
            if not resp.ok:
                self._log(f"Ошибка Capsola create: {resp.status} {resp.status_text}")
                return None
            data = resp.json()
            if data.get("error") == 0 and "id" in data:
                return str(data["id"])
            self._log(f"Ответ с ошибкой от Capsola: {data}")
            return None
        except Exception as error:
            self._log(f"Исключение при POST в Capsola: {error}")
            return None

    def _wait_for_result(self, page, task_id: str) -> str | None:
        """Опрашивает Capsola до готовности решения и возвращает строку с координатами."""
        for _ in range(self.poll_attempts):
            try:
                resp = page.context.request.post(
                    "https://api.capsola.cloud/result",
                    data={"api_key": self.token, "id": task_id},
                )
                if resp.ok:
                    data = resp.json()
                    status = data.get("status")
                    if status == "ready":
                        return data.get("solution")
                    if status == "processing":
                        page.wait_for_timeout(self.poll_interval_ms)
                        continue
                    self._log(f"Неизвестный статус/ошибка из Capsola: {data}")
                    return None
            except Exception as error:
                self._log(f"Исключение при опросе результата: {error}")
            page.wait_for_timeout(self.poll_interval_ms)
        return None
