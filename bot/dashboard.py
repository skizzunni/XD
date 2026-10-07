"""Standalone interactive HTML, usable as a local file without a web server."""

import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path


def read_native_csv(path):
    content = Path(path).read_text(encoding="utf-8-sig")
    # Capture only complete rows while NinjaTrader may be appending the next event.
    if content and not content.endswith("\n"):
        content = content.rsplit("\n", 1)[0] + "\n"
    return list(csv.DictReader(io.StringIO(content), strict=True))


def details(value):
    return dict(p.split("=", 1) for p in (value or "").split(";") if "=" in p)


BLOCK_HINTS = {
    "ATR_WARMUP": "ATR20 history is not ready. Load complete individual-contract bars and ticks for at least 20 completed prior RTH sessions; the setup guide uses 60 calendar days.",
    "DATA_GAP": "A missing or incomplete tick/bar interval was recorded. Repair history or the live feed and reconcile positions before restarting the strategy.",
    "STALE_FEED": "The latest tick timestamp exceeded the strategy's freshness limit. Moving delayed quotes can also trigger this check. Check real-time CME entitlement, the Windows clock, connection state and whether the strategy is enabled.",
    "DATA_QUALITY": "An invalid price, volume or quote was recorded. Inspect the data source and the original event details.",
    "BAD_TIMESTAMP": "Tick timestamps moved backward. Check the feed, display timezone and tick history.",
    "UNRESOLVED_CONTRACT": "The frozen calendar contract did not match the strategy instrument. Read the logged contract and compare it with the calendar before changing either.",
    "UNRESOLVED_SESSION": "The date is missing from the reviewed calendar. Supply a reviewed calendar for the intended session.",
    "WARMUP_ONLY": "The calendar permits history loading but excludes trading on this date.",
    "EARLY_CLOSE": "The calendar excludes this shortened session from entries.",
    "ROLL_DAY": "The calendar excludes this contract-roll session from entries.",
    "NEWS_WINDOW": "The selected strategy/calendar excludes entries for scheduled news. Review the frozen schedule and the selected arm.",
    "DISCONNECT": "A connection loss was recorded. Restore a working feed and reconcile orders/positions before a fresh strategy start.",
    "RECONNECT": "A reconnection was recorded. This strategy deliberately keeps entries blocked after reconnection until the session is reconciled and restarted with healthy data.",
    "STRATEGY_ALREADY_RUNNING": "Another instance owns this account/instrument. Check for a duplicate strategy instance.",
    "ORPHAN_OR_EXTERNAL_POSITION": "An existing account position is not owned by this strategy. Reconcile it in NinjaTrader before enabling new entries.",
    "UNRESOLVED_POSITION": "A position remained unresolved across sessions. Reconcile account orders and positions.",
    "DAILY_RISK_LIMIT": "A configured session loss or evaluation profit limit was reached. Preserve the session risk records.",
    "LOSS_BUDGET_EXIT": "The session loss control requested an exit. Preserve the session risk records.",
    "TIME_EXIT": "The scheduled flatten time was reached. Re-entry waits for the next reviewed strategy session; R2 uses the CME futures trading day.",
    "QUOTE_WAIT": "The initial live bid/ask pair is not ready. R2 waits for paired quotes before entering; check the real-time CME connection if this persists.",
    "DAILY_PROFIT_TARGET": "The evaluation profit target requested an exit. Check the account stage and configured target.",
    "DUPLICATE_BAR": "A repeated closed bar was recorded. Inspect history and the feed.",
    "DUPLICATE_EXECUTION": "A duplicate execution was recorded. Reconcile the native execution ledger.",
    "STOP_CANCELLED": "A protective stop was cancelled while the strategy expected it to remain active. Reconcile native orders and positions.",
    "NOT_SIMULATION_ACCOUNT": "The strategy accepts Sim101 or Playback101 only.",
    "RISK_HISTORY_NOT_OWNED": "The strategy did not own the persistent risk ledger. Reconcile duplicate instances and positions.",
    "RISK_HISTORY_WRITE_FAILED": "The risk ledger could not be saved. Resolve its file access problem before restarting.",
    "DUPLICATE_RESULT": "A trade result was already recorded. Preserve the ledger and reconcile the duplicate.",
    "TERMINATED": "The strategy instance terminated. This dashboard can still display its saved logs; check the currently enabled instance in NinjaTrader.",
}


