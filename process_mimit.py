import os, math, json, sys, logging
from io import StringIO
from datetime import datetime, timezone
import requests
import pandas as pd

CENTRO_LAT, CENTRO_LON, RAGGIO_KM = 45.8409, 12.3507, 20.0
MIMIT_ANA = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"
MIMIT_PRZ = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

def dist_km(la1, lo1, la2, lo2):
    R = 6371
    dla = math.radians(la2-la1); dlo = math.radians(lo2-lo1)
    a = math.sin(dla/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlo/2)**2
    return round(R*2*math.asin(math.sqrt(a)), 2)

def detect_sep(text):
    """Cerca il separatore nelle prime 5 righe, saltando righe senza | o ;"""
    for line in text.split("\n")[:5]:
        if "|" in line: return "|"
        if ";" in line: return ";"
    return ";"

def skip_header_rows(text, sep):
    """Salta le righe iniziali che non sono l'intestazione vera (es. 'Estrazione del ...')"""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if sep in line and ("id" in line.lower() or "impianto" in line.lower() or "prezzo" in line.lower()):
            log.info("Header trovato alla riga %d: %s", i, line[:100])
            return "\n".join(lines[i:])
    return text  # fallback: torna il testo originale

def norm(c):
    return c.strip().lower().replace(" ","_").replace("'","")

def find(cols, *kws):
    for kw in kws:
        for c in cols:
            if kw in c: return c
    return None

def fetch(url):
    log.info("Download: %s", url)
    r = requests.get(url, headers={"User-Agent":"FuelRadar/2.0"}, timeout=60)
    r.raise_for_status()
    return r.content.decode("latin-1", errors="replace")

