import tempfile
import unittest
from pathlib import Path

from app.workflow.artifacts import DOC_REQUIREMENT_CONFIRM, DOC_SOLUTION_DESIGN
from app.workflow.runtime import _apply_interrupt_content_to_artifacts, _normalize_state


class RuntimeStateTest(unittest.TestCase):
    def test_normalize_state_dedupes_node_inputs(self):
        state = _normalize_state(
            {
                "workflow_type": "sql_modify",
                "requirement_name": "demo",
                "node_inputs": {
                    "requirement_confirm": [
                        "https://example.com/detail",
                        "https://example.com/detail",
                        "  https://example.com/detail  ",
                        "",
                        "补充说明",
                        "补充说明",
                    ]
                },
            }
        )

        self.assertEqual(
            state["node_inputs"]["requirement_confirm"],
            ["https://example.com/detail", "补充说明"],
        )

    def test_normalize_state_migrates_dev_doc_to_requirement_doc(self):
        state = _normalize_state(
            {
                "workflow_type": "sql_modify",
                "requirement_name": "demo",
                "artifacts": {
                    "docs/开发文档.md": "# 开发文档\n\n旧内容\n",
                },
            }
        )

        self.assertEqual(state["artifacts"][DOC_REQUIREMENT_CONFIRM], "# 02 需求解析确认文档\n\n旧内容\n")
        self.assertEqual(state["artifacts"][DOC_SOLUTION_DESIGN], "# 03 开发方案文档\n\n旧内容\n")

    def test_normalize_state_prefers_disk_artifacts_over_persisted_paths(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            requirement_dir = Path(tmp_dir)
            docs_dir = requirement_dir / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / "02 需求解析确认文档.md").write_text("# 02 需求解析确认文档\n\n正文\n", encoding="utf-8")
            (docs_dir / "03 开发方案文档.md").write_text("# 03 开发方案文档\n\n正文\n", encoding="utf-8")
            state = _normalize_state(
                {
                    "workflow_type": "sql_modify",
                    "requirement_name": "demo",
                    "requirement_dir": str(requirement_dir),
                    "artifacts": {
                        DOC_REQUIREMENT_CONFIRM: str((docs_dir / "02 需求解析确认文档.md").resolve()),
                        DOC_SOLUTION_DESIGN: str((docs_dir / "03 开发方案文档.md").resolve()),
                    },
                }
            )

        self.assertEqual(state["artifacts"][DOC_REQUIREMENT_CONFIRM], "# 02 需求解析确认文档\n\n正文\n")
        self.assertEqual(state["artifacts"][DOC_SOLUTION_DESIGN], "# 03 开发方案文档\n\n正文\n")

    def test_interrupt_content_overrides_artifact_for_review_node(self):
        state = {
            "workflow_type": "sql_modify",
            "artifacts": {
                DOC_REQUIREMENT_CONFIRM: "# 旧需求文档\n",
            },
        }
        interrupt_payload = {
            "type": "node_review",
            "step_id": "requirement_confirm",
            "content": "# 新需求文档\n",
        }

        updated = _apply_interrupt_content_to_artifacts(state, interrupt_payload)

        self.assertEqual(updated["artifacts"][DOC_REQUIREMENT_CONFIRM], "# 新需求文档\n")


if __name__ == "__main__":
    unittest.main()
