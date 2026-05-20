import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from requirement_flow.node_executors import requirement_confirm_prepare
from requirement_flow.runtime import _write_runtime_outputs


class RequirementConfirmDebugTest(unittest.TestCase):
    @staticmethod
    def _preview_text(text: str, limit: int = 240) -> str:
        normalized = str(text or "").replace("\n", "\\n")
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "...(truncated)"

    @classmethod
    def _print_step(cls, title: str, expected: str, **values: object) -> None:
        print(f"\n[{title}]")
        print(f"预期目标: {expected}")
        for key, value in values.items():
            if isinstance(value, str):
                print(f"- {key}: {cls._preview_text(value)}")
            else:
                print(f"- {key}: {value!r}")

    def test_requirement_confirm_debug_entry(self):
        """
        这个用例是给 IDEA 断点调试用的，不走 dashboard / HTTP / 表单。

        调试目标:
        1. 看 requirement_confirm_prepare 是否真的把 Codex 结果放进了 `artifacts["docs/需求文档.md"]`
        2. 看 graph/runtime 合并状态时，是否把新需求文档覆盖掉
        3. 看 runtime 持久化时，磁盘文件和 latest_state.json 是否一致

        建议断点位置:
        1. `prepare_updates = requirement_confirm_prepare(...)`
           这里看 TAPD 抓取结果、Codex 输出、prepare_updates["artifacts"]
        2. `prepared_artifacts.update(...)`
           这里看 prepare 产物合入 node artifact 后，内存里的最终文档是什么
        3. `_write_runtime_outputs(...)`
           这里看 runtime 写盘前后，snapshot / interrupt_payload / persisted_snapshot
        4. `persisted_doc = ...read_text(...)`
           这里看最终磁盘文件和 latest_state.json 里记录的路径

        运行方式:
        - 在 IDEA 里直接 Debug 这个 test method
        - 或命令行执行:
          `python3 -m unittest tests.test_requirement_confirm_debug.RequirementConfirmDebugTest.test_requirement_confirm_debug_entry`
        """

        # 这一段是假 TAPD 返回值，避免调试时依赖真实网络和 Cookie。
        # 目标是稳定复现“先抓需求，再交给 Codex，总结成需求文档”的主链路。
        fake_tapd_detail = json.dumps(
            {
                "detail_url": "https://www.tapd.cn/tapd_fe/20848741/story/detail/1120848741001823055",
                "workspace_id": "20848741",
                "story_id": "1120848741001823055",
                "name": "上门、门店回收判责详情表添加字段",
                "status": "new",
                "source": "tapd-detail",
                "source_field": "description",
                "full_text": (
                    "一、需求背景\n"
                    "质检复核判责看板搭建，需要这张表获取复审人员，以及判责的责任类别、责任明细等信息。\n"
                    "二、需求内容\n"
                    "目标表添加来源表中的 id/operator/operate_type/responsibility_detail/"
                    "duty_classification/judge_duty_reason 字段。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        # 这一段是假 Codex 输出，代表 requirement_confirm 的理想产物。
        # 断点时你可以随意改这里的内容，观察后续哪一步把它覆盖掉。
        fake_requirement_doc = """# 需求文档

## 需求名称
上门、门店回收判责详情表添加字段

## 需求背景
质检复核判责看板搭建，需要通过判责详情表获取复审人员，以及判责的责任类别、责任明细等信息。

## 需求目标
在目标表中补充判责记录相关字段，用于支持质检复核判责看板取数。
"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 用临时目录模拟真实 requirement 目录，避免污染你当前线程文件。
            requirement_dir = Path(tmp_dir) / "TAPDnew5_ff"
            docs_dir = requirement_dir / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            # 先写一份“旧需求文档”，这是为了复现“新内容被旧内容覆盖”的问题。
            (docs_dir / "需求文档.md").write_text("# 旧需求文档\n", encoding="utf-8")
            (docs_dir / "开发文档.md").write_text("", encoding="utf-8")
            (requirement_dir / "README.md").write_text("# README\n", encoding="utf-8")

            # runtime_root 只给这个测试用，方便你检查 latest_state.json / latest_interrupt.json。
            runtime_root = Path(tmp_dir) / "runtime"

            # 这里构造的是 requirement_confirm 进入前的最小运行态。
            # 关键是：
            # - requirement_dir 指到临时目录
            # - node_inputs 里给一条 TAPD URL
            # - artifacts 里先塞旧文档，方便观察覆盖行为
            state = {
                "workflow_type": "sql_modify",
                "thread_id": "DEBUG_TAPDnew5_ff",
                "requirement_dir": str(requirement_dir),
                "title": "new",
                "requirement_name": "new",
                "brief": "new",
                "tapd_id": "TAPDNEW5",
                "tapd_url": "",
                "external_contexts": {},
                "node_inputs": {
                    "requirement_confirm": [
                        "https://www.tapd.cn/tapd_fe/20848741/story/detail/1120848741001823055 获取需求"
                    ]
                },
                "node_statuses": {
                    "requirement_confirm": {
                        "label": "需求确认",
                        "status": "pending",
                        "updated_at": "",
                        "note": "",
                    }
                },
                "artifacts": {
                    "README.md": "# README\n",
                    "docs/需求文档.md": "# 旧需求文档\n",
                    "docs/开发文档.md": "",
                },
                "current_step": "requirement_confirm",
                "status": "running",
            }
            node_def = {
                "id": "requirement_confirm",
                "label": "需求确认",
                "artifact_key": "docs/需求文档.md",
            }

            # 三个 patch 的作用：
            # 1. fetch_tapd_requirement_detail: 固定 TAPD 抓取结果
            # 2. _run_requirement_confirm_codex: 固定 Codex 总结结果
            # 3. thread_runtime_dir: 把 runtime 输出写进临时目录，方便调试，不污染正式 .runtime
            with patch(
                "requirement_flow.node_executors.fetch_tapd_requirement_detail",
                return_value=(fake_tapd_detail, "ok: tapd fetch produced 631 chars"),
            ), patch(
                "requirement_flow.node_executors._run_requirement_confirm_codex",
                return_value=(fake_requirement_doc, "ok: local codex produced 200 chars"),
            ), patch(
                "requirement_flow.runtime.thread_runtime_dir",
                side_effect=lambda thread_id: runtime_root / thread_id,
            ):
                # BREAKPOINT 1: 看 prepare executor 的产出。
                prepare_updates = requirement_confirm_prepare(
                    state,
                    node_def,
                    dict(state["artifacts"]),
                    requirement_dir,
                )
                self._print_step(
                    "STEP 1: prepare_updates",
                    "prepare_updates['artifacts']['docs/需求文档.md'] 已经变成新需求文档，并保留 TAPD/Codex 执行状态。",
                    old_requirement_doc=state["artifacts"]["docs/需求文档.md"],
                    prepared_requirement_doc=dict(prepare_updates.get("artifacts", {})).get("docs/需求文档.md", ""),
                    tapd_fetch_status=dict(prepare_updates.get("external_contexts", {})).get(
                        "tapd_requirement_fetch_status", ""
                    ),
                    codex_status=dict(prepare_updates.get("external_contexts", {})).get(
                        "requirement_confirm_executor_status", ""
                    ),
                )

                prepared_artifacts = dict(state["artifacts"])
                # 这里模拟 graph._make_node() 里 prepare 后合并 artifacts 的动作。
                prepared_artifacts.update(dict(prepare_updates.get("artifacts", {}) or {}))
                self._print_step(
                    "STEP 2: prepared_artifacts merge",
                    "prepared_artifacts['docs/需求文档.md'] 覆盖旧文档；interrupt_payload.content 和它完全一致。",
                    merged_requirement_doc=prepared_artifacts["docs/需求文档.md"],
                    merge_replaced_old_doc=prepared_artifacts["docs/需求文档.md"] != state["artifacts"]["docs/需求文档.md"],
                )

                # BREAKPOINT 2: 看 merge 后的 artifacts，确认新需求文档已经在内存里。
                # interrupt_payload.content 就是前端审核页看到的内容。
                # 如果这里已经是新文档，而最终文件不是，问题就一定在 runtime 写盘阶段。
                interrupt_payload = {
                    "type": "node_review",
                    "step_id": "requirement_confirm",
                    "label": "需求确认",
                    "content": prepared_artifacts["docs/需求文档.md"],
                    "instructions": "debug requirement confirm",
                    "actions": ["approve", "edit", "rerun_with_input"],
                }
                snapshot = {
                    **state,
                    **{key: value for key, value in prepare_updates.items() if key != "artifacts"},
                    "artifacts": prepared_artifacts,
                }
                self._print_step(
                    "STEP 3: runtime input snapshot",
                    "传给 runtime 的 snapshot.artifacts 和 interrupt_payload.content 都应该是新需求文档。",
                    snapshot_requirement_doc=snapshot["artifacts"]["docs/需求文档.md"],
                    interrupt_content=interrupt_payload["content"],
                    snapshot_matches_interrupt=(
                        snapshot["artifacts"]["docs/需求文档.md"] == interrupt_payload["content"]
                    ),
                )

                # BREAKPOINT 3: 进 runtime 持久化，确认没有被旧 artifact 覆盖。
                # 这里是这次问题的关键观察点。
                _write_runtime_outputs(state["thread_id"], snapshot, interrupt_payload)

                # persisted_doc: 最终磁盘文件正文
                # persisted_state: latest_state.json 持久化后的状态
                persisted_doc = (docs_dir / "需求文档.md").read_text(encoding="utf-8")
                persisted_state = json.loads(
                    ((runtime_root / state["thread_id"]) / "latest_state.json").read_text(encoding="utf-8")
                )
                self._print_step(
                    "STEP 4: persisted outputs",
                    "磁盘 docs/需求文档.md 保持新文档；latest_state.json 的 artifacts 记录的是磁盘路径，不是正文。",
                    persisted_requirement_doc=persisted_doc,
                    persisted_artifact_path=persisted_state["artifacts"]["docs/需求文档.md"],
                    persisted_doc_matches_fake=(persisted_doc == fake_requirement_doc.rstrip() + "\n"),
                )

                # BREAKPOINT 4: 最终磁盘文件和 latest_state.json 都应该是新结构。
                # 断点时重点看：
                # - persisted_doc
                # - persisted_state["artifacts"]
                # - interrupt_payload["content"]
                self.assertEqual(persisted_doc, fake_requirement_doc.rstrip() + "\n")
                self.assertEqual(
                    persisted_state["artifacts"]["docs/需求文档.md"],
                    str((docs_dir / "需求文档.md").resolve()),
                )


if __name__ == "__main__":
    unittest.main()
