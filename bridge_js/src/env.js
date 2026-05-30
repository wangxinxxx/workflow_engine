import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

loadDotEnv();

export const config = {
  port: numberFromEnv("PORT", 8787),
  host: process.env.HOST || "0.0.0.0",
  feishuApiBase: trimTrailingSlash(process.env.FEISHU_API_BASE || "https://open.feishu.cn"),
  feishuAppId: requiredEnv("FEISHU_APP_ID"),
  feishuAppSecret: requiredEnv("FEISHU_APP_SECRET"),
  feishuDefaultChatId: process.env.FEISHU_DEFAULT_CHAT_ID || ""
};

function loadDotEnv() {
  const path = resolve(process.cwd(), ".env");
  if (!existsSync(path)) {
    return;
  }

  const lines = readFileSync(path, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const index = trimmed.indexOf("=");
    if (index === -1) {
      continue;
    }

    const key = trimmed.slice(0, index).trim();
    const rawValue = trimmed.slice(index + 1).trim();
    if (!key || process.env[key] !== undefined) {
      continue;
    }

    process.env[key] = unquote(rawValue);
  }
}

function requiredEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function numberFromEnv(name, fallback) {
  const value = process.env[name];
  if (!value) {
    return fallback;
  }

  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`Invalid numeric environment variable: ${name}`);
  }

  return parsed;
}

function trimTrailingSlash(value) {
  return value.replace(/\/+$/, "");
}

function unquote(value) {
  if (
    (value.startsWith("\"") && value.endsWith("\"")) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1);
  }
  return value;
}
