// melrater — montage switchers, keyboard shortcuts, jump box, auto-advance.
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
  // shown, so a component page loads one image instead of twelve. The three
  // choices — axis, background, smoothing — are independent, so a swap of one
  // keeps the other two.
  const showMontage = ({ bg, axis, sm }) => {
    document.querySelectorAll("[data-axis-btn]").forEach((b) =>
      b.classList.toggle("active", b.dataset.axisBtn === axis));
    document.querySelectorAll("[data-bg-btn]").forEach((b) =>
      b.classList.toggle("active", b.dataset.bgBtn === bg));
    document.querySelectorAll("[data-sm-btn]").forEach((b) =>
      b.classList.toggle("active", b.dataset.smBtn === sm));
    document.querySelectorAll("[data-bg-caption]").forEach((c) =>
      c.classList.toggle("active", c.dataset.bgCaption === bg));
    document.querySelectorAll("[data-sm-caption]").forEach((c) =>
      c.classList.toggle("active", c.dataset.smCaption === sm));
    document.querySelectorAll("[data-axis-frame]").forEach((f) =>
      f.classList.toggle("active", f.dataset.axisFrame === axis));
    document.querySelectorAll("[data-axis-img]").forEach((img) => {
      const active = img.dataset.axisImg === axis &&
        img.dataset.bgImg === bg && img.dataset.smImg === sm;
      if (active && !img.getAttribute("src") && img.dataset.src) {
        img.src = img.dataset.src;
      }
      img.classList.toggle("active", active);
    });
  };

  const currentAxis = () =>
    document.querySelector("[data-axis-btn].active")?.dataset.axisBtn;
  // a run with a single background (or smoothing level) renders no buttons
  // for it at all, so fall back to whichever montage is on screen
  const currentBg = () =>
    document.querySelector("[data-bg-btn].active")?.dataset.bgBtn ??
    document.querySelector("[data-axis-img].active")?.dataset.bgImg;
  const currentSm = () =>
    document.querySelector("[data-sm-btn].active")?.dataset.smBtn ??
    document.querySelector("[data-axis-img].active")?.dataset.smImg;
  const current = () => ({ bg: currentBg(), axis: currentAxis(), sm: currentSm() });

  // Montages are encoded at voxel resolution and shown at twice that. On a
  // window wide enough, pin the frame to exactly 2x the image's own width so
  // the nearest-neighbour upscale (image-rendering: pixelated) lands on whole
  // pixels; narrower windows fall back to the stylesheet's cap. The functional
  // image is the reference: the anatomical is finer and is shown at the same
  // size, so swapping backgrounds does not move the page.
  const pinFrame = (img) => {
    if (img.dataset.bgImg !== "func" || !img.naturalWidth) return;
    const frame = img.closest("[data-axis-frame]");
    if (frame) frame.style.maxWidth = `${2 * img.naturalWidth}px`;
  };
  document.addEventListener("load", (e) => {
    if (e.target.matches?.("img[data-axis-img]")) pinFrame(e.target);
  }, true);
  document.querySelectorAll("img[data-axis-img]").forEach((img) => {
    if (img.complete) pinFrame(img);
  });

  document.addEventListener("click", (e) => {
    const ax = e.target.closest("[data-axis-btn]");
    if (ax) { showMontage({ ...current(), axis: ax.dataset.axisBtn }); return; }
    const bg = e.target.closest("[data-bg-btn]");
    if (bg) { showMontage({ ...current(), bg: bg.dataset.bgBtn }); return; }
    const sm = e.target.closest("[data-sm-btn]");
    if (sm) { showMontage({ ...current(), sm: sm.dataset.smBtn }); return; }
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

  const cycle = (selector) => {
    const btns = [...document.querySelectorAll(selector)];
    if (btns.length > 1) {
      const at = btns.findIndex((b) => b.classList.contains("active"));
      btns[(at + 1) % btns.length].click();
    }
  };

  document.addEventListener("keydown", (e) => {
    // Before the isTyping bail, or it could never fire: escape is the way back
    // out of the note box, and blurring it is what triggers htmx's save.
    if (e.key === "Escape" && e.target.id === "note-text") {
      e.target.blur();
      return;
    }
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
    // Cycle rather than toggle, and go through .click() rather than calling
    // showMontage: that one path fires both the visual swap and htmx's
    // preference POST, so the choice survives navigation.
    else if (e.key === "b") cycle("[data-bg-btn]");
    else if (e.key === "m") cycle("[data-sm-btn]");
    else if (e.key === "o") cycle("[data-axis-btn]");
    // Both re-query the element rather than holding a reference: the note card
    // is replaced out of band on every rating.
    else if (e.key === "g") {
      const jump = document.getElementById("jump");
      if (jump) {
        jump.focus();
        e.preventDefault();
      }
    } else if (e.key === "c") {
      const note = document.getElementById("note-text");
      if (note) {
        note.focus();
        e.preventDefault();  // or the c lands in the box
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
