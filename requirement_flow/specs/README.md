# Workflow Specs

## Baseline

Current baseline workflow type:

- `sql_modify`

Current baseline chain:

1. `requirement_confirm`
2. `task_confirm`
3. `fetch_and_confirm_changes`
4. `local_edit_draft`
5. `validate_result`
6. `deliver`

Each key node follows the same loop:

```text
execute
-> review
   - approve
   - edit
   - rerun_with_input
-> next node
```

## Minimal State

The state should stay small. Keep only:

- workflow identity
- current node and node statuses
- key artifact paths
- compact structured references

Do not treat state as a dump for:

- full prompts
- full model outputs
- large SQL bodies
- long debug histories

Large content should live in files and be referenced by path.
