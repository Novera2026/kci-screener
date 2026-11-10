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

### Lãi/lỗ bất thường một lần (10/11/2026)

LN thuần khác LN HĐ × (1 − thuế) là **chuyện thường** (30–70% số kỳ ở nhiều ngành): lãi công ty liên kết (Hyundai
Mobis ~1,2× LN HĐ đều đặn), lãi tiền gửi của DN nhiều tiền. Vì vậy App **không** thay LN thuần bằng LN HĐ hàng loạt.

Một quý bị coi là bất thường khi **LN thuần > doanh thu**, hoặc khi đủ cả hai điều kiện:
- Tỷ lệ LN thuần/LN HĐ của quý nằm ngoài 0,67–1,5× tỷ lệ trung vị năm của **chính mã đó**. Cần ≥ 3 năm lịch sử,
  thiếu thì dùng 0,78.
- Phần lệch lớn hơn 15% doanh thu quý.

Khi đó:
- Số **đã công bố giữ nguyên** (lãi đó có thật, vào vốn chủ một lần).
- Các quý **chưa công bố** không ngoại suy phần một lần (Hynix Q2/2026: LN 93,9조 so với lõi 51,1조).
- Năm 2+ của dự phóng xuất phát từ **biên lõi**, và với DN phi tài chính không vượt trần 50% của cổng.

Bước ② có dòng kiểm "Lãi/lỗ bất thường (quý)"; bước ③ đánh dấu năm có LN thuần lệch LN HĐ. Sửa này cũng áp vào dự
phóng 1 năm của trang chính (`financials._ytd_run_rate`). Backtest không đo được phần này vì không có BCTC quý
point-in-time.

---

## Quy trình 5 bước (10/11/2026) — trang 🧮 sắp lại theo thứ tự nghiên cứu

| Bước | Nội dung | Module |
|---|---|---|
| **① Đối thủ trong & ngoài nước** | Bảng P/E, P/B, tăng trưởng, biên, ROE, cổ tức: mã vs đối thủ **trong nước** (gợi ý Naver ∪ top vốn hóa cùng ngành, chỉ cổ phiếu thường) và **nước ngoài** (Naver world, đã quy KRW; sửa danh sách ở thanh bên). Vị thế từng chỉ số + chiết khấu so với quốc tế. | `peers.py` |
| **② Phân tích & bóc tách** | Kiểm số liệu, tỉ số từng năm; DuPont (biên × vòng quay × đòn bẩy); cơ cấu LN trước thuế (HĐ / tài chính / liên kết / khác); dòng tiền (CFO/LN, FCF, capex); vốn lưu động. | `analysis.decompose` |
| **③ Chất lượng tài sản** | Accruals, CFO/LN, phải thu & tồn kho so với doanh thu, lợi thế TM + vô hình/VCSH, Piotroski F-score (8 tiêu chí), Altman Z, khả năng trả lãi, nợ ròng/LN HĐ — mỗi chỉ tiêu ngưỡng số cố định. Hạng A–D; ≥ 2 ❌ tối đa C; ngành tài chính tối đa B. | `analysis.quality` |
| **④ Định giá theo ngành** | Như trước: 🏭 chuyên ngành, dự phóng, chi phí vốn, các mô hình + cổng số, kịch bản & độ nhạy, football field & nhật ký. Phép neo dùng **đúng đối thủ trong nước ở ①**. | `pipeline`, `sector_models` |
| **⑤ Kết luận & tổng quan** | 4 trụ 0–100 (định giá, sức khỏe tài chính, chất lượng tài sản, vị thế cạnh tranh) + kết luận theo **quy tắc số**. | `pipeline.overall_assessment` |

Đối thủ nước ngoài **chỉ để so sánh**, không vào phép neo định giá (chiết khấu Hàn Quốc kinh niên). Kết luận không được
🟢 khi chưa công bố giá trị, chất lượng tài sản hạng D / ≥ 3 ❌, hoặc ngành tài chính (chưa chấm được nợ xấu).

**Hồ sơ:** mỗi bước ghi vào `dossiers/{mã}_{ngày}.json` (`dossier.py`, không đưa lên git; mất khi Streamlit Cloud
khởi động lại).

**Giới hạn đã biết:** ô tô có công ty tài chính hợp nhất (Hyundai Motor + Hyundai Capital) bị chấm thấp ở Altman Z /
khả năng trả lãi vì nợ của mảng tài chính; F-score thiếu tiêu chí phát hành cổ phiếu; ngân hàng không có nợ xấu.

## 🔍 Trang sàng lọc cổ phiếu rẻ (`screen.py`)

① Vũ trụ: top vốn hóa / ngành KRX / chủ đề / nhập tay → ② lọc rẻ: P/E, P/B ÷ trung vị ngành **trong vũ trụ** (≥ 3 mã,
thiếu thì so cả danh sách), P/E tối đa, cổ tức tối thiểu, bắt buộc có lãi → ③ định giá sâu K mã rẻ nhất bằng **đúng quy
trình 🧮** (giả định mặc định, không đối thủ nước ngoài cho nhanh), lưu hồ sơ → ④ xếp hạng: kết luận → chênh lệch →
chất lượng; mở mã bất kỳ trong 🧮 để đi đủ 5 bước. Backtest mới kiểm chứng phần **định giá**; lọc P/E thấp và điểm
tổng hợp chưa được kiểm chứng riêng.

