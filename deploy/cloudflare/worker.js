// The production page on Cloudflare Workers, for people outside the office (reins report deploy / push).
// Viewers need an access code: a link ...?k=CODE sets a cookie for a year. Codes are the Worker secret VIEW_TOKENS
// (comma-separated). The page's data and workbook are KV values pushed from the machine running reins:
//   production  -> /api/production (JSON, the same as the board's)      xlsx -> /production.xlsx
const COOKIE = "rk";

const LOGIN = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Production</title><style>
:root{--bg:#faf9f6;--panel:#fff;--text:#1f1e1c;--muted:#6b6963;--border:#e7e5dd;--accent:#c96442}
@media (prefers-color-scheme:dark){:root{--bg:#1b1a18;--panel:#232220;--text:#ebe9e3;--muted:#a8a59c;--border:#34322e;--accent:#d97757}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,"Segoe UI",Inter,sans-serif;padding:16px}
form{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:28px;max-width:360px;width:100%}
h1{font-size:20px;margin:0 0 6px}p{color:var(--muted);margin:0 0 18px;font-size:14px}
input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--border);border-radius:9px;background:var(--bg);color:var(--text);font:inherit}
button{margin-top:12px;width:100%;padding:10px;border:0;border-radius:9px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}
</style></head><body><form method="get" action="/"><h1>Production</h1><p>Enter the access code you were given.</p>
<input name="k" autocomplete="off" required autofocus><button>Open</button></form></body></html>`;

function same(a, b) {                       // constant-time string compare
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const codes = (env.VIEW_TOKENS || "").split(",").map((s) => s.trim()).filter((s) => s.length >= 16);
    const valid = (c) => !!c && codes.some((t) => same(t, c));
    const given = url.searchParams.get("k");
    if (given !== null) {
      if (!valid(given)) return new Response(LOGIN, { status: 401, headers: { "content-type": "text/html; charset=utf-8" } });
      url.searchParams.delete("k");
      return new Response(null, { status: 302, headers: { Location: url.pathname + url.search,
        "Set-Cookie": `${COOKIE}=${encodeURIComponent(given)}; Path=/; Max-Age=31536000; HttpOnly; Secure; SameSite=Lax` } });
    }
    const m = (req.headers.get("Cookie") || "").match(new RegExp(`(?:^|;\\s*)${COOKIE}=([^;]+)`));
    if (!valid(m && decodeURIComponent(m[1])))
      return new Response(LOGIN, { status: 401, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });

    const priv = { "cache-control": "private, no-store", "x-robots-tag": "noindex" };
    if (url.pathname === "/api/production") {
      const v = await env.REPORT.get("production");
      return new Response(v || "{}", { headers: { "content-type": "application/json; charset=utf-8", ...priv } });
    }
    if (url.pathname === "/production.xlsx") {
      const b = await env.REPORT.get("xlsx", "arrayBuffer");
      if (!b) return new Response("not published yet", { status: 404 });
      return new Response(b, { headers: { ...priv, "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-disposition": 'attachment; filename="production.xlsx"' } });
    }
    const path = url.pathname === "/" ? "/production" : url.pathname;     // assets serve production.html at /production
    const r = await env.ASSETS.fetch(new Request(new URL(path, url), req));
    const h = new Headers(r.headers); for (const [k, v] of Object.entries(priv)) h.set(k, v);
    return new Response(r.body, { status: r.status, headers: h });
  },
};
