
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .config import TEMPLATE_DIR, ensure_runtime_dirs, resolve_requirement_dir
from .dashboard import serve_dashboard
from .file_store import (
    thread_runtime_dir,
    write_interrupt_payload,
    write_runtime_snapshot,
)
from .graph import build_requirement_graph
from .schemas import RequirementState


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
    return {
        "thread_id": requirement_dir.name,
        "requirement_dir": str(requirement_dir),
        "template_dir": str(TEMPLATE_DIR),
        "title": _derive_title(requirement_dir, args.title),
        "tapd_id": _derive_tapd_id(requirement_dir, args.tapd_id),
        "predecessor_requirements": args.predecessor or [],
        "brief": brief,
        "interactive_review": not args.auto_approve,
        "status": "created",
    }


def run_command(args: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    requirement_dir = resolve_requirement_dir(args.requirement_dir)
    state = _initial_state_from_args(args, requirement_dir)
    graph = build_requirement_graph()
    config = {"configurable": {"thread_id": requirement_dir.name}}
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
    config = {"configurable": {"thread_id": requirement_dir.name}}
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
