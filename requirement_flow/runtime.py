
import json
import os
import re
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import TEMPLATE_DIR, THREADS_DIR, resolve_requirement_dir
from .file_store import (
    read_requirement_files,
    render_requirement_readme,
    sync_requirement_template,
    thread_runtime_dir,
    write_requirement_files,
)
from .graph import build_requirement_graph
from .skill_registry import match_preferred_skills
from .workflow_defs import SQL_MODIFY_WORKFLOW_ID, get_workflow_definition


def thread_state_path(thread_id: str) -> Path:
    return thread_runtime_dir(thread_id) / "latest_state.json"


def thread_interrupt_path(thread_id: str) -> Path:
    return thread_runtime_dir(thread_id) / "latest_interrupt.json"


def read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _allowed_artifact_keys(workflow_type: str) -> set[str]:
    keys = {"README.md"}
    for node in get_workflow_definition(workflow_type)["nodes"]:
        artifact_key = str(node.get("artifact_key", "") or "").strip()
        if artifact_key:
            keys.add(artifact_key)
    return keys


def _migrate_artifacts(state: Dict[str, Any]) -> Dict[str, str]:
    workflow_type = str(state.get("workflow_type", SQL_MODIFY_WORKFLOW_ID) or SQL_MODIFY_WORKFLOW_ID)
    allowed = _allowed_artifact_keys(workflow_type)
    raw_artifacts = dict(state.get("artifacts", {}) or {})

    dev_doc = str(raw_artifacts.get("docs/开发文档.md", "") or "").strip()
    old_summary = str(raw_artifacts.get("00_summary.md", "") or "").strip()
    old_tasks = str(raw_artifacts.get("03_tasks.md", "") or "").strip()
    if not dev_doc and (old_summary or old_tasks):
        sections = ["# 开发文档"]
        if old_summary:
            sections.extend(["", "## 历史需求确认迁移", "", old_summary])
        if old_tasks:
            sections.extend(["", "## 历史任务清单迁移", "", old_tasks])
        raw_artifacts["docs/开发文档.md"] = "\n".join(sections).strip() + "\n"

    if not str(raw_artifacts.get("draft_sql/modified.sql", "") or "").strip():
        old_draft = str(raw_artifacts.get("delivery/sql/modified.sql", "") or "").strip()
        if old_draft:
            raw_artifacts["draft_sql/modified.sql"] = old_draft

    if not str(raw_artifacts.get("自测报告.md", "") or "").strip():
        old_report = str(raw_artifacts.get("delivery/self_test_report.md", "") or "").strip()
        if old_report:
            raw_artifacts["自测报告.md"] = old_report

    return {key: value for key, value in raw_artifacts.items() if key in allowed}


def _normalize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    if not state:
        return {}
    normalized = dict(state)
    normalized["workflow_type"] = str(normalized.get("workflow_type", SQL_MODIFY_WORKFLOW_ID) or SQL_MODIFY_WORKFLOW_ID)
    normalized["requirement_name"] = str(normalized.get("requirement_name", "") or normalized.get("title", "") or "")
    normalized["tapd_id"] = str(normalized.get("tapd_id", "") or "")
    normalized["node_inputs"] = dict(normalized.get("node_inputs", {}) or {})
    normalized["node_statuses"] = dict(normalized.get("node_statuses", {}) or {})
    if not normalized["node_statuses"]:
        normalized["node_statuses"] = {
            str(node["id"]): {
                "label": str(node["label"]),
                "status": "pending",
                "updated_at": "",
                "note": "",
            }
            for node in get_workflow_definition(normalized["workflow_type"])["nodes"]
        }
    requirement_dir = str(normalized.get("requirement_dir", "") or "").strip()
    if requirement_dir:
        requirement_path = Path(requirement_dir)
        normalized["local_sql_path"] = str(
            normalized.get("local_sql_path", "") or requirement_path / "online_sql" / "source.sql"
        )
        normalized["modified_sql_path"] = str(
            normalized.get("modified_sql_path", "") or requirement_path / "draft_sql" / "modified.sql"
        )
        normalized["self_test_report_path"] = str(
            normalized.get("self_test_report_path", "") or requirement_path / "自测报告.md"
        )
    normalized["source_script_info"] = dict(normalized.get("source_script_info", {}) or {})
    normalized["artifacts"] = _migrate_artifacts(normalized)
    normalized["current_step"] = str(normalized.get("current_step", "") or normalized.get("current_node", "") or "created")
    normalized["status"] = str(normalized.get("status", "") or "created")
    valid_steps = {"created", "load_context", "done"} | {
        str(node["id"]) for node in get_workflow_definition(normalized["workflow_type"])["nodes"]
    }
    if normalized["current_step"] not in valid_steps:
        normalized["current_step"] = "done" if normalized["status"] == "completed" else "created"
    for legacy_key in (
        "step_knowledge",
        "step_statuses",
        "step_histories",
        "execution_status",
        "execution_events",
        "execution_steps",
        "current_node",
    ):
        normalized.pop(legacy_key, None)
    return normalized


