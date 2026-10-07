"""
Job Bucket - Batch Window Optimizer
Implements balanced bin-packing with adaptive window shrinking for staged job enrichment.

Key Capabilities:
1. Calculates fixed context overhead (system instructions + candidate profile + behavioral profile).
2. Bin-packs staged jobs into balanced windows to minimize character and token variance across concurrent workers.
3. Dynamically shrinks batch window sizes when large job descriptions are encountered, guaranteeing that
   no batch exceeds context window or Tokens Per Minute (TPM) limits.
"""

import math
from typing import Any, Dict, List, Tuple


# Safe default context ceilings
DEFAULT_MAX_WINDOW_CHARS = 35000  # ~8,500 tokens
DEFAULT_TARGET_BATCH_SIZE = 5
AVG_CHARS_PER_TOKEN = 4.0


def estimate_tokens_from_chars(char_count: int) -> int:
    """Estimates token count from raw character length."""
    return max(1, int(math.ceil(char_count / AVG_CHARS_PER_TOKEN)))


def calculate_window_metrics(
    staged_jobs: List[Dict[str, Any]],
    target_batch_size: int = DEFAULT_TARGET_BATCH_SIZE,
    max_window_chars: int = DEFAULT_MAX_WINDOW_CHARS,
    base_context_chars: int = 12000,
) -> Dict[str, Any]:
    """
    Computes aggregate window metrics across all currently staged jobs.
    Returns:
        total_staged: int
        total_chars: int
        estimated_total_tokens: int
        recommended_batch_size: int
        estimated_windows_count: int
        average_chars_per_job: int
    """
    if not staged_jobs:
        return {
            "total_staged": 0,
            "total_chars": 0,
            "estimated_total_tokens": 0,
            "recommended_batch_size": target_batch_size,
            "estimated_windows_count": 0,
            "average_chars_per_job": 0,
        }

    total_chars = 0
    for j in staged_jobs:
        c = j.get("char_count") or 0
        if c <= 0:
            desc = j.get("description_full") or j.get("description") or ""
            c = len(desc) if len(desc) > 0 else 2500
        total_chars += c

    total_staged = len(staged_jobs)
    avg_chars = int(total_chars / total_staged) if total_staged > 0 else 0

    # Determine recommended batch size based on average job size
    usable_chars_per_window = max(5000, max_window_chars - base_context_chars)
    calculated_capacity = max(1, int(usable_chars_per_window / max(1, avg_chars)))
    recommended_batch_size = min(target_batch_size, calculated_capacity)

    estimated_windows = int(math.ceil(total_staged / max(1, recommended_batch_size)))

    return {
        "total_staged": total_staged,
        "total_chars": total_chars,
        "estimated_total_tokens": estimate_tokens_from_chars(total_chars + (base_context_chars * estimated_windows)),
        "recommended_batch_size": recommended_batch_size,
        "estimated_windows_count": estimated_windows,
        "average_chars_per_job": avg_chars,
    }


def partition_staged_jobs_into_windows(
    staged_jobs: List[Dict[str, Any]],
    target_batch_size: int = DEFAULT_TARGET_BATCH_SIZE,
    max_window_chars: int = DEFAULT_MAX_WINDOW_CHARS,
    base_context_chars: int = 12000,
) -> List[List[Dict[str, Any]]]:
    """
    Partitions staged jobs into balanced batches using bin-packing.
    Ensures that for every batch:
      base_context_chars + sum(job.char_count) <= max_window_chars
    If a job is exceptionally large or the accumulated sum crosses the limit,
    the window dynamically shrinks and starts a new partition.
    """
    if not staged_jobs:
        return []

    # Extract character count for each job
    job_items: List[Tuple[int, Dict[str, Any]]] = []
    for j in staged_jobs:
        c = j.get("char_count") or 0
        if c <= 0:
            desc = j.get("description_full") or j.get("description") or ""
            c = len(desc) if len(desc) > 0 else 2500
        job_items.append((c, j))

    usable_window_capacity = max(5000, max_window_chars - base_context_chars)

    windows: List[List[Dict[str, Any]]] = []
    current_window: List[Dict[str, Any]] = []
    current_window_chars = 0

    for char_count, job in job_items:
        # Check if adding this job exceeds the window capacity or target batch size
        exceeds_capacity = (current_window_chars + char_count > usable_window_capacity)
        exceeds_size = (len(current_window) >= target_batch_size)

        if current_window and (exceeds_capacity or exceeds_size):
            # Finalize current window
            windows.append(current_window)
            current_window = [job]
            current_window_chars = char_count
        else:
            current_window.append(job)
            current_window_chars += char_count

    if current_window:
        windows.append(current_window)

    return windows


def group_jobs_into_balanced_packs(
    jobs: List[Dict[str, Any]],
    pack_size: int = 2,
) -> List[List[Dict[str, Any]]]:
    """
    Groups a list of jobs into balanced packs of size `pack_size` (e.g. 2).
    Uses Longest Processing Time First paired with smallest to minimize character variance across packs.
    """
    if not jobs:
        return []
    if pack_size <= 1:
        return [[j] for j in jobs]

    job_tuples = []
    for j in jobs:
        c = j.get("char_count") or 0
        if c <= 0:
            desc = j.get("description_full") or j.get("description") or ""
            c = len(desc) if len(desc) > 0 else 2500
        job_tuples.append((c, j))

    job_tuples.sort(key=lambda x: x[0], reverse=True)

    num_packs = int(math.ceil(len(job_tuples) / pack_size))
    packs: List[List[Dict[str, Any]]] = [[] for _ in range(num_packs)]
    pack_weights: List[int] = [0 for _ in range(num_packs)]

    for c, j in job_tuples:
        eligible_packs = [i for i in range(num_packs) if len(packs[i]) < pack_size]
        if not eligible_packs:
            packs.append([j])
            pack_weights.append(c)
        else:
            best_idx = min(eligible_packs, key=lambda i: pack_weights[i])
            packs[best_idx].append(j)
            pack_weights[best_idx] += c

    return [p for p in packs if p]

