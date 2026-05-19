from __future__ import annotations

import html
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, quote

from wsgiref.simple_server import make_server

from .runtime import (
    create_requirement_thread,
    dashboard_env_summary,
    list_threads,
    read_thread_bundle,
    resume_thread,
    rerun_step,
    resolve_thread_requirement_dir,
    start_existing_thread,
    step_specs_index,
)

MAX_ARTIFACT_PREVIEW_CHARS = 8000


def serve_dashboard(host: str = "127.0.0.1", port: int = 8787) -> None:
    with make_server(host, port, dashboard_app) as httpd:
        print(f"Requirement dashboard running at http://{host}:{port}")
        httpd.serve_forever()


def dashboard_app(environ: Dict[str, Any], start_response) -> List[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")
    query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    intake_error = ""
    try:
        if method == "GET" and path == "/":
            body = render_index_page()
            return respond_html(start_response, body)
        if method == "POST" and path == "/intake":
            form = read_form_body(environ)
            try:
                result = create_requirement_thread(
                    tapd_id=form.get("tapd_id", ""),
                    short_name=form.get("short_name", ""),
                    title=form.get("title", ""),
                    brief=form.get("brief", ""),
                    predecessors=split_predecessors(form.get("predecessors", "")),
                    auto_start=form.get("auto_start", "") == "on",
                )
                return redirect(start_response, f"/thread/{result['thread_id']}")
            except ValueError as exc:
                intake_error = str(exc)
                body = render_index_page(intake_error=intake_error, form_data=form)
                return respond_html(start_response, body, status="400 Bad Request")
        if method == "GET" and path.startswith("/thread/"):
            thread_id = path.split("/thread/", 1)[1].strip("/")
            body = render_thread_page(thread_id, action_error=query.get("error", [""])[-1])
            return respond_html(start_response, body)
        if method == "POST" and path.startswith("/thread/") and path.endswith("/start"):
            thread_id = path[len("/thread/") : -len("/start")].strip("/")
            try:
                start_existing_thread(thread_id)
            except ValueError as exc:
                return redirect(start_response, thread_error_location(thread_id, str(exc)))
            return redirect(start_response, f"/thread/{thread_id}")
        if method == "POST" and path.startswith("/thread/") and path.endswith("/approve"):
            thread_id = path[len("/thread/") : -len("/approve")].strip("/")
            form = read_form_body(environ)
            try:
                resume_thread(thread_id, {"action": "approve", "note": form.get("review_note", "") or form.get("shared_note", "")})
            except ValueError as exc:
                return redirect(start_response, thread_error_location(thread_id, str(exc)))
            return redirect(start_response, f"/thread/{thread_id}")
        if method == "POST" and path.startswith("/thread/") and path.endswith("/edit"):
            thread_id = path[len("/thread/") : -len("/edit")].strip("/")
            form = read_form_body(environ)
            content = form.get("content", "")
            try:
                resume_thread(
                    thread_id,
                    {
                        "action": "edit",
                        "content": content,
                        "note": form.get("review_note", ""),
                    },
                )
            except ValueError as exc:
                return redirect(start_response, thread_error_location(thread_id, str(exc)))
            return redirect(start_response, f"/thread/{thread_id}")
        if method == "POST" and path.startswith("/thread/") and path.endswith("/regenerate"):
            thread_id = path[len("/thread/") : -len("/regenerate")].strip("/")
            form = read_form_body(environ)
            try:
                rerun_step(thread_id, form.get("step_id", ""), form.get("knowledge_note", "") or form.get("shared_note", ""))
            except ValueError as exc:
                return redirect(start_response, thread_error_location(thread_id, str(exc)))
            return redirect(start_response, f"/thread/{thread_id}")
        return respond_html(start_response, render_not_found(path), status="404 Not Found")
    except ValueError as exc:
        body = render_error_page(path, exc)
        return respond_html(start_response, body, status="400 Bad Request")
    except Exception as exc:
        body = render_error_page(path, exc)
        return respond_html(start_response, body, status="500 Internal Server Error")


def read_form_body(environ: Dict[str, Any]) -> Dict[str, str]:
    size = int(environ.get("CONTENT_LENGTH") or 0)
    raw = environ["wsgi.input"].read(size).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def render_index_page(intake_error: str = "", form_data: Dict[str, str] | None = None) -> str:
    threads = list_threads()
    env = dashboard_env_summary()
    form_data = form_data or {}
    cards = "\n".join(render_thread_card(item) for item in threads) or "<p class='empty'>No threads yet.</p>"
    env_rows = "\n".join(
        f"<li><strong>{escape_html(key)}</strong>: {escape_html(value or '-')}</li>"
        for key, value in env.items()
    )
    error_block = (
        f"<div class='error-box'><strong>Create failed:</strong> {escape_html(intake_error)}</div>"
        if intake_error
        else ""
    )
    return page_shell(
        "Requirement Workflow Dashboard",
        f"""
        <section class="panel hero">
          <div>
            <h1>Requirement Workflow Dashboard</h1>
            <p>本地观测 LangGraph 线程、步骤状态、待审核内容和需求文档产物。</p>
          </div>
          <div class="env-box">
            <h2>Runtime Config</h2>
            <ul>{env_rows}</ul>
          </div>
        </section>
        <section class="panel">
          <div class="section-title">
            <h2>New Requirement</h2>
            <span>Manual intake</span>
          </div>
          {error_block}
          {render_intake_form(form_data)}
        </section>
        <section class="panel">
          <div class="section-title">
            <h2>Threads</h2>
            <span>{len(threads)} total</span>
          </div>
          <div class="thread-grid">
            {cards}
          </div>
        </section>
        """,
    )


def render_intake_form(form_data: Dict[str, str]) -> str:
    auto_start_checked = "checked" if form_data.get("auto_start", "on") == "on" else ""
    return f"""
    <form method="post" action="/intake" class="intake-form">
      <div class="intake-grid">
        <label>
          <strong>TAPD ID</strong>
          <input type="text" name="tapd_id" placeholder="1120848741001796117 或 TAPD112084..." value="{escape_html(form_data.get('tapd_id', ''))}" required />
        </label>
        <label>
          <strong>Short Name</strong>
          <input type="text" name="short_name" placeholder="gold-recycle-order-realtime-task" value="{escape_html(form_data.get('short_name', ''))}" required />
        </label>
        <label class="wide">
          <strong>Title</strong>
          <input type="text" name="title" placeholder="黄金回收订单实时任务" value="{escape_html(form_data.get('title', ''))}" />
        </label>
        <label class="wide">
          <strong>Predecessors</strong>
          <input type="text" name="predecessors" placeholder="多个目录名用逗号分隔" value="{escape_html(form_data.get('predecessors', ''))}" />
        </label>
        <label class="wide">
          <strong>Brief</strong>
          <textarea name="brief" class="brief-box" placeholder="输入需求摘要、原始描述、关键约束、附件摘要" required>{escape_html(form_data.get('brief', ''))}</textarea>
        </label>
      </div>
      <div class="toggle-row">
        <label class="checkbox-row">
          <input type="checkbox" name="auto_start" {auto_start_checked} />
          <span>提交后立即启动工作流</span>
        </label>
        <button type="submit" class="primary-btn">Create Requirement</button>
      </div>
    </form>
    """


def render_thread_card(item: Dict[str, Any]) -> str:
    badge = f"badge badge-{escape_html(item['status'])}"
    interrupt = "<span class='chip chip-warning'>interrupted</span>" if item.get("interrupted") else ""
    return f"""
    <a class="thread-card" href="/thread/{escape_html(item['thread_id'])}">
      <div class="thread-top">
        <span class="{badge}">{escape_html(item['status'])}</span>
        {interrupt}
      </div>
      <h3>{escape_html(item['thread_id'])}</h3>
      <p>{escape_html(item.get('title', '-'))}</p>
      <dl>
        <div><dt>TAPD</dt><dd>{escape_html(item.get('tapd_id', '-'))}</dd></div>
        <div><dt>Current</dt><dd>{escape_html(item.get('current_step', '-'))}</dd></div>
        <div><dt>Updated</dt><dd>{escape_html(item.get('updated_at', '-'))}</dd></div>
        <div><dt>History</dt><dd>{escape_html(str(item.get('history_count', 0)))}</dd></div>
      </dl>
    </a>
    """


def render_thread_page(thread_id: str, action_error: str = "") -> str:
    bundle = read_thread_bundle(thread_id)
    state = bundle["state"]
    pending_interrupts = bundle.get("pending_interrupts", [])
    artifacts = bundle["artifacts"]
    requirement_dir = resolve_thread_requirement_dir(thread_id)
    step_specs = step_specs_index()
    status_cards = "".join(render_step_card(spec, state, pending_interrupts, thread_id) for spec in step_specs)
    artifact_blocks = "".join(render_artifact_block(name, artifacts.get(name, "")) for name in ordered_artifact_names(artifacts))
    start_panel = render_start_panel(thread_id, state)
    summary_rows = "".join(
        f"<li><strong>{escape_html(label)}</strong>: {escape_html(value)}</li>"
        for label, value in [
            ("Thread", thread_id),
            ("Status", str(state.get("status", "-"))),
            ("Current Step", str(state.get("current_step", "-"))),
            ("Requirement Dir", str(requirement_dir)),
            ("TAPD", str(state.get("tapd_id", "-"))),
            ("Title", str(state.get("requirement_name", "-"))),
        ]
    )
    error_block = (
        f"<div class='error-box'><strong>Action failed:</strong> {escape_html(action_error)}</div>"
        if action_error else ""
    )
    return page_shell(
        f"Thread {thread_id}",
        f"""
        <section class="panel hero compact">
          <div>
            <a class="back-link" href="/">← Back</a>
            <h1>{escape_html(thread_id)}</h1>
            <p>{escape_html(str(state.get('title', '-')))}</p>
          </div>
          <div class="meta-box">
            <ul>{summary_rows}</ul>
          </div>
        </section>
        <section class="thread-layout">
          <div class="left-column">
            {error_block}
            <section class="panel">
              <div class="section-title">
                <h2>Steps</h2>
                <span>{escape_html(str(state.get('status', '-')))}</span>
              </div>
              <div class="step-list">{status_cards}</div>
            </section>
            {start_panel}
          </div>
          <div class="right-column">
            <section class="panel">
              <div class="section-title">
                <h2>Artifacts</h2>
                <span>{len(artifacts)} files</span>
              </div>
              {artifact_blocks}
            </section>
          </div>
        </section>
        """,
    )
def render_step_card(spec: Dict[str, Any], state: Dict[str, Any], pending_interrupts: List[Dict[str, Any]], thread_id: str) -> str:
    statuses = state.get("node_statuses", {})
    step = statuses.get(spec["id"], {})
    status = str(step.get("status", "pending"))
    updated_at = str(step.get("updated_at", "-") or "-")
    is_current = state.get("current_step") == spec["id"]
    current_class = " current" if is_current else ""
    skill_line = render_skill_line(spec)
    action_block = render_step_action_block(spec, pending_interrupts, thread_id)
    return f"""
    <div class="step-card{current_class}">
      <div class="step-head">
        <h3>{escape_html(spec['label'])}</h3>
        <span class="badge badge-{escape_html(status)}">{escape_html(status)}</span>
      </div>
      <p class="mono">{escape_html(str(spec.get('artifact_key', '-')))}</p>
      <p>{escape_html(str(spec.get('instructions', '-')))}</p>
      {skill_line}
      {action_block}
      <p class="muted">Updated: {escape_html(updated_at)}</p>
    </div>
    """


def render_step_action_block(spec: Dict[str, Any], pending_interrupts: List[Dict[str, Any]], thread_id: str) -> str:
    spec_id = str(spec.get("id", "") or "")
    matching = None
    for item in pending_interrupts:
        if str(item.get("step_id", "") or "") == spec_id:
            matching = item
            break
    content = str(matching.get("content", "") if matching else "")
    approve_block = ""
    edit_block = ""
    if matching:
        approve_block = f"""
        <form method="post" action="/thread/{escape_html(thread_id)}/approve" class="step-rerun-form compact-form">
          <label for="shared-note-{escape_html(spec_id)}"><strong>Review Note / Rerun Input</strong></label>
          <input type="hidden" name="step_id" value="{escape_html(spec_id)}" />
          <textarea id="shared-note-{escape_html(spec_id)}" name="shared_note" class="note-box" placeholder="输入通过说明；如果要重跑当前节点，也在这里输入新知识"></textarea>
          <div class="button-row">
            <button type="submit" class="primary-btn">Approve And Continue</button>
            <button type="submit" formaction="/thread/{escape_html(thread_id)}/regenerate" class="secondary-btn">Rerun This Step</button>
          </div>
        </form>
        """
        edit_block = f"""
        <form method="post" action="/thread/{escape_html(thread_id)}/edit" class="edit-form">
          <label for="edit-review-note-{escape_html(spec_id)}"><strong>Review Note</strong></label>
          <textarea id="edit-review-note-{escape_html(spec_id)}" name="review_note" class="note-box" placeholder="写你的修改理由、约束、补充说明"></textarea>
          <label for="content-{escape_html(spec_id)}"><strong>Final Content</strong></label>
          <textarea id="content-{escape_html(spec_id)}" name="content">{escape_html(content)}</textarea>
          <button type="submit" class="secondary-btn">Save Edit And Continue</button>
        </form>
        """
    rerun_block = "" if matching else f"""
    <form method="post" action="/thread/{escape_html(thread_id)}/regenerate" class="step-rerun-form">
      <label for="rerun-note-{escape_html(spec_id)}"><strong>Rerun This Step</strong></label>
      <input type="hidden" name="step_id" value="{escape_html(spec_id)}" />
      <textarea id="rerun-note-{escape_html(spec_id)}" name="knowledge_note" class="note-box" placeholder="输入新知识、修正事实、约束条件、补充规则"></textarea>
      <button type="submit" class="secondary-btn">Rerun This Step</button>
    </form>
    """
    return approve_block + rerun_block + edit_block


def render_skill_line(spec: Dict[str, Any]) -> str:
    matches = spec.get("skill_matches", {}) or {}
    available = [item.get("name", "") for item in matches.get("available", []) if item.get("name")]
    missing = [item.get("name", "") for item in matches.get("missing", []) if item.get("name")]
    parts: List[str] = []
    if available:
        parts.append("available: " + ", ".join(escape_html(name) for name in available))
    if missing:
        parts.append("missing: " + ", ".join(escape_html(name) for name in missing))
    if not parts:
        return ""
    return f"<p class='muted'><strong>Skills</strong>: {' | '.join(parts)}</p>"


def render_start_panel(thread_id: str, state: Dict[str, Any]) -> str:
    if str(state.get("status", "")).strip() != "created":
        return ""
    return f"""
    <section class="panel">
      <div class="section-title">
        <h2>Start Workflow</h2>
      </div>
      <p>这个线程是手动创建但尚未启动。点击后会进入第一个节点并开始正常审核流。</p>
      <form method="post" action="/thread/{escape_html(thread_id)}/start" class="action-row">
        <button type="submit" class="primary-btn">Start Workflow</button>
      </form>
    </section>
    """


def ordered_artifact_names(artifacts: Dict[str, Any]) -> List[str]:
    preferred = [
        "README.md",
        "docs/开发文档.md",
        "online_sql/source.sql",
        "draft_sql/modified.sql",
        "自测报告.md",
    ]
    rest = [name for name in artifacts.keys() if name not in preferred]
    return [name for name in preferred if name in artifacts] + sorted(rest)


def render_artifact_block(name: str, content: str) -> str:
    body = escape_html(truncate_text(content.strip() or "(empty)", MAX_ARTIFACT_PREVIEW_CHARS))
    return f"""
    <details class="artifact-block">
      <summary>{escape_html(name)}</summary>
      <pre>{body}</pre>
    </details>
    """


def render_not_found(path: str) -> str:
    return page_shell(
        "Not Found",
        f"""
        <section class="panel">
          <h1>404</h1>
          <p>Path not found: <code>{escape_html(path)}</code></p>
          <a class="back-link" href="/">Back to dashboard</a>
        </section>
        """,
    )


def render_error_page(path: str, exc: Exception) -> str:
    trace = escape_html("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    return page_shell(
        "Dashboard Error",
        f"""
        <section class="panel">
          <h1>500</h1>
          <p>Failed to handle <code>{escape_html(path)}</code></p>
          <pre>{trace}</pre>
          <a class="back-link" href="/">Back to dashboard</a>
        </section>
        """,
    )


def respond_html(start_response, body: str, status: str = "200 OK") -> List[bytes]:
    payload = body.encode("utf-8")
    headers = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(payload))),
    ]
    start_response(status, headers)
    return [payload]


