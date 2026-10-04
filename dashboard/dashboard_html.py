"""
Builds the single-page, scroll-navigated dashboard HTML that app.py embeds
via st.components.v1.html().

Why a separate module: this file is pure stdlib (base64/json/os/html only
-- no pandas, no duckdb, no streamlit). That means the exact HTML app.py
will render can be generated and eyeballed from the command line without
the rest of the pipeline being set up, with scripts/preview_dashboard_html.py
and a hand-written sample data dict. app.py's only job is to turn
DataFrames into the plain dict shape this module expects.

Layout: one sticky top nav (Overview / Spending / Budget / Recurring)
with smooth-scroll anchor links, four full-width sections in alternating
tints, a duotone photo banner behind the hero section and another behind
the (dark) Recurring section, and a scroll-triggered fade/slide-in on each
section via IntersectionObserver. This is embedded in a fixed-height,
internally-scrolling iframe (see app.py) -- that's a deliberate trade-off:
Streamlit's components.html() always renders inside an iframe, and
position:sticky only works relative to a scrolling ancestor *inside* that
iframe, not the outer page. A fixed-height scrollable panel is what makes
the sticky nav and anchor scrolling actually work; a free-height iframe
would make the nav sticky to nothing.
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
html, body { height:100%; }
body { margin:0; background:var(--bg); font-family:'Inter',sans-serif; color:var(--text); overflow-y:auto; }
h2 { font-family:'Inter',sans-serif; color:var(--forest-dark); font-weight:700; margin:0; }

.topnav { position:sticky; top:0; z-index:50; background:rgba(247,243,234,0.92); backdrop-filter:blur(6px);
  border-bottom:1px solid var(--border); display:flex; align-items:center; gap:28px; padding:14px 40px; }
.topnav .mark { width:28px; height:28px; border-radius:7px; background:var(--forest); color:#fff;
  display:flex; align-items:center; justify-content:center; flex-shrink:0; font-weight:800; font-size:13px; letter-spacing:-0.02em; }
.topnav .wordmark { font-weight:800; font-size:1.05rem; color:var(--forest-dark); margin-right:10px; white-space:nowrap; letter-spacing:-0.01em; }
.topnav a { font-size:0.86rem; font-weight:600; color:var(--muted); text-decoration:none; padding:6px 2px;
  border-bottom:2px solid transparent; transition:color .15s ease, border-color .15s ease; }
.topnav a:hover, .topnav a.active { color:var(--forest-dark); border-bottom-color:var(--forest); }
.topnav .spacer { flex:1; }
.topnav .asof { font-size:0.78rem; color:var(--muted); white-space:nowrap; }

.section { position:relative; border-bottom:1px solid var(--border); }
.section:last-child { border-bottom:none; }
.section-pad { padding:56px 40px; position:relative; z-index:1; }
.section.tint-b { background:var(--bg-alt); }
.section.tint-c { background:var(--bg); }
.section.tint-d { background:var(--forest-dark); color:#F2EFE4; }
.section.tint-d .section-sub { color:#C7CFC5; }
.inner { max-width:1100px; margin:0 auto; }
.eyebrow { font-size:0.78rem; font-weight:700; text-transform:uppercase; letter-spacing:0.08em; color:var(--forest); margin-bottom:10px; }
.tint-d .eyebrow { color:#9FC2A7; }
.section-head { margin-bottom:28px; max-width:640px; }
.section-head h2 { font-size:1.5rem; }
.tint-d .section-head h2 { color:#F7F5EC; }
.section-sub { color:var(--muted); font-size:0.92rem; margin-top:6px; }

.photo-banner { position:relative; min-height:380px; display:flex; align-items:center; overflow:hidden; }
.photo-banner .bgphoto { position:absolute; inset:0; background-size:cover; background-position:72% center; }
.photo-banner .scrim { position:absolute; inset:0; background:linear-gradient(90deg, rgba(247,243,234,0.97) 0%, rgba(247,243,234,0.90) 28%, rgba(247,243,234,0.55) 50%, rgba(247,243,234,0.18) 68%, rgba(247,243,234,0.05) 100%); }
.photo-banner .content { position:relative; z-index:2; padding:48px 40px; width:100%; }
.photo-banner .inner { max-width:1100px; margin:0 auto; }

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

.dark-banner { position:relative; min-height:340px; display:flex; align-items:center; overflow:hidden; }
.dark-banner .bgphoto { position:absolute; inset:0; background-size:cover; background-position:20% center; }
.dark-banner .scrim { position:absolute; inset:0; background:linear-gradient(90deg, rgba(31,51,40,0.15) 0%, rgba(31,51,40,0.75) 46%, rgba(31,51,40,0.97) 64%, #1F3328 100%); }
.dark-banner .content { position:relative; z-index:2; padding:48px 40px; width:100%; }
.dark-banner .inner { max-width:1100px; margin:0 auto; display:flex; justify-content:flex-end; }
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
<body>

<div class="topnav">
  <div class="mark">PF</div>
  <div class="wordmark">Personal Finance</div>
  <a href="#overview" class="navlink active">Overview</a>
  <a href="#spending" class="navlink">Spending</a>
  <a href="#budget" class="navlink">Budget</a>
  <a href="#recurring" class="navlink">Recurring</a>
  <div class="spacer"></div>
  <div class="asof">&middot; every number traces back to a tested dbt model</div>
</div>

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

  var navLinks = document.querySelectorAll('.navlink');
  var sections = document.querySelectorAll('section[id]');
  var navObserver = new IntersectionObserver(function(entries) {
    entries.forEach(function(entry) {
      if (entry.isIntersecting) {
        navLinks.forEach(function(l) { l.classList.remove('active'); });
        var active = document.querySelector('.navlink[href="#' + entry.target.id + '"]');
        if (active) active.classList.add('active');
      }
    });
  }, { rootMargin: '-45% 0px -45% 0px' });
  sections.forEach(function(s) { navObserver.observe(s); });
</script>

</body>
</html>
"""


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
            "margin": {"l": 10, "r": 10, "t": 10, "b": 10},
            "height": 260,
            "showlegend": False,
            "xaxis": {"gridcolor": "#E3DDC9", "zerolinecolor": "#E3DDC9"},
            "yaxis": {"gridcolor": "#E3DDC9", "zerolinecolor": "#E3DDC9", "rangemode": "tozero"},
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
