# Hướng dẫn chọn phương pháp định giá (KCI)

> Rút gọn từ **KCI Valuation Master Spec**. Mục tiêu: nhìn 1 mã → biết **nên dùng phương pháp nào** và **đọc kết quả sao cho đúng**.

---

## 3 nguyên tắc vàng (đọc trước)

1. **Chọn method theo loại doanh nghiệp (entity), không theo cái gì dễ tính.** Sai entity = sai mọi thứ.
2. **Intrinsic làm gốc, multiples chỉ để soi chiếu.** Lấy P/E thị trường định giá chính nó = vô nghĩa (fair value sẽ luôn ≈ giá thị trường).
3. **Fair value là ƯỚC LƯỢNG có kỷ luật, không phải sự thật.** Thiếu dữ liệu → hạ độ tin cậy, không bịa số.

---

## Bảng tra nhanh: ngành → phương pháp nên dùng

| Nhóm ngành | Nên dùng (Primary) | Soi chiếu (Cross) | App tính tự động được? |
|---|---|---|---|
| **Ngân hàng** (KB, Shinhan, Hana, Woori) | **RIM / P/B–ROE** | DDM, P/E | ✅ Tốt — đây là "đất nhà" của app |
| **Chứng khoán** (Mirae, Korea Inv, Samsung Sec) | **P/B–ROE / RIM** | P/E, DDM | ✅ Tốt |
| **Bảo hiểm** (Samsung Life, DB Ins) | Embedded Value | P/EV, RIM | ⚠️ Proxy (P/B–ROE), độ tin cậy thấp |
| **Tiện ích** (KEPCO, Korea Gas) | **DDM / RAB** | P/B, EV/EBITDA | ✅ DDM được (nếu trả cổ tức đều) |
| **Viễn thông** (SKT, KT, LG U+) | **DCF + DDM** | EV/EBITDA | ✅ DDM được |
| **Tiêu dùng/Bán lẻ** (Amore, LG H&H) | **DCF** | P/E, PEG | 🟡 DCF-earnings (thô) + P/E |
| **Bán dẫn** (Samsung, SK Hynix) | **Normalized DCF** (mid-cycle) | EV/EBITDA, **P/B–ROE** | 🟡 Dùng P/B–ROE làm cross; coi chừng boom premium |
| **Thép/Hóa chất** (POSCO, LG Chem) | **Normalized DCF** | EV/EBITDA, P/B–ROE | 🟡 P/B–ROE; KHÔNG suy từ năm đỉnh |
| **Pin** (LGES, Samsung SDI) | **DCF + EV/GWh** | EV/EBITDA, EV/Sales | 🔴 Cần mô hình capex — app chưa đủ |
| **Ô tô** (Hyundai, Kia) | **DCF / SOTP** | EV/EBITDA, P/E | 🟡 DCF-earnings (thô) |
| **Đóng tàu/Quốc phòng** | **DCF dựa backlog** | EV/EBITDA, P/E | 🔴 Cần backlog — app chưa đủ |
| **Dược/Biotech** | DCF (có lãi) / **rNPV** (pipeline) | EV/EBITDA | 🔴 Pre-profit cần rNPV — app chưa đủ |
| **Internet/Platform** (NAVER, Kakao) | **SOTP** | DCF, EV/Sales | 🔴 Cần định giá từng mảng — app chưa đủ |
| **Holdco/Chaebol** (Samsung C&T, SK, LG) | **NAV / SOTP** (+ discount) | P/B | 🔴 Cần SOTP — P/B chỉ tham chiếu thô |
| **Giải trí** (HYBE, JYP, SM) | **DCF + scenario** | EV/EBITDA, P/E | 🟡 DCF-earnings (thô) |
| Doanh nghiệp vận hành "bình thường" | **DCF (FCFF)** | P/E, EV/EBITDA | 🟡 DCF-earnings (thô) |

**Chú thích cột cuối:**
✅ = app cho con số đáng tin · 🟡 = app cho con số tham khảo (proxy thô) · 🔴 = cần dữ liệu sâu (capex/backlog/SOTP/pipeline) app chưa lấy → chỉ là tham chiếu, **đừng tin tuyệt đối**.

App đã **tự gợi ý** phương pháp + ghi chú cho từng mã ở mục **💰 Định giá → 🔎 Chi tiết 1 mã**.

---

## Các phương pháp app tính được (giải thích nhanh)

| Method | Công thức cốt lõi | Hợp với | Cảnh báo |
|---|---|---|---|
| **S-RIM** | Fair = BPS + (ROE−Ke)×BPS × w/(1+Ke−w) | Ngân hàng, công ty ROE ổn định | Phụ thuộc chất lượng book; normalize ROE |
| **P/B–ROE (justified)** | Fair = BPS × (ROE−g)/(Ke−g) | Tài chính, tài sản nặng | Vô nghĩa nếu ROE ≤ g (app tự ẩn) |
| **DDM (Gordon)** | Fair = DPS×(1+g)/(Ke−g) | Cổ tức đều: bank, utility, telecom | Vô dụng nếu không trả cổ tức |
| **DDM 2 giai đoạn** | Cổ tức tăng nhanh `n` năm rồi về g | Như trên nhưng đang tăng trưởng | Nhạy với g giai đoạn đầu |
| **DCF earnings (thô)** | Chiết khấu chuỗi EPS | Cảm nhận độ lớn, cross-check | ⚠️ EPS ≠ dòng tiền tự do — rất thô |
| **P/E, P/B vs median ngành** | EPS × median P/E ngành | So tương đối trong nhóm | Chỉ cross-check, không phải base |

**Các tham số (chỉnh ở sidebar):**
- **Ke** (lợi suất yêu cầu) = Rf + Beta × ERP. Mặc định Rf 3.2% (TPCP Hàn 10Y), ERP 6.5%, Beta 1.0 → Ke ≈ 9.7%.
- **g vĩnh viễn**: cap 2.5% (≤ tăng trưởng GDP danh nghĩa dài hạn). Không đặt cao hơn.
- **w (S-RIM)**: 1.0 = siêu lợi nhuận giữ mãi; 0.8–0.9 = phai dần (thận trọng hơn).

---

## Đọc kết quả thế nào cho đúng

- **Upside +/- vừa phải (±15%)**, độ tin cậy Medium+ → tín hiệu rẻ/đắt đáng tham khảo.
- **Fair ≪ giá thị trường** (vd Samsung −50%): KHÔNG phải "app sai". Đây thường là **boom premium** — thị trường đang định giá một siêu chu kỳ (AI/HBM). Dùng tư duy *reverse*: "giá này ngụ ý tăng trưởng/biên bao nhiêu? Có hợp lý không?".
- **Fair ≫ giá thị trường** (upside lớn): coi chừng **value trap** hoặc dữ liệu bị méo (EPS one-off). Kiểm lại trước khi mừng.
- **Độ tin cậy Low**: ngành cần phương pháp app chưa làm tự động (DCF đầy đủ/SOTP/EV/rNPV) → coi như tham chiếu, không phải khuyến nghị mua/bán.

> ⚠️ Đây là công cụ sàng lọc, **không phải khuyến nghị đầu tư**. Luôn đối chiếu báo cáo tài chính gốc (DART/KRX) trước khi quyết định.
