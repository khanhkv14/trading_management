# -*- coding: utf-8 -*-
"""CLI nạp dòng tiền Khối ngoại & Tự doanh vào bảng `market_flows`.

Phần LÕI (lấy nguồn + upsert) nằm ở app/flows_ingest.py để dùng chung với nút
"Cập nhật" trên giao diện. File này chỉ là lớp vỏ dòng lệnh + đặt lịch.

Cách dùng:
    python scripts/fetch_market_flows.py                 # hôm nay
    python scripts/fetch_market_flows.py 2026-07-09       # một ngày
    python scripts/fetch_market_flows.py 2026-06-01 2026-07-09   # khoảng ngày
    python scripts/fetch_market_flows.py --mock 2026-07-09       # ép giả lập

Lưu ý: trên PythonAnywhere free, kết nối internet RA bị chặn -> hãy chạy CLI này
ở máy có mạng, hoặc dùng nút "Cập nhật" trên web (chạy bằng dữ liệu giả lập,
không cần internet nên hoạt động cả trên PA free).
"""
import argparse
import logging
import os
import sqlite3
import sys
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "database", "trades.db"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_market_flows")


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
    # Cho phép import app.flows_ingest khi chạy trực tiếp từ thư mục scripts/.
    sys.path.insert(0, BASE_DIR)
    from app.flows_ingest import ingest_range

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
    try:
        # commit_each_day=True: lỗi giữa chừng vẫn giữ phần ngày đã nạp.
        res = ingest_range(con, start, end, force_mock=args.mock, commit_each_day=True)
    finally:
        con.close()

    log.info("HOÀN TẤT: %s -> %s | %d phiên, %d dòng đã ghi.",
             res["tu_ngay"], res["den_ngay"], res["so_phien"], res["so_dong"])


if __name__ == "__main__":
    main()
