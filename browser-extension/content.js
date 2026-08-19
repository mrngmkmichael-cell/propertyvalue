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

  function extractPostcode() {
    const metaCandidates = [
      'meta[property="og:street-address"]',
      'meta[name="address"]',
      'meta[property="og:title"]',
      "title",
    ];
    for (const selector of metaCandidates) {
      const el = document.querySelector(selector);
      const text = el ? el.getAttribute("content") || el.textContent : "";
      const match = text && text.match(POSTCODE_RE);
      if (match) return normalizePostcode(match[1]);
    }
    const bodyMatch = document.body.innerText.match(POSTCODE_RE);
    return bodyMatch ? normalizePostcode(bodyMatch[1]) : null;
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
    .pv-crime-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
    .pv-crime-bar-label { width: 110px; flex-shrink: 0; color: #475569; font-size: 11.5px; text-transform: capitalize; }
    .pv-crime-bar-track { flex: 1; height: 7px; background: #f1f3f7; border-radius: 4px; overflow: hidden; }
    .pv-crime-bar-fill { height: 100%; background: #3b5bfd; }
    .pv-crime-bar-count { width: 30px; text-align: right; color: #667085; font-size: 10.5px; flex-shrink: 0; }
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
    }
    .pv-modal-backdrop[hidden] { display: none; }
    .pv-modal {
      background: #ffffff; border-radius: 16px; padding: 28px; width: min(340px, 100%);
      box-shadow: 0 20px 48px rgba(16, 24, 40, 0.25); position: relative; text-align: center;
    }
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
    .pv-modal-value { margin: 0 0 18px; font-size: 20px; font-weight: 800; color: #12141c; line-height: 1.3; }
    .pv-modal-link { display: inline-block; color: #3b5bfd; font-weight: 700; font-size: 13px; text-decoration: none; }
    .pv-modal-link:hover { text-decoration: underline; }
    .pv-dash-card:not(.pv-dash-locked) { cursor: pointer; transition: border-color 0.15s ease, transform 0.15s ease; }
    .pv-dash-card:not(.pv-dash-locked):hover { border-color: #98a2b3; transform: translateY(-1px); }
  `;

  let currentData = null;
  let currentPremiumData = null;
  let premiumLoading = false;
  let currentToken = null;
  let currentEmail = null;
  let root = null;
  let shadowRoot = null;

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

  function buildWidget() {
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
      '<div class="pv-body">' +
        '<div class="pv-tabs"></div>' +
        '<div class="pv-tab-content"><div class="pv-loading-block"><span class="pv-spinner"></span><p class="pv-loading-text">Loading your UKPropertyInsight report…</p></div></div>' +
      "</div>" +
      '<div class="pv-resize-handle" title="Drag to resize"></div>';
    shadow.appendChild(card);

    const modalBackdrop = document.createElement("div");
    modalBackdrop.className = "pv-modal-backdrop";
    modalBackdrop.hidden = true;
    modalBackdrop.innerHTML =
      '<div class="pv-modal" role="dialog" aria-modal="true">' +
        '<button type="button" class="pv-modal-close" aria-label="Close">✕</button>' +
        '<span class="pv-modal-icon"></span>' +
        '<h3 class="pv-modal-title"></h3>' +
        '<p class="pv-modal-value"></p>' +
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

  function openCardModal(dashCardEl) {
    if (!shadowRoot) return;
    const title = dashCardEl.querySelector(".pv-dash-card-title").textContent;
    const value = dashCardEl.querySelector(".pv-dash-card-value").textContent;
    const iconEl = dashCardEl.querySelector(".pv-dash-card-icon");
    const backdrop = shadowRoot.querySelector(".pv-modal-backdrop");
    const iconTarget = backdrop.querySelector(".pv-modal-icon");
    iconTarget.textContent = iconEl ? iconEl.textContent : "";
    iconTarget.className = "pv-modal-icon" + (iconEl ? " " + iconEl.className.replace("pv-dash-card-icon", "").trim() : "");
    backdrop.querySelector(".pv-modal-title").textContent = title;
    backdrop.querySelector(".pv-modal-value").textContent = value;
    backdrop.querySelector(".pv-modal-link").href = API_BASE + (currentData ? currentData.report_url : "/premium");
    backdrop.hidden = false;
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
    { heading: "Value & Market", cards: ["Area Prosperity", "Price Trend & Forecast", "Rental Analysis"] },
    { heading: "Property & Condition", cards: ["Aspect"] },
    { heading: "Risk & Safety", cards: ["Surface Water Risk", "Sewage Discharge", "Noise", "Radon Gas", "Subsidence Risk", "Air Quality", "Historic Contamination", "Mining Risk"] },
    { heading: "Planning & Heritage", cards: ["Planning Constraints", "Environmental Designations", "Listed Buildings"] },
    { heading: "Location & Connectivity", cards: ["Broadband", "Mobile Signal"] },
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

      html += '<h3 class="pv-category-heading">At a glance</h3><div class="pv-dash-grid">' +
        dashCard("Avg sold price", gbp(s.avg_price)) +
        dashCard("Flood risk", s.flood_zone) +
        dashCard("Crime nearby", s.crime_total != null ? s.crime_total + " recorded" : null) +
        dashCard("Schools", s.schools_good_pct != null ? s.schools_good_pct + "% Outstanding/Good" : null) +
        dashCard("EPC rating", s.epc_rating) +
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
      const max = Math.max.apply(null, c.by_category.map(function (x) { return x.count; }));
      return (
        '<ul class="pv-stats">' +
          "<li><span>Total recorded" + (c.month ? " (" + escapeHtml(c.month) + ")" : "") + "</span><span>" + c.total + "</span></li>" +
        "</ul>" +
        c.by_category.map(function (cat) {
          const pct = max ? Math.round((cat.count / max) * 100) : 0;
          return (
            '<div class="pv-crime-bar-row">' +
              '<span class="pv-crime-bar-label">' + escapeHtml(cat.category) + "</span>" +
              '<span class="pv-crime-bar-track"><span class="pv-crime-bar-fill" style="width:' + pct + '%"></span></span>' +
              '<span class="pv-crime-bar-count">' + cat.count + "</span>" +
            "</div>"
          );
        }).join("")
      );
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

  function loadReport() {
    const postcode = extractPostcode();
    if (!postcode) return Promise.resolve();

    const headers = {};
    if (currentToken) headers["Authorization"] = "Bearer " + currentToken;

    return fetch(API_BASE + "/api/extension-report?postcode=" + encodeURIComponent(postcode), { headers: headers })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          renderError("No UKPropertyInsight data found for this address.");
          return;
        }
        currentData = data;
        const gradeClass = "pv-grade-" + String(data.overview.grade || "").toLowerCase().replace(/\s+/g, "-");
        root.querySelector(".pv-header-score").className = "pv-header-score " + gradeClass;
        root.querySelector(".pv-header-score-num").textContent = data.overview.score;
        root.querySelector(".pv-header-score-grade").textContent = data.overview.grade || "";
        const reportLink = root.querySelector(".pv-header-report-link");
        reportLink.href = API_BASE + data.report_url;
        reportLink.hidden = false;
        renderTabStrip(root, data);
        refreshActiveTab();
        if (currentToken) loadPremiumReport();
      })
      .catch(function () {
        renderError("Couldn't load UKPropertyInsight data right now.");
      });
  }

  function loadPremiumReport() {
    const postcode = extractPostcode();
    if (!postcode || !currentToken) return Promise.resolve();

    premiumLoading = true;
    return fetch(API_BASE + "/api/extension-premium-report?postcode=" + encodeURIComponent(postcode), {
      headers: { Authorization: "Bearer " + currentToken },
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        premiumLoading = false;
        if (data && data.sections) {
          currentPremiumData = data;
          refreshActiveTab();
        }
      })
      .catch(function () { premiumLoading = false; });
  }

  function init() {
    if (!extractPostcode()) return;
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
