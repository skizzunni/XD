import { readFileSync } from 'node:fs';
import { PineTS } from 'pinets';

const source = readFileSync(new URL('../MNQ_R2_Paper.pine', import.meta.url), 'utf8');
const start = Date.parse('2026-10-07T09:30:00-04:00');
const bars = Array.from({length: 80}, (_, i) => ({openTime:start + i*300000, closeTime:start+(i+1)*300000, open:30000+i, high:30002+i, low:29999+i, close:30001+i, volume:100}));
const provider = {
  configure(){},
  async getMarketData(ticker, timeframe) {
    if (timeframe === '15') return bars.filter((_, i)=>i%3===0).map((b,i)=>({...b,closeTime:b.openTime+900000,high:b.high+2,close:b.close+2}));
    return bars;
  },
  async getSymbolInfo() {return {tickerid:'CME_MINI:MNQZ2026',ticker:'MNQZ2026',root:'MNQ',prefix:'CME_MINI',timezone:'America/New_York',type:'futures',mintick:0.25,pointvalue:2,minmove:1,pricescale:4,session:'extended',currency:'USD',mincontract:1};}
};
const runtime = new PineTS(provider,'CME_MINI:MNQZ2026','5',bars.length);
const result = await runtime.run(source);
console.log('Full source smoke: bars',bars.length,'plots',Object.keys(result.plots),'warnings',result.warnings.length);
console.log('strategy',JSON.stringify(result.strategy).slice(0,600));
