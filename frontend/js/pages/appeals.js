// Раздел «Апелляции»: очередь проверяющего (спорная оценка, комментарий, фрагмент диалога) и история решений.
import { API } from "../api.js";
import { toast } from "../components/ui.js";
import { CHANNELS, SPEAKERS, esc, fmtDate, fmtScore, linkifyTurns, parseValue, valueChip, valueOption } from "../utils.js";

let tab = "open";

export async function render(view, params) {
  if (params.tab) tab = params.tab;
  view.innerHTML = `
    <div class="cqa-page-head">
      <div><h1>Апелляции</h1><p>Проверяющий видит спорную оценку, комментарий и фрагмент диалога, подтверждает или меняет оценку с причиной.</p></div>
      <div class="cqa-segment" data-tabs>
        <button data-tab="open" class="${tab === "open" ? "is-active" : ""}">Открытые</button>
        <button data-tab="resolved" class="${tab === "resolved" ? "is-active" : ""}">История решений</button>
      </div>
    </div>
    <div data-list class="cqa-stack"></div>`;
  view.querySelector("[data-tabs]").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (!b) return;
    tab = b.dataset.tab;
    render(view, {});
  });
  await load(view);
}

async function load(view) {
  const list = view.querySelector("[data-list]");
  const items = await API.appeals(tab);
  if (!items.length) {
    list.innerHTML = `<div class="cqa-panel"><div class="cqa-empty"><strong>${tab === "open" ? "Открытых апелляций нет" : "Решений пока нет"}</strong>
      Апелляция подаётся из карточки диалога кнопкой «Оспорить оценку» у критерия.</div></div>`;
    return;
  }
  list.innerHTML = items.map((a) => (tab === "open" ? openItem(a) : resolvedItem(a))).join("");
  list.querySelectorAll("form[data-appeal]").forEach((form) => bindForm(view, form, items.find((a) => a.id === Number(form.dataset.appeal))));
}

function fragment(a) {
  if (!a.fragment || !a.fragment.length) return "";
  const evidence = new Set((a.criterion && a.criterion.evidence_turns) || []);
  return `<div class="cqa-panel" style="box-shadow:none"><div class="cqa-transcript__list" style="max-height:260px">
    ${a.fragment.map((t) => `<div class="cqa-turn cqa-turn--${t.speaker}${evidence.has(t.idx) ? " is-evidence" : ""}">
      <div class="cqa-turn__idx">${t.idx}</div><div><div class="cqa-turn__meta"><span class="cqa-turn__who">${esc(SPEAKERS[t.speaker])}</span><span>${esc(t.ts_raw.length > 8 ? (t.ts_raw.match(/T(\d{2}:\d{2}:\d{2})/) || [])[1] || t.ts_raw : t.ts_raw)}</span></div>
      <div class="cqa-turn__text">${esc(t.text)}</div></div></div>`).join("")}
  </div></div>`;
}

function header(a) {
  const c = a.criterion || {};
  return `<div class="cqa-panel__head">
    <div><h2>${esc(c.title || a.criterion_id)} ${valueChip(c, a.disputed_value)}</h2>
      <div class="cqa-faint">Диалог <a class="cqa-id" href="#/conversation/${encodeURIComponent(a.conversation_id)}">${esc(a.conversation_id)}</a>
        · ${esc(CHANNELS[a.channel] || a.channel || "")} ${esc(a.language || "")} · подана ${esc(fmtDate(a.created_at))} · ${esc(a.author)}</div></div>
    <div class="cqa-small">Итог сейчас: <strong>${fmtScore(a.evaluation && a.evaluation.final_score)}</strong></div>
  </div>`;
}

function openItem(a) {
  const c = a.criterion || {};
  const n = 9999;
  return `<section class="cqa-panel">
    ${header(a)}
    <div class="cqa-panel__body cqa-grid cqa-grid--2">
      <div class="cqa-stack">
        <div><div class="cqa-faint">Обоснование ИИ</div>${linkifyTurns(c.rationale || "", n)}</div>
        ${c.quote ? `<div class="cqa-quote">«${esc(c.quote)}»</div>` : ""}
        <div class="cqa-review-box"><div class="cqa-faint">Комментарий апелляции</div>${esc(a.comment)}</div>
        ${fragment(a)}
        <a class="cqa-small" href="#/conversation/${encodeURIComponent(a.conversation_id)}?turn=${(c.evidence_turns || [])[0] || ""}">Открыть диалог целиком →</a>
      </div>
      <form class="cqa-stack" data-appeal="${a.id}">
        <div class="cqa-segment" data-action>
          <button type="button" data-v="confirm" class="is-active">Подтвердить оценку</button>
          <button type="button" data-v="change">Изменить оценку</button>
        </div>
        <label class="cqa-field" data-new-value hidden><span>Новое значение</span>
          <select class="cqa-select">${(c.allowed_values || []).filter((v) => v !== a.disputed_value).map((v) => valueOption(c, v)).join("")}</select></label>
        <label class="cqa-field"><span>Причина решения (обязательно)</span><textarea class="cqa-textarea" rows="4" data-reason required></textarea></label>
        <label class="cqa-field"><span>Проверяющий</span><input class="cqa-input" data-reviewer value="Проверяющий ОКК"></label>
        <div><button class="cqa-btn cqa-btn--primary" type="submit">Сохранить решение</button></div>
      </form>
    </div>
  </section>`;
}

function resolvedItem(a) {
  const c = a.criterion || {};
  return `<section class="cqa-panel">
    ${header(a)}
    <div class="cqa-panel__body cqa-stack">
      <div>Исходная оценка ИИ: ${valueChip(c, a.disputed_value)} → решение: ${a.action === "confirm" ? "<strong>подтверждена</strong>" : valueChip(c, a.new_value)}
        · итог ${fmtScore(a.score_before)} → <strong>${fmtScore(a.score_after)}</strong></div>
      <div class="cqa-review-box"><div class="cqa-faint">Комментарий апелляции</div>${esc(a.comment)}</div>
      <div><strong>Причина решения:</strong> ${esc(a.reason)} <span class="cqa-faint">· ${esc(a.reviewer)} · ${esc(fmtDate(a.decided_at))}</span></div>
    </div>
  </section>`;
}

function bindForm(view, form, a) {
  let action = "confirm";
  form.querySelector("[data-action]").addEventListener("click", (e) => {
    const b = e.target.closest("[data-v]");
    if (!b) return;
    action = b.dataset.v;
    form.querySelectorAll("[data-action] button").forEach((x) => x.classList.toggle("is-active", x === b));
    form.querySelector("[data-new-value]").hidden = action !== "change";
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const reason = form.querySelector("[data-reason]").value.trim();
    if (!reason) return toast("Укажите причину решения", "error");
    const body = { action, reason, reviewer: form.querySelector("[data-reviewer]").value.trim() || "Проверяющий ОКК" };
    if (action === "change") body.new_value = parseValue(form.querySelector("[data-new-value] select").value);
    try {
      const r = await API.decideAppeal(a.id, body);
      toast(`Решение сохранено. Итог: ${fmtScore(a.evaluation && a.evaluation.final_score)} → ${fmtScore(r.evaluation.final_score)}`, "ok", 6000);
      window.dispatchEvent(new Event("cqa:refresh-header"));
      await load(view);
    } catch (err) {
      toast(err.message, "error", 7000);
    }
  });
}
