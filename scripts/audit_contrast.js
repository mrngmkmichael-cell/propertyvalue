/* Walk the rendered page and report two failures the dark theme can
   produce: a surface that is still painted a light colour, and a text
   colour that no longer clears 4.5:1 against whatever ends up behind it.
   Returns a compact list keyed by selector so each one can be fixed in
   style.css.

   How to run it: paste into the browser console on any page of the site
   with the dark theme on (?theme=dark). To sweep the whole site in one
   go, keep this as window.__audit and fetch each path into a hidden
   1200x900 iframe, adding .theme-dark to the iframe's body before
   calling it.

   Two families of hit are expected and not bugs: .skip-link and the
   gold CTA buttons are meant to be light surfaces.

   Written on 26 Aug 2026 for the dark rollout, where it found three
   faults that reading the stylesheet had missed: an inverted panel that
   went white on white, an inline-styled figure no theme could reach,
   and a second Google map that had not been given the dark basemap. */
(function () {
  function lin(c) { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
  function lum(rgb) { return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]); }
  function ratio(a, b) { var la = lum(a), lb = lum(b), hi = Math.max(la, lb), lo = Math.min(la, lb); return (hi + 0.05) / (lo + 0.05); }
  function parse(s) {
    var m = s && s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    var p = m[1].split(',').map(function (x) { return parseFloat(x); });
    return { rgb: [p[0], p[1], p[2]], a: p.length > 3 ? p[3] : 1 };
  }
  function blend(fg, bg) { return fg.rgb.map(function (c, i) { return c * fg.a + bg[i] * (1 - fg.a); }); }

  /* The colour actually painted behind an element, walking up through
     transparent ancestors the way the compositor does. */
  function effectiveBg(el) {
    var stack = [], n = el;
    while (n && n.nodeType === 1) {
      var p = parse(getComputedStyle(n).backgroundColor);
      if (p && p.a > 0) { stack.push(p); if (p.a === 1) break; }
      n = n.parentElement;
    }
    var base = [5, 5, 5];
    for (var i = stack.length - 1; i >= 0; i--) base = blend(stack[i], base);
    return base;
  }

  function name(el) {
    var s = el.tagName.toLowerCase();
    if (el.id) return s + '#' + el.id;
    var cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\s+/).filter(Boolean);
    return cls.length ? s + '.' + cls.slice(0, 3).join('.') : s;
  }

  var lightSurfaces = {}, lowContrast = {};
  document.querySelectorAll('body *').forEach(function (el) {
    var cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return;
    var r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;

    var own = parse(cs.backgroundColor);
    if (own && own.a > 0.5) {
      var painted = blend(own, effectiveBg(el.parentElement || document.body));
      /* Anything above mid-grey is a light surface that did not get
         themed. Small saturated chips (EPC bands, Ofsted grades) are
         meant to be bright, so only flag things big enough to be a
         panel. */
      if (lum(painted) > 0.35 && r.width * r.height > 6000) {
        var k = name(el);
        if (!lightSurfaces[k]) lightSurfaces[k] = { bg: cs.backgroundColor, area: Math.round(r.width * r.height) };
      }
    }

    var txt = (el.textContent || '').trim();
    var direct = Array.prototype.some.call(el.childNodes, function (n) { return n.nodeType === 3 && n.textContent.trim(); });
    if (!direct || !txt) return;
    var fg = parse(cs.color);
    if (!fg) return;
    var bg = effectiveBg(el);
    var c = ratio(blend(fg, bg), bg);
    var size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight, 10) >= 700;
    var need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    if (c < need) {
      var k2 = name(el);
      if (!lowContrast[k2]) lowContrast[k2] = { ratio: Math.round(c * 100) / 100, need: need, color: cs.color, sample: txt.slice(0, 40) };
    }
  });

  return JSON.stringify({
    url: location.pathname,
    lightSurfaces: lightSurfaces,
    lowContrast: lowContrast
  }, null, 1);
})()
