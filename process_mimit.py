"""
process_mimit.py  — ROOT del repo fuel_radar

Genera:
  output/fuel-data.json          → impianti entro 20km da Mareno di Piave (retrocompatibilità)
  output/fuel-data-veneto.json   → TUTTI gli impianti del Veneto (per artifact universale)

Fonte: Osservaprezzi Carburanti MIMIT — Licenza IODL 2.0
CSV sep: | (pipe) dal 10/02/2026, con riga di intestazione "Estrazione del..."
"""

import os, math, json, sys, logging
from io import StringIO
from datetime import datetime, timezone

import requests
import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────────
# Centro fisso per fuel-data.json (retrocompatibilità)
CENTRO_LAT  = 45.8409
CENTRO_LON  = 12.3507
RAGGIO_KM   = 20.0

# Province del Veneto
PROVINCE_VENETO = {"VE", "TV", "PD", "VR", "VI", "RO", "BL"}

MIMIT_ANA = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"
MIMIT_PRZ = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Utils ─────────────────────────────────────────────────────────────────────
def dist_km(la1, lo1, la2, lo2):
    R = 6371
    dla = math.radians(la2-la1); dlo = math.radians(lo2-lo1)
    a = math.sin(dla/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlo/2)**2
    return round(R*2*math.asin(math.sqrt(a)), 2)

def detect_sep(text):
    for line in text.split("\n")[:5]:
        if "|" in line: return "|"
        if ";" in line: return ";"
    return ";"

