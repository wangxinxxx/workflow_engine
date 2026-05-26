import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { config } from "./env.js";

const COMMANDS = new Map([
  ["/help", help],
  ["/model", model],
  ["/ping", ping],
  ["/run-test", runTest],
  ["/create-workflow", createWorkflow],
  ["/message", routeMessage]
]);

export function parseCommand(text) {
  const normalized = text.trim();
  if (!normalized) {
    return null;
  }

  const withoutMention = normalized.replace(/^@\S+\s+/, "").trim();
  if (isModelQuestion(withoutMention)) {
    return { name: "/model", args: [] };
  }

  if (!withoutMention.startsWith("/")) {
    return { name: "/message", args: [withoutMention] };
  }

  const [name, ...args] = withoutMention.split(/\s+/);
  if (!name.startsWith("/")) {
    return null;
  }

  return { name: name.toLowerCase(), args };
}

export async function runWorkflow(command, context) {
  const handler = COMMANDS.get(command.name);
  if (!handler) {
    return `Unknown command: ${command.name}\n\n${helpText()}`;
  }

  return handler(command.args, context);
}

function help() {
  return helpText();
}

function model() {
  return `Current workflow model: ${process.env.OPENAI_MODEL || "[unset]"}`;
}

function ping() {
  return "pong";
}

async function createWorkflow(args) {
  const parsed = parseCreateWorkflowArgs(args);
  if (!parsed.ok) {
    return parsed.message;
  }

  if (!existsSync(config.workflowWorkspaceDir)) {
    return `Cannot create workflow because WORKFLOW_WORKSPACE_DIR does not exist:\n${config.workflowWorkspaceDir}`;
  }

  const invocation = resolveWorkflowInvocation();
  const runArgs = [
    ...invocation.prefixArgs,
    "run",
    "--requirement-dir",
    parsed.threadId,
    "--tapd-id",
    parsed.tapdId
  ];

  if (parsed.title) {
    runArgs.push("--title", parsed.title);
  }
  if (parsed.briefText) {
    runArgs.push("--brief-text", parsed.briefText);
  } else if (parsed.briefFile) {
    runArgs.push("--brief-file", parsed.briefFile);
  }
  if (parsed.autoApprove) {
    runArgs.push("--auto-approve");
  }

  const startedAt = Date.now();
  const result = await runProcess(
    invocation.command,
    runArgs,
    config.workflowWorkspaceDir,
    config.codexTimeoutMs
  );
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  if (result.exitCode !== 0) {
    const cleanedOutput = sanitizeCommandOutput(result.output);
    const reason = summarizeFailureReason(cleanedOutput || result.output);
    return [
      `Create failed (${seconds}s)`,
      `Thread: ${parsed.threadId}`,
      `Reason: ${reason}`
    ].join("\n");
  }

  const summary = readWorkflowSummary(parsed.threadId);
  return [
    `Workflow created (${seconds}s)`,
    `Thread: ${parsed.threadId}`,
    `Status: ${summary.status}`,
    `Current: ${summary.currentStep}`,
    `Review: ${summary.interrupted ? "pending" : "none"}`
  ].join("\n");
}

async function routeMessage(args, context) {
  const text = String(args[0] || "").trim();
  if (!text) {
    return "Empty message.";
  }

  if (!existsSync(config.workflowWorkspaceDir)) {
    return `Cannot route message because WORKFLOW_WORKSPACE_DIR does not exist:\n${config.workflowWorkspaceDir}`;
  }

  const invocation = resolveWorkflowInvocation();
  const runArgs = [...invocation.prefixArgs, "handle-message", "--text", text];
  if (context?.chatId) {
    runArgs.push("--chat-id", String(context.chatId));
  }
  if (context?.chatType) {
    runArgs.push("--chat-type", String(context.chatType));
  }
  if (context?.senderId?.open_id) {
    runArgs.push("--user-open-id", String(context.senderId.open_id));
  }
  if (context?.messageId) {
    runArgs.push("--message-id", String(context.messageId));
  }
  if (context?.eventId) {
    runArgs.push("--event-id", String(context.eventId));
  }
  const result = await runProcess(
    invocation.command,
    runArgs,
    config.workflowWorkspaceDir,
    config.codexTimeoutMs
  );
  const output = sanitizeCommandOutput(result.output);
  if (result.exitCode !== 0) {
    return `Message handling failed:\n${summarizeFailureReason(output || result.output)}`;
  }
  return output || "Message received.";
}

async function runTest() {
  if (!existsSync(config.workspaceDir)) {
    return `Cannot run tests because WORKSPACE_DIR does not exist:\n${config.workspaceDir}`;
  }

  const startedAt = Date.now();
  const result = await runShellCommand(config.testCommand, config.workspaceDir, 120_000);
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  const status = result.exitCode === 0 ? "passed" : "failed";

  return [
    `Test ${status} in ${seconds}s`,
    `Command: ${config.testCommand}`,
    `Workspace: ${config.workspaceDir}`,
    "",
    tail(result.output, 40)
  ].join("\n");
}

function runShellCommand(command, cwd, timeoutMs) {
  return runProcess(command, [], cwd, timeoutMs, true);
}

function runProcess(command, args, cwd, timeoutMs, useShell = false) {
  return new Promise((resolve) => {
    const childEnv = {
      ...process.env,
      PYTHONIOENCODING: process.env.PYTHONIOENCODING || "utf-8",
      PYTHONUTF8: process.env.PYTHONUTF8 || "1"
    };
    const child = spawn(command, args, {
      cwd,
      shell: useShell,
      windowsHide: true,
      env: childEnv,
      stdio: ["ignore", "pipe", "pipe"]
    });

    let output = "";
    const timer = setTimeout(() => {
      output += "\n[timeout] command exceeded timeout and was terminated\n";
      child.kill("SIGTERM");
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      output += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      output += chunk.toString();
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ exitCode: 1, output: `${output}\n${error.message}` });
    });
    child.on("close", (exitCode) => {
      clearTimeout(timer);
      resolve({ exitCode, output });
    });
  });
}

