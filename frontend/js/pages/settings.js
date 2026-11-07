// Раздел «Настройки модели»: провайдер модели-судьи, чек-лист по умолчанию, статус подключений.
import { API } from "../api.js";
import { toast } from "../components/ui.js";
import { esc } from "../utils.js";

export async function render(view) {
  const [settings, status, checklists] = await Promise.all([API.settings(), API.status(), API.checklists()]);
  const versions = checklists.flatMap((cl) => cl.versions.map((v) => ({ ...v, label: `${cl.name} · v${v.version}` })));
  const s = { ...settings };

  const A = status.providers.anthropic;
  const O = status.providers.openai;
  const dot = (ok) => `<span class="cqa-chip cqa-chip--${ok ? "good" : "bad"}">${ok ? "подключено" : "нет"}</span>`;
  const seg = (key, options) => `<div class="cqa-segment" data-seg="${key}">${options.map(([v, label]) =>
    `<button type="button" data-v="${v}" class="${s[key] === v ? "is-active" : ""}">${esc(label)}</button>`).join("")}</div>`;

  view.innerHTML = `
    <div class="cqa-page-head"><div><h1>Настройки модели</h1>
      <p>Один вызов модели на диалог: по каждому критерию модель даёт значение, номера реплик, дословную цитату и обоснование.
      Код проверяет цитаты по тексту, считает баллы и отправляет сомнительные выводы человеку.</p></div></div>
    <div class="cqa-grid cqa-grid--2">
      <section class="cqa-panel"><div class="cqa-panel__head"><h2>Модель-судья</h2></div>
        <div class="cqa-panel__body cqa-stack">
          <div class="cqa-field"><span>Провайдер</span>${seg("provider", [["anthropic", `Claude · ${A.model}`], ["openai", `OpenAI-совместимый · ${O.model || "не задан"}`]])}
            <span class="cqa-faint">Claude — основной. OpenAI-совместимый API (OpenAI, OpenRouter) — запасной путь, модель и ключ задаются в .env.</span></div>
          <label class="cqa-field"><span>Чек-лист по умолчанию</span><select class="cqa-select" data-version>
            ${versions.map((v) => `<option value="${v.id}" ${v.id === settings.checklist_version_id_effective ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select></label>
          <div><button class="cqa-btn cqa-btn--primary" data-save>Сохранить</button></div>
        </div></section>
      <section class="cqa-panel"><div class="cqa-panel__head"><h2>Статус подключений</h2><button class="cqa-btn cqa-btn--sm" data-refresh>Обновить</button></div>
        <div class="cqa-panel__body cqa-stack">
          <div><strong>Claude (Anthropic)</strong> ${dot(A.configured)}
            <div class="cqa-small cqa-muted">${esc(A.model)} · effort ${esc(A.effort)}${A.price ? ` · $${A.price.input} / $${A.price.output} за 1 млн токенов (вход/выход)` : ""}</div></div>
          <div><strong>OpenAI-совместимый API</strong> ${dot(O.configured)}
            <div class="cqa-small cqa-muted">${esc(O.model || "OPENAI_MODEL не задан")}${O.base_url ? ` · ${esc(O.base_url)}` : ""}${O.price ? ` · $${O.price.input} / $${O.price.output} за 1 млн токенов` : " · цены не заданы в .env"}</div></div>
          <div><strong>API организаторов</strong> ${dot(status.organizers_api_configured)}</div>
          <div class="cqa-faint">Версии промптов: ${esc(Object.values(status.prompt_versions).join(", "))} · команда ${esc(status.team_name)}</div>
        </div></section>
    </div>`;

  view.querySelectorAll("[data-seg]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("[data-v]");
    if (!b) return;
    s[g.dataset.seg] = b.dataset.v;
    g.querySelectorAll("button").forEach((x) => x.classList.toggle("is-active", x === b));
  }));
  view.querySelector("[data-refresh]").addEventListener("click", () => render(view));
  view.querySelector("[data-save]").addEventListener("click", async () => {
    try {
      await API.saveSettings({ provider: s.provider, checklist_version_id: Number(view.querySelector("[data-version]").value) });
      toast("Настройки сохранены", "ok");
      window.dispatchEvent(new Event("cqa:refresh-header"));
    } catch (e) { toast(e.message, "error"); }
  });
}
