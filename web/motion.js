/* Presentation-only motion. Values and chart geometry remain owned by app.js. */
(() => {
  "use strict";
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  const preferenceKey = "short-tracker-motion";
  let preference = null;
  try {
    const saved = localStorage.getItem(preferenceKey);
    if (["full", "reduced"].includes(saved)) preference = saved;
  } catch { /* Private or blocked storage keeps this preference in memory. */ }
  const motionReduced = () => preference === "reduced" || (preference !== "full" && reduced.matches);
  const signatures = new Map();
  const running = new Set();
  const elementAnimations = new WeakMap();
  const pendingRenders = new Map();
  const easing = "cubic-bezier(.2,.75,.2,1)";
  const selectors = {
    rankings: ".rankings-intro, .ranking-snapshot, .snapshot-overview, .ranking-chart-card, .ranking-table-card, .ranking-distribution-card",
    security: ".security-identity, .data-provenance, .kpi-card, .chart-card",
  };
  let viewFrame = 0;
  let renderFrame = 0;
  let kpiTimer = 0;
  let lightFrame = 0;
  let lightTarget = null;
  let pointer = null;

  function visible(element) {
    return element && !element.closest("[hidden]") && element.getClientRects().length > 0;
  }

  function animate(element, frames, options = {}) {
    if (motionReduced() || document.hidden || !visible(element) || !element.animate) return;
    elementAnimations.get(element)?.cancel();
    const animation = element.animate(frames, { duration: 420, easing, ...options });
    elementAnimations.set(element, animation);
    running.add(animation);
    animation.finished.then(() => running.delete(animation), () => running.delete(animation));
    return animation;
  }

  function reveal(selector, stagger = 42) {
    [...document.querySelectorAll(selector)].filter(visible).slice(0, 16).forEach((element, index) => {
      animate(element, [{ opacity: .25, transform: "translateY(13px)" }, { opacity: 1, transform: "translateY(0)" }], {
        delay: Math.min(index * stagger, 220), duration: 480, fill: "backwards",
      });
    });
  }

  function viewEntrance(view) {
    cancelAnimationFrame(viewFrame);
    viewFrame = requestAnimationFrame(() => {
      reveal(selectors[view] || selectors.rankings);
      viewFrame = 0;
    });
  }

  function renderFeedback(event) {
    const { type, signature, view, source } = event.detail || {};
    if (!type || typeof signature !== "string") return;
    const rankingType = ["table", "bars", "distribution"].includes(type);
    if (rankingType && !visible(document.getElementById("rankingsContent"))) return;
    if (["kpis", "chart-range"].includes(type) && !visible(document.getElementById("securityWorkspace"))) return;
    if (signatures.get(type) === signature) return;
    const first = !signatures.has(type);
    signatures.set(type, signature);
    if (type === "view") {
      viewEntrance(view);
    } else if (type === "table") {
      reveal("#rankingTableBody tr", 22);
    } else if (type === "bars" || type === "distribution") {
      if (type === "bars" && first) reveal(".snapshot-overview, .ranking-chart-card, .ranking-table-card, .ranking-distribution-card");
      const selector = type === "bars" ? ".ranking-bar-fill" : ".distribution-fill, .distribution-segment";
      document.querySelectorAll(selector).forEach((element, index) => {
        animate(element, [{ transform: "scaleX(.08)", opacity: .3 }, { transform: "scaleX(1)", opacity: 1 }], {
          duration: 580, delay: index * 28, fill: "backwards",
        });
      });
    } else if (type === "kpis") {
      clearTimeout(kpiTimer);
      kpiTimer = setTimeout(() => reveal(".kpi-value:not(.is-loading)", 35), 120);
    } else if (type === "chart-range" && !["pan", "zoom"].includes(source)) {
      animate(document.querySelector(".range-summary"), [{ backgroundColor: "#e2eeff" }, { backgroundColor: "#f5f8fd" }], { duration: 500 });
    }
  }

  // A real sliding selection indicator follows the active range or language.
  function selectionPill(container) {
    if (!container) return;
    const pill = document.createElement("span");
    pill.className = "selection-pill";
    pill.setAttribute("aria-hidden", "true");
    container.prepend(pill);
    container.classList.add("has-selection-pill");
    let previous = null;
    function align(transition) {
      const active = container.querySelector('button[aria-pressed="true"]');
      if (!active || !visible(container)) {
        pill.hidden = true;
        previous = null;
        return;
      }
      pill.hidden = false;
      const next = { x: active.offsetLeft, y: active.offsetTop, width: active.offsetWidth, height: active.offsetHeight };
      Object.assign(pill.style, { left: `${next.x}px`, top: `${next.y}px`, width: `${next.width}px`, height: `${next.height}px` });
      if (transition && previous && previous.width && next.width) {
        animate(pill, [
          { transform: `translate(${previous.x - next.x}px, ${previous.y - next.y}px) scaleX(${previous.width / next.width})` },
          { transform: "translate(0,0) scaleX(1)" },
        ], { duration: 320 });
      }
      previous = next;
    }
    new MutationObserver(() => align(true)).observe(container, { attributes: true, subtree: true, attributeFilter: ["aria-pressed"] });
    new ResizeObserver(() => align(false)).observe(container);
    document.addEventListener("shorttracker:render", (event) => { if (event.detail?.type === "view") requestAnimationFrame(() => align(false)); });
    align(false);
  }

  // The light follows a pointer only while it is over a material panel.
  function clearLight() {
    cancelAnimationFrame(lightFrame);
    lightFrame = 0;
    lightTarget?.classList.remove("is-lit");
    lightTarget = null;
  }
  document.addEventListener("pointermove", (event) => {
    if (!finePointer.matches || motionReduced() || document.hidden) return;
    const target = event.target.closest?.(".ranking-snapshot, .data-provenance, .snapshot-stat, .kpi-card, .ranking-distribution-card");
    if (target !== lightTarget) {
      clearLight();
      lightTarget = target;
    }
    if (!target) return;
    pointer = { x: event.clientX, y: event.clientY };
    if (lightFrame) return;
    lightFrame = requestAnimationFrame(() => {
      lightFrame = 0;
      if (!lightTarget || motionReduced()) return;
      const bounds = lightTarget.getBoundingClientRect();
      lightTarget.style.setProperty("--light-x", `${pointer.x - bounds.left}px`);
      lightTarget.style.setProperty("--light-y", `${pointer.y - bounds.top}px`);
      lightTarget.classList.add("is-lit");
    });
  }, { passive: true });
  document.addEventListener("pointerout", (event) => { if (!event.relatedTarget) clearLight(); }, { passive: true });
  document.addEventListener("pointerdown", (event) => {
    const button = event.target.closest?.("button");
    if (button && !button.disabled && event.button === 0) {
      animate(button, [{ transform: "scale(.965)" }, { transform: "scale(1)" }], { duration: 260 });
    }
  }, { passive: true });

  // Observe semantic UI state; do not monkey-patch requests or alter data.
  document.addEventListener("shorttracker:render", (event) => {
    const detail = event.detail;
    if (!detail?.type) return;
    pendingRenders.set(detail.type, detail);
    if (renderFrame) return;
    renderFrame = requestAnimationFrame(() => {
      renderFrame = 0;
      for (const pending of pendingRenders.values()) renderFeedback({ detail: pending });
      pendingRenders.clear();
    });
  });
  new MutationObserver((records) => {
    for (const record of records) {
      if (record.target.open) animate(record.target, [
        { opacity: 0, transform: "translateY(18px) scale(.98)" }, { opacity: 1, transform: "translateY(0) scale(1)" },
      ], { duration: 360 });
    }
  }).observe(document.body, { attributes: true, subtree: true, attributeFilter: ["open"] });
  function updateWorkingState() {
    const active = [...document.querySelectorAll('.is-syncing, #rankingsLoading:not([hidden]), #chartLoading:not([hidden])')].some(visible);
    document.body.classList.toggle("is-working", active);
  }
  const stateObserver = new MutationObserver(updateWorkingState);
  ["syncButton", "rankingsLoading", "chartLoading"].forEach((id) => {
    const element = document.getElementById(id);
    if (element) stateObserver.observe(element, { attributes: true, attributeFilter: ["class", "hidden"] });
  });
  selectionPill(document.querySelector(".range-selector"));
  selectionPill(document.querySelector(".language-switch"));
  document.addEventListener("shorttracker:render", (event) => { if (event.detail?.type === "view") updateWorkingState(); });
  updateWorkingState();

  function stopMotion() {
    if (!motionReduced() && !document.hidden) return;
    running.forEach((animation) => animation.cancel());
    running.clear();
    clearLight();
    cancelAnimationFrame(viewFrame);
    cancelAnimationFrame(renderFrame);
    renderFrame = 0;
    pendingRenders.clear();
    clearTimeout(kpiTimer);
  }
  function updatePreference() {
    const enabled = !motionReduced();
    document.documentElement.dataset.motion = enabled ? "full" : "reduced";
    const button = document.getElementById("motionToggleButton");
    if (button) {
      const key = enabled ? "motion.disable" : preference === null && reduced.matches ? "motion.enableSystem" : "motion.enable";
      const languageIndex = document.documentElement.lang === "en-GB" ? 1 : 0;
      const label = window.SHORT_TRACKER_COPY?.[key]?.[languageIndex] || "Motion";
      button.setAttribute("aria-pressed", String(enabled));
      button.setAttribute("aria-label", label);
      button.title = label;
    }
    stopMotion();
  }
  document.getElementById("motionToggleButton")?.addEventListener("click", () => {
    preference = motionReduced() ? "full" : "reduced";
    try { localStorage.setItem(preferenceKey, preference); } catch { /* Session fallback. */ }
    updatePreference();
    if (!motionReduced()) viewEntrance(document.body.dataset.view || "rankings");
  });
  new MutationObserver(updatePreference).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  reduced.addEventListener("change", updatePreference);
  document.addEventListener("visibilitychange", stopMotion);
  updatePreference();
  viewEntrance(document.body.dataset.view || "rankings");
})();
