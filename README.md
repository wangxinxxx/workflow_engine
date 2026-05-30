# Workflow Engine

This package adds a requirement workflow built with LangGraph and a Codex-compatible OpenAI endpoint.

## Layout

- `backend/app/api`: FastAPI routes for frontend queries and user actions.
- `backend/app/workflow`: LangGraph workflow orchestration, runtime state, commands and checkpoint integration.
- `backend/app/schemas`: shared Pydantic API schemas.
- `backend/app/services`: API-facing service adapters and serializers.
- `backend/app/agents`: reserved for stateless Agent modules.
- `backend/app/tools`: reserved for deterministic tool wrappers.
- `backend/app/integrations`: reserved for external platform clients.
- `frontend`: React/Vite frontend.
- `templates/requirement`: requirement result directory template using the standard `01` through `08` document names.

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
  - `backend/app/workflow/specs/sql_modify_workflow.yaml`
  - `backend/app/workflow/specs/sql_modify_state.yaml`
- Requirement outputs use the current platform document set:
  - `docs/01 需求输入文档.md`
  - `docs/02 需求解析确认文档.md`
  - `docs/02-1 待确认项文档.md`
  - `docs/03 开发方案文档.md`
  - `docs/04 SQL代码与评审文档.md`
  - `docs/05 测试项设计文档.md`
  - `docs/06 数据测试报告文档.md`
  - `docs/07 交付报告文档.md`
  - `docs/08 上线确认记录文档.md`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run the API:

```bash
uvicorn app.main:app --app-dir backend --reload
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
