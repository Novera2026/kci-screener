# Hướng dẫn sử dụng KCI Screener (cho người không rành code)

> Mục tiêu: từ con số 0 → mở được app trên MacBook → nhập mã → ra bảng + P/E ngành → tải Excel.
> Làm đúng thứ tự. Mỗi bước có lệnh chính xác để **copy-paste**.

---

## BƯỚC 0 — Chuẩn bị folder

1. Tạo 1 folder tên `kci_screener` (ví dụ trong **Documents**).
2. Bỏ TẤT CẢ các file mình gửi vào chung folder đó:
   `app.py`, `screener.py`, `toss_api.py`, `tickers.csv`, `requirements.txt`,
   `run_app.command`, `verify_toss.py`, `.env.example`, README, các file hướng dẫn.

   ⚠️ Phải nằm **chung 1 folder**, không tách rời.

---

## BƯỚC 1 — Kiểm tra máy đã có Python chưa

1. Mở **Terminal** (bấm `Cmd + dấu cách` → gõ `Terminal` → Enter).
2. Gõ dòng này rồi Enter:

   ```bash
   python3 --version
   ```

3. Kết quả:
   - Hiện `Python 3.x.x` → ✅ có rồi, sang **Bước 2**.
   - Báo lỗi `command not found` → cài Python: vào https://www.python.org/downloads/macos/ tải bản mới nhất, cài như app bình thường (Next → Next). Cài xong làm lại bước này.

---

## BƯỚC 2 — Mở đúng folder trong Terminal

Gõ `cd ` (có dấu cách ở cuối), rồi **kéo thả folder `kci_screener`** từ Finder vào cửa sổ Terminal → Enter.

Ví dụ nó sẽ thành:

```bash
cd /Users/tentaikhoan/Documents/kci_screener
```

Kiểm tra đúng chỗ chưa:

```bash
ls
```

Phải thấy `app.py`, `screener.py`... hiện ra → ✅ đúng folder.

---

## BƯỚC 3 — Cài thư viện (chỉ làm 1 lần)

Copy-paste cả khối này, Enter, đợi 1–3 phút:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Dòng cuối chạy xong, không báo đỏ `ERROR` là OK.
- Từ lần sau **không cần** làm lại bước này.

---

## BƯỚC 4 — Mở app

```bash
streamlit run app.py
```

- Trình duyệt (Safari/Chrome) **tự mở** trang `http://localhost:8501`.
- Nếu không tự mở, copy `http://localhost:8501` dán vào trình duyệt.
- 🎉 Đây chính là app của bạn.

> Muốn **tắt app**: quay lại Terminal, bấm `Control + C`.

---

## BƯỚC 5 — Dùng app

1. Ô bên trái (sidebar): chọn **Nguồn giá ưu tiên**
   - `pykrx` → mặc định, không cần key, chạy ngay.
   - `toss` → realtime chính thức (cần làm Bước 7 trước).
2. Mục **🗂️ Danh mục cổ phiếu** có 3 tab: **📂 Nhóm lớn** (bluechip), **🏷️ Theo chủ đề** (Value-up, Bán dẫn, Pin, Quốc phòng, Cổ tức cao...), **🏭 Ngành KRX (toàn sàn)** (tất cả ~4.400 mã theo 업종 giống Naver). Chọn nhóm, bỏ chọn mã không cần, rồi bấm **📋 Thay vào ô** hoặc **➕ Thêm vào ô** (gộp nhiều nhóm). Danh sách tự điền xuống ô bên dưới.
3. Ô giữa: nhập/sửa danh sách mã, **mỗi dòng 1 mã**. Gõ tên Hàn, tên Anh, hay mã số đều được. Ví dụ:

   ```
   삼성전자
   SK Hynix
   005490
   Hyundai Motor
   ```

4. Bấm nút **🚀 Cào dữ liệu**, đợi thanh tiến trình chạy.
5. Kết quả:
   - **Bảng dữ liệu**: giá, đỉnh/đáy 52 tuần, P/E, P/B, EPS, BPS, cổ tức, nhãn 🟢 rẻ / 🔴 đắt so với median ngành. Tick **📑 Bổ sung tài chính** để thêm doanh thu, LN thuần, nợ, EPS dự tính, đánh giá.
   - **Chỉ số toàn ngành**: P/E toàn ngành (đúng chuẩn ΣVốn hóa/ΣLN ròng), median, dividend yield.
   - **💰 Định giá**: fair value + upside theo phương pháp chuẩn của ngành (RIM/DCF/DDM…).
   - **🔎 Chi tiết 1 mã**: chọn 1 mã → xem mọi phương pháp định giá + **báo cáo tài chính 4 năm/4 quý** (doanh thu, LN, ROE, nợ, EPS…) + thẻ Tổng vốn hóa/Nợ/Tổng tài sản + **đánh giá cân đối theo tiêu chuẩn đầu tư**.
6. Bấm **⬇️ Tải Excel** để lưu file về máy.

---

## BƯỚC 6 (gọn hơn) — Lần sau chỉ cần bấm đúp

Sau khi đã làm Bước 3 một lần:

1. Trong Terminal (đang ở folder), gõ 1 lần:

   ```bash
   chmod +x run_app.command
   ```

2. Từ giờ: vào Finder, **bấm đúp file `run_app.command`** là app tự mở.
   - Lần đầu macOS chặn → **chuột phải vào file → Open → Open**.
   - Có thể kéo file này ra Desktop làm shortcut.

---

## BƯỚC 7 (tùy chọn) — Bật nguồn Toss (giá realtime chính thức)

Chỉ làm nếu bạn muốn dùng Toss Open API.

1. Trong app **Toss증권** trên điện thoại: `더보기` → `Open API` → `신청` → phát hành **Client ID** và **Client Secret**.
2. Trên Mac, trong folder `kci_screener`, copy file `.env.example` thành `.env`:

   ```bash
   cp .env.example .env
   ```

3. Mở file `.env` bằng TextEdit, điền 2 giá trị thật:

   ```
   TOSS_CLIENT_ID=dán_client_id_vào_đây
   TOSS_CLIENT_SECRET=dán_client_secret_vào_đây
   ```

   Lưu lại.
   ⚠️ **Tuyệt đối không gửi file `.env` cho ai, không đăng lên mạng.** Đây là chìa khóa tài khoản.

4. Kiểm tra Toss chạy được:

   ```bash
   python3 verify_toss.py
   ```

   Thấy `Token OK` và giá Samsung hiện ra là thành công. Nó cũng tải `toss_openapi.json` để đối chiếu.
5. Quay lại app (Bước 4-5), chọn nguồn **toss**.

---

## Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| `command not found: python3` | Chưa cài Python — làm lại Bước 1 |
| `No such file or directory` khi chạy app | Chưa `cd` đúng folder — làm lại Bước 2 |
| `streamlit: command not found` | Chưa bật môi trường — gõ `source .venv/bin/activate` rồi chạy lại |
| Bảng ra giá nhưng **P/E trống** | Nguồn fundamentals chưa lấy được lúc đó — thử lại, hoặc đổi nguồn sang `naver` |
| `run_app.command` không mở được | Chuột phải → Open; hoặc chạy lại `chmod +x run_app.command` |
| Mã không resolve được | Gõ thẳng mã số 6 chữ số (vd `005930`) cho chắc |

---

## Tóm tắt siêu gọn (dán lên dán nhớ)

```bash
# Lần đầu:
cd <folder kci_screener>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py

# Lần sau: bấm đúp run_app.command
```
