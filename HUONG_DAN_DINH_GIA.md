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
- **Ke** (lợi suất yêu cầu) = Rf + Beta × ERP. Rf LIVE (TPCP Hàn 10Y, FRED), ERP mặc định **4,87%** (ERP tổng cho Hàn Quốc của Damodaran, 01/2026 — cùng nguồn với KCI-Valuation-Lab; trước 06/11/2026 là 6,5%). Beta riêng từng mã (52 tuần vs KOSPI) được kẹp 0,5–1,8 rồi **điều chỉnh Blume** 0,67·β + 0,33, vì beta hồi quy 2026 bị nhóm bán dẫn chi phối.
- **g vĩnh viễn**: cap 2.5% (≤ tăng trưởng GDP danh nghĩa dài hạn). Không đặt cao hơn.
- **w (S-RIM)**: 1.0 = siêu lợi nhuận giữ mãi; 0.8–0.9 = phai dần (thận trọng hơn).

---

## Đọc kết quả thế nào cho đúng

- **Upside +/- vừa phải (±15%)**, độ tin cậy Medium+ → tín hiệu rẻ/đắt đáng tham khảo.
- **Fair ≪ giá thị trường** (vd Samsung −50%): KHÔNG phải "app sai". Đây thường là **boom premium** — thị trường đang định giá một siêu chu kỳ (AI/HBM). Dùng tư duy *reverse*: "giá này ngụ ý tăng trưởng/biên bao nhiêu? Có hợp lý không?".
- **Fair ≫ giá thị trường** (upside lớn): coi chừng **value trap** hoặc dữ liệu bị méo (EPS one-off). Kiểm lại trước khi mừng.
- **Độ tin cậy Low**: ngành cần phương pháp app chưa làm tự động (DCF đầy đủ/SOTP/EV/rNPV) → coi như tham chiếu, không phải khuyến nghị mua/bán.

### Cổng tin cậy (06/11/2026)

- **Lõi nội tại sổ sách = trung vị của S-RIM, P/B-ROE và RIM nhiều giai đoạn** (cùng nền ROE: hiện tại, hoặc mid-cycle
  với ngành chu kỳ). **Khi ROE < Ke, không giả định tăng trưởng** (g = 0): P/B hợp lý = ROE/Ke, thay vì
  (ROE−g)/(Ke−g) tiến về 0 khi ROE ≈ g (Doosan Enerbility từng ra 0,02× giá) — tăng trưởng ở ROE dưới chi phí vốn là
  phá hủy giá trị, cùng nguyên tắc với KCI-Valuation-Lab.
- **△ tam giác hóa**: nội tại lệch trung vị các phép neo peer (P/E, P/E dự phóng, P/B so với ngành) quá 2× → giá trị
  hợp lý = 50% nội tại + 50% peer, độ tin cậy Thấp (cùng luật với lab: phương pháp chính giữ ≥ 50%).
- **⚪ không công bố** (cột Fair value để "—"): giá trị hợp lý < 0,4× hoặc > 2,5× giá; nội tại và peer lệch > 4× (một
  trong hai mô hình hỏng — trộn 50/50 chỉ ra nửa phép neo); hoặc thiếu giá. Cột "Giá hợp lý (P/E)" ở bảng chính qua
  cùng cổng. **○ chưa đối chiếu peer**: ngành có < 3 mã trong danh sách nên chưa có phép neo. **— không đủ dữ liệu**.
  Mô hình dựa trên ROE/EPS quá khứ không giải thích được giá (tăng trưởng, chu kỳ, tái cấu trúc). Số thô vẫn xem
  được ở Chi tiết 1 mã (`fair_raw`) và bảng phương pháp. Backtest chấm số thô để không mất các điểm cực trị.

> ⚠️ Đây là công cụ sàng lọc, **không phải khuyến nghị đầu tư**. Luôn đối chiếu báo cáo tài chính gốc (DART/KRX) trước khi quyết định.

---

## 🧮 Trang "Quy trình định giá" (giai đoạn 1 — 09/11/2026)

Trang riêng cho **1 mã**, đi theo thứ tự (logic ở `pipeline.py`):

1. **Nhận diện** — nhóm định giá theo ngành KRX, mô hình chính/đối chiếu, ngành chu kỳ?
2. **Dữ liệu** — kiểm bằng số: số năm BCTC, số năm DART, BPS > 0, EPS×số cp khớp LN, ROE công bố khớp EPS/BPS,
   độ mới BCTC năm/quý, beta. Có ❌ → trần độ tin cậy **Low**; ≥ 2 ⚠️ → **Medium**.
