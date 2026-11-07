// Общие функции отображения.

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export const SPEAKERS = { client: "Клиент", operator: "Оператор", bot: "Бот", system: "Система" };
export const CHANNELS = { chat: "Чат", voice: "Звонок", email: "Email", messenger: "Мессенджер" };
export const PROVIDERS = { anthropic: "Claude", openai: "OpenAI-совместимый API" };
export const CONFIDENCE = { high: "уверенность высокая", medium: "уверенность средняя", low: "уверенность низкая" };
export const EVIDENCE = {
  verified: "цитата подтверждена", corrected: "номер реплики исправлен кодом", no_quote: "без цитаты",
  unverified: "цитата не найдена в тексте", missing: "нет доказательства", default: "проверенный участок по умолчанию",
};

export const ICONS = {
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
  flag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 4 20 12 6 20 6 4"/></svg>',
  external: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6M10 14 21 3"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>',
};

export function fmtScore(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toFixed(2).replace(/\.?0+$/, "");
}

export function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

export function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return Math.round(v * 100) + "%";
}

export function fmtMoney(v) {
  if (v === null || v === undefined) return "—";
  if (v === 0) return "$0";
  return "$" + (v < 0.01 ? v.toFixed(5) : v.toFixed(4));
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function scoreTone(v) {
  if (v === null || v === undefined) return "na";
  if (v >= 85) return "good";
  if (v >= 70) return "mid";
  return "bad";
}

export function scaleMax(c) {
  const nums = (c.allowed_values || []).filter((x) => typeof x === "number");
  return nums.length ? Math.max(...nums) : null;
}

export function valueLabel(c, v) {
  if (c.is_critical_flag) return v === true ? "Нарушение" : v === false ? "Нет нарушений" : "N/A";
  if (v === null || v === undefined) return "N/A";
  if (v === true) return "Да";
  if (v === false) return "Нет";
  const max = scaleMax(c);
  return max ? `${v} из ${max}` : String(v);
}

export function valueTone(c, v) {
  if (v === null || v === undefined) return "na";
  if (c.is_critical_flag) return v === true ? "bad" : "good";
  if (v === true) return "good";
  if (v === false) return "bad";
  const max = scaleMax(c);
  if (v === max) return "good";
  if (v === 0) return "bad";
  return "mid";
}

export function valueChip(c, v) {
  return `<span class="cqa-chip cqa-chip--${valueTone(c, v)}">${esc(valueLabel(c, v))}</span>`;
}

export function scoreChip(v) {
  return `<span class="cqa-chip cqa-chip--${scoreTone(v)}">${esc(fmtScore(v))}</span>`;
}

// Ссылки на реплики в обосновании: «[12]», «реплика 12», «реплики 5 и 6».
export function linkifyTurns(text, maxIdx = 9999) {
  let s = esc(text);
  s = s.replace(/\[(\d{1,3})\]/g, (m, n) => (+n >= 1 && +n <= maxIdx ? turnLink(+n) : m));
  s = s.replace(/(реплик[а-яё]*|turn[s]?)\s+((?:№\s*)?\d{1,3}(?:\s*(?:,|и|and|–|-)\s*\d{1,3})*)/gi, (m, word, nums) => {
    const linked = nums.replace(/\d{1,3}/g, (n) => (+n >= 1 && +n <= maxIdx ? turnLink(+n) : n));
    return `${word} ${linked}`;
  });
  return s;
}

export function turnLink(n) {
  return `<a class="cqa-turnlink" data-turn="${n}" href="#" title="Перейти к реплике ${n}">[${n}]</a>`;
}

export function fmtClock(sec) {
  if (sec === null || sec === undefined) return "";
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function parseValue(raw) {
  if (raw === "null") return null;
  if (raw === "true") return true;
  if (raw === "false") return false;
  return Number(raw);
}

export function valueOption(c, v) {
  return `<option value="${v === null ? "null" : String(v)}">${esc(valueLabel(c, v))}</option>`;
}
