
import copy
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from .config import CHECKPOINT_DB, TEMPLATE_DIR, ensure_runtime_dirs
from .file_store import (
    read_requirement_files,
    render_requirement_readme,
    sync_requirement_template,
    write_requirement_files,
)
from .llm import build_llm, coerce_ai_content
from .prompts import SYSTEM_PROMPT, build_node_prompt
from .schemas import RequirementState, utc_now_iso
from .workflow_defs import SQL_MODIFY_WORKFLOW_ID, get_workflow_definition

FETCH_AND_CONFIRM_CHANGES_NODE_ID = "fetch_and_confirm_changes"


def _workflow_nodes() -> List[Dict[str, object]]:
    # 统一从 workflow 定义里取节点顺序，避免 graph 和定义文件分叉。
    return list(get_workflow_definition(SQL_MODIFY_WORKFLOW_ID)["nodes"])


def _ensure_node_statuses(state: RequirementState) -> Dict[str, Dict[str, Any]]:
    # 为每个主节点补齐运行态，缺失时默认初始化为 pending。
    statuses = copy.deepcopy(state.get("node_statuses", {}))
    for node in _workflow_nodes():
        statuses.setdefault(
            str(node["id"]),
            {
                "label": str(node["label"]),
                "status": "pending",
                "updated_at": "",
                "note": "",
            },
        )
    return statuses


def _ensure_node_inputs(state: RequirementState) -> Dict[str, List[str]]:
    # 每个节点都保留一份“人工补充输入”列表，供 rerun 时追加使用。
    node_inputs = copy.deepcopy(state.get("node_inputs", {}))
    for node in _workflow_nodes():
        node_inputs.setdefault(str(node["id"]), [])
    return node_inputs


def _draft_default_for_node(node_id: str, requirement_name: str) -> str:
    # 当模型没有返回有效内容时，给每个节点一个最小可编辑草稿，避免页面空白。
    if node_id == "requirement_confirm":
        return (
            "# 需求确认\n\n"
            f"## 需求名\n\n- {requirement_name or '-'}\n\n"
            "## 背景\n\n-\n\n"
            "## 目标\n\n-\n\n"
            "## 范围\n\n-\n\n"
            "## 风险与待确认项\n\n-\n"
        )
    if node_id == "task_confirm":
        return "# 任务清单\n\n## 待执行任务\n\n- [ ] \n"
    if node_id == "fetch_and_confirm_changes":
        return (
            "# 改动项确认\n\n"
            "## 对应脚本信息\n\n- task_name:\n- task_id:\n- script_path:\n- source_type:\n\n"
            "## 本地 SQL 路径\n\n- local_sql_path:\n\n"
            "## 改动项\n\n- \n"
        )
    if node_id == "local_edit_draft":
        return "-- modified sql draft\n"
    if node_id == "validate_result":
        return "# 自测报告\n\n## 校验项\n\n- \n\n## 结果\n\n- \n"
    if node_id == "deliver":
        return "# 交付说明\n\n## 交付物\n\n- \n\n## 最终结论\n\n- \n"
    return ""


def _artifact_key_for_node(node: Dict[str, object]) -> str:
    return str(node.get("artifact_key", "") or "")


def _safe_read_text(path: Path) -> str:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return ""


def _extract_fetch_fields(content: str) -> Dict[str, str]:
    fields = {
        "task_name": "",
        "task_id": "",
        "script_path": "",
        "source_type": "",
        "local_sql_path": "",
    }
    pattern = re.compile(
        r"^\s*(?:-\s*)?(task_name|task_id|script_path|source_type|local_sql_path)\s*:\s*(.+?)\s*$",
        re.IGNORECASE,
    )
    for line in content.splitlines():
        matched = pattern.match(line)
        if not matched:
            continue
        key = matched.group(1).strip().lower()
        value = matched.group(2).strip()
        if value:
            fields[key] = value
    return fields


