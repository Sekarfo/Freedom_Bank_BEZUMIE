// Раздел «Чек-листы»: редактор критериев (формулировки, вес, тип, шкала, N/A, секции), версии и сравнение.
import { API } from "../api.js";
import { modal, toast } from "../components/ui.js";
import { navigate } from "../router.js";
import { esc, fmtDate, fmtNum } from "../utils.js";

const ALL_CHANNELS = ["chat", "voice", "email", "messenger"];
let st = null;

export async function render(view, params) {
  const [checklists, settings] = await Promise.all([API.checklists(), API.settings()]);
  const allVersions = checklists.flatMap((cl) => cl.versions.map((v) => ({ ...v, checklist: cl })));
  const versionId = Number(params.version) || settings.checklist_version_id_effective || (allVersions[0] || {}).id;
  const version = await API.version(versionId);
  st = { view, checklists, allVersions, version, settings, draft: structuredClone(version.criteria) };
  draw();
}

export function destroy() { st = null; }

function draw() {
  const { view, checklists, version, settings } = st;
  const isDefault = settings.checklist_version_id_effective === version.id;
  view.innerHTML = `
  <div class="cqa-page-head">
    <div><h1>Чек-листы</h1><p>Изменение критериев сохраняется новой версией. Промпты моделей собираются из конфигурации — правка кода не нужна.</p></div>
  </div>
  <div class="cqa-cl-layout">
    <aside class="cqa-panel cqa-cl-list">
      ${checklists.map((cl) => `<h3>${esc(cl.name)}</h3>${cl.versions.map((v) => `<a href="#/checklists?version=${v.id}" class="${v.id === version.id ? "is-active" : ""}">
          v${v.version} <span class="cqa-faint">· ${esc(fmtDate(v.created_at))}${settings.checklist_version_id_effective === v.id ? " · по умолчанию" : ""}</span>
          ${v.note ? `<div class="cqa-faint">${esc(v.note)}</div>` : ""}</a>`).join("")}`).join("")}
      <div class="cqa-panel__body"><button class="cqa-btn cqa-btn--sm" data-new-checklist>Новый чек-лист на основе этой версии</button></div>
    </aside>
    <section class="cqa-stack">
      <div class="cqa-panel"><div class="cqa-panel__body cqa-row cqa-row--between">
        <div><h2>${esc(version.label)} ${isDefault ? '<span class="cqa-chip cqa-chip--good">по умолчанию</span>' : ""}</h2>
          <div class="cqa-faint">${esc(version.note || "")} · ${esc(fmtDate(version.created_at))} · ${version.criteria.length} критериев</div></div>
        <div class="cqa-row">
          ${isDefault ? "" : '<button class="cqa-btn" data-make-default>Сделать версией по умолчанию</button>'}
          <button class="cqa-btn" data-prompt>Как видит модель</button>
          <select class="cqa-select" data-compare><option value="">Сравнить с…</option>${st.allVersions.filter((v) => v.id !== version.id).map((v) => `<option value="${v.id}">${esc(v.checklist.name)} · v${v.version}</option>`).join("")}</select>
          <button class="cqa-btn cqa-btn--primary" data-apply>Применить к выбранным диалогам</button>
        </div>
      </div></div>
      <div data-editor></div>
      <div class="cqa-panel"><div class="cqa-panel__body cqa-row">
        <button class="cqa-btn" data-add>+ Добавить критерий</button>
        <span class="cqa-spacer"></span>
        <span class="cqa-small cqa-muted" data-weights></span>
        <input class="cqa-input" data-note placeholder="Что изменено (комментарий к версии)" style="min-width:280px">
        <label class="cqa-check cqa-small"><input type="checkbox" data-set-default checked> сделать по умолчанию</label>
        <button class="cqa-btn cqa-btn--primary" data-save>Сохранить новую версию</button>
      </div></div>
    </section>
  </div>`;
  drawEditor();
  bind();
}

function sections() {
  return [...new Set(st.draft.map((c) => c.section).filter(Boolean))];
}

function drawEditor() {
  const box = st.view.querySelector("[data-editor]");
  box.innerHTML = `<datalist id="cqa-sections">${sections().map((s) => `<option value="${esc(s)}">`).join("")}</datalist>` +
    st.draft.map((c, i) => critEditor(c, i)).join("");
  updateWeights();
}