function helpText() {
  return [
    "Available commands:",
    "/help - show commands",
    "/model - show the configured workflow model",
    "/ping - check bridge status",
    "/run-test - run the configured TEST_COMMAND",
    "/create-workflow --tapd-id <id> --short-name <slug> (--brief <text> | --brief-file <path>) [--title <text>] [--auto-approve]"
  ].join("\n");
}

function tail(text, maxLines) {
  const lines = String(text || "").trimEnd().split(/\r?\n/);
  return lines.slice(-maxLines).join("\n") || "[no output]";
}

function sanitizeCommandOutput(text) {
  return String(text || "")
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed) {
        return false;
      }
      if (trimmed.startsWith("[RF_DEBUG]")) {
        return false;
      }
      if (trimmed.includes("LangChainPendingDeprecationWarning")) {
        return false;
      }
      return true;
    })
    .join("\n")
    .trim();
}

function summarizeFailureReason(text) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) {
    return "unknown error";
  }

  const preferred = lines.find((line) => {
    const normalized = line.toLowerCase();
    return (
      normalized.includes("error") ||
      normalized.includes("failed") ||
      normalized.includes("traceback") ||
      normalized.includes("missing") ||
      normalized.includes("denied")
    );
  });
  return preferred || lines[lines.length - 1];
}

function readWorkflowSummary(threadId) {
  const runtimeDir = join(
    config.workflowWorkspaceDir,
    ".runtime",
    "langgraph",
    "threads",
    threadId
  );
  const statePath = join(runtimeDir, "latest_state.json");
  const interruptPath = join(runtimeDir, "latest_interrupt.json");

  let status = "unknown";
  let currentStep = "unknown";
  let interrupted = false;

  if (existsSync(statePath)) {
    try {
      const state = JSON.parse(readFileSync(statePath, "utf8"));
      status = String(state.status || "unknown");
      currentStep = String(state.current_step || "unknown");
    } catch {
      status = "unknown";
      currentStep = "unknown";
    }
  }

  if (existsSync(interruptPath)) {
    try {
      const interrupt = JSON.parse(readFileSync(interruptPath, "utf8"));
      interrupted = Boolean(interrupt && Object.keys(interrupt).length > 0);
    } catch {
      interrupted = false;
    }
  }

  return { status, currentStep, interrupted };
}

function isModelQuestion(text) {
  const normalized = text.trim().toLowerCase();
  if (!normalized) {
    return false;
  }

  return /(^|[\s/])(浠€涔堟ā鍨媩鍝釜妯″瀷|鐜板湪鐢ㄧ殑鏄粈涔堟ā鍨媩褰撳墠妯″瀷|current model|which model|what model)([\s?锛?锛?]|$)/i.test(normalized);
}

function parseCreateWorkflowArgs(args) {
  const options = parseLongOptions(args);
  const tapdId = firstNonEmpty(options["tapd-id"], options.tapd, options.t);
  const shortName = firstNonEmpty(options["short-name"], options.short, options.s);
  const briefText = firstNonEmpty(options.brief, options.b);
  const briefFile = firstNonEmpty(options["brief-file"]);
  const title = firstNonEmpty(options.title);
  const autoApproveRaw = firstNonEmpty(options["auto-approve"]);
  const autoApprove = autoApproveRaw === "" || isTruthy(autoApproveRaw);

  if (!tapdId || !shortName || (!briefText && !briefFile)) {
    return {
      ok: false,
      message: [
        "Usage:",
        "/create-workflow --tapd-id <id> --short-name <slug> (--brief <text> | --brief-file <path>) [--title <text>] [--auto-approve]"
      ].join("\n")
    };
  }

  return {
    ok: true,
    tapdId,
    shortName,
    threadId: buildThreadId(tapdId, shortName),
    briefText,
    briefFile,
    title,
    autoApprove
  };
}

function resolveWorkflowInvocation() {
  const configured = String(config.workflowCommand || "").trim();
  if (configured && configured !== "requirement-flow") {
    return { command: configured, prefixArgs: [] };
  }

  const venvPython = join(config.workflowWorkspaceDir, ".venv", "Scripts", "python.exe");
  if (existsSync(venvPython)) {
    return { command: venvPython, prefixArgs: ["-m", "requirement_flow.cli"] };
  }

  return { command: configured || "requirement-flow", prefixArgs: [] };
}

function parseLongOptions(args) {
  const options = {};
  let index = 0;

  while (index < args.length) {
    const token = String(args[index] || "");
    if (!token.startsWith("--")) {
      index += 1;
      continue;
    }

    const key = token.slice(2).toLowerCase();
    const values = [];
    index += 1;
    while (index < args.length && !String(args[index] || "").startsWith("--")) {
      values.push(String(args[index]));
      index += 1;
    }

    options[key] = values.join(" ").trim();
  }

  return options;
}

function buildThreadId(tapdId, shortName) {
  const tapd = String(tapdId || "").trim();
  const normalizedShortName = String(shortName || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return `${tapd}_${normalizedShortName || "new-requirement"}`;
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (typeof value !== "string") {
      continue;
    }
    const trimmed = value.trim();
    if (trimmed) {
      return trimmed;
    }
    if (value === "") {
      return "";
    }
  }
  return "";
}

function isTruthy(value) {
  return /^(1|true|yes|y|on)$/i.test(String(value || "").trim());
}