def feed_timing(live, observed_at=None):
    """Compare recorded timestamps; do not equate updating delayed prices with live data."""
    if not live or live.get("account") != "Sim101":
        return None
    observed_at = observed_at or datetime.now(timezone.utc)
    values = details(live.get("details"))

    def timestamp(value):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is not None else None
        except (TypeError, ValueError):
            return None

    tick = timestamp(live.get("timestamp"))
    if tick is None:
        return None
    received = timestamp(values.get("received_at_et"))
    try:
        limit = float(values.get("max_tick_gap_seconds", ""))
    except (TypeError, ValueError):
        limit = float("nan")
    native_limit = math.isfinite(limit) and limit > 0
    limit = limit if native_limit else 90
    age = (observed_at - tick).total_seconds()
    receipt_age = (observed_at - received).total_seconds() if received else None
    lag = (received - tick).total_seconds() if received else None
    if lag is not None and lag > limit:
        condition = "DELAYED_OR_CLOCK_OFFSET"
        message = f"At the logged update, the tick was already {lag:.0f}s behind the PC clock. Check delayed CME data and Windows time synchronization; updating prices alone do not establish a current feed."
    elif age < -5 or lag is not None and lag < -5:
        condition = "CLOCK_AHEAD"
        message = "The tick timestamp is ahead of the reference clock. Check Windows time synchronization and NinjaTrader's display timezone."
    elif age > limit:
        condition = "OLD_TICK_TIMESTAMP"
        message = f"The latest tick timestamp is {age:.0f}s old at this dashboard snapshot. Check delayed CME data, clock settings, the connection and the enabled instance; these saved logs alone cannot distinguish a delayed feed from a stopped run."
    else:
        condition = "RECENT_TICK_TIMESTAMP"
        message = "The latest logged tick timestamp is recent. This timestamp check does not establish entry eligibility or an independent account connection."
    return {
        "condition": condition, "message": message,
        "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
        "tick_age_seconds": age, "received_at_et": values.get("received_at_et"),
        "receipt_age_seconds": receipt_age, "delivery_lag_seconds": lag,
        "freshness_limit_seconds": limit,
        "limit_source": "native" if native_limit else "dashboard_default",
    }


def entry_diagnostics(rows, ledger):
    """Show recorded evidence without inventing a cause for a blocked flag."""
    rows = [row for row in rows if row.get("timestamp") and row.get("reason")]
    if not rows:
        return None
    latest = rows[-1]
    live = next((row for row in reversed(rows) if row["reason"] == "LIVE_STATUS"), None)
    logged_day = latest["timestamp"][:10]
    flags = {}
    for row in rows:
        if row["timestamp"][:10] != logged_day:
            continue
        reason, values = row["reason"], details(row.get("details"))
        hint = BLOCK_HINTS.get(reason)
        if not hint and (
            values.get("error") not in (None, "", "NoError")
            or values.get("state") in ("Rejected", "PartFilled")
            or reason == "PARTIAL"
        ):
            hint = "A native order error, rejection or partial fill was recorded. Inspect the order details and reconcile positions."
        if not hint:
            continue
        flag = flags.setdefault(reason, {
            "reason": reason, "first_time": row["timestamp"],
            "last_time": row["timestamp"], "occurrences": 0,
            "details": "", "hint": hint,
        })
        flag["last_time"] = row["timestamp"]
        flag["occurrences"] += 1
        flag["details"] = row.get("details") or ""
    blocked = details(live.get("details")).get("blocked") if live and live["timestamp"][:10] == logged_day else None
    blocked = str(blocked).lower()
    return {
        "account": latest.get("account"), "stage": latest.get("stage"),
        "strategy": latest.get("arm"), "ledger_file": str(ledger),
        "logged_session": logged_day,
        "last_tick": live["timestamp"] if live else None,
        "blocked": True if blocked == "true" else False if blocked == "false" else None,
        "latest_event": latest["reason"], "latest_event_time": latest["timestamp"],
        "feed_timing": feed_timing(live),
        "recorded_checks": list(flags.values()),
    }


