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
 * bleed into (or be broken by) the widget, and vice versa. Pinned to
 * the top of the viewport rather than floating over content, matching
 * the "loads at the top of the page" pattern of similar tools - a
 * slim always-visible summary strip that expands into a full tabbed
 * report on click, rather than an unsolicited full panel shoving the
 * listing photos down on every page load.
 */
(function () {
  // TODO before publishing: replace with your actual deployed domain
  // (must match host_permissions in manifest.json).
  const API_BASE = "https://YOUR-DEPLOYED-DOMAIN";

  const POSTCODE_RE = /\b([A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2})\b/i;

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

  function extractPostcode() {
    // Meta tags are the most reliable source when present (several
    // portals put the full address there for SEO) - checked first,
    // then fall back to a full-page text scan.
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

  const STYLE = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .pv-root {
      position: fixed;
      top: 0; left: 0; right: 0;
      z-index: 2147483647;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      color: #0f172a;
      font-size: 13px;
      line-height: 1.4;
    }
    .pv-strip {
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 8px 16px;
      background: #ffffff;
      border-bottom: 1px solid #e5e7eb;
      box-shadow: 0 2px 10px rgba(15, 23, 42, 0.08);
      cursor: pointer;
    }
    .pv-strip-logo { display: flex; align-items: center; gap: 6px; font-weight: 800; flex-shrink: 0; }
    .pv-mark {
      display: inline-flex; align-items: center; justify-content: center;
      width: 22px; height: 22px; border-radius: 6px; background: #3b5bfd; color: #fff;
      font-size: 13px; font-weight: 800;
    }
    .pv-strip-score { display: flex; align-items: baseline; gap: 4px; flex-shrink: 0; }
    .pv-strip-score-num { font-size: 18px; font-weight: 800; }
    .pv-strip-verdict { color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }
    .pv-strip-toggle {
      background: #f1f5f9; border: none; border-radius: 8px; padding: 6px 10px;
      font-weight: 700; font-size: 12px; color: #334155; cursor: pointer; flex-shrink: 0;
    }
    .pv-strip-close {
      background: none; border: none; cursor: pointer; font-size: 15px; color: #94a3b8;
      padding: 2px 4px; flex-shrink: 0;
    }
    .pv-grade-excellent .pv-strip-score-num { color: #059669; }
    .pv-grade-good .pv-strip-score-num { color: #3b5bfd; }
    .pv-grade-fair .pv-strip-score-num { color: #d97706; }
    .pv-grade-below-average .pv-strip-score-num, .pv-grade-poor .pv-strip-score-num { color: #dc2626; }
    .pv-panel {
      display: none;
      background: #ffffff;
      border-bottom: 1px solid #e5e7eb;
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.1);
      max-height: 70vh;
      overflow-y: auto;
    }
    .pv-panel.pv-open { display: block; }
    .pv-tabs {
      display: flex; gap: 2px; padding: 0 16px; border-bottom: 1px solid #e5e7eb;
      overflow-x: auto;
    }
    .pv-tab {
      background: none; border: none; padding: 10px 12px; font-size: 12px; font-weight: 700;
      color: #64748b; cursor: pointer; border-bottom: 2px solid transparent; white-space: nowrap;
    }
    .pv-tab.pv-active { color: #3b5bfd; border-bottom-color: #3b5bfd; }
    .pv-tab-content { padding: 16px; min-height: 120px; }
    .pv-loading, .pv-empty { color: #64748b; text-align: center; padding: 20px 0; }
    .pv-error { color: #dc2626; text-align: center; padding: 20px 0; }
    .pv-stats { list-style: none; margin: 0 0 12px; padding: 0; }
    .pv-stats li {
      display: flex; justify-content: space-between; gap: 8px;
      padding: 6px 0; border-bottom: 1px solid #f1f5f9;
    }
    .pv-stats li:last-child { border-bottom: none; }
    .pv-stats span:first-child { color: #64748b; }
    .pv-stats span:last-child { font-weight: 600; }
    .pv-table { width: 100%; border-collapse: collapse; font-size: 12px; }
    .pv-table th {
      text-align: left; color: #64748b; font-weight: 700; font-size: 11px;
      text-transform: uppercase; letter-spacing: 0.03em; padding: 6px 8px; border-bottom: 1px solid #e5e7eb;
    }
    .pv-table th.pv-num, .pv-table td.pv-num { text-align: right; }
    .pv-table td { padding: 7px 8px; border-bottom: 1px solid #f1f5f9; }
    .pv-badge {
      display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700;
      background: #eef2ff; color: #3b5bfd;
    }
    .pv-badge-outstanding, .pv-badge-good { background: #d1fae5; color: #059669; }
    .pv-badge-requires-improvement { background: #fef3c7; color: #d97706; }
    .pv-badge-inadequate { background: #fee2e2; color: #dc2626; }
    .pv-crime-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
    .pv-crime-bar-label { width: 130px; flex-shrink: 0; color: #475569; font-size: 12px; text-transform: capitalize; }
    .pv-crime-bar-track { flex: 1; height: 8px; background: #f1f5f9; border-radius: 4px; overflow: hidden; }
    .pv-crime-bar-fill { height: 100%; background: #3b5bfd; }
    .pv-crime-bar-count { width: 36px; text-align: right; color: #64748b; font-size: 11px; flex-shrink: 0; }
    .pv-map-frame { width: 100%; height: 260px; border: none; border-radius: 8px; }
    .pv-cta {
      display: block; text-align: center; margin-top: 14px;
      background: #3b5bfd; color: #fff; text-decoration: none;
      font-weight: 700; padding: 10px 12px; border-radius: 10px;
    }
    .pv-summary-verdict { color: #334155; margin: 0 0 12px; }
  `;

  function buildWidget() {
    const host = document.createElement("div");
    host.id = "pv-overlay-host";
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLE;
    shadow.appendChild(style);

    const root = document.createElement("div");
    root.className = "pv-root";
    root.innerHTML =
      '<div class="pv-strip">' +
        '<span class="pv-strip-logo"><span class="pv-mark">U</span>UKPropertyInsight</span>' +
        '<span class="pv-strip-score"><span class="pv-strip-score-num">…</span></span>' +
        '<span class="pv-strip-verdict">Loading area report…</span>' +
        '<button class="pv-strip-toggle" type="button">Show report</button>' +
        '<button class="pv-strip-close" type="button" aria-label="Close">✕</button>' +
      "</div>" +
      '<div class="pv-panel">' +
        '<div class="pv-tabs"></div>' +
        '<div class="pv-tab-content"><p class="pv-loading">Loading…</p></div>' +
      "</div>";
    shadow.appendChild(root);

    const panel = root.querySelector(".pv-panel");
    const strip = root.querySelector(".pv-strip");
    const toggleBtn = root.querySelector(".pv-strip-toggle");
    function togglePanel() {
      const open = panel.classList.toggle("pv-open");
      toggleBtn.textContent = open ? "Hide report" : "Show report";
    }
    strip.addEventListener("click", function (e) {
      if (e.target.closest(".pv-strip-close")) return;
      togglePanel();
    });
    root.querySelector(".pv-strip-close").addEventListener("click", function (e) {
      e.stopPropagation();
      host.remove();
    });

    return root;
  }

  function renderError(root, message) {
    root.querySelector(".pv-strip-verdict").textContent = message;
    root.querySelector(".pv-strip-score-num").textContent = "—";
    root.querySelector(".pv-tab-content").innerHTML = '<p class="pv-error">' + message + "</p>";
  }

  function renderTabStrip(root, data) {
    const tabsEl = root.querySelector(".pv-tabs");
    tabsEl.innerHTML = TABS.map(function (t, i) {
      return '<button class="pv-tab' + (i === 0 ? " pv-active" : "") + '" data-tab="' + t.key + '" type="button">' + t.label + "</button>";
    }).join("");
    tabsEl.querySelectorAll(".pv-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        tabsEl.querySelectorAll(".pv-tab").forEach(function (b) { b.classList.remove("pv-active"); });
        btn.classList.add("pv-active");
        renderTabContent(root, btn.dataset.tab, data);
      });
    });
  }

  function ratingBadgeClass(label) {
    if (!label) return "";
    return "pv-badge-" + label.toLowerCase().replace(/\s+/g, "-");
  }

  const RENDERERS = {
    summary: function (data) {
      const s = data.summary;
      return (
        '<p class="pv-summary-verdict">' + (data.overview.verdict || "") + "</p>" +
        '<ul class="pv-stats">' +
          "<li><span>Avg sold price</span><span>" + gbp(s.avg_price) + "</span></li>" +
          "<li><span>Flood risk</span><span>" + (s.flood_zone || "No data") + "</span></li>" +
          "<li><span>Crime nearby</span><span>" + (s.crime_total != null ? s.crime_total + " recorded" : "No data") + "</span></li>" +
          "<li><span>Schools</span><span>" + (s.schools_good_pct != null ? s.schools_good_pct + "% Outstanding/Good" : "No data") + "</span></li>" +
          "<li><span>EPC rating</span><span>" + (s.epc_rating || "No data") + "</span></li>" +
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
      return (
        '<table class="pv-table"><thead><tr><th>Address</th><th>Date</th><th class="pv-num">Price</th></tr></thead><tbody>' +
        rows.map(function (t) {
          return "<tr><td>" + t.address + "</td><td>" + t.date + "</td><td class=\"pv-num\">" + gbp(t.amount) + "</td></tr>";
        }).join("") +
        "</tbody></table>"
      );
    },
    comparables: function (data) {
      const c = data.comparables;
      if (!c || !c.transactions || !c.transactions.length) return '<p class="pv-empty">No nearby sold comparables found.</p>';
      return (
        '<ul class="pv-stats">' +
          "<li><span>Nearby sales found</span><span>" + c.count + "</span></li>" +
          "<li><span>Median price</span><span>" + gbp(c.median) + "</span></li>" +
        "</ul>" +
        '<table class="pv-table"><thead><tr><th>Address</th><th class="pv-num">Distance</th><th class="pv-num">Price</th></tr></thead><tbody>' +
        c.transactions.map(function (t) {
          return "<tr><td>" + t.address + "</td><td class=\"pv-num\">" + distanceText(t.distance_m) + "</td><td class=\"pv-num\">" + gbp(t.amount) + "</td></tr>";
        }).join("") +
        "</tbody></table>"
      );
    },
    schools: function (data) {
      const rows = data.schools || [];
      if (!rows.length) return '<p class="pv-empty">No nearby schools found.</p>';
      return (
        '<table class="pv-table"><thead><tr><th>School</th><th>Phase</th><th class="pv-num">Distance</th><th class="pv-num">Ofsted</th></tr></thead><tbody>' +
        rows.map(function (s) {
          const badge = s.ofsted_rating_label
            ? '<span class="pv-badge ' + ratingBadgeClass(s.ofsted_rating_label) + '">' + s.ofsted_rating_label + "</span>"
            : "—";
          return "<tr><td>" + s.name + "</td><td>" + (s.phase || "—") + "</td><td class=\"pv-num\">" + distanceText(s.distance_m) + "</td><td class=\"pv-num\">" + badge + "</td></tr>";
        }).join("") +
        "</tbody></table>"
      );
    },
    epc: function (data) {
      if (!data.epc) return '<p class="pv-empty">No EPC certificate found for this postcode.</p>';
      return (
        '<ul class="pv-stats">' +
          "<li><span>Energy rating</span><span>" + data.epc.rating + "</span></li>" +
          "<li><span>Certificate date</span><span>" + data.epc.date + "</span></li>" +
        "</ul>"
      );
    },
    demographics: function (data) {
      const d = data.demographics || {};
      return (
        '<ul class="pv-stats">' +
          "<li><span>Household income</span><span>" + (d.household_income != null ? gbp(d.household_income) + "/yr" : "No data") + "</span></li>" +
          "<li><span>Deprivation</span><span>" + (d.imd_label || "No data") + "</span></li>" +
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
          "<li><span>Total recorded" + (c.month ? " (" + c.month + ")" : "") + "</span><span>" + c.total + "</span></li>" +
        "</ul>" +
        c.by_category.map(function (cat) {
          const pct = max ? Math.round((cat.count / max) * 100) : 0;
          return (
            '<div class="pv-crime-bar-row">' +
              '<span class="pv-crime-bar-label">' + cat.category + "</span>" +
              '<span class="pv-crime-bar-track"><span class="pv-crime-bar-fill" style="width:' + pct + '%"></span></span>' +
              '<span class="pv-crime-bar-count">' + cat.count + "</span>" +
            "</div>"
          );
        }).join("")
      );
    },
  };

  function renderTabContent(root, tabKey, data) {
    const contentEl = root.querySelector(".pv-tab-content");
    const renderer = RENDERERS[tabKey];
    contentEl.innerHTML = renderer ? renderer(data) : '<p class="pv-empty">Not available.</p>';
  }

  function renderData(root, data) {
    if (data.error) {
      renderError(root, "No UKPropertyInsight data found for this address.");
      return;
    }
    const gradeClass = "pv-grade-" + String(data.overview.grade || "").toLowerCase().replace(/\s+/g, "-");
    root.querySelector(".pv-strip").classList.add(gradeClass);
    root.querySelector(".pv-strip-score-num").textContent = data.overview.score;
    root.querySelector(".pv-strip-verdict").textContent = data.postcode + " — " + (data.overview.grade || "");
    renderTabStrip(root, data);
    renderTabContent(root, "summary", data);
  }

  function init() {
    const postcode = extractPostcode();
    if (!postcode) return;

    const root = buildWidget();
    fetch(API_BASE + "/api/extension-report?postcode=" + encodeURIComponent(postcode))
      .then(function (r) { return r.json(); })
      .then(function (data) { renderData(root, data); })
      .catch(function () { renderError(root, "Couldn't load UKPropertyInsight data right now."); });
  }

  if (document.readyState === "complete") {
    init();
  } else {
    window.addEventListener("load", init);
  }
})();
