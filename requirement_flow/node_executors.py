import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .debug_trace import log_event
from .llm import ensure_env_loaded
from .schemas import RequirementState

NodeExecutor = Callable[[RequirementState, Dict[str, object], Dict[str, str], Path], Dict[str, object]]
DEFAULT_CODEX_EXECUTABLE = "/Applications/Codex.app/Contents/Resources/codex"
TAPD_REQUIREMENT_FETCHER_SKILL = "/Users/zz/.codex/skills/tapd-requirement-fetcher/SKILL.md"
TAPD_REQUIREMENT_FETCHER_SCRIPT = "/Users/zz/.codex/skills/tapd-requirement-fetcher/scripts/tapd_fetch_story_detail.py"


def _debug_breakpoint(label: str) -> None:
    if str(os.getenv("REQUIREMENT_FLOW_DEBUG_BREAKPOINTS", "") or "").strip() != "1":
        return
    print(f"[debug-breakpoint] {label}")
    breakpoint()


def normalize_tapd_reference(raw_value: str) -> Tuple[str, str]:
    text = str(raw_value or "").strip()
    if not text:
        return "", ""
    if re.fullmatch(r"(?i)tapd[a-z0-9]+", text):
        return text.upper(), ""
    if re.fullmatch(r"\d+", text):
        return f"TAPD{text}", ""
    if text.startswith(("http://", "https://")):
        tapd_id = _extract_tapd_id_from_url(text)
        if tapd_id:
            return tapd_id, text
    raise ValueError(f"Invalid TAPD reference: {text}")


def _extract_tapd_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    candidates = []
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key in ("id", "story_id", "workitem_id", "bug_id"):
        candidates.extend(query.get(key, []))
    candidates.extend(re.findall(r"(?<!\d)(\d{6,})(?!\d)", parsed.path))
    candidates.extend(re.findall(r"(?<!\d)(\d{6,})(?!\d)", parsed.fragment))
    for value in candidates:
        if re.fullmatch(r"\d{6,}", value):
            return f"TAPD{value}"
    return ""


def run_template_command(command_template: str, values: Dict[str, str], cwd: Path) -> str:
    command_template = str(command_template or "").strip()
    if not command_template:
        return ""
    try:
        command = command_template.format(**values)
    except Exception:
        return ""
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def _truncate_text(text: str, limit: int = 400) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + " ..."


def _content_truncation_limit() -> int:
    raw = str(os.getenv("REQUIREMENT_FLOW_CODEX_CONTENT_CHAR_LIMIT", "60000") or "60000").strip()
    try:
        value = int(raw)
    except ValueError:
        return 60000
    return max(value, 200)