def skip_header(text, sep):
    """Salta righe iniziali non-header (es. 'Estrazione del 2026-03-18')."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if sep in line and ("id" in line.lower() or "impianto" in line.lower() or "prezzo" in line.lower()):
            log.info("Header trovato alla riga %d: %s", i, line[:100])
            return "\n".join(lines[i:])
    return text

def norm(c):
    return c.strip().lower().replace(" ", "_").replace("'", "")

def find(cols, *kws):
    for kw in kws:
        for c in cols:
            if kw in c: return c
    return None

# ── Fetch ─────────────────────────────────────────────────────────────────────
def fetch(url):
    log.info("Download: %s", url)
    r = requests.get(url, headers={"User-Agent": "FuelRadar/3.0"}, timeout=60)
    r.raise_for_status()
    return r.content.decode("latin-1", errors="replace")

# ── Parse anagrafica ──────────────────────────────────────────────────────────
def parse_ana(text):
    sep = detect_sep(text)
    text = skip_header(text, sep)
    for i, l in enumerate(text.split("\n")[:2]):
        log.info("  Ana r%d: %s", i, l[:150])

    df = pd.read_csv(StringIO(text), sep=sep, dtype=str,
                     on_bad_lines="skip", encoding_errors="replace")
    df.columns = [norm(c) for c in df.columns]
    log.info("Colonne ana: %s", list(df.columns))
    cols = list(df.columns)

    cid  = find(cols, "idimpianto", "id_impianto", "id")
    clat = find(cols, "latit", "lat")
    clon = find(cols, "longit", "lon")
    if not cid or not clat or not clon:
        log.error("Colonne essenziali mancanti: id=%s lat=%s lon=%s — trovate: %s", cid, clat, clon, cols)
        sys.exit(1)

    ren = {cid: "id", clat: "lat", clon: "lon"}
    for src, dst in [
        (find(cols, "gestore"),       "gestore"),
        (find(cols, "bandiera"),      "bandiera"),
        (find(cols, "nome_impianto", "nomeimpianto", "nome"), "nome"),
        (find(cols, "indirizzo"),     "indirizzo"),
        (find(cols, "comune"),        "comune"),
        (find(cols, "provincia", "prov"), "prov"),
    ]:
        if src: ren[src] = dst
    df = df.rename(columns=ren)

    df["lat"] = pd.to_numeric(df["lat"].str.replace(",", "."), errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"].str.replace(",", "."), errors="coerce")
    df["id"]  = pd.to_numeric(df["id"], errors="coerce")
    df = df.dropna(subset=["id", "lat", "lon"])
    df["id"]  = df["id"].astype(int)
    df["prov"] = df.get("prov", pd.Series([""] * len(df))).fillna("").str.strip().str.upper()
    log.info("Totale impianti anagrafica: %d", len(df))
    return df

# ── Parse prezzi ─────────────────────────────────────────────────────────────
def parse_prz(text, id_set):
    sep = detect_sep(text)
    text = skip_header(text, sep)
    for i, l in enumerate(text.split("\n")[:2]):
        log.info("  Prz r%d: %s", i, l[:150])

    df = pd.read_csv(StringIO(text), sep=sep, dtype=str,
                     on_bad_lines="skip", encoding_errors="replace")
    df.columns = [norm(c) for c in df.columns]
    log.info("Colonne prz: %s", list(df.columns))
    cols = list(df.columns)

    cid   = find(cols, "idimpianto", "id_impianto", "id")
    ccarb = find(cols, "desccarburante", "desccarb", "carburante", "carb", "desc")
    cprz  = find(cols, "prezzo")
    cself = find(cols, "self_service", "isself", "self")

    if not cid or not ccarb or not cprz:
        log.error("Colonne prezzi mancanti id=%s carb=%s prezzo=%s — trovate: %s", cid, ccarb, cprz, cols)
        sys.exit(1)

    ren = {cid: "id", ccarb: "carburante", cprz: "prezzo"}
    if cself: ren[cself] = "self_service"
    df = df.rename(columns=ren)

    df["id"]     = pd.to_numeric(df["id"], errors="coerce")
    df["prezzo"] = pd.to_numeric(df["prezzo"].astype(str).str.replace(",", "."), errors="coerce")
    df["self"]   = pd.to_numeric(
        df.get("self_service", pd.Series(["1"] * len(df))),
        errors="coerce"
    ).fillna(1).astype(int)

    df = df.dropna(subset=["id", "prezzo"])
    df["id"] = df["id"].astype(int)
    df = df[df["id"].isin(id_set)]
    log.info("Righe prezzi filtrate: %d", len(df))
    return df

# ── Build lista impianti ───────────────────────────────────────────────────────
def build_impianti(df_imp, df_prz):
    """Costruisce lista impianti con prezzi. Dist è opzionale (calcolata solo per Mareno)."""
    pm = {}
    for _, r in df_prz.iterrows():
        iid = int(r["id"])
        key = f"{r['carburante']}_{'self' if int(r['self']) else 'serv'}"
        pm.setdefault(iid, {})[key] = round(float(r["prezzo"]), 3)

    out = []
    for _, r in df_imp.iterrows():
        iid = int(r["id"])
        prz = pm.get(iid, {})
        if not prz:
            continue
        entry = {
            "id":       iid,
            "bandiera": str(r.get("bandiera", "")).strip(),
            "nome":     str(r.get("nome", r.get("gestore", ""))).strip(),
            "indirizzo":str(r.get("indirizzo", "")).strip(),
            "comune":   str(r.get("comune", "")).strip(),
            "prov":     str(r.get("prov", "")).strip(),
            "lat":      round(float(r["lat"]), 6),
            "lon":      round(float(r["lon"]), 6),
            "prezzi":   prz,
        }
        out.append(entry)
    return out

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram non configurato (secret mancanti)")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10
    )
    log.info("Telegram: %s", "✓ inviato" if r.ok else f"errore {r.status_code}")

def build_msg(impianti_mareno, aggiornato):
    key = "Gasolio_self"
    prz = [i["prezzi"][key] for i in impianti_mareno if key in i["prezzi"]]
    if not prz: return None
    mn, mx = min(prz), max(prz)
    best = next(i for i in impianti_mareno if i["prezzi"].get(key) == mn)
    top5 = [i for i in impianti_mareno if key in i["prezzi"]][:5]
    righe = "\n".join(
        f"  {j+1}. {i['nome']} ({i['comune']}) — <b>€{i['prezzi'][key]:.3f}</b> · {i.get('dist',0):.1f}km"
        for j, i in enumerate(top5)
    )
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    return (
        f"⛽ <b>FUEL RADAR</b> — {ts}\n"
        f"📍 Mareno di Piave · {RAGGIO_KM:.0f}km\n\n"
        f"<b>Gasolio self oggi:</b>\n"
        f"  🟢 Min: <b>€{mn:.3f}</b> → {best['nome']} ({best.get('dist',0):.1f}km)\n"
        f"  🔴 Max: €{mx:.3f}\n"
        f"  ⚖️ Media: €{sum(prz)/len(prz):.3f}\n"
        f"  💰 Risparmio 60L: €{(mx-mn)*60:.2f}\n\n"
        f"<b>Top 5:</b>\n{righe}"
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    os.makedirs("output", exist_ok=True)
    log.info("=== FUEL RADAR v3 ===")

    # Download CSV
    try:
        ana_text = fetch(MIMIT_ANA)
        prz_text = fetch(MIMIT_PRZ)
    except Exception as e:
        log.error("Download fallito: %s", e)
        sys.exit(1)

    # Parse anagrafica completa
    df_all = parse_ana(ana_text)
    now    = datetime.now(timezone.utc).isoformat()

    # ── 1. fuel-data-veneto.json — tutti gli impianti del Veneto ──────────────
    df_veneto = df_all[df_all["prov"].isin(PROVINCE_VENETO)].copy()
    log.info("Impianti Veneto: %d", len(df_veneto))

    id_veneto = set(df_veneto["id"].tolist())
    df_prz_veneto = parse_prz(prz_text, id_veneto)

    impianti_veneto = build_impianti(df_veneto, df_prz_veneto)
    # Ordina per Gasolio_self
    impianti_veneto.sort(key=lambda x: x["prezzi"].get("Gasolio_self", 99))

    out_veneto = {
        "aggiornato": now,
        "regione":    "Veneto",
        "province":   sorted(PROVINCE_VENETO),
        "impianti":   impianti_veneto,
        "meta": {
            "totale":   len(impianti_veneto),
            "fonte":    "MIMIT Osservaprezzi",
            "licenza":  "IODL 2.0",
            "note":     "Filtro lato client per posizione utente",
        }
    }
    veneto_path = "output/fuel-data-veneto.json"
    with open(veneto_path, "w", encoding="utf-8") as f:
        json.dump(out_veneto, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(veneto_path) / 1024
    log.info("Salvato %s — %d impianti — %.0f KB", veneto_path, len(impianti_veneto), size_kb)

    # ── 2. fuel-data.json — impianti entro 20km da Mareno (retrocompatibilità) ─
    df_all["dist"] = df_all.apply(
        lambda r: dist_km(CENTRO_LAT, CENTRO_LON, r["lat"], r["lon"]), axis=1
    )
    df_mareno = df_all[df_all["dist"] <= RAGGIO_KM].copy()
    log.info("Impianti entro %dkm da Mareno: %d", RAGGIO_KM, len(df_mareno))

    id_mareno = set(df_mareno["id"].tolist())
    df_prz_mareno = parse_prz(prz_text, id_mareno)

    impianti_mareno_raw = build_impianti(df_mareno, df_prz_mareno)
    # Aggiungi dist per Mareno
    dist_map = df_mareno.set_index("id")["dist"].to_dict()
    impianti_mareno = []
    for imp in impianti_mareno_raw:
        imp["dist"] = round(dist_map.get(imp["id"], 0), 2)
        impianti_mareno.append(imp)
    impianti_mareno.sort(key=lambda x: x["prezzi"].get("Gasolio_self", 99))

    out_mareno = {
        "aggiornato": now,
        "centro":     {"lat": CENTRO_LAT, "lon": CENTRO_LON, "nome": "Mareno di Piave (TV)", "raggio_km": RAGGIO_KM},
        "impianti":   impianti_mareno,
        "meta":       {"totale": len(impianti_mareno), "fonte": "MIMIT Osservaprezzi", "licenza": "IODL 2.0"}
    }
    with open("output/fuel-data.json", "w", encoding="utf-8") as f:
        json.dump(out_mareno, f, ensure_ascii=False, indent=2)
    log.info("Salvato output/fuel-data.json — %d impianti", len(impianti_mareno))

    # CORS headers
    with open("output/_headers", "w") as f:
        f.write("/*\n  Access-Control-Allow-Origin: *\n")

    # Telegram
    msg = build_msg(impianti_mareno, now)
    if msg:
        send_telegram(msg)

if __name__ == "__main__":
    main()