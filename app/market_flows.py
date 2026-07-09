# -*- coding: utf-8 -*-
"""Thống kê dòng tiền Khối ngoại / Tự doanh theo TUẦN (ISO) và THÁNG.

Bảng GỐC `market_flows` lưu 1 dòng/mã/ngày (khối lượng & giá trị mua–bán của từng
khối). Các con số MUA RÒNG theo tuần/tháng và bảng xếp hạng đều được TÍNH ĐỘNG từ
bảng gốc khi truy vấn — không lưu dữ liệu phái sinh (đồng bộ với triết lý vị thế
được tính động trong app/positions.py).

Công thức:  Giá trị mua ròng = Giá trị mua − Giá trị bán  (tính riêng từng khối).

Cách lọc theo kỳ: quy đổi mã kỳ ('2026-W28' / '2026-07') về KHOẢNG NGÀY
[đầu kỳ, cuối kỳ] rồi lọc `WHERE ngay BETWEEN ? AND ?`. Nhờ đó tận dụng index trên
cột `ngay` và không cần lưu thêm cột 'tuần'/'tháng' phái sinh.
"""
import calendar
import logging
import re
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

# Các khối được hỗ trợ và tiền tố cột tương ứng trong bảng market_flows.
#   foreign -> kn_ (khối ngoại) · prop -> td_ (tự doanh)
KHOI_PREFIX = {"foreign": "kn", "prop": "td"}

_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")     # 2026-W28
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")      # 2026-07


class InvalidPeriod(ValueError):
    """Mã kỳ (value) sai định dạng hoặc ngoài phạm vi hợp lệ."""


def iso_week_of(day):
    """date/'YYYY-MM-DD' -> nhãn tuần ISO 'YYYY-Www' (vd 2026-W28)."""
    d = _as_date(day)
    y, w, _ = d.isocalendar()
    return f"{y:04d}-W{w:02d}"


def month_of(day):
    """date/'YYYY-MM-DD' -> nhãn tháng 'YYYY-MM' (vd 2026-07)."""
    d = _as_date(day)
    return f"{d.year:04d}-{d.month:02d}"


def _as_date(day):
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    return datetime.strptime(str(day)[:10], "%Y-%m-%d").date()


def week_range(value):
    """'YYYY-Www' -> (thứ Hai, Chủ Nhật) của tuần ISO đó, dạng 'YYYY-MM-DD'.

    Dùng %G-W%V-%u (năm/tuần/thứ theo lịch ISO) để lấy đúng thứ Hai đầu tuần —
    KHÔNG dùng %Y/%W vì đó là tuần bắt đầu từ Chủ Nhật/khác chuẩn ISO."""
    m = _WEEK_RE.match((value or "").strip())
    if not m:
        raise InvalidPeriod(f"Invalid week: {value!r} (expected '2026-W28')")
    year, week = int(m.group(1)), int(m.group(2))
    if not 1 <= week <= 53:
        raise InvalidPeriod(f"Week number out of range 1..53: {value!r}")
    try:
        monday = datetime.strptime(f"{year:04d}-W{week:02d}-1", "%G-W%V-%u").date()
    except ValueError as e:
        raise InvalidPeriod(f"Week does not exist: {value!r}") from e
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def month_range(value):
    """'YYYY-MM' -> (ngày đầu tháng, ngày cuối tháng) dạng 'YYYY-MM-DD'."""
    m = _MONTH_RE.match((value or "").strip())
    if not m:
        raise InvalidPeriod(f"Invalid month: {value!r} (expected '2026-07')")
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise InvalidPeriod(f"Month out of range 1..12: {value!r}")
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def period_range(period_type, value):
    """Quy đổi (type, value) -> (ngày_đầu, ngày_cuối). type: 'weekly' | 'monthly'."""
    if period_type == "weekly":
        return week_range(value)
    if period_type == "monthly":
        return month_range(value)
    raise InvalidPeriod(f"Invalid type: {period_type!r} (use 'weekly'/'monthly')")


def aggregate(db, period_type, value, khoi="foreign", limit=20):
    """Cộng dồn dòng tiền trong kỳ rồi xếp hạng theo GIÁ TRỊ MUA RÒNG (giảm dần).

    Tham số:
        db          : kết nối sqlite3 (row_factory = Row).
        period_type : 'weekly' | 'monthly'.
        value       : '2026-W28' | '2026-07'.
        khoi        : 'foreign' (khối ngoại) | 'prop' (tự doanh).
        limit       : số mã lấy cho mỗi chiều top mua/top bán ròng.

    Trả về dict gồm khoảng ngày, danh sách xếp hạng đầy đủ (ranked) và hai lát
    cắt tiện dụng top_mua_rong / top_ban_rong.
    """
    if khoi not in KHOI_PREFIX:
        raise InvalidPeriod(f"Invalid khoi: {khoi!r} (use 'foreign'/'prop')")
    pfx = KHOI_PREFIX[khoi]
    start, end = period_range(period_type, value)

    # Cộng dồn theo mã trong khoảng ngày. Chỉ tính đúng khối được chọn để câu
    # truy vấn gọn; COALESCE tránh NULL khi một ngày thiếu số liệu.
    rows = db.execute(
        f"""
        SELECT ma_cp,
               COALESCE(SUM({pfx}_mua_gt), 0) AS mua_gt,
               COALESCE(SUM({pfx}_ban_gt), 0) AS ban_gt,
               COALESCE(SUM({pfx}_mua_kl), 0) AS mua_kl,
               COALESCE(SUM({pfx}_ban_kl), 0) AS ban_kl,
               COUNT(*) AS so_phien
        FROM market_flows
        WHERE ngay BETWEEN ? AND ?
        GROUP BY ma_cp
        """,
        (start, end),
    ).fetchall()

    ranked = []
    for r in rows:
        mua_gt, ban_gt = r["mua_gt"], r["ban_gt"]
        ranked.append({
            "ma_cp": r["ma_cp"],
            "mua_gt": round(mua_gt, 2),
            "ban_gt": round(ban_gt, 2),
            "net_gt": round(mua_gt - ban_gt, 2),            # giá trị mua ròng
            "net_kl": round(r["mua_kl"] - r["ban_kl"], 2),  # khối lượng mua ròng
            "so_phien": r["so_phien"],
        })
    # Sắp giảm dần theo giá trị mua ròng: đầu danh sách = mua ròng mạnh nhất,
    # cuối danh sách = bán ròng mạnh nhất.
    ranked.sort(key=lambda x: x["net_gt"], reverse=True)

    top_mua = [x for x in ranked if x["net_gt"] > 0][:limit]
    # Bán ròng nhiều nhất = net âm nhất -> lấy từ cuối danh sách lên.
    top_ban = [x for x in reversed(ranked) if x["net_gt"] < 0][:limit]

    return {
        "type": period_type,
        "value": value,
        "khoi": khoi,
        "tu_ngay": start,
        "den_ngay": end,
        "so_ma": len(ranked),
        "ranked": ranked,
        "top_mua_rong": top_mua,
        "top_ban_rong": top_ban,
    }
