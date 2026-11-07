// Карточка диалога: расшифровка + оценка. Ссылки на реплики прокручивают расшифровку и перематывают запись.
import { API } from "../api.js";
import { modal, progressBar, scoreRing, toast } from "../components/ui.js";
import {
  CHANNELS, CONFIDENCE, EVIDENCE, ICONS, SPEAKERS, esc, fmtClock, fmtDate, fmtMoney, fmtNum,
  fmtScore, linkifyTurns, scoreTone, turnLink, valueChip, valueLabel,
} from "../utils.js";

let ctx = null;
let pollTimer = null;

export async function render(view, params) {
  const id = params.id;
  const evaluationId = params.evaluation ? Number(params.evaluation) : undefined;
  const [data, checklists] = await Promise.all([API.conversation(id, evaluationId), API.checklists()]);
  const versions = checklists.flatMap((cl) => cl.versions.map((v) => ({ ...v, label: `${cl.name} · v${v.version}` })));
  ctx = { id, view, data, versions, conv: data.conversation, ev: data.evaluation };
  view.addEventListener("click", onClick);
  draw();
  if (params.turn) setTimeout(() => goToTurn(Number(params.turn)), 100);
}

export function destroy() {
  clearInterval(pollTimer);
  if (ctx) ctx.view.removeEventListener("click", onClick);
  ctx = null;
}

function draw() {
  const { view, conv, ev, data } = ctx;
  const n = conv.turns.length;
  const hist = data.history.map((h) => `<option value="${h.id}" ${ev && ev.id === h.id ? "selected" : ""}>
      #${h.id} · ${esc(fmtDate(h.created_at))} · ${esc(h.model_name || "")} · ${h.status === "ok" ? fmtScore(h.final_score) : "ошибка"}</option>`).join("");
  const curVersion = ev ? ev.checklist_version_id : (ctx.versions[0] || {}).id;
  const verOpts = ctx.versions.map((v) => `<option value="${v.id}" ${v.id === curVersion ? "selected" : ""}>${esc(v.label)}</option>`).join("");

  view.innerHTML = `
  <div class="cqa-page-head">
    <div>
      <a href="#/conversations" class="cqa-small">${ICONS.back.replace("<svg", '<svg width="12" height="12"')} Диалоги</a>
      <h1 class="cqa-id" style="font-size:20px">${esc(conv.conversation_id)}</h1>
      <p>${esc(CHANNELS[conv.channel] || conv.channel)} · ${esc(conv.language)} · ${n} реплик · набор ${esc(conv.dataset)}</p>
    </div>
    <div class="cqa-row">
      ${hist ? `<label class="cqa-field"><span>Оценка</span><select class="cqa-select" data-history>${hist}</select></label>` : ""}
      <label class="cqa-field"><span>Чек-лист</span><select class="cqa-select" data-version>${verOpts}</select></label>
      <button class="cqa-btn cqa-btn--primary" data-reeval style="align-self:flex-end">${ICONS.refresh} ${ev ? "Переоценить" : "Оценить"}</button>
    </div>
  </div>
  <div data-job></div>
  <div class="cqa-card-layout">
    <section class="cqa-panel cqa-transcript">
      ${conv.has_audio ? player() : ""}
      <div class="cqa-panel__head"><h2>Расшифровка</h2>
        <span class="cqa-faint">Номера реплик — как в исходном массиве (с 1)</span></div>
      <div class="cqa-transcript__list" data-transcript>${conv.turns.map(turnHtml).join("")}</div>
    </section>
    <section class="cqa-stack" data-eval>${ev ? evaluationHtml(ev, n) : noEvaluation()}</section>
  </div>`;
  bind();
}

function player() {
  return `<div class="cqa-player">
    <div class="cqa-row cqa-row--between"><strong>Запись звонка</strong>
      <a class="cqa-btn cqa-btn--sm" href="${esc(API.audioPageUrl(ctx.id))}" target="_blank" rel="noopener">${ICONS.external} Открыть запись в новой вкладке</a></div>
    <audio controls preload="none" data-audio src="${esc(API.audioUrl(ctx.id))}"></audio>
    <div class="cqa-faint">Кнопка ▶ у реплики перематывает запись к её началу (паузы записи учтены).</div>
  </div>`;
}

