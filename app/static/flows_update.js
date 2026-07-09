/* Nút "Update" dòng tiền Khối ngoại — chạy NGAY TRÊN TRÌNH DUYỆT.

   Vì sao client-side: PythonAnywhere free chặn server ra internet, nhưng KHÔNG
   chặn trình duyệt. API khối ngoại VNDirect (api-finfo) trả CORS '*' nên trình
   duyệt gọi trực tiếp được. Luồng: với mỗi phiên trong [data-start .. hôm nay],
   trình duyệt fetch VNDirect -> POST các dòng về /market-flows/ingest (server chỉ
   upsert). Xong thì reload để render lại số mới. Nhờ đó nút chạy cả trên PA free,
   đồng bộ cách app đang lấy giá realtime client-side.

   Gắn nút:  <button onclick="MarketFlowUpdate(this)" data-start="YYYY-MM-DD">…</button>
*/
(function () {
  var VND = 'https://api-finfo.vndirect.com.vn/v4/foreigns';

  function iso(d) {
    return d.getFullYear() + '-' +
      String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  }

  // Các ngày GIAO DỊCH (bỏ Thứ 7/CN) từ start tới hôm nay, dạng 'YYYY-MM-DD'.
  function tradingDays(startStr) {
    var days = [];
    var d = new Date(startStr + 'T00:00:00');
    var today = new Date(); today.setHours(0, 0, 0, 0);
    while (d <= today) {
      var wd = d.getDay();               // 0=CN, 6=T7
      if (wd !== 0 && wd !== 6) days.push(iso(d));
      d.setDate(d.getDate() + 1);
    }
    return days;
  }

  // Fetch khối ngoại 1 phiên -> mảng dòng theo schema server. Lỗi/ngày nghỉ -> [].
  function fetchDay(day) {
    var url = VND + '?q=tradingDate:' + day + '&size=3000';
    return fetch(url).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var data = (d && d.data) || [];
        var rows = [];
        for (var i = 0; i < data.length; i++) {
          var x = data[i];
          if (x.type && x.type !== 'STOCK') continue;   // bỏ CW/ETF...
          if (!x.code) continue;
          rows.push({
            ngay: String(x.tradingDate).slice(0, 10), ma_cp: x.code, san: x.floor,
            kn_mua_kl: x.buyVol, kn_mua_gt: x.buyVal,
            kn_ban_kl: x.sellVol, kn_ban_gt: x.sellVal
          });
        }
        return rows;
      })
      .catch(function () { return []; });
  }

  // Gửi 1 phiên về server để upsert. Trả số dòng ghi (0 nếu rỗng/lỗi).
  function postDay(rows) {
    if (!rows.length) return Promise.resolve(0);
    return fetch('/market-flows/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ rows: rows })
    }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { return (d && d.n) || 0; })
      .catch(function () { return 0; });
  }

  window.MarketFlowUpdate = function (btn) {
    if (btn.dataset.busy === '1') return;              // chống bấm chồng
    var start = btn.getAttribute('data-start');
    if (!start) return;
    var days = tradingDays(start);
    if (!days.length) { location.reload(); return; }

    btn.dataset.busy = '1';
    var label = btn.textContent;
    btn.disabled = true;
    var done = 0, written = 0;

    // Chạy TUẦN TỰ từng phiên: nhẹ tải, hiện tiến độ, tránh nã 30 request cùng lúc.
    function step() {
      if (done >= days.length) {
        btn.textContent = 'Done · ' + written + ' rows';
        setTimeout(function () { location.reload(); }, 400);
        return;
      }
      var day = days[done];
      btn.textContent = 'Updating… ' + (done + 1) + '/' + days.length;
      fetchDay(day).then(postDay).then(function (n) {
        written += n; done += 1; step();
      });
    }
    step();
  };
})();
