from __future__ import annotations

import json
from pathlib import Path


CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"


def build_chart_html(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <script src="{CHART_JS_URL}"></script>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; width: 1600px; height: 900px; overflow: hidden; background: #000; }}
    body {{ color: #e8e8e8; font-family: Inter, Arial, sans-serif; font-variant-numeric: tabular-nums; }}
    main {{ width: 1600px; height: 900px; padding: 42px 54px 28px; display: grid; grid-template-rows: auto auto 1fr auto; gap: 14px; }}
    h1 {{ margin: 0; font-size: 37px; line-height: 1.1; letter-spacing: -0.025em; }}
    .subtitle {{ margin-top: 8px; color: #a7adb4; font-size: 19px; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 24px; min-height: 28px; align-items: center; font-size: 16px; }}
    .legend-item {{ display: flex; gap: 9px; align-items: center; }}
    .swatch {{ width: 27px; height: 4px; display: inline-block; }}
    .pending {{ border-top: 3px dashed #a7adb4; height: 0; }}
    .plot {{ position: relative; min-height: 0; }}
    canvas {{ width: 100% !important; height: 100% !important; }}
    footer {{ min-height: 86px; display: grid; align-content: end; gap: 7px; }}
    .insight {{ color: #e8e8e8; font-size: 17px; font-weight: 600; }}
    .source {{ color: #a7adb4; font-size: 13px; }}
    .credit {{ color: #e8e8e8; font-size: 13px; font-weight: 700; }}
  </style>
</head>
<body>
<main>
  <header><h1 id="title"></h1><div class="subtitle" id="subtitle"></div></header>
  <div class="legend" id="legend"></div>
  <div class="plot"><canvas id="chart"></canvas></div>
  <footer><div class="insight" id="insight"></div><div class="source" id="source"></div><div class="credit" id="credit"></div></footer>
</main>
<script>
const P = {encoded};
document.getElementById('title').textContent = P.title;
document.getElementById('subtitle').textContent = P.subtitle;
document.getElementById('insight').textContent = P.insight;
document.getElementById('source').textContent = P.source;
document.getElementById('credit').textContent = P.credit;
const legend = document.getElementById('legend');
for (const ds of P.datasets) {{
  const item = document.createElement('span'); item.className = 'legend-item';
  const swatch = document.createElement('span'); swatch.className = 'swatch' + (ds.pending ? ' pending' : '');
  swatch.style.background = ds.pending ? 'transparent' : ds.color;
  if (ds.pending) swatch.style.borderColor = ds.color;
  const label = document.createElement('span'); label.textContent = ds.label + (ds.pending ? ' · pendiente' : '');
  item.append(swatch, label); legend.append(item);
}}
const annotationPlugin = {{
  id: 'fuelintelAnnotations',
  afterDatasetsDraw(chart) {{
    const ctx = chart.ctx, area = chart.chartArea;
    ctx.save(); ctx.font = '600 13px Inter, Arial, sans-serif';
    chart.data.datasets.forEach((ds, index) => {{
      const meta = chart.getDatasetMeta(index); let last = -1;
      ds.data.forEach((value, i) => {{ if (value !== null && value !== undefined) last = i; }});
      if (last >= 0 && meta.data[last]) {{
        const point = meta.data[last]; ctx.fillStyle = ds.borderColor; ctx.beginPath(); ctx.arc(point.x, point.y, 3.5, 0, Math.PI * 2); ctx.fill();
        ctx.textAlign = 'right'; ctx.fillText(`${{ds.label}} $${{Number(ds.data[last]).toFixed(3)}}`, area.right - 6, point.y - 9);
      }}
    }});
    ctx.font = '600 11px Inter, Arial, sans-serif'; ctx.fillStyle = '#a7adb4';
    for (const note of (P.annotations || [])) {{
      const x = chart.scales.x.getPixelForValue(note.index);
      const y = chart.scales.y.getPixelForValue(note.value);
      ctx.setLineDash([3, 4]); ctx.strokeStyle = '#777';
      ctx.beginPath(); ctx.moveTo(x, y - 23); ctx.lineTo(x, y + 23); ctx.stroke();
      ctx.setLineDash([]); ctx.textAlign = 'left'; ctx.fillText(note.text, x + 5, y - 9);
    }}
    let refY = area.top + 18; ctx.textAlign = 'right';
    for (const ref of (P.references || [])) {{
      ctx.fillStyle = ref.color; ctx.fillText(`${{ref.label}} · hist. $${{ref.min}}–$${{ref.max}}`, area.right - 6, refY); refY += 17;
    }}
    ctx.strokeStyle = '#6f747a'; ctx.lineWidth = 1;
    for (let index = 0; index < chart.data.labels.length; index += 1) {{
      const x = chart.scales.x.getPixelForValue(index);
      ctx.beginPath(); ctx.moveTo(x, area.bottom); ctx.lineTo(x, area.bottom - 6); ctx.stroke();
    }}
    ctx.restore();
  }}
}};
Chart.register(annotationPlugin);
new Chart(document.getElementById('chart'), {{
  type: 'line',
  data: {{ labels: P.labels, datasets: P.datasets.map(ds => ({{
    label: ds.label, data: ds.values, borderColor: ds.color, backgroundColor: ds.color,
    borderWidth: 3, borderDash: ds.pending ? [8, 6] : [], pointRadius: 0,
    tension: 0, stepped: P.stepped ? 'after' : false, spanGaps: true
  }})) }},
  options: {{
    responsive: true, maintainAspectRatio: false, animation: false, normalized: true,
    layout: {{ padding: {{ top: 12, right: 18, left: 32 }} }},
    plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }},
    scales: {{
      x: {{ grid: {{ display: false }}, border: {{ display: false }}, ticks: {{ color: '#a7adb4', maxTicksLimit: 6, font: {{ family: 'Inter', size: 12 }} }} }},
      y: {{ position: 'right', grid: {{ display: false }}, border: {{ display: false }}, ticks: {{ color: '#d1d5db', callback: value => '$' + Number(value).toFixed(P.decimals || 2), font: {{ family: 'Inter', size: 12 }} }} }}
    }}
  }}
}});
Promise.resolve(document.fonts.ready).then(() => {{ window.__chartReady = true; }});
</script>
</body></html>"""


def render_chart(payload: dict, destination: Path) -> Path:
    from playwright.sync_api import sync_playwright

    destination.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
        page.set_content(build_chart_html(payload), wait_until="networkidle", timeout=60_000)
        page.wait_for_function("window.__chartReady === true", timeout=30_000)
        page.screenshot(path=str(destination), full_page=False)
        browser.close()
    return destination

