# -*- coding: utf-8 -*-
"""Nạp lịch sử khớp lệnh (mua/bán từng lần) vào bảng GỐC `transactions`.

Mô hình mới: chỉ lưu lệnh khớp thô; vị thế được app tự gộp bằng quy tắc 0-crossing
(app/positions.py). Vì thế script này KHÔNG tự tính P/L nữa — chỉ chèn giao dịch.

- ma_cp lấy từ Trade ID (bỏ hậu tố _NN) = mã cổ phiếu thật, vd PVT_01 -> PVT.
  Quy tắc 0-crossing sẽ tự tách PVT_01 và PVT_02 thành 2 vị thế độc lập.
- CẢNH BÁO: script XÓA TOÀN BỘ bảng transactions rồi nạp lại (idempotent).
  Nếu bạn đã nhập tay dữ liệu, đừng chạy hoặc hãy sao lưu trước.

Chạy:  python scripts/import_transactions.py
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "database", "trades.db"))
YEAR = 2026

# Dữ liệu thô: Date / Trade ID / Ticker / Action / Quantity / Last Price / Mkt Value
RAW = """
18/1	PLX_01	PLX	Buy	1000	39,750 ₫	39,750,000 ₫
23/1	BSR_01	BSR	Buy	1900	21,900 ₫	41,610,000 ₫
27/2	PVT_01	PVT	Buy	1500	23,550 ₫	35,325,000 ₫
6/3	PLX_01	PLX	Sell	500	62,400 ₫	31,200,000 ₫
5/3	MBS_01	MBS	Buy	500	29,000 ₫	14,500,000 ₫
5/3	ORS_01	ORS	Buy	2000	14,800 ₫	29,600,000 ₫
10/3	MBS_01	MBS	Sell	500	25,300 ₫	12,650,000 ₫
10/3	PLX_01	PLX	Sell	500	56,200 ₫	28,100,000 ₫
10/3	PVT_01	PVT	Sell	500	27,500 ₫	13,750,000 ₫
1/2	MBB_01	MBB	Buy	2000	25,550 ₫	51,100,000 ₫
1/1	SSI_01	SSI	Buy	100	15,000 ₫	1,500,000 ₫
10/3	PVT_01	PVT	Sell	500	28,750 ₫	14,375,000 ₫
10/3	BSR_01	BSR	Sell	900	37,700 ₫	33,930,000 ₫
7/3	FPT_01	FPT	Buy	100	82,850 ₫	8,285,000 ₫
11/3	PVT_01	PVT	Sell	500	26,800 ₫	13,400,000 ₫
2/3	DGC_01	DGC	Buy	500	76,000 ₫	38,000,000 ₫
23/2	BID_01	BID	Buy	1000	48,550 ₫	48,550,000 ₫
2/3	BID_01	BID	Sell	1000	46,600 ₫	46,600,000 ₫
4/3	DGC_01	DGC	Sell	500	70,800 ₫	35,400,000 ₫
20/1	DGW_01	DGW	Buy	300	47,300 ₫	14,190,000 ₫
21/1	DGW_01	DGW	Buy	400	45,950 ₫	18,380,000 ₫
26/1	DGW_01	DGW	Sell	200	44,600 ₫	8,920,000 ₫
27/1	DGW_01	DGW	Sell	200	44,000 ₫	8,800,000 ₫
28/1	DGW_01	DGW	Sell	300	45,250 ₫	13,575,000 ₫
22/1	BCM_01	BCM	Buy	500	76,500 ₫	38,250,000 ₫
26/1	BCM_01	BCM	Sell	200	69,700 ₫	13,940,000 ₫
26/1	BCM_01	BCM	Sell	200	67,200 ₫	13,440,000 ₫
26/1	BCM_01	BCM	Sell	100	69,800 ₫	6,980,000 ₫
8/1	SHB_01	SHB	Buy	900	16,950 ₫	15,255,000 ₫
9/1	SHB_01	SHB	Buy	100	16,800 ₫	1,680,000 ₫
19/1	SHB_01	SHB	Sell	1000	16,400 ₫	16,400,000 ₫
19/1	EIB_01	EIB	Buy	1000	23,050 ₫	23,050,000 ₫
20/1	VCB_01	VCB	Buy	200	73,600 ₫	14,720,000 ₫
21/1	EIB_01	EIB	Sell	1000	22,300 ₫	22,300,000 ₫
23/1	VCB_01	VCB	Sell	200	68,700 ₫	13,740,000 ₫
11/3	ORS_01	ORS	Buy	500	14,000 ₫	7,000,000 ₫
11/3	ORS_01	ORS	Buy	500	13,900 ₫	6,950,000 ₫
11/3	BSR_01	BSR	Sell	500	34,800 ₫	17,400,000 ₫
11/3	BSR_01	BSR	Sell	500	35,500 ₫	17,750,000 ₫
12/3	SSI_01	SSI	Buy	400	29,200 ₫	11,680,000 ₫
12/3	HPG_01	HPG	Buy	500	26,800 ₫	13,400,000 ₫
13/3	PVT_02	PVT2	Buy	1000	24,650 ₫	24,650,000 ₫
17/3	LPB_01	LPB	Buy	400	43,800 ₫	17,520,000 ₫
23/3	ORS_01	ORS	Sell	3000	12,300 ₫	36,900,000 ₫
23/3	PVT_01	PVT	Sell	500	21,800 ₫	10,900,000 ₫
24/3	PVT_01	PVT	Sell	500	20,800 ₫	10,400,000 ₫
27/3	LPB_01	LPB	Sell	400	41,750 ₫	16,700,000 ₫
1/4	REE_01	REE	Buy	300	69,600 ₫	20,880,000 ₫
1/4	PLX_02	PLX2	Buy	500	41,100 ₫	20,550,000 ₫
1/4	ORS_02	ORS2	Buy	1000	13,800 ₫	13,800,000 ₫
2/4	REE_01	REE	Buy	200	68,100 ₫	13,620,000 ₫
6/4	PLX_02	PLX2	Sell	500	39,450 ₫	19,725,000 ₫
8/4	FPT_01	FPT	Buy	100	75,300 ₫	7,530,000 ₫
8/4	ORS_02	ORS2	Buy	500	13,700 ₫	6,850,000 ₫
8/4	TCB_01	TCB	Buy	1000	29,700 ₫	29,700,000 ₫
9/4	REE_01	REE	Sell	200	65,800 ₫	13,160,000 ₫
9/4	TCB_01	TCB	Buy	1000	32,250 ₫	32,250,000 ₫
22/4	ORS_02	ORS2	Sell	100	13,450 ₫	1,345,000 ₫
22/4	FPT_01	FPT	Buy	100	75,000 ₫	7,500,000 ₫
6/5	PVS_03	PVS3	Buy	500	40,700 ₫	20,350,000 ₫
6/5	GAS_01	GAS	Buy	300	79,200 ₫	23,760,000 ₫
6/5	TCH_01	TCH	Buy	800	17,500 ₫	14,000,000 ₫
8/5	PVS_03	PVS3	Sell	500	38,400 ₫	19,200,000 ₫
13/5	GAS_01	GAS	Buy	300	81,300 ₫	24,390,000 ₫
14/5	FPT_01	FPT	Buy	100	74,600 ₫	7,460,000 ₫
15/5	PLX_03	PLX3	Buy	500	42,200 ₫	21,100,000 ₫
18/5	MBB_01	MBB	Sell	2000	25,500 ₫	51,000,000 ₫
18/5	EVF_01	EVF	Buy	2000	14,100 ₫	28,200,000 ₫
18/5	HPG_01	HPG	Sell	500	26,550 ₫	13,275,000 ₫
18/5	SSI_01	SSI	Sell	500	27,950 ₫	13,975,000 ₫
18/5	PLC_01	PLC	Buy	1000	23,800 ₫	23,800,000 ₫
18/5	VCB_02	VCB2	Buy	1000	62,800 ₫	62,800,000 ₫
19/5	ORS_02	ORS2	Buy	600	13,800 ₫	8,280,000 ₫
19/5	VND_01	VND	Buy	2000	17,300 ₫	34,600,000 ₫
19/5	PLC_01	PLC	Buy	1000	24,400 ₫	24,400,000 ₫
19/5	TCH_01	TCH	Sell	800	16,750 ₫	13,400,000 ₫
19/5	FPT_01	FPT	Buy	100	75,400 ₫	7,540,000 ₫
19/5	PLC_01	PLC	Buy	1000	23,200 ₫	23,200,000 ₫
20/5	TCB_01	TCB	Sell	2000	31,750 ₫	63,500,000 ₫
20/5	PLC_01	PLC	Buy	1000	21,100 ₫	21,100,000 ₫
20/5	VGI_01	VGI	Buy	200	92,300 ₫	18,460,000 ₫
20/5	CTR_01	CTR	Buy	200	89,800 ₫	17,960,000 ₫
20/5	EVF_01	EVF	Sell	1000	13,300 ₫	13,300,000 ₫
20/5	GEX_01	GEX	Buy	1000	35,250 ₫	35,250,000 ₫
21/5	EVF_01	EVF	Sell	1000	13,450 ₫	13,450,000 ₫
21/5	VND_01	VND	Sell	1000	16,300 ₫	16,300,000 ₫
21/5	VND_01	VND	Sell	1000	16,350 ₫	16,350,000 ₫
22/5	PLX_03	PLX3	Buy	500	42,100 ₫	21,050,000 ₫
25/5	FPT_01	FPT	Buy	100	74,000 ₫	7,400,000 ₫
26/5	ACB_01	ACB	Buy	1000	24,300 ₫	24,300,000 ₫
27/5	VCB_02	VCB2	Sell	900	64,000 ₫	57,600,000 ₫
28/5	CTR_01	CTR	Sell	200	88,400 ₫	17,680,000 ₫
28/5	GEX_01	GEX	Sell	500	32,450 ₫	16,225,000 ₫
1/6	GEX_01	GEX	Sell	500	31,850 ₫	15,925,000 ₫
2/6	FPT_01	FPT	Buy	100	75,100 ₫	7,510,000 ₫
2/6	FRT_01	FRT	Buy	200	129,500 ₫	25,900,000 ₫
2/6	FRT_01	FRT	Buy	100	128,000 ₫	12,800,000 ₫
4/6	PLC_01	PLC	Sell	1000	22,100 ₫	22,100,000 ₫
4/6	PLC_01	PLC	Sell	1000	21,900 ₫	21,900,000 ₫
4/6	FRT_01	FRT	Sell	100	125,600 ₫	12,560,000 ₫
5/6	TPB_01	TPB	Buy	1500	16,250 ₫	24,375,000 ₫
8/6	ORS_02	ORS2	Sell	2000	12,700 ₫	25,400,000 ₫
8/6	VGI_01	VGI	Sell	200	90,500 ₫	18,100,000 ₫
9/6	FPT_01	FPT	Buy	100	73,700 ₫	7,370,000 ₫
10/6	FRT_01	FRT	Sell	100	120,100 ₫	12,010,000 ₫
11/6	ACB_01	ACB	Sell	1000	26,350 ₫	26,350,000 ₫
12/6	TPB_01	TPB	Buy	1500	16,450 ₫	24,675,000 ₫
12/6	MBB_02	MBB2	Buy	1000	25,150 ₫	25,150,000 ₫
15/6	GEL_01	GEL	Buy	500	32,600 ₫	16,300,000 ₫
15/6	PLX_03	PLX3	Sell	500	39,050 ₫	19,525,000 ₫
15/6	FRT_01	FRT	Sell	100	121,000 ₫	12,100,000 ₫
15/6	VCK_01	VCK	Buy	600	34,050 ₫	20,430,000 ₫
16/6	GEL_01	GEL	Buy	500	33,350 ₫	16,675,000 ₫
16/6	NLG_01	NLG	Buy	1000	27,300 ₫	27,300,000 ₫
16/6	PET_01	PET	Buy	500	52,500 ₫	26,250,000 ₫
16/6	MBB_02	MBB2	Sell	1000	25,200 ₫	25,200,000 ₫
16/6	GAS_01	GAS	Sell	300	81,200 ₫	24,360,000 ₫
16/6	GAS_01	GAS	Sell	300	80,900 ₫	24,270,000 ₫
17/6	PET_01	PET	Buy	200	56,200 ₫	11,240,000 ₫
18/6	TPB_01	TPB	Sell	2000	16,200 ₫	32,400,000 ₫
23/6	PET_01	PET	Buy	300	55,700 ₫	16,710,000 ₫
23/6	ORS_03	ORS3	Buy	1000	13,500 ₫	13,500,000 ₫
23/6	PET_01	PET	Buy	500	51,500 ₫	25,750,000 ₫
23/6	PET_01	PET	Buy	500	51,200 ₫	25,600,000 ₫
24/6	ORS_03	ORS3	Buy	1000	13,800 ₫	13,800,000 ₫
24/6	GEL_01	GEL	Buy	400	32,000 ₫	12,800,000 ₫
26/6	PET_01	PET	Sell	1000	50,900 ₫	50,900,000 ₫
30/6	GEL_01	GEL	Buy	100	32,300 ₫	3,230,000 ₫
30/6	ORS_03	ORS3	Buy	1000	13,650 ₫	13,650,000 ₫
30/6	GEL_01	GEL	Buy	500	32,650 ₫	16,325,000 ₫
1/7	VCK_01	VCK	Sell	600	33,100 ₫	19,860,000 ₫
1/7	MBB_03	MBB3	Buy	600	25,500 ₫	15,300,000 ₫
1/7	GEL_01	GEL	Sell	500	31,950 ₫	15,975,000 ₫
1/7	FPT_01	FPT	Buy	200	71,000 ₫	14,200,000 ₫
19/6	GEL_01	GEL	Sell	500	31,250 ₫	15,625,000 ₫
"""


def parse_rows():
    rows = []
    for line in RAW.strip().splitlines():
        line = line.replace("₫", "").replace(",", "").strip()
        if not line:
            continue
        parts = line.split()
        d, tid, ticker, action, qty, price = parts[:6]
        day, month = d.split("/")
        iso = f"{YEAR:04d}-{int(month):02d}-{int(day):02d}"
        rows.append({
            "date": iso, "tid": tid, "symbol": tid.split("_")[0],
            "action": action.lower(), "qty": int(qty), "price": float(price),
        })
    return rows


def main():
    rows = parse_rows()
    # Chèn theo thứ tự thời gian để id tăng dần khớp trình tự khớp lệnh.
    rows.sort(key=lambda r: r["date"])

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("DELETE FROM transactions")  # nạp lại toàn bộ (xem cảnh báo ở đầu file)

    for r in rows:
        con.execute(
            "INSERT INTO transactions (ngay, ma_cp, loai, so_luong, gia) "
            "VALUES (?,?,?,?,?)",
            (r["date"], r["symbol"],
             "mua" if r["action"] == "buy" else "ban",
             r["qty"], r["price"]))
    con.commit()

    # Tóm tắt: gộp thử thành vị thế để in kết quả (dùng chính logic của app).
    txs = [dict(row) for row in con.execute("SELECT * FROM transactions")]
    con.close()

    from app.positions import compute_positions
    pos = compute_positions(txs)
    closed = [p for p in pos if p["trang_thai"] == "closed"]
    open_ = [p for p in pos if p["trang_thai"] == "open"]
    total_pnl = sum(p["pnl"] or 0 for p in closed)
    wins = sum(1 for p in closed if (p["pnl"] or 0) > 0)
    print(f"Đã nạp {len(rows)} giao dịch -> gộp thành {len(pos)} vị thế.")
    print(f"  - Đã đóng: {len(closed)}  | Đang mở: {len(open_)}")
    print(f"  - Thắng/Tổng đóng: {wins}/{len(closed)}"
          f"  ({round(wins/len(closed)*100,1) if closed else 0}%)")
    print(f"  - Tổng P/L thực hiện: {total_pnl:,.0f} đ")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, BASE_DIR)  # để import được app.positions khi chạy từ scripts/
    main()
