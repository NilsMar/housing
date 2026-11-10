"""
Generates a self-contained index.html with:
  - Scatter plot: price vs score, colour = recency, size = bedrooms
  - Spider/radar chart: appears on scatter click, showing 6 scoring dimensions
  - Value line: OLS regression through the scatter cloud
No server required — all interactivity is handled by Plotly.js in the browser.
"""

import json
import sys
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import db

DIMENSIONS = ["location", "price_value", "bedrooms", "outdoor", "tenure", "condition"]
DIM_LABELS = ["Location", "Price value", "Bedrooms", "Outdoor", "Tenure", "Condition"]

RECENCY_COLORSCALE = [
    [0.0, "#27ae60"],
    [0.33, "#f39c12"],
    [1.0, "#e74c3c"],
]


def _days_norm(days):
    if days is None:
        return 0.5
    return min(1.0, (days or 0) / 30.0)


def _bed_size(beds):
    return {1: 10, 2: 14, 3: 18, 4: 22}.get(int(beds) if beds else 0, 14)


def _value_line(prices, scores):
    if len(prices) < 3:
        return None
    x = np.array(prices, dtype=float)
    y = np.array(scores, dtype=float)
    m, b = np.polyfit(x, y, 1)
    x_range = np.linspace(x.min(), x.max(), 100)
    return go.Scatter(
        x=x_range.tolist(),
        y=(m * x_range + b).tolist(),
        mode="lines",
        line=dict(color="rgba(100,100,200,0.35)", dash="dash", width=1.5),
        name="Market average",
        hoverinfo="skip",
    )


def build_figure(listings: list[dict]) -> go.Figure:
    scored = [l for l in listings if l.get("score") is not None and l.get("price")]

    prices = [l["price"] for l in scored]
    scores = [l["score"] for l in scored]
    ids = [l["id"] for l in scored]

    hover = [
        (
            f"<b>{l.get('address') or l.get('title') or l['id']}</b><br>"
            f"£{l['price']:,} &bull; {l.get('bedrooms', '?')} bed<br>"
            f"Score: {l['score']} &bull; {l.get('tenure', 'unknown')}<br>"
            f"{'Garden ✓' if l.get('has_garden') else 'No garden'} &bull; "
            f"Listed {l.get('days_listed') or 0}d ago"
        )
        for l in scored
    ]

    scatter = go.Scatter(
        x=prices,
        y=scores,
        mode="markers",
        marker=dict(
            size=[_bed_size(l.get("bedrooms")) for l in scored],
            color=[_days_norm(l.get("days_listed")) for l in scored],
            colorscale=RECENCY_COLORSCALE,
            cmin=0, cmax=1,
            colorbar=dict(
                title="Days listed",
                tickvals=[0, 0.33, 0.66, 1.0],
                ticktext=["Today", "10d", "20d", "30d+"],
                len=0.5,
            ),
            line=dict(width=1, color="white"),
            opacity=0.85,
        ),
        text=hover,
        hovertemplate="%{text}<extra></extra>",
        customdata=ids,
        name="Listings",
    )

    traces = [scatter]
    if prices:
        vline = _value_line(prices, scores)
        if vline:
            traces.append(vline)

    fig = go.Figure(data=traces)
    fig.update_layout(
        title="London property tracker — score vs. price",
        xaxis=dict(title="Asking price (£)", tickformat="£,.0f", gridcolor="#f0f0f0"),
        yaxis=dict(title="Weighted score (0–100)", range=[0, 105], gridcolor="#f0f0f0"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="system-ui, sans-serif", size=13),
        legend=dict(orientation="h", y=-0.15),
        margin=dict(t=60, b=60, l=60, r=20),
        annotations=[dict(
            x=0.01, y=0.99, xref="paper", yref="paper",
            text="⬆ Above line = better value",
            showarrow=False,
            font=dict(size=11, color="rgba(100,100,200,0.7)"),
            xanchor="left", yanchor="top",
        )],
    )
    return fig


def _spider_traces(breakdown: dict) -> dict:
    values = [breakdown.get(d, 0) for d in DIMENSIONS]
    closed = values + [values[0]]
    labels = DIM_LABELS + [DIM_LABELS[0]]
    return {"r": closed, "theta": labels}