def _format_list_block(items: List[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "-"
    return "\n".join(f"- {item}" for item in cleaned)


def _latest_requirement_confirm_note(state: RequirementState) -> str:
    node_inputs = state.get("node_inputs", {}) or {}
    notes = [str(item).strip() for item in node_inputs.get("requirement_confirm", []) or [] if str(item).strip()]
    if not notes:
        return ""
    return notes[-1]


def _pick_tapd_detail_url(state: RequirementState) -> str:
    candidates: List[str] = []
    raw_tapd_url = str(state.get("tapd_url", "") or "").strip()
    if raw_tapd_url:
        candidates.append(raw_tapd_url)
    latest_note = _latest_requirement_confirm_note(state)
    if latest_note:
        url_match = re.search(r"https?://\S+", latest_note)
        if url_match:
            candidates.append(url_match.group(0).rstrip(".,;)]}"))
    for candidate in candidates:
        if "/story/detail/" in candidate:
            return candidate
    return candidates[0] if candidates else ""


def _load_tapd_fetcher_module():
    script_path = Path(TAPD_REQUIREMENT_FETCHER_SCRIPT)
    if not script_path.exists():
        raise FileNotFoundError(f"fetcher script not found: {script_path}")
    spec = importlib.util.spec_from_file_location("tapd_fetch_story_detail_skill", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load fetcher module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_tapd_requirement_detail(
    state: RequirementState,
    requirement_dir: Path,
) -> Tuple[str, str]:
    thread_id = str(state.get("thread_id", "") or requirement_dir.name)
    detail_url = _pick_tapd_detail_url(state)
    if not detail_url:
        status = "skipped: no TAPD detail url found"
        log_event(thread_id, "tapd_requirement_fetch_skipped", {"reason": status})
        return "", status

    script_path = Path(TAPD_REQUIREMENT_FETCHER_SCRIPT)
    if not script_path.exists():
        status = f"skipped: fetcher script not found: {script_path}"
        log_event(thread_id, "tapd_requirement_fetch_skipped", {"reason": status})
        return "", status

    cookie = str(os.getenv("TAPD_COOKIE", "") or "").strip()
    if not cookie:
        status = "skipped: TAPD_COOKIE is missing"
        log_event(
            thread_id,
            "tapd_requirement_fetch_skipped",
            {"reason": status, "detail_url": detail_url},
        )
        return "", status

    log_event(
        thread_id,
        "tapd_requirement_fetch_start",
        {
            "detail_url": detail_url,
            "script": str(script_path),
        },
    )
    try:
        module = _load_tapd_fetcher_module()
        detail = module.fetch_story_body(detail_url, cookie)
    except Exception as exc:
        status = f"failed: tapd fetch invocation error: {_truncate_text(str(exc), 240)}"
        log_event(thread_id, "tapd_requirement_fetch_error", {"detail_url": detail_url, "error": str(exc)})
        return "", status

    markdown_description = str(detail.get("markdown_description", "") or "").strip()
    description = str(detail.get("description", "") or "").strip()
    if markdown_description:
        full_text = module.normalize_text(markdown_description)
        source_field = "markdown_description"
    else:
        full_text = module.html_to_text(description)
        source_field = "description"

    if not full_text:
        status = "failed: tapd fetch returned empty full_text"
        log_event(
            thread_id,
            "tapd_requirement_fetch_empty",
            {
                "detail_url": detail_url,
                "story_id": str(detail.get("story_id", "") or ""),
            },
        )
        return "", status

    normalized = {
        "detail_url": str(detail.get("detail_url", detail_url) or detail_url),
        "workspace_id": str(detail.get("workspace_id", "") or ""),
        "story_id": str(detail.get("story_id", "") or ""),
        "name": str(detail.get("name", "") or ""),
        "status": str(detail.get("status", "") or ""),
        "source": "tapd-detail",
        "source_field": source_field,
        "full_text": full_text,
    }
    result_text = json.dumps(normalized, ensure_ascii=False, indent=2)
    status = f"ok: tapd fetch produced {len(result_text)} chars"
    log_event(
        thread_id,
        "tapd_requirement_fetch_done",
        {
            "detail_url": detail_url,
            "output_len": len(result_text),
            "story_id": normalized["story_id"],
            "workspace_id": normalized["workspace_id"],
            "tapd_requirement_detail": normalized,
        },
    )
    return result_text, status


def _external_context_summary(contexts: Dict[str, str]) -> str:
    content_limit = _content_truncation_limit()
    sections: List[str] = []
    for key in sorted(contexts.keys()):
        if key in {"codex_requirement_context", "requirement_confirm_executor_status"}:
            continue
        value = str(contexts.get(key, "") or "").strip()
        if not value:
            continue
        sections.append(f"### {key}")
        sections.append(_truncate_text(value, content_limit))
    return "\n\n".join(sections).strip() or "-"


def _resolve_codex_executable() -> str:
    configured = str(os.getenv("REQUIREMENT_FLOW_CODEX_BIN", "") or "").strip()
    if configured:
        return configured
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    if Path(DEFAULT_CODEX_EXECUTABLE).exists():
        return DEFAULT_CODEX_EXECUTABLE
    return ""


def _build_requirement_confirm_codex_prompt(
    state: RequirementState,
    artifacts: Dict[str, str],
    requirement_dir: Path,
    normalized_tapd_id: str,
    normalized_tapd_url: str,
    contexts: Dict[str, str],
) -> str:
    content_limit = _content_truncation_limit()
    latest_note = _latest_requirement_confirm_note(state)
    notes = [_truncate_text(latest_note, content_limit)] if latest_note else []
    brief = _truncate_text(str(state.get("brief", "") or "-").strip() or "-", content_limit)
    current_doc = _truncate_text(str(artifacts.get("docs/需求文档.md", "") or "-").strip() or "-", content_limit)
    requirement_name = str(state.get("requirement_name", "") or state.get("title", "") or "-").strip() or "-"
    tapd_requirement_detail = _truncate_text(str(contexts.get("tapd_requirement_detail", "") or "-").strip() or "-", content_limit)
    tapd_fetch_status = _truncate_text(str(contexts.get("tapd_requirement_fetch_status", "") or "-").strip() or "-", content_limit)

    sections = [
        "你在 requirement_confirm 节点的预处理阶段工作。",
        "当前 TAPD 正文已经由本地方法预提取完成。",
        "你的任务是优先基于已经提取出来的 TAPD 内容，直接产出可写入 docs/需求文档.md 的需求文档正文。",
        "不要再次调用任何 TAPD skill、脚本或外部抓取逻辑。",
        "限制：不要修改任何文件，不要输出代码围栏，不要解释你的过程，不要围绕旧需求文档做二次总结。",
        "如果 TAPD 提取结果为空或失败，只能基于已知输入说明失败原因、缺失输入和下一步需要补什么。",
        "",
        "输出要求：",
        "1. 直接输出需求文档正文。",
        "2. 文档标题使用 `# 需求文档`。",
        "3. 文档至少包含：需求名称、需求背景、需求目标、需求范围、来源表/目标表、字段改动项、核心规则与约束、待确认项。",
        "4. 基于 TAPD 全文做事实整理，避免编造未给出的规则。",
        "5. 对未明确的信息，用“待确认”写清楚，不要省略。",
        "",
        f"需求目录：{requirement_dir.name}",
        f"需求名：{requirement_name}",
        f"TAPD ID：{normalized_tapd_id or '-'}",
        f"TAPD URL：{normalized_tapd_url or '-'}",
        "",
        "## TAPD提取状态",
        tapd_fetch_status,
        "",
        "## TAPD提取内容",
        tapd_requirement_detail,
        "",
        "## 原始需求",
        brief,
        "",
        "## 当前节点补充输入",
        _format_list_block(notes),
        "",
        "## 已有外部上下文",
        _external_context_summary(contexts),
        "",
        "## 当前需求文档",
        current_doc,
    ]
    return "\n".join(sections).strip()


def _run_requirement_confirm_codex(
    state: RequirementState,
    artifacts: Dict[str, str],
    requirement_dir: Path,
    normalized_tapd_id: str,
    normalized_tapd_url: str,
    contexts: Dict[str, str],
) -> Tuple[str, str]:
    thread_id = str(state.get("thread_id", "") or requirement_dir.name)
    executable = _resolve_codex_executable()
    if not executable:
        status = "skipped: local codex executable not found"
        log_event(
            thread_id,
            "requirement_confirm_executor_skipped",
            {
                "reason": status,
            },
        )
        return "", status

    prompt = _build_requirement_confirm_codex_prompt(
        state,
        artifacts,
        requirement_dir,
        normalized_tapd_id,
        normalized_tapd_url,
        contexts,
    )
    sandbox_mode = str(os.getenv("REQUIREMENT_FLOW_CODEX_SANDBOX", "read-only") or "read-only").strip() or "read-only"
    model = str(os.getenv("REQUIREMENT_FLOW_CODEX_MODEL", "") or "").strip()
    profile = str(os.getenv("REQUIREMENT_FLOW_CODEX_PROFILE", "") or "").strip()
    timeout_sec = int(str(os.getenv("REQUIREMENT_FLOW_CODEX_TIMEOUT_SEC", "180") or "180").strip() or "180")

    with tempfile.NamedTemporaryFile(prefix="requirement-confirm-", suffix=".md", delete=False) as tmp_file:
        output_path = Path(tmp_file.name)

    args = [
        executable,
        "exec",
        "-",
        "--skip-git-repo-check",
        "--sandbox",
        sandbox_mode,
        "--output-last-message",
        str(output_path),
        "--cd",
        str(requirement_dir),
    ]
    if model:
        args.extend(["--model", model])
    if profile:
        args.extend(["--profile", profile])

    log_event(
        thread_id,
        "requirement_confirm_executor_start",
        {
            "executable": executable,
            "sandbox_mode": sandbox_mode,
            "model": model,
            "profile": profile,
            "prompt_len": len(prompt),
            "prompt": prompt,
        },
    )
    try:
        completed = subprocess.run(
            args,
            cwd=str(requirement_dir),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        output_path.unlink(missing_ok=True)
        status = f"failed: local codex timed out after {timeout_sec}s"
        log_event(
            thread_id,
            "requirement_confirm_executor_timeout",
            {
                "executable": executable,
                "timeout_sec": timeout_sec,
            },
        )
        return "", status
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        status = f"failed: local codex invocation error: {_truncate_text(str(exc), 24000)}"
        log_event(
            thread_id,
            "requirement_confirm_executor_error",
            {
                "executable": executable,
                "error": str(exc),
            },
        )
        return "", status

    output = ""
    try:
        output = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
    except Exception:
        output = ""
    output_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        status = (
            f"failed: local codex exited with code {completed.returncode}; "
            f"stderr={_truncate_text(completed.stderr, 24000) or '-'}"
        )
        log_event(
            thread_id,
            "requirement_confirm_executor_failed",
            {
                "executable": executable,
                "returncode": completed.returncode,
                "stderr": _truncate_text(completed.stderr, 10000),
                "stdout": _truncate_text(completed.stdout, 10000),
            },
        )
        return output, status

    if not output:
        status = "failed: local codex completed but returned empty output"
        log_event(
            thread_id,
            "requirement_confirm_executor_empty",
            {
                "executable": executable,
                "returncode": completed.returncode,
                "stdout": _truncate_text(completed.stdout, 10000),
                "stderr": _truncate_text(completed.stderr, 10000),
            },
        )
        return "", status

    status = f"ok: local codex produced {len(output)} chars"
    log_event(
        thread_id,
        "requirement_confirm_executor_done",
        {
            "executable": executable,
            "returncode": completed.returncode,
            "output_len": len(output),
            "stdout": _truncate_text(completed.stdout, 10000),
            "stderr": _truncate_text(completed.stderr, 10000),
        },
    )
    return output, status


def requirement_confirm_prepare(
    state: RequirementState,
    node_def: Dict[str, object],
    artifacts: Dict[str, str],
    requirement_dir: Path,
) -> Dict[str, object]:
    _debug_breakpoint("node_executors.requirement_confirm_prepare")
    del node_def
    ensure_env_loaded()

    normalized_tapd_id = str(state.get("tapd_id", "") or "").strip()
    normalized_tapd_url = str(state.get("tapd_url", "") or "").strip()
    if normalized_tapd_url or normalized_tapd_id:
        normalized_tapd_id, normalized_tapd_url = normalize_tapd_reference(normalized_tapd_url or normalized_tapd_id)

    contexts = dict(state.get("external_contexts", {}) or {})
    fetched_requirement, fetch_status = fetch_tapd_requirement_detail(state, requirement_dir)
    contexts["tapd_requirement_fetch_status"] = fetch_status
    if fetched_requirement:
        contexts["tapd_requirement_detail"] = fetched_requirement
    fetched, status = _run_requirement_confirm_codex(
        state,
        artifacts,
        requirement_dir,
        normalized_tapd_id,
        normalized_tapd_url,
        contexts,
    )
    contexts["requirement_confirm_executor_status"] = status
    if fetched:
        contexts["codex_requirement_context"] = fetched

    updates: Dict[str, object] = {
        "tapd_id": normalized_tapd_id,
        "tapd_url": normalized_tapd_url,
    }
    if fetched:
        updates["artifacts"] = {
            "docs/需求文档.md": fetched,
        }
    if contexts:
        updates["external_contexts"] = contexts
    return updates


def fetch_and_confirm_changes_prepare(
    state: RequirementState,
    node_def: Dict[str, object],
    artifacts: Dict[str, str],
    requirement_dir: Path,
) -> Dict[str, object]:
    del node_def
    pulled_sql = pull_online_sql_content(state, requirement_dir, artifacts)
    if not pulled_sql.strip():
        return {}
    return {
        "artifacts": {
            "online_sql/source.sql": pulled_sql,
        }
    }


def fetch_and_confirm_changes_finalize(
    state: RequirementState,
    node_def: Dict[str, object],
    artifacts: Dict[str, str],
    requirement_dir: Path,
) -> Dict[str, object]:
    del node_def, artifacts
    content = str(state.get("artifacts", {}).get("online_sql/source.sql", "") or "")
    content = str(state.get("_current_node_final_content", "") or content)
    return apply_fetch_node_state_updates(state, content, requirement_dir)


def safe_read_text(path: Path) -> str:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return ""


def extract_fetch_fields(content: str) -> Dict[str, str]:
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


def resolve_requirement_local_path(requirement_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (requirement_dir / candidate).resolve()


def pull_online_sql_from_env_command(
    state: RequirementState,
    requirement_dir: Path,
    fields: Dict[str, str],
) -> str:
    ensure_env_loaded()
    command_template = os.getenv("REQUIREMENT_FLOW_SQL_FETCH_CMD", "").strip()
    if not command_template:
        return ""

    return run_template_command(
        command_template,
        {
            "thread_id": str(state.get("thread_id", "") or ""),
            "tapd_id": str(state.get("tapd_id", "") or ""),
            "tapd_url": str(state.get("tapd_url", "") or ""),
            "requirement_dir": str(requirement_dir),
            "task_id": fields.get("task_id", ""),
            "task_name": fields.get("task_name", ""),
            "script_path": fields.get("script_path", ""),
            "source_type": fields.get("source_type", ""),
            "local_sql_path": fields.get("local_sql_path", ""),
        },
        requirement_dir,
    )


def pull_online_sql_content(
    state: RequirementState,
    requirement_dir: Path,
    artifacts: Dict[str, str],
) -> str:
    existing_artifact = str(artifacts.get("online_sql/source.sql", "") or "").strip()
    if existing_artifact:
        return existing_artifact + "\n"

    raw_fetch_doc = str(artifacts.get("online_sql/source.sql", "") or "")
    raw_dev_doc = str(artifacts.get("docs/开发文档.md", "") or "")
    fields = extract_fetch_fields(raw_fetch_doc)
    if not fields.get("script_path"):
        doc_fields = extract_fetch_fields(raw_dev_doc)
        fields = {**doc_fields, **{key: value for key, value in fields.items() if value}}

    fetched = pull_online_sql_from_env_command(state, requirement_dir, fields)
    if fetched.strip():
        return fetched + "\n"

    candidates = []
    local_sql_path = str(state.get("local_sql_path", "") or "").strip()
    if local_sql_path:
        candidates.append(resolve_requirement_local_path(requirement_dir, local_sql_path))
    parsed_local_sql_path = fields.get("local_sql_path", "").strip()
    if parsed_local_sql_path:
        candidates.append(resolve_requirement_local_path(requirement_dir, parsed_local_sql_path))
    parsed_script_path = fields.get("script_path", "").strip()
    if parsed_script_path:
        candidates.append(resolve_requirement_local_path(requirement_dir, parsed_script_path))
    candidates.append(requirement_dir / "online_sql" / "source.sql")

    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        content = safe_read_text(path).strip()
        if content:
            return content + "\n"
    return ""


def apply_fetch_node_state_updates(
    state: RequirementState,
    content: str,
    requirement_dir: Path,
) -> Dict[str, object]:
    fields = extract_fetch_fields(content)
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
        resolved = resolve_requirement_local_path(requirement_dir, local_sql_path)
        updates["local_sql_path"] = str(resolved)
    return updates


PREPARE_EXECUTORS: Dict[str, NodeExecutor] = {
    "requirement_confirm": requirement_confirm_prepare,
    "fetch_and_confirm_changes": fetch_and_confirm_changes_prepare,
}

FINALIZE_EXECUTORS: Dict[str, NodeExecutor] = {
    "fetch_and_confirm_changes": fetch_and_confirm_changes_finalize,
}


def run_prepare_executor(
    node_id: str,
    state: RequirementState,
    node_def: Dict[str, object],
    artifacts: Dict[str, str],
    requirement_dir: Path,
) -> Dict[str, object]:
    executor = PREPARE_EXECUTORS.get(node_id)
    if not executor:
        return {}
    return executor(state, node_def, artifacts, requirement_dir)


def run_finalize_executor(
    node_id: str,
    state: RequirementState,
    node_def: Dict[str, object],
    artifacts: Dict[str, str],
    requirement_dir: Path,
) -> Dict[str, object]:
    executor = FINALIZE_EXECUTORS.get(node_id)
    if not executor:
        return {}
    return executor(state, node_def, artifacts, requirement_dir)
