"""Responder console: live location evidence per call. One static page that polls /api/location/cases."""

CONSOLE_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Ayo · Caller location</title><style>
:root{--g:#cba868;--bg:#0e0e0e;--card:#171717;--line:#2a2a2a;--tx:#f7f1e4;--mute:#a79f8d;--ok:#6fbf8b;--bad:#e8705f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 -apple-system,system-ui,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:baseline}
h1{font-size:22px;margin:0}h1 b{color:var(--g)}.mute{color:var(--mute)}
main{padding:20px 24px;display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;display:grid;gap:10px}
.top{display:flex;justify-content:space-between;gap:8px;align-items:center}
.pill{padding:2px 10px;border-radius:99px;border:1px solid var(--line);font-size:12px;color:var(--mute)}
.pill.live{color:var(--ok);border-color:var(--ok)}
.row{display:grid;grid-template-columns:150px 1fr;gap:8px}.row span:first-child{color:var(--mute)}
.banner{padding:10px 12px;border-radius:10px;font-size:14px}.agree{background:#12251a;border:1px solid var(--ok)}
.disagree{background:#2a1512;border:1px solid var(--bad)}.insufficient{background:#1d1d1d;border:1px solid var(--line);color:var(--mute)}
iframe{width:100%;height:240px;border:1px solid var(--line);border-radius:10px;background:#222}
a{color:var(--g)}ul{margin:0;padding-left:18px}button,select{background:#222;color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font:inherit}
.empty{padding:40px;text-align:center}
</style></head><body>
<header><h1><b>Ayo</b> · Caller location</h1><span class="mute" id="stamp"></span></header>
<main id="m"><div class="empty mute">Waiting for a call…</div></main>
<script>
var KEY=new URLSearchParams(location.search).get('key')||'';var profiles=[];
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function maps(la,lo){return 'https://www.google.com/maps/search/?api=1&query='+la.toFixed(6)+','+lo.toFixed(6)}
function pin(c){var p=c.gps||c.device_service||c.spoken_point;if(!p)return null;
 return{la:p.latitude,lo:p.longitude}}
function map(c){var p=pin(c);if(!p)return '';var d=0.012;
 var u='https://www.openstreetmap.org/export/embed.html?bbox='+[p.lo-d,p.la-d,p.lo+d,p.la+d].join('%2C')+'&layer=mapnik&marker='+p.la+'%2C'+p.lo;
 return '<iframe loading="lazy" referrerpolicy="no-referrer" src="'+u+'"></iframe>'}
function card(c){
 var h='<div class="card"><div class="top"><b>'+esc(c.id)+' · '+esc(c.caller_number||'unknown number')+'</b>'
  +'<span class="pill '+(c.live?'live':'')+'">'+(c.live?'● live call':'ended')+'</span></div>';
 if(c.cross_check)h+='<div class="banner '+esc(c.cross_check.status)+'">'+esc(c.cross_check.summary)+'</div>';
 h+=row('Best signal',c.best.replace('_',' '));
 h+=row('GPS link',c.gps?('<a target="_blank" rel="noopener" href="'+maps(c.gps.latitude,c.gps.longitude)+'">open map</a>'+(c.gps.accuracy_m?' ±'+Math.round(c.gps.accuracy_m)+' m':'')):(c.link_sent_via.length?'link sent, waiting for the tap':'no link sent'),true);
 h+=row('Device location',c.device_service?('<a target="_blank" rel="noopener" href="'+maps(c.device_service.latitude,c.device_service.longitude)+'">'+esc(c.device_service.area_label)+'</a> ±'+Math.round(c.device_service.accuracy_m)+' m'):'—',true);
 h+=row('Caller said',c.spoken_address?(esc(c.spoken_address)+(c.spoken_point?' ≈ '+esc(c.spoken_point.name):'')):'—',true);
 h+=row('Phone network',c.network||'unknown');
 if(c.nearest_help&&c.nearest_help.length){h+='<div><div class="mute">Nearest help</div><ul>'+c.nearest_help.map(function(x){
  return '<li>'+esc(x.name)+(x.phone?' · <b>'+esc(x.phone)+'</b>':'')+(x.distance_km!=null?' · '+x.distance_km+' km':'')+'</li>'}).join('')+'</ul></div>'}
 h+=map(c);
 h+='<div><select id="p-'+esc(c.id)+'">'+profiles.map(function(p){return '<option value="'+esc(p.id)+'">'+esc(p.label)+'</option>'}).join('')+'</select> '
  +'<button onclick="lookup(\''+esc(c.id)+'\')">Look up device location</button> '
  +'<button onclick="resend(\''+esc(c.id)+'\')">Re-send link</button></div></div>';
 return h}
function row(a,b,raw){return '<div class="row"><span>'+a+'</span><span>'+(raw?b:esc(b))+'</span></div>'}
function api(path,opt){opt=opt||{};opt.headers=Object.assign({'X-Console-Key':KEY,'Content-Type':'application/json'},opt.headers||{});return fetch(path,opt)}
function lookup(id){api('/api/location/cases/'+id+'/device-lookup',{method:'POST',body:JSON.stringify({profile_id:document.getElementById('p-'+id).value})}).then(tick)}
function resend(id){api('/api/location/cases/'+id+'/send-link',{method:'POST'}).then(tick)}
var busy=false,last='';
function tick(){if(busy)return;busy=true;api('/api/location/cases').then(function(r){return r.json()}).then(function(d){
 profiles=d.profiles||[];var sel=document.activeElement&&document.activeElement.tagName==='SELECT';
 var sig=JSON.stringify(d);if(!sel&&sig!==last){last=sig;document.getElementById('m').innerHTML=d.cases.length?d.cases.map(card).join(''):'<div class="empty mute">Waiting for a call…</div>'}
 document.getElementById('stamp').textContent='updated '+new Date().toLocaleTimeString()}).catch(function(){
 document.getElementById('stamp').textContent='no connection or wrong key'}).finally(function(){busy=false})}
tick();setInterval(tick,2000);
</script></body></html>"""