def build_html(output_path: str = "index.html") -> None:
    db.init_db()
    listings = db.get_all_listings()

    fig = build_figure(listings)
    scatter_json = fig.to_json()

    # Pre-compute spider data for every scored listing
    spider_data = {}
    for l in listings:
        if not l.get("score_json"):
            continue
        try:
            breakdown = json.loads(l["score_json"]) if isinstance(l["score_json"], str) else l["score_json"]
        except (json.JSONDecodeError, TypeError):
            continue
        spider_data[l["id"]] = {
            "traces": _spider_traces(breakdown),
            "address": l.get("address") or l.get("title") or l["id"],
            "score": l.get("score", 0),
            "price": l.get("price"),
            "url": l.get("url", "#"),
            "tenure": l.get("tenure", "unknown"),
            "bedrooms": l.get("bedrooms"),
            "has_garden": l.get("has_garden", 0),
        }

    spider_json = json.dumps(spider_data)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>London Property Tracker</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: system-ui, sans-serif; background: #f8f9fa; color: #2c3e50; padding: 20px; }}
    h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
    p.sub {{ color: #7f8c8d; font-size: 0.85rem; margin-bottom: 16px; }}
    #scatter-div {{ background: white; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.08); padding: 8px; }}
    #spider-section {{ margin-top: 20px; display: none; }}
    #spider-header {{ padding: 12px 16px; background: white; border-radius: 8px 8px 0 0;
                      border-bottom: 1px solid #eee; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
    #spider-header h2 {{ font-size: 1rem; }}
    #spider-header a {{ color: #2980b9; font-size: 0.85rem; }}
    #spider-header .meta {{ color: #7f8c8d; font-size: 0.82rem; margin-top: 2px; }}
    #spider-div {{ background: white; border-radius: 0 0 8px 8px;
                   box-shadow: 0 1px 4px rgba(0,0,0,.08); padding: 8px; }}
  </style>
</head>
<body>
  <h1>London Property Tracker</h1>
  <p class="sub">Click any dot to drill into its scoring breakdown. Dot size = bedrooms. Colour = days listed.</p>
  <div id="scatter-div">
    <div id="scatter"></div>
  </div>
  <div id="spider-section">
    <div id="spider-header">
      <h2 id="spider-title"></h2>
      <div class="meta" id="spider-meta"></div>
    </div>
    <div id="spider-div">
      <div id="spider"></div>
    </div>
  </div>

  <script>
    const scatterSpec = {scatter_json};
    const spiderData = {spider_json};

    Plotly.newPlot('scatter', scatterSpec.data, scatterSpec.layout, {{responsive: true}});

    document.getElementById('scatter').on('plotly_click', function(evt) {{
      const pt = evt.points[0];
      const id = pt.customdata;
      const d = spiderData[id];
      if (!d) return;

      document.getElementById('spider-section').style.display = 'block';
      document.getElementById('spider-title').textContent = d.address;

      const price = d.price ? '£' + d.price.toLocaleString('en-GB') : '';
      const garden = d.has_garden ? 'Garden ✓' : 'No garden';
      document.getElementById('spider-meta').textContent =
        `Score: ${{d.score}}/100  ·  ${{price}}  ·  ${{d.bedrooms || '?'}} bed  ·  ${{d.tenure}}  ·  ${{garden}}`;

      const link = document.getElementById('spider-title');
      link.innerHTML = `${{d.address}} <a href="${{d.url}}" target="_blank" style="font-size:.8rem;color:#2980b9">View listing ↗</a>`;

      Plotly.react('spider', [{{
        type: 'scatterpolar',
        r: d.traces.r,
        theta: d.traces.theta,
        fill: 'toself',
        fillcolor: 'rgba(41,128,185,0.18)',
        line: {{color: '#2980b9', width: 2}},
        name: d.address,
      }}], {{
        polar: {{
          radialaxis: {{visible: true, range: [0, 10], tickvals: [0,2,4,6,8,10]}},
          bgcolor: 'white',
        }},
        paper_bgcolor: 'white',
        font: {{family: 'system-ui, sans-serif', size: 12}},
        margin: {{t: 20, b: 20, l: 40, r: 40}},
        showlegend: false,
        height: 400,
      }});

      document.getElementById('spider-section').scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
    }});
  </script>
</body>
</html>"""

    Path(output_path).write_text(html, encoding="utf-8")
    print(f"[report] Written {output_path} ({len(listings)} listings, {len(spider_data)} scored)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "index.html"
    build_html(out)
