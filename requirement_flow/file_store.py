
import json
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import THREADS_DIR
from .schemas import RequirementState, StepSpec
from .workflow_defs import get_workflow_definition


def _copy_missing_tree(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_missing_tree(item, target)
        elif not target.exists():
            shutil.copy2(item, target)


def sync_requirement_template(requirement_dir: Path, template_dir: Path) -> None:
    requirement_dir.mkdir(parents=True, exist_ok=True)
    _copy_missing_tree(template_dir, requirement_dir)


def read_requirement_files(requirement_dir: Path, step_specs: Iterable[StepSpec]) -> Dict[str, str]:
    artifacts: Dict[str, str] = {}
    readme = requirement_dir / "README.md"
    artifacts["README.md"] = readme.read_text(encoding="utf-8") if readme.exists() else ""
    dev_doc = requirement_dir / "docs" / "开发文档.md"
    artifacts["docs/开发文档.md"] = dev_doc.read_text(encoding="utf-8") if dev_doc.exists() else ""
    online_sql = requirement_dir / "online_sql" / "source.sql"
    artifacts["online_sql/source.sql"] = online_sql.read_text(encoding="utf-8") if online_sql.exists() else ""
    modified_sql = requirement_dir / "draft_sql" / "modified.sql"
    artifacts["draft_sql/modified.sql"] = modified_sql.read_text(encoding="utf-8") if modified_sql.exists() else ""
    self_test_report = requirement_dir / "自测报告.md"
    artifacts["自测报告.md"] = self_test_report.read_text(encoding="utf-8") if self_test_report.exists() else ""
    for spec in step_specs:
        target = requirement_dir / spec.target_file
        artifacts[spec.target_file] = target.read_text(encoding="utf-8") if target.exists() else ""
    return artifacts


def write_requirement_files(requirement_dir: Path, artifacts: Dict[str, str]) -> None:
    for relative_path, content in artifacts.items():
        target = requirement_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")


def thread_runtime_dir(thread_id: str) -> Path:
    path = THREADS_DIR / thread_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_runtime_snapshot(thread_id: str, snapshot: Dict[str, object]) -> Path:
    target = thread_runtime_dir(thread_id) / "latest_state.json"
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def write_interrupt_payload(thread_id: str, payload: Optional[object]) -> Path:
    target = thread_runtime_dir(thread_id) / "latest_interrupt.json"
    data = payload if payload is not None else {}
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def render_requirement_readme(state: RequirementState, ordered_specs: List[StepSpec]) -> str:
    requirement_dir = Path(state["requirement_dir"])
    workflow_type = str(state.get("workflow_type", "sql_modify") or "sql_modify")
    workflow_def = get_workflow_definition(workflow_type)
    statuses = state.get("node_statuses", {})
    predecessor_requirements = state.get("predecessor_requirements", [])
    predecessor_text = ", ".join(predecessor_requirements) if predecessor_requirements else "-"
    current_status = state.get("status", "unknown")
    lines = [
        "# 需求目录入口",
        "",
        "## 基本信息",
        "",
        f"- 目录名：{requirement_dir.name}",
        f"- 主 TAPD：{state.get('tapd_id', '') or '-'}",
        f"- 关联 TAPD：-",
        f"- 前置需求：{predecessor_text}",
        f"- 当前状态：{current_status}",
        f"- Workflow Type：{workflow_type}",
        "- 负责人：",
        "",
        "## 当前结论",
        "",
        f"- 标题：{state.get('requirement_name', '') or state.get('title', '') or '-'}",
        f"- 最近流程节点：{state.get('current_step', '') or '-'}",
        "",
        "## 目录导航",
        "",
    ]
    seen_artifacts = set()
    for node in workflow_def["nodes"]:
        status = statuses.get(str(node["id"]), {}).get("status", "pending")
        artifact_key = str(node.get("artifact_key", "") or "-")
        if artifact_key in seen_artifacts:
            continue
        seen_artifacts.add(artifact_key)
        lines.append(f"- `{artifact_key}`：{node['label']}（{status}）")
    lines.extend(
        [
            "- `docs/`：开发文档与任务说明",
            "- `online_sql/`：拉取下来的线上 SQL",
            "- `draft_sql/`：本地修改草稿",
            "- `validation_sql/`：校验 SQL",
            "- `validation_results/`：校验结果",
            "- `自测报告.md`：自测报告",
        ]
    )
    return "\n".join(lines)
