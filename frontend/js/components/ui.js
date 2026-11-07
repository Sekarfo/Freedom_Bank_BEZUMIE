// Уведомления, модальные окна, круговой итог.
import { esc, fmtScore, scoreTone } from "../utils.js";

export function toast(message, kind = "info", ms = 4500) {
  const box = document.querySelector(".cqa-root .cqa-toasts");
  if (!box) return;
  const t = document.createElement("div");
  t.className = `cqa-toast${kind === "error" ? " cqa-toast--error" : kind === "ok" ? " cqa-toast--ok" : ""}`;
  t.textContent = message;
  box.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

// modal({title, body (html|Element), actions:[{label, kind, onClick(close) -> bool|Promise}], wide})
export function modal({ title, body, actions = [], wide = false }) {
  const root = document.querySelector(".cqa-root");
  const backdrop = document.createElement("div");
  backdrop.className = "cqa-modal-backdrop";
  backdrop.innerHTML = `
    <div class="cqa-modal${wide ? " cqa-modal--wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="cqa-modal__head"><h2>${esc(title)}</h2>
        <button class="cqa-btn cqa-btn--ghost" data-close aria-label="Закрыть">✕</button></div>
      <div class="cqa-modal__body"></div>
      <div class="cqa-modal__foot"></div>
    </div>`;
  const bodyEl = backdrop.querySelector(".cqa-modal__body");
  if (typeof body === "string") bodyEl.innerHTML = body;
  else if (body) bodyEl.appendChild(body);
  const foot = backdrop.querySelector(".cqa-modal__foot");
  const close = () => { backdrop.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop || e.target.closest("[data-close]")) close(); });
  if (!actions.length) actions = [{ label: "Закрыть", onClick: () => true }];
  for (const a of actions) {
    const b = document.createElement("button");
    b.className = `cqa-btn${a.kind === "primary" ? " cqa-btn--primary" : ""}`;
    b.textContent = a.label;
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        const res = a.onClick ? await a.onClick(close, bodyEl) : true;
        if (res !== false) close();
      } catch (e) {
        toast(e.message || String(e), "error");
      } finally {
        b.disabled = false;
      }
    });
    foot.appendChild(b);
  }
  root.appendChild(backdrop);
  const first = bodyEl.querySelector("textarea, input, select");
  if (first) first.focus();
  return { close, body: bodyEl };
}

export function scoreRing(score, label = "итог") {
  const r = 56, c = 2 * Math.PI * r;
  const pct = score === null || score === undefined ? 0 : Math.max(0, Math.min(100, score)) / 100;
  const tone = scoreTone(score);
  const color = { good: "var(--cqa-good)", mid: "var(--cqa-mid)", bad: "var(--cqa-bad)", na: "var(--cqa-na)" }[tone];
  return `
    <div class="cqa-ring" role="img" aria-label="Итог ${esc(fmtScore(score))} из 100">
      <svg viewBox="0 0 132 132"><circle cx="66" cy="66" r="${r}" fill="none" stroke="var(--cqa-surface-2)" stroke-width="12"/>
        <circle cx="66" cy="66" r="${r}" fill="none" stroke="${color}" stroke-width="12" stroke-linecap="round"
          stroke-dasharray="${(c * pct).toFixed(1)} ${c.toFixed(1)}"/></svg>
      <div class="cqa-ring__val"><div><strong>${esc(fmtScore(score))}</strong><span>${esc(label)}</span></div></div>
    </div>`;
}

export function progressBar(pct, tone) {
  const color = tone ? `background: var(--cqa-${tone})` : "";
  return `<div class="cqa-progress"><div class="cqa-progress__bar" style="width:${Math.max(0, Math.min(100, pct))}%;${color}"></div></div>`;
}
