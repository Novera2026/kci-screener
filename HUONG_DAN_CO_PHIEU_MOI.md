# Xử lý cổ phiếu mới lên sàn (chưa/ít dữ liệu DART)

## Câu trả lời ngắn

DART **vẫn có ích** nhưng **không đủ một mình** cho cổ phiếu mới niêm yết, vì
사업보고서 (báo cáo năm) chỉ xuất hiện SAU khi công ty nộp lần đầu hậu niêm yết.
Cách khắc phục: hệ thống tự nhận biết độ phủ mỏng → chuyển nguồn + đổi phương pháp
+ chặn trần độ tin cậy, thay vì để DCF ra số rác.

## DART có gì cho DN mới niêm yết?

| Tình huống | DART năm (11011) | Cách lấy được số |
|---|---|---|
| Niêm yết < 1 năm | Chưa có | Báo cáo **quý/bán niên** (분기/반기) + bản cáo bạch IPO |
| Niêm yết 1–2 năm | 1–2 năm | DART năm ít + quý + Naver (gồm số IPO) |
| Niêm yết ≥ 3 năm | Đủ | Bình thường |

Hai "mảnh vá" quan trọng đã thêm vào `dart_api.py`:

- `quarterly_financials(ticker)` — lấy 분기/반기 gần nhất (có SỚM hơn báo cáo năm).
- `data_status(ticker)` — đếm số năm thực, phát hiện mới niêm yết, đề xuất mode + trần confidence.

## Bộ quyết định: `coverage.py`

Kết hợp **Naver + DART** (Naver thường có sẵn số bản cáo bạch IPO + consensus dự phóng,
có khi nhanh hơn DART) rồi ra khuyến nghị:

```python
import coverage
rep = coverage.assess_coverage("462870")
print(coverage.coverage_badge(rep))
# 🟡 3 năm dữ liệu (DART 2n + Naver 3n + quý/bán niên) · trần confidence: Medium · ⚠️ mới niêm yết
```

### Bảng định tuyến

| Độ phủ | valuation_mode | Phương pháp dùng | Tránh | Trần confidence |
|---|---|---|---|---|
| ≥3 năm chính thức | `intrinsic_ok` | DCF/RIM + EV/EBITDA + P/E fwd | — | không chặn |
| ≥3 năm nhưng mới niêm yết* | `intrinsic_ok` | DCF/RIM (tham khảo) + peer + reverse DCF | — | **Medium** |
| 2 năm | `intrinsic_weak` | Multiples forward + peer + reverse DCF | DCF nhiều giai đoạn | Medium |
| 1 năm hoặc chỉ có quý | `relative_forward` | Forward multiples + peer + annualize quý | DCF, RIM, DDM | **Low** |
| ~không có gì | `peer_only` | Peer-relative + số bản cáo bạch IPO | mọi intrinsic | **Low** |

*Đủ năm số liệu nhưng phần lớn từ bản cáo bạch IPO, track record công khai ngắn,
cơ cấu vốn đổi lúc IPO → vẫn chặn Medium.

## Vì sao chặn trần confidence?

Đúng nguyên tắc SOP: với DN mới niêm yết, fair value từ intrinsic rất nhạy (ít lịch sử
để ước driver, chưa qua chu kỳ). Để confidence lên High là **tự lừa mình**. Chặn trần +
nói rõ "đang dùng peer/forward vì thiếu lịch sử" mới là trung thực.

## Cắm vào app/valuation thế nào

Trước khi định giá 1 mã, gọi `coverage.assess_coverage(ticker)`:

1. Hiển thị `coverage_badge(rep)` lên đầu trang định giá (cho người dùng biết độ tin cậy nền).
2. Lấy `rep["valuation_mode"]` để chọn phương pháp primary:
   - `intrinsic_ok` → chạy DCF/RIM như hiện tại.
   - `intrinsic_weak` / `relative_forward` / `peer_only` → ưu tiên multiples forward + peer,
     ẩn/đánh dấu "tham khảo" cho DCF.
3. Lấy `rep["confidence_cap"]` để **chặn trần** điểm confidence cuối (không cho vượt Medium/Low).
4. Hiện `rep["note"]` như cảnh báo vàng (giống dòng cảnh báo đang có trong app).

> Gợi ý: chỉ cần thêm vài dòng trong `valuation.py` chỗ tính confidence cuối:
> `cap = coverage.assess_coverage(ticker)["confidence_cap"]` rồi nếu cap thì hạ điểm.

## Đã kiểm chứng (live, key của bạn)

- `005930` Samsung → 6 năm DART → `intrinsic_ok`, không chặn. ✅
- `462870` 시프트업 (IPO 2024) → DART 2 năm + Naver 3 năm → `intrinsic_ok` nhưng **trần Medium**, cờ "mới niêm yết". ✅
