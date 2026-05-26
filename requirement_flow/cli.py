
import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .chat_store import (
    append_session_message,
    build_session_id,
    create_draft_item,
    create_thread_item,
    ensure_bridge_runtime_dirs,
    open_item_count,
    utc_now_iso,
)
from .config import TEMPLATE_DIR, ensure_runtime_dirs, resolve_requirement_dir
from .dashboard import serve_dashboard
from .file_store import (
    thread_runtime_dir,
    write_interrupt_payload,
    write_runtime_snapshot,
)
from .graph import build_requirement_graph
from .node_executors import normalize_tapd_reference
from .runtime import create_requirement_thread
from .schemas import RequirementState
from .tracing import graph_run_config


def _load_text(path: Optional[str], inline: Optional[str]) -> str:
    if inline:
        return inline
    if path:
        return Path(path).expanduser().read_text(encoding="utf-8")
    return ""


def _derive_tapd_id(requirement_dir: Path, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    name = requirement_dir.name
    if "_" in name:
        return name.split("_", 1)[0]
    return name


def _derive_title(requirement_dir: Path, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if "_" in requirement_dir.name:
        return requirement_dir.name.split("_", 1)[1].replace("-", " ")
    return requirement_dir.name


def _graph_snapshot(graph: Any, config: Dict[str, object], fallback: Dict[str, object]) -> Dict[str, object]:
    try:
        snapshot = graph.get_state(config)
        values = getattr(snapshot, "values", None)
        if isinstance(values, dict):
            return values
    except Exception:
        pass
    return fallback


def _interrupt_payload(result: Dict[str, object]) -> Optional[object]:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    if isinstance(interrupts, (list, tuple)) and interrupts:
        interrupt = interrupts[0]
        return getattr(interrupt, "value", interrupt)
    return interrupts


def _print_result(thread_id: str, state: Dict[str, object], interrupt_payload: Optional[object]) -> None:
    summary = {
        "thread_id": thread_id,
        "status": state.get("status"),
        "current_step": state.get("current_step"),
        "interrupted": bool(interrupt_payload),
        "runtime_dir": str(thread_runtime_dir(thread_id)),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if interrupt_payload:
        print(json.dumps({"interrupt": interrupt_payload}, ensure_ascii=False, indent=2))


def _initial_state_from_args(args: argparse.Namespace, requirement_dir: Path) -> RequirementState:
    brief = _load_text(args.brief_file, args.brief_text)
    derived_tapd = _derive_tapd_id(requirement_dir, args.tapd_id)
    tapd_url = ""
    try:
        normalized_tapd_id, tapd_url = normalize_tapd_reference(derived_tapd)
    except ValueError:
        normalized_tapd_id = derived_tapd
    return {
        "thread_id": requirement_dir.name,
        "requirement_dir": str(requirement_dir),
        "template_dir": str(TEMPLATE_DIR),
        "title": _derive_title(requirement_dir, args.title),
        "tapd_id": normalized_tapd_id,
        "tapd_url": tapd_url,
        "predecessor_requirements": args.predecessor or [],
        "brief": brief,
        "interactive_review": not args.auto_approve,
        "external_contexts": {},
        "status": "created",
    }


def run_command(args: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    requirement_dir = resolve_requirement_dir(args.requirement_dir)
    state = _initial_state_from_args(args, requirement_dir)
    graph = build_requirement_graph()
    config = graph_run_config(state, operation="cli_run")
    try:
        result = graph.invoke(state, config=config)
        interrupt_payload = _interrupt_payload(result)
        snapshot = _graph_snapshot(graph, config, result)
        write_runtime_snapshot(requirement_dir.name, snapshot)
        write_interrupt_payload(requirement_dir.name, interrupt_payload)
        _print_result(requirement_dir.name, snapshot, interrupt_payload)
        return 0
    except Exception as exc:
        failure_state = {
            **state,
            "status": "failed",
            "last_error": str(exc),
        }
        write_runtime_snapshot(requirement_dir.name, failure_state)
        write_interrupt_payload(requirement_dir.name, None)
        print(str(exc), file=sys.stderr)
        return 1


def _parse_message_options(text: str) -> Dict[str, str]:
    try:
        tokens = shlex.split(str(text or "").strip(), posix=False)
    except ValueError:
        tokens = str(text or "").strip().split()
    start = next((index for index, token in enumerate(tokens) if str(token).startswith("--")), -1)
    if start < 0:
        return {}

    options: Dict[str, str] = {}
    index = start
    while index < len(tokens):
        token = str(tokens[index] or "")
        if not token.startswith("--"):
            index += 1
            continue

        key = token[2:].lower()
        values = []
        index += 1
        while index < len(tokens) and not str(tokens[index] or "").startswith("--"):
            values.append(str(tokens[index]))
            index += 1
        options[key] = " ".join(values).strip()
    return options


def _first_non_empty(*values: Optional[str]) -> str:
    for value in values:
        if not isinstance(value, str):
            continue
        trimmed = value.strip()
        if trimmed:
            return trimmed
        if value == "":
            return ""
    return ""


def _is_truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_message_context(args: argparse.Namespace) -> Dict[str, str]:
    user_open_id = str(args.user_open_id or "").strip() or "unknown-user"
    chat_type = str(args.chat_type or "").strip() or "unknown"
    chat_id = str(args.chat_id or "").strip() or "unknown-chat"
    return {
        "session_id": build_session_id(chat_type, chat_id, user_open_id),
        "user_open_id": user_open_id,
        "chat_type": chat_type,
        "chat_id": chat_id,
        "event_id": str(args.event_id or "").strip(),
        "message_id": str(args.message_id or "").strip(),
    }


def _record_incoming_message(context: Dict[str, str], text: str) -> None:
    append_session_message(
        context["session_id"],
        {
            "ts": utc_now_iso(),
            "direction": "in",
            "text": text,
            "chat_type": context["chat_type"],
            "chat_id": context["chat_id"],
            "user_open_id": context["user_open_id"],
            "event_id": context["event_id"],
            "message_id": context["message_id"],
        },
    )


def _extract_create_title(text: str) -> str:
    normalized = str(text or "").strip()
    patterns = [
        r"^(?:创建(?:一个)?(?:新)?需求|新建需求|创建工作流|新建工作流)\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match:
            return match.group(1).strip()
    return ""


def handle_message_command(args: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    ensure_bridge_runtime_dirs()
    context = _build_message_context(args)
    raw_text = str(args.text or "").strip()
    _record_incoming_message(context, raw_text)

    options = _parse_message_options(args.text)
    if not options:
        create_title = _extract_create_title(raw_text)
        if create_title:
            item = create_draft_item(context["session_id"], context["user_open_id"], create_title, raw_text)
            print(
                "\n".join(
                    [
                        "已记录需求草稿",
                        f"事项: {item['item_id']}",
                        f"标题: {item['title']}",
                        f"状态: {item['status']}",
                        f"会话未完成事项: {open_item_count(context['session_id'])}",
                    ]
                )
            )
            return 0
        print(
            "Unsupported message.\n"
            "Use: 创建工作流 --tapd-id <id> --short-name <slug> (--brief <text> | --brief-file <path>) [--title <text>] [--auto-approve]"
        )
        return 0

    tapd_id = _first_non_empty(options.get("tapd-id"), options.get("tapd"), options.get("t"))
    short_name = _first_non_empty(options.get("short-name"), options.get("short"), options.get("s"))
    brief = _first_non_empty(options.get("brief"), options.get("b"))
    brief_file = _first_non_empty(options.get("brief-file"))
    title = _first_non_empty(options.get("title"))
    auto_approve_raw = _first_non_empty(options.get("auto-approve"))
    auto_start = auto_approve_raw == "" or _is_truthy(auto_approve_raw)

    if not tapd_id or not short_name or (not brief and not brief_file):
        print(
            "Usage:\n"
            "创建工作流 --tapd-id <id> --short-name <slug> (--brief <text> | --brief-file <path>) [--title <text>] [--auto-approve]"
        )
        return 0

    if brief_file:
        brief = Path(brief_file).expanduser().read_text(encoding="utf-8")

    try:
        result = create_requirement_thread(
            tapd_id=tapd_id,
            short_name=short_name,
            title=title,
            brief=brief,
            predecessors=[],
            auto_start=auto_start,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    state = dict(result.get("state", {}) or {})
    interrupt_payload = result.get("interrupt")
    thread_id = str(result.get("thread_id", "") or "")
    create_thread_item(
        context["session_id"],
        context["user_open_id"],
        thread_id=thread_id,
        title=title or short_name,
        brief=brief,
        status=str(state.get("status", "unknown") or "unknown"),
    )
    print(
        "\n".join(
            [
                "Workflow created",
                f"Thread: {thread_id or '-'}",
                f"Status: {state.get('status', 'unknown')}",
                f"Current: {state.get('current_step', 'unknown')}",
                f"Review: {'pending' if interrupt_payload else 'none'}",
                f"会话未完成事项: {open_item_count(context['session_id'])}",
            ]
        )
    )
    return 0


def _load_resume_payload(args: argparse.Namespace) -> object:
    if args.approve:
        return {"action": "approve"}
    if args.edit_file:
        return {"action": "edit", "content": Path(args.edit_file).expanduser().read_text(encoding="utf-8")}
    if args.resume_json:
        return json.loads(args.resume_json)
    if args.resume_file:
        return json.loads(Path(args.resume_file).expanduser().read_text(encoding="utf-8"))
    raise ValueError("resume requires --approve, --edit-file, --resume-json, or --resume-file")


def resume_command(args: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    requirement_dir = resolve_requirement_dir(args.requirement_dir)
    graph = build_requirement_graph()
    config = graph_run_config(
        {
            "thread_id": requirement_dir.name,
            "requirement_dir": str(requirement_dir),
            "workflow_type": "sql_modify",
        },
        operation="cli_resume",
    )
    from langgraph.types import Command

    payload = _load_resume_payload(args)
    try:
        result = graph.invoke(Command(resume=payload), config=config)
        interrupt_payload = _interrupt_payload(result)
        snapshot = _graph_snapshot(graph, config, result)
        write_runtime_snapshot(requirement_dir.name, snapshot)
        write_interrupt_payload(requirement_dir.name, interrupt_payload)
        _print_result(requirement_dir.name, snapshot, interrupt_payload)
        return 0
    except Exception as exc:
        failure_state = {
            "thread_id": requirement_dir.name,
            "requirement_dir": str(requirement_dir),
            "status": "failed",
            "last_error": str(exc),
        }
        write_runtime_snapshot(requirement_dir.name, failure_state)
        write_interrupt_payload(requirement_dir.name, None)
        print(str(exc), file=sys.stderr)
        return 1


def show_state_command(args: argparse.Namespace) -> int:
    requirement_dir = resolve_requirement_dir(args.requirement_dir)
    state_path = thread_runtime_dir(requirement_dir.name) / "latest_state.json"
    if not state_path.exists():
        print(f"Missing state snapshot: {state_path}", file=sys.stderr)
        return 1
    print(state_path.read_text(encoding="utf-8"))
    return 0


def serve_command(args: argparse.Namespace) -> int:
    serve_dashboard(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LangGraph + Codex requirement flow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Start or restart a requirement workflow thread.")
    run_parser.add_argument("--requirement-dir", required=True, help="Requirement directory name or path.")
    run_parser.add_argument("--brief-file", help="Path to the raw requirement input markdown/text.")
    run_parser.add_argument("--brief-text", help="Inline raw requirement input.")
    run_parser.add_argument("--tapd-id", help="Explicit TAPD id.")
    run_parser.add_argument("--title", help="Explicit requirement title.")
    run_parser.add_argument("--predecessor", action="append", help="Predecessor requirement directory name.")
    run_parser.add_argument("--auto-approve", action="store_true", help="Skip interrupt-based human review.")
    run_parser.set_defaults(func=run_command)

    message_parser = subparsers.add_parser("handle-message", help="Route a raw chat message into workflow handling.")
    message_parser.add_argument("--text", required=True, help="Raw chat message text.")
    message_parser.add_argument("--chat-id", help="Chat id.")
    message_parser.add_argument("--chat-type", help="Chat type.")
    message_parser.add_argument("--user-open-id", help="Sender open id.")
    message_parser.add_argument("--message-id", help="Feishu message id.")
    message_parser.add_argument("--event-id", help="Feishu event id.")
    message_parser.set_defaults(func=handle_message_command)

    resume_parser = subparsers.add_parser("resume", help="Resume a paused review step.")
    resume_parser.add_argument("--requirement-dir", required=True, help="Requirement directory name or path.")
    resume_group = resume_parser.add_mutually_exclusive_group(required=True)
    resume_group.add_argument("--approve", action="store_true", help="Approve the current review step.")
    resume_group.add_argument("--edit-file", help="Replace current step content with the provided markdown file.")
    resume_group.add_argument("--resume-json", help="Raw JSON payload passed to Command(resume=...).")
    resume_group.add_argument("--resume-file", help="Path to a JSON file passed to Command(resume=...).")
    resume_parser.set_defaults(func=resume_command)

    show_parser = subparsers.add_parser("show-state", help="Print the latest runtime state snapshot.")
    show_parser.add_argument("--requirement-dir", required=True, help="Requirement directory name or path.")
    show_parser.set_defaults(func=show_state_command)

    serve_parser = subparsers.add_parser("serve", help="Start the local workflow dashboard.")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host, default 127.0.0.1.")
    serve_parser.add_argument("--port", type=int, default=8787, help="Bind port, default 8787.")
    serve_parser.set_defaults(func=serve_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
