// Экран «Причины обращений» — отдельная страница в дизайне основного приложения (его стили, иконки, разметка).
// Данные: /api/v1/analytics/reasons, /api/v1/reasons/*, список диалогов — /api/conversations.
import { ICONS } from "./icons.js";

const NAV = [
  ["dialogs", "message-square", "Диалоги", "/#dialogs"],
  ["checklists", "clipboard-check", "Чек-листы", "/#checklists"],
  ["appeals", "scale", "Апелляции", "/#appeals"],
  ["quality", "flask-conical", "Проверка качества", "/#quality"],
  ["reasons", "chart-pie", "Причины обращений", "/reasons.html"],
  ["submission", "file-json", "Сдача результатов", "/#submission"],
];
const OUTCOMES = [
  ["resolved", "Решено"], ["escalated", "Передано специалистам"], ["unresolved", "Не решено"],
  ["client_left", "Клиент ушёл"], ["no_request", "Без запроса"],
];
const LANGS = { RU: "Русский", KZ: "Казахский", EN: "Английский" };

const state = {
  filters: { dataset: "", language: "", channel: "" }, level: "topic", expanded: new Set(),
  data: null, groups: [], taxonomy: null, versions: [], discovery: null, busy: "", error: "",
};

// --------------------------------------------------------------------------- утилиты
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (name, size = 19, cls = "") =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-${name} ${cls}" aria-hidden="true">${ICONS[name] || ""}</svg>`;
const num = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const fmt = (x) => (x == null ? "—" : num.format(x));
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const share = (n, total) => (total ? n / total : 0);
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};
const dialogs = (n) => `${n} ${plural(n, "диалог", "диалога", "диалогов")}`;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? undefined
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* не JSON */ }
  if (!res.ok) throw new Error(data?.error || `Ошибка ${res.status}`);
  return data;
}

async function loadAppStyles() {
  // Имя CSS-файла основного приложения меняется при каждой сборке — берём его из index.html.
  try {
    const html = await (await fetch("/", { cache: "no-store" })).text();
    const hrefs = [...html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"/g)].map((m) => m[1]);
    await Promise.all(hrefs.map((href) => new Promise((resolve) => {
      const link = Object.assign(document.createElement("link"), { rel: "stylesheet", href });
      link.onload = link.onerror = resolve;
      document.head.prepend(link);
    })));
  } catch { /* страница останется без общих стилей, но рабочей */ }
}

let toastTimer;
function toast(text, error = false) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    document.body.append(el);
  }
  el.className = `toast ${error ? "error" : ""}`;
  el.setAttribute("role", error ? "alert" : "status");
  el.innerHTML = `${icon(error ? "triangle-alert" : "check", 18)}<span>${esc(text)}</span>`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 7000);
}

// --------------------------------------------------------------------------- оболочка (как в приложении)
function renderShell() {
  const nav = NAV.map(([key, ic, label, href]) => `
    <a class="nav-item ${key === "reasons" ? "active" : ""}" href="${href}" title="${label}" aria-label="${label}"
       ${key === "reasons" ? 'aria-current="page"' : ""}>${icon(ic)}<span>${label}</span></a>`).join("");
  document.getElementById("root").className = "";
  document.getElementById("root").innerHTML = `
  <div class="app-shell reasons-page">
    <a class="skip-link" href="#main-content">Перейти к содержимому</a>
    <aside class="sidebar">
      <a class="brand" href="/"><img class="brand-logo" src="/brand/freedom-logo.svg" alt="Freedom" width="174" height="39"><img class="brand-compact" src="/brand/favicon-32x32.png" alt="Freedom" width="32" height="32"></a>
      <div class="workspace-tag"><span class="workspace-dot"></span><div><strong>Conversation Quality</strong><span>Проверка качества сервиса</span></div><span class="mini-label">CQA</span></div>
      <div class="nav-caption">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav aria-label="Основная навигация">${nav}</nav>
      <div class="sidebar-note"><span class="note-icon">${icon("shield-check", 23)}</span><strong>За каждой оценкой —<br>доказательство.</strong><p>Откройте диалог, чтобы проверить выводы по репликам.</p><span class="note-languages">RU <i></i> KZ <i></i> EN</span></div>
      <div class="sidebar-footer"><span class="avatar">B</span><div><strong>BEZUMIE</strong><span>Data Challenge · CQA</span></div></div>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <span class="breadcrumb"><span class="breadcrumb-root">CQA Workspace</span>${icon("chevron-right", 14)}<strong>Причины обращений</strong></span>
        <div class="topbar-right">${themeToggle()}</div>
      </header>
      <main id="main-content" tabindex="-1"></main>
      <footer class="main-footer"><span><strong>BEZUMIE</strong> / Conversation Quality</span><span>Data Challenge <span class="footer-dot">·</span> Freedom</span></footer>
    </div>
  </div>`;
  bindThemeToggle();
  for (const a of document.querySelectorAll("a.nav-item")) a.style.textDecoration = "none";
}

