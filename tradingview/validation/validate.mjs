// Independent runtime, not TradingView's official compiler or broker emulator.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { PineTS, Indicator } from 'pinets';

const root = fileURLToPath(new URL('../../', import.meta.url));
const python = process.env.MNQ_TEST_PYTHON || `${root}.venv/bin/python`;
const cases = JSON.parse(execFileSync(python, ['-m', 'scripts.tradingview_cases'], {cwd:root,maxBuffer:20_000_000}));
const source = readFileSync(new URL('../MNQ_R2_Paper.pine', import.meta.url), 'utf8');
const guardedSource = readFileSync(new URL('../MNQ_R2_Guarded.pine', import.meta.url), 'utf8');
const preservedSource = readFileSync(new URL('../baselines/MNQ_R2_PreGuard.pine', import.meta.url), 'utf8');
new Indicator(source).prepare();
new Indicator(guardedSource).prepare();
const helpers = source.split('// @HELPERS_BEGIN@')[1].split('// @HELPERS_END@')[0];
const preamble = `//@version=6\nindicator("Extracted production helper checks")\nconst string TZ="America/New_York"\nconst float TICK=0.25\n` + helpers;
const basicBars = n => Array.from({length:n},(_,i)=>({openTime:Date.parse('2026-10-07T00:00Z')+i*300000,closeTime:Date.parse('2026-10-07T00:00Z')+(i+1)*300000,open:30000,high:30001,low:29999,close:30000,volume:i}));
function pineArray(name, values, type='float') { return `var array<${type}> ${name}=array.from(${values.map(v=>typeof v==='boolean'?String(v):typeof v==='string'?JSON.stringify(v):String(v)).join(',')})\n`; }
function column(data, key, type='float') {return pineArray(key,data.map(d=>d[key]),type);}
const last = (result,key,i) => result.plots[key].data[i].value;
const tables = r => r.plots.__tables__.data.at(-1).value;
const tableText = (r, index=0) => tables(r)[index].cells.flat().map(cell=>cell.text).join('\n');
const contains = (text, phrase) => assert.ok(text.includes(phrase),`Expected chart text: ${phrase}`);
const near=(a,b,label)=>assert.ok(Math.abs(a-b)<1e-8,`${label}: got ${a}, expected ${b}`);

// Test-only carrier fields avoid giant per-bar array-literal snapshots in PineTS.
let code=preamble+`var array<float> recent=array.new<float>()
var int q=1
var int count=0
var int streak=0
var int since=20
var bool weak=false
if int(volume) >= 2
    array.clear(recent)
    q:=int(high)
    count:=0
    streak:=0
    since:=20
    weak:=false
[nextQ,nextCount,nextStreak,nextSince,nextWeak,reason]=f_size_step(recent,q,count,streak,since,weak,open,int(volume)%2==1,int(low))
q:=nextQ
count:=nextCount
streak:=nextStreak
since:=nextSince
weak:=nextWeak
[net,dd,qualifies]=f_window_stats(recent)
plot(q,"q")
plot(count,"count")
plot(net,"net")
plot(dd,"dd")
`;
let result=await new PineTS(basicBars(cases.size.length).map((b,i)=>({...b,open:cases.size[i].value,high:cases.size[i].start,low:cases.size[i].maximum,volume:(cases.size[i].reset?2:0)+(cases.size[i].eligible?1:0)})),'TEST','5').run(code);
cases.size.forEach((c,i)=>{assert.equal(last(result,'q',i),c.q,`size ${i}`);assert.equal(last(result,'count',i),c.count,`count ${i}`);near(last(result,'net',i),c.net,`net ${i}`);near(last(result,'dd',i),c.dd,`dd ${i}`);});
console.log(`PASS: ${cases.size.length} production Pine sizing steps vs independent Python outcomes`);

code=preamble+'plot(f_fit(int(volume),open,close),"fit")\n';
result=await new PineTS(basicBars(cases.fit.length).map((b,i)=>({...b,open:cases.fit[i].risk,close:cases.fit[i].remaining,volume:cases.fit[i].q})),'TEST','5').run(code);
cases.fit.forEach((c,i)=>assert.equal(last(result,'fit',i),c.expected,`cash boundary ${i}`));
console.log(`PASS: ${cases.fit.length} production Pine quantity cash boundaries`);

code=preamble+'plot(f_allowance(open,close,int(volume)==1,high),"allowance")\n';
result=await new PineTS(basicBars(cases.allowance.length).map((b,i)=>({...b,open:cases.allowance[i].budget,close:cases.allowance[i].pnl,high:cases.allowance[i].cap,volume:cases.allowance[i].capped?1:0})),'TEST','5').run(code);
cases.allowance.forEach((c,i)=>near(last(result,'allowance',i),c.expected,`planned risk cap ${i}`));
console.log(`PASS: ${cases.allowance.length} trade-cap/session-risk cases (profits never enlarge allowance)`);

