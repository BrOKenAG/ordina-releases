(function(g){
function normalize(rows,pad=0,gap=0,duration=null){
 if(!Array.isArray(rows)||![pad,gap].every(n=>Number.isSafeInteger(n)&&n>=0)|| (duration!==null&&(!Number.isSafeInteger(duration)||duration<=0)))throw Error('区间或设置无效');
 const items=[];let ignored=0;
 for(const r of rows){if(r.type!==1){ignored++;continue;}const a=r.start_time,b=r.stop_time;if(!Number.isSafeInteger(a)||!Number.isSafeInteger(b)||a<0||b<=a||(duration!==null&&b>duration))throw Error('存在非法或超出视频范围的标注');items.push({start_ms:Math.max(0,a-pad),end_ms:duration===null?b+pad:Math.min(duration,b+pad)});}
 items.sort((a,b)=>a.start_ms-b.start_ms||a.end_ms-b.end_ms);const ranges=[];for(const r of items){const p=ranges.at(-1);if(p&&r.start_ms<=p.end_ms+gap)p.end_ms=Math.max(p.end_ms,r.end_ms);else ranges.push({...r});}
 return {ranges,total_ms:ranges.reduce((s,r)=>s+r.end_ms-r.start_ms,0),raw_count:items.length,ignored};
}
g.Roughcut={normalize};if(typeof module!=='undefined')module.exports=g.Roughcut;
})(globalThis);
