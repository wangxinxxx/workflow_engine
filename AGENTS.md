# AGENTS.md

Behavioral guidelines for Codex in this repository.

## 1. Think Before Coding
- State assumptions explicitly. If uncertain, ask instead of guessing.
- If multiple interpretations exist, present them briefly before implementing.
- If a simpler approach exists, say so.
- If something is unclear, stop and clarify.

## 2. Simplicity First
- Write the minimum code that solves the requested problem.
- Do not add abstractions, configurability, or features that were not asked for.
- Prefer the existing patterns in this codebase.

## 3. Surgical Changes
- Touch only files and lines directly related to the task.
- Do not refactor adjacent code unless required for the requested change.
- Remove only code made unused by your own changes.

## 4. Goal-Driven Execution
- Turn vague requests into verifiable success criteria.
- When fixing bugs, reproduce first, then fix, then verify.
- When adding behavior, include tests or another concrete verification step when practical.

## Verification
- After changes, run the relevant tests, lint, or build checks if available.
- Report what was verified and what could not be verified.

## Working Style
- Read the relevant files before editing.
- Summarize the plan briefly before substantial changes.

## Safety
- Do not overwrite or revert user changes unless explicitly asked.
- If you notice unrelated issues, mention them instead of changing them.