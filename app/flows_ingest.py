# -*- coding: utf-8 -*-
"""Nạp dòng tiền Khối ngoại vào bảng `market_flows` (dùng chung CLI + web).

NGUỒN THẬT: API khối ngoại của VNDirect (finfo). Một request theo NGÀY trả về số
liệu mua/bán/ròng của TOÀN BỘ mã trong phiên đó:
    GET https://api-finfo.vndirect.com.vn/v4/foreigns?q=tradingDate:YYYY-MM-DD&size=3000
Trường dùng: code, tradingDate, floor, buyVol/buyVal, sellVol/sellVal.

Hai đường nạp (cùng dùng upsert theo (ma_cp, ngay) để chống trùng):
  - normalize_rows(): chuẩn hóa các dòng do TRÌNH DUYỆT fetch rồi POST về (nút
    "Update" — chạy được trên PythonAnywhere free vì server không cần ra internet).
  - fetch_foreign_real(): server tự gọi VNDirect (dùng cho CLI ở máy có mạng / cron).

TỰ DOANH (td_*): hiện CHƯA có nguồn API free per-mã -> để NULL (không bịa số). UI
sẽ hiện "No data" cho khối Proprietary cho tới khi cắm được nguồn thật.
"""
import logging
import random
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

VNDIRECT_FOREIGN_URL = "https://api-finfo.vndirect.com.vn/v4/foreigns"

# Thứ tự cột cho câu UPSERT (khớp app/models.MARKET_FLOW_COLUMNS).
FIELDS = [
    "ngay", "ma_cp", "san",
    "kn_mua_kl", "kn_mua_gt", "kn_ban_kl", "kn_ban_gt",
    "td_mua_kl", "td_mua_gt", "td_ban_kl", "td_ban_gt",
]
# Các cột GIÁ TRỊ số (dùng khi chuẩn hóa dữ liệu POST từ trình duyệt).
_NUM_FIELDS = [f for f in FIELDS if f not in ("ngay", "ma_cp", "san")]

# Rổ mã CHỈ dùng cho chế độ --mock (giả lập khi phát triển, KHÔNG dùng ở production).
MOCK_UNIVERSE = [
    "FPT", "HPG", "SSI", "VCB", "MBB", "VND", "TCB", "PVT", "BSR", "GAS",
    "ACB", "DGC", "REE", "PLX", "VGI", "CTR", "GEX", "PET", "NLG", "VIC",
]


def _as_date(day):
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    return datetime.strptime(str(day)[:10], "%Y-%m-%d").date()


def trading_days(start, end):
    """Sinh các ngày GIAO DỊCH (bỏ Thứ 7/CN) trong [start, end] inclusive."""
    d, end = _as_date(start), _as_date(end)
    while d <= end:
        if d.weekday() < 5:      # 0=Thứ 2 ... 4=Thứ 6
            yield d
        d += timedelta(days=1)


