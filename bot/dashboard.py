"""Standalone interactive HTML, usable as a local file without a web server."""

import csv
import io
import json
from pathlib import Path


def read_native_csv(path):
    content = Path(path).read_text(encoding="utf-8-sig")
    # Capture only complete rows while NinjaTrader may be appending the next event.
    if content and not content.endswith("\n"):
        content = content.rsplit("\n", 1)[0] + "\n"
    return list(csv.DictReader(io.StringIO(content), strict=True))


def details(value):
    return dict(p.split("=", 1) for p in value.split(";") if "=" in p)


def native_payload(paths):
    trades, events, reviews, pending = [], [], [], []
    live, learning = {}, {}
    for path in paths:
        path = Path(path)
        rows = read_native_csv(path)
        review_path = path.with_name(
            path.name.replace("_events.csv", "_loss_reviews.csv")
        )
        loss_rows = read_native_csv(review_path) if review_path.exists() else []
        entry = None
        for e in rows:
            if not e.get("timestamp") or not e.get("reason"):
                continue
            d = details(e.get("details", ""))
            events.append(e)
            key = (e.get("account"), e.get("arm"))
            if e["reason"] == "LIVE_STATUS":
                live[key] = {
                    "account": e.get("account"),
                    "strategy": e.get("arm"),
                    "timestamp": e["timestamp"],
                    **d,
                }
            if e["reason"] == "ADAPTATION_UPDATE":
                learning[(e.get("account"), e.get("arm"), d.get("direction"))] = {
                    "account": e.get("account"),
                    "strategy": e.get("arm"),
                    "timestamp": e["timestamp"],
                    **d,
                }
            if e["reason"] == "FILLED" and d.get("name") == "MNQ_ENTRY":
                if entry:
                    pending.append(
                        {
                            "account": e.get("account"),
                            "reason": "Multiple unmatched entry executions; reconcile native orders",
                        }
                    )
                entry = {
                    "entry_time": e["timestamp"],
                    "entry_fill": float(d["price"]),
                    "order_id": d.get("order_id"),
                    "quantity": int(d["quantity"]),
                    "account": e.get("account"),
                    "stage": e.get("stage"),
                    "strategy": e.get("arm"),
                    "direction": int(d["direction"]) if d.get("direction") else None,
                }
            elif "net_usd" in d and e["reason"] != "LOSS_RECORDED":
                if not entry:
                    pending.append(
                        {
                            "account": e.get("account"),
                            "reason": "Exit without matched entry; inspect complete ledger",
                        }
                    )
                    continue
                trade = {
                    **entry,
                    "session": e["timestamp"][:10],
                    "exit_time": e["timestamp"],
                    "exit_reason": e["reason"],
                    "net_usd": float(d["net_usd"]),
                    "net_ticks": float(d["net_usd"]) / 0.50,
                    "fees_usd": float(d.get("fees", 0)),
                    "id": f"{e.get('account')}:{entry.get('order_id')}",
                }
                trades.append(trade)
                if trade["net_usd"] < 0:
                    matching = [
                        r
                        for r in loss_rows
                        if r.get("trade_id") == trade["id"]
                        and r.get("timestamp") == e["timestamp"]
                    ]
                    reviews.append(
                        {
                            "trade": trade,
                            "diagnostics": [
                                {
                                    "number": i + 1,
                                    "category": "NinjaTrader",
                                    "question": r.get("question"),
                                    "answer": r.get("answer"),
                                    "status": r.get("status"),
                                    "evidence": None,
                                }
                                for i, r in enumerate(matching)
                            ],
                            "observed_contributors": [
                                "Read native order updates, quote path and diagnostic answers before attributing cause."
                            ],
                            "active_strategy_changed": False,
                        }
                    )
                entry = None
        if entry:
            pending.append(
                {
                    **entry,
                    "reason": "Open paper position; awaiting its exit execution",
                }
            )
    trades.sort(key=lambda t: t["exit_time"])
    return {
        "source": "NinjaTrader native paper ledger",
        "synthetic": False,
        "trades": trades,
        "events": events,
        "loss_reviews": reviews,
        "pending_positions": pending,
        "live_status": list(live.values()),
        "learning_status": list(learning.values()),
        "note": "Native P&L deducts the configured fees. Verify against NinjaTrader account statements; fees may still be provisional.",
    }


