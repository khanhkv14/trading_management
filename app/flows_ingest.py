# -*- coding: utf-8 -*-
"""Nạp dòng tiền Khối ngoại / Tự doanh vào bảng `market_flows` (dùng chung).

Module này chứa PHẦN LÕI để cả hai nơi cùng gọi:
  - scripts/fetch_market_flows.py  (chạy CLI / đặt lịch ở máy có mạng)
  - route POST /market-flows/update (nút "Cập nhật" trên giao diện)

Nguồn dữ liệu:
  - Ưu tiên `vnstock` (vnstock3) nếu cài được & lấy được số thật.
  - Nếu thiếu thư viện / lỗi mạng / API đổi -> GIẢ LẬP (ổn định theo ngày+mã).

Vì bộ giả lập KHÔNG cần internet, nút "Cập nhật" chạy được cả trên PythonAnywhere
free (nơi chặn kết nối RA). Dữ liệu giả lập deterministic nên mọi máy cho cùng số.

Chống trùng: UPSERT theo khóa (ma_cp, ngay) — chạy lại chỉ cập nhật, không tạo trùng.
"""
import logging
import random
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

# Rổ mã dùng khi GIẢ LẬP (VN30 rút gọn). Nguồn thật sẽ trả toàn thị trường.
MOCK_UNIVERSE = [
    "FPT", "HPG", "SSI", "VCB", "MBB", "VND", "TCB", "PVT", "BSR", "GAS",
    "ACB", "DGC", "REE", "PLX", "VGI", "CTR", "GEX", "PET", "NLG", "VIC",
]

# Thứ tự cột cho câu UPSERT (khớp app/models.MARKET_FLOW_COLUMNS).
FIELDS = [
    "ngay", "ma_cp", "san",
    "kn_mua_kl", "kn_mua_gt", "kn_ban_kl", "kn_ban_gt",
    "td_mua_kl", "td_mua_gt", "td_ban_kl", "td_ban_gt",
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


def fetch_from_vnstock(day):
    """Thử lấy số liệu THẬT qua vnstock. Trả list dict theo FIELDS, hoặc None nếu
    không dùng được (chưa cài / API đổi / lỗi mạng) để caller chuyển sang giả lập.

    Tên hàm/định dạng của vnstock khác nhau theo phiên bản, nên phần map để rỗng
    có chủ đích — điền theo API bạn cài. Mặc định trả None -> chạy bằng giả lập."""
    try:
        import vnstock  # noqa: F401
    except ImportError:
        return None
    try:
        # TODO: gọi API foreign/proprietary của vnstock bạn cài rồi map về FIELDS.
        return None
    except Exception:  # noqa: BLE001 - mọi lỗi nguồn ngoài -> fallback an toàn
        log.exception("Lỗi lấy dữ liệu vnstock (%s) -> giả lập.", day)
        return None


def fetch_mock(day):
    """Sinh dữ liệu GIẢ LẬP ổn định theo (ngày, mã): chạy lại cùng ngày cho ra
    cùng số -> upsert idempotent, không nhiễu ngẫu nhiên giữa các lần chạy."""
    rows = []
    iso = _as_date(day).isoformat()
    for sym in MOCK_UNIVERSE:
        rnd = random.Random(f"{iso}:{sym}")     # seed cố định theo ngày+mã
        gia = rnd.uniform(15_000, 120_000)       # giá tham chiếu giả định (VND)

        def leg():
            kl = rnd.randint(0, 2_000_000)       # khối lượng
            return kl, round(kl * gia, 0)        # (khối lượng, giá trị = KL*giá)

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


def fetch_day(day, force_mock=False):
    """Lấy dữ liệu 1 ngày: thử vnstock trước, không được thì giả lập."""
    rows = None if force_mock else fetch_from_vnstock(day)
    if rows is None:
        rows = fetch_mock(day)
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


def ingest_range(con, start, end, force_mock=False, commit_each_day=False):
    """Nạp + upsert dữ liệu cho mọi phiên trong [start, end]. Trả dict tóm tắt.

    con              : kết nối sqlite3 (script tự mở; route dùng get_db()).
    commit_each_day  : True cho CLI (lỗi giữa chừng vẫn giữ phần đã nạp). Route để
                       False rồi commit một lần ở ngoài cho gọn giao dịch."""
    total = days = 0
    for day in trading_days(start, end):
        try:
            n = upsert(con, fetch_day(day, force_mock=force_mock))
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
