import json
from pathlib import Path
from datetime import datetime
from typing import List, Union
from .models import JobRecord


def export_jobs_to_json(jobs: List[JobRecord], output_dir: Union[str, Path] = "data") -> Path:
    """
    Serializes a list of JobRecord models to a timestamped JSON file.
    Creates parent directories automatically if they do not exist.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_name = f"discovered_jobs_{timestamp_str}.json"
    file_path = out_path / file_name

    # Serialize pydantic models to dict/json
    records_data = [job.model_dump(mode="json") for job in jobs]

    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "total_jobs": len(records_data),
        "jobs": records_data
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    return file_path
