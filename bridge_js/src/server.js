import { createServer } from "node:http";
import * as Lark from "@larksuiteoapi/node-sdk";
import { config } from "./env.js";
import { sendTextMessage } from "./feishu.js";
import { parseCommand, runCommand } from "./commands.js";

installTimestampedLogging();

const seenEventIds = new Map();
const seenEventTtlMs = 10 * 60 * 1000;

const server = createServer((request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    return sendJson(response, 200, { ok: true });
  }

  sendJson(response, 404, { error: "Not found" });
});

server.listen(config.port, config.host, () => {
  console.log(`Feishu Message Bridge listening on http://${config.host}:${config.port}`);
  console.log("Feishu event mode: long connection");
});

const wsClient = new Lark.WSClient({
  appId: config.feishuAppId,
  appSecret: config.feishuAppSecret,
  appType: Lark.AppType.SelfBuild,
  domain: Lark.Domain.Feishu,
  loggerLevel: Lark.LoggerLevel.info,
  logger: createSdkLogger()
});

console.log("Connecting to Feishu long connection...");

wsClient.start({
  eventDispatcher: new Lark.EventDispatcher({}).register({
    "im.message.receive_v1": async (data) => {
      logEvent("Raw message event received", {
        eventId: data?.header?.event_id || "[missing]",
        messageId: data?.message?.message_id || "[missing]",
        chatId: data?.message?.chat_id || "[missing]",
        chatType: data?.message?.chat_type || "[missing]",
        senderOpenId: data?.sender?.sender_id?.open_id || "[missing]",
        senderUserId: data?.sender?.sender_id?.user_id || "[missing]",
        messageType: data?.message?.message_type || "[missing]"
      });

      const event = parseFeishuMessageEvent(data);
      if (!event) {
        logEvent("Ignoring unsupported message event", {
          eventId: data?.header?.event_id || "[missing]",
          messageType: data?.message?.message_type || "[missing]"
        });
        return;
      }

      if (isDuplicateEvent(event.eventId)) {
        logEvent("Ignoring duplicate message event", {
          eventId: event.eventId || "[missing]",
          messageId: event.messageId || "[missing]"
        });
        return;
      }

      await handleMessageEvent(event);
    }
  })
});

async function handleMessageEvent(event) {
  logEvent("Parsed text event", {
    eventId: event.eventId || "[missing]",
    messageId: event.messageId || "[missing]",
    chatId: event.chatId || "[missing]",
    chatType: event.chatType || "[missing]",
    senderOpenId: event.senderId?.open_id || "[missing]",
    senderUserId: event.senderId?.user_id || "[missing]",
    mentionKeys: formatMentionKeys(event.mentions),
    text: previewText(event.text)
  });

  const chatId = event.chatId || config.feishuDefaultChatId;
  if (!chatId) {
    console.warn("No chat id available; cannot reply");
    return;
  }

  await sendReplyMessage(event, "Received.").then(() => {
    logEvent("Acknowledgement sent", {
      chatId,
      messageId: event.messageId || "[missing]"
    });
  }).catch((error) => {
    console.error("Acknowledgement failed", error);
  });

  const command = parseCommand(event.text);
  if (!command) {
    logEvent("Ignoring non-command text", {
      eventId: event.eventId || "[missing]",
      messageId: event.messageId || "[missing]",
      text: previewText(event.text)
    });
    return;
  }

  logEvent("Command accepted", {
    command: command.name,
    chatId: chatId || "[missing]",
    messageId: event.messageId || "[missing]"
  });

  try {
    const result = await runCommand(command, event);
    await sendReplyMessage(event, result);
  } catch (error) {
    console.error(error);
    await sendReplyMessage(event, `Command failed:\n${error.message}`).catch(console.error);
  }
}

function parseFeishuMessageEvent(body) {
  const message = body?.message;
  if (!message) {
    return null;
  }

  if (message.message_type !== "text") {
    return null;
  }

  return {
    eventId: body?.header?.event_id,
    chatId: message.chat_id,
    chatType: message.chat_type,
    messageId: message.message_id,
    senderId: body?.sender?.sender_id,
    mentions: message.mentions,
    text: parseTextContent(message.content)
  };
}

async function sendReplyMessage(event, text) {
  const chatId = event.chatId || config.feishuDefaultChatId;
  if (!chatId) {
    throw new Error("Missing Feishu chat id");
  }

  return sendTextMessage(chatId, formatReplyText(event, text));
}

function isDuplicateEvent(eventId) {
  if (!eventId) {
    return false;
  }

  const now = Date.now();
  for (const [id, expiresAt] of seenEventIds) {
    if (expiresAt <= now) {
      seenEventIds.delete(id);
    }
  }

  if (seenEventIds.has(eventId)) {
    return true;
  }

  seenEventIds.set(eventId, now + seenEventTtlMs);
  return false;
}

function parseTextContent(content) {
  if (!content) {
    return "";
  }

  try {
    const parsed = JSON.parse(content);
    return parsed.text || "";
  } catch {
    return String(content);
  }
}

function sendJson(response, statusCode, body) {
  response.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body));
}

function installTimestampedLogging() {
  const methods = ["log", "info", "warn", "error"];
  for (const method of methods) {
    const original = console[method].bind(console);
    console[method] = (...args) => original(`[${new Date().toISOString()}]`, ...args);
  }
}

function logEvent(message, details) {
  console.log(`${message}: ${formatDetails(details)}`);
}

function formatDetails(details) {
  return Object.entries(details)
    .map(([key, value]) => `${key}=${value}`)
    .join(" ");
}

function previewText(text) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (value.length <= 120) {
    return value || "[empty]";
  }

  return `${value.slice(0, 117)}...`;
}

function formatMentionKeys(mentions) {
  if (!Array.isArray(mentions) || mentions.length === 0) {
    return "[none]";
  }

  return mentions
    .map((mention) => mention?.key || mention?.name || "[unknown]")
    .join(",");
}

function formatReplyText(event, text) {
  const body = String(text || "");
  if (event?.chatType !== "group") {
    return body;
  }

  const openId = event?.senderId?.open_id;
  if (!openId) {
    return body;
  }

  return `<at user_id="${openId}">user</at> ${body}`;
}

function createSdkLogger() {
  return {
    error: (...args) => console.error(formatSdkLog(args)),
    warn: (...args) => console.warn(formatSdkLog(args)),
    info: (...args) => {
      const message = formatSdkLog(args);
      if (!message) {
        return;
      }

      console.log(message);
    },
    debug: (...args) => {
      const message = formatSdkLog(args);
      if (!message) {
        return;
      }

      console.log(message);
    },
    trace: (...args) => {
      const message = formatSdkLog(args);
      if (!message) {
        return;
      }

      console.log(message);
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
