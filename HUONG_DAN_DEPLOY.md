# Đưa KCI Screener lên web (chạy online) — Streamlit Community Cloud

> Mục tiêu: từ code trên máy → 1 đường link web ai cũng mở được, **miễn phí**.
> Cách chuẩn cho app Streamlit là **Streamlit Community Cloud** (deploy thẳng từ GitHub).

---

## Tóm tắt 3 bước
1. Đẩy code lên GitHub (repo **private** khuyến nghị).
2. Vào https://share.streamlit.io → New app → chọn repo → file `app.py`.
3. Dán **Secrets** (Toss/DART key) vào phần Settings → Secrets. Xong, có link web.

---

## 1. Đẩy code lên GitHub
File `.env` (chứa key) **đã được `.gitignore` chặn** — sẽ KHÔNG bị đẩy lên. Yên tâm.

```bash
cd /Users/sun/Kstock
git init
git add .
git commit -m "KCI Screener"
gh repo create kci-screener --private --source=. --push
```
(Đổi `--private` thành `--public` nếu muốn công khai. Khuyến nghị **private**.)

## 2. Tạo app trên Streamlit Cloud
1. Vào https://share.streamlit.io, đăng nhập bằng chính GitHub đang dùng.
2. **New app** → chọn repo `kci-screener`, branch `main`, **Main file path** = `app.py`.
3. Bấm **Deploy**. Lần đầu cài thư viện ~2–4 phút.

## 3. Nạp Secrets (key Toss/DART)
Trong trang app: **Settings (⋮) → Secrets** → dán theo mẫu (điền key thật):
```toml
TOSS_CLIENT_ID = "..."
TOSS_CLIENT_SECRET = "..."
DART_API_KEY = "..."
```
Streamlit tự biến các dòng này thành biến môi trường → code đọc y hệt lúc chạy local,
**không cần sửa gì**. (Xem mẫu trong `.streamlit/secrets.toml.example`.)

> Không có key cũng chạy được: app tự fallback Naver (chỉ thiếu giá realtime Toss và
> phần 5 năm thực + cân đối gốc của DART).

---

## ⚠️ Lưu ý quan trọng (đọc kỹ)

1. **Bảo mật:** key Toss bạn từng dán trong chat coi như đã lộ — **nên phát hành lại key mới**
   (Toss증권 → Open API) trước khi đưa lên web, rồi chỉ dán key mới vào Secrets.
   Không bao giờ commit `.env` hay `secrets.toml` thật.

2. **Link app là CÔNG KHAI** dù repo private: ai có link đều mở được giao diện (dữ liệu hiển thị
   là số liệu thị trường công khai nên không sao). Muốn giới hạn người xem cần bản trả phí của Streamlit.

3. **Nguồn dữ liệu trên server cloud:**
   - `pykrx` (KRX) **bị chặn** trên môi trường server (đã biết) → trên cloud nên để **Nguồn giá = naver**.
   - `Naver` là xương sống — thường chạy được từ server nước ngoài, nhưng có thể chậm/giới hạn hơn ở nhà.
     Nếu lên cloud mà bảng trống, thử lại hoặc giảm số mã mỗi lần.
   - `OpenDART` là API mở toàn cầu → chạy tốt từ cloud.

4. **Giới hạn bản free:** ~1GB RAM, app "ngủ" sau thời gian không ai dùng (mở lại tự thức dậy).
   Tránh định giá quá nhiều mã (>60–80) một lần để khỏi quá tải.

5. **Cập nhật về sau:** chỉ cần `git push`, Streamlit Cloud tự deploy lại bản mới.

---

## Phương án khác (nâng cao, không bắt buộc)
- **Hugging Face Spaces** (cũng free, chọn SDK = Streamlit) — tương tự, dùng khi muốn dự phòng.
- **Render / Railway / Fly.io** — chạy bằng Docker, kiểm soát nhiều hơn nhưng phức tạp hơn, có thể tốn phí.
- Nếu cần **giới hạn người truy cập**: thêm 1 lớp mật khẩu đơn giản bằng `st.text_input(type="password")`
  so với 1 secret — mình làm thêm được nếu bạn muốn.
