#!/usr/bin/env python3
"""
ดึงข้อมูลสถานการณ์น้ำจังหวัดกาญจนบุรี แล้วเขียนเป็นไฟล์ JSON แบบ static ให้หน้าแผนที่อ่าน

แหล่งข้อมูล
  - ThaiWater (สสน.)  : ระดับน้ำ, ฝน 24 ชม., เขื่อน   — ไม่ต้องใช้ key
  - GISTDA API Gateway : ขอบเขตน้ำท่วมจากดาวเทียม        — ต้องใช้ GISTDA_API_KEY

ใช้แค่ standard library (ไม่ต้อง pip install) เพื่อให้รันใน GitHub Actions ได้ทันที
ทุกแหล่งทำงานแยกกัน ถ้าแหล่งไหนล่ม แหล่งอื่นยังอัปเดตได้ และข้อมูลชุดเก่าจะถูกเก็บไว้พร้อมสถานะ "stale"
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TH = timezone(timedelta(hours=7))

PROVINCE_TH = "กาญจนบุรี"
PROVINCE_CODE = "71"

TW_BASE = os.environ.get("THAIWATER_BASE", "https://api-v3.thaiwater.net/api/v1/thaiwater30").rstrip("/")
TW_WATERLEVEL = os.environ.get("THAIWATER_WATERLEVEL_PATH", "public/waterlevel_load")
TW_RAIN = os.environ.get("THAIWATER_RAIN_PATH", "public/rain_24h")
# endpoint เขื่อนยังไม่ยืนยันกับข้อมูลจริง ใส่ได้หลายตัวคั่นด้วยจุลภาค ระบบจะลองทีละตัว
TW_DAM = os.environ.get("THAIWATER_DAM_PATHS", "analyst/dam,public/dam_load")

GISTDA_BASE = os.environ.get("GISTDA_BASE", "https://api-gateway.gistda.or.th/api/2.0/resources").rstrip("/")
GISTDA_KEY = os.environ.get("GISTDA_API_KEY", "").strip()
GISTDA_PERIODS = [p.strip() for p in os.environ.get("GISTDA_PERIODS", "1day,3days,7days").split(",") if p.strip()]

HISTORY_POINTS = int(os.environ.get("HISTORY_POINTS", "96"))  # 96 จุด x 30 นาที = 48 ชม.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36 kanchanaburi-flood-map")


# ---------------------------------------------------------------- HTTP
def http_json(url, headers=None, timeout=60, retries=2):
    h = {"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"}
    if "thaiwater" in url:
        h["Referer"] = "https://www.thaiwater.net/"
    h.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                break
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(redact(f"{type(last).__name__}: {last}"))


def redact(text):
    if GISTDA_KEY:
        text = str(text).replace(GISTDA_KEY, "***")
    return text


# ---------------------------------------------------------------- geometry
def load_boundary():
    with open(os.path.join(DATA, "boundary.geojson"), encoding="utf-8") as f:
        fc = json.load(f)
    province = next(ft for ft in fc["features"] if ft["properties"]["kind"] == "province")
    districts = [ft for ft in fc["features"] if ft["properties"]["kind"] == "district"]
    return province, districts


def _rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def point_in(geom, x, y):
    for poly in _rings(geom):
        if poly and _in_ring(x, y, poly[0]) and not any(_in_ring(x, y, h) for h in poly[1:]):
            return True
    return False


def bbox(geom):
    xs, ys = [], []
    for poly in _rings(geom):
        for ring in poly:
            for c in ring:
                xs.append(c[0]); ys.append(c[1])
    return min(xs), min(ys), max(xs), max(ys)


def area_rai(geom):
    """พื้นที่โดยประมาณ (ไร่) ด้วย equirectangular projection — แม่นพอสำหรับเซลล์ขนาดเล็ก"""
    import math
    total = 0.0
    for poly in _rings(geom):
        for k, ring in enumerate(poly):
            lat0 = math.radians(sum(c[1] for c in ring) / len(ring))
            kx, ky = 111320 * math.cos(lat0), 110540
            a = 0.0
            for i in range(len(ring) - 1):
                x1, y1 = ring[i][0] * kx, ring[i][1] * ky
                x2, y2 = ring[i + 1][0] * kx, ring[i + 1][1] * ky
                a += x1 * y2 - x2 * y1
            total += abs(a) / 2 * (1 if k == 0 else -1)
    return total / 1600.0


def district_of(districts, x, y):
    for d in districts:
        if point_in(d["geometry"], x, y):
            return d["properties"]["name"]
    return None


# ---------------------------------------------------------------- tolerant field access
def flatten(obj, prefix="", out=None):
    """{'station': {'name': {'th': 'x'}}} -> {'station.name.th': 'x'}"""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten(v, f"{prefix}{k}.", out)
    elif not isinstance(obj, list):
        out[prefix[:-1]] = obj
    return out


def pick(flat, *candidates):
    """คืนค่าตัวแรกที่เจอ รองรับทั้ง path เต็ม และท้าย path (suffix)"""
    for c in candidates:
        if c in flat and flat[c] not in (None, ""):
            return flat[c]
    for c in candidates:
        for k, v in flat.items():
            if (k.endswith("." + c) or k == c) and v not in (None, ""):
                return v
    return None


def num(v):
    try:
        if v is None or v == "":
            return None
        f = float(str(v).replace(",", ""))
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def find_records(payload):
    """หา list ของ record ที่ใหญ่ที่สุดใน payload (ThaiWater ห่อข้อมูลหลายชั้นไม่เหมือนกันในแต่ละ endpoint)"""
    best = []

    def walk(o, depth=0):
        nonlocal best
        if depth > 6:
            return
        if isinstance(o, list):
            dicts = [x for x in o if isinstance(x, dict)]
            if len(dicts) > len(best):
                best = dicts
            for x in o[:3]:
                walk(x, depth + 1)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v, depth + 1)

    walk(payload)
    return best


def to_iso(v):
    if not v:
        return None
    s = str(v).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=TH).isoformat()
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=TH)).isoformat()
    except ValueError:
        return None


def coords_of(flat):
    lat = num(pick(flat, "tele_station_lat", "station_lat", "dam_lat", "lat", "latitude"))
    lon = num(pick(flat, "tele_station_long", "station_long", "dam_long", "long", "lng", "lon", "longitude"))
    if lat is None or lon is None:
        return None
    if 90 < abs(lat) and abs(lon) <= 90:  # บางแหล่งสลับ
        lat, lon = lon, lat
    return lon, lat


def in_province(flat, province_geom, xy):
    pname = pick(flat, "province_name.th", "geocode.province_name.th", "province_name", "province")
    pcode = pick(flat, "province_code", "geocode.province_code")
    if pname and PROVINCE_TH in str(pname):
        return True
    if pcode and str(pcode).zfill(2) == PROVINCE_CODE:
        return True
    return bool(xy) and point_in(province_geom, *xy)


# ---------------------------------------------------------------- level classification
# เกณฑ์ตาม สสน.: เทียบระดับน้ำกับความจุลำน้ำ (% ของระดับตลิ่ง)
def level_from_percent(p):
    if p is None:
        return None
    if p > 100: return 5   # ล้นตลิ่ง
    if p > 70:  return 4   # น้ำมาก
    if p > 30:  return 3   # ปกติ
    if p > 10:  return 2   # น้ำน้อย
    return 1               # น้อยวิกฤต


# ---------------------------------------------------------------- ThaiWater
def fetch_waterlevel(province, districts):
    payload = http_json(f"{TW_BASE}/{TW_WATERLEVEL}")
    rows = find_records(payload)
    feats = []
    for r in rows:
        f = flatten(r)
        xy = coords_of(f)
        if not xy or not in_province(f, province["geometry"], xy):
            continue
        wl = num(pick(f, "waterlevel_msl", "waterlevel_m", "waterlevel"))
        bank = num(pick(f, "min_bank", "station.min_bank", "left_bank", "ground_level"))
        pct = num(pick(f, "storage_percent", "waterlevel_percent", "bank_percent"))
        if pct is None and wl is not None and bank:
            bed = num(pick(f, "min_bed", "station.min_bed", "ground_level_bed"))
            if bed is not None and bank > bed:
                pct = (wl - bed) / (bank - bed) * 100
        lvl = num(pick(f, "situation_level"))
        lvl = int(lvl) if lvl is not None else level_from_percent(pct)
        code = pick(f, "tele_station_oldcode", "station.tele_station_oldcode", "station_code", "tele_station_code", "id")
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(xy[0], 5), round(xy[1], 5)]},
            "properties": {
                "id": str(code or f"{xy[0]:.4f},{xy[1]:.4f}"),
                "name": pick(f, "tele_station_name.th", "station_name.th", "station.tele_station_name.th", "station_name", "name.th", "name"),
                "district": pick(f, "amphoe_name.th", "geocode.amphoe_name.th") or district_of(districts, *xy),
                "tambon": pick(f, "tumbon_name.th", "geocode.tumbon_name.th"),
                "river": pick(f, "river_name.th", "river_name", "subbasin_name.th"),
                "agency": pick(f, "agency_shortname.th", "agency_name.th", "agency.agency_name.th"),
                "waterlevel_msl": wl,
                "bank_msl": bank,
                "percent": round(pct, 1) if pct is not None else None,
                "level": lvl,
                "observed_at": to_iso(pick(f, "waterlevel_datetime", "datetime", "date")),
            },
        })
    return feats, len(rows)


def fetch_rain(province, districts):
    payload = http_json(f"{TW_BASE}/{TW_RAIN}")
    rows = find_records(payload)
    feats = []
    for r in rows:
        f = flatten(r)
        xy = coords_of(f)
        if not xy or not in_province(f, province["geometry"], xy):
            continue
        mm = num(pick(f, "rain_24h", "rainfall24h", "rainfall_24h", "rain_value", "rainfall"))
        if mm is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(xy[0], 5), round(xy[1], 5)]},
            "properties": {
                "name": pick(f, "tele_station_name.th", "station_name.th", "station_name", "name"),
                "district": pick(f, "amphoe_name.th") or district_of(districts, *xy),
                "agency": pick(f, "agency_shortname.th", "agency_name.th"),
                "rain_24h": round(mm, 1),
                "observed_at": to_iso(pick(f, "rainfall_datetime", "rain_datetime", "datetime", "date")),
            },
        })
    return feats, len(rows)


def fetch_dams(province, districts):
    errors = []
    for path in [p.strip() for p in TW_DAM.split(",") if p.strip()]:
        try:
            payload = http_json(f"{TW_BASE}/{path}")
        except RuntimeError as e:
            errors.append(f"{path}: {e}")
            continue
        rows = find_records(payload)
        feats = []
        for r in rows:
            f = flatten(r)
            xy = coords_of(f)
            if not xy or not in_province(f, province["geometry"], xy):
                continue
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(xy[0], 5), round(xy[1], 5)]},
                "properties": {
                    "name": pick(f, "dam_name.th", "dam.dam_name.th", "dam_name", "name.th", "name"),
                    "storage_percent": num(pick(f, "dam_storage_percent", "storage_percent")),
                    "storage_mcm": num(pick(f, "dam_storage", "storage")),
                    "uses_percent": num(pick(f, "dam_uses_water_percent", "uses_water_percent")),
                    "inflow_mcm": num(pick(f, "dam_inflow", "inflow")),
                    "release_mcm": num(pick(f, "dam_released", "released", "release")),
                    "level_msl": num(pick(f, "dam_level", "water_level")),
                    "observed_at": to_iso(pick(f, "dam_date", "date", "datetime")),
                },
            })
        if rows:
            return feats, len(rows), path
        errors.append(f"{path}: ไม่พบรายการข้อมูลใน response")
    raise RuntimeError(" | ".join(errors) or "ไม่ได้ตั้งค่า endpoint เขื่อน")


# ---------------------------------------------------------------- GISTDA
ACQ_RE = re.compile(r"(\d{8})_(\d{4})")


def fetch_gistda(period, districts):
    if not GISTDA_KEY:
        raise RuntimeError("ยังไม่ได้ตั้งค่า GISTDA_API_KEY")
    feats, offset, limit = [], 0, 1000
    for _ in range(60):  # กันลูปไม่รู้จบ (สูงสุด 60,000 เซลล์)
        q = urllib.parse.urlencode({"pv_idn": PROVINCE_CODE, "limit": limit, "offset": offset})
        payload = http_json(f"{GISTDA_BASE}/features/flood/{period}?{q}", headers={"API-Key": GISTDA_KEY}, timeout=90)
        batch = payload.get("features") if isinstance(payload, dict) else None
        if batch is None:
            batch = [x for x in find_records(payload) if "geometry" in x]
        for ft in batch:
            geom = ft.get("geometry")
            if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            props = ft.get("properties") or {}
            m = ACQ_RE.search(str(props.get("file_name", "")))
            acq = None
            if m:
                acq = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M").replace(tzinfo=TH).isoformat()
            ring = _rings(geom)[0][0]
            cx = sum(c[0] for c in ring) / len(ring)
            cy = sum(c[1] for c in ring) / len(ring)
            feats.append({
                "type": "Feature",
                "geometry": round_geom(geom),
                "properties": {
                    "district": props.get("ap_tn") or props.get("amphoe_t") or district_of(districts, cx, cy),
                    "tambon": props.get("tb_tn") or props.get("tambon_t"),
                    "area_rai": round(num(props.get("area_rai")) or area_rai(geom), 1),
                    "acquired_at": acq,
                },
            })
        if len(batch) < limit:
            break
        offset += limit
    return feats


def round_geom(g):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], list) else [round(c[0], 5), round(c[1], 5)]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


# ---------------------------------------------------------------- history & output
def read_json(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(name, obj):
    tmp = os.path.join(DATA, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(DATA, name))


def update_history(history, stations):
    for ft in stations:
        p = ft["properties"]
        if p["waterlevel_msl"] is None or not p["observed_at"]:
            continue
        series = history.setdefault(p["id"], [])
        if not series or series[-1][0] != p["observed_at"]:
            series.append([p["observed_at"], p["waterlevel_msl"]])
        del series[:-HISTORY_POINTS]
    return history


def main():
    now = datetime.now(TH).isoformat(timespec="seconds")
    province, districts = load_boundary()
    prev_status = read_json("status.json", {"sources": {}})
    status = {"generated_at": now, "sources": {}}
    stations = read_json("stations.json", {"waterlevel": [], "rain": [], "dams": []})

    def run(key, label, fn):
        try:
            result = fn()
            status["sources"][key] = {"label": label, "ok": True, "fetched_at": now, **result}
        except Exception as e:  # แหล่งเดียวล่มต้องไม่ทำให้ทั้งรอบล่ม
            old = prev_status.get("sources", {}).get(key, {})
            status["sources"][key] = {**old, "label": label, "ok": False, "error": redact(str(e))[:300], "last_attempt": now}
            print(f"[warn] {key}: {redact(e)}", file=sys.stderr)

    def do_waterlevel():
        feats, total = fetch_waterlevel(province, districts)
        if total and not feats:
            raise RuntimeError(f"ได้ {total} รายการแต่ไม่มีสถานีในกาญจนบุรี — โครงสร้างข้อมูลอาจเปลี่ยน")
        stations["waterlevel"] = feats
        return {"count": len(feats), "upstream_rows": total}

    def do_rain():
        feats, total = fetch_rain(province, districts)
        stations["rain"] = feats
        return {"count": len(feats), "upstream_rows": total}

    def do_dams():
        feats, total, path = fetch_dams(province, districts)
        stations["dams"] = feats
        return {"count": len(feats), "upstream_rows": total, "endpoint": path}

    run("waterlevel", "ระดับน้ำ (สสน.)", do_waterlevel)
    run("rain", "ฝน 24 ชม. (สสน.)", do_rain)
    run("dams", "เขื่อน (สสน.)", do_dams)
    write_json("stations.json", stations)

    history = update_history(read_json("history.json", {}), stations["waterlevel"])
    write_json("history.json", history)

    for period in GISTDA_PERIODS:
        def do_flood(period=period):
            feats = fetch_gistda(period, districts)
            write_json(f"flood_{period}.geojson", {"type": "FeatureCollection", "features": feats})
            by_district = {}
            for ft in feats:
                d = ft["properties"]["district"] or "ไม่ระบุ"
                a = ft["properties"]["area_rai"] or 0
                by_district[d] = round(by_district.get(d, 0) + a, 1)
            acq = sorted({ft["properties"]["acquired_at"] for ft in feats if ft["properties"]["acquired_at"]})
            return {"count": len(feats), "area_rai": round(sum(by_district.values()), 1),
                    "by_district": by_district, "acquired": acq[-6:]}
        run(f"flood_{period}", f"น้ำท่วมจากดาวเทียม {period} (GISTDA)", do_flood)

    write_json("status.json", status)
    ok = sum(1 for s in status["sources"].values() if s.get("ok"))
    print(f"done {now}: {ok}/{len(status['sources'])} sources ok")
    # ไม่ exit ด้วย error ถ้ายังมีบางแหล่งใช้ได้ เพื่อให้ workflow deploy ข้อมูลที่มีอยู่
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
