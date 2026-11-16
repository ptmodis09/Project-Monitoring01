"""Cek spreadsheet PROJECT ID dan catat setiap perubahan ke data/changelog.json.

Dijalankan oleh GitHub Actions (.github/workflows/sheet-log.yml) tiap ~5 menit.
- data/snapshot.json  : isi spreadsheet terakhir yang tercatat
- data/changelog.json : riwayat perubahan permanen (hanya bertambah, tidak pernah dihapus)
- data/status.json    : kapan terakhir dicek
Hanya memakai library bawaan Python.
"""
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

CSV_URL = os.environ.get(
    "CSV_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vSR0TDCfx8VsqKZMz2Q9iB1Rg6LDZFA74tkih9Ain-fd8PmAH_OG2VQVue0vyDiDA/pub?output=csv",
)
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
SNAP = os.path.join(DATA, "snapshot.json")
LOG = os.path.join(DATA, "changelog.json")
STATUS = os.path.join(DATA, "status.json")
FIELDS = ["pic", "client", "owner", "year", "name", "site", "type", "remarks", "status"]
ALIAS = {"TSEL": "TELKOMSEL"}


def clean(v):
    return re.sub(r"[ \s]+", " ", str(v or "")).strip()


def fetch_rows():
    url = CSV_URL + ("&" if "?" in CSV_URL else "?") + "_=" + str(int(datetime.now().timestamp()))
    req = urllib.request.Request(url, headers={"User-Agent": "sheet-log/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def to_records(grid):
    h, H = -1, []
    for i, row in enumerate(grid[:40]):
        up = [clean(x).upper() for x in row]
        if "PROJECT ID" in up and "PIC" in up:
            h, H = i, up
            break
    if h < 0:
        raise RuntimeError("Header 'PROJECT ID' tidak ditemukan")
    names = {"no": "NO.", "id": "PROJECT ID", "pic": "PIC", "client": "CLIENT", "owner": "OWNER", "year": "TAHUN",
             "name": "PROJECT NAME", "site": "SITE", "type": "PROJECT TYPE", "remarks": "REMARKS", "status": "PROJECT STATUS"}
    I = {k: (H.index(v) if v in H else -1) for k, v in names.items()}

    def g(r, k, off=0):
        j = I[k] + off
        return clean(r[j]) if I[k] >= 0 and j < len(r) else ""

    out, seen = {}, {}
    for r in grid[h + 1:]:
        pid = g(r, "id")
        if not pid:
            continue
        st = (g(r, "status") or g(r, "status", 1)).upper() or "TANPA STATUS"
        yd = re.sub(r"\D", "", g(r, "year"))
        cl, ow = g(r, "client").upper(), g(r, "owner").upper()
        rec = {
            "pic": g(r, "pic").upper() or "—", "client": ALIAS.get(cl, cl) or "—", "owner": ALIAS.get(ow, ow) or "—",
            "year": yd if len(yd) == 4 else (g(r, "year") or "—"), "name": g(r, "name"), "site": g(r, "site") or "—",
            "type": g(r, "type") or "—", "remarks": g(r, "remarks"), "status": st,
        }
        key = pid
        if key in seen:  # Project ID kembar: beri akhiran agar tetap terlacak
            seen[pid] += 1
            key = f"{pid} #{seen[pid]}"
        else:
            seen[pid] = 1
        out[key] = rec
    return out


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def main():
    os.makedirs(DATA, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        new = to_records(fetch_rows())
    except Exception as e:  # gagal ambil data: jangan catat apa pun
        print("Gagal membaca spreadsheet:", e)
        return 0
    if not new:
        print("Spreadsheet kosong, dilewati.")
        return 0

    old = load(SNAP, None)
    log = load(LOG, [])
    events = []
    if old is None:
        events.append({"ts": now, "kind": "init", "id": "", "name": "",
                       "detail": f"Pencatatan dimulai dengan {len(new)} proyek"})
    else:
        if len(new) < 0.5 * len(old):
            print(f"Jumlah baris turun drastis ({len(old)} -> {len(new)}), kemungkinan data belum lengkap. Dilewati.")
            return 0
        for pid, r in new.items():
            p = old.get(pid)
            if p is None:
                events.append({"ts": now, "kind": "added", "id": pid, "name": r["name"], "status": r["status"]})
                continue
            ch = [{"field": f, "from": p.get(f, ""), "to": r[f]} for f in FIELDS if p.get(f, "") != r[f]]
            if ch:
                kind = "status" if any(c["field"] == "status" for c in ch) else "edited"
                events.append({"ts": now, "kind": kind, "id": pid, "name": r["name"], "changes": ch})
        for pid, p in old.items():
            if pid not in new:
                events.append({"ts": now, "kind": "removed", "id": pid, "name": p.get("name", ""), "status": p.get("status", "")})

    status = load(STATUS, {})
    status_changed = status.get("lastChecked", "")[:10] != now[:10]  # perbarui heartbeat sekali sehari
    if events:
        log.extend(events)
        save(LOG, log)
        save(SNAP, new)
        status_changed = True
        print(f"{len(events)} perubahan dicatat.")
    else:
        print("Tidak ada perubahan.")
    if status_changed:
        status.update({"lastChecked": now, "projects": len(new), "events": len(log)})
        if events:
            status["lastChange"] = now
        save(STATUS, status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
