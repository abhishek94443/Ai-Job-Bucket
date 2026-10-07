import pytest
from src.batch_window_optimizer import (
    estimate_tokens_from_chars,
    calculate_window_metrics,
    partition_staged_jobs_into_windows,
)


def test_estimate_tokens_from_chars():
    assert estimate_tokens_from_chars(400) == 100
    assert estimate_tokens_from_chars(0) == 1
    assert estimate_tokens_from_chars(15) == 4


def test_calculate_window_metrics_empty():
    res = calculate_window_metrics([])
    assert res["total_staged"] == 0
    assert res["total_chars"] == 0
    assert res["estimated_windows_count"] == 0


def test_calculate_window_metrics_normal():
    jobs = [
        {"id": 1, "char_count": 3000},
        {"id": 2, "char_count": 4000},
        {"id": 3, "char_count": 5000},
    ]
    res = calculate_window_metrics(jobs, target_batch_size=5)
    assert res["total_staged"] == 3
    assert res["total_chars"] == 12000
    assert res["average_chars_per_job"] == 4000
    assert res["recommended_batch_size"] == 5
    assert res["estimated_windows_count"] == 1


def test_partition_staged_jobs_normal_batch():
    jobs = [{"id": i, "char_count": 2000} for i in range(12)]
    # Target batch size 5, default max window capacity fits 5 jobs easily (5 * 2000 = 10000 <= 23000 usable)
    windows = partition_staged_jobs_into_windows(jobs, target_batch_size=5)
    assert len(windows) == 3
    assert len(windows[0]) == 5
    assert len(windows[1]) == 5
    assert len(windows[2]) == 2


def test_partition_staged_jobs_adaptive_shrinking():
    # Massive job descriptions: each is 15,000 characters
    jobs = [{"id": i, "char_count": 15000} for i in range(4)]
    # With base overhead 12,000 and max 35,000, usable capacity is 23,000.
    # Two 15,000 jobs would be 30,000 > 23,000.
    # Therefore the window MUST shrink to 1 job per window!
    windows = partition_staged_jobs_into_windows(
        jobs, target_batch_size=5, max_window_chars=35000, base_context_chars=12000
    )
    assert len(windows) == 4
    for w in windows:
        assert len(w) == 1
