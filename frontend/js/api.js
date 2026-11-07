// HTTP-клиент. Адрес API — только из config.js (window.CQA_CONFIG.apiBaseUrl), без захардкоженного хоста.

export function apiBase() {
  const cfg = window.CQA_CONFIG || {};
  return String(cfg.apiBaseUrl || "").replace(/\/+$/, "");
}

export function apiUrl(path) {
  return apiBase() + path;
}

export async function api(path, { method = "GET", body } = {}) {
  let resp;
  try {
    resp = await fetch(apiUrl(path), {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new Error("Сервер недоступен. Проверьте, что backend запущен.");
  }
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!resp.ok) {
    let msg = (data && data.detail) || `Ошибка ${resp.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg || JSON.stringify(d)).join("; ");
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export const API = {
  conversations: (params = {}) => api("/api/conversations?" + new URLSearchParams(clean(params))),
  conversation: (id, evaluationId) =>
    api(`/api/conversations/${encodeURIComponent(id)}` + (evaluationId ? `?evaluation_id=${evaluationId}` : "")),
  audioUrl: (id) => apiUrl(`/api/conversations/${encodeURIComponent(id)}/audio`),
  audioPageUrl: (id) => apiUrl(`/api/conversations/${encodeURIComponent(id)}/audio-page`),
  fetchDataset: (group_id, dataset) => api("/api/datasets/fetch", { method: "POST", body: { group_id, dataset: dataset || null } }),
  upload: (payload, dataset) => api("/api/conversations/upload", { method: "POST", body: { payload, dataset } }),
  evaluate: (body) => api("/api/evaluations", { method: "POST", body }),
  job: (id) => api(`/api/jobs/${id}`),
  jobs: () => api("/api/jobs"),
  evaluation: (id) => api(`/api/evaluations/${id}`),
  checklists: () => api("/api/checklists"),
  version: (id) => api(`/api/checklists/versions/${id}`),
  versionPrompt: (id) => api(`/api/checklists/versions/${id}/prompt`),
  createVersion: (checklistId, criteria, note) =>
    api(`/api/checklists/${checklistId}/versions`, { method: "POST", body: { criteria, note } }),
  createChecklist: (name, description, criteria, note) =>
    api("/api/checklists", { method: "POST", body: { name, description, criteria, note } }),
  diff: (a, b) => api(`/api/checklists/diff/${a}/${b}`),
  appeals: (status) => api("/api/appeals" + (status ? `?status=${status}` : "")),
  createAppeal: (evaluation_id, criterion_id, comment, author) =>
    api("/api/appeals", { method: "POST", body: { evaluation_id, criterion_id, comment, author } }),
  decideAppeal: (id, body) => api(`/api/appeals/${id}/decision`, { method: "POST", body }),
  analytics: (params = {}) => api("/api/analytics/summary?" + new URLSearchParams(clean(params))),
  exportUrl: (dataset, versionId) =>
    apiUrl(`/api/export/results?download=true&dataset=${encodeURIComponent(dataset)}` + (versionId ? `&checklist_version_id=${versionId}` : "")),
  validateExport: (dataset) => api(`/api/export/validate?dataset=${encodeURIComponent(dataset)}`),
  status: () => api("/api/system/status"),
  settings: () => api("/api/settings"),
  saveSettings: (body) => api("/api/settings", { method: "PUT", body }),
};

function clean(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null && v !== "") out[k] = v;
  return out;
}
