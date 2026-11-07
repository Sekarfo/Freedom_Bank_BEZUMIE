// Раздел «Диалоги»: загрузка набора по group_id и файла, пакетная оценка с прогрессом, фильтры, экспорт.
import { API } from "../api.js";
import { modal, progressBar, toast } from "../components/ui.js";
import { navigate } from "../router.js";
import { CHANNELS, ICONS, MODES, PROVIDERS, debounce, esc, fmtDate, fmtMoney, scoreChip } from "../utils.js";

const state = {
  filters: { dataset: "", channel: "", language: "", status: "", needs_review: "", critical: "", q: "" },
  selected: new Set(),
  items: [],
  datasets: [],
  versions: [],
  settings: null,
  versionId: null,
  pollTimer: null,
};

function storage(key, value) {
  try {
    if (value === undefined) return sessionStorage.getItem(key);
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch (e) { return null; }
  return null;
}

export async function render(view, params) {
  view.innerHTML = layout();
  const [checklists, settings] = await Promise.all([API.checklists(), API.settings()]);
  state.settings = settings;
  state.versions = checklists.flatMap((cl) => cl.versions.map((v) => ({ ...v, label: `${cl.name} · v${v.version}` })));
  state.versionId = Number(params.checklistVersion) || settings.checklist_version_id_effective || (state.versions[0] || {}).id;
  if (params.checklistVersion) toast("Выберите диалоги и нажмите «Оценить выбранные» — будет применена выбранная версия чек-листа.");
  renderEvalPanel(view);
  bind(view);
  await loadList(view);
  const activeJob = storage("cqa-active-job");
  if (activeJob) pollJob(view, activeJob);
}

export function destroy() {
  clearInterval(state.pollTimer);
}

function layout() {
  return `
  <div class="cqa-page-head">
    <div><h1>Диалоги</h1><p>Загрузите набор, запустите оценку и откройте карточку диалога.</p></div>
  </div>
  <div class="cqa-grid cqa-grid--2">
    <section class="cqa-panel">
      <div class="cqa-panel__head"><h2>Данные</h2></div>
      <div class="cqa-panel__body cqa-stack">
        <div class="cqa-row">
          <label class="cqa-field" style="width:150px"><span>group_id</span><input class="cqa-input" data-group value="DEV"></label>
          <label class="cqa-field" style="width:130px"><span>Набор</span><input class="cqa-input" data-dataset-name placeholder="DEV / PROD"></label>
          <button class="cqa-btn cqa-btn--primary" data-fetch style="align-self:flex-end">Загрузить набор</button>
        </div>
        <div class="cqa-row">
          <label class="cqa-btn" style="cursor:pointer">Загрузить файл JSON<input type="file" accept=".json,application/json" data-file hidden></label>
          <span class="cqa-faint">Формат организаторов, {records:[...]} или одна запись с transcript</span>
        </div>
        <div class="cqa-row">
          <select class="cqa-select" data-export-dataset></select>
          <a class="cqa-btn" data-export href="#">Скачать results.json</a>
          <button class="cqa-btn" data-validate>Проверить формат</button>
        </div>
      </div>
    </section>
    <section class="cqa-panel">
      <div class="cqa-panel__head"><h2>Оценка</h2><a href="#/settings" class="cqa-small">Настройки модели →</a></div>
      <div class="cqa-panel__body cqa-stack" data-eval-panel></div>
    </section>
  </div>
  <div data-job class="cqa-mt"></div>
  <section class="cqa-panel cqa-mt">
    <div class="cqa-panel__head">
      <div class="cqa-row" data-filters>
        <select class="cqa-select" data-f="dataset"><option value="">Все наборы</option></select>
        <select class="cqa-select" data-f="channel"><option value="">Все каналы</option><option value="chat">Чат</option><option value="voice">Звонок</option></select>
        <select class="cqa-select" data-f="language"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select>
        <select class="cqa-select" data-f="status"><option value="">Любой статус</option><option value="ok">Оценён</option><option value="error">Ошибка</option><option value="not_evaluated">Не оценён</option></select>
        <select class="cqa-select" data-f="needs_review"><option value="">Проверка: все</option><option value="true">Требует проверки</option><option value="false">Не требует</option></select>
        <select class="cqa-select" data-f="critical"><option value="">Грубые ошибки: все</option><option value="true">Есть нарушение</option><option value="false">Нет нарушений</option></select>
        <input class="cqa-input" data-f="q" placeholder="Поиск по ID" style="width:160px">
      </div>
      <span class="cqa-faint" data-count></span>
    </div>
    <div class="cqa-table-wrap" data-table></div>
  </section>`;
}

function renderEvalPanel(view) {
  const s = state.settings;
  const box = view.querySelector("[data-eval-panel]");
  const opts = state.versions.map((v) => `<option value="${v.id}" ${v.id === state.versionId ? "selected" : ""}>${esc(v.label)}</option>`).join("");
  const mode = [MODES[s.mode]];
  if (s.mode !== "cloud") mode.push(s.local_model);
  if (s.mode !== "local") mode.push(PROVIDERS[s.cloud_provider]);
  if (s.mode === "hybrid") mode.push(s.hybrid_strategy === "blind" ? "слепая + арбитр" : "проверка облаком");
  box.innerHTML = `
    <label class="cqa-field"><span>Версия чек-листа</span><select class="cqa-select" data-version>${opts}</select></label>
    <div class="cqa-small cqa-muted">Режим: <strong>${esc(mode.join(" · "))}</strong></div>
    <div class="cqa-row">
      <button class="cqa-btn cqa-btn--primary" data-eval="selected">Оценить выбранные <span data-sel-count></span></button>
      <button class="cqa-btn" data-eval="unevaluated">Оценить неоценённые</button>
      <button class="cqa-btn" data-eval="force" title="Без кэша: заново вызвать модели">Переоценить выбранные</button>
    </div>`;
  box.querySelector("[data-version]").addEventListener("change", (e) => (state.versionId = Number(e.target.value)));
}

function bind(view) {
  view.querySelector("[data-fetch]").addEventListener("click", async (e) => {
    const group = view.querySelector("[data-group]").value.trim();
    const ds = view.querySelector("[data-dataset-name]").value.trim();
    if (!group) return toast("Укажите group_id", "error");
    e.target.disabled = true;
    e.target.textContent = "Загрузка…";
    try {
      const r = await API.fetchDataset(group, ds);
      toast(`${r.dataset}: получено ${r.total}, новых ${r.added}, обновлено ${r.updated}` + (r.warning ? `. ${r.warning}` : ""), r.warning ? "info" : "ok", 7000);
      state.filters.dataset = r.dataset;
      await loadList(view);
    } catch (err) {
      toast(err.message, "error", 8000);
    } finally {
      e.target.disabled = false;
      e.target.textContent = "Загрузить набор";
    }
  });

  view.querySelector("[data-file]").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      const r = await API.upload(payload, "upload");
      toast(`Загружено диалогов: ${r.total} (новых ${r.added})` + (r.errors.length ? `, ошибок разбора: ${r.errors.length}` : ""), r.errors.length ? "error" : "ok");
      state.filters.dataset = "upload";
      await loadList(view);
    } catch (err) {
      toast("Файл не прочитан: " + err.message, "error", 8000);
    }
    e.target.value = "";
  });

  view.querySelectorAll("[data-filters] [data-f]").forEach((input) => {
    const handler = () => { state.filters[input.dataset.f] = input.value; loadList(view); };
    input.addEventListener(input.tagName === "INPUT" ? "input" : "change", input.tagName === "INPUT" ? debounce(handler, 300) : handler);
  });

  view.querySelector("[data-eval-panel]").addEventListener("click", (e) => {
    const b = e.target.closest("[data-eval]");
    if (b) startEvaluation(view, b.dataset.eval);
  });

  view.querySelector("[data-export]").addEventListener("click", (e) => {
    const ds = view.querySelector("[data-export-dataset]").value;
    if (!ds) { e.preventDefault(); return toast("Нет загруженных наборов", "error"); }
    e.currentTarget.href = API.exportUrl(ds);
  });
  view.querySelector("[data-validate]").addEventListener("click", async () => {
    const ds = view.querySelector("[data-export-dataset]").value;
    if (!ds) return;
    try {
      const r = await API.validateExport(ds);
      const body = r.errors.length
        ? `<p class="cqa-chip cqa-chip--bad">Ошибок: ${r.errors.length}</p><ul>${r.errors.slice(0, 40).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
        : `<p><span class="cqa-chip cqa-chip--good">Формат results.json 1.0 соблюдён</span> Записей: ${r.records}.</p>`;
      modal({ title: `Проверка results.json · ${ds}`, body: body + (r.warnings_total ? `<p class="cqa-faint">Предупреждений: ${r.warnings_total}</p><ul class="cqa-small">${r.warnings.slice(0, 15).map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "") });
    } catch (err) { toast(err.message, "error"); }
  });

  view.querySelector("[data-table]").addEventListener("click", (e) => {
    const cb = e.target.closest("input[type=checkbox]");
    if (cb) {
      if (cb.dataset.all !== undefined) {
        state.items.forEach((it) => (cb.checked ? state.selected.add(it.conversation_id) : state.selected.delete(it.conversation_id)));
        view.querySelectorAll("[data-row-check]").forEach((x) => (x.checked = cb.checked));
      } else {
        cb.checked ? state.selected.add(cb.value) : state.selected.delete(cb.value);
      }
      updateSelCount(view);
      return;
    }
    const tr = e.target.closest("tr[data-id]");
    if (tr && !e.target.closest("label")) navigate(`#/conversation/${encodeURIComponent(tr.dataset.id)}`);
  });
}