def _persist_requirement_views(state: Dict[str, Any]) -> None:
    raw_requirement_dir = str(state.get("requirement_dir", "")).strip()
    if not raw_requirement_dir:
        return
    requirement_dir = Path(raw_requirement_dir)
    artifacts = dict(state.get("artifacts", {}))
    artifacts["README.md"] = render_requirement_readme(state, [])
    write_requirement_files(requirement_dir, artifacts)
    state["artifacts"] = artifacts


def list_threads() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not THREADS_DIR.exists():
        return items
    for path in sorted(THREADS_DIR.iterdir()):
        if not path.is_dir():
            continue
        state = _normalize_state(read_json_file(path / "latest_state.json"))
        pending_interrupts = list_pending_interrupts(path.name) if state else []
        items.append(
            {
                "thread_id": path.name,
                "status": state.get("status", "unknown"),
                "current_step": state.get("current_step", "-"),
                "tapd_id": state.get("tapd_id", "-"),
                "title": state.get("requirement_name") or state.get("title", path.name),
                "requirement_dir": state.get("requirement_dir", ""),
                "interrupted": bool(pending_interrupts),
                "updated_at": _last_updated_at(state),
                "history_count": _history_count(state),
            }
        )
    return items


def _last_updated_at(state: Dict[str, Any]) -> str:
    statuses = state.get("node_statuses", {})
    timestamps = [
        str(step.get("updated_at", "")).strip()
        for step in statuses.values()
        if str(step.get("updated_at", "")).strip()
    ]
    if not timestamps:
        return "-"
    return max(timestamps)


def _history_count(state: Dict[str, Any]) -> int:
    return 0


def read_thread_bundle(thread_id: str) -> Dict[str, Any]:
    raw_state = read_json_file(thread_state_path(thread_id))
    state = _normalize_state(raw_state)
    interrupt = read_json_file(thread_interrupt_path(thread_id))
    if state:
        _persist_requirement_views(state)
    if state and (
        state != raw_state
        or "README.md" not in state.get("artifacts", {})
    ):
        _persist_requirement_views(state)
        _write_runtime_outputs(thread_id, state, interrupt or None)
    pending_interrupts = list_pending_interrupts(thread_id) if state else []
    if not pending_interrupts and interrupt:
        interrupt = {}
        _write_runtime_outputs(thread_id, state, None)
    elif pending_interrupts:
        interrupt = pending_interrupts[0]
    artifacts = state.get("artifacts", {})
    return {
        "thread_id": thread_id,
        "state": state,
        "interrupt": interrupt,
        "pending_interrupts": pending_interrupts,
        "artifacts": artifacts,
    }


