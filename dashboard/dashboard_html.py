"""
Builds the dashboard's page body (render_dashboard_html) and its top nav
(render_topnav_html), both embedded by app.py.

Why a separate module: this file is pure stdlib (base64/json/os/html only
-- no pandas, no duckdb, no streamlit). That means the exact HTML app.py
will render can be generated and eyeballed from the command line without
the rest of the pipeline being set up, with scripts/preview_dashboard_html.py
and a hand-written sample data dict. app.py's only job is to turn
DataFrames into the plain dict shape this module expects.

Layout: four full-width sections (Overview / Spending / Budget / Recurring)
in alternating tints, a duotone photo banner behind the hero section and
another behind the (dark) Recurring section, and a scroll-triggered
fade/slide-in on each section via IntersectionObserver. render_dashboard_html()
renders all of that inside a components.html() iframe that auto-sizes itself
to its own content height (see the resize script at the bottom of
_BODY_TEMPLATE) so the OUTER Streamlit page does all the scrolling, as one
continuous page -- not a boxed-in panel with its own internal scrollbar.

The top nav is deliberately a *separate* function (render_topnav_html,
rendered by app.py via st.markdown(..., unsafe_allow_html=True), outside
the iframe) rather than part of the body above: position:sticky only ever
sticks relative to a scrolling ancestor *in the same document* as the
element. Nothing inside an iframe can stick to the outer page's scroll, so
a sticky nav that's meant to follow you down the whole page has to live in
Streamlit's own page, not in the iframe. Because that iframe is embedded
via srcdoc (not sandboxed), it's same-origin with the parent page, so the
nav's script can still reach into it to find section positions and jump to
them on click -- see render_topnav_html()'s own script for how.
"""

import base64
import html as html_mod
import json
import os

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

CATEGORY_PALETTE = [
    "#2F4A3D", "#5C7A68", "#8FA98F", "#B6673F", "#C9A15A", "#6B8F9E", "#9E7B6B", "#4E6B5A",
]

_image_cache: dict[str, str] = {}
_plotly_js_cache: str | None = None


def _b64(filename: str) -> str:
    if filename not in _image_cache:
        with open(os.path.join(_ASSETS_DIR, filename), "rb") as f:
            _image_cache[filename] = base64.b64encode(f.read()).decode("ascii")
    return _image_cache[filename]


def _plotly_js() -> str:
    # Bundled locally (dashboard/assets/plotly-basic.min.js) rather than
    # pulled from a CDN <script src>. st.components.v1.html() renders this
    # whole document inside a sandboxed iframe, and some embedding contexts
    # (including the chat preview this was prototyped in) apply a CSP that
    # silently drops cross-origin <script src> tags -- that showed up as
    # "Plotly is not defined" even though the markup looked right. Inlining
    # the library's source removes that failure mode entirely: nothing to
    # fetch, nothing a CSP can block. "basic" is the smallest official
    # Plotly bundle that still covers scatter/line charts with fills, which
    # is all the trend chart below needs.
    global _plotly_js_cache
    if _plotly_js_cache is None:
        with open(os.path.join(_ASSETS_DIR, "plotly-basic.min.js"), "r", encoding="utf-8") as f:
            _plotly_js_cache = f.read().replace("</script", "<\\/script")
    return _plotly_js_cache


def cat_color(i: int) -> str:
    return CATEGORY_PALETTE[i % len(CATEGORY_PALETTE)]


def _esc(s) -> str:
    return html_mod.escape(str(s), quote=True)


def _sparkline_svg(values: list, color: str, width: int = 160, height: int = 40) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pad = 4
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = pad + i * (width - 2 * pad) / (n - 1)
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    path = " ".join(pts)
    last_x, last_y = pts[-1].split(",")
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="3.5" fill="{color}"/>'
        f"</svg>"
    )


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root {
  --bg:#F7F3EA; --bg-alt:#EFE8D8; --card:#FFFFFF; --border:#E3DDC9; --text:#2B2B26; --muted:#6B6358;
  --forest:#2F4A3D; --forest-dark:#1F3328; --forest-tint:#E4EDE1;
  --under-text:#234D33; --over-tint:#F3E1D7; --over-text:#7A3B22;
}
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; background:var(--bg); font-family:'Inter',sans-serif; color:var(--text); }
h2 { font-family:'Inter',sans-serif; color:var(--forest-dark); font-weight:700; margin:0; }

