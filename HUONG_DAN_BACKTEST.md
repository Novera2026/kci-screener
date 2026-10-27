# Backtest — đo độ tin cậy thực nghiệm (mảnh thứ 3)

Đây là bước biến hệ thống từ "đúng lý thuyết" → "đã kiểm chứng bằng số". Chạy định giá
tại thời điểm quá khứ rồi so với giá thực sau đó.

## Cách 1 (mặc định, khuyến nghị): point-in-time trên số gốc DART — `backtest_dart.py`

Chỉ cần `DART_API_KEY` trong `.env`. Trong app: mở **🔬 Backtest** → nguồn **DART** → nhập mã
→ chọn các năm làm mốc → **🚀 Chạy backtest**. Dòng lệnh:

```bash
source .venv/bin/activate
python backtest_dart.py                       # 14 mã mặc định, mốc 15/4 năm 2021–2025
python backtest_dart.py 005930 000660 105560  # mã tùy chọn
```

Tại mốc T = phiên đầu tiên ≥ 15/4 năm Y, chỉ dùng thông tin có tại T: BCTC DART năm ≤ Y−1
(mã tài khoản IFRS chuẩn, hợp nhất), số cổ phiếu và cổ tức năm Y−1, giá Naver (đã điều chỉnh
chia tách), beta 78 tuần trước T so với KOSPI, Rf = TPCP 10 năm (FRED) tháng trước T, median
P/E–P/B peer cùng ngành trong cùng lát cắt. Định giá bằng chính `valuation.value_stock` của app
(ERP/g/w theo sidebar) rồi so với lợi suất vượt KOSPI sau N tháng.

Kết quả: **IC** trung bình theo năm (+ t-stat) và IC gộp, **ngũ phân vị** (Q1 = 20% mã rẻ nhất
mỗi năm: vượt KOSPI trung vị/TB, tỷ lệ mất >30%, tỷ lệ thắng thị trường), IC **từng phương
pháp**, theo **độ tin cậy**, và **dự phóng 1 năm** của công thức KCI so với naive (năm sau =
năm trước) trên số DART thực. Cache ở `.cache/` (BCTC năm đã nộp giữ vĩnh viễn).

Giới hạn riêng: tập đoàn tài chính (KB, Shinhan…) chưa có BCTC dạng cấu trúc trên DART trước
năm tài chính 2023 → chỉ có điểm từ mốc 2024; số cổ phiếu đổi mạnh không theo tỷ lệ tròn
(sáp nhập) → bỏ điểm đó.

## Cách 2: pykrx (KRX) — `backtest.py`

KRX hiện bắt đăng nhập mới trả P/E–EPS–BPS lịch sử: cần tài khoản miễn phí data.krx.co.kr,
thêm `KRX_ID` / `KRX_PW` vào `.env`. Thiếu tài khoản, app báo ngay thay vì chạy ra 0 điểm.

```bash
python backtest.py
```

Mặc định: 18 mã trải nhiều ngành × 3 mốc as-of (6/2023, 12/2023, 6/2024), horizon 12 tháng.
Xuất `backtest_results.csv` (chi tiết từng điểm) + `backtest_report.md` (tổng kết).

## Đọc 3 con số quan trọng

| Chỉ số | Nghĩa | "Tốt" nghĩa là |
|---|---|---|
| **median_abs_error** | Sai số định giá: median của \|fair − price\|/price | Càng nhỏ càng tốt. 0.3–0.5 là bình thường cho intrinsic; >0.7 = công cụ chỉ nên tham khảo |
| **IC (Spearman)** | Tương quan hạng giữa *upside model* và *return thực 12 tháng sau* | >0.1 đã có tín hiệu; >0.2–0.3 là tốt; ~0 nghĩa là model **không** dự báo được |
| **spread rẻ − đắt** | Chênh return giữa nhóm model cho là "rẻ nhất" và "đắt nhất" | Dương & lớn = model phân biệt được cổ phiếu rẻ/đắt |

Bảng **theo confidence**: nếu nhóm **High** có sai số nhỏ hơn / IC cao hơn nhóm **Low**
→ thang confidence có ý nghĩa thật. Nếu không → cần hiệu chỉnh lại cách chấm confidence.

## Cách dùng kết quả để NÂNG độ tin cậy

1. **Hiệu chỉnh confidence:** nếu nhóm High không tốt hơn Low, siết lại điều kiện chấm High
   trong `valuation.py` (hoặc hạ trần mặc định).
2. **Lọc phương pháp theo ngành:** xem IC theo `method`/`sector` (cột trong CSV) — ngành nào
   model sai nhiều thì để confidence Low cứng cho ngành đó.
3. **Hiệu chỉnh giả định:** chạy lại với `Assumptions` khác (Ke, g, persistence) xem bộ nào
   cho sai số thấp & IC cao nhất → chốt house assumptions có bằng chứng.
4. **Công bố sai số thật:** thay vì nói "định giá chính xác", hiển thị "sai số lịch sử ~X%,
   IC ~Y" — đây là điều khác biệt giữa công cụ nghiêm túc và app đoán mò.

## Giới hạn (đọc kỹ — đúng tinh thần SOP)

- **Mẫu nhỏ & ngắn:** vài chục điểm, vài năm → kết quả chỉ định hướng, không phải bằng chứng
  thống kê mạnh. Tăng số mã & số mốc as-of để chắc hơn.
- **Survivorship bias:** universe gồm mã còn niêm yết hôm nay → bỏ sót mã đã hủy niêm yết.
- **Proxy multiples:** as-of dùng EPS/BPS/PER/PBR pykrx (đã chế biến), chưa phải DART normalize
  → backtest đo *signal của model hiện tại*, không phải định giá lý tưởng.
- **Không phải chiến lược giao dịch:** IC dương ≠ kiếm tiền chắc (chưa tính phí, thanh khoản,
  rủi ro). Đây là kiểm định *chất lượng định giá*, không phải khuyến nghị đầu tư.

## Đã kiểm chứng

- Pipeline phân tích (IC, sai số, bucket, theo-confidence, report) — PASS với dữ liệu giả lập
  có tín hiệu dương (IC 0.85, spread +43.8%).
- `value_points` dùng đúng engine `valuation.value_stock` + median ngành theo cross-section as-of.
- Phần fetch giá OHLCV chạy live OK; fetch fundamental cần chạy trên máy bạn.
