"""The one-tap page the child opens from the text. Plain HTML, no third-party scripts."""

LINK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Ayo</title><style>
body{font:18px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:28px 22px;background:#0e0e0e;color:#f7f1e4}
button{display:block;width:100%;padding:18px;font-size:20px;font-weight:600;border:0;border-radius:14px;background:#cba868;color:#111;margin-top:22px}
p{margin:0 0 10px}#s{margin-top:18px;color:#cfc8b8}
</style></head><body>
<p><b>Ayo</b></p><p>Tap the button, then allow location. This shares where you are with the people helping you.</p>
<button id="b">Share my location</button><p id="s"></p>
<script>
var b=document.getElementById('b'),s=document.getElementById('s');
b.onclick=function(){
 if(!navigator.geolocation){s.textContent='This phone cannot share location. Tell Ayo where you are.';return;}
 b.disabled=true;s.textContent='Finding you...';
 navigator.geolocation.getCurrentPosition(function(p){
  fetch(location.pathname.replace('/l/','/api/l/'),{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({latitude:p.coords.latitude,longitude:p.coords.longitude,accuracy_m:p.coords.accuracy})})
  .then(function(r){s.textContent=r.ok?'Done. You can close this page.':'This link has expired. Tell Ayo where you are.';b.style.display='none';})
  .catch(function(){s.textContent='No connection. Tell Ayo where you are.';b.disabled=false;});
 },function(){s.textContent='Location is turned off. That is okay, just tell Ayo where you are.';b.disabled=false;},
 {enableHighAccuracy:true,timeout:15000,maximumAge:0});
};
</script></body></html>"""

EXPIRED_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ayo</title></head><body style="font:18px -apple-system,system-ui,sans-serif;padding:28px 22px;background:#0e0e0e;color:#f7f1e4">
<p>This link has expired. If you still want to talk, call Ayo again.</p></body></html>"""
