# Requirement Flow

This package adds a requirement workflow built with LangGraph and a Codex-compatible OpenAI endpoint.

## What it does

- Uses one requirement directory as one LangGraph thread.
- Current baseline workflow type is `sql_modify`.
- Current baseline chain is:
  - `requirement_confirm`
  - `task_confirm`
  - `fetch_and_confirm_changes`
  - `local_edit_draft`
  - `validate_result`
  - `deliver`
- Each key node supports the same HITL loop:
  - `approve`
  - `edit`
  - `rerun_with_input`
- Writes runtime snapshots to `.runtime/langgraph/threads/<thread_id>/`.
- Keeps state small and file-oriented. Large content should stay in files, not state.
- Static workflow/state specs live in:
  - `workflow_engine/requirement_flow/specs/sql_modify_workflow.yaml`
  - `workflow_engine/requirement_flow/specs/sql_modify_state.yaml`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Required environment variable:

```bash
export OPENAI_API_KEY="..."
```

Optional:

```bash
export OPENAI_MODEL="gpt-5.2-codex"
```

## Runtime outputs

Each workflow thread writes runtime state here:

```text
.runtime/langgraph/threads/<thread_id>/
├── latest_state.json
└── latest_interrupt.json
```

These files are meant to be consumed later by a UI layer together with a static workflow definition.

## Example

```bash
requirement-flow run \
  --requirement-dir TAPDpending_gold-recycle-order-realtime-task \
  --brief-file TAPDpending_gold-recycle-order-realtime-task/research/notes/gold_recycle_order_metric_definition.md \
  --title "黄金回收订单实时任务"
```

Resume after a review interrupt:

```bash
requirement-flow resume \
  --requirement-dir TAPDpending_gold-recycle-order-realtime-task \
  --approve
```
