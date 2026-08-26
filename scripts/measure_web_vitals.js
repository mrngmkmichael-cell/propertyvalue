/* Measure CLS, LCP and the layout-shift culprits the way Lighthouse
   does, in-page. Paste into the console on a fresh load, then wait.
   Returns after the page has settled.

   CLS here is the sum of unexpected shifts (no input within 500ms),
   which is what PageSpeed reports. The culprit list names the elements
   that moved, so a fix can be aimed rather than guessed. */
window.__perf = function (settleMs) {
  settleMs = settleMs || 6000;
  return new Promise(function (resolve) {
    var cls = 0, shifts = [], lcp = null, longTasks = [];

    try {
      new PerformanceObserver(function (l) {
        l.getEntries().forEach(function (e) {
          if (e.hadRecentInput) return;
          cls += e.value;
          (e.sources || []).forEach(function (s) {
            var n = s.node;
            var name = n ? (n.nodeName || '').toLowerCase() +
              (n.id ? '#' + n.id : '') +
              (n.className && typeof n.className === 'string'
                ? '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.') : '')
              : '(detached)';
            shifts.push({ el: name, value: Math.round(e.value * 1000) / 1000 });
          });
        });
      }).observe({ type: 'layout-shift', buffered: true });
    } catch (e) {}

    try {
      new PerformanceObserver(function (l) {
        var es = l.getEntries();
        var last = es[es.length - 1];
        if (last) lcp = { time: Math.round(last.startTime), el: last.element ?
          (last.element.nodeName || '').toLowerCase() +
          (last.element.className && typeof last.element.className === 'string'
            ? '.' + last.element.className.trim().split(/\s+/)[0] : '') : String(last.url || '') };
      }).observe({ type: 'largest-contentful-paint', buffered: true });
    } catch (e) {}

    try {
      new PerformanceObserver(function (l) {
        l.getEntries().forEach(function (e) {
          longTasks.push(Math.round(e.duration));
        });
      }).observe({ type: 'longtask', buffered: true });
    } catch (e) {}

    setTimeout(function () {
      /* collapse the culprit list to a total per element */
      var byEl = {};
      shifts.forEach(function (s) { byEl[s.el] = Math.round(((byEl[s.el] || 0) + s.value) * 1000) / 1000; });
      var nav = performance.getEntriesByType('navigation')[0] || {};
      resolve({
        CLS: Math.round(cls * 1000) / 1000,
        LCP_ms: lcp && lcp.time,
        LCP_element: lcp && lcp.el,
        culprits: Object.keys(byEl).sort(function (a, b) { return byEl[b] - byEl[a]; })
          .slice(0, 8).map(function (k) { return k + ' = ' + byEl[k]; }),
        longTasks_ms: longTasks.sort(function (a, b) { return b - a; }).slice(0, 5),
        domContentLoaded_ms: Math.round(nav.domContentLoadedEventEnd || 0),
        load_ms: Math.round(nav.loadEventEnd || 0)
      });
    }, settleMs);
  });
};
'perf harness installed';
