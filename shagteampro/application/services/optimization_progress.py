from __future__ import annotations

import datetime
import threading
import uuid
from typing import Any

_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}

ACTION_CLICK_KEYS: tuple[tuple[str, str], ...] = (
    ("Показать телефон", "tel"),
    ("Сайт", "site"),
    ("Маршрут", "route"),
    ("мессенджер", "msg"),
    ("Записаться", "story"),
)


def count_enabled_key_phrases(cards: list[dict[str, object]]) -> int:
    total = 0
    for card in cards:
        keys = card.get("keys", [])
        if not isinstance(keys, list):
            continue
        for key in keys:
            if not isinstance(key, dict):
                continue
            if bool(key.get("search_enabled")) or bool(key.get("maps_enabled")):
                total += 1
    return total


def count_total_work_units(cards: list[dict[str, object]]) -> int:
    total = 0
    for card in cards:
        keys = card.get("keys", [])
        if not isinstance(keys, list):
            continue
        has_search = any(
            isinstance(key, dict) and bool(key.get("search_enabled"))
            for key in keys
        )
        has_maps = any(
            isinstance(key, dict) and bool(key.get("maps_enabled"))
            for key in keys
        )
        if has_search:
            total += int(card.get("search_target", 0) or 0)
        if has_maps:
            total += int(card.get("maps_target", 0) or 0)
    return total


def _empty_card_stats() -> dict[str, Any]:
    return {
        "failures": 0,
        "in_flight": 0,
        "search_performed": 0,
        "maps_performed": 0,
        "clicks": {short: 0 for _, short in ACTION_CLICK_KEYS},
    }


def create_run(threads: int, cards: list[dict[str, object]]) -> str:
    run_id = uuid.uuid4().hex
    card_names: dict[int, str] = {}
    card_stats: dict[int, dict[str, Any]] = {}
    for card in cards:
        card_id = card.get("card_id")
        if card_id is None:
            continue
        card_id_int = int(card_id)
        card_names[card_id_int] = str(card.get("card_name") or card.get("organization") or "Без названия")
        card_stats[card_id_int] = _empty_card_stats()

    with _lock:
        _runs[run_id] = {
            "status": "running",
            "dispatch_control": "active",
            "stopped_by_user": False,
            "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "threads": int(threads),
            "total_key_phrases": count_enabled_key_phrases(cards),
            "total_work_units": count_total_work_units(cards),
            "card_names": card_names,
            "card_stats": card_stats,
            "summary": None,
            "error": None,
        }
    return run_id


def get_dispatch_control(run_id: str) -> str | None:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return None
        return str(run.get("dispatch_control", "active"))


def is_dispatch_allowed(run_id: str | None) -> bool:
    if not run_id:
        return True
    return get_dispatch_control(run_id) == "active"


def pause_run(run_id: str) -> bool:
    with _lock:
        run = _runs.get(run_id)
        if run is None or run.get("status") != "running":
            return False
        if run.get("dispatch_control") != "active":
            return False
        run["dispatch_control"] = "paused"
        return True


def resume_run(run_id: str) -> bool:
    with _lock:
        run = _runs.get(run_id)
        if run is None or run.get("status") != "running":
            return False
        if run.get("dispatch_control") != "paused":
            return False
        run["dispatch_control"] = "active"
        return True


def request_stop_run(run_id: str) -> bool:
    with _lock:
        run = _runs.get(run_id)
        if run is None or run.get("status") != "running":
            return False
        if run.get("dispatch_control") == "stopping":
            return True
        run["dispatch_control"] = "stopping"
        run["stopped_by_user"] = True
        return True


def was_stopped_by_user(run_id: str | None) -> bool:
    if not run_id:
        return False
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return False
        return bool(run.get("stopped_by_user"))


def _extract_clicks(action_counts: object) -> dict[str, int]:
    clicks = {short: 0 for _, short in ACTION_CLICK_KEYS}
    if not isinstance(action_counts, dict):
        return clicks
    for label, short in ACTION_CLICK_KEYS:
        clicks[short] = int(action_counts.get(label, 0) or 0)
    return clicks


