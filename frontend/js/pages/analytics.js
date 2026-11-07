// Раздел «Аналитика»: средний итог, распределение, слабые критерии, разрезы, грубые нарушения, апелляции, стоимость.
import { API } from "../api.js";
import { AGREEMENT, CHANNELS, esc, fmtMoney, fmtNum, fmtPct, fmtScore, scoreChip, scoreTone } from "../utils.js";

const filters = { dataset: "", channel: "", language: "" };

export async function render(view) {
  view.innerHTML = `
    <div class="cqa-page-head">
      <div><h1>Аналитика</h1><p>По последней оценке каждого диалога (с учётом решений по апелляциям).</p></div>
      <div class="cqa-row" data-filters>
        <select class="cqa-select" data-f="dataset"><option value="">Все наборы</option></select>
        <select class="cqa-select" data-f="channel"><option value="">Все каналы</option><option value="chat">Чат</option><option value="voice">Звонок</option></select>
        <select class="cqa-select" data-f="language"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select>
      </div>
    </div>
    <div data-body></div>`;
  const list = await API.conversations();
  const ds = view.querySelector("[data-f=dataset]");
  ds.innerHTML += list.datasets.map((d) => `<option>${esc(d)}</option>`).join("");
  view.querySelectorAll("[data-f]").forEach((s) => {
    s.value = filters[s.dataset.f];
    s.addEventListener("change", () => { filters[s.dataset.f] = s.value; draw(view); });
  });
  await draw(view);
}