function turnHtml(t) {
  const isVoice = ctx.conv.channel === "voice";
  let ts = esc(t.ts_raw);
  if (isVoice) {
    const clock = (t.ts_raw.match(/T(\d{2}:\d{2}:\d{2})/) || [])[1] || t.ts_raw;
    ts = `<button class="cqa-ts-btn" data-seek="${t.audio_offset_sec ?? ""}" title="Исходная метка: ${esc(t.ts_raw)}">▶ ${esc(fmtClock(t.audio_offset_sec))}</button> <span title="${esc(t.ts_raw)}">${esc(clock)}</span>`;
  }
  return `<div class="cqa-turn cqa-turn--${t.speaker}" id="cqa-turn-${t.idx}" data-idx="${t.idx}">
    <div class="cqa-turn__idx">${t.idx}</div>
    <div><div class="cqa-turn__meta"><span class="cqa-turn__who">${esc(SPEAKERS[t.speaker] || t.speaker)}</span><span>${ts}</span></div>
    <div class="cqa-turn__text">${esc(t.text)}</div></div></div>`;
}

function noEvaluation() {
  return `<div class="cqa-panel"><div class="cqa-empty"><strong>Диалог ещё не оценён</strong>
    Выберите версию чек-листа и нажмите «Оценить». Режим модели задаётся в разделе «Настройки модели».</div></div>`;
}

function evaluationHtml(ev, n) {
  if (ev.status !== "ok") {
    return `<div class="cqa-banner cqa-banner--bad">${ICONS.alert}<div><strong>Оценка не выполнена (status: error)</strong><br>${esc(ev.error || "")}
      <div class="cqa-faint">Ошибка не подменяется нулём. Проверьте подключение моделей и нажмите «Переоценить».</div></div></div>
      ${auditHtml(ev)}`;
  }
  const crits = ev.criteria;
  const critical = crits.find((c) => c.is_critical_flag && c.value === true);
  const reviewCount = crits.filter((c) => c.needs_review).length;
  const sections = (ev.sections || []).filter((s) => !s.is_flag_section);
  const changed = ev.ai_score !== ev.final_score;
  return `
    <div class="cqa-panel"><div class="cqa-panel__body">
      <div class="cqa-summary">
        ${scoreRing(ev.final_score, changed ? "финальный итог" : "итог из 100")}
        <div class="cqa-stack" style="gap:8px">
          ${changed ? `<div class="cqa-small">Исходный итог ИИ: <strong>${fmtScore(ev.ai_score)}</strong> → после апелляций: <strong>${fmtScore(ev.final_score)}</strong></div>` : ""}
          <div class="cqa-sections">${sections.map(sectionBar).join("")}</div>
          <div class="cqa-faint">${esc(ev.model_name)} · ${esc(ev.checklist_label)} · ${fmtMoney(ev.cost_usd)} · ${fmtNum(ev.duration_ms / 1000, 1)} с</div>
        </div>
      </div>
    </div></div>
    ${critical ? `<div class="cqa-banner cqa-banner--bad">${ICONS.flag}<div><strong>Грубое нарушение</strong> — итог не обнуляется, флаг передаётся отдельно.
        <div class="cqa-quote">«${esc(critical.quote || "")}»</div>
        <div class="cqa-small">${linkifyTurns(critical.rationale, n)} ${critical.evidence_turns.map(turnLink).join("")}</div></div></div>` : ""}
    ${reviewCount ? `<div class="cqa-banner cqa-banner--mid">${ICONS.alert}<div><strong>Требует проверки человеком: ${reviewCount}</strong>
        — модели не уверены, разошлись или доказательство не подтверждено текстом. На баллы не влияет.</div></div>` : ""}
    ${lostHtml(crits, n)}
    <div>${crits.map((c) => criterionHtml(c, n)).join("")}</div>
    ${auditHtml(ev)}`;
}

function sectionBar(s) {
  const pct = s.score ?? 0;
  return `<div class="cqa-section-bar"><span>${esc(s.section)}</span>
    <strong style="text-align:right">${s.score === null ? "N/A" : fmtScore(s.score)}</strong>
    ${progressBar(pct, scoreTone(s.score))}</div>`;
}

function lostHtml(crits, n) {
  const lost = crits.filter((c) => !c.is_critical_flag && c.value !== null && c.points !== null && c.max_points && c.points < c.max_points)
    .map((c) => ({ c, delta: c.max_points - c.points })).sort((a, b) => b.delta - a.delta);
  const na = crits.filter((c) => !c.is_critical_flag && c.value === null);
  const items = lost.map(({ c, delta }) => `<div class="cqa-lost__item"><span class="cqa-lost__pts">−${fmtScore(delta)}</span>
      <div><a href="#" data-crit-link="${esc(c.criterion_id)}"><strong>${esc(c.title)}</strong></a> · ${esc(valueLabel(c, c.value))}<br>
      <span class="cqa-muted">${linkifyTurns(c.rationale, n)}</span></div></div>`).join("");
  return `<div class="cqa-panel"><div class="cqa-panel__head"><h2>Почему снижена оценка</h2></div><div class="cqa-panel__body cqa-lost">
    ${items || '<div class="cqa-muted">Оценка не снижалась: все применимые критерии выполнены полностью.</div>'}
    ${na.length ? `<div class="cqa-faint">N/A: ${na.map((c) => esc(c.title)).join(", ")} — вес перераспределён между применимыми критериями.</div>` : ""}
  </div></div>`;
}

