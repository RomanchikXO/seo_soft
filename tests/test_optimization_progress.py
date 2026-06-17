from __future__ import annotations

from shagteampro.application.services.optimization_progress import (
    count_total_work_units,
    create_run,
    finish_run,
    get_run_snapshot,
    update_run_from_targets,
)


def test_count_total_work_units_respects_enabled_modes() -> None:
    cards = [
        {
            "search_target": 4,
            "maps_target": 1,
            "keys": [{"search_enabled": True, "maps_enabled": False}],
        }
    ]
    assert count_total_work_units(cards) == 4


def test_optimization_progress_snapshot_tracks_cards_and_timing() -> None:
    cards_payload = [
        {
            "card_id": 1,
            "card_name": "Card A",
            "search_target": 2,
            "maps_target": 1,
            "keys": [{"id": 10, "search_enabled": True, "maps_enabled": False}],
        }
    ]
    run_id = create_run(3, cards_payload)
    card_results = {
        1: {
            "card_id": 1,
            "search_performed": 1,
            "maps_performed": 0,
            "maps_action_counts": {"Сайт": 2},
        }
    }
    targets = [
        {
            "card_id": 1,
            "mode": "search",
            "failures": 0,
            "in_flight": 1,
            "effect_key_ids": set(),
        }
    ]

    update_run_from_targets(
        run_id,
        targets=targets,
        card_results=card_results,
        action_completed=True,
    )
    snapshot = get_run_snapshot(run_id)

    assert snapshot is not None
    assert snapshot["status"] == "running"
    assert snapshot["threads"] == 3
    assert snapshot["total_key_phrases"] == 1
    assert snapshot["total_work_units"] == 2
    assert snapshot["processed_key_phrases"] == 1
    assert snapshot["total_successful"] == 1
    assert snapshot["cards"][0]["card_name"] == "Card A"
    assert snapshot["cards"][0]["performed"] == 1
    assert snapshot["cards"][0]["in_flight"] == 1
    assert snapshot["cards"][0]["clicks"]["site"] == 2

    finish_run(
        run_id,
        summary={
            "started_at": "2026-06-11T23:10:05",
            "duration_seconds": 125,
            "total_search_performed": 2,
            "total_maps_performed": 1,
            "cards": [
                {
                    "card_id": 1,
                    "card_name": "Card A",
                    "search_target": 2,
                    "maps_target": 1,
                    "search_performed": 2,
                    "maps_performed": 1,
                    "maps_action_counts": {"Маршрут": 1},
                }
            ],
        },
    )
    done = get_run_snapshot(run_id)
    assert done is not None
    assert done["status"] == "done"
    assert done["search_performed"] == 2
    assert done["maps_performed"] == 1
    assert done["estimated_remaining_seconds"] == 0.0


def test_processed_count_ignores_failed_attempts() -> None:
    cards_payload = [
        {
            "card_id": 1,
            "card_name": "Card A",
            "search_target": 4,
            "maps_target": 0,
            "keys": [{"id": 10, "search_enabled": True, "maps_enabled": False}],
        }
    ]
    run_id = create_run(2, cards_payload)
    card_results = {
        1: {
            "card_id": 1,
            "search_performed": 1,
            "maps_performed": 0,
            "maps_action_counts": {},
        }
    }
    targets = [
        {
            "card_id": 1,
            "mode": "search",
            "failures": 2,
            "in_flight": 0,
            "effect_key_ids": set(),
        }
    ]

    for _ in range(3):
        update_run_from_targets(
            run_id,
            targets=targets,
            card_results=card_results,
            action_completed=True,
        )

    snapshot = get_run_snapshot(run_id)
    assert snapshot is not None
    assert snapshot["total_work_units"] == 4
    assert snapshot["total_successful"] == 1
    assert snapshot["processed_key_phrases"] == 1
    assert snapshot["total_failed_attempts"] == 2