def update_run_from_targets(
    run_id: str | None,
    *,
    targets: list[dict[str, object]],
    card_results: dict[int, dict[str, object]],
    action_completed: bool = False,
) -> None:
    if not run_id:
        return

    with _lock:
        run = _runs.get(run_id)
        if run is None or run["status"] != "running":
            return

        for card_id_value, card_entry in card_results.items():
            stats = run["card_stats"].setdefault(int(card_id_value), _empty_card_stats())
            stats["search_performed"] = int(card_entry.get("search_performed", 0) or 0)
            stats["maps_performed"] = int(card_entry.get("maps_performed", 0) or 0)
            stats["clicks"] = _extract_clicks(card_entry.get("maps_action_counts"))

        per_card: dict[int, dict[str, int]] = {}
        for state in targets:
            cid = int(state["card_id"])
            bucket = per_card.setdefault(cid, {"failures": 0, "in_flight": 0})
            bucket["failures"] += int(state.get("failures", 0) or 0)
            bucket["in_flight"] += int(state.get("in_flight", 0) or 0)

        for cid, bucket in per_card.items():
            stats = run["card_stats"].setdefault(cid, _empty_card_stats())
            stats["failures"] = bucket["failures"]
            stats["in_flight"] = bucket["in_flight"]


def finish_run(run_id: str, *, summary: dict[str, object] | None = None, error: str | None = None) -> None:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        if error:
            run["status"] = "error"
            run["error"] = error
            return
        run["status"] = "done"
        run["summary"] = summary
        if summary and summary.get("started_at"):
            run["started_at"] = str(summary["started_at"])
        if summary:
            cards = summary.get("cards", [])
            if isinstance(cards, list):
                for card in cards:
                    if not isinstance(card, dict):
                        continue
                    card_id = int(card.get("card_id", 0) or 0)
                    stats = run["card_stats"].setdefault(card_id, _empty_card_stats())
                    stats["search_performed"] = int(card.get("search_performed", 0) or 0)
                    stats["maps_performed"] = int(card.get("maps_performed", 0) or 0)
                    stats["clicks"] = _extract_clicks(card.get("maps_action_counts"))


def _count_successful_transitions(run: dict[str, Any]) -> tuple[int, int, int]:
    """Возвращает (успешно всего, поиск, карты) по card_stats."""
    search_performed = 0
    maps_performed = 0
    for card_id in run.get("card_names", {}):
        stats = run.get("card_stats", {}).get(card_id, {})
        search_performed += int(stats.get("search_performed", 0) or 0)
        maps_performed += int(stats.get("maps_performed", 0) or 0)
    return search_performed + maps_performed, search_performed, maps_performed


def _count_failed_attempts(run: dict[str, Any]) -> int:
    return sum(
        int(run.get("card_stats", {}).get(card_id, {}).get("failures", 0) or 0)
        for card_id in run.get("card_names", {})
    )


def get_run_snapshot(run_id: str) -> dict[str, object] | None:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return None
        return _serialize_run(run)