### Bộ tiêu chuẩn sàng lọc có sẵn (10/11/2026) — trang 🔍 chạy bằng 1 nút

Chọn bộ tiêu chuẩn → **▶️ Chạy sàng lọc**. Vũ trụ mặc định: top 200 vốn hóa cả hai sàn. App làm hết phần còn lại:
- **Tầng 1** — dữ liệu thị trường trên cả vũ trụ.
- **Định giá sâu** — tối đa 20 mã qua lọc, bằng đúng quy trình 🧮.
- **Tầng 2** — kiểm bằng BCTC DART.
- **Xếp hạng** — mã đạt tầng 2 lên trước.

| Bộ | Tầng 1 | Tầng 2 | Nguồn |
|---|---|---|---|
| **KCI (mặc định)** | có lãi, ROE ≥ 8%, P/E & P/B ÷ ngành ≤ 0,8 | chất lượng TS ≥ C, có giá trị qua cổng | quy tắc nhà, phần định giá đã backtest |
| **Graham phòng thủ** | P/E ≤ 15, P/B ≤ 1,5, P/E×P/B ≤ 22,5, có cổ tức, vốn hóa ≥ 1.000 tỷ | thanh toán hiện hành ≥ 1,5, không năm lỗ | Graham, *The Intelligent Investor* ch.14 |
| **Số Graham** | giá ≤ √(22,5·EPS·BPS), xếp theo biên an toàn | chất lượng TS ≥ C | Graham |
| **Value-up Hàn Quốc** | P/B ≤ 1, ROE ≥ 10% | chất lượng TS ≥ C, nợ/VCSH ≤ 200% (trừ tài chính) | Corporate Value-up (FSC/KRX 2024) |
| **Cổ tức cao bền vững** | cổ tức ≥ 4%, P/E ≤ 15, tỷ lệ chi trả ≤ 70%, ROE ≥ 6% | không năm lỗ, chất lượng TS ≥ C | phổ biến |
| **Magic Formula** | bỏ tài chính & tiện ích, vốn hóa ≥ 500 tỷ, top 30 theo hạng E/P + hạng ROE | chất lượng TS ≥ C | Greenblatt (2005) — biến thể E/P, ROE thay EBIT/EV, ROIC |
| **Piotroski** | 20% P/B thấp nhất | F-score ≥ 7/8 | Piotroski (2000) — 8/9 tiêu chí |

Ngưỡng chỉnh được trong "⚙️ Chỉnh ngưỡng", nhưng không bắt buộc. Các bộ là quy tắc kinh điển, **chưa được backtest
riêng trên thị trường Hàn** trong App. Chỉ phần định giá của quy trình đã kiểm chứng (IC +0,106).

### Điều hướng trên thanh bên (11/11/2026)

5 bước (và 6 phần con của bước ④) nằm ở **🧭 Quy trình** trên thanh bên. Ngay dưới là giá, giá trị hợp lý và kết luận
của mã, luôn hiện. Cuối mỗi phần có nút **← trước / tiếp →** để đi tuần tự qua 10 phần.

Mọi bước vẫn được tính đầy đủ, chỉ phần đang chọn được hiện ra, nên ô đã sửa không mất khi đổi bước.

**Giả định** gom vào mục thu gọn "⚙️ Giả định chi phí vốn & dự phóng". Dòng tóm tắt cho biết đang dùng mặc định KCI
hay đã chỉnh mấy mục, kèm Ke thực tế; có nút ↺ về mặc định. Danh sách đối thủ (số mã trong nước, mã nước ngoài) nằm
trong bước ①.

### Số liệu thị trường luôn mới (11/11/2026)

Kết quả định giá **không lưu để dùng lại**. App tách hai loại dữ liệu:

| Loại | Gồm | Cách lấy |
|---|---|---|
| **Tĩnh** | BCTC Naver/DART, danh sách đối thủ và BCTC của họ | Cache ≤ 6 giờ. Chỉ đổi khi DN nộp báo cáo |
| **Thị trường** | Giá, vốn hóa, P/E–P/B đối thủ trong & ngoài nước, beta 52 tuần, Rf (FRED) | **Lấy mới mỗi lần bấm Bắt đầu** hoặc 🔄 Cập nhật; không cache |

- Trong **một** lần phân tích (sửa giả định, đổi bước), App giữ nguyên ảnh chụp thị trường đó để các con số nhất quán.
  Thời điểm lấy hiện dưới tên mã.
- Rf: ô Rf tự theo giá trị FRED mới, trừ khi anh đã tự sửa.
- Trang 🔍 không cache giá. Kết quả sàng lọc ghi giờ tính; bấm "Mở trong 🧮" sẽ định giá lại với số liệu mới.
- Hồ sơ `dossiers/` và nhật ký chỉ là bản ghi, không bao giờ được đọc lại để định giá.
- Trang chính (`app.py`): Rf, KOSPI, beta từng mã, vốn hóa công ty con (SOTP) chỉ giữ trong **một lần bấm 🚀 Cào &
  định giá**. Bộ nhớ nằm trong phiên của từng người dùng (trước đây cache chung tới 24 giờ) và bị xóa mỗi lần chạy mới.
