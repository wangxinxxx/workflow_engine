
from typing import Dict, List


SQL_MODIFY_WORKFLOW_ID = "sql_modify"


# SQL 修改类 workflow:
# 1. 先确认需求本身
# 2. 再确认执行任务
# 3. 拉取线上 SQL 与改动范围
# 4. 生成本地草稿
# 5. 完成自测校验
# 6. 整理最终交付
SQL_MODIFY_WORKFLOW: Dict[str, object] = {
    "id": SQL_MODIFY_WORKFLOW_ID,
    "label": "SQL Modification Workflow",
    "description": "Single-workflow baseline for SQL change requests. Keep the chain short and let each key node support approve/edit/rerun_with_input.",
    "stages": [
        {
            "id": "requirement",
            "label": "需求确认与理解",
            "nodes": ["requirement_confirm"],
        },
        {
            "id": "execution",
            "label": "执行阶段",
            "nodes": [
                "task_confirm",
                "fetch_and_confirm_changes",
                "local_edit_draft",
                "validate_result",
            ],
        },
        {
            "id": "delivery",
            "label": "交付",
            "nodes": ["deliver"],
        },
    ],
    "nodes": [
        # 节点 1:
        # 需求理解节点，负责把原始需求整理成可执行的确认稿。
        # 这是整个流程的入口，允许反复补充信息并重跑，直到需求确认通过。
        {
            "id": "requirement_confirm",
            "label": "需求确认",
            "stage": "requirement",
            "type": "review_loop",
            "goal": "确认需求理解、范围、目标和约束。",
            "instructions": "基于原始需求和 TAPD 内容生成需求文档，并支持人工补充/修正后重跑，直到确认通过。",
            "inputs": ["brief", "tapd_id", "requirement_name"],
            "outputs": ["requirement_confirmed_file"],
            "artifact_key": "docs/需求文档.md",
            "llm_enabled": False,
            "preferred_skills": ["requirement-doc-parser", "tapd-requirement-fetcher", "tapd-requirement-intake"],
            "actions": ["approve", "edit", "rerun_with_input"],
        },
        # 节点 2:
        # 任务确认节点，负责把已确认需求拆成最终任务清单。
        # 后续所有执行节点都应以这里锁定的任务为准继续推进。
        {
            "id": "task_confirm",
            "label": "确认任务",
            "stage": "execution",
            "type": "review_loop",
            "goal": "基于已确认需求补充执行范围，并更新开发文档。",
            "instructions": "基于需求文档生成并更新开发文档，明确执行任务、对象范围、改动项和待确认约束，支持人工编辑或补充执行约束后重跑。",
            "inputs": ["requirement_confirmed_file"],
            "outputs": ["task_file"],
            "artifact_key": "docs/开发文档.md",
            "preferred_skills": ["requirement-doc-parser"],
            "actions": ["approve", "edit", "rerun_with_input"],
        },
        # 节点 3:
        # 拉取代码并确认改动项，负责定位线上对象、拉取 SQL 到本地，
        # 同时确认这次到底改哪些脚本/字段/逻辑。
        {
            "id": "fetch_and_confirm_changes",
            "label": "拉取代码并确认改动项",
            "stage": "execution",
            "type": "review_loop",
            "goal": "定位改动对象、拉取上下文，并确认最终改动项。",
            "instructions": "拉取代码、确认对应脚本信息、本地 SQL 路径和最终改动项。",
            "inputs": ["task_file"],
            "outputs": ["source_script_info", "local_sql_path", "change_confirm_file"],
            "artifact_key": "online_sql/source.sql",
            "preferred_skills": ["task-platform-tool-query", "xinghe-sql-api-runner", "58-task-list-opencli-skill"],
            "actions": ["approve", "edit", "rerun_with_input"],
        },
        # 节点 4:
        # 本地编辑节点，负责基于已确认的改动项生成本地 SQL 草稿。
        # 这里的核心产物是 draft_sql/modified.sql。
        {
            "id": "local_edit_draft",
            "label": "本地编辑并保存草稿",
            "stage": "execution",
            "type": "review_loop",
            "goal": "完成本地 SQL 修改并保存草稿文件。",
            "instructions": "在本地完成 SQL 修改并保存草稿，确认 modified_sql_path 和改动摘要。",
            "inputs": ["local_sql_path", "change_confirm_file"],
            "outputs": ["modified_sql_path", "edit_summary_file"],
            "artifact_key": "draft_sql/modified.sql",
            "preferred_skills": ["hive-sql-formatter", "python-embedded-sql-escape"],
            "actions": ["approve", "edit", "rerun_with_input"],
        },
        # 节点 5:
        # 校验节点，负责记录自测过程、验证修改结果，并沉淀自测报告。
        # 如果校验结论不满足预期，应允许补充校验条件后重跑。
        {
            "id": "validate_result",
            "label": "校验结果",
            "stage": "execution",
            "type": "review_loop",
            "goal": "执行自测或结果校验，并确认是否通过。",
            "instructions": "记录 SQL 修改后的自测过程和校验结论，必要时带新校验条件重跑。",
            "inputs": ["modified_sql_path", "task_file"],
            "outputs": ["self_test_report_path", "validation_summary_file"],
            "artifact_key": "自测报告.md",
            "preferred_skills": ["comprehensive-data-validation", "self-test-report-template"],
            "actions": ["approve", "edit", "rerun_with_input"],
        },
        # 节点 6:
        # 交付节点，负责整理最终交付说明、执行记录和结束状态。
        # 这是流程出口，但仍然允许基于新约束重新生成交付内容。
        {
            "id": "deliver",
            "label": "交付",
            "stage": "delivery",
            "type": "review_loop",
            "goal": "整理交付物、执行记录和最终结论。",
            "instructions": "沉淀交付说明、执行记录和最终状态，准备交付或后续上线。",
            "inputs": ["modified_sql_path", "self_test_report_path"],
            "outputs": ["delivery_summary_file", "final_status"],
            "artifact_key": "README.md",
            "preferred_skills": ["self-test-report-template"],
            "actions": ["approve", "edit", "rerun_with_input"],
        },
    ],
}


SQL_MODIFY_STATE_FIELDS: List[Dict[str, str]] = [
    {"name": "workflow_type", "required": "yes", "description": "Workflow kind, fixed to sql_modify."},
    {"name": "thread_id", "required": "yes", "description": "Workflow instance id."},
    {"name": "requirement_name", "required": "yes", "description": "Current requirement title."},
    {"name": "tapd_id", "required": "yes", "description": "Primary TAPD id."},
    {"name": "tapd_url", "required": "no", "description": "Original TAPD URL when intake used a link."},
    {"name": "current_node", "required": "yes", "description": "Current node id."},
    {"name": "status", "required": "yes", "description": "Overall workflow status."},
    {"name": "node_statuses", "required": "yes", "description": "Per-node runtime status."},
    {"name": "external_contexts", "required": "no", "description": "Executor-fetched external context keyed by source."},
    {"name": "source_script_info", "required": "no", "description": "Task/script metadata for the source SQL object."},
    {"name": "local_sql_path", "required": "no", "description": "Path to the pulled online SQL file."},
    {"name": "modified_sql_path", "required": "no", "description": "Path to the modified draft SQL file."},
    {"name": "self_test_report_path", "required": "no", "description": "Path to the self-test report."},
]


def get_workflow_definition(workflow_type: str) -> Dict[str, object]:
    if workflow_type == SQL_MODIFY_WORKFLOW_ID:
        return SQL_MODIFY_WORKFLOW
    raise KeyError(f"Unsupported workflow_type: {workflow_type}")