function themeToggle() {
  const dark = document.documentElement.dataset.theme === "dark";
  return `<button class="theme-toggle" aria-label="Тёмная тема" aria-pressed="${dark}" title="${dark ? "Включить светлую тему" : "Включить тёмную тему"}">${icon("sun", 17, dark ? "" : "selected")}${icon("moon", 17, dark ? "selected" : "")}</button>`;
}

function bindThemeToggle() {
  // Тот же переключатель, что в приложении: тема в localStorage «cqa-theme», общая для обеих страниц.
  document.querySelector(".theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", next === "dark" ? "#051525" : "#ffffff");
    try { localStorage.setItem("cqa-theme", next); } catch { /* приватный режим */ }
    document.querySelector(".topbar-right").innerHTML = themeToggle();
    bindThemeToggle();
  });
}

// --------------------------------------------------------------------------- данные
async function loadAll() {
  const [convs, taxonomy, versions] = await Promise.all([
    api("/api/conversations"), api("/api/v1/reasons/taxonomy"), api("/api/v1/reasons/versions"),
  ]);
  state.groups = [...new Set(convs.map((c) => c.groupId))].sort();
  state.taxonomy = taxonomy;
  state.versions = versions;
  await loadAnalytics();
}

async function loadAnalytics() {
  const q = new URLSearchParams({ level: state.level });
  for (const [k, v] of Object.entries(state.filters)) if (v) q.set(k, v);
  state.data = await api(`/api/v1/analytics/reasons?${q}`);
}

// --------------------------------------------------------------------------- разметка блоков
function heading() {
  const tax = state.taxonomy;
  return `
  <div class="page-heading dashboard-heading">
    <div>
      <p class="eyebrow"><span></span> АНАЛИТИКА ОБРАЩЕНИЙ</p>
      <h1>Причины <em>обращений.</em></h1>
      <p class="page-subtitle">О чём пишут клиенты, <strong>чем это заканчивается</strong> и где операторы теряют баллы.</p>
    </div>
    <div class="dashboard-actions">
      <a class="button secondary" href="#taxonomy" style="text-decoration:none">${icon("list-filter", 17)}Справочник v${esc(tax?.version ?? "")}</a>
      <a class="button primary review-cta" href="/#dialogs" style="text-decoration:none">К диалогам ${icon("arrow-right", 17)}</a>
    </div>
  </div>`;
}

