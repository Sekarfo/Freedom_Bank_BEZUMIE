// Хеш-роутер: #/conversations, #/conversation/<id>, #/checklists, #/appeals, #/analytics, #/settings.
// Каждый экран — отдельный модуль pages/*.js с функцией render(container, params) и необязательной destroy().

const routes = [];
let current = null;

export function route(pattern, loader) {
  const keys = [];
  const rx = new RegExp("^" + pattern.replace(/:([a-zA-Z]+)/g, (_, k) => { keys.push(k); return "([^/?]+)"; }) + "$");
  routes.push({ rx, keys, loader, pattern });
}

export function navigate(hash) {
  if (location.hash === hash) handle();
  else location.hash = hash;
}

export function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/conversations";
  const [path, query = ""] = raw.split("?");
  return { path, query: Object.fromEntries(new URLSearchParams(query)) };
}

export async function handle() {
  const { path, query } = parseHash();
  const view = document.getElementById("cqa-view");
  for (const r of routes) {
    const m = path.match(r.rx);
    if (!m) continue;
    const params = { ...query };
    r.keys.forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1])));
    if (current && current.destroy) {
      try { current.destroy(); } catch (e) { /* ignore */ }
    }
    document.querySelectorAll(".cqa-nav__link").forEach((a) => {
      const page = a.dataset.page;
      a.classList.toggle("is-active", path.startsWith("/" + page) || (page === "conversations" && path.startsWith("/conversation/")));
    });
    view.innerHTML = '<div class="cqa-empty">Загрузка…</div>';
    try {
      const mod = await r.loader();
      current = mod;
      await mod.render(view, params);
    } catch (e) {
      console.error(e);
      view.innerHTML = `<div class="cqa-empty"><strong>Не удалось открыть раздел</strong>${String(e.message || e)}</div>`;
    }
    view.focus({ preventScroll: true });
    return;
  }
  navigate("#/conversations");
}

export function start() {
  window.addEventListener("hashchange", handle);
  handle();
}
