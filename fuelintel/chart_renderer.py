from __future__ import annotations

import json
from pathlib import Path

from .market_math import validate_chart_payload


CHART_JS_URLS = (
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js",
    "https://unpkg.com/chart.js@4.4.7/dist/chart.umd.min.js",
)
DISPLAY_DECIMALS = 3


def build_chart_html(payload: dict, chart_js_url: str = CHART_JS_URLS[0]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    light = payload.get("theme") == "light"
    background = "#F3F0E8" if light else "#000000"
    foreground = "#171717" if light else "#E8E8E8"
    muted = "#5F6368" if light else "#A7ADB4"
    axis = "#303030" if light else "#D1D5DB"
    headline = "#1473B8" if light else "#8CC8FF"
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <script src="{chart_js_url}"></script>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; width: 1600px; height: 900px; overflow: hidden; background: {background}; }}
    body {{ color: {foreground}; font-family: Inter, Arial, sans-serif; font-variant-numeric: tabular-nums; }}
    main {{ width: 1600px; height: 900px; padding: 42px 60px 28px; display: grid; grid-template-rows: auto auto 1fr auto; gap: 14px; }}
    h1 {{ margin: 0; color: {headline}; font-size: 37px; line-height: 1.1; letter-spacing: -0.025em; }}
    .subtitle {{ margin-top: 8px; color: {muted}; font-size: 19px; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 24px; min-height: 28px; align-items: center; font-size: 16px; }}
    .legend-item {{ display: flex; gap: 9px; align-items: center; }}
    .swatch {{ width: 27px; height: 4px; display: inline-block; }}
    .pending {{ border-top: 3px dashed #a7adb4; height: 0; }}
    .plot {{ position: relative; min-height: 0; margin: 0 110px 0 100px; overflow: visible; }}
    canvas {{ width: 100% !important; height: 100% !important; overflow: visible; }}
    footer {{ min-height: 34px; display: grid; align-content: end; }}
    .source {{ color: {muted}; font-size: 14px; }}
  </style>
</head>
<body>
<main>
  <header><h1 id="title"></h1><div class="subtitle" id="subtitle"></div></header>
  <div class="legend" id="legend"></div>
  <div class="plot"><canvas id="chart"></canvas></div>
  <footer><div class="source" id="source"></div></footer>
</main>
<script>
const P = {encoded};
const DISPLAY_DECIMALS = Number.isInteger(P.displayDecimals) ? P.displayDecimals : {DISPLAY_DECIMALS};
const VALUE_PREFIX = P.valuePrefix === undefined ? '$' : P.valuePrefix;
const VALUE_SUFFIX = P.valueSuffix || '';
const MUTED = '{muted}', AXIS = '{axis}';
const CHART_KIND = P.chartKind || (P.stepped ? 'step' : 'line');
const formatValue = value => VALUE_PREFIX + Number(value).toFixed(DISPLAY_DECIMALS) + VALUE_SUFFIX;
document.getElementById('title').textContent = P.title;
document.getElementById('subtitle').textContent = P.subtitle;
document.getElementById('source').textContent = P.source;
const legend = document.getElementById('legend');
for (const ds of P.datasets) {{
  if (ds.colorsBySign) {{
    for (const [text, color] of [['Subida', '#71866C'], ['Bajada', '#9A5E57']]) {{
      const item = document.createElement('span'); item.className = 'legend-item';
      const swatch = document.createElement('span'); swatch.className = 'swatch';
      swatch.style.background = color;
      const label = document.createElement('span'); label.textContent = text;
      item.append(swatch, label); legend.append(item);
    }}
    continue;
  }}
  const item = document.createElement('span'); item.className = 'legend-item';
  const swatch = document.createElement('span'); swatch.className = 'swatch' + (ds.pending ? ' pending' : '');
  swatch.style.background = ds.pending ? 'transparent' : ds.color;
  if (ds.pending) swatch.style.borderColor = ds.color;
  const ref = (P.references || []).find(item => item.label === ds.label);
  const range = ref ? ` · hist. ${{formatValue(ref.min)}}–${{formatValue(ref.max)}}` : '';
  const latest = ds.legendValue ? ` · ${{ds.legendValue}}` : '';
  const label = document.createElement('span'); label.textContent = ds.label + latest + range + (ds.pending ? ' · pendiente' : '');
  item.append(swatch, label); legend.append(item);
}}
const annotationPlugin = {{
  id: 'fuelintelAnnotations',
  beforeDatasetsDraw(chart) {{
    if (!P.zeroBaseline) return;
    const ctx = chart.ctx, area = chart.chartArea;
    const y = chart.scales.y.getPixelForValue(0);
    if (y < area.top || y > area.bottom) return;
    ctx.save(); ctx.strokeStyle = '{muted}'; ctx.globalAlpha = 0.45; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(area.left, y); ctx.lineTo(area.right, y); ctx.stroke();
    ctx.restore();
  }},
  afterDatasetsDraw(chart) {{
    const ctx = chart.ctx, area = chart.chartArea;
    ctx.save(); ctx.font = '600 13px Inter, Arial, sans-serif';
    const endpoints = [];
    if (P.showEndpointLabels !== false && !['bar', 'stacked-bar'].includes(CHART_KIND)) chart.data.datasets.forEach((ds, index) => {{
      const meta = chart.getDatasetMeta(index); let last = -1;
      ds.data.forEach((value, i) => {{ if (value !== null && value !== undefined) last = i; }});
      if (last >= 0 && meta.data[last]) {{
        const point = meta.data[last]; ctx.fillStyle = ds.borderColor; ctx.beginPath(); ctx.arc(point.x, point.y, 3.5, 0, Math.PI * 2); ctx.fill();
        endpoints.push({{ point, color: ds.borderColor, text: `${{ds.label}} ${{formatValue(ds.data[last])}}` }});
      }}
    }});
    if (['bar', 'stacked-bar'].includes(CHART_KIND)) chart.data.datasets.forEach((ds, index) => {{
      const meta = chart.getDatasetMeta(index);
      meta.data.forEach((bar, i) => {{
        const value = ds.data[i];
        if (value === null || value === undefined) return;
        ctx.fillStyle = AXIS; ctx.textAlign = 'center'; ctx.font = '600 12px Inter, Arial, sans-serif';
        ctx.fillText(formatValue(value), bar.x, Number(value) >= 0 ? bar.y - 9 : bar.y + 18);
      }});
    }});
    // Resolve endpoint-label collisions and keep labels away from mirrored Y ticks.
    endpoints.sort((a, b) => a.point.y - b.point.y);
    const minimumGap = 24;
    let previousY = area.top - minimumGap;
    for (const endpoint of endpoints) {{
      endpoint.labelY = Math.max(endpoint.point.y - 14, previousY + minimumGap);
      previousY = endpoint.labelY;
    }}
    if (endpoints.length && endpoints[endpoints.length - 1].labelY > area.bottom - 8) {{
      const shift = endpoints[endpoints.length - 1].labelY - (area.bottom - 8);
      for (const endpoint of endpoints) endpoint.labelY -= shift;
    }}
    for (const endpoint of endpoints) {{
      ctx.fillStyle = endpoint.color; ctx.textAlign = 'right';
      ctx.fillText(endpoint.text, endpoint.point.x - 76, endpoint.labelY);
    }}
    ctx.font = '600 11px Inter, Arial, sans-serif'; ctx.fillStyle = MUTED;
    for (const note of (P.annotations || [])) {{
      if (note.axis === 'y') {{
        const y = chart.scales.y.getPixelForValue(note.value);
        ctx.setLineDash([3, 4]); ctx.strokeStyle = '#777';
        ctx.beginPath(); ctx.moveTo(area.left, y); ctx.lineTo(area.right, y); ctx.stroke();
        ctx.setLineDash([]);
        if (note.text) {{ ctx.textAlign = 'left'; ctx.fillText(note.text, area.left + 6, y - 6); }}
      }} else {{
        const x = chart.scales.x.getPixelForValue(note.index);
        ctx.setLineDash([3, 4]); ctx.strokeStyle = '#777';
        ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.bottom); ctx.stroke();
        ctx.setLineDash([]); ctx.textAlign = 'left'; ctx.fillText(note.text, x + 6, area.top + 15);
      }}
    }}
    ctx.strokeStyle = MUTED; ctx.lineWidth = 1;
    for (let index = 0; index < chart.data.labels.length; index += 1) {{
      const x = chart.scales.x.getPixelForValue(index);
      ctx.beginPath(); ctx.moveTo(x, area.bottom); ctx.lineTo(x, area.bottom - 6); ctx.stroke();
    }}
    ctx.restore();
  }}
}};
Chart.register(annotationPlugin);
const isBar = ['bar', 'stacked-bar'].includes(CHART_KIND);
const isArea = CHART_KIND === 'area';
new Chart(document.getElementById('chart'), {{
  type: isBar ? 'bar' : 'line',
  data: {{ labels: P.labels, datasets: P.datasets.map(ds => ({{
    label: ds.label, data: ds.values, borderColor: ds.color,
    backgroundColor: ds.colorsBySign ? ds.values.map(v => Number(v) >= 0 ? '#71866C' : '#9A5E57') : (isArea ? ds.color + '33' : ds.color),
    borderWidth: isBar ? 0 : 3, borderDash: ds.pending ? [8, 6] : [], pointRadius: 0,
    tension: 0, stepped: CHART_KIND === 'step' ? 'after' : false, spanGaps: true,
    fill: isArea, barPercentage: 0.72, categoryPercentage: 0.78
  }})) }},
  options: {{
    responsive: true, maintainAspectRatio: false, animation: false, normalized: true,
    layout: {{ padding: {{ top: 16, right: 96, bottom: 20, left: 90 }} }},
    plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }},
    scales: {{
      x: {{ stacked: CHART_KIND === 'stacked-bar', grid: {{ display: false }}, border: {{ display: false }}, ticks: {{ align: 'inner', padding: 10, color: MUTED, maxTicksLimit: 6, font: {{ family: 'Inter', size: 12 }} }} }},
      y: {{ stacked: CHART_KIND === 'stacked-bar', position: 'right', grace: '8%', grid: {{ display: false }}, border: {{ display: false }}, ticks: {{ mirror: false, padding: 12, align: 'inner', color: AXIS, callback: value => formatValue(value), font: {{ family: 'Inter', size: 12 }} }} }}
    }}
  }}
}});
Promise.resolve(document.fonts.ready).then(() => {{ window.__chartReady = true; }});
</script>
</body></html>"""


def render_chart(payload: dict, destination: Path) -> Path:
    from playwright.sync_api import sync_playwright

    validate_chart_payload(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            installed = next(
                (
                    path for path in (
                        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                    ) if path.exists()
                ),
                None,
            )
            if installed is None:
                raise
            browser = playwright.chromium.launch(headless=True, executable_path=str(installed))
        last_error = None
        for chart_js_url in CHART_JS_URLS:
            page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
            try:
                page.set_content(build_chart_html(payload, chart_js_url), wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_function("window.__chartReady === true", timeout=30_000)
                page.screenshot(path=str(destination), full_page=False)
                last_error = None
                break
            except Exception as error:
                last_error = error
            finally:
                page.close()
        browser.close()
        if last_error is not None:
            raise RuntimeError("No se pudo cargar Chart.js desde los CDN permitidos") from last_error
    return destination


def build_heatmap_html(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<style>
*{{box-sizing:border-box}}html,body{{margin:0;width:1600px;height:900px;background:#000;color:#e8e8e8}}
body{{font-family:Inter,Arial,sans-serif;font-variant-numeric:tabular-nums}}
main{{height:900px;padding:48px 78px 34px;display:grid;grid-template-rows:auto 1fr auto;gap:30px}}
h1{{font-size:38px;margin:0 0 9px;color:#8cc8ff}}.sub,.source{{color:#a7adb4}}.sub{{font-size:19px}}
.grid{{display:grid;grid-template-columns:230px repeat(3,1fr);gap:8px;align-content:center}}
.head,.name,.cell{{padding:23px 18px}}.head{{color:#a7adb4;font-weight:600;text-align:center}}
.name{{font-size:21px;font-weight:700}}.cell{{font-size:26px;font-weight:700;text-align:center}}
footer{{display:grid}}.source{{font-size:14px}}
</style></head><body><main><header><h1 id="title"></h1><div class="sub" id="subtitle"></div></header>
<div class="grid" id="grid"><div></div><div class="head">1 sesión</div><div class="head">1 semana</div><div class="head">1 mes</div></div>
<footer><div class="source" id="source"></div></footer></main>
<script>const P={encoded}; title.textContent=P.title;subtitle.textContent=P.subtitle;source.textContent=P.source;
const color=v=>v===null?'#24272b':v>=5?'#236b43':v>=1?'#315b43':v<=-5?'#7a3030':v<=-1?'#613535':'#33363a';
for(const row of P.rows){{const n=document.createElement('div');n.className='name';n.textContent=row.label;grid.append(n);for(const v of row.values){{const c=document.createElement('div');c.className='cell';c.style.background=color(v);c.textContent=v===null?'n/d':(v>=0?'+':'')+v.toFixed(2)+'%';grid.append(c)}}}}
window.__chartReady=true;</script></body></html>"""


def render_heatmap(payload: dict, destination: Path) -> Path:
    from playwright.sync_api import sync_playwright

    validate_chart_payload(payload, heatmap=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
        page.set_content(build_heatmap_html(payload), wait_until="domcontentloaded")
        page.wait_for_function("window.__chartReady === true")
        page.screenshot(path=str(destination), full_page=False)
        browser.close()
    return destination
