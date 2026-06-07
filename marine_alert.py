#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marine_alert.py
-----------------
Στέλνει στο WhatsApp σου τα επίσημα δελτία METAREA III από το WMO WWMIWS
(ΟΛΕΣ οι περιοχές, με κάθε περιοχή σε *bold*).

Πηγή: https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html

Υποστηρίζει 2 τρόπους αποστολής (env: SENDER = "twilio" ή "callmebot"):

  SENDER=twilio  (δουλεύει αμέσως μέσω Twilio WhatsApp Sandbox)
     TWILIO_SID, TWILIO_TOKEN  -> από το Twilio Console
     TWILIO_FROM  -> π.χ. whatsapp:+14155238886 (ο αριθμός του sandbox)
     TWILIO_TO    -> π.χ. whatsapp:+3069XXXXXXXX (το δικό σου)

  SENDER=callmebot
     CALLMEBOT_PHONE, CALLMEBOT_APIKEY

Χρήση:
  python marine_alert.py --selftest    # offline έλεγχος
  python marine_alert.py --dry-run     # τυπώνει αντί να στείλει
  python marine_alert.py               # στέλνει
"""

import os
import re
import sys
import json
import html
import time
import base64
import argparse
import hashlib
import urllib.parse
import urllib.request
import urllib.error
import ssl

# --------- Ρυθμίσεις ---------
SENDER = os.environ.get("SENDER", "").strip().lower()

# CallMeBot
PHONE  = os.environ.get("CALLMEBOT_PHONE", "").strip()
APIKEY = os.environ.get("CALLMEBOT_APIKEY", "").strip()

# Twilio
TWILIO_SID   = os.environ.get("TWILIO_SID", "").strip()
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN", "").strip()
TWILIO_FROM  = os.environ.get("TWILIO_FROM", "whatsapp:+14155238886").strip()
TWILIO_TO    = os.environ.get("TWILIO_TO", "").strip()

# Open-Meteo (προαιρετικό)
LAT = float(os.environ.get("LAT", "40.55"))
LON = float(os.environ.get("LON", "22.95"))

INCLUDE_WEST = os.environ.get("INCLUDE_WEST", "0") == "1"
STATE_FILE   = os.environ.get("STATE_FILE", "state.json")

BULLETINSET_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
MAX_CHARS  = 1400            # ασφαλές όριο ανά μήνυμα WhatsApp
USER_AGENT = "Mozilla/5.0 (marine-alert; captains association)"

WMO_HEADER = re.compile(r"[A-Z]{4}\d{2}\s+[A-Z]{4}\s+\d{6}")
NOT_AREA = {"WARNING NONE", "METEO-FRANCE"}


# --------- Δίκτυο (με SSL fallback για Windows) ---------
def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


def _open(req, timeout=25):
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX).read()
    except (ssl.SSLError, urllib.error.URLError) as e:
        if "CERTIFICATE_VERIFY" not in str(e):
            raise
        print("Προσοχή: SSL χωρίς επαλήθευση (τρέξε: python -m pip install certifi)")
        ctx = ssl._create_unverified_context()
        return urllib.request.urlopen(req, timeout=timeout, context=ctx).read()


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _open(req).decode("utf-8", errors="replace")


def http_post(url: str, data: dict, auth: str = "") -> str:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    if auth:
        req.add_header("Authorization", "Basic " + auth)
    return _open(req).decode("utf-8", errors="replace")


# --------- METAREA III bulletin ---------
def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    lines = [l.strip() for l in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _is_area(line: str) -> bool:
    s = line.strip().rstrip(".")
    if not s or s in NOT_AREA:
        return False
    if any(c.isdigit() for c in s):
        return False
    if not re.fullmatch(r"[A-Z /]{3,}", s):
        return False
    return len(s.split()) <= 4


def _format_body(body: str) -> str:
    lines = [l.strip() for l in body.splitlines()]
    out = []
    for i, s in enumerate(lines):
        if not s:
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if _is_area(s) and (any(c.isdigit() for c in nxt) or "." in nxt):
            out.append("*" + s + "*")
        else:
            out.append(s)
    return "\n".join(out)


def parse_bulletinset(text: str):
    parts = WMO_HEADER.split(text)
    headers = WMO_HEADER.findall(text)
    bulletins = []
    pre = parts[0]
    for idx, header in enumerate(headers):
        body = parts[idx + 1] if idx + 1 < len(parts) else ""
        title_lines = [l.strip() for l in pre.splitlines()
                       if l.strip() and set(l.strip()) != {"-"}]
        title = title_lines[-1] if title_lines else header
        body = body.split("=")[0].strip()
        bulletins.append((title, header, body))
        pre = parts[idx + 1] if idx + 1 < len(parts) else ""
    return bulletins


def get_metarea_bulletin() -> str:
    raw = http_get(BULLETINSET_URL)
    bulletins = parse_bulletinset(_strip_html(raw))
    if not bulletins:
        return "(METAREA III: δεν βρέθηκαν δελτία στη σελίδα WMO)"
    chosen = []
    for title, header, body in bulletins:
        if "WEST" in title.upper() and not INCLUDE_WEST:
            continue
        chosen.append("*== " + title + " ==*\n" + _format_body(body))
    return "\n\n".join(chosen) if chosen else "(METAREA III: δεν επιλέχθηκε δελτίο)"


# --------- Open-Meteo (προαιρετικό) ---------
def beaufort(knots: float) -> int:
    limits = [1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64]
    for i, lim in enumerate(limits):
        if knots < lim:
            return i
    return 12


def deg_to_compass(deg: float) -> str:
    dirs = ["Β", "ΒΑ", "Α", "ΝΑ", "Ν", "ΝΔ", "Δ", "ΒΔ"]
    return dirs[int((deg / 45) + 0.5) % 8]


def get_marine_weather() -> str:
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={LAT}&longitude={LON}"
           "&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m"
           "&wind_speed_unit=kn&forecast_days=1&timezone=Europe%2FAthens")
    w = json.loads(http_get(url))
    t, spd, gust, wdir = (w["hourly"]["time"], w["hourly"]["wind_speed_10m"],
                          w["hourly"]["wind_gusts_10m"], w["hourly"]["wind_direction_10m"])
    lines = [f"*Open-Meteo {LAT:.2f}N {LON:.2f}E*"]
    for i in [0, min(3, len(t) - 1), min(6, len(t) - 1)]:
        lines.append(f"{t[i][-5:]}: {deg_to_compass(wdir[i])} {beaufort(spd[i])} Bf "
                     f"({spd[i]:.0f}kt, ριπές {gust[i]:.0f}kt)")
    return "\n".join(lines)


# --------- Αποστολή ---------
def _chunks(text: str, limit: int = MAX_CHARS):
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur); cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


def _send_callmebot(piece: str):
    url = ("https://api.callmebot.com/whatsapp.php"
           f"?phone={urllib.parse.quote(PHONE)}"
           f"&text={urllib.parse.quote(piece)}"
           f"&apikey={urllib.parse.quote(APIKEY)}")
    print("CallMeBot:", http_get(url)[:120])


def _send_twilio(piece: str):
    sid, tok = TWILIO_SID, TWILIO_TOKEN
    frm = TWILIO_FROM if TWILIO_FROM.startswith("whatsapp:") else "whatsapp:" + TWILIO_FROM
    to  = TWILIO_TO if TWILIO_TO.startswith("whatsapp:") else "whatsapp:" + TWILIO_TO
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    auth = base64.b64encode(f"{sid}:{tok}".encode()).decode()
    resp = http_post(url, {"From": frm, "To": to, "Body": piece}, auth=auth)
    try:
        j = json.loads(resp)
        print("Twilio:", j.get("status", "?"), j.get("error_message") or "")
    except Exception:
        print("Twilio:", resp[:200])


def send_whatsapp(text: str):
    twilio_ready = bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_TO)
    if SENDER == "twilio" or (SENDER != "callmebot" and twilio_ready):
        if not twilio_ready:
            raise SystemExit("Λείπει TWILIO_SID / TWILIO_TOKEN / TWILIO_TO.")
        sender = _send_twilio
    else:
        if not (PHONE and APIKEY):
            raise SystemExit("Δεν βρέθηκαν στοιχεία αποστολής "
                             "(ούτε TWILIO_*, ούτε CALLMEBOT_*).")
        sender = _send_callmebot
    pieces = _chunks(text)
    for n, piece in enumerate(pieces, 1):
        tag = f"({n}/{len(pieces)})\n" if len(pieces) > 1 else ""
        sender(tag + piece)
        if n < len(pieces):
            time.sleep(6)


def already_sent(text: str) -> bool:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    prev = None
    try:
        with open(STATE_FILE) as f:
            prev = json.load(f).get("hash")
    except Exception:
        pass
    if prev == h:
        return True
    with open(STATE_FILE, "w") as f:
        json.dump({"hash": h}, f)
    return False


def build_message(source: str) -> str:
    parts = []
    if source in ("bulletin", "all"):
        parts.append(get_metarea_bulletin())
    if source in ("weather", "all"):
        parts.append(get_marine_weather())
    return "\n\n".join(parts)


def selftest():
    assert beaufort(0) == 0 and beaufort(5) == 2 and beaufort(35) == 8
    assert deg_to_compass(0) == "Β" and deg_to_compass(90) == "Α"
    sample = ("X\n---\nEAST / HIGH SEAS FORECAST\n---\nFQME24 LGAT 150800\n\n"
              "PART 3\nTHERMAIKOS\nSOUTH 3 OR 4. SLIGHT\nCENTRAL AEGEAN\n"
              "VARIABLE 3 OR 4. SLIGHT=\n")
    b = parse_bulletinset(sample)
    assert b and b[0][0] == "EAST / HIGH SEAS FORECAST"
    fb = _format_body(b[0][2])
    assert "*THERMAIKOS*" in fb and "*PART" not in fb
    print("OK — parser + bold + helpers σωστά.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="bulletin", choices=["bulletin", "weather", "all"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-dedup", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest(); return
    msg = build_message(args.source)
    if args.dry_run:
        print("---- DRY RUN ----\n" + msg); return
    if not args.no_dedup and already_sent(msg):
        print("Ίδιο με το προηγούμενο — δεν στέλνω."); return
    send_whatsapp(msg)
    print("Στάλθηκε.")


if __name__ == "__main__":
    main()