def run_payload(directory):
    directory = Path(directory)
    report = json.loads((directory / "report.json").read_text())
    trades = json.loads((directory / "trades.json").read_text())
    for i, t in enumerate(trades):
        t["net_usd"] = t["net_ticks"] * 0.50
        t["id"] = str(i + 1)
        t["account"] = "paper replay"
        t["stage"] = (
            "evaluation"
            if json.loads((directory / "manifest.json").read_text())
            .get("config", {})
            .get("daily_profit_target_usd")
            else "funded/baseline"
        )
    position = (
        json.loads((directory / "open_position.json").read_text())
        if (directory / "open_position.json").exists()
        else None
    )
    pending = (
        [
            {
                "account": "paper replay",
                "reason": "Replay failed with an unresolved position; supply missing executable ticks",
                **position,
            }
        ]
        if position
        else []
    )
    return {
        "source": "Python tick replay",
        "synthetic": report["synthetic_data"],
        "trades": trades,
        "loss_reviews": json.loads((directory / "loss_reviews.json").read_text()),
        "pending_positions": pending,
        "live_status": [],
        "learning_status": [e for e in engine_learning_events(directory)],
        "events": [
            json.loads(line)
            for line in (directory / "events.jsonl").read_text().splitlines()
        ],
        "note": "Synthetic fixtures validate implementation only; they are not evidence of profit."
        if report["synthetic_data"]
        else "Paper replay only. Real account costs and research gates must be verified.",
    }


