
from pathlib import Path
from typing import Dict, List

from .schemas import RequirementState
from .skill_registry import match_preferred_skills, skill_excerpt


SYSTEM_PROMPT = """你是一个严谨的 SQL 需求交付助手。
你的任务是围绕当前节点生成可直接落盘的结果，不要发散，不要输出无关说明。

要求：
1. 只输出最终结果内容。
2. 优先复用已有文件内容。
3. 信息不足时明确写出待确认项或假设。
4. 当前 workflow_type 是 sql_modify，所有输出都应服务于 SQL 修改工作流。"""


def _block(title: str, content: str) -> str:
    return f"## {title}\n\n{content.strip() or '-'}"


def _node_inputs_block(state: RequirementState, node_id: str) -> str:
    node_inputs = state.get("node_inputs", {}) or {}
    notes = node_inputs.get(node_id, [])
    if not notes:
        return "-"
    return "\n".join(f"- {item}" for item in notes if str(item).strip()) or "-"


def _external_contexts_block(state: RequirementState) -> str:
    contexts = state.get("external_contexts", {}) or {}
    if not contexts:
        return "-"
    sections: List[str] = []
    for key in sorted(contexts.keys()):
        value = str(contexts.get(key, "") or "").strip()
        if not value:
            continue
        sections.append(f"### {key}")
        sections.append(value)
    return "\n\n".join(sections).strip() or "-"


def _skill_context_block(node_def: Dict[str, object]) -> str:
    preferred = list(node_def.get("preferred_skills", []) or [])
    if not preferred:
        return "-"
    matches = match_preferred_skills(preferred)
    available = matches["available"]
    missing = matches["missing"]
    lines: List[str] = []
    if available:
        lines.append("已命中的本地 skill：")
        for item in available:
            lines.append(f"- {item['name']} ({item['path']})")
        for item in available[:2]:
            lines.append("")
            lines.append(f"### Skill: {item['name']}")
            lines.append(skill_excerpt(item["path"], limit=1800))
    if missing:
        if lines:
            lines.append("")
        lines.append("未命中的首选 skill：")
        for item in missing:
            lines.append(f"- {item['name']}")
    return "\n".join(lines).strip() or "-"


def build_node_prompt(state: RequirementState, node_def: Dict[str, object]) -> str:
    artifacts = state.get("artifacts", {}) or {}
    requirement_dir = Path(state["requirement_dir"]).name
    artifact_key = str(node_def.get("artifact_key", "") or "")
    sections: List[str] = [
        f"需求目录：`{requirement_dir}`",
        f"当前节点：`{node_def['id']}` / {node_def['label']}",
        f"节点目标：{node_def.get('goal', '-')}",
        f"输出文件：`{artifact_key or '-'} `",
        f"需求名：{state.get('requirement_name', '') or state.get('title', '') or '-'}",
        f"主TAPD：{state.get('tapd_id', '') or '-'}",
        f"TAPD链接：{state.get('tapd_url', '') or '-'}",
        "",
        "请根据下面上下文为当前节点生成结果。",
        "如果节点对应的是文档或文本产物，请直接输出完整内容。",
        "如果节点对应的是执行计划或修改草稿，请输出适合直接保存的正文。",
        "",
        _block("节点说明", str(node_def.get("instructions", "") or "-")),
        _block("原始需求", str(state.get("brief", "") or "-")),
        _block("外部执行器上下文", _external_contexts_block(state)),
        _block("当前节点补充输入", _node_inputs_block(state, str(node_def["id"]))),
        _block("当前节点可用 Skill", _skill_context_block(node_def)),
        _block("当前 README.md", str(artifacts.get("README.md", "") or "-")),
        _block("当前 docs/需求文档.md", str(artifacts.get("docs/需求文档.md", "") or "-")),
        _block("当前 docs/开发文档.md", str(artifacts.get("docs/开发文档.md", "") or "-")),
        _block("当前 online_sql/source.sql", str(artifacts.get("online_sql/source.sql", "") or "-")),
        _block("当前 draft_sql/modified.sql", str(artifacts.get("draft_sql/modified.sql", "") or "-")),
        _block("当前 自测报告.md", str(artifacts.get("自测报告.md", "") or "-")),
        "",
        "输出要求：",
        "- 直接输出目标结果内容。",
        "- 不要输出代码围栏。",
        "- 不要补充“以下是结果”等解释。",
    ]
    return "\n".join(sections)