code=preamble+'plot(f_runner(open>0?1:-1,math.abs(open),low,high,close,volume),"stop")\n';
result=await new PineTS(basicBars(cases.runner.length).map((b,i)=>({...b,open:cases.runner[i].d*cases.runner[i].fill,low:cases.runner[i].risk,high:cases.runner[i].peak,close:cases.runner[i].stop,volume:cases.runner[i].quote})),'TEST','5').run(code);
cases.runner.forEach((c,i)=>near(last(result,'stop',i),c.expected,`runner ${i}`));
console.log(`PASS: ${cases.runner.length} production Pine monotone runner cases`);

code=preamble+column(cases.setup,'atr')+column(cases.setup,'minimum')+column(cases.setup,'max_stop');
code+='int caseIndex=int(volume)\n[d,stop,eff,m]=f_setup(array.get(atr,caseIndex),array.get(minimum,caseIndex),array.get(max_stop,caseIndex))\nplot(d,"d")\nplot(stop,"stop")\n';
result=await new PineTS(cases.setup.flatMap(c=>c.bars),'TEST','5').run(code);
cases.setup.forEach((c,i)=>{assert.equal(last(result,'d',i*6+5),c.direction,`setup ${i}`);if(c.direction)near(last(result,'stop',i*6+5),c.stop,`setup stop ${i}`);});
console.log(`PASS: ${cases.setup.length} production Pine six-bar entry cases vs Python rules`);

// Full source: reviewed calendar + 15-minute cash ATR + fills, losses and table rendering.
const calendar=JSON.parse(readFileSync(`${root}ninjatrader/calendars/mnq-dec26-full-session-2026-10-07-30/calendar.json`,'utf8'));
const pattern=[[0,3,-1,2],[2,5,1,4],[4,7,3,6],[6,9,5,8],[8,9,6.5,7],[7,13,6.5,12]];
const primary=[];
const master=[];
for(const row of calendar.sessions.filter(r=>r.date<='2026-10-09')){
  const a=Date.parse(row.globex.open),b=Date.parse(row.globex.close),cashA=Date.parse(row.open);
  for(let ts=a,i=0;ts<b;ts+=300000,i++){
    let p=row.trade_enabled?pattern[i%6]:[0,1,-1,0],shift=30000+(row.trade_enabled?Math.floor(i/6)*12:0);
    if(!row.trade_enabled && ts===cashA)p=[0,100,-100,0];
    // Alternating losing/growing cycles, including gap and stop fills.
    if(row.trade_enabled && i%36===0 && i>0)p=[0,3,-12,2];
    const bar={openTime:ts,closeTime:ts+300000,open:shift+p[0],high:shift+p[1],low:shift+p[2],close:shift+p[3],volume:100};
    master.push(bar);
    if(row.trade_enabled)primary.push(bar);
  }
}
const secondary=[];
for(let i=0;i<master.length;i+=3){
  const group=master.slice(i,i+3);
  assert.ok(group.length===3 && group[0].closeTime===group[1].openTime && group[1].closeTime===group[2].openTime,'Contiguous 15-minute aggregation');
  secondary.push({...group[0],closeTime:group[2].closeTime,high:Math.max(...group.map(b=>b.high)),low:Math.min(...group.map(b=>b.low)),close:group[2].close,volume:group.reduce((s,b)=>s+b.volume,0)});
}
const expectedATR=new Map();
let previousCashClose=null;
const cashRanges=[];
for(const row of calendar.sessions.filter(r=>r.date<='2026-10-09')){
  expectedATR.set(row.date,cashRanges.length>=20?cashRanges.slice(-20).reduce((a,b)=>a+b,0)/20:null);
  const cash=master.filter(b=>b.openTime>=Date.parse(row.open)&&b.closeTime<=Date.parse(row.close));
  if(cash.length!==78){cashRanges.length=0;previousCashClose=null;continue;}
  const h=Math.max(...cash.map(b=>b.high)),l=Math.min(...cash.map(b=>b.low));
  if(previousCashClose!==null)cashRanges.push(Math.max(h-l,Math.abs(h-previousCashClose),Math.abs(l-previousCashClose)));
  previousCashClose=cash.at(-1).close;
}
function provider(primaryBars=primary, secondaryBars=secondary, ticker='MNQZ2026'){
  return {configure(){},async getMarketData(_,timeframe){return timeframe==='15'?secondaryBars:primaryBars;},async getSymbolInfo(){return {tickerid:`CME_MINI:${ticker}`,ticker,root:'MNQ',prefix:'CME_MINI',timezone:'America/New_York',type:'futures',mintick:.25,pointvalue:2,minmove:1,pricescale:4,session:'extended',currency:'USD',mincontract:1};}};
}
async function full(overrides={}, p=provider(), program=source){
  const runtime=new PineTS(p,'CME_MINI:MNQZ2026','5',primary.length);
  const r=await runtime.run(new Indicator(program,{'Write entry plans and every completed trade to Pine Logs':false,...overrides}));
  assert.equal(r.warnings.length,0,'No independent-runtime warnings');
  return r;
}
result=await full({'Session loss budget ($)':1000,'Write 30 answers to Pine Logs on each loss':false});
assert.ok(last(result,'Lagged cash ATR20',primary.length-1)>0,'Prior cash ATR warmed');
primary.forEach((bar,i)=>{
  // All fixture dates are Eastern daylight time; 18:00 starts tomorrow's risk day.
  const local=new Date(bar.openTime-4*60*60000);
  if(local.getUTCHours()>=18)local.setUTCDate(local.getUTCDate()+1);
  const expected=expectedATR.get(local.toISOString().slice(0,10));
  if(expected!==null&&expected!==undefined)near(last(result,'Lagged cash ATR20',i),expected,`Lagged cash ATR at ${i}`);
});
assert.ok(result.strategy.closedtrades.length>5,'Full source actually trades');
assert.ok(result.strategy.closedtrades.some(t=>t.entry_comment==='RTH_PULLBACK_BREAKOUT'),'Cash trades');
assert.ok(result.strategy.closedtrades.some(t=>t.entry_comment==='OVERNIGHT_PULLBACK_BREAKOUT'),'Overnight trades');
assert.ok(result.strategy.closedtrades.some(t=>t.profit<0),'Loss review branch executed');
assert.ok(result.strategy.closedtrades.every(t=>Math.abs(t.size)<=10),'Sizing cap');
for(const t of result.strategy.closedtrades){near(t.profit,(t.size>0?1:-1)*(t.exit_price-t.entry_price)*2*Math.abs(t.size)-t.commission,'net fill accounting');near(t.commission,Math.abs(t.size)*1.5,'per-side commission accounting');}
contains(tableText(result),'STRATEGY TESTER SIMULATION');
contains(tableText(result),'Delayed research');
contains(tableText(result,1),'1. Did the setup meet all mechanical entry criteria');
contains(tableText(result,1),'10. Was quantity within');
console.log(`PASS: full-source ${primary.length} synthetic ETH bars; cash/night entries, losses, fees and chart tables`);