function criterionHtml(c, n) {
  const isNA = c.value === null;
  let pts;
  if (c.is_critical_flag) pts = `<span class="cqa-faint">флаг, без баллов</span>`;
  else if (isNA) pts = `<span class="cqa-crit__pts"><small>N/A · вес ${fmtNum(c.weight)} перераспределён</small></span>`;
  else pts = `<span class="cqa-crit__pts" title="Эффективный вес после перераспределения N/A: ${c.effective_weight ?? "—"}">${fmtScore(c.points)} <small>/ ${fmtNum(c.max_points)}</small></span>`;
  const evNote = c.evidence_status && c.evidence_status !== "verified"
    ? `<span class="cqa-chip cqa-chip--${c.evidence_status === "corrected" || c.evidence_status === "default" ? "outline" : "mid"}" title="${esc(c.evidence_note || "")}">${esc(EVIDENCE[c.evidence_status] || c.evidence_status)}</span>` : "";
  const missed = !isNA && (c.value === false || (typeof c.value === "number" && c.points < c.max_points)) && !c.is_critical_flag;
  const review = c.review;
  const appealOpen = c.appeal_status === "open";
  return `<article class="cqa-crit${c.needs_review ? " is-review" : ""}${c.is_critical_flag && c.value === true ? " is-critical-hit" : ""}" id="cqa-crit-${esc(c.criterion_id)}" data-crit="${esc(c.criterion_id)}">
    <div class="cqa-crit__head">
      <div><div class="cqa-crit__section">${esc(c.section)}</div><div class="cqa-crit__title">${esc(c.title)} <span class="cqa-faint cqa-id">${esc(c.criterion_id)}</span></div></div>
      <div class="cqa-crit__score">${valueChip(c, c.value)}${pts}</div>
    </div>
    <div class="cqa-crit__body">
      <div class="cqa-crit__meta">
        ${review && review.action === "change" ? '<span class="cqa-chip cqa-chip--info">решение проверяющего</span>' : ""}
        <span class="cqa-chip cqa-chip--${c.confidence === "high" ? "good" : c.confidence === "medium" ? "outline" : "mid"}">${esc(CONFIDENCE[c.confidence] || c.confidence)}</span>
        ${evNote}
        ${c.needs_review ? '<span class="cqa-chip cqa-chip--mid">требует проверки</span>' : ""}
        ${appealOpen ? '<span class="cqa-chip cqa-chip--info">апелляция на рассмотрении</span>' : ""}
      </div>
      ${isNA && !c.is_critical_flag ? `<div><span class="cqa-chip cqa-chip--na">N/A — причина</span> ${linkifyTurns(c.rationale, n)}</div>` : `<div>${linkifyTurns(c.rationale, n)}</div>`}
      <div class="cqa-small">${missed ? "Проверенный участок" : "Доказательство"}: ${c.evidence_turns.map(turnLink).join("") || "—"}</div>
      ${c.quote ? `<div class="cqa-quote">«${esc(c.quote)}»</div>` : ""}
      ${c.review_reasons && c.review_reasons.length ? `<ul class="cqa-small cqa-muted" style="margin:0;padding-left:18px">${c.review_reasons.map((r) => `<li>${linkifyTurns(r, n)}</li>`).join("")}</ul>` : ""}
      ${review ? `<div class="cqa-review-box">
          <div>Исходная оценка ИИ: ${valueChip(c, c.ai_value)} → решение проверяющего: ${valueChip(c, review.value)} → финал: ${valueChip(c, c.value)}</div>
          <div><strong>Причина:</strong> ${esc(review.reason)}</div>
          <div class="cqa-faint">${esc(review.reviewer)} · ${esc(fmtDate(review.decided_at))} · комментарий апелляции: «${esc(review.comment || "")}»</div></div>` : ""}
    </div>
    <div class="cqa-crit__foot">
      <button class="cqa-btn cqa-btn--sm" data-show="${esc(c.criterion_id)}">Перейти к фрагменту</button>
      <button class="cqa-btn cqa-btn--sm" data-appeal="${esc(c.criterion_id)}" ${appealOpen || review ? "disabled" : ""}>${review ? "Апелляция рассмотрена" : appealOpen ? "Апелляция подана" : "Оспорить оценку"}</button>
    </div>
  </article>`;
}

