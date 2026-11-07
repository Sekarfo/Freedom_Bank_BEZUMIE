// Раздел «Настройки модели»: режим, провайдер, локальная модель, стратегия, статус подключений.
import { API } from "../api.js";
import { toast } from "../components/ui.js";
import { esc, fmtMoney } from "../utils.js";

export async function render(view) {
  const [settings, status, checklists] = await Promise.all([API.settings(), API.status(), API.checklists()]);
  const versions = checklists.flatMap((cl) => cl.versions.map((v) => ({ ...v, label: `${cl.name} · v${v.version}` })));
  const s = { ...settings };

  const seg = (key, options) => `<div class="cqa-segment" data-seg="${key}">${options.map(([v, label]) =>
    `<button type="button" data-v="${v}" class="${s[key] === v ? "is-active" : ""}">${esc(label)}</button>`).join("")}</div>`;

  const L = status.local;
  const A = status.cloud.anthropic;
  const O = status.cloud.openai;
  const dot = (ok) => `<span class="cqa-chip cqa-chip--${ok ? "good" : "bad"}">${ok ? "подключено" : "нет"}</span>`;

  view.innerHTML = `
    <div class="cqa-page-head"><div><h1>Настройки модели</h1>
      <p>Гибрид: локальная модель делает первичную оценку, облачная проверяет, код принимает финальное решение и считает баллы.</p></div></div>
    <div class="cqa-grid cqa-grid--2">
      <section class="cqa-panel"><div class="cqa-panel__head"><h2>Режим оценки</h2></div>
        <div class="cqa-panel__body cqa-stack">
          <div class="cqa-field"><span>Режим</span>${seg("mode", [["local", "Локально"], ["cloud", "Облако"], ["hybrid", "Гибрид"]])}</div>
          <div class="cqa-field"><span>Облачный провайдер</span>${seg("cloud_provider", [["anthropic", "Claude"], ["openai", "OpenAI"]])}</div>
          <div class="cqa-field"><span>Локальная модель</span>${seg("local_model", status.local_models.map((m) => [m.key, m.display]))}</div>
          <div class="cqa-field"><span>Стратегия гибрида</span>${seg("hybrid_strategy", [["review", "Проверка (review)"], ["blind", "Слепая + арбитр (blind)"]])}
            <span class="cqa-faint">review — облако видит вердикты локальной модели (1 вызов); blind — облако оценивает независимо, расхождения решает арбитр (1–2 вызова, нет «якоря»).</span></div>
          <label class="cqa-field"><span>Чек-лист по умолчанию</span><select class="cqa-select" data-version>
            ${versions.map((v) => `<option value="${v.id}" ${v.id === settings.checklist_version_id_effective ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select></label>
          <div><button class="cqa-btn cqa-btn--primary" data-save>Сохранить</button></div>
        </div></section>
      <section class="cqa-panel"><div class="cqa-panel__head"><h2>Статус подключений</h2><button class="cqa-btn cqa-btn--sm" data-refresh>Обновить</button></div>
        <div class="cqa-panel__body cqa-stack">
          <div><strong>Локальная модель</strong> ${dot(L.reachable)}
            <div class="cqa-small cqa-muted">${esc(L.backend)} · ${esc(L.base_url)}</div>
            ${L.reachable ? `<div class="cqa-small">Модели на сервере: ${esc(L.models.join(", ") || "нет")}</div>
              ${L.selected_available ? `<div class="cqa-small"><span class="cqa-chip cqa-chip--good">${esc(L.selected_tag)} загружена</span></div>`
                : `<div class="cqa-small"><span class="cqa-chip cqa-chip--mid">${esc(L.selected_tag)} не найдена</span> выполните <code>ollama pull ${esc(L.selected_tag)}</code></div>`}`
              : `<div class="cqa-small" style="color:var(--cqa-bad)">${esc(L.error || "")}</div><div class="cqa-faint">Запустите Ollama: <code>ollama serve</code></div>`}</div>
          <div><strong>Claude (Anthropic)</strong> ${dot(A.configured)}
            <div class="cqa-small cqa-muted">${esc(A.model)} · effort ${esc(A.effort)}${A.price ? ` · $${A.price.input} / $${A.price.output} за 1 млн токенов (вход/выход)` : ""}</div></div>
          <div><strong>OpenAI</strong> ${dot(O.configured)}
            <div class="cqa-small cqa-muted">${esc(O.model || "OPENAI_MODEL не задан")}${O.price ? ` · $${O.price.input} / $${O.price.output} за 1 млн токенов` : " · цены не заданы в .env"}</div></div>
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
      await API.saveSettings({ mode: s.mode, cloud_provider: s.cloud_provider, local_model: s.local_model,
        hybrid_strategy: s.hybrid_strategy, checklist_version_id: Number(view.querySelector("[data-version]").value) });
      toast("Настройки сохранены", "ok");
      window.dispatchEvent(new Event("cqa:refresh-header"));
    } catch (e) { toast(e.message, "error"); }
  });
}
