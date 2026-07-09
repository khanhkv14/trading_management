# -*- coding: utf-8 -*-
"""Nạp dòng tiền GIAO DỊCH HÀNG NGÀY của Khối ngoại & Tự doanh vào bảng `market_flows`.

Cách dùng:
    python scripts/fetch_market_flows.py                 # hôm nay
    python scripts/fetch_market_flows.py 2026-07-09       # một ngày
    python scripts/fetch_market_flows.py 2026-07-01 2026-07-09   # khoảng ngày
    python scripts/fetch_market_flows.py --mock 2026-07-09       # ép dùng dữ liệu giả lập

Nguồn dữ liệu:
    - Ưu tiên thư viện `vnstock` (vnstock3) nếu đã cài & lấy được số liệu thật.
    - Nếu thiếu thư viện / lỗi mạng / API đổi -> tự động GIẢ LẬP dữ liệu (deterministic
      theo ngày+mã) để pipeline luôn chạy được khi phát triển & test.

Lưu ý vận hành: trên PythonAnywhere tài khoản free, kết nối internet RA từ server
bị chặn nên hãy chạy script này Ở MÁY LOCAL (hoặc máy có mạng) rồi đồng bộ file
database/trades.db lên. Script chỉ GHI SQLite nên chạy offline vẫn được (giả lập).

Chống trùng: dùng UPSERT theo khóa (ma_cp, ngay) — chạy lại cùng ngày chỉ cập nhật
số liệu, KHÔNG tạo dòng trùng.
"""
import argparse
import logging
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "database", "trades.db"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_market_flows")

# Rổ mã dùng khi GIẢ LẬP (VN30 rút gọn). Nguồn thật sẽ trả toàn thị trường.
MOCK_UNIVERSE = [
    "FPT", "HPG", "SSI", "VCB", "MBB", "VND", "TCB", "PVT", "BSR", "GAS",
    "ACB", "DGC", "REE", "PLX", "VGI", "CTR", "GEX", "PET", "NLG", "VIC",
]

# Thứ tự cột dùng cho câu UPSERT (khớp app/models.MARKET_FLOW_COLUMNS).
_FIELDS = [
    "ngay", "ma_cp", "san",
    "kn_mua_kl", "kn_mua_gt", "kn_ban_kl", "kn_ban_gt",
    "td_mua_kl", "td_mua_gt", "td_ban_kl", "td_ban_gt",
]


def daterange(start, end):
    """Sinh các ngày GIAO DỊCH (bỏ Thứ 7/CN) trong [start, end] inclusive."""
    d = start
    while d <= end:
        if d.weekday() < 5:  # 0=Thứ 2 ... 4=Thứ 6
            yield d
        d += timedelta(days=1)


def fetch_from_vnstock(day):
    """Thử lấy số liệu thật qua vnstock. Trả list dict theo _FIELDS, hoặc None nếu
    không dùng được (chưa cài / API đổi / lỗi mạng) để caller chuyển sang giả lập.

    Ghi chú: tên hàm/định dạng của vnstock thay đổi theo phiên bản, nên phần map
    dữ liệu để rỗng có chủ đích — hãy điền theo API bạn đang cài. Mặc định trả None
    để pipeline chạy bằng dữ liệu giả lập cho tới khi bạn nối nguồn thật."""
    try:
        import vnstock  # noqa: F401  (chỉ kiểm tra tồn tại thư viện)
    except ImportError:
        log.info("Chưa cài vnstock -> dùng dữ liệu giả lập.")
        return None
    try:
        # TODO: gọi API foreign/proprietary của phiên bản vnstock bạn cài, ví dụ
        #   from vnstock import Trading
        #   raw = Trading(...).foreign_trade(symbol=..., start=..., end=...)
        # rồi map từng dòng về dict theo _FIELDS. Tạm thời chưa map -> trả None.
        log.info("vnstock đã cài nhưng chưa nối API foreign/prop -> giả lập tạm.")
        return None
    except Exception:  # noqa: BLE001 - mọi lỗi nguồn ngoài đều fallback an toàn
        log.exception("Lỗi khi lấy dữ liệu từ vnstock (%s) -> giả lập.", day)
        return None