def _to_float(v):
    """Ép về float; rỗng/None/sai kiểu -> None (giữ NULL trong DB)."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row_from_vnd(item):
    """Map 1 bản ghi VNDirect -> dict theo FIELDS (td_* để None vì chưa có nguồn)."""
    return {
        "ngay": str(item.get("tradingDate"))[:10],
        "ma_cp": (item.get("code") or "").strip().upper(),
        "san": item.get("floor"),
        "kn_mua_kl": _to_float(item.get("buyVol")),
        "kn_mua_gt": _to_float(item.get("buyVal")),
        "kn_ban_kl": _to_float(item.get("sellVol")),
        "kn_ban_gt": _to_float(item.get("sellVal")),
        "td_mua_kl": None, "td_mua_gt": None,
        "td_ban_kl": None, "td_ban_gt": None,
    }


def fetch_foreign_real(day, session=None):
    """Server tự gọi VNDirect lấy khối ngoại TOÀN THỊ TRƯỜNG cho 1 ngày.

    Chỉ dùng ở máy CÓ INTERNET (CLI/cron). Trên PythonAnywhere free server bị chặn
    ra ngoài -> dùng nút Update (client-side) thay thế. Ném lỗi nếu request hỏng
    (KHÔNG âm thầm trả số giả)."""
    import requests  # import cục bộ: chỉ CLI cần, tránh bắt buộc phụ thuộc khi chạy web
    iso = _as_date(day).isoformat()
    http = session or requests
    r = http.get(
        VNDIRECT_FOREIGN_URL,
        params={"q": f"tradingDate:{iso}", "size": 3000},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json().get("data", []) or []
    rows = []
    for x in data:
        # Chỉ lấy CỔ PHIẾU (bỏ CW/ETF...) và bản ghi có mã hợp lệ.
        if x.get("type") and x.get("type") != "STOCK":
            continue
        row = _row_from_vnd(x)
        if row["ma_cp"] and row["ngay"]:
            rows.append(row)
    return rows


def normalize_rows(raw_rows):
    """Chuẩn hóa danh sách dòng do TRÌNH DUYỆT gửi về (POST /market-flows/ingest).

    Chỉ giữ đúng các cột trong FIELDS, ép kiểu số an toàn, bỏ dòng thiếu (ma_cp,
    ngay). Trả list dict sẵn sàng cho upsert."""
    out = []
    for r in raw_rows or []:
        if not isinstance(r, dict):
            continue
        ma = (str(r.get("ma_cp") or "")).strip().upper()
        ngay = (str(r.get("ngay") or ""))[:10]
        if not ma or len(ngay) != 10:
            continue
        row = {"ngay": ngay, "ma_cp": ma, "san": r.get("san")}
        for f in _NUM_FIELDS:
            row[f] = _to_float(r.get(f))
        out.append(row)
    return out


def fetch_mock(day):
    """[CHỈ --mock] Sinh dữ liệu GIẢ LẬP để test giao diện. KHÔNG phải số thật."""
    rows = []
    iso = _as_date(day).isoformat()
    for sym in MOCK_UNIVERSE:
        rnd = random.Random(f"{iso}:{sym}")
        gia = rnd.uniform(15_000, 120_000)

        def leg():
            kl = rnd.randint(0, 2_000_000)
            return kl, round(kl * gia, 0)

        kn_mua_kl, kn_mua_gt = leg()
        kn_ban_kl, kn_ban_gt = leg()
        rows.append({
            "ngay": iso, "ma_cp": sym, "san": "HOSE",
            "kn_mua_kl": kn_mua_kl, "kn_mua_gt": kn_mua_gt,
            "kn_ban_kl": kn_ban_kl, "kn_ban_gt": kn_ban_gt,
            "td_mua_kl": None, "td_mua_gt": None,
            "td_ban_kl": None, "td_ban_gt": None,
        })
    return rows


def upsert(con, rows):
    """UPSERT theo (ma_cp, ngay). Trả số dòng ghi. Caller tự commit."""
    if not rows:
        return 0
    cols = ", ".join(FIELDS)
    placeholders = ", ".join(f":{f}" for f in FIELDS)
    updates = ", ".join(
        f"{f}=excluded.{f}" for f in FIELDS if f not in ("ma_cp", "ngay")
    )
    sql = (
        f"INSERT INTO market_flows ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(ma_cp, ngay) DO UPDATE SET {updates}"
    )
    con.executemany(sql, rows)
    return len(rows)


def ingest_range(con, start, end, source="real", commit_each_day=False):
    """[CLI] Nạp khối ngoại cho mọi phiên trong [start, end]. source: 'real'|'mock'.

    commit_each_day=True: lỗi giữa chừng vẫn giữ phần đã nạp (an toàn cho cron)."""
    import requests
    session = requests.Session() if source == "real" else None
    total = days = 0
    for day in trading_days(start, end):
        try:
            rows = fetch_mock(day) if source == "mock" else fetch_foreign_real(day, session)
            n = upsert(con, rows)
            if commit_each_day:
                con.commit()
            total += n
            days += 1
        except Exception:  # noqa: BLE001 - một ngày lỗi không làm hỏng cả khoảng
            if commit_each_day:
                con.rollback()
            log.exception("Lỗi nạp ngày %s (bỏ qua).", _as_date(day).isoformat())
    return {
        "tu_ngay": _as_date(start).isoformat(),
        "den_ngay": _as_date(end).isoformat(),
        "so_dong": total,
        "so_phien": days,
    }
