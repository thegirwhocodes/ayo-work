"""Have Ayo ring a phone. Usage:  .venv/bin/python scripts/call_me.py +234XXXXXXXXXX https://YOUR-PUBLIC-URL

Needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER in the environment. A Twilio TRIAL account can
only call numbers verified in the console, and plays Twilio's trial notice first. The public URL must reach
this server (e.g. a cloudflared tunnel); Twilio fetches <URL>/voice/incoming when the person answers.
"""
import base64, json, os, sys, urllib.parse, urllib.request

if len(sys.argv) != 3:
    sys.exit(__doc__)
to, base = sys.argv[1], sys.argv[2].rstrip("/")
sid, token, frm = (os.environ.get(k, "") for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"))
if not (sid and token and frm):
    sys.exit("Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER")
data = urllib.parse.urlencode({"To": to, "From": frm, "Url": f"{base}/voice/incoming", "Method": "POST"}).encode()
request = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json", data=data)
request.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
call = json.load(urllib.request.urlopen(request, timeout=20))
print("Ringing", to[:-4].replace("+", "+") + "XXXX" if len(to) > 4 else to, "| call", call["sid"], "|", call["status"])
