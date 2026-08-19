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
 * bleed into (or be broken by) the widget, and vice versa. A compact
 * card pinned to the top-right of the viewport, expanded by default -
 * unlike a full-width bar, it never competes with the listing photos
 * for horizontal space, and the report is immediately visible rather
 * than needing a click to reveal.
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
    { key: "summary", label: "Summary" },
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
      top: 16px; right: 16px;
      width: 380px;
      max-height: calc(100vh - 32px);
      display: flex;
      flex-direction: column;
      z-index: 2147483647;
      background: #ffffff;
      border: 1px solid #e4e7ec;
      border-radius: 16px;
      box-shadow: 0 16px 32px rgba(16, 24, 40, 0.10), 0 2px 6px rgba(16, 24, 40, 0.05);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      color: #12141c;
      font-size: 13px;
      line-height: 1.45;
    }
    .pv-header {
      display: flex; align-items: center; gap: 8px;
      padding: 12px 14px; border-bottom: 1px solid #e4e7ec; flex-shrink: 0;
    }
    .pv-logo { display: flex; align-items: center; gap: 7px; font-weight: 800; font-size: 13px; }
    .pv-mark {
      display: inline-flex; align-items: center; justify-content: center;
      width: 22px; height: 22px; border-radius: 8px; background: #3b5bfd; color: #fff;
      font-size: 13px; font-weight: 800; flex-shrink: 0;
    }
    .pv-header-score { display: flex; align-items: baseline; gap: 3px; margin-left: 4px; flex-shrink: 0; }
    .pv-header-score-num { font-size: 16px; font-weight: 800; }
    .pv-grade-excellent .pv-header-score-num { color: #059669; }
    .pv-grade-good .pv-header-score-num { color: #3b5bfd; }
    .pv-grade-fair .pv-header-score-num { color: #d97706; }
    .pv-grade-below-average .pv-header-score-num, .pv-grade-poor .pv-header-score-num { color: #dc2626; }
    .pv-spacer { flex: 1; }
    .pv-icon-btn {
      background: none; border: none; cursor: pointer; color: #98a2b3;
      font-size: 14px; padding: 3px 5px; border-radius: 6px; flex-shrink: 0;
    }
    .pv-icon-btn:hover { background: #f1f3f7; color: #667085; }
    .pv-account-btn {
      background: #f1f3f7; border: none; border-radius: 999px; padding: 4px 10px;
      font-size: 11px; font-weight: 700; color: #12141c; cursor: pointer; flex-shrink: 0;
      white-space: nowrap; max-width: 110px; overflow: hidden; text-overflow: ellipsis;
    }
    .pv-account-btn.pv-premium { background: #eef1ff; color: #3b5bfd; }
    .pv-body { overflow-y: auto; flex: 1; min-height: 0; }
    .pv-body.pv-collapsed { display: none; }
    .pv-tabs {
      display: flex; gap: 2px; padding: 0 10px; border-bottom: 1px solid #e4e7ec;
      overflow-x: auto; flex-shrink: 0;
    }
    .pv-tab {
      background: none; border: none; padding: 9px 9px; font-size: 11.5px; font-weight: 700;
      color: #667085; cursor: pointer; border-bottom: 2px solid transparent; white-space: nowrap;
    }
    .pv-tab.pv-active { color: #3b5bfd; border-bottom-color: #3b5bfd; }
    .pv-tab-content { padding: 14px; min-height: 100px; }
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
    .pv-cta {
      display: block; text-align: center; margin-top: 12px;
      background: #3b5bfd; color: #fff; text-decoration: none;
      font-weight: 700; padding: 9px 12px; border-radius: 10px;
    }
    .pv-summary-verdict { color: #334155; margin: 0 0 10px; }
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
  `;

  let currentData = null;
  let currentToken = null;
  let currentEmail = null;
  let root = null;

  function buildWidget() {
    const host = document.createElement("div");
    host.id = "pv-overlay-host";
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLE;
    shadow.appendChild(style);

    const card = document.createElement("div");
    card.className = "pv-card";
    card.innerHTML =
      '<div class="pv-header">' +
        '<span class="pv-logo"><span class="pv-mark">U</span>UKPropertyInsight</span>' +
        '<span class="pv-header-score"><span class="pv-header-score-num">…</span></span>' +
        '<span class="pv-spacer"></span>' +
        '<button class="pv-account-btn" type="button">Log in</button>' +
        '<button class="pv-icon-btn pv-collapse-btn" type="button" aria-label="Collapse">▾</button>' +
        '<button class="pv-icon-btn pv-close-btn" type="button" aria-label="Close">✕</button>' +
      "</div>" +
      '<div class="pv-body">' +
        '<div class="pv-tabs"></div>' +
        '<div class="pv-tab-content"><p class="pv-loading">Loading…</p></div>' +
      "</div>";
    shadow.appendChild(card);

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

    return card;
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

  const RENDERERS = {
    summary: function (data) {
      const s = data.summary;
      return (
        '<p class="pv-summary-verdict">' + escapeHtml(data.overview.verdict || "") + "</p>" +
        '<ul class="pv-stats">' +
          "<li><span>Avg sold price</span><span>" + gbp(s.avg_price) + "</span></li>" +
          "<li><span>Flood risk</span><span>" + escapeHtml(s.flood_zone || "No data") + "</span></li>" +
          "<li><span>Crime nearby</span><span>" + (s.crime_total != null ? s.crime_total + " recorded" : "No data") + "</span></li>" +
          "<li><span>Schools</span><span>" + (s.schools_good_pct != null ? s.schools_good_pct + "% Outstanding/Good" : "No data") + "</span></li>" +
          "<li><span>EPC rating</span><span>" + escapeHtml(s.epc_rating || "No data") + "</span></li>" +
        "</ul>" +
        '<a class="pv-cta" href="' + API_BASE + data.report_url + '" target="_blank" rel="noopener">See full report →</a>'
      );
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
      if (!rows.length) return '<p class="pv-empty">No recorded sales at this exact address.</p>';
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
      const gateBtn = contentEl.querySelector(".pv-gate-login-btn");
      if (gateBtn) gateBtn.addEventListener("click", function () { selectTab("account"); });
    }
  }

  function handleLogout() {
    clearToken().then(function () {
      currentToken = null;
      currentEmail = null;
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
        renderTabStrip(root, data);
        const activeTab = root.querySelector(".pv-tab.pv-active");
        renderTabContent(activeTab ? activeTab.dataset.tab : "summary");
      })
      .catch(function () {
        renderError("Couldn't load UKPropertyInsight data right now.");
      });
  }

  function init() {
    if (!extractPostcode()) return;
    root = buildWidget();
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