function filters() {
  const f = state.filters;
  const opt = (v, label, cur) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`;
  return `
  <section class="panel" aria-label="Фильтры">
    <div class="filters">
      <label class="filter-field"><span>Набор</span><select data-filter="dataset">${opt("", "Все наборы", f.dataset)}${state.groups.map((g) => opt(g, g, f.dataset)).join("")}</select></label>
      <label class="filter-field"><span>Язык</span><select data-filter="language">${opt("", "Все языки", f.language)}${Object.entries(LANGS).map(([k, v]) => opt(k, v, f.language)).join("")}</select></label>
      <label class="filter-field"><span>Канал</span><select data-filter="channel">${opt("", "Все каналы", f.channel)}${opt("chat", "Чаты", f.channel)}${opt("voice", "Звонки", f.channel)}</select></label>
    </div>
  </section>`;
}

function stats(d) {
  const r = d.resolution || {};
  const n = d.classified;
  const lost = (r.unresolved || 0) + (r.client_left || 0);
  const top = d.topics[0];
  return `
  <div class="stats-grid dashboard-stats">
    <div class="stat-card"><div><span>Классифицировано</span><span class="stat-icon">${icon("chart-pie", 18)}</span></div>
      <strong>${n}<small>из ${d.evaluated}</small></strong>
      <div class="stat-progress"><div role="progressbar" aria-label="Доля диалогов с причиной" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(share(n, d.evaluated) * 100)}"><i style="width:${share(n, d.evaluated) * 100}%"></i></div><span>${pct(share(n, d.evaluated))}</span></div>
    </div>
    <div class="stat-card featured"><div><span>Решено оператором</span><span class="stat-icon">${icon("check-check", 18)}</span></div>
      <strong>${pct(share(r.resolved || 0, n))}</strong><p>${dialogs(r.resolved || 0)} из ${n}</p></div>
    <div class="stat-card"><div><span>Передано специалистам</span><span class="stat-icon">${icon("arrow-right", 18)}</span></div>
      <strong>${pct(share(r.escalated || 0, n))}</strong><p>${dialogs(r.escalated || 0)} ждут ответа отдела</p></div>
    <div class="stat-card attention-card"><div><span>Не решено или клиент ушёл</span><span class="stat-icon">${icon("triangle-alert", 18)}</span></div>
      <strong>${pct(share(lost, n))}</strong><p>${top ? `Чаще всего пишут: ${esc(top.title)} (${pct(top.share)})` : "—"}</p></div>
  </div>`;
}

function outcomeBar(res, total) {
  const parts = OUTCOMES.filter(([k]) => res?.[k]).map(([k, label]) =>
    `<i class="oc-${k}" style="width:${share(res[k], total) * 100}%" title="${label}: ${res[k]}"></i>`).join("");
  return `<div class="outcome-bar" role="img" aria-label="${OUTCOMES.filter(([k]) => res?.[k]).map(([k, l]) => `${l} ${res[k]}`).join(", ")}">${parts}</div>`;
}

function weakCell(row) {
  const w = row.weakest_criteria?.[0];
  if (!w) return `<span class="status neutral">—</span>`;
  const bad = w.gap != null && w.gap <= -0.15;
  return `<span class="verdict ${bad ? "negative" : "na"}">${pct(w.avg_ratio)}</span>${esc(w.title)}
    ${w.baseline != null ? `<div class="outcome-note">в среднем ${pct(w.baseline)}</div>` : ""}`;
}

function topicsTable(d) {
  const rows = d.topics.map((t) => {
    const open = state.expanded.has(t.code);
    const subs = open ? t.subtopics.map((s) => row(s, true)).join("") : "";
    return row(t, false, open) + subs;
  }).join("");
  function row(t, sub, open) {
    const name = sub
      ? `<span class="reason-name"><strong>${esc(t.title)}</strong><code>${esc(t.code)}</code></span>`
      : `<button class="reason-name" data-toggle="${esc(t.code)}" aria-expanded="${!!open}" ${t.subtopics.length ? "" : "disabled"}>
           <strong>${t.subtopics.length ? icon("chevron-right", 14, "chev") : ""}${esc(t.title)}</strong>
           <small>${t.subtopics.length} ${plural(t.subtopics.length, "подтема", "подтемы", "подтем")}</small></button>`;
    return `<tr class="${sub ? "subtopic-row" : ""}">
      <td>${name}</td>
      <td class="share-cell"><div class="share-bar"><div><i style="width:${(t.share || 0) * 100}%"></i></div>${pct(t.share)}</div></td>
      <td class="align-right"><span class="table-score">${t.count}</span></td>
      <td class="align-right"><span class="table-score">${fmt(t.avg_score)}</span></td>
      <td>${outcomeBar(t.resolution, t.count)}<div class="outcome-note">решено ${pct(t.resolved_rate)} · передано ${pct(t.escalated_rate)}</div></td>
      <td class="weak-cell">${weakCell(t)}</td>
    </tr>`;
  }
  return `
  <section class="panel" aria-label="Темы обращений">
    <div class="panel-title"><div><h2>Темы обращений <span class="count-badge">${d.topics.length}</span></h2>
      <p>Нажмите на тему, чтобы раскрыть подтемы. <strong>Слабое место</strong> — критерий с самым низким баллом по этой причине.</p></div>
      <span class="badge">Справочник v${esc(d.taxonomy.version)}</span></div>
    <div class="outcome-legend">${OUTCOMES.slice(0, 4).map(([k, l]) => `<span><b class="oc-${k}"></b>${l}</span>`).join("")}</div>
    <div class="table-scroll"><table class="dialog-table">
      <caption class="sr-only">Причины обращений: доля, число, средний балл, исходы, слабое место</caption>
      <thead><tr><th scope="col">Причина</th><th scope="col">Доля</th><th scope="col" class="align-right">Обращений</th>
        <th scope="col" class="align-right">Средний балл</th><th scope="col">Исход</th><th scope="col">Слабое место</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
  </section>`;
}

function findings(d) {
  const items = d.findings.slice(0, 8);
  const body = items.length ? items.map((f) => `
    <article class="finding">
      <span class="verdict negative gap">${Math.round(f.gap * 100)} п.п.</span>
      <div><h3>${esc(f.reason_title)}</h3>
        <p>«${esc(f.criterion_title)}»: ${pct(f.avg_ratio)} против ${pct(f.baseline)} в среднем по всем обращениям · ${dialogs(f.evaluated)}</p></div>
      <div class="finding-bars">
        <span>По этой причине</span><div><i class="bar-reason" style="width:${f.avg_ratio * 100}%"></i></div><b>${pct(f.avg_ratio)}</b>
        <span>В среднем</span><div><i class="bar-base" style="width:${f.baseline * 100}%"></i></div><b>${pct(f.baseline)}</b>
      </div>
      ${f.conversation_ids.length ? `<div class="dialog-links">${f.conversation_ids.slice(0, 8).map((id) =>
        `<a href="/#dialog=${encodeURIComponent(id)}" title="Открыть диалог">${esc(id)}</a>`).join("")}</div>` : ""}
    </article>`).join("")
    : `<div class="empty-state"><span class="empty-icon">${icon("check", 22)}</span><h3>Явных провалов нет</h3>
       <p>Ни по одной причине критерии не проседают на 15+ п.п. ниже среднего (при 3 и более диалогах).</p></div>`;
  return `
  <section class="panel" aria-label="Где проседает качество">
    <div class="panel-title"><div><h2>Где проседает качество <span class="count-badge">${d.findings.length}</span></h2>
      <p>Критерий по причине на <strong>15+ п.п.</strong> ниже среднего. Ссылки ведут к диалогам с провалом — материал для разбора с операторами.</p></div></div>
    ${body}
  </section>`;
}

function heatClass(v, flag) {
  if (v == null) return "heat-na";
  if (flag) return v > 0 ? "heat-lo" : "heat-hi";
  return v >= 0.85 ? "heat-hi" : v >= 0.6 ? "heat-mid" : "heat-lo";
}

function matrix(d) {
  const m = d.matrix;
  const head = m.criteria.map((c) => `<th scope="col" class="crit">${esc(c.title)}${c.is_critical_flag ? "<br><small>доля нарушений</small>" : ""}</th>`).join("");
  const rows = m.rows.map((r) => `<tr><td><span class="reason-name"><strong>${esc(r.title)}</strong><small>${dialogs(r.count)}</small></span></td>${
    m.criteria.map((c) => {
      const cell = r.cells[c.criterion_id] || {};
      return `<td class="heat ${heatClass(cell.avg, c.is_critical_flag)}">${cell.avg == null ? "N/A" : pct(cell.avg)}<small>${cell.evaluated ?? 0} из ${r.count}</small></td>`;
    }).join("")}</tr>`).join("");
  return `
  <section class="panel" aria-label="Причина и критерий">
    <div class="panel-title"><div><h2>Причина × критерий</h2>
      <p>Средняя доля баллов по критерию. Зелёный — от 85%, жёлтый — от 60%, красный — ниже. N/A — критерий не применялся.</p></div></div>
    <div class="queue-tabs" role="group" aria-label="Уровень">
      <button data-level="topic" aria-pressed="${state.level === "topic"}" class="${state.level === "topic" ? "active" : ""}"><span>Темы</span></button>
      <button data-level="subtopic" aria-pressed="${state.level === "subtopic"}" class="${state.level === "subtopic" ? "active" : ""}"><span>Подтемы</span></button>
    </div>
    <div class="table-scroll"><table class="dialog-table heat-table"><thead><tr><th scope="col">Причина</th>${head}</tr></thead><tbody>${rows}</tbody></table></div>
  </section>`;
}

function secondary(d) {
  const max = Math.max(1, ...d.secondary.map((s) => s.count));
  const body = d.secondary.length
    ? `<ul class="bar-list">${d.secondary.slice(0, 10).map((s) => `<li><span>${esc(s.title)}</span><b>${s.count}</b><div><i style="width:${s.count / max * 100}%"></i></div></li>`).join("")}</ul>`
    : `<p style="padding:0 24px 24px;color:var(--muted)">Клиенты не задавали дополнительных вопросов.</p>`;
  return `
  <section class="panel" aria-label="Дополнительные вопросы">
    <div class="panel-title"><div><h2>Попутные вопросы</h2><p>О чём ещё спрашивают в тех же диалогах, помимо основной причины.</p></div></div>
    ${body}
  </section>`;
}

function other(d) {
  const o = d.other;
  const disc = state.discovery;
  const busy = state.busy === "discover";
  const list = o.count ? `<ul class="other-list">${o.items.slice(0, 6).map((i) => `<li>${esc(i.description || "без описания")}<small>${esc(i.conversation_id)}</small></li>`).join("")}</ul>` : "";
  let result = "";
  if (disc) {
    result = disc.proposals.length ? disc.proposals.map((p) => p.kind === "new_subtopic" ? `
      <div class="proposal"><header><h3>${esc(p.title)}</h3><span class="badge">${p.count} ${plural(p.count, "обращение", "обращения", "обращений")}</span></header>
        <p>Новая подтема <code>${esc(p.code)}</code> в теме «${esc(p.topic_title)}»${p.new_topic ? " (новая тема)" : ""}${p.description ? ` — ${esc(p.description)}` : ""}</p>
        <ul>${p.examples.slice(0, 3).map((e) => `<li>${esc(e.description)}</li>`).join("")}</ul></div>` : `
      <div class="proposal"><header><h3>Подходит под «${esc(p.title)}»</h3><span class="badge amber">${p.count}</span></header>
        <p>Эти обращения есть в справочнике — классификатор отнёс их в «Прочее» по ошибке.</p></div>`).join("")
      : `<p style="padding:0 24px 16px;color:var(--muted)">${esc(disc.error || "Групп похожих обращений не нашлось.")}</p>`;
    if (disc.method && disc.method !== "none") {
      result = `<p style="padding:0 24px 10px;font-size:11px;color:var(--muted)">Способ: ${disc.method === "llm" ? "модель" : "по общим словам"}${disc.cost_usd ? ` · $${disc.cost_usd.toFixed(3)}` : ""} · не сгруппировано: ${disc.unassigned}</p>` + result;
    }
  }
  const canSave = disc?.proposed_topics;
  return `
  <section class="panel" aria-label="Новые темы">
    <div class="panel-title"><div><h2>Новые темы из «Прочего» <span class="count-badge">${o.count}</span></h2>
      <p>Обращения без подходящей подтемы. Когда похожих набирается несколько, система предлагает новую подтему.</p></div></div>
    ${list}
    ${result}
    <div class="panel-actions">
      <button class="button ${canSave ? "secondary" : "primary"}" data-action="discover" ${o.count < 2 || busy ? "disabled" : ""}>
        ${icon(busy ? "loader-circle" : "sparkles", 17, busy ? "spin" : "")}${busy ? "Ищем похожие обращения…" : "Предложить подтемы"}</button>
      ${canSave ? `<button class="button primary" data-action="save-taxonomy" ${state.busy ? "disabled" : ""}>${icon("save", 17)}Сохранить новую версию справочника</button>` : ""}
      <span class="form-hint">${o.count < 2 ? "Нужно хотя бы 2 обращения в «Прочем»." : "Справочник не меняется, пока вы не сохраните версию."}</span>
    </div>
  </section>`;
}

function taxonomyPanel() {
  const t = state.taxonomy;
  if (!t) return "";
  const options = state.versions.map((v) => `<option value="${v.id}" ${v.active ? "selected" : ""}>v${v.version} · ${v.codes_count} подтем${v.active ? " · активная" : ""}</option>`).join("");
  return `
  <section class="panel" id="taxonomy" aria-label="Справочник причин">
    <div class="panel-title"><div><h2>Справочник причин <span class="count-badge">v${esc(t.version)}</span></h2>
      <p>По этой версии модель классифицирует новые оценки. Изменение справочника — новая версия; прежние оценки остаются со своей.</p></div>
      <label class="checklist-picker">${icon("list-filter", 16)}<span class="sr-only">Версия справочника</span>
        <select data-action="activate">${options}</select></label></div>
    <div class="taxonomy">${t.topics.map((tp) => `
      <section><h3><span>${esc(tp.title)}</span><code>${esc(tp.code)}</code></h3>
        <ul>${tp.subtopics.map((s) => `<li title="${esc(s.description || "")}"><span>${esc(s.title)}</span><code>${esc(s.code)}</code></li>`).join("")}</ul></section>`).join("")}
    </div>
  </section>`;
}

function renderMain() {
  const main = document.getElementById("main-content");
  const d = state.data;
  if (state.error) {
    main.innerHTML = heading() + `<div class="notice">${icon("triangle-alert", 20)}<div><strong>Не удалось загрузить данные</strong><p>${esc(state.error)}</p></div></div>`;
    return;
  }
  if (!d) {
    main.innerHTML = `<div class="empty-state">${icon("loader-circle", 24, "spin")}<h2>Собираем статистику обращений</h2></div>`;
    return;
  }
  const notice = d.unclassified ? `<div class="notice">${icon("triangle-alert", 20)}<div><strong>${dialogs(d.unclassified)} без причины</strong>
    <p>Эти оценки сделаны до появления справочника причин. Переоцените диалоги («Пересчитать AI» в карточке или «Проверить оценки»), чтобы они попали в статистику.</p></div></div>` : "";
  if (!d.classified) {
    main.innerHTML = heading() + filters() + notice + `<section class="panel"><div class="empty-state"><span class="empty-icon">${icon("chart-pie", 24)}</span>
      <h3>Пока нечего показать</h3><p>Причина определяется при оценке диалога. Оцените диалоги — статистика появится здесь.</p>
      <a class="button primary" href="/#dialogs" style="text-decoration:none">К диалогам ${icon("arrow-right", 17)}</a></div></section>` + taxonomyPanel();
    return;
  }
  main.innerHTML = heading() + filters() + stats(d) + notice + topicsTable(d) + findings(d) + matrix(d)
    + `<div class="reasons-split">${secondary(d)}${other(d)}</div>` + taxonomyPanel();
}

// --------------------------------------------------------------------------- действия
async function refresh() {
  try {
    await loadAnalytics();
    state.error = "";
  } catch (e) {
    state.error = e.message;
  }
  renderMain();
}

function bind() {
  const main = document.getElementById("main-content");
  main.addEventListener("change", async (ev) => {
    const el = ev.target;
    if (el.dataset.filter) {
      state.filters[el.dataset.filter] = el.value;
      state.discovery = null;
      await refresh();
    } else if (el.dataset.action === "activate") {
      try {
        await api(`/api/v1/reasons/versions/${el.value}/activate`, {});
        [state.taxonomy, state.versions] = await Promise.all([api("/api/v1/reasons/taxonomy"), api("/api/v1/reasons/versions")]);
        toast(`Активна версия справочника v${state.taxonomy.version}. Новые оценки будут классифицироваться по ней.`);
        renderMain();
      } catch (e) { toast(e.message, true); }
    }
  });
  main.addEventListener("click", async (ev) => {
    const t = ev.target.closest("[data-toggle],[data-level],[data-action]");
    if (!t || t.tagName === "SELECT") return;
    if (t.dataset.toggle) {
      state.expanded.has(t.dataset.toggle) ? state.expanded.delete(t.dataset.toggle) : state.expanded.add(t.dataset.toggle);
      renderMain();
    } else if (t.dataset.level && t.dataset.level !== state.level) {
      state.level = t.dataset.level;
      await refresh();
    } else if (t.dataset.action === "discover") {
      state.busy = "discover";
      renderMain();
      try {
        state.discovery = await api("/api/v1/reasons/discover", { dataset: state.filters.dataset || null, min_cluster: 2, use_llm: true });
        const n = state.discovery.proposals.filter((p) => p.kind === "new_subtopic").length;
        toast(n ? `Предложено новых подтем: ${n}. Проверьте и сохраните версию.` : "Новых подтем не найдено.");
      } catch (e) { toast(e.message, true); }
      state.busy = "";
      renderMain();
    } else if (t.dataset.action === "save-taxonomy") {
      state.busy = "save";
      renderMain();
      try {
        const v = await api("/api/v1/reasons/versions", { topics: state.discovery.proposed_topics, note: "Новые подтемы из «Прочего»", activate: true });
        [state.taxonomy, state.versions] = await Promise.all([api("/api/v1/reasons/taxonomy"), api("/api/v1/reasons/versions")]);
        state.discovery = null;
        toast(`Сохранена версия справочника v${v.version}. Переоцените диалоги из «Прочего», чтобы они получили новые подтемы.`);
      } catch (e) { toast(e.message, true); }
      state.busy = "";
      renderMain();
    }
  });
}

// --------------------------------------------------------------------------- запуск
(async () => {
  await loadAppStyles();
  renderShell();
  renderMain();
  bind();
  try {
    await loadAll();
  } catch (e) {
    state.error = e.message;
  }
  renderMain();
})();