def fetch_mock(day):
    """Sinh dữ liệu GIẢ LẬP ổn định theo (ngày, mã): chạy lại cùng ngày cho ra
    cùng số liệu -> upsert thể hiện đúng tính idempotent, không nhiễu ngẫu nhiên."""
    rows = []
    iso = day.isoformat()
    for sym in MOCK_UNIVERSE:
        rnd = random.Random(f"{iso}:{sym}")   # seed cố định theo ngày+mã
        gia = rnd.uniform(15_000, 120_000)     # giá tham chiếu giả định (VND)

        def leg():
            kl = rnd.randint(0, 2_000_000)     # khối lượng
            return kl, round(kl * gia, 0)      # (khối lượng, giá trị = KL*giá)

        kn_mua_kl, kn_mua_gt = leg()
        kn_ban_kl, kn_ban_gt = leg()
        td_mua_kl, td_mua_gt = leg()
        td_ban_kl, td_ban_gt = leg()
        rows.append({
            "ngay": iso, "ma_cp": sym, "san": "HOSE",
            "kn_mua_kl": kn_mua_kl, "kn_mua_gt": kn_mua_gt,
            "kn_ban_kl": kn_ban_kl, "kn_ban_gt": kn_ban_gt,
            "td_mua_kl": td_mua_kl, "td_mua_gt": td_mua_gt,
            "td_ban_kl": td_ban_kl, "td_ban_gt": td_ban_gt,
        })
    return rows


def upsert(con, rows):
    """UPSERT theo (ma_cp, ngay): chèn mới hoặc cập nhật nếu đã tồn tại.
    Trả về số dòng đã ghi (chèn + cập nhật)."""
    cols = ", ".join(_FIELDS)
    placeholders = ", ".join(f":{f}" for f in _FIELDS)
    # Cập nhật mọi cột giá trị (trừ khóa ma_cp/ngay) từ bản ghi mới.
    updates = ", ".join(
        f"{f}=excluded.{f}" for f in _FIELDS if f not in ("ma_cp", "ngay")
    )
    sql = (
        f"INSERT INTO market_flows ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(ma_cp, ngay) DO UPDATE SET {updates}"
    )
    con.executemany(sql, rows)
    return len(rows)


def ingest_day(con, day, force_mock=False):
    """Lấy (thật hoặc giả lập) + upsert dữ liệu 1 ngày. Trả số dòng đã ghi."""
    rows = None if force_mock else fetch_from_vnstock(day)
    if rows is None:
        rows = fetch_mock(day)
    if not rows:
        log.warning("Không có dữ liệu cho %s (bỏ qua).", day.isoformat())
        return 0
    n = upsert(con, rows)
    log.info("%s: đã ghi %d mã.", day.isoformat(), n)
    return n


def parse_args(argv):
    p = argparse.ArgumentParser(description="Nạp dòng tiền Khối ngoại / Tự doanh.")
    p.add_argument("start", nargs="?", help="Ngày (YYYY-MM-DD). Mặc định: hôm nay.")
    p.add_argument("end", nargs="?", help="Ngày cuối (YYYY-MM-DD) cho khoảng ngày.")
    p.add_argument("--mock", action="store_true", help="Ép dùng dữ liệu giả lập.")
    return p.parse_args(argv)


def _as_date(s, fallback):
    if not s:
        return fallback
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        log.error("Ngày sai định dạng: %r (cần YYYY-MM-DD).", s)
        sys.exit(2)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    today = date.today()
    start = _as_date(args.start, today)
    end = _as_date(args.end, start)
    if end < start:
        start, end = end, start

    if not os.path.exists(DB_PATH):
        log.error("Không thấy DB tại %s. Chạy app một lần để tạo bảng trước.", DB_PATH)
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    total = 0
    try:
        for day in daterange(start, end):
            try:
                total += ingest_day(con, day, force_mock=args.mock)
                con.commit()   # commit theo từng ngày -> lỗi giữa chừng vẫn giữ phần đã nạp
            except Exception:  # noqa: BLE001 - một ngày lỗi không làm hỏng cả khoảng
                con.rollback()
                log.exception("Lỗi khi nạp ngày %s (bỏ qua ngày này).", day.isoformat())
    finally:
        con.close()

    log.info("HOÀN TẤT: %s -> %s, tổng %d dòng đã ghi.",
             start.isoformat(), end.isoformat(), total)


if __name__ == "__main__":
    main()