async function loadList(view) {
  const f = state.filters;
  const data = await API.conversations(f);
  state.items = data.items;
  state.datasets = data.datasets;
  const dsSel = view.querySelector("[data-f=dataset]");
  dsSel.innerHTML = `<option value="">Все наборы</option>` + data.datasets.map((d) => `<option ${d === f.dataset ? "selected" : ""}>${esc(d)}</option>`).join("");
  const exp = view.querySelector("[data-export-dataset]");
  const prev = exp.value || f.dataset || data.datasets[0] || "";
  exp.innerHTML = data.datasets.map((d) => `<option ${d === prev ? "selected" : ""}>${esc(d)}</option>`).join("") || "<option value=''>нет наборов</option>";
  for (const [k, v] of Object.entries(f)) {
    const inp = view.querySelector(`[data-f=${k}]`);
    if (inp && inp.value !== v) inp.value = v;
  }
  view.querySelector("[data-count]").textContent = `Показано: ${data.total}`;
  view.querySelector("[data-table]").innerHTML = table(data.items);
  updateSelCount(view);
}

function table(items) {
  if (!items.length) {
    return `<div class="cqa-empty"><strong>Диалогов нет</strong>Загрузите набор по group_id (например, DEV) или файл JSON.</div>`;
  }
  const rows = items.map((it) => {
    const ev = it.evaluation;
    let status = '<span class="cqa-chip cqa-chip--outline">не оценён</span>';
    if (ev && ev.status === "ok") status = '<span class="cqa-chip cqa-chip--good">оценён</span>';
    if (ev && ev.status === "error") status = `<span class="cqa-chip cqa-chip--bad" title="${esc(ev.error)}">ошибка</span>`;
    return `<tr class="is-clickable" data-id="${esc(it.conversation_id)}">
      <td><label><input type="checkbox" data-row-check value="${esc(it.conversation_id)}" ${state.selected.has(it.conversation_id) ? "checked" : ""}></label></td>
      <td class="cqa-id">${esc(it.conversation_id)}</td>
      <td>${esc(CHANNELS[it.channel] || it.channel)}${it.has_audio ? ' <span class="cqa-faint">· запись</span>' : ""}</td>
      <td>${esc(it.language)}</td>
      <td class="cqa-num">${it.turns_count}</td>
      <td>${ev && ev.status === "ok" ? scoreChip(ev.final_score) : "—"}${ev && ev.ai_score !== ev.final_score && ev.status === "ok" ? ` <span class="cqa-faint" title="Исходный итог ИИ">ИИ ${ev.ai_score ?? "—"}</span>` : ""}</td>
      <td>${ev && ev.critical_flag ? `<span class="cqa-chip cqa-chip--bad">${ICONS.flag} нарушение</span>` : ev && ev.status === "ok" ? '<span class="cqa-faint">нет</span>' : "—"}</td>
      <td>${ev && ev.needs_review ? '<span class="cqa-chip cqa-chip--mid">требует проверки</span>' : ev && ev.status === "ok" ? '<span class="cqa-faint">нет</span>' : "—"}</td>
      <td>${status}</td>
      <td class="cqa-faint">${ev ? esc(MODES[ev.mode] || ev.mode) + " · " + fmtDate(ev.created_at) : esc(it.dataset)}</td>
    </tr>`;
  }).join("");
  return `<table class="cqa-table"><thead><tr>
      <th><input type="checkbox" data-all title="Выбрать все"></th><th>ID</th><th>Канал</th><th>Язык</th><th class="cqa-num">Реплик</th>
      <th>Итог</th><th>Грубая ошибка</th><th>Проверка</th><th>Статус</th><th>Оценка</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function updateSelCount(view) {
  const el = view.querySelector("[data-sel-count]");
  if (el) el.textContent = state.selected.size ? `(${state.selected.size})` : "";
}

async function startEvaluation(view, kind) {
  const body = { checklist_version_id: state.versionId };
  if (kind === "unevaluated") {
    const ids = state.items.map((i) => i.conversation_id);
    if (!ids.length) return toast("Нет диалогов в текущем фильтре", "error");
    Object.assign(body, { conversation_ids: ids, only_unevaluated: true });
  } else {
    if (!state.selected.size) return toast("Отметьте диалоги галочками", "error");
    Object.assign(body, { conversation_ids: [...state.selected], force: kind === "force" });
  }
  try {
    const r = await API.evaluate(body);
    if (!r.job_id) return toast(r.message || "Нечего оценивать");
    toast(`Запущена оценка ${r.total} диалогов`, "ok");
    storage("cqa-active-job", r.job_id);
    pollJob(view, r.job_id);
  } catch (e) {
    toast(e.message, "error", 8000);
  }
}

function pollJob(view, jobId) {
  clearInterval(state.pollTimer);
  const box = view.querySelector("[data-job]");
  const tick = async () => {
    let job;
    try { job = await API.job(jobId); } catch (e) { clearInterval(state.pollTimer); storage("cqa-active-job", null); box.innerHTML = ""; return; }
    const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
    box.innerHTML = `<div class="cqa-job">
      <div class="cqa-row cqa-row--between"><strong>${job.status === "running" || job.status === "queued" ? "Идёт оценка…" : job.status === "done" ? "Оценка завершена" : "Оценка прервана"}</strong>
        <span class="cqa-small">${job.done} из ${job.total} · ошибок ${job.failed} · из кэша ${job.from_cache} · ${fmtMoney(job.cost_usd)}</span></div>
      ${progressBar(pct)}${job.error ? `<div class="cqa-small" style="color:var(--cqa-bad)">${esc(job.error)}</div>` : ""}</div>`;
    if (job.done && job.done % 3 === 0) loadList(view);
    if (job.status === "done" || job.status === "failed") {
      clearInterval(state.pollTimer);
      storage("cqa-active-job", null);
      toast(job.failed ? `Готово, ошибок: ${job.failed}` : "Оценка завершена", job.failed ? "error" : "ok");
      window.dispatchEvent(new Event("cqa:refresh-header"));
      await loadList(view);
      setTimeout(() => { if (box.isConnected) box.innerHTML = ""; }, 8000);
    }
  };
  tick();
  state.pollTimer = setInterval(tick, 1500);
}