.section { position:relative; border-bottom:1px solid var(--border); }
.section:last-child { border-bottom:none; }
.section-pad { padding:56px 40px; position:relative; z-index:1; }
.section.tint-b { background:var(--bg-alt); }
.section.tint-c { background:var(--bg); }
.section.tint-d { background:var(--forest-dark); color:#F2EFE4; }
.section.tint-d .section-sub { color:#C7CFC5; }
.inner { max-width:1320px; margin:0 auto; }
.eyebrow { font-size:0.78rem; font-weight:700; text-transform:uppercase; letter-spacing:0.08em; color:var(--forest); margin-bottom:10px; }
.tint-d .eyebrow { color:#9FC2A7; }
.section-head { margin-bottom:28px; max-width:640px; }
.section-head h2 { font-size:1.5rem; }
/* Any h2 on a dark (tint-d) section, not just one inside .section-head --
   the Recurring section's heading sits directly in .textblock instead (see
   below), and without this it was defaulting to the same color as its own
   dark-green background: --forest-dark text on a --forest-dark backdrop,
   which is how "Detected recurring charges" ended up nearly invisible. */
.tint-d h2 { color:#F7F5EC; }
.section-sub { color:var(--muted); font-size:0.92rem; margin-top:6px; }

.photo-banner { position:relative; min-height:380px; display:flex; align-items:center; overflow:hidden; }
.photo-banner .bgphoto { position:absolute; inset:0; background-size:cover; background-position:72% center; }
.photo-banner .scrim { position:absolute; inset:0; background:linear-gradient(90deg, rgba(247,243,234,0.97) 0%, rgba(247,243,234,0.90) 28%, rgba(247,243,234,0.55) 50%, rgba(247,243,234,0.18) 68%, rgba(247,243,234,0.05) 100%); }
.photo-banner .content { position:relative; z-index:2; padding:48px 40px; width:100%; }
.photo-banner .inner { max-width:1320px; margin:0 auto; }

.hero-label { font-size:0.8rem; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px; }
.hero-value { font-weight:800; font-size:3.2rem; line-height:1.05; }
.hero-value.positive { color:var(--forest-dark); }
.hero-value.negative { color:var(--over-text); }
.hero-sub { font-size:0.95rem; color:var(--muted); margin-top:8px; }
.hero-sparkline { margin-top:14px; }
.stat-strip { display:flex; gap:14px; margin-top:26px; max-width:600px; }
.stat-chip { background:rgba(255,255,255,0.82); border:1px solid var(--border); border-radius:12px; padding:14px 16px; flex:1; backdrop-filter:blur(3px); }
.stat-chip-label { font-size:0.70rem; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:3px; }
.stat-chip-value { font-weight:700; font-size:1.2rem; color:var(--forest-dark); }
.stat-chip-sub { font-size:0.72rem; color:var(--muted); margin-top:1px; }

.row { display:flex; gap:20px; }
.panel { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:22px 24px 10px 24px;
  box-shadow:0 1px 2px rgba(31,51,40,0.04), 0 6px 16px rgba(31,51,40,0.05); flex:1; }
.panel-title { font-weight:700; font-size:1.0rem; color:var(--forest-dark); margin-bottom:16px; }

.cat-row { display:flex; align-items:center; gap:14px; padding:9px 0; }
.cat-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.cat-name { width:146px; font-size:0.88rem; flex-shrink:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.cat-track { flex:1; background:var(--forest-tint); border-radius:999px; height:9px; overflow:hidden; }
.cat-fill { height:100%; border-radius:999px; }
.cat-amt { width:80px; text-align:right; font-size:0.86rem; flex-shrink:0; font-variant-numeric:tabular-nums; }

.meter-row { display:flex; align-items:center; gap:14px; padding:12px 0; border-bottom:1px solid var(--border); }
.meter-row:last-child { border-bottom:none; }
.meter-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.meter-main { flex:1; min-width:0; }
.meter-top { display:flex; justify-content:space-between; font-size:0.85rem; margin-bottom:6px; }
.meter-amt { color:var(--muted); font-variant-numeric:tabular-nums; }
.meter-track { background:var(--forest-tint); border-radius:999px; height:8px; overflow:hidden; }
.meter-fill { height:100%; border-radius:999px; background:var(--forest); }
.meter-fill.over { background:#B6673F; }
.badge { flex-shrink:0; font-size:0.72rem; font-weight:600; padding:4px 10px; border-radius:999px; white-space:nowrap; }
.badge.ok { background:var(--forest-tint); color:var(--under-text); }
.badge.over { background:var(--over-tint); color:var(--over-text); }

.dark-banner { position:relative; display:flex; align-items:center; overflow:hidden; }
.dark-banner .bgphoto { position:absolute; inset:0; background-size:cover; background-position:20% center; }
.dark-banner .scrim { position:absolute; inset:0; background:linear-gradient(90deg, rgba(31,51,40,0.15) 0%, rgba(31,51,40,0.75) 46%, rgba(31,51,40,0.97) 64%, #1F3328 100%); }
.dark-banner .content { position:relative; z-index:2; padding:48px 40px; width:100%; }
.dark-banner .inner { max-width:1320px; margin:0 auto; display:flex; justify-content:flex-end; }
.dark-banner .textblock { max-width:620px; }

.sub-row { display:flex; align-items:center; gap:14px; padding:13px 0; border-bottom:1px solid rgba(255,255,255,0.12); }
.sub-row:last-child { border-bottom:none; }
.sub-mark { width:30px; height:30px; border-radius:50%; background:rgba(255,255,255,0.1); color:#F2EFE4; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; flex-shrink:0; letter-spacing:0.02em; }
.sub-main { flex:1; min-width:0; }
.sub-name { font-size:0.92rem; color:#F2EFE4; font-weight:500; }
.sub-tag { font-size:0.74rem; color:#9FAF9C; margin-top:1px; }
.sub-amt, .sub-count { text-align:right; font-size:0.88rem; flex-shrink:0; color:#D8DDD2; font-variant-numeric:tabular-nums; }
.sub-count { font-size:0.74rem; color:#8E9A8B; width:90px; }
.tint-d .panel { background:rgba(255,255,255,0.045); border-color:rgba(255,255,255,0.12); box-shadow:none; }
.tint-d .panel-title { color:#F2EFE4; }

.empty-state { color:var(--muted); font-size:0.9rem; padding:10px 0 18px 0; }

.reveal { opacity:0; transform:translateY(18px); transition:opacity .6s ease, transform .6s ease; }
.reveal.in { opacity:1; transform:translateY(0); }
"""

_BODY_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>__CSS__</style>
<script>__PLOTLY_JS__</script>
</head>
<body data-fp-dashboard="1">

<section id="overview" class="section photo-banner">
  <div class="bgphoto" style="background-image:url('data:image/jpeg;base64,__HERO_B64__')"></div>
  <div class="scrim"></div>
  <div class="content">
    <div class="inner">
      <div class="eyebrow reveal">Overview</div>
      <div class="hero-label reveal">Net &middot; __MONTH_LABEL__</div>
      <div class="hero-value __SENTIMENT_CLASS__ reveal">__NET_DISPLAY__</div>
      <div class="hero-sub reveal">__SENTIMENT_WORD__ &middot; __INCOME_DISPLAY__ income vs __SPENT_DISPLAY__ spent</div>
      <div class="hero-sparkline reveal">__SPARK_SVG__</div>
      <div class="stat-strip reveal">
        <div class="stat-chip"><div class="stat-chip-label">Total spent</div><div class="stat-chip-value">__SPENT_DISPLAY__</div></div>
        <div class="stat-chip"><div class="stat-chip-label">Total income</div><div class="stat-chip-value">__INCOME_DISPLAY__</div></div>
        <div class="stat-chip"><div class="stat-chip-label">Top category</div><div class="stat-chip-value">__TOP_CAT_NAME__</div><div class="stat-chip-sub">__TOP_CAT_AMT__</div></div>
      </div>
    </div>
  </div>
</section>

<section id="spending" class="section tint-b">
  <div class="section-pad">
    <div class="inner">
      <div class="section-head reveal">
        <div class="eyebrow">Spending</div>
        <h2>Where it went, and how the trend is moving</h2>
        <div class="section-sub">Category breakdown and six-month trend, both sourced from the marts layer.</div>
      </div>
      <div class="row reveal">
        <div class="panel">
          <div class="panel-title">Spending by category &middot; __MONTH_LABEL__</div>
          __CATEGORY_ROWS__
        </div>
        <div class="panel">
          <div class="panel-title">Monthly spending trend</div>
          <div id="trend-chart"></div>
        </div>
      </div>
    </div>
  </div>
</section>

<section id="budget" class="section tint-c">
  <div class="section-pad">
    <div class="inner">
      <div class="section-head reveal">
        <div class="eyebrow">Budget</div>
        <h2>Budget vs. actual</h2>
        <div class="section-sub">Every envelope, tracked against what was actually spent.</div>
      </div>
      <div class="panel reveal">
        __BUDGET_ROWS__
      </div>
    </div>
  </div>
</section>

<section id="recurring" class="section dark-banner tint-d">
  <div class="bgphoto" style="background-image:url('data:image/jpeg;base64,__DARK_B64__')"></div>
  <div class="scrim"></div>
  <div class="content">
    <div class="inner">
      <div class="textblock">
        <div class="eyebrow reveal">Recurring</div>
        <h2 class="reveal">Detected recurring charges</h2>
        <div class="section-sub reveal">Flagged by dbt when the same merchant + amount shows up in 3+ distinct months.</div>
        <div class="panel reveal" style="margin-top:22px;">
          <div class="panel-title">Subscriptions</div>
          __RECURRING_ROWS__
        </div>
      </div>
    </div>
  </div>
</section>

<script>
  var trendFig = __TREND_JSON__;
  Plotly.newPlot('trend-chart', trendFig.data, trendFig.layout, {displayModeBar:false, responsive:true});

  var revealEls = document.querySelectorAll('.reveal');
  var io = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) { if (e.isIntersecting) e.target.classList.add('in'); });
  }, { threshold: 0.15 });
  revealEls.forEach(function(el) { io.observe(el); });

  // Resize this iframe to match its own real content height, so the OUTER
  // Streamlit page handles all the scrolling as one continuous page instead
  // of this iframe showing a boxed-in panel with its own internal scrollbar.
  // Safe to reach window.frameElement here because st.components.v1.html()
  // embeds this document via an un-sandboxed srcdoc iframe, which is
  // same-origin with the parent page.
  (function () {
    function resize() {
      try {
        var h = document.documentElement.scrollHeight;
        if (window.frameElement && h > 0) {
          window.frameElement.style.height = h + 'px';
        }
      } catch (e) { /* not embedded the way we expect -- leave Streamlit's static height */ }
    }
    resize();
    window.addEventListener('load', resize);
    window.addEventListener('resize', resize);
    if (window.ResizeObserver) {
      new ResizeObserver(resize).observe(document.body);
    } else {
      // No ResizeObserver (very old browser): a couple of delayed re-checks
      // still catch the Inter webfont swap reflowing text after first paint.
      setTimeout(resize, 400);
      setTimeout(resize, 1200);
    }
  })();
</script>

</body>
</html>
"""

_TOPNAV_TEMPLATE = """
<style>
.fp-topnav { position:sticky; top:0; z-index:999; background:rgba(247,243,234,0.95);
  backdrop-filter:blur(6px); border:1px solid #E3DDC9; border-radius:12px;
  display:flex; align-items:center; gap:28px; padding:12px 20px; font-family:'Inter',sans-serif; }
.fp-topnav .fp-mark { width:26px; height:26px; border-radius:7px; background:#2F4A3D; color:#fff;
  display:flex; align-items:center; justify-content:center; flex-shrink:0; font-weight:800;
  font-size:12px; letter-spacing:-0.02em; }
.fp-topnav .fp-wordmark { font-weight:800; font-size:1.0rem; color:#1F3328; margin-right:6px;
  white-space:nowrap; letter-spacing:-0.01em; }
.fp-topnav a.fp-navlink { font-size:0.86rem; font-weight:600; color:#6B6358; text-decoration:none;
  padding:6px 2px; border-bottom:2px solid transparent; transition:color .15s ease, border-color .15s ease; }
.fp-topnav a.fp-navlink:hover, .fp-topnav a.fp-navlink.active { color:#1F3328; border-bottom-color:#2F4A3D; }
.fp-topnav .fp-spacer { flex:1; }
.fp-topnav .fp-asof { font-size:0.78rem; color:#6B6358; white-space:nowrap; }
@media (max-width: 900px) { .fp-topnav .fp-asof { display:none; } }
</style>
<div class="fp-topnav" id="fp-topnav">
  <div class="fp-mark">B</div>
  <div class="fp-wordmark">Budgeting</div>
  <a href="#overview" class="fp-navlink active" data-fp-anchor="overview">Overview</a>
  <a href="#spending" class="fp-navlink" data-fp-anchor="spending">Spending</a>
  <a href="#budget" class="fp-navlink" data-fp-anchor="budget">Budget</a>
  <a href="#recurring" class="fp-navlink" data-fp-anchor="recurring">Recurring</a>
  <div class="fp-spacer"></div>
  <div class="fp-asof">&middot; every number traces back to a tested dbt model</div>
</div>
"""

# A <script> tag embedded in st.markdown(..., unsafe_allow_html=True) does
# NOT reliably execute -- Streamlit's markdown renderer inserts that HTML
# via innerHTML, and per the DOM spec, script elements inserted that way
# just sit there inert (worse, in this app it also got mangled into
# visible literal text on the page, which is how this bug actually showed
# up). st.components.v1.html() is the channel Streamlit actually supports
# for running arbitrary JS, so the nav's behavior (click-to-scroll,
# highlighting the active link) lives here instead, in its own effectively
# invisible (height=0) iframe -- see app.py. That means this script's
# `document` is its OWN iframe's document, not the real page, so
# everything here goes through window.parent to reach the real nav (in the
# outer page) and the dashboard content (in its own separate iframe,
# found the same way render_dashboard_html()'s own resize script finds
# itself: by the data-fp-dashboard marker on its <body>).
_TOPNAV_SCRIPT = """<!doctype html><html><body><script>
(function () {
  var doc = window.parent.document;

  function dashboardIframe() {
    var frames = doc.querySelectorAll('iframe');
    for (var i = 0; i < frames.length; i++) {
      try {
        var d = frames[i].contentDocument;
        if (d && d.body && d.body.getAttribute('data-fp-dashboard')) return frames[i];
      } catch (e) { /* cross-origin iframe elsewhere on the page -- not ours */ }
    }
    return null;
  }

  function navHeight() {
    var nav = doc.getElementById('fp-topnav');
    return nav ? nav.offsetHeight : 0;
  }

  function scrollToAnchor(id) {
    var ifr = dashboardIframe();
    if (!ifr) return;
    var target = ifr.contentDocument.getElementById(id);
    if (!target) return;
    var ifrRect = ifr.getBoundingClientRect();
    var targetRect = target.getBoundingClientRect();
    var destY = window.parent.scrollY + ifrRect.top + targetRect.top - navHeight() - 10;
    window.parent.scrollTo({ top: Math.max(destY, 0), behavior: 'smooth' });
  }

  doc.querySelectorAll('.fp-topnav .fp-navlink').forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      scrollToAnchor(a.getAttribute('data-fp-anchor'));
    });
  });

  var SECTION_IDS = ['overview', 'spending', 'budget', 'recurring'];

  function updateActiveLink() {
    var ifr = dashboardIframe();
    if (!ifr) return;
    var navH = navHeight();
    // el.getBoundingClientRect() here is relative to the IFRAME's own
    // viewport, which never scrolls (the iframe has no internal overflow
    // -- see the resize script in render_dashboard_html). It's a constant
    // per section, not a reflection of how far the outer page has
    // scrolled. ifrRect.top (the iframe's own position in the outer page)
    // is what actually moves as you scroll -- same combination
    // scrollToAnchor() above uses, just in reverse.
    var ifrRect = ifr.getBoundingClientRect();
    var current = SECTION_IDS[0];
    for (var i = 0; i < SECTION_IDS.length; i++) {
      var el = ifr.contentDocument.getElementById(SECTION_IDS[i]);
      if (!el) continue;
      var top = ifrRect.top + el.getBoundingClientRect().top;
      if (top - navH <= 90) current = SECTION_IDS[i];
    }
    doc.querySelectorAll('.fp-topnav .fp-navlink').forEach(function (a) {
      a.classList.toggle('active', a.getAttribute('data-fp-anchor') === current);
    });
  }

  var ticking = false;
  window.parent.addEventListener('scroll', function () {
    if (ticking) return;
    ticking = true;
    window.parent.requestAnimationFrame(function () { updateActiveLink(); ticking = false; });
  }, { passive: true });
  window.parent.addEventListener('load', updateActiveLink);
  setTimeout(updateActiveLink, 800);
  setTimeout(updateActiveLink, 2000); // catch the dashboard iframe's own resize settling late
})();
</script></body></html>
"""


def render_topnav_html() -> str:
    """The dashboard's top nav markup + styling, standalone -- meant to be
    rendered by app.py via st.markdown(render_topnav_html(),
    unsafe_allow_html=True), placed directly above the
    st.components.v1.html(render_dashboard_html(...)) call. Kept separate
    from render_dashboard_html() on purpose: see the module docstring for
    why a sticky nav that follows you down the page can't live inside that
    function's iframe. Pairs with render_topnav_script_html() for the
    nav's actual click/highlight behavior -- see that function's docstring
    for why the script isn't just included here."""
    return _TOPNAV_TEMPLATE


def render_topnav_script_html() -> str:
    """The nav's behavior (click a link -> smooth-scroll to that section;
    highlight the active link as you scroll), as its own tiny HTML
    document. Meant to be rendered by app.py via
    st.components.v1.html(render_topnav_script_html(), height=0) --
    st.components.v1.html() is Streamlit's actual supported channel for
    running JS (st.markdown's unsafe_allow_html does not reliably execute
    <script> tags at all, and can mangle them into visible text instead of
    silently doing nothing)."""
    return _TOPNAV_SCRIPT


def _category_rows_html(categories: list) -> str:
    if not categories:
        return '<div class="empty-state">No spending recorded this month.</div>'
    rows = []
    for i, c in enumerate(categories):
        color = cat_color(i)
        rows.append(
            f'<div class="cat-row"><div class="cat-dot" style="background:{color}"></div>'
            f'<div class="cat-name">{_esc(c["name"])}</div>'
            f'<div class="cat-track"><div class="cat-fill" style="width:{c["pct"]:.0f}%;background:{color}"></div></div>'
            f'<div class="cat-amt">${c["amt"]:,.0f}</div></div>'
        )
    return "".join(rows)


def _budget_rows_html(budget_rows: list) -> str:
    if not budget_rows:
        return '<div class="empty-state">No budget data for this month.</div>'
    rows = []
    for i, b in enumerate(budget_rows):
        color = cat_color(i)
        fill_class = "over" if b["over"] else ""
        badge_class = "over" if b["over"] else "ok"
        badge_text = "Over budget" if b["over"] else "On track"
        pct_fill = min(b["pct_fill"], 100)
        rows.append(
            f'<div class="meter-row"><div class="meter-dot" style="background:{color}"></div>'
            f'<div class="meter-main"><div class="meter-top">'
            f'<span>{_esc(b["category"])}</span>'
            f'<span class="meter-amt">${b["actual"]:,.0f} of ${b["budgeted"]:,.0f}</span></div>'
            f'<div class="meter-track"><div class="meter-fill {fill_class}" style="width:{pct_fill:.0f}%"></div></div>'
            f'</div><div class="badge {badge_class}">{badge_text}</div></div>'
        )
    return "".join(rows)


def _recurring_rows_html(recurring_rows: list) -> str:
    if not recurring_rows:
        return '<div class="empty-state">No recurring charges detected yet.</div>'
    rows = []
    for r in recurring_rows:
        rows.append(
            f'<div class="sub-row"><div class="sub-mark">{_esc(r["initials"])}</div>'
            f'<div class="sub-main"><div class="sub-name">{_esc(r["name"])}</div>'
            f'<div class="sub-tag">{_esc(r["category"])}</div></div>'
            f'<div class="sub-count">{r["count"]} charges</div>'
            f'<div class="sub-amt">${r["lifetime"]:,.0f} lifetime</div></div>'
        )
    return "".join(rows)


def render_dashboard_html(data: dict) -> str:
    """data keys:
    month_label: str
    net, total_income, total_spent: float
    top_category_name: str, top_category_amt: float | None
    spark_values: list[float]
    categories: list[{"name": str, "amt": float, "pct": float}]
    trend_months: list[str]  (x-axis labels, e.g. "Mar 2026")
    trend_values: list[float]
    budget_rows: list[{"category": str, "budgeted": float, "actual": float, "pct_fill": float, "over": bool}]
    recurring_rows: list[{"name": str, "initials": str, "category": str, "count": int, "lifetime": float}]
    """
    net = data["net"]
    sentiment_class = "positive" if net >= 0 else "negative"
    sentiment_word = "Surplus" if net >= 0 else "Deficit"
    net_display = f'{"-" if net < 0 else ""}${abs(net):,.0f}'
    spark_color = "#2F4A3D" if net >= 0 else "#7A3B22"
    spark_svg = _sparkline_svg(data["spark_values"], spark_color) if len(data.get("spark_values", [])) >= 2 else ""

    top_cat_name = data.get("top_category_name") or "—"
    top_cat_amt = f'${data["top_category_amt"]:,.0f}' if data.get("top_category_amt") is not None else ""

    trend_fig = {
        "data": [{
            "x": data.get("trend_months", []),
            "y": data.get("trend_values", []),
            "type": "scatter",
            "mode": "lines+markers",
            "line": {"color": "#2F4A3D", "width": 2.5, "shape": "spline", "smoothing": 0.3},
            "marker": {"size": 7, "color": "#2F4A3D", "line": {"width": 2, "color": "#FFFFFF"}},
            "fill": "tozeroy",
            "fillcolor": "rgba(47,74,61,0.10)",
            "hovertemplate": "%{x}: $%{y:,.0f}<extra></extra>",
        }],
        "layout": {
            "paper_bgcolor": "#FFFFFF",
            "plot_bgcolor": "#FFFFFF",
            "font": {"family": "Inter, sans-serif", "color": "#2B2B26", "size": 12},
            # l/b give the $ y-axis and month x-axis labels room to actually
            # show up -- 10px all around (the previous values) was tight
            # enough that Plotly's own tick labels got clipped at the plot's
            # edges. automargin:true is a second safety net on top of that:
            # it lets Plotly grow the margin further on its own if a
            # particular label (e.g. "$12,000") needs more room than this.
            "margin": {"l": 48, "r": 16, "t": 16, "b": 40},
            "height": 260,
            "showlegend": False,
            "xaxis": {"gridcolor": "#E3DDC9", "zerolinecolor": "#E3DDC9", "automargin": True},
            "yaxis": {
                "gridcolor": "#E3DDC9", "zerolinecolor": "#E3DDC9", "rangemode": "tozero",
                "automargin": True, "tickprefix": "$",
            },
        },
    }

    substitutions = {
        "__PLOTLY_JS__": _plotly_js(),
        "__CSS__": CSS,
        "__HERO_B64__": _b64("hero_banner.jpg"),
        "__DARK_B64__": _b64("dark_banner.jpg"),
        "__MONTH_LABEL__": _esc(data["month_label"]),
        "__SENTIMENT_CLASS__": sentiment_class,
        "__SENTIMENT_WORD__": sentiment_word,
        "__NET_DISPLAY__": net_display,
        "__INCOME_DISPLAY__": f'${data["total_income"]:,.0f}',
        "__SPENT_DISPLAY__": f'${data["total_spent"]:,.0f}',
        "__SPARK_SVG__": spark_svg,
        "__TOP_CAT_NAME__": _esc(top_cat_name),
        "__TOP_CAT_AMT__": top_cat_amt,
        "__CATEGORY_ROWS__": _category_rows_html(data.get("categories", [])),
        "__BUDGET_ROWS__": _budget_rows_html(data.get("budget_rows", [])),
        "__RECURRING_ROWS__": _recurring_rows_html(data.get("recurring_rows", [])),
        "__TREND_JSON__": json.dumps(trend_fig),
    }

    out = _BODY_TEMPLATE
    for token, value in substitutions.items():
        out = out.replace(token, value)
    return out
