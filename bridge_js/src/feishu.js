import { config } from "./env.js";

let cachedTenantToken = null;
let cachedTenantTokenExpiresAt = 0;

export async function sendTextMessage(chatId, text) {
  if (!chatId) {
    throw new Error("Missing Feishu chat id");
  }

  const tenantToken = await getTenantAccessToken();
  const response = await fetch(
    `${config.feishuApiBase}/open-apis/im/v1/messages?receive_id_type=chat_id`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${tenantToken}`,
        "Content-Type": "application/json; charset=utf-8"
      },
      body: JSON.stringify({
        receive_id: chatId,
        msg_type: "text",
        content: JSON.stringify({ text: truncateFeishuText(text) })
      })
    }
  );

  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.code !== 0) {
    throw new Error(`Feishu send message failed: ${response.status} ${JSON.stringify(data)}`);
  }

  return data.data;
}

export async function getTenantAccessToken() {
  const now = Date.now();
  if (cachedTenantToken && now < cachedTenantTokenExpiresAt) {
    return cachedTenantToken;
  }

  const response = await fetch(`${config.feishuApiBase}/open-apis/auth/v3/tenant_access_token/internal`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json; charset=utf-8"
    },
    body: JSON.stringify({
      app_id: config.feishuAppId,
      app_secret: config.feishuAppSecret
    })
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.code !== 0 || !data.tenant_access_token) {
    throw new Error(`Feishu token request failed: ${response.status} ${JSON.stringify(data)}`);
  }

  cachedTenantToken = data.tenant_access_token;
  cachedTenantTokenExpiresAt = now + Math.max(60, data.expire - 120) * 1000;
  return cachedTenantToken;
}

function truncateFeishuText(text) {
  const value = String(text);
  if (value.length <= 3500) {
    return value;
  }

  return `${value.slice(0, 3400)}\n\n[truncated]`;
}
