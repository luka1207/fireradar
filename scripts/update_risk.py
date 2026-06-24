"""
ZjarrRadar — Worker i llogaritjes së rrezikut të zjarreve
============================================================
Ky skript xhiron periodikisht (çdo orë) nga GitHub Actions.
Hapat:
  1. Lexon zonat e Shqipërisë (data/zones.json)
  2. Tërheq motin aktual për çdo zonë (Open-Meteo API — falas, pa key)
  3. Tërheq zjarret aktive nga NASA FIRMS (satelitor, live)
  4. Llogarit një "risk score" 0-100 për çdo zonë
  5. Ruan rezultatin në Upstash Redis (REST API) që frontend-i e lexon live

Mjedisi i nevojshëm (Secrets në GitHub Actions):
  UPSTASH_REDIS_REST_URL
  UPSTASH_REDIS_REST_TOKEN
  NASA_FIRMS_MAP_KEY   (falas nga https://firms.modaps.eosdis.nasa.gov/api/map_key/)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ----------------------------------------------------------------------
# KONFIGURIMI
# ----------------------------------------------------------------------

ZONES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "zones.json")

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
NASA_FIRMS_KEY = os.environ.get("NASA_FIRMS_MAP_KEY", "")

# Bounding box i Shqipërisë (për NASA FIRMS)
ALBANIA_BBOX = "19.0,39.6,21.1,42.7"  # min_lon,min_lat,max_lon,max_lat

# Pragjet e thatësisë / nxehtësisë (mund të kalibrohen me kohë)
TEMP_HIGH_RISK = 30.0       # °C — mbi këtë, rrezik i lartë nga temp
TEMP_MODERATE_RISK = 24.0   # °C
HUMIDITY_LOW_RISK = 30.0    # % — nën këtë, ajri shumë i thatë
DRY_DAYS_THRESHOLD = 5      # ditë pa shi që rrit rrezikun ndjeshëm


# ----------------------------------------------------------------------
# HAP 1 — LEXIMI I ZONAVE
# ----------------------------------------------------------------------

def load_zones():
    with open(ZONES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------
# HAP 2 — MOTI (Open-Meteo, falas, pa API key)
# ----------------------------------------------------------------------

def fetch_weather(lat, lon):
    """
    Merr temperaturën aktuale, lagështinë, erën, dhe llogarit
    sa ditë rresht nuk ka rënë shi (proxy për thatësinë e bimësisë).
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "daily": "precipitation_sum",
        "past_days": 10,
        "timezone": "Europe/Tirane",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    current = data.get("current", {})
    daily = data.get("daily", {})
    precip = daily.get("precipitation_sum", [])

    # Numëro ditët rresht (nga më e fundmja prapa) me shi ~0
    dry_days = 0
    for val in reversed(precip):
        if val is not None and val < 1.0:  # < 1mm konsiderohet "pa shi"
            dry_days += 1
        else:
            break

    return {
        "temperature": current.get("temperature_2m"),
        "humidity": current.get("relative_humidity_2m"),
        "wind_speed": current.get("wind_speed_10m"),
        "dry_days": dry_days,
    }


# ----------------------------------------------------------------------
# HAP 3 — ZJARRE AKTIVE NGA SATELITI (NASA FIRMS)
# ----------------------------------------------------------------------

def fetch_active_fires():
    """
    Kthen listën e zjarreve aktive të detektuara nga sateliti
    në 24 orët e fundit, brenda Shqipërisë.
    Nëse NASA_FIRMS_KEY mungon, kthen listë bosh (sistemi vazhdon
    të punojë vetëm me të dhëna moti).
    """
    if not NASA_FIRMS_KEY:
        print("⚠️  NASA_FIRMS_MAP_KEY mungon — duke vazhduar pa zjarre aktive satelitore.")
        return []

    url = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{NASA_FIRMS_KEY}/VIIRS_SNPP_NRT/{ALBANIA_BBOX}/1"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        if len(lines) <= 1:
            return []
        header = lines[0].split(",")
        lat_idx = header.index("latitude")
        lon_idx = header.index("longitude")
        fires = []
        for line in lines[1:]:
            parts = line.split(",")
            fires.append({"lat": float(parts[lat_idx]), "lon": float(parts[lon_idx])})
        return fires
    except Exception as e:
        print(f"⚠️  Gabim duke marrë NASA FIRMS: {e}")
        return []