function critEditor(c, i) {
  const scale = c.type === "scale";
  const scaleRows = (c.scale || []).map((s, j) => `<div class="cqa-scale-row" data-scale-row="${j}">
      <input class="cqa-input" type="number" step="1" min="0" data-k="scale_value" value="${esc(s.value)}">
      <input class="cqa-input" data-k="scale_desc" value="${esc(s.description || "")}" placeholder="Описание ступени">
      <button class="cqa-btn cqa-btn--ghost" data-scale-del="${j}" title="Удалить ступень">✕</button></div>`).join("");
  return `<div class="cqa-crit-edit" data-idx="${i}">
    <div class="cqa-row cqa-row--between" style="margin-bottom:8px">
      <strong>${i + 1}. ${esc(c.title || "Без названия")}</strong>
      <div class="cqa-row">
        <button class="cqa-btn cqa-btn--ghost" data-move="-1" ${i === 0 ? "disabled" : ""} title="Выше">↑</button>
        <button class="cqa-btn cqa-btn--ghost" data-move="1" ${i === st.draft.length - 1 ? "disabled" : ""} title="Ниже">↓</button>
        <button class="cqa-btn cqa-btn--ghost cqa-btn--danger" data-del title="Удалить критерий">✕</button>
      </div>
    </div>
    <div class="cqa-crit-edit__grid">
      <label class="cqa-field"><span>Код (criterion_id)</span><input class="cqa-input cqa-id" data-k="criterion_id" value="${esc(c.criterion_id)}" pattern="[a-z][a-z0-9_]{1,40}"></label>
      <label class="cqa-field"><span>Название</span><input class="cqa-input" data-k="title" value="${esc(c.title)}"></label>
      <label class="cqa-field"><span>Секция</span><input class="cqa-input" data-k="section" list="cqa-sections" value="${esc(c.section)}"></label>
      <label class="cqa-field"><span>Вес</span><input class="cqa-input" type="number" min="0" step="0.5" data-k="weight" value="${esc(c.weight)}" ${c.is_critical_flag ? "disabled" : ""}></label>
      <label class="cqa-field"><span>Тип ответа</span><select class="cqa-select" data-k="type" ${c.is_critical_flag ? "disabled" : ""}>
        <option value="binary" ${!scale ? "selected" : ""}>Да / Нет</option><option value="scale" ${scale ? "selected" : ""}>Шкала</option></select></label>
    </div>
    <div class="cqa-row cqa-mt" style="margin-top:8px">
      <label class="cqa-check"><input type="checkbox" data-k="na_allowed" ${c.na_allowed ? "checked" : ""} ${c.is_critical_flag ? "disabled" : ""}> N/A допускается</label>
      <label class="cqa-check"><input type="checkbox" data-k="is_critical_flag" ${c.is_critical_flag ? "checked" : ""}> Грубое нарушение (флаг без баллов)</label>
      <span class="cqa-faint">Каналы:</span>
      ${ALL_CHANNELS.map((ch) => `<label class="cqa-check cqa-small"><input type="checkbox" data-channel="${ch}" ${(c.channels || ALL_CHANNELS).includes(ch) ? "checked" : ""}> ${ch}</label>`).join("")}
    </div>
    <div class="cqa-crit-edit__defs">
      ${scale ? `<div class="cqa-field" style="grid-column:1/-1"><span>Шкала (верхняя ступень = полный вес)</span>${scaleRows}
          <div><button class="cqa-btn cqa-btn--sm cqa-mt" data-scale-add style="margin-top:6px">+ ступень</button></div></div>`
        : `<label class="cqa-field"><span>«Да» — когда</span><textarea class="cqa-textarea" data-k="yes_definition">${esc(c.yes_definition)}</textarea></label>
           <label class="cqa-field"><span>«Нет» — когда</span><textarea class="cqa-textarea" data-k="no_definition">${esc(c.no_definition)}</textarea></label>`}
      ${c.na_allowed ? `<label class="cqa-field"><span>N/A — условие неприменимости</span><textarea class="cqa-textarea" data-k="na_condition">${esc(c.na_condition)}</textarea></label>` : ""}
      <label class="cqa-field" ${c.na_allowed ? "" : 'style="grid-column:1/-1"'}><span>Пояснения для оценщика (попадают в промпт)</span><textarea class="cqa-textarea" data-k="guidance">${esc(c.guidance)}</textarea></label>
    </div>
  </div>`;
}