def native_payload(paths):
    trades, events, reviews, pending = [], [], [], []
    live, learning = {}, {}
    entry_checks = []
    for path in paths:
        path = Path(path)
        rows = read_native_csv(path)
        check = entry_diagnostics(rows, path)
        if check:
            entry_checks.append(check)
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
                learning[(e.get("account"), e.get("arm"), d.get("direction"), d.get("regime", "RTH"))] = {
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
                    "futures_day": d.get("futures_day"),
                    "regime": d.get("regime"),
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
                    "session": entry.get("futures_day") or e["timestamp"][:10],
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
        "entry_checks": entry_checks,
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
            latest[(event["direction"], event.get("regime", "RTH"))] = event
    return latest.values()


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MNQ Paper Dashboard</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#0c131d;color:#e8eef7}body{max-width:1200px;margin:28px auto;padding:0 22px}h1{font-weight:650;letter-spacing:-1px}h2{font-size:20px}p{color:#aabbcf}.badge{padding:5px 10px;border-radius:6px;background:#253a50;font-size:12px}.notice{border-left:3px solid #e5b04e;padding:12px;background:#192230}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{padding:18px;background:#162233;border:1px solid #293a51;border-radius:10px}.card strong{font-size:26px;display:block;margin-top:8px}label{margin-right:16px}select,button{background:#182a41;border:1px solid #486078;color:inherit;border-radius:5px;padding:8px}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td,th{text-align:left;padding:11px;border-bottom:1px solid #293a51;font-size:13px}th{color:#aabbcf}.positive{color:#6bdab3}.negative{color:#ff8e92}.unknown{color:#ebbf6d}svg{width:100%;height:230px;background:#121e2d;border-radius:8px}details{background:#142133;border-radius:8px;padding:14px;margin:10px 0}summary{cursor:pointer}.scroll{overflow:auto}pre{white-space:pre-wrap;font-size:12px}.question{padding:12px 0;border-bottom:1px solid #293a51}.answer{color:#aabbcf;font-size:14px;margin-top:6px}@media(max-width:650px){.cards{grid-template-columns:repeat(2,1fr)}body{padding:0 12px}}
</style></head><body>
<span class="badge">PAPER EXECUTION</span><h1>MNQ trade dashboard</h1>
<p id="source"></p><p class="notice" id="notice"></p><div id="startup"></div>
<div><label>Account <select id="account"></select></label><label>Trades <select id="filter"><option value="all">All trades</option><option value="loss">Losses</option><option value="win">Wins</option></select></label></div><br>
<div class="cards"><div class="card">Net P&amp;L<strong id="pnl"></strong></div><div class="card">Closed trades<strong id="count"></strong></div><div class="card">Win rate<strong id="winrate"></strong></div><div class="card">Max drawdown<strong id="dd"></strong></div></div>
<h2>Current market and positions</h2><div id="live"></div>
<h2>Entry diagnostics</h2><div id="entry-checks"></div>
<p id="diagnostic-download" hidden><a href="/diagnostics.json" download>Download diagnostic report</a> — recorded checks and events, with routine tick-status updates removed.</p>
<h2>Quality filter learning</h2><div id="learning"></div>
<h2>Closed-trade equity</h2><svg id="equity" viewBox="0 0 1100 230" role="img" aria-label="Cumulative paper profit and loss"></svg>
<h2>Trades</h2><div class="scroll"><table><thead><tr><th>Session / account</th><th>Profile / strategy</th><th>Entry (ET)</th><th>Exit (ET)</th><th>Exit reason</th><th>Net P&amp;L</th></tr></thead><tbody id="trades"></tbody></table></div>
<h2>Open or unmatched positions</h2><div id="pending"></div>
<h2>Loss investigations</h2><div id="reviews"></div>
<details id="event-ledger"><summary>Event ledger</summary><p><label>Show <select id="event-mode"><option value="diagnostic">Diagnostic events</option><option value="all">All events</option><option value="live">Live tick updates</option></select></label><span id="event-count"></span></p><div class="scroll"><table><thead><tr><th>Time</th><th>Reason</th><th>Evidence</th></tr></thead><tbody id="events"></tbody></table></div></details>
<p>Funded means no daily profit target in this paper run. Evaluation applies the configured target. Stops and break-even orders cannot guarantee a fill price or prevent every loss. The dashboard never submits orders.</p>
<script id="payload" type="application/json">__PAYLOAD__</script><script>
const data=JSON.parse(document.getElementById('payload').textContent);
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(v), et=s=>{const d=new Date(s);return Number.isNaN(d.getTime())?s:new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(d)};
$('source').textContent=data.source+' · Latest ledger event: '+(data.events.length?data.events[data.events.length-1].timestamp:'none'); $('notice').textContent=(data.synthetic?'SYNTHETIC TEST DATA. ':'')+data.note;
const startup=data.startup, unavailable=startup&&startup.state!=='logs_found';
if(location.protocol==='http:'||location.protocol==='https:')$('diagnostic-download').hidden=false;
if(startup){
 $('startup').innerHTML='<p><strong>Paper run ID: '+esc(startup.run_id)+'</strong><br>Reading logs from '+esc(startup.folder)+'</p>'+(startup.state==='waiting_for_logs'?'<p class="notice">Waiting for strategy startup logs. Match Research → Paper run ID in NinjaTrader. Open New → NinjaScript Output and Control Center → Log if initialization has not finished. No trade is required for logs to appear.</p>':startup.state==='read_error'?'<p class="notice">Cannot read the current logs: '+esc(startup.error)+'. The page will retry in five seconds.</p>':'<p>Strategy logs found. Live feed status below comes from the strategy, not a separate account connection.</p>')+(startup.other_runs||[]).map(r=>'<p>Other logs found: <strong>'+esc(r.run_id)+'</strong> · '+esc(r.account)+' / '+esc(r.strategy)+' · Last event '+esc(r.reason)+' at '+esc(r.timestamp)+'. Existing logs may belong to stopped runs.</p>').join('');
}
const accounts=[...new Set([...data.trades,...data.events].map(t=>t.account).filter(Boolean))]; $('account').innerHTML='<option value="all">All accounts</option>'+accounts.map(a=>'<option value="'+esc(a)+'">'+esc(a)+'</option>').join('');
try{ $('account').value=localStorage.getItem('mnqAccount')||'all';if(!$('account').value)$('account').value='all';$('filter').value=localStorage.getItem('mnqFilter')||'all';}catch(e){}
try{$('event-mode').value=localStorage.getItem('mnqEventMode')||'diagnostic';}catch(e){$('event-mode').value='diagnostic';}
if(!['diagnostic','all','live'].includes($('event-mode').value))$('event-mode').value='diagnostic';
function draw(){
 const account=$('account').value,filter=$('filter').value;
 try{localStorage.setItem('mnqAccount',account);localStorage.setItem('mnqFilter',filter)}catch(e){}
 const all=data.trades.filter(t=>account==='all'||t.account===account), chosen=all.filter(t=>filter==='all'||(filter==='loss'?t.net_usd<0:t.net_usd>0));
 let sum=0,peak=0,dd=0;const points=[0];all.forEach(t=>{sum+=t.net_usd;peak=Math.max(peak,sum);dd=Math.max(dd,peak-sum);points.push(sum)});
 $('pnl').textContent=money(sum);$('pnl').className=sum>=0?'positive':'negative';$('count').textContent=all.length;$('winrate').textContent=all.length?(100*all.filter(t=>t.net_usd>0).length/all.length).toFixed(1)+'%':'—';$('dd').textContent=money(dd);
 const lo=Math.min(...points,0),hi=Math.max(...points,1),range=hi-lo||1; const coords=points.map((y,i)=>[30+i/Math.max(points.length-1,1)*1040,200-(y-lo)/range*170]);
 $('equity').innerHTML='<line x1="30" x2="1070" y1="'+(200-(0-lo)/range*170)+'" y2="'+(200-(0-lo)/range*170)+'" stroke="#3d5068"/><polyline points="'+coords.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#73d1bc" stroke-width="3"/><text x="35" y="20" fill="#aabbcf">'+esc(money(hi))+'</text><text x="35" y="222" fill="#aabbcf">'+esc(money(lo))+'</text>';
 $('trades').innerHTML=chosen.map(t=>'<tr><td>'+esc(t.session)+'<br>'+esc(t.account)+'</td><td>'+esc(t.stage)+' / '+esc(t.strategy)+'</td><td>'+esc(et(t.entry_time))+'</td><td>'+esc(et(t.exit_time))+'</td><td>'+esc(t.exit_reason)+'</td><td class="'+(t.net_usd<0?'negative':'positive')+'">'+money(t.net_usd)+'</td></tr>').join('')||'<tr><td colspan="6">No matching closed trades.</td></tr>';
 if(unavailable){['pnl','count','winrate','dd'].forEach(id=>$(id).textContent='—');}
 $('pending').innerHTML=unavailable?'<p>Position status unavailable until the logs can be read.</p>':data.pending_positions.map(p=>'<p class="notice">'+esc(p.account)+': '+esc(p.reason)+'</p>').join('')||'<p>No unmatched positions in the imported ledger.</p>';
 const live=(data.live_status||[]).filter(s=>account==='all'||s.account===account);
 const phases={WAITING_QUOTES:'Waiting for a paired live bid/ask quote',WAITING_R2_BARS:'Building six fresh closed bars after the CME open',PRE_CLOSE:'Pre-close protection; fresh entries paused before CME maintenance',CME_CLOSED:'CME maintenance/weekend/reviewed closure; waiting for the next open',OUTSIDE_RTH:'Outside the 09:30–16:00 ET session; R1 setups start at 10:00 ET',NO_CALENDAR_SESSION:'No reviewed calendar session for this date',WAITING_R1_WINDOW:'Outside the 10:00–15:45 ET R1 setup window',POSITION_OPEN:'Managing an open position',ENTRY_PENDING:'Entry order pending',BLOCKED:'Entries blocked',NEWS_PAUSE:'Scheduled news pause',SCANNING:'Scanning for eligible setups'};
 $('live').innerHTML=live.map(s=>'<p><strong>'+esc(s.account)+' / '+esc(s.strategy)+'</strong>'+(s.regime?' · '+esc(s.regime):'')+(s.futures_day?' · Futures day '+esc(s.futures_day):'')+' · Price '+esc(s.price)+' · Open contracts '+esc(s.open_qty)+' · Estimated open P&amp;L '+money(Number(s.unrealized_usd))+' · Session realized '+money(Number(s.day_net_usd))+'<br>'+esc(phases[s.phase]||((s.blocked==='True'||s.blocked===true)?'Entries blocked':(s.news_pause==='True'||s.news_pause===true)?'Scheduled news pause':'Scanning for eligible setups'))+' · Latest tick '+esc(et(s.timestamp))+' <span class="tick-age" data-time="'+esc(s.timestamp)+'" data-limit="'+esc(s.max_tick_gap_seconds||90)+'"></span>'+(s.received_at_et?'<br>Delivery lag at logged update: '+esc(s.feed_age_seconds)+'s · Received '+esc(et(s.received_at_et)):'')+'</p>').join('')||'<p>Waiting for live ticks from the strategy. Read the event ledger for initialization and history status. Historical replay has no live connection.</p>';
 $('entry-checks').innerHTML=(data.entry_checks||[]).filter(c=>account==='all'||c.account===account).map(c=>'<div class="notice"><strong>'+esc(c.account)+' / '+esc(c.strategy)+' · Latest logged session '+esc(c.logged_session)+'</strong>'+(c.feed_timing?'<p>'+esc(c.feed_timing.message)+'</p>':'')+'<p>'+(c.latest_event==='TERMINATED'?'This ledger ends with TERMINATED. Check the currently enabled strategy instance.':c.blocked===true?'The latest logged tick reports entries blocked.':c.blocked===false?'The latest logged tick reports entries unblocked.':'No blocked/unblocked state is available for this logged session.')+'</p>'+(c.strategy==='P0'?'<p>Recorded arm is P0, which uses late-afternoon entries. The requested full-session scanner is R2; select R2 explicitly in NinjaTrader.</p>':'')+((c.recorded_checks||[]).length?'<p>Recorded checks for this session; earlier checks may no longer be active:</p>'+c.recorded_checks.map(f=>'<p><strong>'+esc(f.reason)+'</strong> · First '+esc(et(f.first_time))+' · Latest '+esc(et(f.last_time))+' · '+esc(f.occurrences)+' occurrence(s)<br>'+esc(f.hint)+'<br><code>'+esc(f.details)+'</code></p>').join(''):c.blocked===true?'<p>The ledger does not identify a blocking cause for this session. Inspect NinjaScript Output and Control Center Log.</p>':'<p>No blocking-check events were found for this logged session.</p>')+'</div>').join('')||'<p>No native diagnostic records available yet.</p>';
 $('learning').innerHTML=(data.learning_status||[]).filter(s=>account==='all'||!s.account||s.account===account).map(s=>'<p>'+esc(s.account||'Replay')+' · '+esc(s.regime||'RTH')+' · '+(Number(s.direction)>0?'Long':'Short')+' · '+esc(s.observations)+' completed observations · Recent net '+money(Number(s.recent_net_usd))+' · Minimum trend efficiency '+esc(Number(s.min_efficiency).toFixed(2))+' · '+((s.tightened===true||s.tightened==='True')?'Stricter filter active':'Base filter active')+'</p>').join('')||'<p>R1/R2 learn from completed trades. R2 keeps daytime and overnight outcomes separate; eight outcomes per direction and profile are needed before a filter can tighten.</p>';
 $('reviews').innerHTML=data.loss_reviews.filter(r=>account==='all'||r.trade.account===account||r.trade.account==null).map((r,i)=>'<details id="loss-'+i+'"><summary>'+esc(r.trade.session)+' · '+esc(r.trade.account||'paper replay')+' · '+money(r.trade.net_usd??r.trade.net_ticks*.5)+' · '+r.diagnostics.length+' questions</summary>'+r.diagnostics.map(q=>'<div class="question"><strong>'+q.number+'. '+esc(q.question)+'</strong> <span class="badge '+(q.status==='flag'?'negative':q.status==='unknown'?'unknown':'')+'">'+esc(q.status)+'</span><div class="answer">'+esc(q.answer)+'</div>'+(q.evidence?'<pre>'+esc(JSON.stringify(q.evidence,null,2))+'</pre>':'')+'</div>').join('')+'<p>R1/R2 record bounded quality-filter changes from completed trades. Broader fixes need separate paper tests and future evidence.</p></details>').join('')||'<p>No recorded losses for this account.</p>';
 const eventMode=$('event-mode').value;
 try{localStorage.setItem('mnqEventMode',eventMode);}catch(e){}
 const ledger=data.events.filter(e=>account==='all'||!e.account||e.account===account).filter(e=>eventMode==='all'||(eventMode==='live'?e.reason==='LIVE_STATUS':e.reason!=='LIVE_STATUS'));
 $('event-count').textContent='Showing '+Math.min(ledger.length,300)+' of '+ledger.length+' matching events. Diagnostic mode excludes LIVE_STATUS.';
 $('events').innerHTML=ledger.slice(-300).map(e=>'<tr><td>'+esc(e.timestamp)+'</td><td>'+esc(e.reason)+'</td><td>'+esc(e.details||JSON.stringify(e))+'</td></tr>').join('')||'<tr><td colspan="3">No matching events.</td></tr>';
} $('account').onchange=draw;$('filter').onchange=draw;$('event-mode').onchange=draw;draw();
function tickAges(){document.querySelectorAll('.tick-age').forEach(e=>{const age=Math.max(0,Math.floor((Date.now()-Date.parse(e.dataset.time))/1000));e.textContent='('+age+'s ago)';const limit=Number(e.dataset.limit)||90;e.className='tick-age '+(age>limit?'negative':'');});}
tickAges();setInterval(tickAges,1000);
try{const saved=JSON.parse(sessionStorage.getItem('mnqUi')||'null');if(saved){saved.open.forEach(id=>{const e=$(id);if(e)e.open=true;});window.scrollTo(0,saved.scroll);}}catch(e){}
setTimeout(()=>{try{sessionStorage.setItem('mnqUi',JSON.stringify({scroll:window.scrollY,open:[...document.querySelectorAll('details[id][open]')].map(e=>e.id)}));}catch(e){}location.reload();},5000);
</script></body></html>"""


def html_document(payload):
    # Escape HTML parser terminators in embedded JSON, and never interpolate untrusted text as markup.
    raw = (
        json.dumps(payload, ensure_ascii=True, allow_nan=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return HTML.replace("__PAYLOAD__", raw)


def render(payload, output):
    document = html_document(payload)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.html")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(output)
    return str(output)