def _resolve_requirement_local_path(requirement_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (requirement_dir / candidate).resolve()


def _pull_online_sql_from_env_command(
    state: RequirementState,
    requirement_dir: Path,
    fields: Dict[str, str],
) -> str:
    command_template = os.getenv("REQUIREMENT_FLOW_SQL_FETCH_CMD", "").strip()
    if not command_template:
        return ""

    values = {
        "thread_id": str(state.get("thread_id", "") or ""),
        "tapd_id": str(state.get("tapd_id", "") or ""),
        "requirement_dir": str(requirement_dir),
        "task_id": fields.get("task_id", ""),
        "task_name": fields.get("task_name", ""),
        "script_path": fields.get("script_path", ""),
        "source_type": fields.get("source_type", ""),
        "local_sql_path": fields.get("local_sql_path", ""),
    }
    try:
        command = command_template.format(**values)
    except Exception:
        return ""

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(requirement_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return ""

    if completed.returncode != 0:
        return ""

    content = (completed.stdout or "").strip()
    return content + "\n" if content else ""


def _pull_online_sql_content(
    state: RequirementState,
    requirement_dir: Path,
    artifacts: Dict[str, str],
) -> str:
    existing_artifact = str(artifacts.get("online_sql/source.sql", "") or "").strip()
    if existing_artifact:
        return existing_artifact + "\n"

    raw_fetch_doc = str(artifacts.get("online_sql/source.sql", "") or "")
    raw_dev_doc = str(artifacts.get("docs/开发文档.md", "") or "")
    fields = _extract_fetch_fields(raw_fetch_doc)
    if not fields.get("script_path"):
        doc_fields = _extract_fetch_fields(raw_dev_doc)
        fields = {**doc_fields, **{k: v for k, v in fields.items() if v}}

    fetched = _pull_online_sql_from_env_command(state, requirement_dir, fields)
    if fetched.strip():
        return fetched

    candidates: List[Path] = []
    local_sql_path = str(state.get("local_sql_path", "") or "").strip()
    if local_sql_path:
        candidates.append(_resolve_requirement_local_path(requirement_dir, local_sql_path))
    parsed_local_sql_path = fields.get("local_sql_path", "").strip()
    if parsed_local_sql_path:
        candidates.append(_resolve_requirement_local_path(requirement_dir, parsed_local_sql_path))
    parsed_script_path = fields.get("script_path", "").strip()
    if parsed_script_path:
        candidates.append(_resolve_requirement_local_path(requirement_dir, parsed_script_path))
    candidates.append(requirement_dir / "online_sql" / "source.sql")

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        content = _safe_read_text(path).strip()
        if content:
            return content + "\n"
    return ""


def _apply_fetch_node_state_updates(
    state: RequirementState,
    content: str,
    requirement_dir: Path,
) -> Dict[str, object]:
    fields = _extract_fetch_fields(content)
    source_script_info = dict(state.get("source_script_info", {}) or {})
    for key in ("task_name", "task_id", "script_path", "source_type"):
        value = fields.get(key, "").strip()
        if value:
            source_script_info[key] = value

    updates: Dict[str, object] = {
        "source_script_info": source_script_info,
    }
    local_sql_path = fields.get("local_sql_path", "").strip()
    if local_sql_path:
        resolved = _resolve_requirement_local_path(requirement_dir, local_sql_path)
        updates["local_sql_path"] = str(resolved)
    return updates


def _sync_support_artifacts(state: RequirementState, artifacts: Dict[str, str]) -> Dict[str, str]:
    # README 不是单独节点产物，而是根据当前 state 聚合渲染出来的总览文件。
    updated = dict(artifacts)
    updated["README.md"] = render_requirement_readme(state, [])
    return updated


def _load_context_node(state: RequirementState) -> RequirementState:
    # 初始化节点:
    # 1. 同步需求目录模板
    # 2. 读取已有文件
    # 3. 构造运行态和关键路径字段
    requirement_dir = Path(state["requirement_dir"])
    template_dir = Path(state.get("template_dir") or TEMPLATE_DIR)
    sync_requirement_template(requirement_dir, template_dir)
    artifacts = read_requirement_files(requirement_dir, [])
    for node in _workflow_nodes():
        artifact_key = _artifact_key_for_node(node)
        if artifact_key:
            artifacts.setdefault(artifact_key, "")
    node_statuses = _ensure_node_statuses(state)
    node_inputs = _ensure_node_inputs(state)
    runtime_state: RequirementState = {
        **state,
        "workflow_type": str(state.get("workflow_type") or SQL_MODIFY_WORKFLOW_ID),
        "requirement_name": str(state.get("requirement_name") or state.get("title") or ""),
        "artifacts": artifacts,
        "node_statuses": node_statuses,
        "node_inputs": node_inputs,
        "local_sql_path": str(requirement_dir / "online_sql" / "source.sql"),
        "modified_sql_path": str(requirement_dir / "draft_sql" / "modified.sql"),
        "self_test_report_path": str(requirement_dir / "自测报告.md"),
        "current_step": "load_context",
        "status": "running",
        "last_error": None,
    }
    runtime_state["artifacts"] = _sync_support_artifacts(runtime_state, artifacts)
    write_requirement_files(requirement_dir, runtime_state["artifacts"])
    return runtime_state


def _make_node(node_def: Dict[str, object]):
    node_id = str(node_def["id"])
    artifact_key = _artifact_key_for_node(node_def)

    def _node(state: RequirementState):
        from langgraph.types import Command, interrupt

        # 每个业务节点都遵循相同模型:
        # 1. 基于当前 state 生成一版草稿
        # 2. 进入人工审核中断
        # 3. 根据 approve / edit / rerun_with_input 决定后续流转
        requirement_dir = Path(state["requirement_dir"])
        node_statuses = _ensure_node_statuses(state)
        node_inputs = _ensure_node_inputs(state)
        artifacts = dict(state.get("artifacts", {}))

        if node_id == FETCH_AND_CONFIRM_CHANGES_NODE_ID:
            pulled_sql = _pull_online_sql_content(state, requirement_dir, artifacts)
            if pulled_sql.strip():
                artifacts["online_sql/source.sql"] = pulled_sql

        # 先调模型产出当前节点草稿，并写入该节点绑定的 artifact。
        llm = build_llm()
        prompt_state = {**state, "artifacts": artifacts}
        prompt = build_node_prompt(prompt_state, node_def)
        response = llm.invoke([("system", SYSTEM_PROMPT), ("human", prompt)])
        drafted_content = coerce_ai_content(response).strip() or artifacts.get(artifact_key, "")
        if not drafted_content:
            drafted_content = _draft_default_for_node(node_id, str(state.get("requirement_name", "") or ""))
        if artifact_key:
            artifacts[artifact_key] = drafted_content

        now = utc_now_iso()
        node_statuses[node_id] = {
            **node_statuses.get(node_id, {}),
            "label": str(node_def["label"]),
            "status": "reviewing",
            "updated_at": now,
            "note": "",
        }

        draft_state: RequirementState = {
            **state,
            "artifacts": artifacts,
            "node_statuses": node_statuses,
            "node_inputs": node_inputs,
            "local_sql_path": str(state.get("local_sql_path") or requirement_dir / "online_sql" / "source.sql"),
            "modified_sql_path": str(
                state.get("modified_sql_path") or requirement_dir / "draft_sql" / "modified.sql"
            ),
            "self_test_report_path": str(
                state.get("self_test_report_path") or requirement_dir / "自测报告.md"
            ),
            "current_step": node_id,
            "status": "running",
            "latest_interrupt": {},
        }
        draft_state["artifacts"] = _sync_support_artifacts(draft_state, artifacts)
        write_requirement_files(requirement_dir, draft_state["artifacts"])

        # 进入 review gate。页面上的通过、编辑、重跑都围绕这个中断点恢复。
        payload = {
            "type": "node_review",
            "step_id": node_id,
            "label": str(node_def["label"]),
            "content": draft_state["artifacts"].get(artifact_key, ""),
            "instructions": str(node_def.get("instructions", "") or ""),
            "actions": list(node_def.get("actions", [])),
        }
        decision = interrupt(payload)

        action = ""
        content = ""
        note = ""
        if isinstance(decision, dict):
            action = str(decision.get("action", "")).strip().lower()
            content = str(decision.get("content", "")).strip()
            note = str(decision.get("note", "") or decision.get("review_note", "")).strip()
        elif isinstance(decision, str):
            action = decision.strip().lower()

        # rerun_with_input:
        # 保留人工补充说明，把当前节点状态打回 pending，然后直接回跳到自己重跑。
        if action == "rerun_with_input":
            if note:
                return Command(
                    update={
                        "node_inputs": {node_id: [note]},
                        "node_statuses": {
                            node_id: {
                                "label": str(node_def["label"]),
                                "status": "pending",
                                "updated_at": utc_now_iso(),
                                "note": note,
                            }
                        },
                        "current_step": node_id,
                    },
                    goto=node_id,
                )
            raise ValueError(f"{node_id} rerun_with_input requires note.")

        # approve / edit:
        # approve 使用当前草稿，edit 用人工最终稿覆盖当前草稿。
        final_content = drafted_content
        if action == "edit":
            if not content:
                raise ValueError(f"{node_id} edit requires content.")
            final_content = content
        elif action not in {"approve", "approved", "edit"}:
            raise ValueError(f"Unsupported action for {node_id}: {decision!r}")

        if artifact_key:
            artifacts[artifact_key] = final_content
        approved_at = utc_now_iso()
        node_statuses[node_id] = {
            **node_statuses.get(node_id, {}),
            "label": str(node_def["label"]),
            "status": "approved",
            "updated_at": approved_at,
            "note": note,
        }

        node_state_updates: Dict[str, object] = {}
        if node_id == FETCH_AND_CONFIRM_CHANGES_NODE_ID:
            node_state_updates = _apply_fetch_node_state_updates(state, final_content, requirement_dir)

        next_state: RequirementState = {
            **state,
            **node_state_updates,
            "artifacts": artifacts,
            "node_statuses": node_statuses,
            "node_inputs": node_inputs,
            "local_sql_path": str(
                node_state_updates.get("local_sql_path")
                or state.get("local_sql_path")
                or requirement_dir / "online_sql" / "source.sql"
            ),
            "modified_sql_path": str(
                state.get("modified_sql_path") or requirement_dir / "draft_sql" / "modified.sql"
            ),
            "self_test_report_path": str(
                state.get("self_test_report_path") or requirement_dir / "自测报告.md"
            ),
            "current_step": node_id,
            "status": "running",
            "latest_interrupt": {},
        }
        next_state["artifacts"] = _sync_support_artifacts(next_state, artifacts)
        write_requirement_files(requirement_dir, next_state["artifacts"])
        return next_state

    return _node


def _finalize_node(state: RequirementState) -> RequirementState:
    # 收尾节点:
    # 刷新 README，总结最终状态，并把 current_step 标成 done。
    requirement_dir = Path(state["requirement_dir"])
    artifacts = _sync_support_artifacts(
        {
            **state,
            "current_step": "done",
            "status": "completed",
        },
        dict(state.get("artifacts", {})),
    )
    write_requirement_files(requirement_dir, artifacts)
    return {
        **state,
        "artifacts": artifacts,
        "local_sql_path": str(requirement_dir / "online_sql" / "source.sql"),
        "modified_sql_path": str(requirement_dir / "draft_sql" / "modified.sql"),
        "self_test_report_path": str(requirement_dir / "自测报告.md"),
        "current_step": "done",
        "status": "completed",
        "latest_interrupt": {},
    }


def build_requirement_graph() -> Any:
    ensure_runtime_dirs()
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph

    # Graph 结构保持单链路:
    # load_context -> 6 个主节点 -> finalize -> END
    builder = StateGraph(RequirementState)
    builder.add_node("load_context", _load_context_node)
    builder.add_edge(START, "load_context")

    previous_node = "load_context"
    for node in _workflow_nodes():
        node_id = str(node["id"])
        builder.add_node(node_id, _make_node(node))
        builder.add_edge(previous_node, node_id)
        previous_node = node_id

    builder.add_node("finalize", _finalize_node)
    builder.add_edge(previous_node, "finalize")
    builder.add_edge("finalize", END)

    connection = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    return builder.compile(checkpointer=checkpointer)