const baselineResult=result;
const preserved=await full({'Session loss budget ($)':1000,'Write 30 answers to Pine Logs on each loss':false},provider(),preservedSource);
const ledger=r=>r.strategy.closedtrades.map(t=>({entry:t.entry_price,exit:t.exit_price,entryTime:t.entry_time,exitTime:t.exit_time,size:t.size,profit:t.profit,commission:t.commission,reason:t.exit_comment}));
assert.deepEqual(ledger(baselineResult),ledger(preserved),'Baseline orders/fills/net are identical to the preserved original');
console.log('PASS: complete baseline ledger unchanged from the pre-guard source');

const wrong=await full({'Write 30 answers to Pine Logs on each loss':false},provider(primary,secondary,'MNQ1!'));
assert.equal(wrong.strategy.closedtrades.length,0,'Wrong contract stays blocked');
contains(tableText(wrong),'WRONG_CONTRACT');
const empty=await full({'Write 30 answers to Pine Logs on each loss':false},provider(primary,secondary.slice(-26)));
assert.equal(empty.strategy.closedtrades.length,0,'Missing historical cash data stays blocked');
contains(tableText(empty),'ATR_WARMUP');
const future=await full({'Paper experiment start (ET)':Date.parse('2026-10-20T00:00:00-04:00'),'Write 30 answers to Pine Logs on each loss':false});
assert.equal(future.strategy.closedtrades.length,0,'Explicit experiment date stays blocked');
console.log('PASS: full-source wrong-contract, missing-history and future-start blocks');

