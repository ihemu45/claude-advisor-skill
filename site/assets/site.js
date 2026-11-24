/* dealgrep front-end. Vanilla JS, no build step. Reads data/*.json produced by the agent. */
(() => {
  "use strict";
  const html = document.documentElement;
  const ROOT = html.getAttribute("data-root") || "./";
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

  /* ---------- theme ---------- */
  const THEME_KEY = "dealgrep.theme";
  function getStoredTheme() { try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; } }
  function applyTheme(t) {
    if (t === "light") html.setAttribute("data-theme", "light"); else html.removeAttribute("data-theme");
    $$("[data-theme-toggle]").forEach(b => { b.innerHTML = '<span class="t">theme: </span><span class="v">' + (t === "light" ? "light" : "dark") + "</span>"; b.setAttribute("aria-label", "Switch to " + (t === "light" ? "dark" : "light") + " theme"); });
  }
  applyTheme(getStoredTheme() || "dark");
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-theme-toggle]");
    if (!b) return;
    const next = html.getAttribute("data-theme") === "light" ? "dark" : "light";
    try { localStorage.setItem(THEME_KEY, next); } catch (err) { /* private mode */ }
    applyTheme(next);
  });

  /* ---------- helpers ---------- */
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = u => (typeof u === "string" && /^https?:\/\//i.test(u)) ? u : null;
  const money = v => (typeof v === "number" && isFinite(v)) ? "$" + v.toLocaleString("en-US", { minimumFractionDigits: v % 1 ? 2 : 0, maximumFractionDigits: 2 }) : null;
  const num = v => (typeof v === "number" && isFinite(v)) ? v.toLocaleString("en-US") : "–";
  function relTime(iso) {
    const t = Date.parse(iso || "");
    if (!t) return "unknown";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  const fmtDate = iso => { const d = new Date(iso); return isNaN(d) ? "–" : d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); };
  const slugName = (meta, slug) => { const c = (meta.categories || []).find(x => x.slug === slug); return c ? c.name : slug; };
  const storeName = (meta, slug) => (meta.stores && meta.stores[slug] && meta.stores[slug].name) || slug;
  const param = (k, d = "") => new URLSearchParams(location.search).get(k) ?? d;
  let toastTimer;
  function toast(msg) {
    let el = $(".toast");
    if (!el) { el = document.createElement("div"); el.className = "toast"; el.setAttribute("role", "status"); document.body.appendChild(el); }
    el.textContent = msg; el.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), 1600);
  }

  /* ---------- data ---------- */
  let cache;
  async function fetchJson(name) {
    const r = await fetch(ROOT + "data/" + name + "?t=" + Math.floor(Date.now() / 300000), { cache: "no-cache" });
    if (!r.ok) throw new Error(name + " " + r.status);
    return r.json();
  }
  function load() {
    if (!cache) {
      cache = Promise.all([fetchJson("deals.json"), fetchJson("meta.json"), fetchJson("runs.json").catch(() => [])])
        .then(([d, meta, runs]) => ({ deals: Array.isArray(d.deals) ? d.deals : [], meta: meta || {}, runs: Array.isArray(runs) ? runs : [], sample: !!d.sample }));
    }
    return cache;
  }

  /* ---------- shared chrome ---------- */
  function renderStatus({ meta, sample }) {
    const el = $("[data-statusline]");
    if (!el) return;
    const src = meta.sources || [];
    const active = src.filter(s => !s.skipped);
    const ok = active.filter(s => s.ok).length;
    const r = meta.research || {};
    const age = Date.now() - Date.parse(meta.generated_at || 0);
    const dot = sample ? "warn" : (age > 3 * 3600e3 ? "bad" : (ok < active.length ? "warn" : ""));
    const research = r.enabled ? `research: <b>on</b> (${esc((r.categories || []).join(", "))})` : `research: <b>off</b>`;
    el.innerHTML = `<span><i class="dot ${dot}"></i>last refresh <b title="${esc(meta.generated_at)}">${esc(relTime(meta.generated_at))}</b></span>` +
      `<span><b>${num(meta.total)}</b> deals</span>` +
      `<span><b>${num(meta.new_this_run)}</b> new last run</span>` +
      `<span>sources <b>${ok}/${active.length}</b> ok</span>` +
      `<span>${research}</span>` +
      (sample ? `<span class="warn">sample data</span>` : "");
    const banner = $("[data-sample-banner]");
    if (banner) banner.hidden = !sample;
  }
  function markNav() {
    const page = document.body.getAttribute("data-page");
    $$(".nav a").forEach(a => { if (a.getAttribute("data-nav") === page) a.setAttribute("aria-current", "page"); });
  }

  /* ---------- deal card ---------- */
  function heatMeter(h) {
    const level = Math.max(0, Math.min(5, Math.round((h || 0) / 20)));
    let bars = "";
    for (let i = 1; i <= 5; i++) bars += `<i class="${i <= level ? "on" : ""}"></i>`;
    return `<span class="heat h${level}" title="heat ${h}/100">${bars}<span>${h}</span></span>`;
  }
  function dealCard(d, meta) {
    const url = safeUrl(d.url);
    const src = safeUrl(d.source_url);
    const price = money(d.price);
    const list = money(d.list_price);
    const tags = (d.tags || []).filter(t => t !== "coupon");
    const hotTag = t => (t === "black-friday" || t === "cyber-monday" || t === "lightning" || t === "today-only" || t === "price-drop") ? "hot" : (t === "new" ? "new" : (t === "researched" ? "researched" : ""));
    const title = url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(d.title)}</a>` : esc(d.title);
    const hist = (d.price_history || []).length > 1 ? ` · seen ${esc(String(d.seen_count || 1))}×` : "";
    return `<article class="card deal" data-id="${esc(d.id)}">
      <div class="top">
        <span class="badge store store-${esc(d.store)}">${esc(d.store_name || storeName(meta, d.store))}</span>
        <a class="badge cat" href="${ROOT}deals.html?cat=${encodeURIComponent(d.category)}">${esc(slugName(meta, d.category))}</a>
        ${heatMeter(d.heat)}
      </div>
      <h3>${title}</h3>
      <div class="price-row">
        ${price ? `<span class="price">${esc(price)}</span>` : `<span class="price none">price on site</span>`}
        ${list ? `<s class="list">${esc(list)}</s>` : ""}
        ${d.discount_pct ? `<span class="pct">-${esc(String(d.discount_pct))}%</span>` : ""}
        ${d.coupon ? `<span class="coupon">${esc(d.coupon)}<button type="button" data-copy="${esc(d.coupon)}" title="copy code">copy</button></span>` : ""}
      </div>
      ${d.note ? `<p class="small muted" style="margin:0">${esc(d.note)}</p>` : ""}
      ${tags.length ? `<div class="tags">${tags.map(t => `<span class="tag ${hotTag(t)}">#${esc(t)}</span>`).join("")}</div>` : ""}
      <div class="meta">
        <span title="${esc(d.first_seen)}">first seen ${esc(relTime(d.first_seen))}${hist}</span>
        <span>via ${(d.sources || []).map(s => esc(s)).join(", ")}</span>
        ${src ? `<a href="${esc(src)}" target="_blank" rel="noopener noreferrer nofollow">thread ↗</a>` : ""}
        ${d.expires ? `<span class="warn">ends ${esc(d.expires)}</span>` : ""}
      </div>
    </article>`;
  }
  document.addEventListener("click", async e => {
    const b = e.target.closest("[data-copy]");
    if (!b) return;
    const code = b.getAttribute("data-copy");
    try { await navigator.clipboard.writeText(code); toast("copied " + code); }
    catch (err) { toast("code: " + code); }
  });

  /* ---------- pages ---------- */
  const pages = {};

  pages.home = async data => {
    const { deals, meta, runs } = data;
    const stats = $("[data-stats]");
    if (stats) {
      const avg = deals.filter(d => d.discount_pct).reduce((a, d) => a + d.discount_pct, 0) / Math.max(1, deals.filter(d => d.discount_pct).length);
      stats.innerHTML = [
        ["deals tracked", num(meta.total), `across ${Object.values(meta.by_store || {}).filter(Boolean).length} stores`],
        ["new last run", num(meta.new_this_run), `refreshed ${relTime(meta.generated_at)}`],
        ["with coupon code", num(meta.with_coupon), "codes shown on the card"],
        ["avg discount", isFinite(avg) && avg ? Math.round(avg) + "%" : "–", `${num(meta.black_friday)} tagged black friday`],
      ].map(([l, v, s]) => `<div class="stat"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="sub">${esc(s)}</div></div>`).join("");
    }
    const hot = $("[data-hot]");
    if (hot) hot.innerHTML = deals.slice(0, 9).map(d => dealCard(d, meta)).join("") || `<div class="empty">no deals yet — the agent has not run</div>`;
    const cats = $("[data-categories]");
    if (cats) cats.innerHTML = (meta.categories || []).filter(c => c.slug !== "other" || (meta.by_category || {})[c.slug]).map(c => {
      const n = (meta.by_category || {})[c.slug] || 0;
      return `<a class="card" href="${ROOT}deals.html?cat=${encodeURIComponent(c.slug)}"><h3>${esc(c.name)}</h3><p class="small muted" style="margin:0">${esc(c.blurb)}</p><span class="count">${num(n)} deals →</span></a>`;
    }).join("");
    const stores = $("[data-stores]");
    if (stores) stores.innerHTML = Object.entries(meta.by_store || {}).slice(0, 8).map(([slug, n]) => `<a class="chip" href="${ROOT}deals.html?store=${encodeURIComponent(slug)}">${esc(storeName(meta, slug))} <b>${num(n)}</b></a>`).join("");
    const term = $("[data-term]");
    if (term) {
      const r = meta.research || {};
      const src = (meta.sources || []).filter(s => !s.skipped);
      const lines = [
        `<span class="p">$</span> dealgrep <span class="k">--refresh</span> <span class="c"># ${esc(fmtDate(meta.generated_at))} UTC</span>`,
        ...src.map(s => `  <span class="${s.ok ? "p" : "c"}">${s.ok ? "✓" : "✗"}</span> ${esc(s.name.padEnd(22))} <span class="v">${String(s.count).padStart(4)}</span> items ${s.error ? `<span class="c">(${esc(s.error)})</span>` : ""}`),
        r.enabled ? `  <span class="p">✓</span> claude research        <span class="v">${String(r.found || 0).padStart(4)}</span> items <span class="c">(${esc((r.categories || []).join(", "))})</span>` : `  <span class="c">– claude research       off (${esc(r.skipped || "no key")})</span>`,
        `<b>${num(meta.total)} deals</b> · <b>${num(meta.new_this_run)}</b> new · next run in ~1h`,
      ];
      term.innerHTML = lines.join("\n");
    }
    const last = $("[data-last-run]");
    if (last && runs[0]) last.textContent = `last run ${relTime(runs[0].at)} · ${runs[0].sources_ok}/${runs[0].sources_total} sources · ${runs[0].duration_ms} ms`;
  };

  pages.deals = async data => {
    const { deals, meta } = data;
    const state = {
      cat: param("cat"), store: param("store"), q: param("q"), sort: param("sort", "heat"),
      min: parseInt(param("min", "0"), 10) || 0, coupon: param("coupon") === "1", bf: param("bf") === "1", src: param("src"),
    };
    const PAGE = 48;
    let shown = PAGE;

    const side = $("[data-sidebar]");
    const search = $("[data-search]");
    const storeSel = $("[data-store]");
    const sortSel = $("[data-sort]");
    const minSel = $("[data-min]");
    const chipCoupon = $("[data-chip='coupon']");
    const chipBf = $("[data-chip='bf']");
    const results = $("[data-results]");
    const head = $("[data-results-head]");
    const compare = $("[data-compare]");
    const more = $("[data-more]");

    storeSel.innerHTML = `<option value="">all stores</option>` + Object.entries(meta.by_store || {}).map(([s, n]) => `<option value="${esc(s)}">${esc(storeName(meta, s))} (${n})</option>`).join("");
    storeSel.value = state.store;
    search.value = state.q;
    sortSel.value = state.sort;
    minSel.value = String(state.min);

    function syncUrl() {
      const p = new URLSearchParams();
      if (state.cat) p.set("cat", state.cat);
      if (state.store) p.set("store", state.store);
      if (state.q) p.set("q", state.q);
      if (state.sort !== "heat") p.set("sort", state.sort);
      if (state.min) p.set("min", String(state.min));
      if (state.coupon) p.set("coupon", "1");
      if (state.bf) p.set("bf", "1");
      if (state.src) p.set("src", state.src);
      const qs = p.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
    }
    function renderSidebar() {
      const counts = {};
      deals.forEach(d => { if (!state.store || d.store === state.store) counts[d.category] = (counts[d.category] || 0) + 1; });
      const total = Object.values(counts).reduce((a, b) => a + b, 0);
      const link = (slug, name, n) => `<a href="?cat=${encodeURIComponent(slug)}" data-cat="${esc(slug)}" aria-current="${state.cat === slug}"><span>${esc(name)}</span><span class="n">${n}</span></a>`;
      side.innerHTML = `<div class="side-title">categories</div>` + link("", "all categories", total) +
        (meta.categories || []).filter(c => counts[c.slug]).map(c => link(c.slug, c.name, counts[c.slug] || 0)).join("");
    }
    function filtered() {
      const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
      return deals.filter(d => {
        if (state.cat && d.category !== state.cat) return false;
        if (state.store && d.store !== state.store) return false;
        if (state.min && (d.discount_pct || 0) < state.min) return false;
        if (state.coupon && !d.coupon) return false;
        if (state.bf && !(d.tags || []).some(t => t === "black-friday" || t === "cyber-monday")) return false;
        if (state.src && !(d.sources || []).includes(state.src)) return false;
        if (terms.length) {
          const hay = (d.title + " " + (d.store_name || "") + " " + d.store + " " + d.category + " " + (d.coupon || "") + " " + (d.tags || []).join(" ") + " " + (d.note || "")).toLowerCase();
          if (!terms.every(t => hay.includes(t))) return false;
        }
        return true;
      });
    }
    const sorters = {
      heat: (a, b) => b.heat - a.heat || (b.discount_pct || 0) - (a.discount_pct || 0),
      discount: (a, b) => (b.discount_pct || 0) - (a.discount_pct || 0) || b.heat - a.heat,
      "price-asc": (a, b) => (a.price ?? 1e12) - (b.price ?? 1e12),
      "price-desc": (a, b) => (b.price ?? -1) - (a.price ?? -1),
      newest: (a, b) => Date.parse(b.first_seen || 0) - Date.parse(a.first_seen || 0),
    };
    function render() {
      const list = filtered().sort(sorters[state.sort] || sorters.heat);
      const label = [state.cat ? slugName(meta, state.cat) : "all categories", state.store ? storeName(meta, state.store) : null, state.q ? `"${state.q}"` : null].filter(Boolean).join(" · ");
      head.innerHTML = `<h2>${esc(label)}</h2><span>${num(list.length)} ${list.length === 1 ? "deal" : "deals"}${state.min ? ` · ≥${state.min}% off` : ""}</span>`;
      if (state.q && list.length) {
        const best = {};
        list.forEach(d => { if (typeof d.price === "number" && (!best[d.store] || d.price < best[d.store].price)) best[d.store] = d; });
        const entries = Object.entries(best).sort((a, b) => a[1].price - b[1].price);
        compare.hidden = entries.length === 0;
        compare.innerHTML = `<span class="label">lowest price by store:</span>` + entries.map(([s, d], i) => `<a class="item ${i === 0 ? "best" : ""}" href="${esc(safeUrl(d.url) || "#")}" target="_blank" rel="noopener noreferrer nofollow"><span class="badge store store-${esc(s)}">${esc(storeName(meta, s))}</span><b>${esc(money(d.price))}</b>${d.coupon ? `<span class="muted">w/ ${esc(d.coupon)}</span>` : ""}</a>`).join("");
      } else { compare.hidden = true; compare.innerHTML = ""; }
      if (!list.length) {
        results.innerHTML = `<div class="empty">no matches.<br><span class="small">try fewer words, another store, or lower the minimum discount.</span></div>`;
        more.hidden = true;
      } else {
        results.innerHTML = list.slice(0, shown).map(d => dealCard(d, meta)).join("");
        more.hidden = shown >= list.length;
        more.textContent = `show ${Math.min(PAGE, list.length - shown)} more (${list.length - shown} left)`;
      }
      chipCoupon.setAttribute("aria-pressed", String(state.coupon));
      chipBf.setAttribute("aria-pressed", String(state.bf));
      renderSidebar();
      syncUrl();
      document.title = (label === "all categories" ? "Browse deals" : label) + " · dealgrep";
    }
    side.addEventListener("click", e => { const a = e.target.closest("a[data-cat]"); if (!a) return; e.preventDefault(); state.cat = a.getAttribute("data-cat"); shown = PAGE; render(); });
    let t;
    search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = search.value.trim(); shown = PAGE; render(); }, 120); });
    storeSel.addEventListener("change", () => { state.store = storeSel.value; shown = PAGE; render(); });
    sortSel.addEventListener("change", () => { state.sort = sortSel.value; render(); });
    minSel.addEventListener("change", () => { state.min = parseInt(minSel.value, 10) || 0; shown = PAGE; render(); });
    chipCoupon.addEventListener("click", () => { state.coupon = !state.coupon; shown = PAGE; render(); });
    chipBf.addEventListener("click", () => { state.bf = !state.bf; shown = PAGE; render(); });
    more.addEventListener("click", () => { shown += PAGE; render(); });
    $("[data-reset]").addEventListener("click", () => { Object.assign(state, { cat: "", store: "", q: "", sort: "heat", min: 0, coupon: false, bf: false, src: "" }); search.value = ""; storeSel.value = ""; sortSel.value = "heat"; minSel.value = "0"; shown = PAGE; render(); });
    document.addEventListener("keydown", e => { if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) { e.preventDefault(); search.focus(); } });
    render();
  };

  pages.stores = async data => {
    const { deals, meta } = data;
    const el = $("[data-store-grid]");
    const by = {};
    deals.forEach(d => { (by[d.store] = by[d.store] || []).push(d); });
    const cards = Object.entries(by).sort((a, b) => b[1].length - a[1].length).map(([slug, list]) => {
      const withPct = list.filter(d => d.discount_pct);
      const avg = withPct.length ? Math.round(withPct.reduce((a, d) => a + d.discount_pct, 0) / withPct.length) : null;
      const best = list.slice().sort((a, b) => b.heat - a.heat)[0];
      const cats = {};
      list.forEach(d => { cats[d.category] = (cats[d.category] || 0) + 1; });
      const topCats = Object.entries(cats).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([c, n]) => `${esc(slugName(meta, c))} (${n})`).join(", ");
      const home = meta.stores && meta.stores[slug] && safeUrl(meta.stores[slug].homepage);
      return `<div class="card">
        <div class="top" style="display:flex;gap:8px;align-items:center;font-family:var(--mono);font-size:11px"><span class="badge store store-${esc(slug)}">${esc(storeName(meta, slug))}</span>${home ? `<a class="muted" href="${esc(home)}" target="_blank" rel="noopener noreferrer nofollow">${esc(home.replace(/^https?:\/\//, ""))} ↗</a>` : ""}</div>
        <div class="price-row"><span class="price">${list.length}</span><span class="muted mono small">deals</span>${avg ? `<span class="pct">avg -${avg}%</span>` : ""}</div>
        <p class="small muted" style="margin:0">top: ${topCats}</p>
        ${best ? `<p class="small" style="margin:0">hottest: <a href="${esc(safeUrl(best.url) || "#")}" target="_blank" rel="noopener noreferrer nofollow">${esc(best.title)}</a>${money(best.price) ? ` <span class="mono">${esc(money(best.price))}</span>` : ""}</p>` : ""}
        <a class="btn small" href="${ROOT}deals.html?store=${encodeURIComponent(slug)}">browse ${esc(storeName(meta, slug))} →</a>
      </div>`;
    });
    el.innerHTML = cards.join("") || `<div class="empty">no stores yet</div>`;
    const srcEl = $("[data-source-table]");
    if (srcEl) {
      srcEl.innerHTML = (meta.sources || []).map(s => `<tr><td class="mono">${esc(s.name)}</td><td>${s.skipped ? `<span class="muted">skipped: ${esc(s.skipped)}</span>` : (s.ok ? `<span class="ok">ok</span>` : `<span class="bad">failed</span>`)}</td><td class="num">${s.count}</td><td class="num">${s.ms}</td><td class="small muted">${esc(s.error || "")}</td></tr>`).join("");
    }
  };

  pages.changelog = async data => {
    const { runs } = data;
    const el = $("[data-runs]");
    if (!el) return;
    if (!runs.length) { el.innerHTML = `<tr><td colspan="7" class="muted">no runs recorded yet</td></tr>`; return; }
    el.innerHTML = runs.map(r => {
      const res = r.research || {};
      const errs = [...(r.source_errors || []), ...(res.errors || [])];
      return `<tr>
        <td class="mono" title="${esc(r.at)}">${esc(fmtDate(r.at))}${r.sample ? ` <span class="warn">sample</span>` : ""}</td>
        <td class="num">${num(r.total)}</td><td class="num">${num(r.new)}</td>
        <td class="num"><span class="${r.sources_ok === r.sources_total ? "ok" : (r.sources_ok ? "warn" : "bad")}">${r.sources_ok}/${r.sources_total}</span></td>
        <td class="mono small">${res.enabled ? `${esc((res.categories || []).join(", "))} → ${res.found} <span class="muted">(${res.web_searches || 0} searches)</span>` : `<span class="muted">off</span>`}</td>
        <td class="num">${num(r.duration_ms)}</td>
        <td class="small ${errs.length ? "warn" : "muted"}">${errs.length ? esc(errs.join("; ")) : "–"}</td>
      </tr>`;
    }).join("");
  };

  /* ---------- boot ---------- */
  markNav();
  const page = document.body.getAttribute("data-page");
  load().then(data => {
    renderStatus(data);
    if (pages[page]) return pages[page](data);
  }).catch(err => {
    console.error(err);
    const el = $("[data-statusline]");
    if (el) el.innerHTML = `<span><i class="dot bad"></i>could not load data (${esc(err.message)})</span>`;
    $$("[data-hot],[data-results],[data-store-grid]").forEach(x => { x.innerHTML = `<div class="empty">data failed to load. if you opened this file directly, serve the folder over http.</div>`; });
  });
})();
