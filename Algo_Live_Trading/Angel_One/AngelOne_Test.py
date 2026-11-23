# AngelOne_Test.py - READ-ONLY check (no orders): TOTP login, profile, funds, positions. Stdlib only.
import base64, hashlib, hmac, json, struct, time, urllib.request
from AngelOne_Config import *

BASE = "https://apiconnect.angelone.in"

# Fresh Windows VMs miss some public root certificates -> "CERTIFICATE_VERIFY_FAILED".
# If certifi is installed (py -m pip install certifi) its root list is used in addition to Windows' own.
import ssl
_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX.load_verify_locations(cafile=certifi.where())
except ImportError:
    pass


def _open(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX)


def totp(secret, step=30, digits=6):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", int(time.time()) // step), hashlib.sha1).digest()
    o = h[-1] & 15
    return str(((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits)).zfill(digits)


def public_ip():
    return _open("https://api.ipify.org", 10).read().decode()


def call(path, body=None, token=None, ip=""):
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "X-UserType": "USER", "X-SourceID": "WEB", "X-ClientLocalIP": "127.0.0.1",
         "X-ClientPublicIP": ip or "127.0.0.1", "X-MACAddress": "00:00:00:00:00:00",
         "X-PrivateKey": AO_API_KEY}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=h, method="POST" if body is not None else "GET")
    try:
        return json.loads(_open(req, 20).read())
    except urllib.error.HTTPError as e:
        return {"status": False, "message": f"HTTP {e.code}", "body": e.read().decode()[:300]}


if __name__ == "__main__":
    ip = public_ip()
    print("This PC public IP:", ip, "| registered static IP:", AO_STATIC_IP, "| match:", ip == AO_STATIC_IP)
    r = call("/rest/auth/angelbroking/user/v1/loginByPassword",
             {"clientcode": AO_CLIENT_CODE, "password": AO_PIN, "totp": totp(AO_TOTP_KEY)}, ip=ip)
    print("LOGIN:", r.get("status"), r.get("message"), r.get("errorcode"))
    if not r.get("status"):
        print(r); raise SystemExit(1)
    tok = r["data"]["jwtToken"]
    p = call("/rest/secure/angelbroking/user/v1/getProfile", token=tok, ip=ip)
    print("PROFILE:", p.get("status"), p.get("message"), "| exchanges:", (p.get("data") or {}).get("exchanges"))
    f = call("/rest/secure/angelbroking/user/v1/getRMS", token=tok, ip=ip)
    d = f.get("data") or {}
    print("FUNDS:", f.get("status"), f.get("message"), "| available cash:", d.get("availablecash"))
    pos = call("/rest/secure/angelbroking/order/v1/getPosition", token=tok, ip=ip)
    print("POSITIONS:", pos.get("status"), pos.get("message"), "| count:", len(pos.get("data") or []))
