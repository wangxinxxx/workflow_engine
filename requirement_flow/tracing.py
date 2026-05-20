import os
from typing import Any, Dict, Mapping, Optional

from .llm import ensure_env_loaded


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def tracing_enabled() -> bool:
    ensure_env_loaded()
    return (
        _env_flag("LANGSMITH_TRACING")
        or _env_flag("LANGSMITH_TRACING_V2")
        or _env_flag("LANGCHAIN_TRACING_V2")
    )


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def thread_metadata(
    state_or_snapshot: Mapping[str, Any],
    *,
    operation: str = "",
    node_id: str = "",
    stage: str = "",
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "workflow_type": _safe_text(state_or_snapshot.get("workflow_type")) or "sql_modify",
        "thread_id": _safe_text(state_or_snapshot.get("thread_id")),
        "tapd_id": _safe_text(state_or_snapshot.get("tapd_id")),
        "tapd_url": _safe_text(state_or_snapshot.get("tapd_url")),
        "requirement_name": _safe_text(
            state_or_snapshot.get("requirement_name") or state_or_snapshot.get("title")
        ),
        "current_step": _safe_text(state_or_snapshot.get("current_step")),
        "status": _safe_text(state_or_snapshot.get("status")),
    }
    if operation:
        metadata["operation"] = operation
    if node_id:
        metadata["node_id"] = node_id
    if stage:
        metadata["stage"] = stage
    return {key: value for key, value in metadata.items() if value}


def thread_tags(
    state_or_snapshot: Mapping[str, Any],
    *,
    operation: str = "",
    node_id: str = "",
    stage: str = "",
    extra: Optional[list[str]] = None,
) -> list[str]:
    tags = [
        "requirement-flow",
        _safe_text(state_or_snapshot.get("workflow_type")) or "sql_modify",
    ]
    thread_id = _safe_text(state_or_snapshot.get("thread_id"))
    if thread_id:
        tags.append(f"thread:{thread_id}")
    tapd_id = _safe_text(state_or_snapshot.get("tapd_id"))
    if tapd_id:
        tags.append(f"tapd:{tapd_id}")
    if operation:
        tags.append(f"op:{operation}")
    if node_id:
        tags.append(f"node:{node_id}")
    if stage:
        tags.append(f"stage:{stage}")
    if extra:
        tags.extend(tag for tag in extra if tag)
    return tags


def graph_run_config(state_or_snapshot: Mapping[str, Any], *, operation: str) -> Dict[str, Any]:
    thread_id = _safe_text(state_or_snapshot.get("thread_id"))
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": f"requirement_flow.{operation}",
        "tags": thread_tags(state_or_snapshot, operation=operation),
        "metadata": thread_metadata(state_or_snapshot, operation=operation),
    }


def node_metadata(node_def: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = {
        "workflow_type": "sql_modify",
        "node_id": _safe_text(node_def.get("id")),
        "node_label": _safe_text(node_def.get("label")),
        "stage": _safe_text(node_def.get("stage")),
        "artifact_key": _safe_text(node_def.get("artifact_key")),
        "node_type": _safe_text(node_def.get("type")),
    }
    return {key: value for key, value in metadata.items() if value}


def llm_run_config(state_or_snapshot: Mapping[str, Any], node_def: Mapping[str, Any]) -> Dict[str, Any]:
    node_id = _safe_text(node_def.get("id"))
    stage = _safe_text(node_def.get("stage"))
    metadata = thread_metadata(state_or_snapshot, operation="llm_invoke", node_id=node_id, stage=stage)
    artifact_key = _safe_text(node_def.get("artifact_key"))
    if artifact_key:
        metadata["artifact_key"] = artifact_key
    return {
        "run_name": f"llm.{node_id or 'node'}",
        "tags": thread_tags(
            state_or_snapshot,
            operation="llm_invoke",
            node_id=node_id,
            stage=stage,
            extra=["llm"],
        ),
        "metadata": metadata,
    }
