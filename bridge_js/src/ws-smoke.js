import * as Lark from "@larksuiteoapi/node-sdk";
import { config } from "./env.js";

installTimestampedLogging();

console.log("Starting minimal Feishu WS smoke test...");
console.log(`App ID: ${config.feishuAppId}`);

const wsClient = new Lark.WSClient({
  appId: config.feishuAppId,
  appSecret: config.feishuAppSecret,
  appType: Lark.AppType.SelfBuild,
  domain: Lark.Domain.Feishu,
  loggerLevel: Lark.LoggerLevel.info,
  logger: createSdkLogger()
});

wsClient.start({
  eventDispatcher: new Lark.EventDispatcher({}).register({
    "im.message.receive_v1": async (data) => {
      console.log(
        `Smoke event received: eventId=${data?.header?.event_id || "[missing]"} messageId=${data?.message?.message_id || "[missing]"}`
      );
    }
  })
});

function installTimestampedLogging() {
  const methods = ["log", "info", "warn", "error"];
  for (const method of methods) {
    const original = console[method].bind(console);
    console[method] = (...args) => original(`[${new Date().toISOString()}]`, ...args);
  }
}

function createSdkLogger() {
  return {
    error: (...args) => console.error(formatSdkLog(args)),
    warn: (...args) => console.warn(formatSdkLog(args)),
    info: (...args) => {
      const message = formatSdkLog(args);
      if (message) {
        console.log(message);
      }
    },
    debug: (...args) => {
      const message = formatSdkLog(args);
      if (message) {
        console.log(message);
      }
    },
    trace: (...args) => {
      const message = formatSdkLog(args);
      if (message) {
        console.log(message);
      }
    }
  };
}

function formatSdkLog(args) {
  const text = args
    .flatMap((arg) => normalizeSdkLogPart(arg))
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) {
    return "";
  }

  if (text.includes("receive events or callbacks through persistent connection only available")) {
    return "Feishu SDK: persistent connection mode is enabled for this self-built app";
  }

  if (text.includes("[ws] ws client ready")) {
    return "Feishu SDK: ws client ready";
  }

  return text;
}

function normalizeSdkLogPart(value) {
  if (Array.isArray(value)) {
    return value.flatMap((item) => normalizeSdkLogPart(item));
  }

  if (value instanceof Error) {
    return [value.stack || value.message];
  }

  const text = String(value ?? "").replace(/[^\x09\x0A\x0D\x20-\x7E]/g, " ").trim();
  if (!text) {
    return [];
  }

  return [text];
}