def parse_ana(text):
    sep = detect_sep(text)
    log.info("Anagrafica sep='%s'", sep)
    text = skip_header_rows(text, sep)
    for i,l in enumerate(text.split("\n")[:2]): log.info("  r%d: %s", i, l[:180])
    df = pd.read_csv(StringIO(text), sep=sep, dtype=str, on_bad_lines="skip", encoding_errors="replace")
    df.columns = [norm(c) for c in df.columns]
    log.info("Colonne: %s", list(df.columns))
    cols = list(df.columns)
    cid  = find(cols,"idimpianto","id_impianto","id")
    clat = find(cols,"latit","lat")
    clon = find(cols,"longit","lon")
    if not cid or not clat or not clon:
        log.error("Colonne mancanti id=%s lat=%s lon=%s — trovate: %s", cid, clat, clon, cols)
        sys.exit(1)
    ren = {cid:"id", clat:"lat", clon:"lon"}
    for src,dst in [(find(cols,"gestore"),"gestore"),(find(cols,"bandiera"),"bandiera"),
                    (find(cols,"nome_impianto","nomeimpianto","nome"),"nome"),
                    (find(cols,"indirizzo"),"indirizzo"),(find(cols,"comune"),"comune"),
                    (find(cols,"provincia","prov"),"prov")]:
        if src: ren[src]=dst
    df = df.rename(columns=ren)
    df["lat"] = pd.to_numeric(df["lat"].str.replace(",","."), errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"].str.replace(",","."), errors="coerce")
    df["id"]  = pd.to_numeric(df["id"], errors="coerce")
    df = df.dropna(subset=["id","lat","lon"])
    df["id"] = df["id"].astype(int)
    df["dist"] = df.apply(lambda r: dist_km(CENTRO_LAT,CENTRO_LON,r["lat"],r["lon"]), axis=1)
    df = df[df["dist"]<=RAGGIO_KM].copy()
    log.info("Impianti nel raggio %.0f km: %d", RAGGIO_KM, len(df))
    return df

def parse_prz(text, id_set):
    sep = detect_sep(text)
    log.info("Prezzi sep='%s'", sep)
    text = skip_header_rows(text, sep)
    for i,l in enumerate(text.split("\n")[:2]): log.info("  r%d: %s", i, l[:180])
    df = pd.read_csv(StringIO(text), sep=sep, dtype=str, on_bad_lines="skip", encoding_errors="replace")
    df.columns = [norm(c) for c in df.columns]
    log.info("Colonne: %s", list(df.columns))
    cols = list(df.columns)
    cid   = find(cols,"idimpianto","id_impianto","id")
    ccarb = find(cols,"desccarburante","desccarb","carburante","carb","desc")
    cprz  = find(cols,"prezzo")
    cself = find(cols,"self_service","self")
    if not cid or not ccarb or not cprz:
        log.error("Colonne mancanti id=%s carb=%s prezzo=%s — trovate: %s", cid, ccarb, cprz, cols)
        sys.exit(1)
    ren = {cid:"id", ccarb:"carburante", cprz:"prezzo"}
    if cself: ren[cself]="self_service"
    df = df.rename(columns=ren)
    df["id"]    = pd.to_numeric(df["id"], errors="coerce")
    df["prezzo"]= pd.to_numeric(df["prezzo"].astype(str).str.replace(",","."), errors="coerce")
    df["self"]  = pd.to_numeric(df.get("self_service", pd.Series(["1"]*len(df))), errors="coerce").fillna(1).astype(int)
    df = df.dropna(subset=["id","prezzo"])
    df["id"] = df["id"].astype(int)
    df = df[df["id"].isin(id_set)]
    log.info("Righe prezzi nel raggio: %d", len(df))
    return df

def build(df_imp, df_prz):
    pm = {}
    for _,r in df_prz.iterrows():
        k = f"{r['carburante']}_{'self' if int(r['self']) else 'serv'}"
        pm.setdefault(int(r["id"]),{})[k] = round(float(r["prezzo"]),3)
    out = []
    for _,r in df_imp.iterrows():
        iid = int(r["id"]); prz = pm.get(iid,{})
        if not prz: continue
        out.append({"id":iid,
            "gestore":  str(r.get("gestore","")).strip(),
            "bandiera": str(r.get("bandiera","")).strip(),
            "nome":     str(r.get("nome", r.get("gestore",""))).strip(),
            "indirizzo":str(r.get("indirizzo","")).strip(),
            "comune":   str(r.get("comune","")).strip(),
            "prov":     str(r.get("prov","")).strip(),
            "lat":  round(float(r["lat"]),6),
            "lon":  round(float(r["lon"]),6),
            "dist": float(r["dist"]),
            "prezzi": prz})
    out.sort(key=lambda x: x["prezzi"].get("Gasolio_self",99))
    return {"aggiornato": datetime.now(timezone.utc).isoformat(),
            "centro": {"lat":CENTRO_LAT,"lon":CENTRO_LON,"nome":"Mareno di Piave (TV)","raggio_km":RAGGIO_KM},
            "impianti": out,
            "meta": {"totale":len(out),"fonte":"MIMIT Osservaprezzi","licenza":"IODL 2.0"}}

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram non configurato (secret mancanti)")
        return
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id":TELEGRAM_CHAT_ID,"text":text,"parse_mode":"HTML"}, timeout=10)
    log.info("Telegram: %s", "✓ inviato" if r.ok else f"errore {r.status_code} {r.text}")

def build_msg(data):
    key = "Gasolio_self"
    imp = data["impianti"]
    prz = [i["prezzi"][key] for i in imp if key in i["prezzi"]]
    if not prz: return None
    mn, mx = min(prz), max(prz)
    best = next(i for i in imp if i["prezzi"].get(key)==mn)
    top5 = [i for i in imp if key in i["prezzi"]][:5]
    righe = "\n".join(f"  {j+1}. {i['nome']} ({i['comune']}) — <b>€{i['prezzi'][key]:.3f}</b> · {i['dist']:.1f}km"
                      for j,i in enumerate(top5))
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    return (f"⛽ <b>FUEL RADAR</b> — {ts}\n"
            f"📍 Mareno di Piave · {RAGGIO_KM:.0f}km\n\n"
            f"<b>Gasolio self oggi:</b>\n"
            f"  🟢 Min: <b>€{mn:.3f}</b> → {best['nome']} ({best['dist']:.1f}km)\n"
            f"  🔴 Max: €{mx:.3f}\n"
            f"  ⚖️ Media: €{sum(prz)/len(prz):.3f}\n"
            f"  💰 Risparmio 60L: €{(mx-mn)*60:.2f}\n\n"
            f"<b>Top 5:</b>\n{righe}")

def main():
    os.makedirs("output", exist_ok=True)
    log.info("=== FUEL RADAR ===")
    try:
        ana = fetch(MIMIT_ANA)
        prz = fetch(MIMIT_PRZ)
    except Exception as e:
        log.error("Download fallito: %s", e); sys.exit(1)
    df_imp = parse_ana(ana)
    if df_imp.empty:
        log.error("Nessun impianto nel raggio"); sys.exit(1)
    df_prz = parse_prz(prz, set(df_imp["id"].tolist()))
    data = build(df_imp, df_prz)
    with open("output/fuel-data.json","w",encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open("output/_headers","w") as f:
        f.write("/fuel-data.json\n  Access-Control-Allow-Origin: *\n")
    log.info("Salvato output/fuel-data.json — %d impianti", len(data["impianti"]))
    msg = build_msg(data)
    if msg: send_telegram(msg)

if __name__ == "__main__":
    main()