def redirect(start_response, location: str) -> List[bytes]:
    start_response("303 See Other", [("Location", location)])
    return [b""]


def thread_error_location(thread_id: str, message: str) -> str:
    return f"/thread/{thread_id}?error={quote(message)}"


def escape_html(value: str) -> str:
    return html.escape(value, quote=True)


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}\n\n... [truncated {omitted} chars]"


def split_predecessors(raw: str) -> List[str]:
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def page_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape_html(title)}</title>
  <style>
    :root {{
      --bg: #f4efe7;
      --panel: #fffaf2;
      --ink: #1f2933;
      --muted: #5b6773;
      --border: #dccfbf;
      --accent: #0f766e;
      --accent-soft: #d7f3ef;
      --warning: #b45309;
      --warning-soft: #fef3c7;
      --danger: #b91c1c;
      --shadow: 0 16px 40px rgba(31, 41, 51, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.10), transparent 28%),
        linear-gradient(180deg, #f7f3ec 0%, var(--bg) 100%);
    }}
    a {{ color: inherit; text-decoration: none; }}
    code, pre, .mono, textarea {{
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
    }}
    .app {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 24px;
      box-shadow: var(--shadow);
      padding: 24px;
      margin-bottom: 20px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1.6fr 1fr;
      gap: 20px;
      align-items: start;
    }}
    .hero.compact {{
      grid-template-columns: 1.3fr 1fr;
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: -0.03em;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.5;
    }}
    .env-box, .meta-box {{
      background: rgba(255,255,255,0.65);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 18px;
    }}
    .error-box {{
      margin-bottom: 16px;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid #efb5b5;
      background: #fff1f1;
      color: var(--danger);
      font-size: 14px;
      line-height: 1.5;
    }}
    .env-box h2, .section-title h2 {{
      margin: 0;
      font-size: 18px;
    }}
    .env-box ul, .meta-box ul {{
      list-style: none;
      padding: 0;
      margin: 12px 0 0;
      display: grid;
      gap: 10px;
    }}
    .intake-form {{
      display: grid;
      gap: 16px;
    }}
    .intake-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .intake-grid label,
    .edit-form label,
    .action-row label {{
      display: grid;
      gap: 8px;
      font-size: 14px;
    }}
    .intake-grid label.wide {{
      grid-column: 1 / -1;
    }}
    .toggle-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      flex-wrap: wrap;
    }}
    .checkbox-row {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-size: 14px;
      color: var(--muted);
    }}
    .section-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .thread-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
    }}
    .thread-card {{
      display: block;
      padding: 18px;
      border-radius: 20px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.72);
      transition: transform 140ms ease, box-shadow 140ms ease;
    }}
    .thread-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 14px 24px rgba(31, 41, 51, 0.08);
    }}
    .thread-card h3 {{
      margin: 14px 0 8px;
      font-size: 20px;
      line-height: 1.2;
    }}
    .thread-card p {{
      margin: 0 0 14px;
      color: var(--muted);
      line-height: 1.45;
    }}
    .thread-card dl {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      margin: 0;
    }}
    .thread-card dl div {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 14px;
    }}
    .thread-card dt {{
      color: var(--muted);
    }}
    .thread-top {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .badge, .chip {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }}
    .badge-running, .badge-approved, .badge-completed, .badge-docs_completed, .badge-execution_running {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .badge-blocked, .badge-execution_blocked {{
      background: #fee2e2;
      color: var(--danger);
    }}
    .badge-execution_in_progress {{
      background: #dbeafe;
      color: #1d4ed8;
    }}
    .badge-pending, .badge-drafted {{
      background: #ece7dd;
      color: #6b5c4d;
    }}
    .badge-failed {{
      background: #fee2e2;
      color: var(--danger);
    }}
    .chip-warning {{
      background: var(--warning-soft);
      color: var(--warning);
    }}
    .thread-layout {{
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 20px;
      align-items: start;
    }}
    .step-list {{
      display: grid;
      gap: 12px;
    }}
    .step-card {{
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
      background: rgba(255,255,255,0.75);
    }}
    .step-card.current {{
      border-color: var(--accent);
      box-shadow: inset 0 0 0 1px rgba(15, 118, 110, 0.15);
    }}
    .step-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      margin-bottom: 8px;
    }}
    .step-head h3 {{
      margin: 0;
      font-size: 17px;
    }}
    .muted, .empty, .mono {{
      color: var(--muted);
    }}
    .artifact-block {{
      border: 1px solid var(--border);
      border-radius: 16px;
      margin-bottom: 14px;
      overflow: hidden;
      background: rgba(255,255,255,0.7);
    }}
    .artifact-block summary {{
      cursor: pointer;
      padding: 14px 16px;
      font-weight: 700;
      background: rgba(255,255,255,0.8);
    }}
    .artifact-block pre {{
      margin: 0;
      padding: 16px;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.5;
      font-size: 13px;
      max-height: 520px;
      overflow: auto;
      border-top: 1px solid var(--border);
    }}
    .back-link {{
      display: inline-block;
      margin-bottom: 16px;
      color: var(--accent);
      font-weight: 700;
    }}
    .action-row {{
      margin: 16px 0 18px;
      display: grid;
      gap: 10px;
    }}
    .edit-form {{
      display: grid;
      gap: 10px;
    }}
    .button-row {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .note-box {{
      min-height: 120px;
    }}
    .brief-box {{
      min-height: 220px;
    }}
    .select-box {{
      width: 100%;
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--ink);
      font-size: 14px;
    }}
    input[type="text"] {{
      width: 100%;
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--ink);
      font-size: 14px;
    }}
    textarea {{
      width: 100%;
      min-height: 320px;
      padding: 14px;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.5;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      font-weight: 700;
      cursor: pointer;
    }}
    .primary-btn {{
      background: var(--accent);
      color: #fff;
    }}
    .secondary-btn {{
      background: #efe6d7;
      color: #3f3428;
    }}
    .history-stack {{
      padding: 16px;
      border-top: 1px solid var(--border);
      display: grid;
      gap: 14px;
    }}
    .history-entry {{
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
      background: rgba(255,255,255,0.72);
    }}
    .history-entry pre {{
      margin: 8px 0 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.5;
      max-height: 280px;
      overflow: auto;
    }}
    .history-meta {{
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 12px;
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
    }}
    @media (max-width: 1024px) {{
      .hero, .hero.compact, .thread-layout {{
        grid-template-columns: 1fr;
      }}
      .intake-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="app">
    {body}
  </main>
</body>
</html>"""