const middle=await full({'Session loss budget ($)':1000,'Loss review page (1–3)':2,'Write 30 answers to Pine Logs on each loss':false});
contains(tableText(middle,1),'11. Were contracts added');
contains(tableText(middle,1),'20. Was break-even protection');
const final=await full({'Session loss budget ($)':1000,'Loss review page (1–3)':3,'Write 30 answers to Pine Logs on each loss':false});
contains(tableText(final,1),'21. Did a stop gap');
contains(tableText(final,1),'30. Would a proposed fix generalize');
const evaluation=await full({'Account stage':'Evaluation','Evaluation session profit target ($)':20,'Write 30 answers to Pine Logs on each loss':false});
assert.ok(evaluation.strategy.closedtrades.length>0 && evaluation.strategy.closedtrades.length<result.strategy.closedtrades.length,'Evaluation target changes actual entry behavior');
const missingBar=secondary.filter(b=>b.openTime!==Date.parse('2026-10-01T10:00:00-04:00'));
const gap=await full({'Write 30 answers to Pine Logs on each loss':false},provider(primary,missingBar));
assert.equal(gap.strategy.closedtrades.length,0,'Missing reviewed cash bar resets ATR; no phantom coverage');
console.log('PASS: all 30 loss-review answers rendered, Evaluation target, and incomplete-cash-history block');

const guarded=await full({'Session loss budget ($)':1000,'Write 30 answers to Pine Logs on each loss':false},provider(),guardedSource);
assert.ok(guarded.strategy.closedtrades.length>0,'Guarded variant can still trade');
for(let i=0;i<primary.length;i++){
  const plan=last(guarded,'Last submitted planned position risk ($)',i);
  const allowed=last(guarded,'Last submitted position risk allowance ($)',i);
  if(Number.isFinite(plan)){assert.ok(plan<=25+1e-8,'Full position plan fits $25 cap');assert.ok(plan<=allowed+1e-8);}
}
for(const t of guarded.strategy.closedtrades){
  const entry=new Date(t.entry_time-4*60*60000),exit=new Date(t.exit_time-4*60*60000);
  const entryMinutes=entry.getUTCHours()*60+entry.getUTCMinutes();
  const exitMinutes=exit.getUTCHours()*60+exit.getUTCMinutes();
  assert.ok(entryMinutes<565||entryMinutes>=580,'No entries in cash opening buffer');
  assert.ok(!(entry.toISOString().slice(0,10)===exit.toISOString().slice(0,10)&&entryMinutes<565&&exitMinutes>=570),'No held position carried through cash open');
}
contains(tableText(guarded),'Guarded R2');
assert.ok(last(guarded,'Cash-open guard bars',primary.length-1)>0,'Open guard actually processed bars');
const capOnly=await full({'Paper test variant':'Trade risk cap only','Session loss budget ($)':1000,'Write 30 answers to Pine Logs on each loss':false});
assert.equal(last(capOnly,'Cash-open guard bars',primary.length-1),0,'Trade-cap-only experiment does not silently enable opening guard');
const openOnly=await full({'Paper test variant':'Cash open guard only','Session loss budget ($)':1000,'Write 30 answers to Pine Logs on each loss':false});
assert.ok(primary.some((_,i)=>last(openOnly,'Last submitted planned position risk ($)',i)>25),'Opening-only experiment does not silently cap trade risk');
console.log('PASS: guarded planned risk/opening protection and isolated cap-only/open-only variants');

// Actual production Pine log text must round-trip through the separate Python reconciler.
const auditLines=[];
const originalLog=console.log;
let audited;
try {
  console.log=(message,...args)=>{if(typeof message==='string' && /R2_(TRADE_AUDIT|ENTRY_PLAN)\|/.test(message))auditLines.push(message);else originalLog(message,...args);};
  audited=await full({'Session loss budget ($)':1000,'Write 30 answers to Pine Logs on each loss':false,'Write entry plans and every completed trade to Pine Logs':true});
} finally {console.log=originalLog;}
const review=JSON.parse(execFileSync(python,['-c','import json,sys; from scripts.analyze_pine_audit import analyze; print(json.dumps(analyze(sys.stdin.read()),allow_nan=False))'],{cwd:root,input:auditLines.join('\n'),maxBuffer:5_000_000}));
assert.equal(review.experiments.length,1,'One fixed synthetic configuration');
assert.equal(review.experiments[0].summary.completed_positions,audited.strategy.closedtrades.length,'Every completion was audited exactly once');
near(review.experiments[0].summary.net_usd,audited.strategy.closedtrades.reduce((s,t)=>s+t.profit,0),'Audits reconcile completed net P&L');
assert.equal(review.experiments[0].configuration.chart_session,'extended');
assert.equal(review.experiments[0].configuration.symbol,'CME_MINI:MNQZ2026');
assert.equal(review.experiments[0].configuration.evaluation_target_usd,'750.00');
assert.equal(review.experiments[0].configuration.entry_chase_ticks,'4');
console.log('PASS: actual production Pine audit text round-trips through Python reconciliation');