def distance_km(lat1, lon1, lat2, lon2):
    """Distancë e thjeshtuar (jo Haversine i plotë, mjafton për proximitet të përafërt)."""
    return ((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) ** 0.5 * 111  # ~111km/gradë


def count_nearby_fires(zone, fires, radius_km=25):
    return sum(
        1 for f in fires
        if distance_km(zone["lat"], zone["lon"], f["lat"], f["lon"]) <= radius_km
    )


# ----------------------------------------------------------------------
# HAP 4 — LLOGARITJA E SCORE-IT TË RREZIKUT
# ----------------------------------------------------------------------

def calculate_risk_score(zone, weather, nearby_fires_count, historical_score):
    """
    Kombinon faktorët në një score 0-100.
    Pesha (weights) janë konfigurim fillestar — duhen kalibruar
    me të dhëna reale historike kur t'i kesh.
    """
    score = 0.0

    temp = weather.get("temperature")
    humidity = weather.get("humidity")
    dry_days = weather.get("dry_days", 0)

    # --- Faktori i temperaturës (max 25 pikë) ---
    if temp is not None:
        if temp >= TEMP_HIGH_RISK:
            score += 25
        elif temp >= TEMP_MODERATE_RISK:
            score += 15 * ((temp - TEMP_MODERATE_RISK) / (TEMP_HIGH_RISK - TEMP_MODERATE_RISK))
            score += 10  # bazë për "moderate"

    # --- Faktori i lagështisë (max 20 pikë) — sa më e ulët, aq më keq ---
    if humidity is not None:
        if humidity <= HUMIDITY_LOW_RISK:
            score += 20
        elif humidity <= 50:
            score += 20 * ((50 - humidity) / (50 - HUMIDITY_LOW_RISK))

    # --- Faktori i ditëve të thata rresht (max 20 pikë) ---
    score += min(dry_days / DRY_DAYS_THRESHOLD, 1.0) * 20

    # --- Faktori i erës (max 10 pikë) — era e fortë përshpejton përhapjen ---
    wind = weather.get("wind_speed") or 0
    score += min(wind / 40, 1.0) * 10  # 40 km/h = erë e fortë

    # --- Faktori i historisë / zjarreve aktive aktualisht pranë (max 15 pikë) ---
    score += min(nearby_fires_count * 5, 15)

    # --- Faktori antropogjenik: kullotë + bar i thatë (max 10 pikë) ---
    # Rrezik shtesë nëse zona ka kullotë AKTIVE dhe bari është tashmë i thatë
    # (dry_days i lartë = bari tashmë i thatë = kushte "ideale" për djegie qëllimore)
    if zone.get("has_grazing") and dry_days >= 3:
        score += 10

    # --- Bonus nga historiku i regjistruar (nëse e ke ndërtuar bazën historike) ---
    score += historical_score or 0

    return round(min(score, 100), 1)


def risk_label(score):
    if score >= 70:
        return "KRITIK"
    elif score >= 45:
        return "I LARTË"
    elif score >= 20:
        return "MODERUAR"
    else:
        return "I ULËT"


# ----------------------------------------------------------------------
# HAP 5 — RUAJTJA NË UPSTASH REDIS (REST API)
# ----------------------------------------------------------------------

def redis_set(key, value):
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        print(f"⚠️  Upstash nuk është konfiguruar — do printoj në vend që ta ruaj: {key}")
        print(value)
        return
    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
    resp = requests.post(
        f"{UPSTASH_URL}/set/{key}",
        headers=headers,
        data=json.dumps(value),
        timeout=10,
    )
    resp.raise_for_status()


def redis_get_historical(zone_id):
    """
    Lexon (nëse ekziston) një score historik manual për zonën,
    p.sh. vendosur paraprakisht bazuar në të dhëna nga zjarrfikëset lokale.
    Kthen 0 nëse nuk ka.
    """
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return 0
    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
    try:
        resp = requests.get(
            f"{UPSTASH_URL}/get/historical:{zone_id}",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json().get("result")
        if result:
            return float(json.loads(result))
        return 0
    except Exception:
        return 0


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    print(f"🔥 ZjarrRadar — fillimi i ciklit të përditësimit: {datetime.now(timezone.utc).isoformat()}")

    zones = load_zones()
    fires = fetch_active_fires()
    print(f"   → {len(fires)} zjarre aktive të detektuara nga sateliti (24h e fundit)")

    all_results = []

    for zone in zones:
        try:
            weather = fetch_weather(zone["lat"], zone["lon"])
        except Exception as e:
            print(f"⚠️  Gabim moti për {zone['name']}: {e}")
            continue

        nearby = count_nearby_fires(zone, fires)
        historical = redis_get_historical(zone["id"])
        score = calculate_risk_score(zone, weather, nearby, historical)
        label = risk_label(score)

        result = {
            "zone_id": zone["id"],
            "name": zone["name"],
            "lat": zone["lat"],
            "lon": zone["lon"],
            "region": zone["region"],
            "score": score,
            "label": label,
            "weather": weather,
            "nearby_active_fires": nearby,
            "has_grazing": zone.get("has_grazing", False),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        redis_set(f"risk:{zone['id']}", result)
        all_results.append(result)

        print(f"   ✓ {zone['name']:15s} | score={score:5.1f} | {label:10s} | temp={weather.get('temperature')}°C | lagështi={weather.get('humidity')}%")
        time.sleep(0.3)  # mos e mbingarko API-n falas

    # Ruaj edhe një snapshot të plotë (më e shpejtë për frontend ta marrë krejt njëherësh)
    redis_set("risk:all", {
        "zones": all_results,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_active_fires": len(fires),
    })

    print(f"✅ Përditësimi përfundoi. {len(all_results)} zona të përditësuara.")


if __name__ == "__main__":
    main()