def _serialize_run(run: dict[str, Any]) -> dict[str, object]:
    started_at = run.get("started_at")
    started_dt = None
    if started_at:
        try:
            started_dt = datetime.datetime.fromisoformat(str(started_at))
        except (TypeError, ValueError):
            started_dt = None

    successful, search_performed, maps_performed = _count_successful_transitions(run)
    elapsed_seconds = 0.0
    if started_dt is not None:
        elapsed_seconds = max(0.0, (datetime.datetime.now() - started_dt).total_seconds())
    elif run.get("summary") and run["summary"].get("duration_seconds") is not None:
        elapsed_seconds = float(run["summary"]["duration_seconds"])

    processed = successful
    total = int(run.get("total_key_phrases", 0) or 0)
    total_work_units = int(run.get("total_work_units", 0) or 0)
    failed_attempts = _count_failed_attempts(run)
    avg_seconds = elapsed_seconds / successful if successful > 0 else 0.0
    remaining = max(0, total_work_units - successful) * avg_seconds if successful > 0 else 0.0

    card_names: dict[int, str] = run.get("card_names", {})
    card_stats: dict[int, dict[str, Any]] = run.get("card_stats", {})
    cards: list[dict[str, object]] = []
    for card_id in sorted(card_names):
        stats = card_stats.get(card_id, _empty_card_stats())
        clicks = stats.get("clicks") or {short: 0 for _, short in ACTION_CLICK_KEYS}
        search_done = int(stats.get("search_performed", 0) or 0)
        maps_done = int(stats.get("maps_performed", 0) or 0)
        cards.append(
            {
                "card_id": card_id,
                "card_name": card_names.get(card_id, "Без названия"),
                "performed": search_done + maps_done,
                "failures": int(stats.get("failures", 0) or 0),
                "in_flight": int(stats.get("in_flight", 0) or 0),
                "search_performed": search_done,
                "maps_performed": maps_done,
                "clicks": dict(clicks),
            }
        )

    search_performed = sum(int(item.get("search_performed", 0) or 0) for item in cards)
    maps_performed = sum(int(item.get("maps_performed", 0) or 0) for item in cards)

    return {
        "status": run.get("status", "running"),
        "dispatch_control": run.get("dispatch_control", "active"),
        "stopped_by_user": bool(run.get("stopped_by_user")),
        "started_at": started_at,
        "threads": int(run.get("threads", 0) or 0),
        "total_key_phrases": total,
        "total_work_units": total_work_units,
        "processed_key_phrases": processed,
        "search_performed": search_performed,
        "maps_performed": maps_performed,
        "total_successful": search_performed + maps_performed,
        "total_failed_attempts": failed_attempts,
        "elapsed_seconds": elapsed_seconds,
        "avg_seconds_per_phrase": avg_seconds,
        "avg_seconds_per_work_unit": avg_seconds,
        "estimated_remaining_seconds": remaining if run.get("status") == "running" else 0.0,
        "cards": cards,
        "summary": run.get("summary"),
        "error": run.get("error"),
    }


def build_snapshot_from_summary(
    summary: dict[str, object],
    *,
    threads: int,
    cards_payload: list[dict[str, object]],
) -> dict[str, object]:
    cards: list[dict[str, object]] = []
    cards_raw = summary.get("cards", [])
    if isinstance(cards_raw, list):
        for card in cards_raw:
            if not isinstance(card, dict):
                continue
            search_done = int(card.get("search_performed", 0) or 0)
            maps_done = int(card.get("maps_performed", 0) or 0)
            cards.append(
                {
                    "card_id": int(card.get("card_id", 0) or 0),
                    "card_name": str(card.get("card_name") or card.get("organization") or "Без названия"),
                    "performed": search_done + maps_done,
                    "failures": max(
                        0,
                        int(card.get("search_target", 0) or 0) - search_done,
                    )
                    + max(
                        0,
                        int(card.get("maps_target", 0) or 0) - maps_done,
                    ),
                    "in_flight": 0,
                    "search_performed": search_done,
                    "maps_performed": maps_done,
                    "clicks": _extract_clicks(card.get("maps_action_counts")),
                }
            )

    duration_seconds = float(summary.get("duration_seconds") or 0)
    successful = int(summary.get("total_search_performed", 0) or 0) + int(summary.get("total_maps_performed", 0) or 0)
    total = count_enabled_key_phrases(cards_payload)
    total_work_units = count_total_work_units(cards_payload)
    search_performed = int(summary.get("total_search_performed", 0) or 0)
    maps_performed = int(summary.get("total_maps_performed", 0) or 0)
    failed_attempts = sum(int(item.get("failures", 0) or 0) for item in cards)

    return {
        "status": "done",
        "started_at": summary.get("started_at"),
        "threads": threads,
        "total_key_phrases": total,
        "total_work_units": total_work_units,
        "processed_key_phrases": successful,
        "search_performed": search_performed,
        "maps_performed": maps_performed,
        "total_successful": successful,
        "total_failed_attempts": failed_attempts,
        "elapsed_seconds": duration_seconds,
        "avg_seconds_per_phrase": duration_seconds / successful if successful > 0 else 0.0,
        "avg_seconds_per_work_unit": duration_seconds / successful if successful > 0 else 0.0,
        "estimated_remaining_seconds": 0.0,
        "cards": cards,
        "summary": summary,
        "error": None,
    }
