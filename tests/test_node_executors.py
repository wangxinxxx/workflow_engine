import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflow.artifacts import DOC_REQUIREMENT_CONFIRM
from app.workflow.node_executors import (
    normalize_tapd_reference,
    requirement_confirm_prepare,
)
from app.workflow.schemas import merge_node_input_dicts
from app.workflow.runtime import build_thread_id


class NodeExecutorsTest(unittest.TestCase):
    def test_merge_node_input_dicts_dedupes_history(self):
        merged = merge_node_input_dicts(
            {"requirement_confirm": ["A", "B"]},
            {"requirement_confirm": ["A", "B", "C"]},
        )
        self.assertEqual(merged["requirement_confirm"], ["A", "B", "C"])

    def test_normalize_tapd_reference_accepts_url(self):
        tapd_id, tapd_url = normalize_tapd_reference(
            "https://tapd.example.com/story/view?id=1120848741001796117"
        )
        self.assertEqual(tapd_id, "TAPD1120848741001796117")
        self.assertEqual(tapd_url, "https://tapd.example.com/story/view?id=1120848741001796117")

    def test_build_thread_id_accepts_tapd_url(self):
        thread_id = build_thread_id(
            "https://tapd.example.com/story/view?id=1120848741001796117",
            "gold-recycle-order",
        )
        self.assertEqual(thread_id, "TAPD1120848741001796117_gold-recycle-order")

    def test_requirement_confirm_prepare_calls_local_codex_cli(self):
        state = {
            "thread_id": "TAPD1120848741001796117_gold-recycle-order",
            "tapd_id": "https://tapd.example.com/story/view?id=1120848741001796117",
            "requirement_name": "gold recycle order",
            "brief": "sync requirement detail from TAPD",
            "node_inputs": {"requirement_confirm": ["补充说明"]},
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_codex = Path(tmp_dir) / "fake_codex.py"
            fake_codex.write_text(
                (
                    "#!/usr/bin/env python3\n"
                    "import pathlib, sys\n"
                    "args = sys.argv[1:]\n"
                    "out = ''\n"
                    "for idx, item in enumerate(args):\n"
                    "    if item in {'-o', '--output-last-message'} and idx + 1 < len(args):\n"
                    "        out = args[idx + 1]\n"
                    "prompt = sys.stdin.read()\n"
                    "pathlib.Path(out).write_text('structured context\\n' + prompt.splitlines()[0], encoding='utf-8')\n"
                ),
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            with patch.dict(
                os.environ,
                {
                    "REQUIREMENT_FLOW_CODEX_BIN": str(fake_codex),
                    "REQUIREMENT_FLOW_CODEX_TIMEOUT_SEC": "10",
                },
                clear=False,
            ):
                updates = requirement_confirm_prepare(state, {}, {}, Path(tmp_dir))

        self.assertEqual(updates["tapd_id"], "TAPD1120848741001796117")
        self.assertEqual(
            updates["tapd_url"],
            "https://tapd.example.com/story/view?id=1120848741001796117",
        )
        self.assertEqual(
            updates["external_contexts"]["codex_requirement_context"],
            "structured context\n你在 requirement_confirm 节点的预处理阶段工作。",
        )
        self.assertEqual(
            updates["artifacts"][DOC_REQUIREMENT_CONFIRM],
            "structured context\n你在 requirement_confirm 节点的预处理阶段工作。",
        )
        self.assertTrue(
            updates["external_contexts"]["requirement_confirm_executor_status"].startswith("ok: local codex produced ")
        )


if __name__ == "__main__":
    unittest.main()
