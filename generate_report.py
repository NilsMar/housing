"""
Generates a self-contained index.html with:
  - Filter bar: price range, min score slider, tenure toggle
  - Scatter plot: price vs score, colour = recency, size = bedrooms
  - Price-reduced overlay: purple triangle on listings that dropped in price
  - Value line: OLS regression through the scatter cloud
  - Spider/radar chart: appears on scatter click, showing 6 scoring dimensions
All interactivity is client-side JS — no server required.
"""

import json
import sys
from pathlib import Path

import db

DIMENSIONS = ["location", "commute", "price_value", "bedrooms", "outdoor", "tenure", "condition"]
DIM_LABELS = ["Location", "Commute", "Price value", "Bedrooms", "Outdoor", "Tenure", "Condition"]
DIM_WEIGHTS = [15, 10, 25, 20, 15, 10, 5]  # must match config.yaml weights in same order


def build_html(output_path: str = "index.html") -> None:
    db.init_db()
    listings = db.get_all_listings()

    rows = []
    for l in listings:
        if not l.get("price") or not l.get("score"):
            continue
        try:
            breakdown = json.loads(l["score_json"]) if l.get("score_json") else {}
        except (json.JSONDecodeError, TypeError):
            breakdown = {}

        address = l.get("address") or l.get("title") or l["id"]
        rows.append({
            "id": l["id"],
            "price": l["price"],
            "score": round(l["score"], 1),
            "bedrooms": l.get("bedrooms"),
            "days_listed": l.get("days_listed") or 0,
            "address": address,
            "url": l.get("url", "#"),
            "tenure": l.get("tenure", "unknown"),
            "has_garden": l.get("has_garden", 0),
            "image_url": l.get("image_url") or "",
            "previous_price": l.get("previous_price"),
            "price_changed_at": l.get("price_changed_at"),
            "commute_m": l.get("commute_m"),
            "lease_years": l.get("lease_years"),
            "breakdown": {d: breakdown.get(d, 0) for d in DIMENSIONS},
        })

    listings_json = json.dumps(rows)
    dim_keys_json = json.dumps(DIMENSIONS)
    dim_labels_json = json.dumps(DIM_LABELS)
    dim_weights_json = json.dumps(DIM_WEIGHTS)

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>London Property Tracker</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,sans-serif;background:#f5f6fa;color:#2c3e50;padding:20px}
    h1{font-size:1.4rem;margin-bottom:4px}
    p.sub{color:#7f8c8d;font-size:.85rem;margin-bottom:16px}

    #filters{background:white;border-radius:8px;padding:14px 18px;margin-bottom:14px;
             box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;flex-wrap:wrap;gap:20px;align-items:flex-end}
    .fg label{display:block;font-size:.75rem;font-weight:600;color:#7f8c8d;text-transform:uppercase;
              letter-spacing:.04em;margin-bottom:5px}
    .price-row{display:flex;align-items:center;gap:6px}
    .price-row input{width:110px;padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:.85rem}
    .price-row span{color:#aaa;font-size:.85rem}
    .score-row{display:flex;align-items:center;gap:8px}
    .score-row input[type=range]{width:120px;accent-color:#2980b9}
    .score-row span{font-size:.85rem;font-weight:600;color:#2980b9;min-width:28px}
    .pills{display:flex;gap:5px;flex-wrap:wrap}
    .pill{padding:4px 10px;border:1px solid #ddd;border-radius:20px;cursor:pointer;font-size:.8rem;
          background:white;color:#555;transition:all .15s}
    .pill.active{background:#2980b9;color:white;border-color:#2980b9}
    #count{margin-left:auto;font-size:.8rem;color:#7f8c8d;align-self:center}

    #scatter-wrap{background:white;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.08);padding:8px}
    #spider-section{margin-top:14px;display:none}
    #spider-header{background:white;border-radius:8px 8px 0 0;padding:12px 16px;
                   border-bottom:1px solid #eee;box-shadow:0 1px 4px rgba(0,0,0,.08)}
    #spider-header h2{font-size:1rem}
    #spider-header .meta{color:#7f8c8d;font-size:.82rem;margin-top:3px}
    #spider-wrap{background:white;border-radius:0 0 8px 8px;box-shadow:0 1px 4px rgba(0,0,0,.08);padding:8px}
  </style>
</head>
<body>
  <h1>London Property Tracker</h1>
  <p class="sub">Click any dot to drill into its scoring breakdown. &nbsp;▼ purple triangle = price reduced.</p>

  <div id="filters">
    <div class="fg">
      <label>Price</label>
      <div class="price-row">
        <input type="number" id="price-min" placeholder="No min" step="25000">
        <span>–</span>
        <input type="number" id="price-max" placeholder="No max" step="25000">
      </div>
    </div>
    <div class="fg">
      <label>Min score &nbsp;<span id="score-label" style="color:#2980b9;font-weight:700">0</span></label>
      <div class="score-row">
        <input type="range" id="score-min" min="0" max="100" value="0">
      </div>
    </div>
    <div class="fg">
      <label>Tenure</label>
      <div class="pills" id="tenure-pills">
        <button class="pill active" data-t="">All</button>
        <button class="pill" data-t="freehold">Freehold</button>
        <button class="pill" data-t="share_freehold">Share of freehold</button>
        <button class="pill" data-t="leasehold">Leasehold</button>
      </div>
    </div>
    <div id="count"></div>
  </div>

  <div id="scatter-wrap"><div id="scatter"></div></div>

  <div id="spider-section">
    <div id="spider-header">
      <h2 id="spider-title"></h2>
      <div class="meta" id="spider-meta"></div>
    </div>
    <div id="spider-wrap"><div id="spider"></div></div>
  </div>

<script>
const ALL = __LISTINGS_DATA__;
const DIM_KEYS = __DIM_KEYS__;
const DIM_LABELS = __DIM_LABELS__;
const DIM_WEIGHTS = __DIM_WEIGHTS__;
const RECENCY = [[0,'#27ae60'],[0.33,'#f39c12'],[1,'#e74c3c']];

let tenure = '', minScore = 0, priceMin = 0, priceMax = Infinity;

function bedSz(b){ return({1:9,2:13,3:17,4:21}[b]||13); }
function daysNorm(d){ return Math.min(1,(d||0)/30); }

function tenureStr(l){
  if(l.tenure==='leasehold') return l.lease_years ? `Leasehold (${l.lease_years}yr)` : 'Leasehold';
  if(l.tenure==='share_freehold') return 'Share of freehold';
  if(l.tenure==='freehold') return 'Freehold';
  return l.tenure||'Unknown tenure';
}
function commuteStr(l){
  return l.commute_m != null ? `${l.commute_m} min` : '—';
}
function hover(l){
  const pr = l.previous_price ? ` &nbsp;<b style="color:#8e44ad">↓ from £${l.previous_price.toLocaleString('en-GB')}</b>` : '';
  const gd = l.has_garden ? 'Garden ✓' : 'No garden';
  return `<b>${l.address}</b><br>£${l.price.toLocaleString('en-GB')}${pr}<br>`+
         `Score: ${l.score} · ${l.bedrooms||'?'} bed · ${tenureStr(l)}<br>`+
         `🚇 ${commuteStr(l)} commute · ${gd} · Listed ${l.days_listed}d ago`;
}

function valueLine(ls){
  if(ls.length < 3) return null;
  const xs = ls.map(l=>l.price), ys = ls.map(l=>l.score);
  const n=xs.length, xm=xs.reduce((a,b)=>a+b)/n, ym=ys.reduce((a,b)=>a+b)/n;
  const num=xs.map((x,i)=>(x-xm)*(ys[i]-ym)).reduce((a,b)=>a+b);
  const den=xs.map(x=>(x-xm)**2).reduce((a,b)=>a+b);
  if(!den) return null;
  const m=num/den, bv=ym-m*xm;
  const x0=Math.min(...xs), x1=Math.max(...xs);
  return{type:'scatter',x:[x0,x1],y:[m*x0+bv,m*x1+bv],mode:'lines',
         line:{color:'rgba(100,100,200,0.35)',dash:'dash',width:1.5},
         name:'Market average',hoverinfo:'skip'};
}

function buildTraces(ls){
  const main = {
    type:'scatter', x:ls.map(l=>l.price), y:ls.map(l=>l.score), mode:'markers',
    marker:{
      size:ls.map(l=>bedSz(l.bedrooms)),
      color:ls.map(l=>daysNorm(l.days_listed)),
      colorscale:RECENCY, cmin:0, cmax:1,
      colorbar:{title:{text:'Days listed',side:'right'},
                tickvals:[0,0.33,0.66,1],ticktext:['Today','10d','20d','30d+'],len:0.5},
      line:{width:1,color:'white'}, opacity:0.85,
    },
    text:ls.map(hover), hovertemplate:'%{text}<extra></extra>',
    customdata:ls.map(l=>l.id), name:'Listings',
  };

  const red = ls.filter(l=>l.previous_price && l.previous_price > l.price);
  const overlay = {
    type:'scatter', x:red.map(l=>l.price), y:red.map(l=>l.score), mode:'markers',
    marker:{symbol:'triangle-down',size:14,color:'#8e44ad',line:{width:2,color:'white'}},
    text:red.map(l=>`↓ Price reduced: £${l.previous_price.toLocaleString('en-GB')} → £${l.price.toLocaleString('en-GB')}`),
    hovertemplate:'%{text}<extra></extra>',
    customdata:red.map(l=>l.id), name:'↓ Price reduced',
  };

  const traces = [main, overlay];
  const vl = valueLine(ls);
  if(vl) traces.push(vl);
  return traces;
}

const layout = {
  xaxis:{title:'Asking price (£)',tickformat:'£,.0f',gridcolor:'#f0f0f0',
         range:[0, 1050000], dtick:200000},
  yaxis:{title:'Weighted score (0–100)',range:[0,105],gridcolor:'#f0f0f0'},
  plot_bgcolor:'white', paper_bgcolor:'white',
  font:{family:'system-ui,sans-serif',size:13},
  legend:{orientation:'h',y:-0.18},
  margin:{t:40,b:60,l:60,r:20},
  annotations:[{x:0.01,y:0.99,xref:'paper',yref:'paper',
    text:'⬆ Above line = better value',showarrow:false,
    font:{size:11,color:'rgba(100,100,200,0.65)'},xanchor:'left',yanchor:'top'}],
};

function filtered(){
  return ALL.filter(l=>
    l.price >= priceMin && l.price <= priceMax &&
    l.score >= minScore &&
    (!tenure || l.tenure === tenure)
  );
}

function render(){
  const ls = filtered();
  document.getElementById('count').textContent = ls.length + ' listing' + (ls.length!==1?'s':'');
  Plotly.react('scatter', buildTraces(ls), layout);
}

Plotly.newPlot('scatter', buildTraces(ALL), layout, {responsive:true});
document.getElementById('count').textContent = ALL.length + ' listing' + (ALL.length!==1?'s':'');

// Filter wiring
document.getElementById('price-min').addEventListener('input', e=>{
  priceMin = parseInt(e.target.value)||0; render();
});
document.getElementById('price-max').addEventListener('input', e=>{
  priceMax = parseInt(e.target.value)||Infinity; render();
});
document.getElementById('score-min').addEventListener('input', e=>{
  minScore = parseInt(e.target.value);
  document.getElementById('score-label').textContent = minScore;
  render();
});
document.querySelectorAll('#tenure-pills .pill').forEach(btn=>{
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('#tenure-pills .pill').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    tenure = btn.dataset.t;
    render();
  });
});

// Spider drill-down
document.getElementById('scatter').on('plotly_click', evt=>{
  const pt = evt.points[0];
  const id = pt.customdata;
  const l = ALL.find(x=>x.id===id);
  if(!l) return;

  document.getElementById('spider-section').style.display='block';
  const pr = l.previous_price && l.previous_price > l.price
    ? ` &nbsp;<span style="color:#8e44ad">↓ from £${l.previous_price.toLocaleString('en-GB')}</span>` : '';
  document.getElementById('spider-title').innerHTML =
    `${l.address} &nbsp;<a href="${l.url}" target="_blank" style="font-size:.8rem;color:#2980b9">View listing ↗</a>`;
  document.getElementById('spider-meta').innerHTML =
    `Score: ${l.score}/100 &nbsp;·&nbsp; £${l.price.toLocaleString('en-GB')}${pr} &nbsp;·&nbsp; `+
    `${l.bedrooms||'?'} bed &nbsp;·&nbsp; ${tenureStr(l)} &nbsp;·&nbsp; `+
    `🚇 ${commuteStr(l)} commute`;

  const vals = DIM_KEYS.map(k => l.breakdown[k] || 0);
  const closed = [...vals, vals[0]];
  // Axis labels include weight so users see the multiplier
  const axisLabels = DIM_LABELS.map((lbl,i) => `${lbl} (×${DIM_WEIGHTS[i]}%)`);
  const theta = [...axisLabels, axisLabels[0]];
  // Hover shows: raw score, weight, and resulting contribution to total
  const hoverText = DIM_LABELS.map((lbl,i) => {
    const contrib = (vals[i] * DIM_WEIGHTS[i] / 10).toFixed(1);
    return `<b>${lbl}</b><br>${vals[i]}/10 × ${DIM_WEIGHTS[i]}% = <b>${contrib} pts</b>`;
  });

  Plotly.react('spider',[{
    type:'scatterpolar', r:closed, theta:theta, fill:'toself',
    fillcolor:'rgba(41,128,185,0.18)', line:{color:'#2980b9',width:2},
    text:[...hoverText, hoverText[0]],
    hovertemplate:'%{text}<extra></extra>',
  }],{
    polar:{radialaxis:{visible:true,range:[0,10],tickvals:[0,2,4,6,8,10]},bgcolor:'white'},
    paper_bgcolor:'white', font:{family:'system-ui,sans-serif',size:12},
    margin:{t:20,b:20,l:40,r:40}, showlegend:false, height:400,
  });
  document.getElementById('spider-section').scrollIntoView({behavior:'smooth',block:'nearest'});
});
</script>
</body>
</html>"""

    html = (html
        .replace("__LISTINGS_DATA__", listings_json)
        .replace("__DIM_KEYS__", dim_keys_json)
        .replace("__DIM_LABELS__", dim_labels_json)
        .replace("__DIM_WEIGHTS__", dim_weights_json)
    )
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"[report] Written {output_path} ({len(rows)} scored listings)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "index.html"
    build_html(out)
