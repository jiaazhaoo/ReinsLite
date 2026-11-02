/* Search box on both board pages: any id (batch, case, path, issue #N, release, version, session) -> reins find. */
(function () {
  const bar = document.querySelector(".topbar, .pbar");
  if (!bar) return;
  const zh = () => (localStorage.getItem("lang") || document.documentElement.lang || "zh").startsWith("zh");
  const box = document.createElement("input");
  box.className = "findbox"; box.type = "search"; box.autocomplete = "off";
  box.placeholder = zh() ? "查编号：批次 / case / 路径 / #问题单" : "Find: batch / case / path / #issue";
  const spacer = bar.querySelector(".spacer, .pspacer");
  bar.insertBefore(box, spacer ? spacer.nextSibling : null);
  const panel = document.createElement("div");
  panel.className = "findpanel"; panel.hidden = true;
  document.body.appendChild(panel);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const link = v => {
    const s = String(v);
    return /^(#\d+|[a-z0-9]+(-[a-z0-9_]+)+-\d{8}-\d+|qa-\d{8}-\d+)$/.test(s) ? `<a href="#" data-q="${esc(s)}">${esc(s)}</a>` : esc(s);
  };
  const val = v => {
    if (v === null || v === undefined || v === "") return "<span class='muted'>—</span>";
    if (Array.isArray(v)) {
      if (!v.length) return "<span class='muted'>—</span>";
      return "<ul>" + v.slice(0, 40).map(x => "<li>" + (typeof x === "object" && x
        ? Object.entries(x).filter(([, y]) => y !== null && y !== "").map(([k, y]) => `<span class='muted'>${esc(k)}</span> ${link(typeof y === "object" ? JSON.stringify(y) : y)}`).join(" · ")
        : link(x)) + "</li>").join("") + (v.length > 40 ? `<li class='muted'>+${v.length - 40}</li>` : "") + "</ul>";
    }
    if (typeof v === "object") return "<table>" + Object.entries(v).map(([k, y]) => `<tr><th>${esc(k)}</th><td>${val(y)}</td></tr>`).join("") + "</table>";
    return link(v);
  };
  async function go(q) {
    q = q.trim(); if (!q) { panel.hidden = true; return; }
    box.value = q;
    panel.hidden = false; panel.innerHTML = `<div class='muted'>${zh() ? "查询中…" : "Searching…"}</div>`;
    try {
      const d = await (await fetch("api/find?q=" + encodeURIComponent(q))).json();
      const {kind, id, ...rest} = d;
      panel.innerHTML = `<div class='fhead'><b>${esc(kind)}</b> ${esc(id || q)}<button class='iconbtn fclose'>×</button></div>` + val(rest);
    } catch (e) {
      panel.innerHTML = `<div class='muted'>${esc(e)}</div>`;
    }
  }
  box.addEventListener("keydown", e => { if (e.key === "Enter" || e.keyCode === 13) go(box.value); if (e.key === "Escape") panel.hidden = true; });
  box.addEventListener("search", () => go(box.value));
  panel.addEventListener("click", e => {
    const a = e.target.closest("a[data-q]");
    if (a) { e.preventDefault(); go(a.dataset.q); }
    if (e.target.closest(".fclose")) panel.hidden = true;
  });
})();
