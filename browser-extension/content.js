/**
 * UKPropertyInsight overlay - injected into Rightmove/Zoopla/OnTheMarket
 * listing pages (see manifest.json's content_scripts.matches).
 *
 * Deliberately does NOT scrape each site's specific DOM structure for
 * the postcode - those change on every front-end redesign, which
 * would silently break the extension. Instead it scans the page's
 * visible text for a UK postcode pattern, which every listing page
 * displays somewhere regardless of markup, and is what these sites
 * themselves use for search/proximity - a slower but much more
 * durable approach than hardcoded selectors.
 *
 * Renders inside a Shadow DOM so the host page's global CSS can't
 * bleed into (or be broken by) the widget, and vice versa. A full-width
 * bar pinned to the top of the page (like Propbar), expanded by default
 * but height-capped so the underlying listing stays visible and
 * scrollable beneath it.
 *
 * Colours/type/spacing below are hardcoded to match
 * app/static/css/style.css's design tokens exactly - a Shadow DOM
 * with `:host { all: initial; }` can't inherit the host page's CSS
 * custom properties (there aren't any relevant ones on Rightmove/
 * Zoopla anyway), so there's no way to reference --accent etc.
 * directly; the values are just copied over instead.
 */
(function () {
  // TODO before publishing: replace with your actual deployed domain
  // (must match host_permissions in manifest.json).
  const API_BASE = "https://ukpropertyinsight.co.uk";

  const POSTCODE_RE = /\b([A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2})\b/i;
  const TOKEN_STORAGE_KEY = "pv_ext_token";

  const TABS = [
    { key: "overview", label: "Overview" },
    { key: "map", label: "Map" },
    { key: "market", label: "Market History" },
    { key: "comparables", label: "Comparables" },
    { key: "schools", label: "Schools" },
    { key: "epc", label: "EPC" },
    { key: "demographics", label: "Demographics" },
    { key: "crime", label: "Crime" },
  ];

  // Which tabs show a "log in to see the rest" gate when premium_unlocked
  // is false, and which payload key holds that tab's *_full_count.
  const GATED_TABS = { market: "market_history", comparables: "comparables", schools: "schools" };

  // Only the page's own title/meta tags are trusted for postcode
  // detection - NOT a scan of the whole page's visible text. Rightmove/
  // Zoopla/OnTheMarket listing pages routinely contain other real,
  // full postcodes that have nothing to do with the property being
  // viewed (the estate agent's branch office address, nearby schools,
  // "similar properties" widgets) - a body-text scan can and does pick
  // those up instead, silently showing another address's data. The
  // title/meta tags are reliably about THIS listing, but the sites
  // deliberately only show the outward code there (e.g. "BR5", not a
  // full postcode) to stop buyers looking the property up and
  // bypassing the agent - so a full-postcode match usually isn't even
  // possible from public listing text, and every lookup here should be
  // treated as best-effort until the user confirms it.
  const OUTWARD_RE = /\b([A-Z]{1,2}[0-9][A-Z0-9]?)\b/i;

  function extractLocation() {
    const metaCandidates = [
      'meta[property="og:street-address"]',
      'meta[name="address"]',
      'meta[property="og:title"]',
      "title",
    ];
    for (const selector of metaCandidates) {
      const el = document.querySelector(selector);
      const text = el ? el.getAttribute("content") || el.textContent : "";
      const full = text && text.match(POSTCODE_RE);
      if (full) return { postcode: normalizePostcode(full[1]), partial: false };
    }
    for (const selector of metaCandidates) {
      const el = document.querySelector(selector);
      const text = el ? el.getAttribute("content") || el.textContent : "";
      const outward = text && text.match(OUTWARD_RE);
      if (outward) return { postcode: normalizePostcode(outward[1]), partial: true };
    }
    return null;
  }

  function normalizePostcode(raw) {
    return raw.toUpperCase().replace(/\s+/g, " ").trim();
  }

  function gbp(amount) {
    if (amount === null || amount === undefined || amount === "") return "No data";
    const n = typeof amount === "string" ? parseFloat(amount) : amount;
    if (!isFinite(n)) return "No data";
    return "£" + Math.round(n).toLocaleString("en-GB");
  }

  function gbpCompact(amount) {
    const n = Number(amount) || 0;
    if (n >= 1000000) return "£" + (n / 1000000).toFixed(n % 1000000 === 0 ? 0 : 1) + "m";
    if (n >= 1000) return "£" + Math.round(n / 1000) + "k";
    return "£" + Math.round(n);
  }

  function distanceText(m) {
    if (m === null || m === undefined) return "";
    if (m < 1000) return Math.round(m) + " m";
    return (m / 1000).toFixed(1) + " km";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // --- Token storage (chrome.storage.local, not the page's own
  // localStorage - keeps it inaccessible to the host page's JS and
  // shared across every listing site, not just the current one). ---

  function getToken() {
    return new Promise(function (resolve) {
      if (!chrome?.storage?.local) return resolve(null);
      chrome.storage.local.get([TOKEN_STORAGE_KEY], function (result) {
        resolve(result[TOKEN_STORAGE_KEY] || null);
      });
    });
  }

  function setToken(token, email) {
    return new Promise(function (resolve) {
      if (!chrome?.storage?.local) return resolve();
      chrome.storage.local.set({ pv_ext_token: token, pv_ext_email: email }, resolve);
    });
  }

  function clearToken() {
    return new Promise(function (resolve) {
      if (!chrome?.storage?.local) return resolve();
      chrome.storage.local.remove(["pv_ext_token", "pv_ext_email"], resolve);
    });
  }

  // A user-typed postcode correction for THIS exact listing URL,
  // remembered so re-opening the same listing doesn't lose it. Keyed
  // per-URL (not globally) since a wrong auto-detect on one listing
  // says nothing about the postcode for a different one.
  function overrideKey() {
    return "pv_ext_pc_override:" + location.href;
  }

  function getPostcodeOverride() {
    return new Promise(function (resolve) {
      if (!chrome?.storage?.local) return resolve(null);
      const key = overrideKey();
      chrome.storage.local.get([key], function (result) { resolve(result[key] || null); });
    });
  }

  function setPostcodeOverride(postcode) {
    if (!chrome?.storage?.local) return;
    chrome.storage.local.set({ [overrideKey()]: postcode });
  }

  const STYLE = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .pv-card {
      position: fixed;
      top: 0; left: 0; right: 0;
      max-height: 44vh;
      display: flex;
      flex-direction: column;
      z-index: 2147483647;
      background: #ffffff;
      border: none;
      border-bottom: 1px solid #e4e7ec;
      border-radius: 0;
      box-shadow: 0 8px 24px rgba(16, 24, 40, 0.10);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      color: #12141c;
      font-size: 13px;
      line-height: 1.45;
    }
    .pv-header {
      display: flex; align-items: center; gap: 10px;
      padding: 12px 24px; border-bottom: 1px solid #e4e7ec; flex-shrink: 0;
      max-width: 1200px; margin: 0 auto; width: 100%;
    }
    .pv-logo { display: flex; align-items: center; gap: 7px; font-weight: 800; font-size: 13px; }
    .pv-mark {
      display: inline-flex; align-items: center; justify-content: center;
      width: 22px; height: 22px; border-radius: 8px; background: #3b5bfd; color: #fff;
      font-size: 13px; font-weight: 800; flex-shrink: 0;
    }
    .pv-header-score { display: flex; align-items: baseline; gap: 4px; margin-left: 4px; flex-shrink: 0; }
    .pv-header-score-num { font-size: 16px; font-weight: 800; }
    .pv-header-score-max { font-size: 10px; color: #98a2b3; margin-right: 2px; }
    .pv-header-score-grade {
      font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em;
      padding: 2px 6px; border-radius: 999px;
    }
    .pv-grade-excellent .pv-header-score-num { color: #059669; }
    .pv-grade-excellent .pv-header-score-grade { background: #d1fae5; color: #059669; }
    .pv-grade-good .pv-header-score-num { color: #3b5bfd; }
    .pv-grade-good .pv-header-score-grade { background: #dbeafe; color: #3b5bfd; }
    .pv-grade-fair .pv-header-score-num { color: #d97706; }
    .pv-grade-fair .pv-header-score-grade { background: #fef3c7; color: #d97706; }
    .pv-grade-below-average .pv-header-score-num, .pv-grade-poor .pv-header-score-num { color: #dc2626; }
    .pv-grade-below-average .pv-header-score-grade, .pv-grade-poor .pv-header-score-grade { background: #fee2e2; color: #dc2626; }
    .pv-spacer { flex: 1; }
    .pv-icon-btn {
      background: none; border: none; cursor: pointer; color: #98a2b3;
      font-size: 14px; padding: 3px 5px; border-radius: 6px; flex-shrink: 0;
    }
    .pv-icon-btn:hover { background: #f1f3f7; color: #667085; }
    .pv-account-btn {
      background: #f1f3f7; border: none; border-radius: 999px; padding: 5px 12px;
      font-size: 12px; font-weight: 700; color: #12141c; cursor: pointer; flex-shrink: 0;
      white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis;
    }
    .pv-account-btn.pv-premium { background: #eef1ff; color: #3b5bfd; }
    .pv-body { overflow-y: auto; flex: 1; min-height: 0; }
    .pv-body.pv-collapsed { display: none; }
    .pv-tabs {
      display: flex; gap: 4px; padding: 0 24px; border-bottom: 1px solid #e4e7ec;
      overflow-x: auto; flex-shrink: 0;
      max-width: 1200px; margin: 0 auto; width: 100%;
    }
    .pv-tab {
      background: none; border: none; padding: 10px 12px; font-size: 12.5px; font-weight: 700;
      color: #667085; cursor: pointer; border-bottom: 2px solid transparent; white-space: nowrap;
    }
    .pv-tab.pv-active { color: #3b5bfd; border-bottom-color: #3b5bfd; }
    .pv-tab-content {
      padding: 18px 24px 24px; min-height: 100px;
      max-width: 1200px; margin: 0 auto; width: 100%;
    }
    .pv-loading, .pv-empty { color: #667085; text-align: center; padding: 20px 0; }
    .pv-error { color: #dc2626; text-align: center; padding: 20px 0; }
    .pv-stats { list-style: none; margin: 0 0 10px; padding: 0; }
    .pv-stats li {
      display: flex; justify-content: space-between; gap: 8px;
      padding: 6px 0; border-bottom: 1px solid #f1f3f7;
    }
    .pv-stats li:last-child { border-bottom: none; }
    .pv-stats span:first-child { color: #667085; }
    .pv-stats span:last-child { font-weight: 600; }
    .pv-table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
    .pv-table th {
      text-align: left; color: #667085; font-weight: 700; font-size: 10.5px;
      text-transform: uppercase; letter-spacing: 0.03em; padding: 5px 6px; border-bottom: 1px solid #e4e7ec;
    }
    .pv-table th.pv-num, .pv-table td.pv-num { text-align: right; }
    .pv-table td { padding: 6px; border-bottom: 1px solid #f1f3f7; }
    .pv-badge {
      display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 10.5px; font-weight: 700;
      background: #eef1ff; color: #3b5bfd;
    }
    .pv-badge-outstanding, .pv-badge-good { background: #ecfdf5; color: #059669; }
    .pv-badge-requires-improvement { background: #fffbeb; color: #d97706; }
    .pv-badge-inadequate { background: #fef2f2; color: #dc2626; }
    .pv-trend-higher { color: #dc2626; font-weight: 700; }
    .pv-trend-lower { color: #059669; font-weight: 700; }
    .pv-trend-same { color: #667085; font-weight: 600; }
    .pv-map-frame { width: 100%; height: 220px; border: none; border-radius: 10px; }
    .pv-header-report-link {
      display: inline-flex; align-items: center; gap: 3px; flex-shrink: 0;
      background: #3b5bfd; color: #fff; text-decoration: none; font-weight: 700;
      font-size: 11.5px; padding: 6px 12px; border-radius: 999px; white-space: nowrap;
    }
    .pv-header-report-link:hover { background: #2c47e0; }
    .pv-header-report-link[hidden] { display: none; }
    .pv-summary-verdict { color: #334155; margin: 0 0 14px; font-size: 13.5px; }
    .pv-category-heading {
      font-size: 11.5px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
      color: #3b5bfd; margin: 20px 0 10px;
    }
    .pv-category-heading:first-child { margin-top: 0; }
    .pv-dash-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px;
    }
    .pv-dash-card {
      position: relative; display: flex; flex-direction: column; gap: 3px;
      background: #ffffff; border: 1px solid #e4e7ec; border-radius: 12px; padding: 10px;
    }
    .pv-dash-card.pv-dash-attn { border-color: #fde68a; background: #fffbeb; }
    .pv-dash-card-icon {
      display: inline-flex; align-items: center; justify-content: center;
      width: 26px; height: 26px; border-radius: 8px; font-size: 13px; margin-bottom: 2px;
    }
    .pv-check-this-tag {
      position: absolute; top: 8px; right: 8px; padding: 1px 7px; border-radius: 999px;
      background: #fef3c7; font-size: 8.5px; font-weight: 700; letter-spacing: 0.03em;
      text-transform: uppercase; color: #92400e;
    }
    .pv-dash-card-title {
      font-size: 10px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: #667085;
    }
    .pv-dash-card-value { font-size: 13px; font-weight: 600; color: #12141c; line-height: 1.3; }
    .pv-dash-card.pv-dash-locked .pv-dash-card-value {
      filter: blur(4px); user-select: none;
    }
    .pv-dash-lock-overlay {
      position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
      background: rgba(255,255,255,0.55); border-radius: 12px; cursor: pointer;
    }
    .pv-dash-lock-icon {
      width: 22px; height: 22px; border-radius: 999px; background: #eef1ff; color: #3b5bfd;
      display: flex; align-items: center; justify-content: center; font-size: 12px;
    }
    .icon-market       { background: #eff6ff; color: #2563eb; }
    .icon-energy       { background: #fffbeb; color: #d97706; }
    .icon-flood        { background: #ecfeff; color: #0891b2; }
    .icon-crime        { background: #fff1f2; color: #e11d48; }
    .icon-schools      { background: #f5f3ff; color: #7c3aed; }
    .icon-prosperity   { background: #ecfdf5; color: #059669; }
    .icon-rental       { background: #ecfeff; color: #0e7490; }
    .icon-orientation  { background: #fefce8; color: #a16207; }
    .icon-sewage       { background: #f5f5f4; color: #57534e; }
    .icon-noise        { background: #fefce8; color: #ca8a04; }
    .icon-radon        { background: #f7fee7; color: #65a30d; }
    .icon-geology      { background: #fff7ed; color: #9a3412; }
    .icon-air-quality  { background: #eff6ff; color: #1d4ed8; }
    .icon-landfill     { background: #fef2f2; color: #b91c1c; }
    .icon-mining       { background: #fef3c7; color: #92400e; }
    .icon-planning     { background: #eef2ff; color: #3730a3; }
    .icon-environmental{ background: #ecfdf5; color: #047857; }
    .icon-heritage     { background: #fafaf9; color: #78716c; }
    .icon-broadband    { background: #f0f9ff; color: #0284c7; }
    .icon-mobile       { background: #fdf4ff; color: #a21caf; }
    .icon-valuation    { background: #fefce8; color: #a16207; }
    .icon-extension    { background: #f0f9ff; color: #0369a1; }
    .icon-income       { background: #f0fdfa; color: #0d9488; }
    .icon-deprivation  { background: #fdf2f8; color: #db2777; }
    .icon-occupation   { background: #f8fafc; color: #475569; }
    .icon-qualification{ background: #faf5ff; color: #9333ea; }
    .icon-age          { background: #eef2ff; color: #4338ca; }
    .icon-housing      { background: #fff7ed; color: #c2410c; }
    .icon-ethnicity    { background: #f0fdfa; color: #0f766e; }
    .icon-wellbeing    { background: #fdf2f8; color: #be185d; }
    .icon-amenities    { background: #fff7ed; color: #ea580c; }
    .icon-transport    { background: #eef2ff; color: #4f46e5; }
    .pv-score-card {
      display: flex; align-items: center; gap: 14px; margin: 0 0 16px;
      padding: 12px 14px; border-radius: 12px; border: 1px solid #e4e7ec; background: #fffbeb;
    }
    .pv-score-num { display: flex; align-items: baseline; flex-shrink: 0; }
    .pv-score-value { font-size: 26px; font-weight: 800; line-height: 1; }
    .pv-score-max { font-size: 11px; color: #667085; margin-left: 2px; }
    .pv-score-body { min-width: 0; }
    .pv-score-grade {
      display: inline-block; font-size: 9.5px; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.04em; padding: 2px 8px; border-radius: 999px; margin-bottom: 4px;
    }
    .pv-score-verdict { margin: 0; font-size: 12.5px; color: #12141c; }
    .pv-score-card.pv-grade-excellent { background: #ecfdf5; border-color: #a7f3d0; }
    .pv-score-card.pv-grade-excellent .pv-score-value,
    .pv-score-card.pv-grade-excellent .pv-score-grade { color: #059669; }
    .pv-score-card.pv-grade-excellent .pv-score-grade { background: #d1fae5; }
    .pv-score-card.pv-grade-good { background: #eff6ff; border-color: #bfdbfe; }
    .pv-score-card.pv-grade-good .pv-score-value,
    .pv-score-card.pv-grade-good .pv-score-grade { color: #3b5bfd; }
    .pv-score-card.pv-grade-good .pv-score-grade { background: #dbeafe; }
    .pv-score-card.pv-grade-fair { background: #fffbeb; border-color: #fde68a; }
    .pv-score-card.pv-grade-fair .pv-score-value,
    .pv-score-card.pv-grade-fair .pv-score-grade { color: #d97706; }
    .pv-score-card.pv-grade-fair .pv-score-grade { background: #fef3c7; }
    .pv-score-card.pv-grade-below-average, .pv-score-card.pv-grade-poor { background: #fef2f2; border-color: #fecaca; }
    .pv-score-card.pv-grade-below-average .pv-score-value, .pv-score-card.pv-grade-below-average .pv-score-grade,
    .pv-score-card.pv-grade-poor .pv-score-value, .pv-score-card.pv-grade-poor .pv-score-grade { color: #dc2626; }
    .pv-score-card.pv-grade-below-average .pv-score-grade, .pv-score-card.pv-grade-poor .pv-score-grade { background: #fee2e2; }
    .pv-loading-block {
      display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 28px 0;
    }
    .pv-loading-row { display: flex; align-items: center; gap: 8px; color: #667085; font-size: 12px; margin-bottom: 12px; }
    .pv-spinner {
      width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
      border: 3px solid #eef1ff; border-top-color: #3b5bfd;
      animation: pv-spin 0.8s linear infinite;
    }
    .pv-spinner-sm { width: 15px; height: 15px; border-width: 2px; }
    @keyframes pv-spin { to { transform: rotate(360deg); } }
    .pv-loading-text { color: #667085; font-size: 12.5px; margin: 0; }
    .pv-gate {
      margin-top: 10px; padding: 12px; background: #f1f3f7; border-radius: 10px; text-align: center;
    }
    .pv-gate p { margin: 0 0 8px; color: #475569; font-size: 12px; }
    .pv-gate button {
      background: #3b5bfd; border: none; color: #fff; font-weight: 700; font-size: 12px;
      padding: 7px 14px; border-radius: 8px; cursor: pointer;
    }
    .pv-login-form { padding: 4px 0; }
    .pv-login-form label { display: block; font-size: 11px; font-weight: 700; color: #667085; margin: 8px 0 3px; }
    .pv-login-form input {
      width: 100%; padding: 7px 9px; border: 1px solid #d0d5dd; border-radius: 8px;
      font-size: 12.5px; font-family: inherit; color: #12141c; background: #ffffff;
      color-scheme: light;
    }
    .pv-login-form input:focus { outline: 2px solid #3b5bfd; outline-offset: 1px; }
    .pv-login-submit {
      width: 100%; margin-top: 12px; background: #3b5bfd; border: none; color: #fff;
      font-weight: 700; font-size: 12.5px; padding: 9px 12px; border-radius: 10px; cursor: pointer;
    }
    .pv-login-error { color: #dc2626; font-size: 11.5px; margin-top: 8px; }
    .pv-login-note { color: #98a2b3; font-size: 11px; margin-top: 10px; text-align: center; }
    .pv-logged-in { text-align: center; padding: 6px 0 2px; }
    .pv-logged-in p { margin: 0 0 10px; color: #475569; font-size: 12.5px; }
    .pv-logout-btn {
      background: none; border: 1px solid #d0d5dd; color: #475569; font-weight: 700; font-size: 12px;
      padding: 7px 14px; border-radius: 8px; cursor: pointer;
    }
    .pv-resize-handle {
      height: 8px; flex-shrink: 0; cursor: ns-resize; position: relative; touch-action: none;
    }
    .pv-resize-handle::after {
      content: ""; position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
      width: 40px; height: 4px; border-radius: 999px; background: #d0d5dd;
    }
    .pv-resize-handle:hover::after, .pv-resize-handle.pv-resizing::after { background: #3b5bfd; }
    .pv-modal-backdrop {
      position: fixed; inset: 0; z-index: 2147483647;
      background: rgba(15, 17, 23, 0.55); backdrop-filter: blur(2px);
      display: flex; align-items: center; justify-content: center; padding: 20px;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      color: #12141c; font-size: 13px; line-height: 1.45;
    }
    .pv-modal-backdrop[hidden] { display: none; }
    .pv-modal {
      background: #ffffff; border-radius: 16px; padding: 28px; width: min(340px, 100%);
      box-shadow: 0 20px 48px rgba(16, 24, 40, 0.25); position: relative; text-align: center;
    }
    .pv-modal.pv-modal-rich {
      width: min(520px, 100%); max-height: 82vh; overflow-y: auto; text-align: left;
    }
    .pv-modal.pv-modal-rich .pv-modal-title { text-align: center; }
    .pv-modal-body { text-align: left; }
    .pv-modal-body .pv-modal-value { margin: 0 0 18px; font-size: 20px; font-weight: 800; color: #12141c; line-height: 1.3; text-align: center; }
    .pv-modal-close {
      position: absolute; top: 12px; right: 12px; width: 28px; height: 28px; border-radius: 50%;
      border: none; background: #f1f3f7; color: #667085; cursor: pointer; font-size: 13px;
    }
    .pv-modal-close:hover { background: #e4e7ec; }
    .pv-modal-icon {
      display: inline-flex; align-items: center; justify-content: center;
      width: 44px; height: 44px; border-radius: 12px; font-size: 22px; margin: 0 auto 12px;
    }
    .pv-modal-title {
      margin: 0 0 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.05em; color: #667085;
    }
    .pv-modal-link { display: block; text-align: center; margin-top: 4px; color: #3b5bfd; font-weight: 700; font-size: 13px; text-decoration: none; }
    .pv-modal-link:hover { text-decoration: underline; }
    .pv-dash-card:not(.pv-dash-locked) { cursor: pointer; transition: border-color 0.15s ease, transform 0.15s ease; }
    .pv-dash-card:not(.pv-dash-locked):hover { border-color: #98a2b3; transform: translateY(-1px); }
    .pv-modal-body .pv-modal-detail { margin: 0 0 18px; font-size: 12px; color: #667085; line-height: 1.5; text-align: center; }
    .pv-modal.pv-modal-rich .pv-modal-body .pv-modal-detail { text-align: left; }
    .pv-calc { text-align: left; }
    .pv-calc-heading {
      font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
      color: #3b5bfd; margin: 18px 0 8px;
    }
    .pv-calc-heading:first-child { margin-top: 0; }
    .pv-calc-row { display: flex; flex-wrap: wrap; gap: 10px; }
    .pv-calc-row label {
      flex: 1; min-width: 120px; display: block; font-size: 10.5px; font-weight: 700;
      color: #667085; text-transform: uppercase; letter-spacing: 0.03em;
    }
    .pv-calc-row input, .pv-calc-row select {
      display: block; width: 100%; margin-top: 4px; padding: 6px 8px; border: 1px solid #d0d5dd;
      border-radius: 8px; font-size: 12.5px; font-family: inherit; color: #12141c; background: #ffffff;
      color-scheme: light;
    }
    .pv-calc-result { margin: 8px 0 0; font-size: 13px; color: #12141c; }
    .pv-calc-result span:first-of-type { font-weight: 800; color: #3b5bfd; }
    .pv-calc-sdlt-rate, .pv-calc-loan-amount { font-weight: 500 !important; color: #667085 !important; font-size: 11.5px; }
    .pv-chart { display: block; width: 100%; height: auto; margin: 0 0 14px; }
    .pv-area-notice {
      display: flex; gap: 8px; align-items: flex-start; margin: 0 0 16px; padding: 10px 12px;
      background: #fffbeb; border: 1px solid #fde68a; border-radius: 10px;
      font-size: 11.5px; color: #92400e; line-height: 1.5;
    }
    .pv-postcode-bar {
      display: flex; align-items: center; gap: 8px; padding: 7px 24px;
      border-bottom: 1px solid #e4e7ec; background: #f8fafc; flex-shrink: 0;
      font-size: 11.5px; color: #475569; max-width: 1200px; margin: 0 auto; width: 100%;
    }
    .pv-postcode-bar.pv-postcode-partial { background: #fffbeb; color: #92400e; }
    .pv-postcode-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pv-postcode-edit-btn {
      background: none; border: 1px solid #d0d5dd; color: inherit; font-weight: 700; font-size: 10.5px;
      padding: 3px 9px; border-radius: 999px; cursor: pointer; flex-shrink: 0;
    }
    .pv-postcode-partial .pv-postcode-edit-btn { border-color: #fbbf24; }
    .pv-postcode-form { display: flex; align-items: center; gap: 6px; flex: 1; }
    .pv-postcode-form input {
      flex: 1; min-width: 0; padding: 4px 8px; border: 1px solid #d0d5dd; border-radius: 6px;
      font-size: 11.5px; font-family: inherit; color: #12141c; background: #ffffff; color-scheme: light;
    }
    .pv-postcode-form button {
      background: #3b5bfd; border: none; color: #fff; font-weight: 700; font-size: 10.5px;
      padding: 4px 10px; border-radius: 6px; cursor: pointer; flex-shrink: 0;
    }
  `;

  let currentData = null;
  let currentPremiumData = null;
  let premiumLoading = false;
  let currentToken = null;
  let currentEmail = null;
  let root = null;
  let shadowRoot = null;
  let currentPostcode = null;
  let postcodeIsPartial = false;

  const HEIGHT_STORAGE_KEY = "pv_ext_height";
  const MIN_CARD_HEIGHT = 160;

  function getStoredHeight() {
    return new Promise(function (resolve) {
      if (!chrome?.storage?.local) return resolve(null);
      chrome.storage.local.get([HEIGHT_STORAGE_KEY], function (result) {
        resolve(result[HEIGHT_STORAGE_KEY] || null);
      });
    });
  }

  function setStoredHeight(px) {
    if (!chrome?.storage?.local) return;
    chrome.storage.local.set({ pv_ext_height: px });
  }

  // The Shadow DOM's `font-family: 'Inter', ...` only matches an
  // *installed/loaded* Inter - the CSS declaring it isn't enough on
  // its own. The main site loads Inter via this exact same Google
  // Fonts URL in its <head>; a listing page's own <head> never does,
  // so without this the extension silently fell back to the next
  // stack entry (Segoe UI on Windows) and looked visibly different
  // from the site despite the CSS matching. Fonts aren't shadow-DOM-
  // scoped, so a <link> anywhere in the host page's document makes
  // Inter available inside the shadow root too.
  function ensureInterFontLoaded() {
    if (document.getElementById("pv-inter-font")) return;
    const link = document.createElement("link");
    link.id = "pv-inter-font";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400&display=swap";
    document.head.appendChild(link);
  }

  function buildWidget() {
    ensureInterFontLoaded();
    const host = document.createElement("div");
    host.id = "pv-overlay-host";
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: "open" });
    shadowRoot = shadow;
    const style = document.createElement("style");
    style.textContent = STYLE;
    shadow.appendChild(style);

    const card = document.createElement("div");
    card.className = "pv-card";
    card.innerHTML =
      '<div class="pv-header">' +
        '<span class="pv-logo"><span class="pv-mark">U</span>UKPropertyInsight</span>' +
        '<span class="pv-header-score"><span class="pv-header-score-num">…</span><span class="pv-header-score-max">/100</span><span class="pv-header-score-grade"></span></span>' +
        '<span class="pv-spacer"></span>' +
        '<a class="pv-header-report-link" href="#" target="_blank" rel="noopener" hidden>Full report →</a>' +
        '<button class="pv-account-btn" type="button">Log in</button>' +
        '<button class="pv-icon-btn pv-collapse-btn" type="button" aria-label="Collapse">▾</button>' +
        '<button class="pv-icon-btn pv-close-btn" type="button" aria-label="Close">✕</button>' +
      "</div>" +
      '<div class="pv-postcode-bar"></div>' +
      '<div class="pv-body">' +
        '<div class="pv-tabs"></div>' +
        '<div class="pv-tab-content"><div class="pv-loading-block"><span class="pv-spinner"></span><p class="pv-loading-text">Loading your UKPropertyInsight report…</p></div></div>' +
      "</div>" +
      '<div class="pv-resize-handle" title="Drag to resize"></div>';
    shadow.appendChild(card);

    renderPostcodeBar(card);

    const modalBackdrop = document.createElement("div");
    modalBackdrop.className = "pv-modal-backdrop";
    modalBackdrop.hidden = true;
    modalBackdrop.innerHTML =
      '<div class="pv-modal" role="dialog" aria-modal="true">' +
        '<button type="button" class="pv-modal-close" aria-label="Close">✕</button>' +
        '<span class="pv-modal-icon"></span>' +
        '<h3 class="pv-modal-title"></h3>' +
        '<div class="pv-modal-body"></div>' +
        '<a class="pv-modal-link" href="#" target="_blank" rel="noopener">View full details on UKPropertyInsight →</a>' +
      "</div>";
    shadow.appendChild(modalBackdrop);
    modalBackdrop.addEventListener("click", function (e) {
      if (e.target === modalBackdrop) closeCardModal();
    });
    modalBackdrop.querySelector(".pv-modal-close").addEventListener("click", closeCardModal);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeCardModal();
    });

    const body = card.querySelector(".pv-body");
    card.querySelector(".pv-collapse-btn").addEventListener("click", function () {
      const collapsed = body.classList.toggle("pv-collapsed");
      card.querySelector(".pv-collapse-btn").textContent = collapsed ? "▸" : "▾";
    });
    card.querySelector(".pv-close-btn").addEventListener("click", function () {
      host.remove();
    });
    card.querySelector(".pv-account-btn").addEventListener("click", function () {
      if (currentToken) return; // logged-in state shown on the Summary tab instead of a click action here
      selectTab("account");
    });

    // Cards open a detail popup on click, mirroring the main site's own
    // dashboard-card behaviour - delegated on the persistent content
    // element so it keeps working across tab switches without needing
    // to be re-attached on every re-render.
    card.querySelector(".pv-tab-content").addEventListener("click", function (e) {
      const dashCardEl = e.target.closest(".pv-dash-card");
      if (!dashCardEl || dashCardEl.classList.contains("pv-dash-locked")) return;
      openCardModal(dashCardEl);
    });

    wireResizeHandle(card);

    return card;
  }

  // Always visible, not just when detection looks shaky - the branch-
  // office mixup above happened on a FULL-looking postcode match too,
  // so "confirmed" and "partial" both get a visible, editable postcode
  // rather than silent trust either way.
  function renderPostcodeBar(card) {
    const bar = card.querySelector(".pv-postcode-bar");
    bar.className = "pv-postcode-bar" + (postcodeIsPartial ? " pv-postcode-partial" : "");
    const label = currentPostcode
      ? (postcodeIsPartial
          ? "Showing area-level data for " + escapeHtml(currentPostcode) + " - not the exact postcode"
          : "Showing data for " + escapeHtml(currentPostcode))
      : "No postcode detected on this page";
    bar.innerHTML =
      '<span class="pv-postcode-text">📍 ' + label + "</span>" +
      '<button type="button" class="pv-postcode-edit-btn">' + (currentPostcode ? "Not right? Edit" : "Enter postcode") + "</button>";
    bar.querySelector(".pv-postcode-edit-btn").addEventListener("click", function () {
      showPostcodeEditForm(card);
    });
  }

  function showPostcodeEditForm(card) {
    const bar = card.querySelector(".pv-postcode-bar");
    bar.innerHTML =
      '<form class="pv-postcode-form">' +
        '<input type="text" placeholder="e.g. BR5 1AB" value="' + escapeHtml(postcodeIsPartial ? "" : (currentPostcode || "")) + '" autocomplete="off">' +
        '<button type="submit">Save</button>' +
      "</form>";
    const input = bar.querySelector("input");
    input.focus();
    bar.querySelector(".pv-postcode-form").addEventListener("submit", function (e) {
      e.preventDefault();
      const typed = normalizePostcode(input.value.trim());
      if (!typed) return;
      currentPostcode = typed;
      postcodeIsPartial = false;
      setPostcodeOverride(typed);
      renderPostcodeBar(card);
      loadReport();
    });
  }

  function wireResizeHandle(card) {
    const handle = card.querySelector(".pv-resize-handle");
    let dragging = false;
    let startY = 0;
    let startHeight = 0;

    handle.addEventListener("pointerdown", function (e) {
      dragging = true;
      startY = e.clientY;
      startHeight = card.getBoundingClientRect().height;
      handle.classList.add("pv-resizing");
      e.preventDefault();
    });
    document.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      const maxHeight = window.innerHeight * 0.92;
      const newHeight = Math.min(maxHeight, Math.max(MIN_CARD_HEIGHT, startHeight + (e.clientY - startY)));
      card.style.height = newHeight + "px";
      card.style.maxHeight = newHeight + "px";
    });
    document.addEventListener("pointerup", function () {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove("pv-resizing");
      setStoredHeight(Math.round(card.getBoundingClientRect().height));
    });
  }

  // "At a glance" cards that mirror an existing tab's real fetched
  // data (transactions, per-category crime, per-school table) open
  // that tab's actual renderer in the popup instead of a generic
  // title+number - the main site's own modals are this detailed, and
  // for these five cards we already have the data to match it, rather
  // than re-describing the same number in a bigger box.
  const CARD_TAB_MAP = {
    "Avg sold price": "market",
    "Crime nearby": "crime",
    "Schools": "schools",
    "EPC rating": "epc",
  };

  // Finds the raw card object (title/value/status/detail) the premium
  // endpoint sent for this title, if any - the DOM only carries the
  // rendered title/value text, not the structured `detail` a card may
  // have, so a real lookup back into the last-fetched payload is
  // needed to show anything richer than that.
  function findPremiumCard(title) {
    if (!currentPremiumData) return null;
    for (const section of currentPremiumData.sections) {
      for (const c of section.cards) {
        if (c.title === title) return c;
      }
    }
    return null;
  }

  function openCardModal(dashCardEl) {
    if (!shadowRoot) return;
    const title = dashCardEl.querySelector(".pv-dash-card-title").textContent;
    const value = dashCardEl.querySelector(".pv-dash-card-value").textContent;
    const iconEl = dashCardEl.querySelector(".pv-dash-card-icon");
    const backdrop = shadowRoot.querySelector(".pv-modal-backdrop");
    const modalEl = backdrop.querySelector(".pv-modal");
    const iconTarget = backdrop.querySelector(".pv-modal-icon");
    iconTarget.textContent = iconEl ? iconEl.textContent : "";
    iconTarget.className = "pv-modal-icon" + (iconEl ? " " + iconEl.className.replace("pv-dash-card-icon", "").trim() : "");
    backdrop.querySelector(".pv-modal-title").textContent = title;

    const bodyEl = backdrop.querySelector(".pv-modal-body");
    const tabKey = CARD_TAB_MAP[title];
    const premiumCard = findPremiumCard(title);
    const isRich = (tabKey && RENDERERS[tabKey] && currentData) || (premiumCard && premiumCard.detail);
    modalEl.classList.toggle("pv-modal-rich", !!isRich);

    if (tabKey && RENDERERS[tabKey] && currentData) {
      bodyEl.innerHTML = RENDERERS[tabKey](currentData);
      bodyEl.querySelectorAll(".pv-gate-login-btn").forEach(function (btn) {
        btn.addEventListener("click", function () { closeCardModal(); selectTab("account"); });
      });
    } else if (premiumCard && premiumCard.detail) {
      bodyEl.innerHTML =
        '<p class="pv-modal-value">' + escapeHtml(value) + "</p>" +
        renderDetail(premiumCard.detail);
      if (premiumCard.detail.type === "calculator") wireCalculator(bodyEl, premiumCard.detail);
    } else {
      const detail = CARD_DETAILS[title];
      bodyEl.innerHTML =
        '<p class="pv-modal-value">' + escapeHtml(value) + "</p>" +
        (detail ? '<p class="pv-modal-detail">' + escapeHtml(detail) + "</p>" : "");
    }
    backdrop.querySelector(".pv-modal-link").href = API_BASE + (currentData && currentData.report_url ? currentData.report_url : "/");
    backdrop.hidden = false;
  }

  // A compact version of the same line chart the site's own "Sold
  // price history" modal draws (hand-rolled inline SVG there too, no
  // charting library) - area fill under a single accent-coloured
  // line, endpoint price labelled, so Local Market/Area Prosperity's
  // popup shows the trend at a glance, not just a table of rows.
  function priceChartSvg(points) {
    if (!points || points.length < 2) return "";
    const W = 460, H = 150, PAD_L = 46, PAD_R = 12, PAD_T = 14, PAD_B = 22;
    const amounts = points.map(function (p) { return p.amount; });
    const minAmount = 0;
    const maxAmount = Math.max.apply(null, amounts) * 1.08;
    const x = function (i) { return PAD_L + (i / (points.length - 1)) * (W - PAD_L - PAD_R); };
    const y = function (v) { return PAD_T + (1 - (v - minAmount) / (maxAmount - minAmount)) * (H - PAD_T - PAD_B); };
    const linePoints = points.map(function (p, i) { return x(i) + "," + y(p.amount); }).join(" ");
    const areaPoints = linePoints + " " + x(points.length - 1) + "," + y(0) + " " + x(0) + "," + y(0);
    const gridCount = 3;
    let gridlines = "";
    for (let g = 0; g <= gridCount; g++) {
      const v = (maxAmount / gridCount) * g;
      const gy = y(v);
      gridlines += '<line x1="' + PAD_L + '" y1="' + gy + '" x2="' + (W - PAD_R) + '" y2="' + gy + '" stroke="#eef0f4" stroke-width="1"/>' +
        '<text x="' + (PAD_L - 6) + '" y="' + (gy + 3) + '" font-size="9" fill="#98a2b3" text-anchor="end">' + gbpCompact(v) + "</text>";
    }
    const last = points[points.length - 1];
    const lastX = x(points.length - 1), lastY = y(last.amount);
    const dateLabel = function (d) { const parts = (d || "").split("-"); return parts.length >= 2 ? ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][parseInt(parts[1], 10) - 1] + " " + parts[0] : d; };
    return (
      '<svg viewBox="0 0 ' + W + " " + H + '" class="pv-chart" preserveAspectRatio="none">' +
        gridlines +
        '<polygon points="' + areaPoints + '" fill="#3b5bfd" fill-opacity="0.08"/>' +
        '<polyline points="' + linePoints + '" fill="none" stroke="#3b5bfd" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
        '<circle cx="' + lastX + '" cy="' + lastY + '" r="3.5" fill="#3b5bfd"/>' +
        '<text x="' + Math.min(lastX, W - 60) + '" y="' + Math.max(lastY - 8, 12) + '" font-size="10" font-weight="700" fill="#12141c">' + gbp(last.amount) + "</text>" +
        '<text x="' + PAD_L + '" y="' + (H - 6) + '" font-size="9" fill="#98a2b3">' + escapeHtml(dateLabel(points[0].date)) + "</text>" +
        '<text x="' + (W - PAD_R) + '" y="' + (H - 6) + '" font-size="9" fill="#98a2b3" text-anchor="end">' + escapeHtml(dateLabel(last.date)) + "</text>" +
      "</svg>"
    );
  }

  function renderDetail(detail) {
    if (!detail) return "";
    if (detail.type === "table") {
      return (
        (detail.chart ? priceChartSvg(detail.chart) : "") +
        '<table class="pv-table"><thead><tr>' +
          detail.columns.map(function (c) { return "<th>" + escapeHtml(c) + "</th>"; }).join("") +
        "</tr></thead><tbody>" +
          detail.rows.map(function (row) {
            return "<tr>" + row.map(function (cell) { return "<td>" + escapeHtml(cell) + "</td>"; }).join("") + "</tr>";
          }).join("") +
        "</tbody></table>"
      );
    }
    if (detail.type === "list") {
      return '<ul class="pv-stats">' + detail.items.map(function (i) { return "<li><span>" + escapeHtml(i) + "</span></li>"; }).join("") + "</ul>";
    }
    if (detail.type === "calculator") return calculatorHtml(detail);
    return "";
  }

  // Same stamp duty bands/rates, mortgage amortization formula and
  // gross-yield formula as property.html's own client-side calculator
  // (app/templates/property.html) - ported verbatim so a figure here
  // never quietly diverges from what the same postcode/price would
  // show on the full report. Rates as of April 2025 - always
  // changeable by a future Budget, same caveat the site gives.
  const SDLT_BANDS = {
    "england-ni": {
      standard: [[125000, 0], [250000, 0.02], [925000, 0.05], [1500000, 0.10], [Infinity, 0.12]],
      ftb: [[300000, 0], [500000, 0.05]],
      ftbCeiling: 500000,
      surcharge: 0.05,
    },
    scotland: {
      standard: [[145000, 0], [250000, 0.02], [325000, 0.05], [750000, 0.10], [Infinity, 0.12]],
      ftb: [[175000, 0], [250000, 0.02], [325000, 0.05], [750000, 0.10], [Infinity, 0.12]],
      ftbCeiling: Infinity,
      surcharge: 0.08,
    },
    wales: {
      standard: [[225000, 0], [400000, 0.06], [750000, 0.075], [1500000, 0.10], [Infinity, 0.12]],
      ftb: null,
      ftbCeiling: 0,
      surcharge: 0.05,
    },
  };

  function marginalTax(price, bands) {
    let tax = 0;
    let lower = 0;
    for (const [upper, rate] of bands) {
      if (price <= lower) break;
      tax += (Math.min(price, upper) - lower) * rate;
      lower = upper;
    }
    return tax;
  }

  function calculatorHtml(detail) {
    const regionMap = { Scotland: "scotland", Wales: "wales" };
    const region = regionMap[detail.country] || "england-ni";
    const price = Math.round(detail.price || 300000);
    const rent = Math.round(detail.rent || 0);
    return (
      '<div class="pv-calc">' +
        '<h4 class="pv-calc-heading">Stamp duty / transaction tax</h4>' +
        '<div class="pv-calc-row">' +
          '<label>Purchase price (£)<input type="number" class="pv-calc-price" value="' + price + '"></label>' +
          '<label>Buyer type<select class="pv-calc-buyer">' +
            '<option value="standard">Home mover</option>' +
            '<option value="ftb">First-time buyer</option>' +
            '<option value="additional">Additional property</option>' +
          "</select></label>" +
          '<label>Tax region<select class="pv-calc-region">' +
            '<option value="england-ni"' + (region === "england-ni" ? " selected" : "") + '>England / Northern Ireland</option>' +
            '<option value="scotland"' + (region === "scotland" ? " selected" : "") + ">Scotland</option>" +
            '<option value="wales"' + (region === "wales" ? " selected" : "") + ">Wales</option>" +
          "</select></label>" +
        "</div>" +
        '<p class="pv-calc-result">Estimated tax: <span class="pv-calc-sdlt-result"></span> <span class="pv-calc-sdlt-rate"></span></p>' +

        '<h4 class="pv-calc-heading">Mortgage repayment</h4>' +
        '<div class="pv-calc-row">' +
          '<label>Deposit (%)<input type="number" class="pv-calc-deposit" value="15"></label>' +
          '<label>Interest rate (% APR)<input type="number" step="0.1" class="pv-calc-rate" value="4.5"></label>' +
          '<label>Term (years)<input type="number" class="pv-calc-term" value="25"></label>' +
        "</div>" +
        '<p class="pv-calc-result">Est. monthly repayment: <span class="pv-calc-mortgage-result"></span> <span class="pv-calc-loan-amount"></span></p>' +

        '<h4 class="pv-calc-heading">Buy-to-let gross yield</h4>' +
        '<div class="pv-calc-row">' +
          '<label>Expected monthly rent (£)<input type="number" class="pv-calc-rent" value="' + rent + '"></label>' +
        "</div>" +
        '<p class="pv-calc-result">Gross yield: <span class="pv-calc-yield-result"></span></p>' +

        '<p class="pv-modal-detail">Estimates only, computed from published tax bands (correct as of April 2025 - always confirm the exact figure on gov.uk before exchanging, since Budgets change these). Not financial advice.</p>' +
      "</div>"
    );
  }

  function wireCalculator(root, detail) {
    const priceEl = root.querySelector(".pv-calc-price");
    const buyerEl = root.querySelector(".pv-calc-buyer");
    const regionEl = root.querySelector(".pv-calc-region");
    const sdltResultEl = root.querySelector(".pv-calc-sdlt-result");
    const sdltRateEl = root.querySelector(".pv-calc-sdlt-rate");
    const depositEl = root.querySelector(".pv-calc-deposit");
    const rateEl = root.querySelector(".pv-calc-rate");
    const termEl = root.querySelector(".pv-calc-term");
    const mortgageResultEl = root.querySelector(".pv-calc-mortgage-result");
    const loanAmountEl = root.querySelector(".pv-calc-loan-amount");
    const rentEl = root.querySelector(".pv-calc-rent");
    const yieldResultEl = root.querySelector(".pv-calc-yield-result");

    function recalc() {
      const price = Math.max(0, Number(priceEl.value) || 0);
      const table = SDLT_BANDS[regionEl.value];
      const type = buyerEl.value;
      let bands = table.standard;
      if (type === "ftb" && table.ftb && price <= table.ftbCeiling) bands = table.ftb;
      let tax = marginalTax(price, bands);
      if (type === "additional") tax += price * table.surcharge;
      sdltResultEl.textContent = gbp(Math.round(tax));
      sdltRateEl.textContent = "(" + (price > 0 ? (tax / price * 100).toFixed(1) : "0") + "% effective rate)";

      const deposit = price * (Math.min(100, Math.max(0, Number(depositEl.value) || 0)) / 100);
      const loan = Math.max(0, price - deposit);
      const annualRate = Math.max(0, Number(rateEl.value) || 0) / 100;
      const months = Math.max(1, Number(termEl.value) || 1) * 12;
      const monthlyRate = annualRate / 12;
      const monthly = monthlyRate === 0 ? loan / months : loan * (monthlyRate * Math.pow(1 + monthlyRate, months)) / (Math.pow(1 + monthlyRate, months) - 1);
      loanAmountEl.textContent = "(on a " + gbp(Math.round(loan)) + " loan)";
      mortgageResultEl.textContent = gbp(Math.round(monthly)) + "/mo";

      const rent = Math.max(0, Number(rentEl.value) || 0);
      yieldResultEl.textContent = price > 0 && rent > 0 ? ((rent * 12) / price * 100).toFixed(2) + "%" : "—";
    }

    [priceEl, buyerEl, regionEl, depositEl, rateEl, termEl, rentEl].forEach(function (el) {
      el.addEventListener("input", recalc);
    });
    recalc();
  }

  function closeCardModal() {
    if (!shadowRoot) return;
    const backdrop = shadowRoot.querySelector(".pv-modal-backdrop");
    if (backdrop) backdrop.hidden = true;
  }

  function ratingBadgeClass(label) {
    if (!label) return "";
    return "pv-badge-" + label.toLowerCase().replace(/\s+/g, "-");
  }

  function gateHtml(tabKey) {
    const fullCountKey = GATED_TABS[tabKey] + "_full_count";
    const fullCount = currentData ? currentData[fullCountKey] : null;
    const more = fullCount != null ? fullCount - 1 : null;
    return (
      '<div class="pv-gate">' +
        "<p>" + (more && more > 0 ? more + " more result" + (more === 1 ? "" : "s") + " available" : "See the full list") + " with Premium.</p>" +
        '<button type="button" class="pv-gate-login-btn">Log in to unlock</button>' +
      "</div>"
    );
  }

  // Mirrors the section/card structure /api/extension-premium-report
  // returns - used to render locked placeholder cards (title visible,
  // value hidden behind a lock icon) for a logged-out/free viewer, the
  // same "see the category exists, log in for the number" pattern the
  // main site's own dashboard grid uses.
  const PREMIUM_SECTIONS = [
    { heading: "Value & Market", cards: ["Local Market", "Valuation Estimate", "Costs & Affordability", "Area Prosperity", "Price Trend & Forecast", "Rental Analysis"] },
    { heading: "Property & Condition", cards: ["Energy Efficiency", "Extended or Modified", "Aspect"] },
    { heading: "Risk & Safety", cards: ["Flood Risk", "Crime & Safety", "Surface Water Risk", "Sewage Discharge", "Noise", "Radon Gas", "Subsidence Risk", "Air Quality", "Historic Contamination", "Mining Risk"] },
    { heading: "Planning & Heritage", cards: ["Planning Constraints", "Environmental Designations", "Listed Buildings"] },
    { heading: "Location & Connectivity", cards: ["Schools Nearby", "School Catchment Areas", "Nearby Essentials", "Getting Around", "Broadband", "Mobile Signal"] },
    { heading: "Area & Community", cards: ["Household Income", "Deprivation", "Occupation", "Qualification", "Age Profile", "Housing Types & Tenure", "Ethnicity, Religion & Origin", "Health, Relationships & Social Grade", "Resident Reviews"] },
  ];

  // Icon glyph + colour-class per card title, mirroring the colour
  // families app/static/css/style.css assigns each dashboard-card-icon
  // on the main site (icon-flood, icon-noise, etc.) so the extension's
  // cards read as the same visual language, not a plain text list.
  const CARD_ICONS = {
    "Avg sold price": ["£", "icon-market"],
    "Flood risk": ["💧", "icon-flood"],
    "Crime nearby": ["🚨", "icon-crime"],
    "Schools": ["🎓", "icon-schools"],
    "EPC rating": ["⚡", "icon-energy"],
    "Area Prosperity": ["📈", "icon-prosperity"],
    "Price Trend & Forecast": ["📊", "icon-market"],
    "Rental Analysis": ["🏠", "icon-rental"],
    "Aspect": ["🧭", "icon-orientation"],
    "Surface Water Risk": ["💧", "icon-flood"],
    "Sewage Discharge": ["🚱", "icon-sewage"],
    "Noise": ["🔊", "icon-noise"],
    "Radon Gas": ["☢", "icon-radon"],
    "Subsidence Risk": ["🪨", "icon-geology"],
    "Air Quality": ["🌫", "icon-air-quality"],
    "Historic Contamination": ["🗑", "icon-landfill"],
    "Mining Risk": ["⛏", "icon-mining"],
    "Planning Constraints": ["📏", "icon-planning"],
    "Environmental Designations": ["🌳", "icon-environmental"],
    "Listed Buildings": ["🏛", "icon-heritage"],
    "Broadband": ["🌐", "icon-broadband"],
    "Mobile Signal": ["📶", "icon-mobile"],
    "Local Market": ["£", "icon-market"],
    "Valuation Estimate": ["🏷", "icon-valuation"],
    "Costs & Affordability": ["🧮", "icon-valuation"],
    "Energy Efficiency": ["⚡", "icon-energy"],
    "Extended or Modified": ["🧱", "icon-extension"],
    "Flood Risk": ["💧", "icon-flood"],
    "Crime & Safety": ["🚨", "icon-crime"],
    "Schools Nearby": ["🎓", "icon-schools"],
    "School Catchment Areas": ["📍", "icon-schools"],
    "Nearby Essentials": ["🛒", "icon-amenities"],
    "Getting Around": ["🚉", "icon-transport"],
    "Household Income": ["💰", "icon-income"],
    "Deprivation": ["📉", "icon-deprivation"],
    "Occupation": ["💼", "icon-occupation"],
    "Qualification": ["🎓", "icon-qualification"],
    "Age Profile": ["🎂", "icon-age"],
    "Housing Types & Tenure": ["🏘", "icon-housing"],
    "Ethnicity, Religion & Origin": ["🌍", "icon-ethnicity"],
    "Health, Relationships & Social Grade": ["❤", "icon-wellbeing"],
    "Resident Reviews": ["⭐", "icon-wellbeing"],
  };

  // Same source/methodology explanation the main site's own modal for
  // each card gives (condensed) - so a popup here is actually
  // informative, not just the number again in a bigger box.
  const CARD_DETAILS = {
    "Avg sold price": "Full sold-transaction history for this postcode, from HM Land Registry Price Paid Data.",
    "Flood risk": "Environment Agency flood zone (rivers & sea) - Zone 3 is high probability, Zone 2 medium, Zone 1 low.",
    "Crime nearby": "Crimes recorded within roughly 1 mile, from police.uk's public data.",
    "Schools": "The 3 nearest schools of each type by proximity - not a catchment-area guarantee, since no free UK-wide catchment dataset exists.",
    "EPC rating": "From the property's Energy Performance Certificate, checked against the Minimum Energy Efficiency Standard (England & Wales require at least an E rating to legally let).",
    "Area Prosperity": "5-year sold-price trend for this area, from HM Land Registry's House Price Index.",
    "Price Trend & Forecast": "A straight-line trend fitted to 5 years of HM Land Registry's House Price Index - not a guarantee, just where prices land if the recent trend continued.",
    "Rental Analysis": "Typical private-rental price by bedroom count for this area, from ONS's Price Index of Private Rents.",
    "Aspect": "An estimated facing direction from building footprint and nearest road - not a measured sunlight survey, and doesn't account for trees or neighbouring buildings.",
    "Surface Water Risk": "Environment Agency's Risk of Flooding from Surface Water map - rainwater that can't drain away, a different risk from the river/sea flood zone above.",
    "Sewage Discharge": "Storm overflow spill counts within 1.5 miles, from water companies' official Event Duration Monitoring returns.",
    "Noise": "Modelled day-evening-night noise level from DEFRA's strategic noise maps - a 10m-grid model, not a measurement at this exact address.",
    "Radon Gas": "% of homes estimated at/above the UK radon Action Level, from the British Geological Survey's radon atlas - modelled at 1km-grid resolution, not measured here.",
    "Subsidence Risk": "BGS climate data on how much clay shrink-swell subsidence risk is likely to worsen by 2030 - relevant over a long mortgage.",
    "Air Quality": "Modelled annual pollutant levels from Defra's Pollution Climate Mapping, benchmarked against WHO's 2021 guideline levels.",
    "Historic Contamination": "Environment Agency's Historic Landfill Sites dataset - old tipping sites that can carry ground-gas risk for decades.",
    "Mining Risk": "Coal Authority's National Coal Mining Database - the same basis solicitors use for a CON29M mining search.",
    "Planning Constraints": "Live check against Historic England/planning.data.gov.uk for Tree Preservation Orders, Article 4 Directions and similar building restrictions.",
    "Environmental Designations": "Live check against Natural England's boundary data for protected natural areas at this exact point.",
    "Listed Buildings": "Listed buildings within about a third of a mile, from Historic England's National Heritage List - proximity only, not a check on this specific building.",
    "Broadband": "Fixed-line broadband speed-tier availability for this postcode, from Ofcom's Connected Nations data.",
    "Mobile Signal": "Ofcom 4G/5G coverage estimate, reported at local-authority level since mobile signal isn't mapped postcode-by-postcode like broadband.",
    "Local Market": "Most recent sold price for this postcode, from HM Land Registry Price Paid Data.",
    "Valuation Estimate": "A range built from recent nearby sold prices and the area's price trend - not narrowed by floor area the way a specific address's estimate would be.",
    "Costs & Affordability": "Stamp duty, mortgage and rental-yield calculators, on the full report.",
    "Energy Efficiency": "Energy Performance Certificates recorded for this postcode, from the national EPC register.",
    "Extended or Modified": "Compares floor area across a single address's own EPC certificates over time - needs an exact house number to check, which listing pages don't publish.",
    "Flood Risk": "Environment Agency flood zone (rivers & sea) - Zone 3 is high probability, Zone 2 medium, Zone 1 low.",
    "Crime & Safety": "Crimes recorded within roughly 1 mile, from police.uk's public data.",
    "Schools Nearby": "Nearest schools by phase (nursery/primary/secondary), by proximity.",
    "School Catchment Areas": "Real published admission-distance data where a council provides it, modelled estimates elsewhere - not a catchment guarantee.",
    "Nearby Essentials": "Restaurants, supermarkets, pharmacies, pubs and hospitals within walking distance, from OpenStreetMap.",
    "Getting Around": "Nearest train/tube/bus stop, and journey time to the nearest major city where available.",
    "Household Income": "Modelled average household income for this area (MSOA), from ONS small-area income estimates.",
    "Deprivation": "English Indices of Deprivation decile for this area (LSOA) - decile 1 is the most deprived 10% nationally, 10 the least.",
    "Occupation": "% in managerial/professional occupations for this area, from the 2021 Census.",
    "Qualification": "% educated to degree level or above for this area, from the 2021 Census.",
    "Age Profile": "% of residents under 25 for this area, from the 2021 Census.",
    "Housing Types & Tenure": "% owner-occupied housing for this area, from the 2021 Census.",
    "Ethnicity, Religion & Origin": "% of residents born outside the UK for this area, from the 2021 Census.",
    "Health, Relationships & Social Grade": "% reporting good or very good health for this area, from the 2021 Census.",
    "Resident Reviews": "UKPropertyInsight users' own ratings for this address - not available at area level, since a review is about one specific property.",
  };

  function dashCard(title, value, locked, status) {
    const iconInfo = CARD_ICONS[title];
    const attn = !locked && status === "attn";
    return (
      '<div class="pv-dash-card' + (locked ? " pv-dash-locked" : "") + (attn ? " pv-dash-attn" : "") + '">' +
        (attn ? '<span class="pv-check-this-tag">Check this</span>' : "") +
        (iconInfo ? '<span class="pv-dash-card-icon ' + iconInfo[1] + '">' + iconInfo[0] + "</span>" : "") +
        '<span class="pv-dash-card-title">' + escapeHtml(title) + "</span>" +
        '<span class="pv-dash-card-value">' + escapeHtml(value == null ? "No data" : value) + "</span>" +
        (locked ? '<div class="pv-dash-lock-overlay pv-gate-login-btn"><span class="pv-dash-lock-icon">🔒</span></div>' : "") +
      "</div>"
    );
  }

  const RENDERERS = {
    overview: function (data) {
      const s = data.summary;
      const o = data.overview;
      let html = "";
      if (premiumLoading) html += '<div class="pv-loading-row"><span class="pv-spinner pv-spinner-sm"></span><span>Unlocking your full report…</span></div>';

      const gradeClass = "pv-grade-" + String(o.grade || "").toLowerCase().replace(/\s+/g, "-");
      html += '<div class="pv-score-card ' + gradeClass + '">' +
        '<div class="pv-score-num"><span class="pv-score-value">' + escapeHtml(o.score) + '</span><span class="pv-score-max">/100</span></div>' +
        '<div class="pv-score-body">' +
          '<span class="pv-score-grade">' + escapeHtml(o.grade || "") + '</span>' +
          '<p class="pv-score-verdict">' + escapeHtml(o.verdict || "") + "</p>" +
        "</div>" +
      "</div>";

      if (data.area_level) {
        html += '<div class="pv-area-notice">📍 <span>Showing area-level information for ' + escapeHtml(data.district || "") +
          " - Zoopla/Rightmove don't publish this listing's exact address, so crime, flood risk and schools here are genuinely for the surrounding area, not confirmed to this specific property. For sold-price history, EPC and a precise valuation, ask the agent for the house number, then search it directly on UKPropertyInsight.</span></div>";
      }

      html += '<h3 class="pv-category-heading">At a glance</h3><div class="pv-dash-grid">' +
        dashCard("Avg sold price", data.area_level ? "Ask agent for address" : gbp(s.avg_price)) +
        dashCard("Flood risk", s.flood_zone) +
        dashCard("Crime nearby", s.crime_total != null ? s.crime_total + " recorded" : null) +
        dashCard("Schools", s.schools_good_pct != null ? s.schools_good_pct + "% Outstanding/Good" : null) +
        dashCard("EPC rating", data.area_level ? "Ask agent for address" : s.epc_rating) +
        "</div>";

      const sections = currentPremiumData
        ? currentPremiumData.sections.map(function (sec) { return { heading: sec.heading, cards: sec.cards.map(function (c) { return [c.title, c.value, false, c.status]; }) }; })
        : PREMIUM_SECTIONS.map(function (sec) { return { heading: sec.heading, cards: sec.cards.map(function (title) { return [title, "Premium", true, null]; }) }; });

      sections.forEach(function (section) {
        html += '<h3 class="pv-category-heading">' + escapeHtml(section.heading) + '</h3><div class="pv-dash-grid">' +
          section.cards.map(function (c) { return dashCard(c[0], c[1], c[2], c[3]); }).join("") +
          "</div>";
      });

      return html;
    },
    map: function (data) {
      if (!data.latitude || !data.longitude) return '<p class="pv-empty">No location data available.</p>';
      const d = 0.006;
      const bbox = (data.longitude - d) + "," + (data.latitude - d) + "," + (data.longitude + d) + "," + (data.latitude + d);
      const src = "https://www.openstreetmap.org/export/embed.html?bbox=" + bbox + "&marker=" + data.latitude + "," + data.longitude + "&layer=mapnik";
      return '<iframe class="pv-map-frame" src="' + src + '" loading="lazy"></iframe>';
    },
    market: function (data) {
      const rows = data.market_history || [];
      if (data.area_level) return '<p class="pv-empty">We don’t have this property’s exact address, so we can’t show its sold-price history. Ask the agent for the house number, then search it directly on UKPropertyInsight.</p>';
      if (data.market_history_error) return '<p class="pv-error">Couldn’t check sold prices right now - try refreshing in a moment.</p>';
      if (!rows.length) return '<p class="pv-empty">No recorded sales found for this postcode.</p>';
      const html =
        '<table class="pv-table"><thead><tr><th>Address</th><th>Date</th><th class="pv-num">Price</th></tr></thead><tbody>' +
        rows.map(function (t) {
          return "<tr><td>" + escapeHtml(t.address) + "</td><td>" + escapeHtml(t.date) + "</td><td class=\"pv-num\">" + gbp(t.amount) + "</td></tr>";
        }).join("") +
        "</tbody></table>";
      return html + (data.premium_unlocked ? "" : gateHtml("market"));
    },
    comparables: function (data) {
      const c = data.comparables;
      if (!c || !c.transactions || !c.transactions.length) return '<p class="pv-empty">No nearby sold comparables found.</p>';
      const html =
        '<ul class="pv-stats">' +
          "<li><span>Nearby sales found</span><span>" + c.count + "</span></li>" +
          "<li><span>Median price</span><span>" + gbp(c.median) + "</span></li>" +
        "</ul>" +
        '<table class="pv-table"><thead><tr><th>Address</th><th class="pv-num">Distance</th><th class="pv-num">Price</th></tr></thead><tbody>' +
        c.transactions.map(function (t) {
          return "<tr><td>" + escapeHtml(t.address) + "</td><td class=\"pv-num\">" + distanceText(t.distance_m) + "</td><td class=\"pv-num\">" + gbp(t.amount) + "</td></tr>";
        }).join("") +
        "</tbody></table>";
      return html + (data.premium_unlocked ? "" : gateHtml("comparables"));
    },
    schools: function (data) {
      const rows = data.schools || [];
      if (!rows.length) return '<p class="pv-empty">No nearby schools found.</p>';
      const html =
        '<table class="pv-table"><thead><tr><th>School</th><th>Phase</th><th class="pv-num">Distance</th><th class="pv-num">Ofsted</th></tr></thead><tbody>' +
        rows.map(function (s) {
          const badge = s.ofsted_rating_label
            ? '<span class="pv-badge ' + ratingBadgeClass(s.ofsted_rating_label) + '">' + escapeHtml(s.ofsted_rating_label) + "</span>"
            : "—";
          return "<tr><td>" + escapeHtml(s.name) + "</td><td>" + escapeHtml(s.phase || "—") + "</td><td class=\"pv-num\">" + distanceText(s.distance_m) + "</td><td class=\"pv-num\">" + badge + "</td></tr>";
        }).join("") +
        "</tbody></table>";
      return html + (data.premium_unlocked ? "" : gateHtml("schools"));
    },
    epc: function (data) {
      if (data.area_level) return '<p class="pv-empty">We don’t have this property’s exact address, so we can’t show its EPC certificate. Ask the agent for the house number, then search it directly on UKPropertyInsight.</p>';
      if (!data.epc) return '<p class="pv-empty">No EPC certificate found for this postcode.</p>';
      return (
        '<ul class="pv-stats">' +
          "<li><span>Energy rating</span><span>" + escapeHtml(data.epc.rating) + "</span></li>" +
          "<li><span>Certificate date</span><span>" + escapeHtml(data.epc.date) + "</span></li>" +
        "</ul>"
      );
    },
    demographics: function (data) {
      const d = data.demographics || {};
      return (
        '<ul class="pv-stats">' +
          "<li><span>Household income</span><span>" + (d.household_income != null ? gbp(d.household_income) + "/yr" : "No data") + "</span></li>" +
          "<li><span>Deprivation</span><span>" + escapeHtml(d.imd_label || "No data") + "</span></li>" +
          "<li><span>Managerial/professional</span><span>" + (d.professional_pct != null ? d.professional_pct + "%" : "No data") + "</span></li>" +
        "</ul>"
      );
    },
    crime: function (data) {
      const c = data.crime;
      if (!c || !c.by_category || !c.by_category.length) return '<p class="pv-empty">No crime data available.</p>';
      const trendLabel = { higher: "Higher", lower: "Lower", same: "About the same" };
      const trendClass = { higher: "pv-trend-higher", lower: "pv-trend-lower", same: "pv-trend-same" };
      const rows = c.comparison && c.comparison.length
        ? c.comparison
        : c.by_category.map(function (cat) { return { category: cat.category, here: cat.count, area: null, trend: null }; });
      let html = '<p class="pv-summary-verdict">' + c.total + " crimes recorded within ~1 mile" + (c.month ? " in " + escapeHtml(c.month) : "") +
        (c.district_total != null ? ", versus " + c.district_total + " in the wider " + escapeHtml(c.outcode || "") + " postcode area." : ".") + "</p>";
      html += '<table class="pv-table"><thead><tr><th>Category</th><th class="pv-num">Here</th><th class="pv-num">' +
        escapeHtml(c.outcode || "Area") + '</th><th class="pv-num">Versus area</th></tr></thead><tbody>' +
        rows.map(function (r) {
          return "<tr><td style=\"text-transform:capitalize\">" + escapeHtml(r.category) + "</td><td class=\"pv-num\">" + r.here +
            "</td><td class=\"pv-num\">" + (r.area != null ? r.area : "—") + "</td><td class=\"pv-num\">" +
            (r.trend ? '<span class="' + trendClass[r.trend] + '">' + trendLabel[r.trend] + "</span>" : "—") + "</td></tr>";
        }).join("") +
        "</tbody></table>";
      return html;
    },
    account: function () {
      if (currentToken) {
        return (
          '<div class="pv-logged-in">' +
            "<p>Logged in as <strong>" + escapeHtml(currentEmail || "") + "</strong></p>" +
            '<button type="button" class="pv-logout-btn">Log out</button>' +
          "</div>"
        );
      }
      return (
        '<form class="pv-login-form">' +
          '<label for="pv-login-email">Email</label>' +
          '<input type="email" id="pv-login-email" required autocomplete="email">' +
          '<label for="pv-login-password">Password</label>' +
          '<input type="password" id="pv-login-password" required autocomplete="current-password">' +
          '<button type="submit" class="pv-login-submit">Log in</button>' +
          '<div class="pv-login-error" hidden></div>' +
          '<p class="pv-login-note">No account? Sign up free at ' + API_BASE.replace("https://", "") + "</p>" +
        "</form>"
      );
    },
  };

  function renderTabStrip(card, data) {
    const tabsEl = card.querySelector(".pv-tabs");
    tabsEl.innerHTML = TABS.map(function (t, i) {
      return '<button class="pv-tab' + (i === 0 ? " pv-active" : "") + '" data-tab="' + t.key + '" type="button">' + t.label + "</button>";
    }).join("");
    tabsEl.querySelectorAll(".pv-tab").forEach(function (btn) {
      btn.addEventListener("click", function () { selectTab(btn.dataset.tab); });
    });
  }

  function selectTab(tabKey) {
    if (!root) return;
    const tabsEl = root.querySelector(".pv-tabs");
    tabsEl.querySelectorAll(".pv-tab").forEach(function (b) { b.classList.toggle("pv-active", b.dataset.tab === tabKey); });
    renderTabContent(tabKey);
  }

  function renderTabContent(tabKey) {
    const contentEl = root.querySelector(".pv-tab-content");
    const renderer = RENDERERS[tabKey];
    contentEl.innerHTML = renderer ? renderer(currentData || {}) : '<p class="pv-empty">Not available.</p>';

    if (tabKey === "account") {
      const form = contentEl.querySelector(".pv-login-form");
      if (form) form.addEventListener("submit", handleLoginSubmit);
      const logoutBtn = contentEl.querySelector(".pv-logout-btn");
      if (logoutBtn) logoutBtn.addEventListener("click", handleLogout);
    } else {
      contentEl.querySelectorAll(".pv-gate-login-btn").forEach(function (btn) {
        btn.addEventListener("click", function () { selectTab("account"); });
      });
    }
  }

  function handleLogout() {
    clearToken().then(function () {
      currentToken = null;
      currentEmail = null;
      currentPremiumData = null;
      updateAccountButton();
      loadReport(); // re-fetch so gated tabs collapse back to the free teaser
      selectTab("account");
    });
  }

  function handleLoginSubmit(e) {
    e.preventDefault();
    const form = e.target;
    const email = form.querySelector("#pv-login-email").value.trim();
    const password = form.querySelector("#pv-login-password").value;
    const errorEl = form.querySelector(".pv-login-error");
    const submitBtn = form.querySelector(".pv-login-submit");
    errorEl.hidden = true;
    submitBtn.disabled = true;
    submitBtn.textContent = "Logging in…";

    fetch(API_BASE + "/api/extension-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email, password: password }),
    })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok) {
          const messages = {
            invalid_credentials: "Incorrect email or password.",
            missing_credentials: "Enter your email and password.",
            not_configured: "Login isn't available on this deployment right now.",
          };
          errorEl.textContent = messages[result.data.error] || "Something went wrong - try again.";
          errorEl.hidden = false;
          submitBtn.disabled = false;
          submitBtn.textContent = "Log in";
          return;
        }
        return setToken(result.data.token, result.data.email).then(function () {
          currentToken = result.data.token;
          currentEmail = result.data.email;
          updateAccountButton();
          return loadReport(); // re-fetch with the new token so gated tabs unlock immediately
        });
      })
      .catch(function () {
        errorEl.textContent = "Couldn't reach UKPropertyInsight - try again.";
        errorEl.hidden = false;
        submitBtn.disabled = false;
        submitBtn.textContent = "Log in";
      });
  }

  function updateAccountButton() {
    const btn = root.querySelector(".pv-account-btn");
    if (currentToken) {
      btn.textContent = currentEmail || "Account";
      btn.classList.add("pv-premium");
    } else {
      btn.textContent = "Log in";
      btn.classList.remove("pv-premium");
    }
  }

  function renderError(message) {
    root.querySelector(".pv-header-score-num").textContent = "—";
    root.querySelector(".pv-tab-content").innerHTML = '<p class="pv-error">' + escapeHtml(message) + "</p>";
  }

  function refreshActiveTab() {
    const activeTab = root.querySelector(".pv-tab.pv-active");
    renderTabContent(activeTab ? activeTab.dataset.tab : "overview");
  }

  // The free /api/extension-report score only reflects the ~6 signals
  // that endpoint fetches anyway; /api/extension-premium-report's own
  // "overview" (once it lands for a logged-in Premium user) is
  // recomputed from the full ~16-signal gather, matching what the same
  // postcode would score on the site itself - this swaps the header/
  // score-card display over to that fuller number without a second
  // fetch, since loadPremiumReport() already has it.
  function applyOverviewScore(overview) {
    const gradeClass = "pv-grade-" + String(overview.grade || "").toLowerCase().replace(/\s+/g, "-");
    root.querySelector(".pv-header-score").className = "pv-header-score " + gradeClass;
    root.querySelector(".pv-header-score-num").textContent = overview.score;
    root.querySelector(".pv-header-score-grade").textContent = overview.grade || "";
  }

  function loadReport() {
    if (!currentPostcode) return Promise.resolve();

    const headers = {};
    if (currentToken) headers["Authorization"] = "Bearer " + currentToken;

    return fetch(API_BASE + "/api/extension-report?postcode=" + encodeURIComponent(currentPostcode), { headers: headers })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          renderError("No UKPropertyInsight data found for this address.");
          return;
        }
        currentData = data;
        applyOverviewScore(data.overview);
        const reportLink = root.querySelector(".pv-header-report-link");
        if (data.report_url) {
          reportLink.href = API_BASE + data.report_url;
          reportLink.hidden = false;
        } else {
          reportLink.hidden = true; // area-level result - no single property page to link to
        }
        renderTabStrip(root, data);
        refreshActiveTab();
        if (currentToken) loadPremiumReport();
      })
      .catch(function () {
        renderError("Couldn't load UKPropertyInsight data right now.");
      });
  }

  function loadPremiumReport() {
    if (!currentPostcode || !currentToken) return Promise.resolve();

    premiumLoading = true;
    return fetch(API_BASE + "/api/extension-premium-report?postcode=" + encodeURIComponent(currentPostcode), {
      headers: { Authorization: "Bearer " + currentToken },
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        premiumLoading = false;
        if (data && data.sections) {
          currentPremiumData = data;
          if (data.overview && currentData) {
            currentData.overview = data.overview;
            applyOverviewScore(data.overview);
          }
          refreshActiveTab();
        }
      })
      .catch(function () { premiumLoading = false; });
  }

  function init() {
    const detected = extractLocation();
    if (!detected) return;
    currentPostcode = detected.postcode;
    postcodeIsPartial = detected.partial;

    getPostcodeOverride().then(function (override) {
      if (override) {
        currentPostcode = override;
        postcodeIsPartial = false;
      }
      initWidget();
    });
  }

  function initWidget() {
    root = buildWidget();
    getStoredHeight().then(function (px) {
      if (px) {
        root.style.height = px + "px";
        root.style.maxHeight = px + "px";
      }
    });
    getToken().then(function (token) {
      currentToken = token;
      if (!chrome?.storage?.local) return loadReport();
      return new Promise(function (resolve) {
        chrome.storage.local.get(["pv_ext_email"], function (result) {
          currentEmail = result.pv_ext_email || null;
          resolve();
        });
      }).then(function () {
        updateAccountButton();
        return loadReport();
      });
    });
  }

  if (document.readyState === "complete") {
    init();
  } else {
    window.addEventListener("load", init);
  }
})();
