// melrater — montage switchers, keyboard shortcuts, jump box.
// State lives on the backend; this only toggles pre-rendered content and
// clicks htmx-wired buttons.
//
// There is deliberately no auto-advance: rating reveals FIX's verdict on the
// component just rated, and moving on the moment the rating landed would
// navigate away from it before it could be read.
(() => {
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
  });

  // Text entry swallows the shortcuts; a focused checkbox or button must not —
  // clicking one would otherwise silently disable the number keys until you
  // clicked somewhere else.
  const TYPING_TYPES = ["checkbox", "radio", "button", "submit", "reset"];
  const isTyping = (el) =>
    el.tagName === "TEXTAREA" ||
    el.isContentEditable ||
    (el.tagName === "INPUT" && !TYPING_TYPES.includes(el.type));

  // Which key does what is keys.py's business, rendered into the page by
  // {% key_sheet %} beside the hints it also draws; this file implements the
  // actions and never names a key. No map (run list, login): no shortcuts.
  const mapEl = document.getElementById("keymap");
  const KEYMAP = mapEl ? JSON.parse(mapEl.textContent) : {};

  // Physical keys: e.code ignores Caps Lock and the layout. A lone character
  // ("?") is the exception, matched on e.key, since which key prints it
  // depends on the layout. Code names are never one character, so the two
  // cannot collide — and the length test keeps Shift+ArrowRight from falling
  // back to ArrowRight.
  const actionFor = (e) =>
    KEYMAP[(e.shiftKey ? "Shift+" : "") + e.code] ??
    (e.key.length === 1 ? KEYMAP[e.key] : undefined);

  // Cycle rather than toggle, and go through .click() rather than calling
  // showMontage: that one path fires both the visual swap and htmx's
  // preference POST, so the choice survives navigation.
  const cycle = (selector, step) => {
    const btns = [...document.querySelectorAll(selector)];
    if (btns.length < 2) return;
    const at = Math.max(0, btns.findIndex((b) => b.classList.contains("active")));
    btns[(at + step + btns.length) % btns.length].click();
  };
  const rate = (label) =>
    document.querySelector(`#ratebar [data-rate="${label}"]`)?.click();
  // Until the next page commits, this one is still live and still gets keys:
  // a 1 pressed in that gap would rate the component being LEFT.
  let leaving = false;
  window.addEventListener("pageshow", (e) => { if (e.persisted) leaving = false; });
  // go() no-ops on a missing element, so a key whose target this page lacks
  // (the last component's "next", say) does nothing.
  const go = (id) => {
    const a = document.getElementById(id);
    if (!a?.href) return;
    // Space also separates words: a reviewer still typing after focus left the
    // composer would otherwise navigate away from an unposted draft.
    const draft = document.getElementById("post-body");
    if (draft?.value.trim()) {
      draft.focus();
      return;
    }
    leaving = true;
    window.location = a.href;
  };
  // Re-queried rather than held: the note card is replaced out of band on
  // every rating. The first that exists wins — the private note on the rating
  // tab, the composer on the discussion tab.
  const focusFirst = (...ids) =>
    ids.map((id) => document.getElementById(id)).find(Boolean)?.focus();
  const step = (e) => (e.shiftKey ? -1 : 1);

  const ACTIONS = {
    "rate-signal": () => rate("Signal"),
    "rate-unknown": () => rate("Unknown"),
    "rate-noise": () => rate("Noise"),
    "cycle-plane": (e) => cycle("[data-axis-btn]", step(e)),
    "cycle-background": (e) => cycle("[data-bg-btn]", step(e)),
    "cycle-smoothing": (e) => cycle("[data-sm-btn]", step(e)),
    "next": () => go("nav-next"),
    "prev": () => go("nav-prev"),
    "other-tab": () => go("nav-tab-other"),
    "focus-text": () => focusFirst("note-text", "post-body"),
    "focus-jump": () => focusFirst("jump"),
  };

  // Codes whose keydown this handler took. Their keyup is ours too: some
  // engines press a focused button on Space's keyup whatever keydown did.
  const taken = new Set();

  document.addEventListener("keydown", (e) => {
    if (e.isComposing || e.metaKey || e.ctrlKey || e.altKey) return;
    const action = actionFor(e);
    if (!action) return;
    if (isTyping(e.target)) {
      // Escape is the way out of any text box. For the note box blurring is
      // also what triggers htmx's save; the go-to box is emptied so it does
      // not come back holding a half-typed number.
      if (action === "leave-text") {
        if (e.target.id === "jump") e.target.value = "";
        e.target.blur();
      } else if (e.repeat && taken.has(e.code)) {
        e.preventDefault();  // a held C or G focuses its box once, types nothing
      }
      return;
    }
    // not prevented: outside a text box Escape is the popovers' own close key
    if (action === "leave-text") return;
    // Space would scroll the last-clicked pane or press a focused button; any
    // other key would land in the box it just focused
    e.preventDefault();
    taken.add(e.code);
    // a held 3 would POST again and again, a held Space run through the run
    if (e.repeat || leaving) return;
    ACTIONS[action]?.(e);
  });
  document.addEventListener("keyup", (e) => {
    if (taken.delete(e.code)) e.preventDefault();
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
})();