async function draw(view) {
  const d = await API.analytics(filters);
  const body = view.querySelector("[data-body]");
  if (!d.ok) {
    body.innerHTML = `<div class="cqa-panel"><div class="cqa-empty"><strong>Нет оценённых диалогов</strong>Запустите оценку в разделе «Диалоги».</div></div>`;
    return;
  }
  const maxBucket = Math.max(1, ...d.distribution.map((b) => b.count));
  const agreeTotal = Object.values(d.agreement).reduce((a, b) => a + b, 0) || 1;
  body.innerHTML = `
    <div class="cqa-kpis">
      ${kpi("Оценено диалогов", `${d.ok}`, `из ${d.conversations_total} · ошибок ${d.errors}`)}
      ${kpi("Средний итог", fmtScore(d.avg_score), `медиана ${fmtScore(d.median_score)}`)}
      ${kpi("Грубые нарушения", `${d.critical_count}`, d.ok ? fmtPct(d.critical_count / d.ok) + " диалогов" : "")}
      ${kpi("Требуют проверки", `${d.needs_review_count}`, d.ok ? fmtPct(d.needs_review_count / d.ok) + " — нагрузка на людей" : "")}
      ${kpi("Апелляции", `${d.appeals.total}`, `открыто ${d.appeals.open} · изменено ${d.appeals.changed}`)}
      ${kpi("Стоимость оценки", fmtMoney(d.cost.avg_per_evaluation_usd), `всего ${fmtMoney(d.cost.total_usd)} · ${fmtNum(d.avg_duration_sec, 1)} с/диалог`)}
    </div>
    <div class="cqa-grid cqa-grid--2 cqa-mt">
      <section class="cqa-panel"><div class="cqa-panel__head"><h2>Распределение итогов</h2></div>
        <div class="cqa-panel__body"><div class="cqa-bars">${d.distribution.map((b) => `<div class="cqa-bars__col">
          <span class="cqa-small">${b.count}</span><div class="cqa-bars__bar" style="height:${(b.count / maxBucket) * 100}%"></div>
          <span class="cqa-bars__label">${esc(b.label)}</span></div>`).join("")}</div></div></section>
      <section class="cqa-panel"><div class="cqa-panel__head"><h2>Источник финальных вердиктов</h2></div>
        <div class="cqa-panel__body cqa-stack">${Object.entries(d.agreement).sort((a, b) => b[1] - a[1]).map(([k, v]) => `
          <div class="cqa-row"><span style="width:170px">${esc(AGREEMENT[k] || k)}</span>
          <div class="cqa-hbar" style="flex:1"><div style="width:${(v / agreeTotal) * 100}%;background:var(--cqa-accent)"></div></div>
          <span class="cqa-small" style="width:70px;text-align:right">${v} · ${fmtPct(v / agreeTotal)}</span></div>`).join("")}
          <div class="cqa-faint">Модели: ${Object.entries(d.models).map(([m, n]) => `${esc(m)} (${n})`).join("; ")}</div></div></section>
    </div>
    <section class="cqa-panel cqa-mt"><div class="cqa-panel__head"><h2>Критерии: где теряются баллы</h2>
      <span class="cqa-faint">override — доля исправлений облаком (где слаба локальная модель)</span></div>
      <div class="cqa-table-wrap"><table class="cqa-table"><thead><tr><th>Критерий</th><th class="cqa-num">Вес</th><th>Средний % от веса</th>
        <th class="cqa-num">«Нет»</th><th class="cqa-num">N/A</th><th class="cqa-num">override</th><th class="cqa-num">На проверку</th><th class="cqa-num">Апелляций</th></tr></thead>
      <tbody>${d.criteria.map((c) => `<tr><td>${esc(c.title)} <span class="cqa-faint cqa-id">${esc(c.criterion_id)}</span></td>
        <td class="cqa-num">${c.is_critical_flag ? "флаг" : fmtNum(c.weight)}</td>
        <td>${c.avg_ratio === null ? (c.is_critical_flag ? `<span class="cqa-small">нарушений: ${fmtPct(c.share_yes)}</span>` : "—") : `<div class="cqa-row" style="flex-wrap:nowrap"><div class="cqa-hbar" style="flex:1"><div style="width:${c.avg_ratio * 100}%;background:var(--cqa-${scoreTone(c.avg_ratio * 100)})"></div></div><span class="cqa-small" style="width:40px">${fmtPct(c.avg_ratio)}</span></div>`}</td>
        <td class="cqa-num">${c.is_critical_flag ? "—" : fmtPct(c.share_no)}</td><td class="cqa-num">${fmtPct(c.share_na)}</td>
        <td class="cqa-num">${fmtPct(c.override_rate)}</td><td class="cqa-num">${fmtPct(c.needs_review_rate)}</td><td class="cqa-num">${c.appealed}</td></tr>`).join("")}</tbody></table></div></section>
    <div class="cqa-grid cqa-grid--2 cqa-mt">
      ${groupTable("По языкам", d.by_language, (k) => k)}
      ${groupTable("По каналам", d.by_channel, (k) => CHANNELS[k] || k)}
    </div>
    <section class="cqa-panel cqa-mt"><div class="cqa-panel__head"><h2>Грубые нарушения</h2></div>
      <div class="cqa-panel__body">${d.criticals.length ? `<div class="cqa-stack">${d.criticals.map((c) => `<div>
        <a class="cqa-id" href="#/conversation/${encodeURIComponent(c.conversation_id)}">${esc(c.conversation_id)}</a>
        <div class="cqa-quote">«${esc(c.quote)}»</div><div class="cqa-small cqa-muted">${esc(c.rationale)}</div></div>`).join("")}</div>`
        : '<span class="cqa-muted">Нарушений не выявлено.</span>'}</div></section>`;
}

function kpi(label, value, hint) {
  return `<div class="cqa-kpi"><div class="cqa-kpi__label">${esc(label)}</div><div class="cqa-kpi__value">${esc(value)}</div><div class="cqa-kpi__hint">${esc(hint)}</div></div>`;
}

function groupTable(title, rows, name) {
  return `<section class="cqa-panel"><div class="cqa-panel__head"><h2>${esc(title)}</h2></div>
    <div class="cqa-table-wrap"><table class="cqa-table"><thead><tr><th></th><th class="cqa-num">Диалогов</th><th>Средний итог</th>
    <th class="cqa-num">Нарушений</th><th class="cqa-num">На проверку</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${esc(name(r.key))}</td><td class="cqa-num">${r.count}</td><td>${scoreChip(r.avg_score)}</td>
      <td class="cqa-num">${r.critical}</td><td class="cqa-num">${r.needs_review}</td></tr>`).join("")}</tbody></table></div></section>`;
}
