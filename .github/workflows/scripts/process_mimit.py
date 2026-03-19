"""
scripts/process_mimit.py

Scarica i CSV ufficiali MIMIT, filtra i distributori nel raggio
configurato da Mareno di Piave, salva output/fuel-data.json
e invia notifica Telegram se il prezzo scende sotto soglia.

Fonte: Osservaprezzi Carburanti — Licenza IODL 2.0
CSV sep: | (pipe) dal 10/02/2026
"""

import os, math, json, sys, logging
from io import StringIO
from datetime import datetime, timezone

import requests
import pandas as pd

# ── Config ──────────────────────────────────────────────────────────
CENTRO_LAT  = 45.8409   # Mareno di Piave
CENTRO_LON  = 12.3507
RAGGIO_KM   = 20.0

MIMIT_ANAGRAFICA = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"
MIMIT_PREZZI     = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SOGLIA_STR       = os.environ.get("SOGLIA_DIESEL", "")
SOGLIA_DIESEL    = float(SOGLIA_STR) if SOGLIA_STR else None

OUTPUT_DIR = "output"
OUTPUT_FILE = f"{OUTPUT_DIR}/fuel-data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Haversine ────────────────────────────────────────────────────────
def dist_km(la1, lo1, la2, lo2):
    R = 6371
    dla = math.radians(la2 - la1)
    dlo = math.radians(lo2 - lo1)
    a = math.sin(dla/2)**2 + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlo/2)**2
    return round(R * 2 * math.asin(math.sqrt(a)), 2)

# ── Fetch CSV ────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "FuelRadar/2.0 (github-actions; IODL-2.0)"}

def fetch_csv(url: str) -> str:
    log.info("Download: %s", url)
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    # MIMIT usa latin-1
    return r.content.decode("latin-1", errors="replace")

def detect_sep(text: str) -> str:
    first = text.split("\n")[0]
    return "|" if "|" in first else ";"

