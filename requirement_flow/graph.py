
import copy
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from .config import CHECKPOINT_DB, TEMPLATE_DIR, ensure_runtime_dirs
from .debug_trace import log_event
from .file_store import (
    read_requirement_files,
    render_requirement_readme,
    sync_requirement_template,
    write_requirement_files,
)
from .llm import build_llm, coerce_ai_content
from .node_executors import run_finalize_executor, run_prepare_executor
from .prompts import SYSTEM_PROMPT, build_node_prompt
from .schemas import RequirementState, utc_now_iso
from .tracing import llm_run_config, node_metadata
from .workflow_defs import SQL_MODIFY_WORKFLOW_ID, get_workflow_definition


def _debug_breakpoint(label: str) -> None:
    if str(os.getenv("REQUIREMENT_FLOW_DEBUG_BREAKPOINTS", "") or "").strip() != "1":
        return
    print(f"[debug-breakpoint] {label}")
    breakpoint()


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

def _artifact_key_for_node(node: Dict[str, object]) -> str:
    return str(node.get("artifact_key", "") or "")


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

        # BREAKPOINT HERE:
        # 看 prepare_updates 合并后，artifacts["docs/需求文档.md"] 是否还是新内容。
        # 这是 graph 层接收 executor 产物的关键位置。
        # 每个业务节点都遵循相同模型:
        # 1. 基于当前 state 生成一版草稿
        # 2. 进入人工审核中断
        # 3. 根据 approve / edit / rerun_with_input 决定后续流转
        requirement_dir = Path(state["requirement_dir"])
        thread_id = str(state.get("thread_id", "") or requirement_dir.name)
        node_statuses = _ensure_node_statuses(state)
        node_inputs = _ensure_node_inputs(state)
        artifacts = dict(state.get("artifacts", {}))
        log_event(
            thread_id,
            "node_start",
            {
                "node_id": node_id,
                "status": state.get("status", ""),
                "current_step": state.get("current_step", ""),
            },
        )

        prepare_updates = run_prepare_executor(node_id, state, node_def, artifacts, requirement_dir)
        if node_id == "requirement_confirm":
            _debug_breakpoint("graph.after_requirement_confirm_prepare")
        log_event(
            thread_id,
            "node_prepare_done",
            {
                "node_id": node_id,
                "prepare_keys": sorted([str(key) for key in prepare_updates.keys()]),
                "prepare_updates": prepare_updates,
            },
        )
        prepared_artifacts = dict(prepare_updates.get("artifacts", {}) or {})
        if prepared_artifacts:
            artifacts.update({key: str(value) for key, value in prepared_artifacts.items()})
        prepared_state: RequirementState = {
            **state,
            **{key: value for key, value in prepare_updates.items() if key != "artifacts"},
            "artifacts": artifacts,
        }

        llm_enabled = bool(node_def.get("llm_enabled", True))
        drafted_content = artifacts.get(artifact_key, "")
        if llm_enabled:
            llm = build_llm()
            prompt_state = {**prepared_state, "artifacts": artifacts}
            prompt = build_node_prompt(prompt_state, node_def)
            log_event(
                thread_id,
                "node_llm_start",
                {
                    "node_id": node_id,
                    "system_prompt": SYSTEM_PROMPT,
                    "human_prompt": prompt,
                    "artifacts_before_llm": artifacts,
                },
            )
            try:
                response = llm.invoke(
                    [("system", SYSTEM_PROMPT), ("human", prompt)],
                    config=llm_run_config(prompt_state, node_def),
                )
            except Exception as exc:
                log_event(
                    thread_id,
                    "node_llm_error",
                    {
                        "node_id": node_id,
                        "error": str(exc),
                    },
                )
                raise
            log_event(
                thread_id,
                "node_llm_done",
                {
                    "node_id": node_id,
                    "raw_response": getattr(response, "content", response),
                },
            )
            drafted_content = coerce_ai_content(response).strip() or drafted_content
        else:
            log_event(
                thread_id,
                "node_llm_skipped",
                {
                    "node_id": node_id,
                    "reason": "llm disabled for node",
                    "artifact_key": artifact_key,
                },
            )
        if not drafted_content:
            drafted_content = '未解析出结果'
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
            **prepared_state,
            "artifacts": artifacts,
            "node_statuses": node_statuses,
            "node_inputs": node_inputs,
            "local_sql_path": str(
                prepared_state.get("local_sql_path") or requirement_dir / "online_sql" / "source.sql"
            ),
            "modified_sql_path": str(
                prepared_state.get("modified_sql_path") or requirement_dir / "draft_sql" / "modified.sql"
            ),
            "self_test_report_path": str(
                prepared_state.get("self_test_report_path") or requirement_dir / "自测报告.md"
            ),
            "current_step": node_id,
            "status": "running",
            "latest_interrupt": {},
        }
        draft_state["artifacts"] = _sync_support_artifacts(draft_state, artifacts)
        if node_id == "requirement_confirm":
            _debug_breakpoint("graph.before_requirement_confirm_draft_write")
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
        log_event(
            thread_id,
            "node_interrupt",
            {
                "node_id": node_id,
                "actions": list(node_def.get("actions", [])),
                "interrupt_payload": payload,
            },
        )
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
        log_event(
            thread_id,
            "node_interrupt_decision",
            {
                "node_id": node_id,
                "action": action,
                "note": note,
                "content": content,
                "decision": decision,
            },
        )

        # rerun_with_input:
        # 保留人工补充说明，把当前节点状态打回 pending，然后直接回跳到自己重跑。
        if action == "rerun_with_input":
            if note:
                log_event(
                    thread_id,
                    "node_rerun_with_input",
                    {
                        "node_id": node_id,
                        "note_len": len(note),
                    },
                )
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

        final_artifacts = dict(artifacts)
        if artifact_key:
            final_artifacts[artifact_key] = final_content
        finalized_state: RequirementState = {
            **prepared_state,
            "artifacts": final_artifacts,
        }
        node_state_updates = run_finalize_executor(node_id, finalized_state, node_def, final_artifacts, requirement_dir)
        log_event(
            thread_id,
            "node_finalize_done",
            {
                "node_id": node_id,
                "action": action,
                "finalize_keys": sorted([str(key) for key in node_state_updates.keys()]),
                "node_state_updates": node_state_updates,
                "final_content": final_content,
            },
        )

        next_state: RequirementState = {
            **prepared_state,
            **node_state_updates,
            "artifacts": final_artifacts,
            "node_statuses": node_statuses,
            "node_inputs": node_inputs,
            "local_sql_path": str(
                node_state_updates.get("local_sql_path")
                or prepared_state.get("local_sql_path")
                or requirement_dir / "online_sql" / "source.sql"
            ),
            "modified_sql_path": str(
                prepared_state.get("modified_sql_path") or requirement_dir / "draft_sql" / "modified.sql"
            ),
            "self_test_report_path": str(
                prepared_state.get("self_test_report_path") or requirement_dir / "自测报告.md"
            ),
            "current_step": node_id,
            "status": "running",
            "latest_interrupt": {},
        }
        next_state["artifacts"] = _sync_support_artifacts(next_state, artifacts)
        if node_id == "requirement_confirm":
            _debug_breakpoint("graph.before_requirement_confirm_final_write")
        write_requirement_files(requirement_dir, next_state["artifacts"])
        log_event(
            thread_id,
            "node_complete",
            {
                "node_id": node_id,
                "current_step": next_state.get("current_step", ""),
                "status": next_state.get("status", ""),
                "artifacts_after_node": next_state.get("artifacts", {}),
            },
        )
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
    log_event(
        str(state.get("thread_id", "") or requirement_dir.name),
        "workflow_finalize",
        {
            "current_step": "done",
            "status": "completed",
        },
    )
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
    builder.add_node(
        "load_context",
        _load_context_node,
        metadata={"workflow_type": SQL_MODIFY_WORKFLOW_ID, "node_id": "load_context", "stage": "system"},
    )
    builder.add_edge(START, "load_context")

    previous_node = "load_context"
    for node in _workflow_nodes():
        node_id = str(node["id"])
        builder.add_node(node_id, _make_node(node), metadata=node_metadata(node))
        builder.add_edge(previous_node, node_id)
        previous_node = node_id

    builder.add_node(
        "finalize",
        _finalize_node,
        metadata={"workflow_type": SQL_MODIFY_WORKFLOW_ID, "node_id": "finalize", "stage": "system"},
    )
    builder.add_edge(previous_node, "finalize")
    builder.add_edge("finalize", END)

    connection = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    return builder.compile(checkpointer=checkpointer)