// Считывает значения формы в st.draft.
function collect() {
  st.view.querySelectorAll(".cqa-crit-edit").forEach((box) => {
    const c = st.draft[Number(box.dataset.idx)];
    box.querySelectorAll("[data-k]").forEach((inp) => {
      const k = inp.dataset.k;
      if (k === "scale_value" || k === "scale_desc") return;
      if (inp.type === "checkbox") c[k] = inp.checked;
      else if (k === "weight") c[k] = inp.value === "" ? 0 : Number(inp.value);
      else c[k] = inp.value;
    });
    c.channels = [...box.querySelectorAll("[data-channel]")].filter((x) => x.checked).map((x) => x.dataset.channel);
    if (c.type === "scale") {
      c.scale = [...box.querySelectorAll("[data-scale-row]")].map((row) => ({
        value: Number(row.querySelector("[data-k=scale_value]").value),
        description: row.querySelector("[data-k=scale_desc]").value,
      }));
    } else {
      c.scale = null;
    }
    if (c.is_critical_flag) { c.weight = 0; c.type = "binary"; c.na_allowed = false; c.scale = null; }
  });
}

function updateWeights() {
  const w = st.draft.filter((c) => !c.is_critical_flag).reduce((s, c) => s + (Number(c.weight) || 0), 0);
  const el = st.view.querySelector("[data-weights]");
  if (el) el.textContent = `Сумма весов: ${fmtNum(w)}${w !== 100 ? " (итог всё равно нормируется к 100)" : ""}`;
}

