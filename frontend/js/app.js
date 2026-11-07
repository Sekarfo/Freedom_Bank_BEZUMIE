// Точка входа: маршруты, статус модели в шапке, параметры из query string (в т.ч. от Dynamics 365).
import { API } from "./api.js";
import { route, start } from "./router.js";
import { MODES, PROVIDERS, esc } from "./utils.js";

route("/conversations", () => import("./pages/conversations.js"));
route("/conversation/:id", () => import("./pages/card.js"));
route("/checklists", () => import("./pages/checklists.js"));
route("/appeals", () => import("./pages/appeals.js"));
route("/analytics", () => import("./pages/analytics.js"));
route("/settings", () => import("./pages/settings.js"));

// ?conversationId=... или веб-ресурс D365: ?data=conversationId%3D...
function applyQueryParams() {
  const qs = new URLSearchParams(location.search);
  let params = qs;
  if (qs.get("data")) {
    try { params = new URLSearchParams(decodeURIComponent(qs.get("data"))); } catch (e) { /* ignore */ }
  }
  const id = params.get("conversationId") || params.get("conversation_id");
  const page = params.get("page");
  if (id && !location.hash) location.hash = `#/conversation/${encodeURIComponent(id)}`;
  else if (page && !location.hash) location.hash = `#/${page}`;
}

export async function refreshHeader() {
  const box = document.querySelector("[data-model-status]");
  try {
    const [settings, appeals] = await Promise.all([API.settings(), API.appeals("open")]);
    const badge = document.querySelector("[data-appeals-count]");
    if (badge) { badge.textContent = appeals.length; badge.hidden = !appeals.length; }
    const parts = [MODES[settings.mode]];
    if (settings.mode !== "cloud") parts.push(settings.local_model);
    if (settings.mode !== "local") parts.push(PROVIDERS[settings.cloud_provider]);
    box.innerHTML = `<span class="cqa-dot is-on"></span><span>${esc(parts.join(" · "))}</span>`;
  } catch (e) {
    box.innerHTML = '<span class="cqa-dot is-off"></span><span>сервер недоступен</span>';
  }
}

applyQueryParams();
start();
refreshHeader();
window.addEventListener("cqa:refresh-header", refreshHeader);
