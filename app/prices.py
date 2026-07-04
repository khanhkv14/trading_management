# -*- coding: utf-8 -*-
"""Lấy giá khớp lệnh THỜI GIAN THỰC (miễn phí) cho cổ phiếu Việt Nam.

Nguồn: iBoard của SSI — endpoint công khai, không cần API key:
    https://iboard-query.ssi.com.vn/stock/<MÃ>
Trả về JSON có `matchedPrice` (giá khớp gần nhất) theo đơn vị VND ĐẦY ĐỦ, cùng
thang với giá người dùng nhập trong bảng transactions (vd 72300 = 72.300đ).

Thiết kế:
- Thuần thư viện chuẩn (urllib) -> KHÔNG thêm dependency, chạy được trên
  PythonAnywhere free.
- Cache trong bộ nhớ theo TTL ngắn: nhiều lần mở Dashboard / poll realtime trong
  ít giây dùng lại kết quả cũ, tránh nã liên tục vào SSI.
- Gọi song song (ThreadPool) cho nhiều mã để tổng độ trễ ~ 1 request.
- Mọi lỗi mạng/parse đều nuốt gọn -> mã lỗi trả giá None (giao diện hiện '—'),
  KHÔNG làm sập trang.
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Thời gian sống của một giá trong cache (giây). Thị trường khớp lệnh liên tục
# nên 10s là đủ "realtime" mà vẫn nhẹ tải.
TTL = 10
_TIMEOUT = 6            # giây cho mỗi request HTTP
_URL = "https://iboard-query.ssi.com.vn/stock/{}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://iboard.ssi.com.vn/",
}

# symbol -> (thoi_diem_luu, gia)
_cache = {}


def _fetch_one(symbol):
    """Gọi SSI cho MỘT mã, trả giá khớp (VND) hoặc None nếu lỗi/không có."""
    url = _URL.format(symbol)
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8")).get("data") or {}
    except Exception:
        return None
    # matchedPrice = giá khớp gần nhất; khi mã chưa khớp phiên nay (=0/None) thì
    # dùng giá tham chiếu / giá đóng cửa hôm trước để vẫn có số hiển thị.
    for key in ("matchedPrice", "priorClosePrice", "refPrice", "closePrice"):
        v = data.get(key)
        if v:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def get_prices(symbols):
    """Trả về dict {mã: giá VND | None} cho danh sách mã (đã khử trùng lặp).

    Dùng cache còn hạn; chỉ gọi mạng cho những mã thiếu hoặc đã hết TTL. Các mã
    cần lấy mới được gọi song song để giảm tổng độ trễ."""
    now = time.time()
    syms = {s.strip().upper() for s in symbols if s and s.strip()}

    result, stale = {}, []
    for s in syms:
        hit = _cache.get(s)
        if hit and (now - hit[0]) < TTL:
            result[s] = hit[1]
        else:
            stale.append(s)

    if stale:
        # Giới hạn số luồng để lịch sự với SSI; đủ nhanh cho vài chục mã.
        with ThreadPoolExecutor(max_workers=min(8, len(stale))) as ex:
            fetched = dict(zip(stale, ex.map(_fetch_one, stale)))
        for s, price in fetched.items():
            # Chỉ ghi đè cache khi lấy được giá; nếu lỗi (None) mà cache cũ còn
            # giá thì giữ lại giá cũ để đỡ nhấp nháy '—' lúc mạng chập chờn.
            if price is not None:
                _cache[s] = (now, price)
                result[s] = price
            else:
                result[s] = _cache[s][1] if s in _cache else None

    return result