# ── Parse ────────────────────────────────────────────────────────────
def parse_anagrafica(text: str) -> pd.DataFrame:
    sep = detect_sep(text)
    log.info("Anagrafica sep='%s'", sep)

    df = pd.read_csv(
        StringIO(text), sep=sep, dtype=str,
        on_bad_lines="skip", encoding_errors="replace"
    )
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Mappa nomi colonne (il MIMIT ha cambiato nomi nel tempo)
    rename = {}
    for col in df.columns:
        if "idimpianto" in col or col == "id": rename[col] = "id"
        elif "gestore"   in col:               rename[col] = "gestore"
        elif "bandiera"  in col:               rename[col] = "bandiera"
        elif "tipo"      in col:               rename[col] = "tipo"
        elif "nome"      in col:               rename[col] = "nome"
        elif "indirizzo" in col:               rename[col] = "indirizzo"
        elif "comune"    in col:               rename[col] = "comune"
        elif "provincia" in col:               rename[col] = "prov"
        elif "latit"     in col:               rename[col] = "lat"
        elif "longit"    in col:               rename[col] = "lon"
    df = df.rename(columns=rename)

    for c in ["id", "lat", "lon"]:
        if c not in df.columns:
            log.error("Colonna '%s' non trovata. Colonne: %s", c, list(df.columns))
            sys.exit(1)

    df["lat"] = pd.to_numeric(df["lat"].str.replace(",", "."), errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"].str.replace(",", "."), errors="coerce")
    df["id"]  = pd.to_numeric(df["id"], errors="coerce")
    df = df.dropna(subset=["id", "lat", "lon"])
    df["id"] = df["id"].astype(int)

    # Filtra per raggio
    df["dist"] = df.apply(lambda r: dist_km(CENTRO_LAT, CENTRO_LON, r["lat"], r["lon"]), axis=1)
    df = df[df["dist"] <= RAGGIO_KM].copy()
    log.info("Impianti nel raggio %.0f km: %d", RAGGIO_KM, len(df))
    return df

def parse_prezzi(text: str, id_set: set) -> pd.DataFrame:
    sep = detect_sep(text)
    log.info("Prezzi sep='%s'", sep)

    df = pd.read_csv(
        StringIO(text), sep=sep, dtype=str,
        on_bad_lines="skip", encoding_errors="replace"
    )
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    rename = {}
    for col in df.columns:
        if "idimpianto" in col or col == "id": rename[col] = "id"
        elif "desccarb" in col or "carb" in col: rename[col] = "carburante"
        elif "prezzo"   in col:                  rename[col] = "prezzo"
        elif "self"     in col:                  rename[col] = "self_service"
        elif "dtcomu"   in col or "data" in col: rename[col] = "data"
    df = df.rename(columns=rename)

    df["id"]     = pd.to_numeric(df.get("id", pd.Series()), errors="coerce")
    df["prezzo"] = pd.to_numeric(df.get("prezzo", pd.Series()).astype(str).str.replace(",", "."), errors="coerce")
    df["self"]   = pd.to_numeric(df.get("self_service", pd.Series(dtype=int)), errors="coerce").fillna(1).astype(int)

    df = df.dropna(subset=["id", "prezzo"])
    df["id"] = df["id"].astype(int)
    df = df[df["id"].isin(id_set)]
    log.info("Righe prezzi per impianti nel raggio: %d", len(df))
    return df

# ── Build output ─────────────────────────────────────────────────────
def build_output(df_imp: pd.DataFrame, df_prz: pd.DataFrame) -> dict:
    prezzi_map: dict[int, dict] = {}
    for _, row in df_prz.iterrows():
        iid  = int(row["id"])
        carb = str(row.get("carburante", "")).strip()
        tipo = "self" if int(row.get("self", 1)) else "serv"
        key  = f"{carb}_{tipo}"
        prezzi_map.setdefault(iid, {})[key] = round(float(row["prezzo"]), 3)

    impianti = []
    for _, row in df_imp.iterrows():
        iid = int(row["id"])
        prz = prezzi_map.get(iid, {})
        if not prz:
            continue  # skip impianti senza prezzi comunicati oggi
        impianti.append({
            "id":       iid,
            "gestore":  str(row.get("gestore", "")).strip(),
            "bandiera": str(row.get("bandiera", "")).strip(),
            "nome":     str(row.get("nome", row.get("gestore", ""))).strip(),
            "indirizzo":str(row.get("indirizzo", "")).strip(),
            "comune":   str(row.get("comune", "")).strip(),
            "prov":     str(row.get("prov", "")).strip(),
            "lat":      round(float(row["lat"]), 6),
            "lon":      round(float(row["lon"]), 6),
            "dist":     float(row["dist"]),
            "prezzi":   prz,
        })

    # Ordina per diesel self
    impianti.sort(key=lambda x: x["prezzi"].get("Gasolio_self", 99))

    now = datetime.now(timezone.utc).isoformat()
    return {
        "aggiornato": now,
        "centro": {"lat": CENTRO_LAT, "lon": CENTRO_LON, "nome": "Mareno di Piave (TV)", "raggio_km": RAGGIO_KM},
        "impianti": impianti,
        "meta": {
            "totale_impianti": len(impianti),
            "fonte": "MIMIT Osservaprezzi Carburanti",
            "licenza": "IODL 2.0",
            "sep_csv": "pipe | (dal 10/02/2026)",
        }
    }

# ── Statistiche ──────────────────────────────────────────────────────
def stats(impianti: list, carb: str = "Gasolio", tipo: str = "self") -> dict:
    key = f"{carb}_{tipo}"
    prezzi = [i["prezzi"][key] for i in impianti if key in i["prezzi"]]
    if not prezzi:
        return {}
    prezzi.sort()
    return {
        "min": prezzi[0],
        "max": prezzi[-1],
        "media": round(sum(prezzi) / len(prezzi), 3),
        "n": len(prezzi),
        "best": next((i for i in impianti if i["prezzi"].get(key) == prezzi[0]), None),
    }

# ── Telegram ─────────────────────────────────────────────────────────
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram non configurato, skip notifica")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }, timeout=10)
    if r.ok:
        log.info("Notifica Telegram inviata ✓")
    else:
        log.error("Telegram error: %s", r.text)

def build_telegram_message(data: dict, st: dict, soglia: float | None) -> str | None:
    now_it = datetime.now().strftime("%d/%m/%Y %H:%M")
    impianti = data["impianti"]
    best = st.get("best")

    # Decidi se mandare
    if soglia is not None:
        if st.get("min", 99) >= soglia:
            log.info("Prezzo min %.3f >= soglia %.3f, nessuna notifica", st.get("min", 99), soglia)
            return None

    lines = [
        f"⛽ <b>FUEL RADAR</b> — {now_it}",
        f"📍 Mareno di Piave · raggio {RAGGIO_KM:.0f} km",
        "",
        f"<b>Gasolio self service oggi:</b>",
        f"  🟢 Min:  <b>€ {st['min']:.3f}</b> → {best['nome']} ({best['comune']}, {best['dist']:.1f} km)" if best else "",
        f"  🔴 Max:  € {st['max']:.3f}",
        f"  ⚖️ Media: € {st['media']:.3f}",
        f"  💰 Risparmio pieno 60L: € {((st['max']-st['min'])*60):.2f}",
    ]

    if soglia:
        lines.append(f"\n🔔 <i>Soglia alert: &lt; € {soglia:.2f}</i>")

    # Top 5
    lines.append("\n<b>Top 5 più economici:</b>")
    key = "Gasolio_self"
    top5 = [i for i in impianti if key in i["prezzi"]][:5]
    for i, imp in enumerate(top5, 1):
        lines.append(f"  {i}. {imp['nome']} ({imp['comune']}) — <b>€ {imp['prezzi'][key]:.3f}</b> · {imp['dist']:.1f}km")

    lines.append(f"\n<a href='https://TUOUSERNAME.github.io/fuel-radar/'>🔗 Apri dashboard</a>")
    return "\n".join(l for l in lines if l is not None)

# ── Main ─────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    log.info("=== FUEL RADAR — processo MIMIT ===")

    # 1. Download
    try:
        ana_text = fetch_csv(MIMIT_ANAGRAFICA)
        prz_text = fetch_csv(MIMIT_PREZZI)
    except Exception as e:
        log.error("Download fallito: %s", e)
        sys.exit(1)

    # 2. Parse
    df_imp = parse_anagrafica(ana_text)
    if df_imp.empty:
        log.error("Nessun impianto trovato nel raggio")
        sys.exit(1)

    id_set = set(df_imp["id"].tolist())
    df_prz = parse_prezzi(prz_text, id_set)

    # 3. Build JSON
    data = build_output(df_imp, df_prz)
    n = len(data["impianti"])
    log.info("Impianti con prezzi oggi: %d", n)

    # 4. Salva JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("Salvato: %s (%.1f KB)", OUTPUT_FILE, os.path.getsize(OUTPUT_FILE)/1024)

    # Salva anche _headers per CORS su GitHub Pages
    headers_file = f"{OUTPUT_DIR}/_headers"
    with open(headers_file, "w") as f:
        f.write("/fuel-data.json\n  Access-Control-Allow-Origin: *\n")

    # 5. Statistiche + Telegram
    st = stats(data["impianti"])
    if not st:
        log.warning("Nessun prezzo Gasolio self trovato")
        return

    log.info("Gasolio self → min=%.3f max=%.3f media=%.3f (%d impianti)",
             st["min"], st["max"], st["media"], st["n"])

    msg = build_telegram_message(data, st, SOGLIA_DIESEL)
    if msg:
        send_telegram(msg)
    else:
        log.info("Nessuna notifica Telegram (prezzo sopra soglia)")

if __name__ == "__main__":
    main()
