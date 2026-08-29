// melrater — axis switcher, keyboard shortcuts, jump box, auto-advance.
// State lives on the backend; this only toggles pre-rendered content, clicks
// htmx-wired buttons, and keeps one per-browser preference in localStorage.
(() => {
  const AUTO_ADVANCE_KEY = "melrater:auto-advance";

  const autoAdvanceOn = () => {
    try {
      return localStorage.getItem(AUTO_ADVANCE_KEY) === "1";
    } catch {
      return false;  // private mode, or site data blocked
    }
  };
  const setAutoAdvance = (on) => {
    try {
      localStorage.setItem(AUTO_ADVANCE_KEY, on ? "1" : "0");
    } catch { /* preference is a convenience, never a requirement */ }
    syncAutoAdvance();
  };
  // the ratebar is replaced on every rating, so the checkbox has to be
  // re-synced from the stored preference after each swap
  const syncAutoAdvance = () => {
    const box = document.querySelector("[data-auto-advance]");
    if (box) box.checked = autoAdvanceOn();
  };

  // Only the visible montage carries a src; the others wait here until first
  // shown, so a component page loads one image instead of three.
  const showAxis = (axis) => {
    document.querySelectorAll("[data-axis-btn]").forEach((b) =>
      b.classList.toggle("active", b.dataset.axisBtn === axis));
    document.querySelectorAll("[data-axis-img]").forEach((img) => {
      const active = img.dataset.axisImg === axis;
      if (active && !img.getAttribute("src") && img.dataset.src) {
        img.src = img.dataset.src;
      }
      img.classList.toggle("active", active);
    });
  };

  document.addEventListener("click", (e) => {
    const ax = e.target.closest("[data-axis-btn]");
    if (ax) { showAxis(ax.dataset.axisBtn); return; }
    const box = e.target.closest("[data-auto-advance]");
    if (box) setAutoAdvance(box.checked);
  });

  const goNext = () => {
    const bar = document.getElementById("ratebar");
    const next = bar && bar.dataset.nextUrl;
    if (next) window.location = next;
  };

  document.addEventListener("htmx:afterSwap", (e) => {
    if (e.target.id !== "ratebar") return;
    syncAutoAdvance();
    if (autoAdvanceOn()) goNext();
  });

  // Text entry swallows the shortcuts; a focused checkbox must not — clicking
  // the auto-advance box would otherwise silently disable the number keys
  // until you clicked somewhere else.
  const TYPING_TYPES = ["checkbox", "radio", "button", "submit", "reset"];
  const isTyping = (el) =>
    el.tagName === "TEXTAREA" ||
    el.isContentEditable ||
    (el.tagName === "INPUT" && !TYPING_TYPES.includes(el.type));

  document.addEventListener("keydown", (e) => {
    if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    const rate = (label) =>
      document.querySelector(`#ratebar [data-rate="${label}"]`)?.click();
    const go = (id) => {
      const a = document.getElementById(id);
      if (a && a.href) window.location = a.href;
    };
    if (e.key === "ArrowRight") go("nav-next");
    else if (e.key === "ArrowLeft") go("nav-prev");
    else if (e.key === "1" || e.key === "s") rate("Signal");
    else if (e.key === "2" || e.key === "u") rate("Unknown");
    else if (e.key === "3" || e.key === "n") rate("Noise");
    else if (e.key === "a") setAutoAdvance(!autoAdvanceOn());
    else if (e.key === "g") {
      const jump = document.getElementById("jump");
      if (jump) {
        jump.focus();
        e.preventDefault();
      }
    }
  });

  const jump = document.getElementById("jump");
  if (jump) {
    jump.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const n = parseInt(jump.value, 10);
      if (n && jump.dataset.urlTemplate) {
        window.location = jump.dataset.urlTemplate.replace("999999", String(n));
      }
    });
  }

  syncAutoAdvance();
})();