def _extract_interrupt_payload(result: Dict[str, Any]) -> Optional[object]:
    interrupts = result.get("__interrupt__")
    if isinstance(interrupts, (list, tuple)) and interrupts:
        first = interrupts[0]
        value = getattr(first, "value", first)
        interrupt_id = getattr(first, "id", "")
        if isinstance(value, dict):
            return {
                **value,
                "interrupt_id": interrupt_id,
            }
        return {
            "content": value,
            "interrupt_id": interrupt_id,
        }
    if interrupts:
        return interrupts
    return None


def list_pending_interrupts(thread_id: str) -> List[Dict[str, Any]]:
    graph = build_requirement_graph()
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    pending: List[Dict[str, Any]] = []
    for item in list(getattr(snapshot, "interrupts", ()) or ()):
        value = getattr(item, "value", item)
        interrupt_id = str(getattr(item, "id", "") or "")
        if isinstance(value, dict):
            pending.append(
                {
                    **value,
                    "interrupt_id": interrupt_id,
                }
            )
        else:
            pending.append(
                {
                    "content": value,
                    "interrupt_id": interrupt_id,
                }
            )
    return pending


def _snapshot_graph_state(graph: Any, config: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        snapshot_state = graph.get_state(config)
        values = getattr(snapshot_state, "values", None)
        if isinstance(values, dict):
            return values
    except Exception:
        pass
    return fallback


def _write_runtime_outputs(thread_id: str, snapshot: Dict[str, Any], interrupt_payload: Optional[object]) -> None:
    snapshot = _normalize_state(snapshot)
    _persist_requirement_views(snapshot)
    thread_runtime_dir(thread_id).mkdir(parents=True, exist_ok=True)
    thread_state_path(thread_id).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    thread_interrupt_path(thread_id).write_text(
        json.dumps(interrupt_payload or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _assert_resume_ready(thread_id: str) -> Dict[str, Any]:
    state = _normalize_state(read_json_file(thread_state_path(thread_id)))
    if not state:
        raise ValueError(f"Thread not found: {thread_id}")
    if not str(state.get("requirement_dir", "")).strip():
        raise ValueError(f"Thread state is invalid, missing requirement_dir: {thread_id}")
    interrupt = read_json_file(thread_interrupt_path(thread_id))
    if not interrupt:
        raise ValueError("Thread is not paused at a review gate. Start the workflow first, or open a paused node.")
    return state


def _workflow_node_ids(workflow_type: str) -> List[str]:
    return [str(node["id"]) for node in get_workflow_definition(workflow_type)["nodes"]]


def _select_interrupt_resume_payload(graph: Any, config: Dict[str, Any], thread_id: str, payload: object) -> object:
    snapshot = graph.get_state(config)
    interrupts = list(getattr(snapshot, "interrupts", ()) or ())
    if not interrupts:
        raise ValueError(f"Thread is not paused at a review gate: {thread_id}")
    if len(interrupts) == 1:
        return payload

    latest_interrupt = read_json_file(thread_interrupt_path(thread_id))
    target_id = str(latest_interrupt.get("interrupt_id", "") or "").strip()
    target_step = str(latest_interrupt.get("step_id", "") or "").strip()
    payload_step = str(payload.get("step_id", "") if isinstance(payload, dict) else "").strip()
    current_step = str(read_json_file(thread_state_path(thread_id)).get("current_step", "") or "").strip()

    chosen = None
    for item in interrupts:
        item_id = str(getattr(item, "id", "") or "")
        value = getattr(item, "value", item)
        step_id = str(value.get("step_id", "") if isinstance(value, dict) else "")
        if payload_step and step_id == payload_step:
            chosen = item_id
            break
        if target_id and item_id == target_id:
            chosen = item_id
            break
        if target_step and step_id == target_step:
            chosen = item_id
            break
        if current_step and step_id == current_step:
            chosen = item_id
            break
    if not chosen:
        raise ValueError("Multiple pending review gates exist for this thread. Refresh the page and retry on the current gate.")
    return {chosen: payload}


def resume_thread(thread_id: str, payload: object) -> Dict[str, Any]:
    _assert_resume_ready(thread_id)
    graph = build_requirement_graph()
    config = {"configurable": {"thread_id": thread_id}}
    from langgraph.types import Command

    resume_payload = _select_interrupt_resume_payload(graph, config, thread_id, payload)
    result = graph.invoke(Command(resume=resume_payload), config=config)
    interrupt_payload = _extract_interrupt_payload(result)
    snapshot = _snapshot_graph_state(graph, config, result)
    _write_runtime_outputs(thread_id, snapshot, interrupt_payload)
    return {
        "state": snapshot,
        "interrupt": interrupt_payload or {},
    }


def rerun_step(thread_id: str, step_id: str, note: str) -> Dict[str, Any]:
    graph = build_requirement_graph()
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    state = _normalize_state(copy.deepcopy(snapshot.values))
    if not state:
        raise ValueError(f"Thread not found: {thread_id}")

    workflow_type = str(state.get("workflow_type", SQL_MODIFY_WORKFLOW_ID) or SQL_MODIFY_WORKFLOW_ID)
    node_ids = _workflow_node_ids(workflow_type)
    if step_id not in node_ids:
        raise ValueError(f"Unsupported step_id: {step_id}")

    target_index = node_ids.index(step_id)
    predecessor = "load_context" if target_index == 0 else node_ids[target_index - 1]

    node_statuses = dict(state.get("node_statuses", {}) or {})
    node_inputs = dict(state.get("node_inputs", {}) or {})
    artifacts = dict(state.get("artifacts", {}) or {})
    workflow_def = get_workflow_definition(workflow_type)
    artifact_keys_by_node = {
        str(node["id"]): str(node.get("artifact_key", "") or "")
        for node in workflow_def["nodes"]
    }

    for downstream_id in node_ids[target_index:]:
        current = dict(node_statuses.get(downstream_id, {}) or {})
        node_statuses[downstream_id] = {
            **current,
            "status": "pending",
            "updated_at": "",
            "note": note if downstream_id == step_id else "",
        }
        artifact_key = artifact_keys_by_node.get(downstream_id, "")
        if artifact_key and artifact_key != "docs/开发文档.md":
            artifacts[artifact_key] = ""

    existing_inputs = list(node_inputs.get(step_id, []) or [])
    if note.strip():
        existing_inputs.append(note.strip())
    node_inputs[step_id] = existing_inputs

    updated_state = {
        **state,
        "node_statuses": node_statuses,
        "node_inputs": node_inputs,
        "artifacts": artifacts,
        "current_step": step_id,
        "status": "running",
        "latest_interrupt": {},
    }

    new_config = graph.update_state(config, updated_state, as_node=predecessor)
    result = graph.invoke(None, config=new_config)
    interrupt_payload = _extract_interrupt_payload(result)
    new_snapshot = _snapshot_graph_state(graph, new_config, result)
    _write_runtime_outputs(thread_id, new_snapshot, interrupt_payload)
    return {
        "state": new_snapshot,
        "interrupt": interrupt_payload or {},
    }


def start_existing_thread(thread_id: str) -> Dict[str, Any]:
    raw_state = read_json_file(thread_state_path(thread_id))
    state = _normalize_state(raw_state)
    if not state:
        raise ValueError(f"Thread not found: {thread_id}")
    if str(state.get("status", "")).strip() != "created":
        raise ValueError("Only threads in created status can be started manually.")
    return start_thread(state)


def regenerate_thread_from_step(thread_id: str, step_id: str, knowledge_note: str) -> Dict[str, Any]:
    return resume_thread(
        thread_id,
        {
            "action": "rerun_with_input",
            "note": knowledge_note,
        },
    )


def resolve_thread_requirement_dir(thread_id: str) -> Path:
    state = read_json_file(thread_state_path(thread_id))
    raw = str(state.get("requirement_dir", "")).strip()
    if raw:
        return Path(raw)
    return resolve_requirement_dir(thread_id)


def step_specs_index() -> List[Dict[str, Any]]:
    specs = []
    for node in get_workflow_definition(SQL_MODIFY_WORKFLOW_ID)["nodes"]:
        item = dict(node)
        preferred = list(item.get("preferred_skills", []) or [])
        item["skill_matches"] = match_preferred_skills(preferred) if preferred else {"available": [], "missing": []}
        specs.append(item)
    return specs


def dashboard_env_summary() -> Dict[str, str]:
    return {
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", ""),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL", ""),
        "OPENAI_USE_RESPONSES_API": os.getenv("OPENAI_USE_RESPONSES_API", ""),
    }


def slugify_short_name(raw: str) -> str:
    text = raw.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "new-requirement"


def build_thread_id(tapd_id: str, short_name: str) -> str:
    tapd = tapd_id.strip() or "TAPDpending"
    if not tapd.lower().startswith("tapd"):
        tapd = f"TAPD{tapd}"
    return f"{tapd}_{slugify_short_name(short_name)}"


def build_initial_state(
    thread_id: str,
    title: str,
    brief: str,
    predecessors: List[str],
    interactive_review: bool = True,
) -> Dict[str, Any]:
    requirement_dir = resolve_requirement_dir(thread_id)
    title_value = title.strip() or thread_id.split("_", 1)[-1].replace("-", " ")
    tapd_id = thread_id.split("_", 1)[0] if "_" in thread_id else thread_id
    return {
        "workflow_type": SQL_MODIFY_WORKFLOW_ID,
        "thread_id": thread_id,
        "requirement_dir": str(requirement_dir),
        "template_dir": str(TEMPLATE_DIR),
        "title": title_value,
        "requirement_name": title_value,
        "tapd_id": tapd_id,
        "predecessor_requirements": predecessors,
        "brief": brief,
        "interactive_review": interactive_review,
        "node_inputs": {},
        "node_statuses": {},
        "source_script_info": {},
        "local_sql_path": str(requirement_dir / "online_sql" / "source.sql"),
        "modified_sql_path": str(requirement_dir / "draft_sql" / "modified.sql"),
        "self_test_report_path": str(requirement_dir / "自测报告.md"),
        "latest_interrupt": {},
        "current_step": "created",
        "status": "created",
    }


def start_thread(initial_state: Dict[str, Any]) -> Dict[str, Any]:
    thread_id = str(initial_state["thread_id"])
    graph = build_requirement_graph()
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(initial_state, config=config)
    interrupt_payload = _extract_interrupt_payload(result)
    snapshot = _snapshot_graph_state(graph, config, result)
    _write_runtime_outputs(thread_id, snapshot, interrupt_payload)
    return {
        "thread_id": thread_id,
        "state": snapshot,
        "interrupt": interrupt_payload or {},
    }


def create_requirement_thread(
    tapd_id: str,
    short_name: str,
    title: str,
    brief: str,
    predecessors: List[str],
    auto_start: bool,
) -> Dict[str, Any]:
    thread_id = build_thread_id(tapd_id, short_name)
    requirement_dir = resolve_requirement_dir(thread_id)
    if requirement_dir.exists():
        raise ValueError(f"Requirement directory already exists: {requirement_dir}")

    sync_requirement_template(requirement_dir, TEMPLATE_DIR)
    initial_state = build_initial_state(
        thread_id=thread_id,
        title=title,
        brief=brief,
        predecessors=predecessors,
        interactive_review=True,
    )

    if auto_start:
        return start_thread(initial_state)

    artifacts = read_requirement_files(requirement_dir, [])
    snapshot = {
        **initial_state,
        "artifacts": artifacts,
        "node_statuses": {},
        "current_step": "created",
        "last_error": None,
        "latest_interrupt": {},
    }
    _persist_requirement_views(snapshot)
    _write_runtime_outputs(thread_id, snapshot, None)
    return {
        "thread_id": thread_id,
        "state": snapshot,
        "interrupt": {},
    }