def engine_learning_events(directory):
    events = [
        json.loads(line)
        for line in (Path(directory) / "events.jsonl").read_text().splitlines()
    ]
    latest = {}
    for event in events:
        if event["reason"] == "ADAPTATION_UPDATE":
            latest[event["direction"]] = event
    return latest.values()


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MNQ Paper Dashboard</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#0c131d;color:#e8eef7}body{max-width:1200px;margin:28px auto;padding:0 22px}h1{font-weight:650;letter-spacing:-1px}h2{font-size:20px}p{color:#aabbcf}.badge{padding:5px 10px;border-radius:6px;background:#253a50;font-size:12px}.notice{border-left:3px solid #e5b04e;padding:12px;background:#192230}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{padding:18px;background:#162233;border:1px solid #293a51;border-radius:10px}.card strong{font-size:26px;display:block;margin-top:8px}label{margin-right:16px}select,button{background:#182a41;border:1px solid #486078;color:inherit;border-radius:5px;padding:8px}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td,th{text-align:left;padding:11px;border-bottom:1px solid #293a51;font-size:13px}th{color:#aabbcf}.positive{color:#6bdab3}.negative{color:#ff8e92}.unknown{color:#ebbf6d}svg{width:100%;height:230px;background:#121e2d;border-radius:8px}details{background:#142133;border-radius:8px;padding:14px;margin:10px 0}summary{cursor:pointer}.scroll{overflow:auto}pre{white-space:pre-wrap;font-size:12px}.question{padding:12px 0;border-bottom:1px solid #293a51}.answer{color:#aabbcf;font-size:14px;margin-top:6px}@media(max-width:650px){.cards{grid-template-columns:repeat(2,1fr)}body{padding:0 12px}}
</style></head><body>
<span class="badge">PAPER EXECUTION</span><h1>MNQ trade dashboard</h1>
<p id="source"></p><p class="notice" id="notice"></p>
<div><label>Account <select id="account"></select></label><label>Trades <select id="filter"><option value="all">All trades</option><option value="loss">Losses</option><option value="win">Wins</option></select></label></div><br>
<div class="cards"><div class="card">Net P&amp;L<strong id="pnl"></strong></div><div class="card">Closed trades<strong id="count"></strong></div><div class="card">Win rate<strong id="winrate"></strong></div><div class="card">Max drawdown<strong id="dd"></strong></div></div>
<h2>Current market and positions</h2><div id="live"></div>
<h2>Quality filter learning</h2><div id="learning"></div>
<h2>Closed-trade equity</h2><svg id="equity" viewBox="0 0 1100 230" role="img" aria-label="Cumulative paper profit and loss"></svg>
<h2>Trades</h2><div class="scroll"><table><thead><tr><th>Session / account</th><th>Profile / strategy</th><th>Entry (ET)</th><th>Exit (ET)</th><th>Exit reason</th><th>Net P&amp;L</th></tr></thead><tbody id="trades"></tbody></table></div>
<h2>Open or unmatched positions</h2><div id="pending"></div>
<h2>Loss investigations</h2><div id="reviews"></div>
<details><summary>Event ledger</summary><div class="scroll"><table><thead><tr><th>Time</th><th>Reason</th><th>Evidence</th></tr></thead><tbody id="events"></tbody></table></div></details>
<p>Funded means no daily profit target in this paper run. Evaluation applies the configured target. Stops and break-even orders cannot guarantee a fill price or prevent every loss. The dashboard never submits orders.</p>
<script id="payload" type="application/json">__PAYLOAD__</script><script>
const data=JSON.parse(document.getElementById('payload').textContent);
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(v), et=s=>{const d=new Date(s);return Number.isNaN(d.getTime())?s:new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(d)};
$('source').textContent=data.source+' · Latest ledger event: '+(data.events.length?data.events[data.events.length-1].timestamp:'none'); $('notice').textContent=(data.synthetic?'SYNTHETIC TEST DATA. ':'')+data.note;
const accounts=[...new Set([...data.trades,...data.events].map(t=>t.account).filter(Boolean))]; $('account').innerHTML='<option value="all">All accounts</option>'+accounts.map(a=>'<option value="'+esc(a)+'">'+esc(a)+'</option>').join('');
try{ $('account').value=localStorage.getItem('mnqAccount')||'all';if(!$('account').value)$('account').value='all';$('filter').value=localStorage.getItem('mnqFilter')||'all';}catch(e){}
function draw(){
 const account=$('account').value,filter=$('filter').value;
 try{localStorage.setItem('mnqAccount',account);localStorage.setItem('mnqFilter',filter)}catch(e){}
 const all=data.trades.filter(t=>account==='all'||t.account===account), chosen=all.filter(t=>filter==='all'||(filter==='loss'?t.net_usd<0:t.net_usd>0));
 let sum=0,peak=0,dd=0;const points=[0];all.forEach(t=>{sum+=t.net_usd;peak=Math.max(peak,sum);dd=Math.max(dd,peak-sum);points.push(sum)});
 $('pnl').textContent=money(sum);$('pnl').className=sum>=0?'positive':'negative';$('count').textContent=all.length;$('winrate').textContent=all.length?(100*all.filter(t=>t.net_usd>0).length/all.length).toFixed(1)+'%':'—';$('dd').textContent=money(dd);
 const lo=Math.min(...points,0),hi=Math.max(...points,1),range=hi-lo||1; const coords=points.map((y,i)=>[30+i/Math.max(points.length-1,1)*1040,200-(y-lo)/range*170]);
 $('equity').innerHTML='<line x1="30" x2="1070" y1="'+(200-(0-lo)/range*170)+'" y2="'+(200-(0-lo)/range*170)+'" stroke="#3d5068"/><polyline points="'+coords.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#73d1bc" stroke-width="3"/><text x="35" y="20" fill="#aabbcf">'+esc(money(hi))+'</text><text x="35" y="222" fill="#aabbcf">'+esc(money(lo))+'</text>';
 $('trades').innerHTML=chosen.map(t=>'<tr><td>'+esc(t.session)+'<br>'+esc(t.account)+'</td><td>'+esc(t.stage)+' / '+esc(t.strategy)+'</td><td>'+esc(et(t.entry_time))+'</td><td>'+esc(et(t.exit_time))+'</td><td>'+esc(t.exit_reason)+'</td><td class="'+(t.net_usd<0?'negative':'positive')+'">'+money(t.net_usd)+'</td></tr>').join('')||'<tr><td colspan="6">No matching closed trades.</td></tr>';
 $('pending').innerHTML=data.pending_positions.map(p=>'<p class="notice">'+esc(p.account)+': '+esc(p.reason)+'</p>').join('')||'<p>No unmatched positions in the imported ledger.</p>';
 const live=(data.live_status||[]).filter(s=>account==='all'||s.account===account);
 $('live').innerHTML=live.map(s=>'<p><strong>'+esc(s.account)+' / '+esc(s.strategy)+'</strong> · Price '+esc(s.price)+' · Open contracts '+esc(s.open_qty)+' · Estimated open P&amp;L '+money(Number(s.unrealized_usd))+' · Session realized '+money(Number(s.day_net_usd))+'<br>'+((s.blocked==='True'||s.blocked===true)?'Entries blocked':(s.news_pause==='True'||s.news_pause===true)?'Scheduled news pause':'Scanning for eligible setups')+' · Latest tick '+esc(et(s.timestamp))+' <span class="tick-age" data-time="'+esc(s.timestamp)+'"></span></p>').join('')||'<p>Waiting for live strategy status. Historical replay has no live connection.</p>';
 $('learning').innerHTML=(data.learning_status||[]).filter(s=>account==='all'||!s.account||s.account===account).map(s=>'<p>'+esc(s.account||'Replay')+' · '+(Number(s.direction)>0?'Long':'Short')+' · '+esc(s.observations)+' completed observations · Recent net '+money(Number(s.recent_net_usd))+' · Minimum trend efficiency '+esc(Number(s.min_efficiency).toFixed(2))+' · '+((s.tightened===true||s.tightened==='True')?'Stricter filter active':'Base filter active')+'</p>').join('')||'<p>R1 learns from completed trades. Eight outcomes in a direction are needed before its quality filter can tighten.</p>';
 $('reviews').innerHTML=data.loss_reviews.filter(r=>account==='all'||r.trade.account===account||r.trade.account==null).map((r,i)=>'<details id="loss-'+i+'"><summary>'+esc(r.trade.session)+' · '+esc(r.trade.account||'paper replay')+' · '+money(r.trade.net_usd??r.trade.net_ticks*.5)+' · '+r.diagnostics.length+' questions</summary>'+r.diagnostics.map(q=>'<div class="question"><strong>'+q.number+'. '+esc(q.question)+'</strong> <span class="badge '+(q.status==='flag'?'negative':q.status==='unknown'?'unknown':'')+'">'+esc(q.status)+'</span><div class="answer">'+esc(q.answer)+'</div>'+(q.evidence?'<pre>'+esc(JSON.stringify(q.evidence,null,2))+'</pre>':'')+'</div>').join('')+'<p>R1 records bounded quality-filter changes from completed trades. Broader fixes need separate paper tests and future evidence.</p></details>').join('')||'<p>No recorded losses for this account.</p>';
 $('events').innerHTML=data.events.filter(e=>account==='all'||!e.account||e.account===account).slice(-300).map(e=>'<tr><td>'+esc(e.timestamp)+'</td><td>'+esc(e.reason)+'</td><td>'+esc(e.details||JSON.stringify(e))+'</td></tr>').join('');
} $('account').onchange=draw;$('filter').onchange=draw;draw();
function tickAges(){document.querySelectorAll('.tick-age').forEach(e=>{const age=Math.max(0,Math.floor((Date.now()-Date.parse(e.dataset.time))/1000));e.textContent='('+age+'s ago)';e.className='tick-age '+(age>90?'negative':'');});}
tickAges();setInterval(tickAges,1000);
try{const saved=JSON.parse(sessionStorage.getItem('mnqUi')||'null');if(saved){saved.open.forEach(id=>{const e=$(id);if(e)e.open=true;});window.scrollTo(0,saved.scroll);}}catch(e){}
setTimeout(()=>{try{sessionStorage.setItem('mnqUi',JSON.stringify({scroll:window.scrollY,open:[...document.querySelectorAll('details[id][open]')].map(e=>e.id)}));}catch(e){}location.reload();},5000);
</script></body></html>"""


def render(payload, output):
    # Escape HTML parser terminators in embedded JSON, and never interpolate untrusted text as markup.
    raw = (
        json.dumps(payload, ensure_ascii=True, allow_nan=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.html")
    temporary.write_text(HTML.replace("__PAYLOAD__", raw), encoding="utf-8")
    temporary.replace(output)
    return str(output)
