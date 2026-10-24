// Generic engine: priors from exposure + reports, Bayesian updates from sensor readings, greedy sortie planner.
const ENGINE = (() => {
  const HAZ = ['veg','conductor','sag','motion','ice','fire'];
  // prior fault probability per km of line inside the storm footprint, per hazard (windstorm, no icing/fire weather)
  const BASE_PER_KM = {veg:.006, conductor:.003, sag:.002, motion:.003, ice:.0003, fire:.0003};
  // how much each report type raises each hazard (likelihood ratio if the report is true and about this line)
  const REPORT_LR = {
    vegetation_and_access_report:{veg:4, sag:1.5},
    protection_event_summary:{veg:2.5, conductor:2, motion:2.5},
    visual_flash_report:{conductor:1.6, motion:1.6, fire:1.6},
    sound_and_warmth_report:{conductor:4},
    conductor_motion_report:{motion:4, sag:1.5},
  };
  const CONF = {'low':.35,'low to medium':.55,'medium':.7,'high':.9};
  // sensor reading likelihood ratios: P(reading | fault) / P(reading | no fault)
  const READ_LR = { strong:8, weak:2, clear:0.25, unclear:1 };
  function pip(x,y,poly){let c=false;for(let i=0,j=poly.length-1;i<poly.length;j=i++){const[a,b]=poly[i],[d,e]=poly[j];if((b>y)!=(e>y)&&x<(d-a)*(y-b)/(e-b)+a)c=!c}return c}
  const km=(a,b)=>Math.hypot((a[0]-b[0])*74.3,(a[1]-b[1])*111.2);
  function kmInside(coords,poly){let L=0;for(let i=1;i<coords.length;i++){const a=coords[i-1],b=coords[i],d=km(a,b);for(let t=0;t<8;t++){const u=(t+.5)/8;if(pip(a[0]+(b[0]-a[0])*u,a[1]+(b[1]-a[1])*u,poly))L+=d/8}}return L}
  function build(segs, ctx, reports, opts={}){
    const storm = ctx.features.find(f=>f.properties.context_kind==='synthetic_storm_footprint').geometry.coordinates[0];
    const areas = ctx.features.filter(f=>f.properties.context_kind==='synthetic_report_area');
    const known = opts.knownDead||[];
    return segs.features.map(f=>{
      const p=f.properties, c=f.geometry.coordinates;
      const exp=kmInside(c,storm);
      const h={}; const why=[];
      for(const z of HAZ) h[z]=Math.min(.5,BASE_PER_KM[z]*Math.max(exp,0.02));
      for(const a of areas){
        const r=reports.find(r=>r.information_id===a.properties.information_id); const lr=REPORT_LR[r.information_type]; if(!lr) continue;
        const kin=kmInside(c,a.geometry.coordinates[0]); if(kin<0.02) continue;
        const share=1-Math.exp(-kin/2); // how much of a 'somewhere in this area' report this line can absorb
        const w=(CONF[r.confidence]??CONF[r.confidence.split(' for')[0]]??.5)*share;
        for(const z in lr){const o=h[z]/(1-h[z])*Math.pow(lr[z],w);h[z]=o/(1+o)}
        why.push(`${r.information_id.replace('SYN_','')} (${kin.toFixed(1)} km)`);
      }
      const dead=known.includes(p.segment_id);
      const cons=(p.voltage_kv>=380?3:1)*(1+0.25*((+p.circuits||1)-1))*(dead?1.6:1);
      return {id:p.segment_id, kv:p.voltage_kv, len:p.length_km, from:p.from_node, to:p.to_node, coords:c, exp, h, why, dead, live: dead?false:null, cons, obs:[]};
    });
  }
  const pFault=s=>1-HAZ.reduce((q,z)=>q*(1-s.h[z]),1);
  const topHaz=s=>HAZ.reduce((a,z)=>s.h[z]>s.h[a]?z:a,HAZ[0]);
  // value of looking: expected fault x consequence, per km we must fly (capped by one sortie's inspect range)
  const priority=(s,P)=>pFault(s)*s.cons/Math.max(2,Math.min(s.len,P.inspectKm));
  function observe(s, hazard, reading, coverage=1){
        const lr=(reading==='clear'||reading==='unclear')?Math.pow(READ_LR[reading],coverage):READ_LR[reading]; // a local finding counts fully; absence of evidence only for the share seen
    const o=s.h[hazard]/(1-s.h[hazard])*lr; s.h[hazard]=o/(1+o); s.obs.push({hazard,reading,coverage});
  }
  function params(a={}){
    const P={flightMin:30, reserveMin:6, cruiseKmh:54, inspectKmh:20, chargeH:3, ...a};
    P.inspectKm=(P.flightMin-P.reserveMin)*P.inspectKmh/60; P.ferryKm=(P.flightMin-P.reserveMin)*P.cruiseKmh/60; return P;
  }
  // greedy: each free package takes the best uncovered segment it can reach and still land on a line believed live
  function plan(S, packs, P, taken=new Set()){
    const out=[];
    const rank=S.filter(s=>!taken.has(s.id)&&(!P.requireEvidence||s.why.length||s.dead)).sort((a,b)=>priority(b,P)-priority(a,P));
    for(const k of packs){
      const t=rank.find(s=>!out.some(o=>o.seg===s.id)); if(!t) break;
      const inspect=Math.min(t.len,P.inspectKm);
      const landing=t.dead? S.find(x=>!x.dead&&(x.from===t.from||x.to===t.from||x.from===t.to||x.to===t.to)&&x.id!==t.id) : t;
      out.push({pack:k, seg:t.id, inspect, coverage:inspect/t.len, land:landing?landing.id:null, score:priority(t,P)});
    }
    return out;
  }
  return {HAZ, build, pFault, topHaz, priority, observe, params, plan, READ_LR};
})();
if (typeof module!=='undefined') module.exports=ENGINE;
