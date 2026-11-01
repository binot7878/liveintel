/* Price arithmetic only. No betting model, price discovery or wager placement. */
(function(root){
'use strict';
function price(raw){
    raw=String(raw??'').trim();
    if(!raw||!/^[-+]?\d+(?:\.\d+)?$/.test(raw))throw new Error('Enter observed American or decimal odds.');
    const value=Number(raw);if(!Number.isFinite(value))throw new Error('Enter finite odds.');
    let numerator,denominator;
    if(/^[+-]/.test(raw)){
        if(!Number.isInteger(value)||Math.abs(value)<100||!Number.isSafeInteger(value))throw new Error('American odds must be +100 or higher, or -100 or lower.');
        const a=BigInt(raw.replace(/^\+/, '').split('.')[0]),abs=a<0n?-a:a;
        numerator=a>0n?100n+a:abs+100n;denominator=a>0n?100n:abs;
    }else{
        if(value<=1)throw new Error('Decimal odds must be greater than 1.');
        const [whole,fraction='']=raw.split('.');if(fraction.length>28)throw new Error('Enter odds with at most 28 decimal places.');
        denominator=10n**BigInt(fraction.length);numerator=BigInt(whole)*denominator+BigInt(fraction||'0');
    }
    return {raw,numerator,denominator,decimal:Number(numerator)/Number(denominator)};
}
function combine(prices){
    if(!prices.length)throw new Error('Add at least one priced leg.');
    const result=prices.reduce((r,p)=>({numerator:r.numerator*p.numerator,denominator:r.denominator*p.denominator}),{numerator:1n,denominator:1n});
    result.decimal=Number(result.numerator)/Number(result.denominator);if(!Number.isFinite(result.decimal))throw new Error('Combined odds are too large.');return result;
}
function cents(raw){if(!/^\d+(\.\d{1,2})?$/.test(String(raw)))throw new Error('Enter a positive wager with exact cents.');const [w,f='']=String(raw).split('.');const c=BigInt(w)*100n+BigInt(f.padEnd(2,'0'));if(c<=0n||c>BigInt(Number.MAX_SAFE_INTEGER))throw new Error('Enter a positive wager within supported limits.');return c;}
function payout(stake,odds){const c=cents(stake),r=(2n*c*odds.numerator+odds.denominator)/(2n*odds.denominator);return Number(r)/100;}
function american(decimal){if(!Number.isFinite(decimal)||decimal<=1)return '—';const a=decimal>=2?(decimal-1)*100:-100/(decimal-1);return (a>0?'+':'')+Math.round(a);}
function binary(leg){
    const side=String(leg.side||'').toUpperCase(),market=String(leg.market||'').trim().toLowerCase();
    // Half-point totals/spreads/props exclude a push. Regulation/draw markets are excluded.
    const forbidden=/regulation|3[ -]?way|draw|first.*(?:goal|score)|race to|correct score|double chance|either|both/i.test(market);
    if(forbidden)return false;
    const known=/^(?:points|rebounds|assists|pra|passing yards|rushing yards|receiving yards|receptions|three pointers|3pm|pts|reb|ast|pass_yds|rush_yds|recv_yds|tgt|targets|total|total points|total yards|spread|team total|team total points)$/i.test(market);
    const line=Number(leg.line);
    return known&&['MORE','LESS','OVER','UNDER'].includes(side)&&leg.line!==''&&leg.line!==null&&leg.line!==undefined&&Number.isFinite(line)&&Math.abs(line*2)%2===1;
}
function analyze(legs,stake,combinedOverride=''){
    const prices=legs.map(l=>price(l.odds)),product=combine(prices),quote=combinedOverride?price(combinedOverride):product;
    const wager=Number(cents(stake))/100,gross=payout(stake,quote);
    const ids=legs.map(l=>l.game_id).filter(Boolean),independent=ids.length===legs.length&&new Set(ids).size===ids.length;
    const pairs=legs.map((leg,i)=>{
        if(!binary(leg)||!leg.opposite_odds)return null;
        const other=price(leg.opposite_odds),sum=1/prices[i].decimal+1/other.decimal;
        return {selected:(1/prices[i].decimal)/sum,other:(1/other.decimal)/sum,overround:sum-1,price:prices[i],opposite:other};
    });
    const hasMarketEstimate=pairs.every(Boolean)&&(legs.length===1||independent);
    const probability=hasMarketEstimate?pairs.reduce((p,pair)=>p*pair.selected,1):null;
    return {count:legs.length,stake:wager,combined:quote,product,payout:gross,profit:gross-wager,implied:1/quote.decimal,
        true_probability:null,market_probability:probability,fair_odds:probability?1/probability:null,
        fair_payout:probability?wager/probability:null,difference:probability?wager/probability-gross:null,
        expected:probability?probability*gross-wager:null,house_cut:probability?1-probability*quote.decimal:null,
        pairs,independent,estimated_combined:!combinedOverride&&legs.length>1};
}
function cover(legs,stake){
    if(legs.length<2||legs.length>6)return {eligible:false,reason:'Cover Every Way supports 2–6 binary legs.'};
    const ids=legs.map(l=>l.game_id);
    if(ids.some(id=>!id)||new Set(ids).size!==ids.length)return {eligible:false,reason:'Separate games are required; same-game outcomes need a joint probability and price.'};
    if(legs.some(l=>!binary(l)||!l.odds||!l.opposite_odds))return {eligible:false,reason:'Each leg needs a supported half-point binary market and observed odds for both sides. Push, draw and unpriced outcomes are not covered.'};
    const pairs=legs.map(l=>[price(l.odds),price(l.opposite_odds)]),n=2**legs.length,wager=Number(cents(stake))/100,total=wager*n;
    const combinations=[];
    for(let mask=0;mask<n;mask++){
        const choices=legs.map((leg,i)=>Boolean(mask&(1<<i)));
        const selected=pairs.map((pair,i)=>pair[choices[i]?1:0]);
        const probability=selected.reduce((p,quote,i)=>p*(1/quote.decimal)/(1/pairs[i][0].decimal+1/pairs[i][1].decimal),1);
        const quote=combine(selected),gross=payout(stake,quote);
        combinations.push({choices,decimal:quote.decimal,probability,payout:gross,net:gross-total});
    }
    return {eligible:true,count:n,total,stake:wager,combinations,expected:combinations.reduce((s,c)=>s+c.probability*c.payout,0)-total};
}
const api={price,combine,cents,payout,american,binary,analyze,cover};
if(typeof module!=='undefined')module.exports=api;else root.TicketMath=api;
})(typeof globalThis!=='undefined'?globalThis:this);
