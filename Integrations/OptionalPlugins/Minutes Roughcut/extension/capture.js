// Observe only responses to the page's own timeline_v2 requests. No additional requests.
(() => {
  const eventName='minutes-roughcut-v1';
  let latest=null;
  const match=u=>{try {const x=new URL(u,location.href); return x.protocol==='https:' && /(^|\.)feishu\.cn$/.test(x.hostname) && /\/timeline_v2\/?$/.test(x.pathname) && x.searchParams.get('type')==='0';} catch{return false;}};
  function publish(data){
    if(data?.code!==0 || !Array.isArray(data?.data?.timeline))return;
    const timeline=data.data.timeline.filter(r=>Number.isSafeInteger(r.start_time)&&Number.isSafeInteger(r.stop_time)&&r.stop_time>r.start_time).map(r=>({start_time:r.start_time,stop_time:r.stop_time,type:r.type,uuid:String(r.uuid??''),pid_list:Array.isArray(r.pid_list)?r.pid_list.map(String):[]}));
    latest={page:location.origin+location.pathname,timeline,captured_at:new Date().toISOString()};
    document.dispatchEvent(new CustomEvent(eventName,{detail:JSON.stringify(latest)}));
  }
  document.addEventListener(eventName+'-request',()=>{if(latest && latest.page===location.origin+location.pathname)document.dispatchEvent(new CustomEvent(eventName,{detail:JSON.stringify(latest)}));});
  const originalFetch=window.fetch;
  window.fetch=function(...args){const result=originalFetch.apply(this,args);const url=args[0]?.url??args[0];if(match(url))result.then(r=>{if(r.ok)r.clone().json().then(publish).catch(()=>{});}).catch(()=>{});return result;};
  const open=XMLHttpRequest.prototype.open,send=XMLHttpRequest.prototype.send,targets=new WeakMap();
  XMLHttpRequest.prototype.open=function(method,url,...rest){targets.set(this,match(url));return open.call(this,method,url,...rest);};
  XMLHttpRequest.prototype.send=function(...args){if(targets.get(this))this.addEventListener('load',()=>{try{if(this.status>=200&&this.status<300)publish(this.responseType==='json'?this.response:JSON.parse(this.responseText));}catch{}},{once:true});return send.apply(this,args);};
})();
