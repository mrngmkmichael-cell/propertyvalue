/**
 * PropertyValue overlay - injected into Rightmove/Zoopla/OnTheMarket
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
 * bleed into (or be broken by) the widget, and vice versa.
 */
(function () {
  // TODO before publishing: replace with your actual deployed domain
  // (must match host_permissions in manifest.json).
  const API_BASE = "https://YOUR-DEPLOYED-DOMAIN";

  const POSTCODE_RE = /\b([A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2})\b/i;

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

  const STYLE = `
    :host { all: initial; }
    .pv-card {
      position: fixed;
      right: 20px;
      bottom: 20px;
      width: 280px;
      max-height: 80vh;
      overflow-y: auto;
      z-index: 2147483647;
      background: #ffffff;
      border-radius: 16px;
      box-shadow: 0 8px 30px rgba(15, 23, 42, 0.18);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      color: #0f172a;
      font-size: 13px;
      line-height: 1.4;
    }
    .pv-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 14px;
      border-bottom: 1px solid #e5e7eb;
    }
    .pv-logo { font-weight: 800; font-size: 13px; color: #3b5bfd; }
    .pv-close {
      background: none; border: none; cursor: pointer;
      font-size: 16px; line-height: 1; color: #64748b; padding: 2px 4px;
    }
    .pv-body { padding: 14px; }
    .pv-loading { color: #64748b; text-align: center; padding: 8px 0; }
    .pv-score { display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px; }
    .pv-score-num { font-size: 28px; font-weight: 800; }
    .pv-score-grade {
      font-size: 11px; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.04em; padding: 2px 8px; border-radius: 999px;
    }
    .pv-grade-excellent .pv-score-num, .pv-grade-excellent .pv-score-grade { color: #059669; }
    .pv-grade-excellent .pv-score-grade { background: #d1fae5; }
    .pv-grade-good .pv-score-num, .pv-grade-good .pv-score-grade { color: #3b5bfd; }
    .pv-grade-good .pv-score-grade { background: #dbeafe; }
    .pv-grade-fair .pv-score-num, .pv-grade-fair .pv-score-grade { color: #d97706; }
    .pv-grade-fair .pv-score-grade { background: #fef3c7; }
    .pv-grade-below-average .pv-score-num, .pv-grade-below-average .pv-score-grade,
    .pv-grade-poor .pv-score-num, .pv-grade-poor .pv-score-grade { color: #dc2626; }
    .pv-grade-below-average .pv-score-grade, .pv-grade-poor .pv-score-grade { background: #fee2e2; }
    .pv-verdict { color: #334155; margin: 0 0 12px; }
    .pv-stats { list-style: none; margin: 0 0 12px; padding: 0; }
    .pv-stats li {
      display: flex; justify-content: space-between; gap: 8px;
      padding: 5px 0; border-bottom: 1px solid #f1f5f9;
    }
    .pv-stats li:last-child { border-bottom: none; }
    .pv-stats span:first-child { color: #64748b; }
    .pv-link {
      display: block; text-align: center; margin-top: 4px;
      background: #3b5bfd; color: #fff; text-decoration: none;
      font-weight: 700; padding: 9px 12px; border-radius: 10px;
    }
    .pv-error { color: #64748b; }
  `;

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
      '<div class="pv-header"><span class="pv-logo">PropertyValue</span>' +
      '<button class="pv-close" aria-label="Close">✕</button></div>' +
      '<div class="pv-body pv-loading">Loading area report…</div>';
    shadow.appendChild(card);
    card.querySelector(".pv-close").addEventListener("click", function () {
      host.remove();
    });
    return card.querySelector(".pv-body");
  }

  function renderError(body, message) {
    body.innerHTML = '<p class="pv-error">' + message + "</p>";
  }

  function renderData(body, data) {
    if (data.error) {
      renderError(body, "No PropertyValue data found for this address.");
      return;
    }
    const gradeClass = "pv-grade-" + String(data.overview.grade || "").toLowerCase().replace(/\s+/g, "-");
    const priceText = data.avg_price ? "£" + Math.round(data.avg_price).toLocaleString("en-GB") : "No data";
    const floodText = data.flood_zone || "No data";
    const crimeText = data.crime_total != null ? data.crime_total + " recorded" : "No data";
    const schoolsText = data.schools_good_pct != null ? data.schools_good_pct + "% Outstanding/Good" : "No data";
    const reportUrl = API_BASE + data.report_url;

    body.innerHTML =
      '<div class="pv-score ' + gradeClass + '">' +
        '<span class="pv-score-num">' + data.overview.score + "</span>" +
        '<span class="pv-score-grade">' + data.overview.grade + "</span>" +
      "</div>" +
      '<p class="pv-verdict">' + data.overview.verdict + "</p>" +
      '<ul class="pv-stats">' +
        "<li><span>Avg sold price</span><span>" + priceText + "</span></li>" +
        "<li><span>Flood risk</span><span>" + floodText + "</span></li>" +
        "<li><span>Crime nearby</span><span>" + crimeText + "</span></li>" +
        "<li><span>Schools</span><span>" + schoolsText + "</span></li>" +
      "</ul>" +
      '<a class="pv-link" href="' + reportUrl + '" target="_blank" rel="noopener">See full report →</a>';
  }

  function init() {
    const postcode = extractPostcode();
    if (!postcode) return;

    const body = buildWidget();
    fetch(API_BASE + "/api/lookup?postcode=" + encodeURIComponent(postcode))
      .then(function (r) { return r.json(); })
      .then(function (data) { renderData(body, data); })
      .catch(function () { renderError(body, "Couldn't load PropertyValue data right now."); });
  }

  if (document.readyState === "complete") {
    init();
  } else {
    window.addEventListener("load", init);
  }
})();
