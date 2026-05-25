import { config } from "./env.js";
import { sendTextMessage } from "./feishu.js";

const text = process.argv.slice(2).join(" ").trim();

if (!text) {
  console.error("Usage: npm run notify -- \"message\"");
  process.exit(1);
}

if (!config.feishuDefaultChatId) {
  console.error("Missing FEISHU_DEFAULT_CHAT_ID");
  process.exit(1);
}

await sendTextMessage(config.feishuDefaultChatId, text);
console.log("sent");