function auditHtml(ev) {
  const a = ev.audit || {};
  const stages = a.stages || {};
  const rows = Object.entries(stages).filter(([, s]) => s && s.model).map(([name, s]) => {
    const u = s.usage || {};
    return `<tr><td>${esc(name)}</td><td>${esc(s.provider)} · ${esc(s.model)}</td><td>${esc(s.prompt_version)}</td><td class="cqa-num">${s.attempts}</td>
      <td class="cqa-num">${fmtNum(u.input_tokens || 0, 0)}${u.cache_read_input_tokens ? ` (+${fmtNum(u.cache_read_input_tokens, 0)} кэш)` : ""}</td>
      <td class="cqa-num">${fmtNum(u.output_tokens || 0, 0)}</td><td class="cqa-num">${s.cost_usd === null ? "?" : fmtMoney(s.cost_usd)}</td>
      <td class="cqa-num">${fmtNum((s.duration_ms || 0) / 1000, 1)} с</td><td>${s.ok ? '<span class="cqa-chip cqa-chip--good">ok</span>' : `<span class="cqa-chip cqa-chip--mid" title="${esc((s.errors || []).join("\n"))}">ошибки</span>`}</td></tr>`;
  }).join("");
  const raws = Object.entries(stages).filter(([, s]) => s && s.raw_texts && s.raw_texts.length)
    .map(([name, s]) => `<details class="cqa-details"><summary>Сырой ответ: ${esc(name)} (${s.raw_texts.length})</summary>
      ${(s.errors || []).length ? `<div class="cqa-small" style="color:var(--cqa-bad)">${s.errors.map(esc).join("<br>")}</div>` : ""}
      <pre class="cqa-pre">${esc(s.raw_texts.join("\n\n----- повтор -----\n\n"))}</pre></details>`).join("");
  return `<details class="cqa-panel cqa-details" style="padding:12px 16px"><summary>Журнал оценки: модели, версии промптов, токены, стоимость</summary>
    <div class="cqa-small cqa-muted">Оценка #${ev.id} · ${esc(fmtDate(ev.created_at))} · промпты: ${esc(Object.values(a.prompt_versions || {}).join(", "))} · чек-лист ${esc((a.checklist_hash || "").slice(0, 10))}</div>
    ${rows ? `<div class="cqa-table-wrap cqa-mt"><table class="cqa-table"><thead><tr><th>Этап</th><th>Модель</th><th>Промпт</th><th class="cqa-num">Попыток</th><th class="cqa-num">Вход</th><th class="cqa-num">Выход</th><th class="cqa-num">Стоимость</th><th class="cqa-num">Время</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}
    <div class="cqa-stack cqa-mt">${raws}</div></details>`;
}

// ---------------------------------------------------------------- взаимодействия
function bind() {
  const { view } = ctx;
  const hist = view.querySelector("[data-history]");
  if (hist) hist.addEventListener("change", async () => {
    const data = await API.conversation(ctx.id, Number(hist.value));
    ctx.data = data; ctx.ev = data.evaluation;
    draw();
  });
  view.querySelector("[data-reeval]").addEventListener("click", reevaluate);
}

function onClick(e) {
  const link = e.target.closest("[data-turn]");
  if (link) { e.preventDefault(); goToTurn(Number(link.dataset.turn)); return; }
  const seek = e.target.closest("[data-seek]");
  if (seek && seek.dataset.seek !== "") { seekAudio(Number(seek.dataset.seek)); return; }
  const show = e.target.closest("[data-show]");
  if (show) { showCriterion(show.dataset.show); return; }
  const critLink = e.target.closest("[data-crit-link]");
  if (critLink) {
    e.preventDefault();
    const card = document.getElementById(`cqa-crit-${critLink.dataset.critLink}`);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
    showCriterion(critLink.dataset.critLink);
    return;
  }
  const appeal = e.target.closest("[data-appeal]");
  if (appeal && !appeal.disabled) openAppeal(appeal.dataset.appeal);
}

function goToTurn(idx, cls = "is-flash") {
  const turn = document.getElementById(`cqa-turn-${idx}`);
  if (!turn) return;
  const list = ctx.view.querySelector("[data-transcript]");
  list.scrollTo({ top: turn.offsetTop - list.offsetTop - 40, behavior: "smooth" });
  if (window.matchMedia("(max-width: 1100px)").matches) turn.scrollIntoView({ behavior: "smooth", block: "center" });
  turn.classList.add(cls);
  setTimeout(() => turn.classList.remove("is-flash"), 2600);
  const t = ctx.conv.turns[idx - 1];
  if (ctx.conv.channel === "voice" && t && t.audio_offset_sec !== null && t.audio_offset_sec !== undefined) seekAudio(t.audio_offset_sec, false);
}

function seekAudio(sec, play = true) {
  const audio = ctx.view.querySelector("[data-audio]");
  if (!audio) return;
  const apply = () => { try { audio.currentTime = sec; } catch (e) { /* ignore */ } if (play) audio.play().catch(() => {}); };
  if (audio.readyState >= 1) apply();
  else { audio.preload = "metadata"; audio.addEventListener("loadedmetadata", apply, { once: true }); audio.load(); }
}

function showCriterion(cid) {
  const c = ctx.ev.criteria.find((x) => x.criterion_id === cid);
  if (!c) return;
  ctx.view.querySelectorAll(".cqa-turn.is-checked, .cqa-turn.is-evidence").forEach((t) => t.classList.remove("is-checked", "is-evidence"));
  const missed = c.value === false || c.value === null || (typeof c.value === "number" && c.points < c.max_points);
  const cls = missed && !c.is_critical_flag ? "is-checked" : "is-evidence";
  c.evidence_turns.forEach((i) => { const t = document.getElementById(`cqa-turn-${i}`); if (t) t.classList.add(cls); });
  if (c.evidence_turns.length) goToTurn(c.evidence_turns[0]);
}

function openAppeal(cid) {
  const c = ctx.ev.criteria.find((x) => x.criterion_id === cid);
  const body = document.createElement("div");
  body.className = "cqa-stack";
  body.innerHTML = `
    <div><strong>${esc(c.title)}</strong> — текущая оценка: ${valueChip(c, c.value)}</div>
    <div class="cqa-small cqa-muted">${esc(c.rationale)}</div>
    <label class="cqa-field"><span>Комментарий (почему оценка неверна)</span><textarea class="cqa-textarea" data-comment rows="4"></textarea></label>
    <label class="cqa-field"><span>Кто подаёт</span><input class="cqa-input" data-author value="Оператор"></label>`;
  modal({
    title: "Оспорить оценку",
    body,
    actions: [
      { label: "Отмена" },
      { label: "Подать апелляцию", kind: "primary", onClick: async (close, b) => {
        const comment = b.querySelector("[data-comment]").value.trim();
        if (!comment) { toast("Добавьте комментарий", "error"); return false; }
        await API.createAppeal(ctx.ev.id, cid, comment, b.querySelector("[data-author]").value.trim() || "Оператор");
        toast("Апелляция сохранена — она появилась в очереди проверяющего", "ok");
        window.dispatchEvent(new Event("cqa:refresh-header"));
        await reload();
        return true;
      } },
    ],
  });
}

async function reload(evaluationId) {
  const data = await API.conversation(ctx.id, evaluationId);
  ctx.data = data; ctx.ev = data.evaluation; ctx.conv = data.conversation;
  draw();
}

async function reevaluate() {
  const versionId = Number(ctx.view.querySelector("[data-version]").value);
  const btn = ctx.view.querySelector("[data-reeval]");
  btn.disabled = true;
  try {
    const r = await API.evaluate({ conversation_ids: [ctx.id], checklist_version_id: versionId, force: true });
    const box = ctx.view.querySelector("[data-job]");
    clearInterval(pollTimer);
    const started = Date.now();
    pollTimer = setInterval(async () => {
      const job = await API.job(r.job_id);
      const sec = Math.round((Date.now() - started) / 1000);
      box.innerHTML = `<div class="cqa-job"><strong>Идёт оценка… ${sec} с</strong>${progressBar(job.total ? (job.done / job.total) * 100 : 10)}</div>`;
      if (job.status === "done" || job.status === "failed") {
        clearInterval(pollTimer);
        toast(job.failed ? "Оценка завершилась с ошибкой — подробности в карточке" : "Оценка готова", job.failed ? "error" : "ok");
        if (ctx) await reload();
        window.dispatchEvent(new Event("cqa:refresh-header"));
      }
    }, 1500);
  } catch (e) {
    toast(e.message, "error", 8000);
    btn.disabled = false;
  }
}
