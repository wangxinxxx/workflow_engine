import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .config import THREADS_DIR


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        return str(value)


def summarize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            result[str(key)] = summarize_for_log(item)
        return result
    if isinstance(value, list):
        return [summarize_for_log(item) for item in value]
    return value


def log_event(thread_id: str, event: str, payload: Dict[str, Any]) -> None:
    record = {
        "ts": datetime.now().astimezone().isoformat(),
        "thread_id": thread_id,
        "event": event,
        "payload": _json_safe(summarize_for_log(payload)),
    }
    text = json.dumps(record, ensure_ascii=False)
    print(f"[RF_DEBUG] {text}")
    # Single root log directory, one file per thread.
    logs_dir = THREADS_DIR.parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    target = logs_dir / f"{thread_id}.jsonl"
    with target.open("a", encoding="utf-8") as f:
        f.write(text + "\n")
