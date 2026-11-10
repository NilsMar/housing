"""
Plotly Dash dashboard.
- Scatter plot: price (X) vs score (Y), colour = days listed (recency), size = bedrooms proxy
- Spider/radar chart: appears below scatter when a dot is clicked, showing 6 scoring dimensions
- Value line: OLS regression — dots above-left beat the market
"""

import json

from dotenv import load_dotenv
load_dotenv()

import numpy as np
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

import db

DIMENSIONS = ["location", "price_value", "bedrooms", "outdoor", "tenure", "condition"]
DIM_LABELS = {
    "location": "Location",
    "price_value": "Price value",
    "bedrooms": "Bedrooms",
    "outdoor": "Outdoor space",
    "tenure": "Tenure",
    "condition": "Condition",
}

RECENCY_COLORSCALE = [
    [0.0, "#27ae60"],    # fresh — green
    [0.3, "#f39c12"],    # 1 week — amber
    [1.0, "#e74c3c"],    # stale — red
]


def _days_to_norm(days: int | None) -> float:
    """Normalise days on market to 0–1 for the colorscale (caps at 30 days)."""
    if days is None:
        return 0.5
    return min(1.0, days / 30.0)


def _bed_to_size(beds: int | None) -> int:
    mapping = {1: 10, 2: 14, 3: 18, 4: 22}
    return mapping.get(int(beds) if beds else 0, 14)


def _make_value_line(prices: list, scores: list) -> go.Scatter | None:
    """OLS regression line through the scatter cloud."""
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
        line=dict(color="rgba(100,100,200,0.4)", dash="dash", width=1.5),
        name="Market average",
        hoverinfo="skip",
    )


def make_scatter(listings: list[dict]) -> go.Figure:
    scored = [l for l in listings if l.get("score") is not None and l.get("price")]

    prices = [l["price"] for l in scored]
    scores = [l["score"] for l in scored]
    ids = [l["id"] for l in scored]
    labels = [
        f"{l.get('address') or l.get('title') or l['id']}<br>"
        f"£{l['price']:,} | {l.get('bedrooms', '?')} bed | Score: {l['score']}<br>"
        f"Tenure: {l.get('tenure', 'unknown')} | "
        f"{'Garden ✓' if l.get('has_garden') else 'No garden'} | "
        f"Listed {l.get('days_listed', 0)} days ago"
        for l in scored
    ]
    sizes = [_bed_to_size(l.get("bedrooms")) for l in scored]
    colors = [_days_to_norm(l.get("days_listed")) for l in scored]

    scatter = go.Scatter(
        x=prices,
        y=scores,
        mode="markers",
        marker=dict(
            size=sizes,
            color=colors,
            colorscale=RECENCY_COLORSCALE,
            cmin=0,
            cmax=1,
            colorbar=dict(
                title="Days listed",
                tickvals=[0, 0.33, 0.66, 1.0],
                ticktext=["Today", "10d", "20d", "30d+"],
                len=0.5,
            ),
            line=dict(width=1, color="white"),
            opacity=0.85,
        ),
        text=labels,
        hovertemplate="%{text}<extra></extra>",
        customdata=ids,
        name="Listings",
    )

    traces = [scatter]
    if prices:
        vline = _make_value_line(prices, scores)
        if vline:
            traces.append(vline)

    fig = go.Figure(data=traces)
    fig.update_layout(
        title="London property tracker — score vs. price",
        xaxis=dict(
            title="Asking price (£)",
            tickformat="£,.0f",
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(
            title="Weighted score (0–100)",
            range=[0, 105],
            gridcolor="#f0f0f0",
        ),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="system-ui, sans-serif", size=13),
        legend=dict(orientation="h", y=-0.15),
        margin=dict(t=60, b=60, l=60, r=20),
        clickmode="event+select",
        annotations=[
            dict(
                x=0.01, y=0.99, xref="paper", yref="paper",
                text="⬆ Above line = better value",
                showarrow=False,
                font=dict(size=11, color="rgba(100,100,200,0.7)"),
                xanchor="left", yanchor="top",
            )
        ],
    )
    return fig


def make_spider(listing: dict) -> go.Figure:
    score_json = listing.get("score_json")
    if score_json:
        try:
            breakdown = json.loads(score_json) if isinstance(score_json, str) else score_json
        except (json.JSONDecodeError, TypeError):
            breakdown = {}
    else:
        breakdown = {}

    values = [breakdown.get(d, 0) for d in DIMENSIONS]
    labels = [DIM_LABELS[d] for d in DIMENSIONS]

    # Close the polygon
    values_closed = values + [values[0]]
    labels_closed = labels + [labels[0]]

    address = listing.get("address") or listing.get("title") or listing["id"]
    score = listing.get("score", 0)
    url = listing.get("url", "#")
    price = listing.get("price")
    price_str = f"£{price:,}" if price else ""

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values_closed,
        theta=labels_closed,
        fill="toself",
        fillcolor="rgba(41, 128, 185, 0.2)",
        line=dict(color="#2980b9", width=2),
        name=address,
    ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 10],
                tickvals=[0, 2, 4, 6, 8, 10],
                gridcolor="#e0e0e0",
            ),
            angularaxis=dict(gridcolor="#e0e0e0"),
            bgcolor="white",
        ),
        title=dict(
            text=(
                f"<b>{address}</b><br>"
                f"<span style='font-size:12px'>"
                f"Score: {score}/100 &nbsp;|&nbsp; {price_str} &nbsp;|&nbsp; "
                f"<a href='{url}' target='_blank'>View listing ↗</a>"
                f"</span>"
            ),
            x=0.5,
            font=dict(size=14),
        ),
        paper_bgcolor="white",
        font=dict(family="system-ui, sans-serif", size=12),
        margin=dict(t=80, b=20, l=40, r=40),
        showlegend=False,
    )
    return fig


def build_app() -> Dash:
    app = Dash(__name__, title="London Property Tracker")

    app.layout = html.Div(
        style={"fontFamily": "system-ui, sans-serif", "maxWidth": "1100px", "margin": "0 auto", "padding": "20px"},
        children=[
            html.H1("London Property Tracker", style={"color": "#2c3e50", "marginBottom": "4px"}),
            html.P(
                "Click any dot to drill into its scoring breakdown.",
                style={"color": "#7f8c8d", "marginTop": 0},
            ),
            dcc.Graph(id="scatter", style={"height": "520px"}),
            html.Div(
                id="spider-container",
                style={"marginTop": "20px"},
                children=[
                    dcc.Graph(id="spider", style={"height": "420px", "display": "none"})
                ],
            ),
            dcc.Interval(id="refresh", interval=5 * 60 * 1000, n_intervals=0),
        ],
    )

    @app.callback(
        Output("scatter", "figure"),
        Input("refresh", "n_intervals"),
    )
    def refresh_scatter(_):
        listings = db.get_all_listings()
        return make_scatter(listings)

    @app.callback(
        Output("spider", "figure"),
        Output("spider", "style"),
        Input("scatter", "clickData"),
    )
    def update_spider(click_data):
        hidden = {"height": "420px", "display": "none"}
        visible = {"height": "420px", "display": "block"}

        if not click_data:
            return go.Figure(), hidden

        points = click_data.get("points", [])
        if not points:
            return go.Figure(), hidden

        listing_id = points[0].get("customdata")
        if not listing_id:
            return go.Figure(), hidden

        listing = db.get_listing(listing_id)
        if not listing:
            return go.Figure(), hidden

        return make_spider(listing), visible

    return app


if __name__ == "__main__":
    import os
    db.init_db()
    app = build_app()
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