3. **Phân tích** — tăng trưởng, biên, ROE, nợ, tỷ lệ cổ tức từng năm; đánh dấu năm bất thường (ROE lệch > 2× trung vị).
4. **Dự phóng 3–5 năm (sửa trực tiếp)** — tăng trưởng DT, biên LN ròng, tỷ lệ cổ tức từng năm. Năm 1 dùng quý thực đã
   công bố. **Cổng dự phóng:** tăng trưởng −30%…+40%/năm, biên ≤ max lịch sử + 10 điểm và ≤ 50%, ROE ≤ 60%, BPS > 0.
   Chỉ số quý thực công bố mới nới được dải năm 1. Vi phạm → RIM/DDM tường minh bị loại khỏi kết luận.
5. **Chi phí vốn** — Rf, β (Blume), ERP, Ke, Kd, WACC (nợ vay DART).
6. **Định giá** — lõi KCI (như bảng chính) + **RIM tường minh** và **DDM tường minh** trên dự phóng + P/E, P/B × trung vị
   peer (top vốn hóa cùng ngành KRX, bỏ chính mã) + FCFF (DART).

**Kết luận:** mỗi mô hình phải nằm trong 0,4–2,5× giá và 0,25–4× phép neo peer. Giá trị hợp lý = trung vị các mô
hình **chính** qua cổng; khoảng = min–max mọi mô hình qua cổng. **High** chỉ khi ≥ 2 mô hình chính lệch nhau ≤ ±20%
và nằm trong 0,67–1,5× phép neo peer. Tải báo cáo `.md` và bảng tính `.xlsx` ở tab cuối.

### Giai đoạn 2 (10/11/2026): kịch bản, độ nhạy, football field, nhật ký, báo cáo in PDF

7. **Kịch bản Bear/Base/Bull** — dịch giả định bước ④ (tăng trưởng ± điểm %, biên × hệ số; sửa được) rồi chạy lại
   **đúng quy trình và cổng số**. Năm 1 đã có quý thực thì giữ nguyên. Giá trị kỳ vọng Σ xác suất × giá trị chỉ công
   bố khi cả 3 kịch bản qua cổng **và** có mô hình chính dùng dự phóng (dự phóng bị chặn → kịch bản không đổi gì).
   **Độ nhạy Ke ±1 điểm × g 1–2,5%.** Mỗi ô giữ **cùng bộ mô hình chính** của trường hợp gốc. Có mô hình rơi khỏi
   cổng, hoặc lõi chuyển sang 50/50 với peer, thì ô đó là ⚪ chứ không đổi sang bộ khác. (Ví dụ: nếu ô Ke +0,5
   đổi bộ mô hình, giá trị lại TĂNG — sai chiều.)
8. **Football field** — chỉ vẽ mô hình đã qua cổng, cộng giá 52 tuần, dải kịch bản, dải độ nhạy; vạch đỏ = giá.
   **Nhật ký định giá** — nút 📓 ở tab Kết luận lưu vào `valuation_log.csv` (không đưa lên git; trên Streamlit Cloud
   sẽ mất khi App khởi động lại). Bảng theo dõi: giá đã đi bao nhiêu % và đã đi được bao nhiêu % quãng về giá trị hợp lý.

Báo cáo `.html` mở trong trình duyệt → In (⌘P) → Lưu PDF.

### Giai đoạn 3 (10/11/2026): mô hình chuyên ngành — tab 🏭 (logic ở `sector_models.py`)

App **không tự lấy** được đầu vào chính của 3 nhóm này. Anh nhập từ 사업보고서 DART / IR; mọi đầu vào qua **cổng số**.

| Nhóm (tự chọn theo ngành) | Đầu vào | Kết quả | Cổng số |
|---|---|---|---|
| **Đóng tàu, quốc phòng — Backlog** | 수주잔고 (억), số năm đốt, biên LN trên backlog, book-to-bill | Thành **giả định bước ④** (DT = backlog đốt đều + đơn mới) | backlog ≤ 8 năm DT · 1–6 năm đốt · biên ≤ max lịch sử + 10 điểm · book-to-bill 0–3 |
| **Pin — Công suất GWh** | GWh hiện tại / năm cuối, hiệu suất, giá bán 억/GWh, biên; EV/GWh tùy chọn | Thành **giả định bước ④** + mô hình đối chiếu EV/GWh | hiệu suất 0–100% · giá bán 0,5–1,5× giá ngụ ý từ doanh thu thực · biên như trên |
| **Dược, biotech — rNPV** | Bảng thuốc: pha, doanh số đỉnh, năm ra mắt, PoS, biên/royalty, chi phí PT còn lại; tiền ròng, chi phí chung | **Mô hình chính** ở ⑥ (chiết khấu Ke riêng mã) | PoS ≤ 1,5× chuẩn pha (BIO/Informa 2011–2020) · biên ≤ 60% · năm ra mắt ≥ năm nay |

Biên mặc định = biên LN ròng **năm gần nhất** (đơn mới ký theo giá hiện tại). Giả định từ 🏭 đi qua đúng cổng dự
phóng, kịch bản và độ nhạy như mọi giả định khác.