function bind() {
  const { view } = st;
  const editor = view.querySelector("[data-editor]");
  editor.addEventListener("change", (e) => {
    const k = e.target.dataset.k;
    if (k === "type" || k === "na_allowed" || k === "is_critical_flag") {
      collect();
      const c = st.draft[Number(e.target.closest(".cqa-crit-edit").dataset.idx)];
      if (c.type === "scale" && (!c.scale || c.scale.length < 2)) {
        c.scale = [{ value: 10, description: "Полностью" }, { value: 5, description: "Частично" }, { value: 0, description: "Не выполнено" }];
      }
      drawEditor();
    } else if (k === "weight") {
      collect(); updateWeights();
    }
  });
  editor.addEventListener("click", (e) => {
    const box = e.target.closest(".cqa-crit-edit");
    if (!box) return;
    const i = Number(box.dataset.idx);
    if (e.target.closest("[data-move]")) {
      collect();
      const d = Number(e.target.closest("[data-move]").dataset.move);
      const [item] = st.draft.splice(i, 1);
      st.draft.splice(i + d, 0, item);
      drawEditor();
    } else if (e.target.closest("[data-del]")) {
      collect();
      if (st.draft.length <= 1) return toast("В чек-листе должен остаться хотя бы один критерий", "error");
      st.draft.splice(i, 1);
      drawEditor();
    } else if (e.target.closest("[data-scale-add]")) {
      collect();
      st.draft[i].scale.push({ value: 0, description: "" });
      drawEditor();
    } else if (e.target.closest("[data-scale-del]")) {
      collect();
      st.draft[i].scale.splice(Number(e.target.closest("[data-scale-del]").dataset.scaleDel), 1);
      drawEditor();
    }
  });

  view.querySelector("[data-add]").addEventListener("click", () => {
    collect();
    const n = st.draft.length + 1;
    const secs = sections();
    st.draft.push({
      criterion_id: `criterion_${n}`, title: "Новый критерий", section: secs[secs.length - 2] || secs[0] || "Без секции",
      weight: 10, type: "binary", scale: null, na_allowed: false, yes_definition: "", no_definition: "", na_condition: "",
      guidance: "", is_critical_flag: false, channels: [...ALL_CHANNELS],
    });
    drawEditor();
    const last = view.querySelector(".cqa-crit-edit:last-child");
    if (last) { last.scrollIntoView({ behavior: "smooth", block: "center" }); last.querySelector("[data-k=criterion_id]").focus(); }
  });

  view.querySelector("[data-save]").addEventListener("click", async (e) => {
    collect();
    const bad = st.draft.find((c) => !/^[a-z][a-z0-9_]{1,40}$/.test(c.criterion_id));
    if (bad) return toast(`Код «${bad.criterion_id}»: латиница в нижнем регистре, цифры и _, начинается с буквы`, "error", 7000);
    e.target.disabled = true;
    try {
      const note = view.querySelector("[data-note]").value.trim() || "Изменение критериев";
      const ver = await API.createVersion(st.version.checklist_id, st.draft, note);
      if (view.querySelector("[data-set-default]").checked) await API.saveSettings({ checklist_version_id: ver.id });
      toast(`Сохранена версия v${ver.version}. Примените её к диалогам.`, "ok", 6000);
      navigate(`#/checklists?version=${ver.id}`);
    } catch (err) {
      toast(err.message, "error", 9000);
    } finally {
      e.target.disabled = false;
    }
  });

  const mk = view.querySelector("[data-make-default]");
  if (mk) mk.addEventListener("click", async () => {
    await API.saveSettings({ checklist_version_id: st.version.id });
    toast("Версия используется по умолчанию для новых оценок", "ok");
    render(view, { version: st.version.id });
  });

  view.querySelector("[data-apply]").addEventListener("click", () => navigate(`#/conversations?checklistVersion=${st.version.id}`));

  view.querySelector("[data-prompt]").addEventListener("click", async () => {
    const r = await API.versionPrompt(st.version.id);
    modal({ title: "Критерии в промпте модели", body: `<pre class="cqa-pre" style="max-height:65vh">${esc(r.text)}</pre>`, wide: true });
  });

  view.querySelector("[data-compare]").addEventListener("change", async (e) => {
    const other = Number(e.target.value);
    if (!other) return;
    const d = await API.diff(other, st.version.id);
    const fmt = (v) => esc(typeof v === "object" ? JSON.stringify(v) : String(v));
    const body = `
      ${d.added.length ? `<h3 class="cqa-diff-add">Добавлены</h3><ul>${d.added.map((c) => `<li><strong>${esc(c.criterion_id)}</strong> — ${esc(c.title)} (вес ${c.weight})</li>`).join("")}</ul>` : ""}
      ${d.removed.length ? `<h3 class="cqa-diff-del">Удалены</h3><ul>${d.removed.map((c) => `<li><strong>${esc(c.criterion_id)}</strong> — ${esc(c.title)}</li>`).join("")}</ul>` : ""}
      ${d.changed.length ? `<h3>Изменены</h3>${d.changed.map((c) => `<div class="cqa-mt"><strong>${esc(c.criterion_id)}</strong>
          <table class="cqa-table"><thead><tr><th>Поле</th><th>Было</th><th>Стало</th></tr></thead><tbody>
          ${c.fields.map((f) => `<tr><td>${esc(f)}</td><td class="cqa-diff-del">${fmt(c.before[f])}</td><td class="cqa-diff-add">${fmt(c.after[f])}</td></tr>`).join("")}</tbody></table></div>`).join("")}` : ""}
      ${!d.added.length && !d.removed.length && !d.changed.length ? "<p>Версии совпадают.</p>" : ""}`;
    modal({ title: "Сравнение версий", body, wide: true });
    e.target.value = "";
  });

  view.querySelector("[data-new-checklist]").addEventListener("click", () => {
    collect();
    const body = `<label class="cqa-field"><span>Название нового чек-листа</span><input class="cqa-input" data-name value="Копия: ${esc(st.version.checklist_name)}"></label>`;
    modal({ title: "Новый чек-лист", body, actions: [{ label: "Отмена" }, { label: "Создать", kind: "primary", onClick: async (close, b) => {
      const ver = await API.createChecklist(b.querySelector("[data-name]").value.trim() || "Новый чек-лист", "", st.draft, "Создан на основе " + st.version.label);
      toast("Чек-лист создан", "ok");
      navigate(`#/checklists?version=${ver.id}`);
    } }] });
  });
}
