// melrater — axis switcher, keyboard shortcuts, jump box.
// State lives on the backend; this only toggles pre-rendered content and
// clicks htmx-wired buttons.
(() => {
  document.addEventListener("click", (e) => {
    const ax = e.target.closest("[data-axis-btn]");
    if (!ax) return;
    document.querySelectorAll("[data-axis-btn]").forEach((b) =>
      b.classList.toggle("active", b === ax));
    document.querySelectorAll("[data-axis-img]").forEach((img) =>
      img.classList.toggle("active", img.dataset.axisImg === ax.dataset.axisBtn));
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.metaKey || e.ctrlKey || e.altKey) return;
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
})();
