# 🤖 Bot săn cổ phiếu (Telegram)

Mỗi **ngày làm việc lúc 16:30 giờ Hàn**, bot chạy quy trình sàng lọc và định giá của App. Sau đó nó gửi báo cáo về
Telegram, kèm file Excel. Ngày sàn nghỉ thì bot không gửi.

**Bot làm gì mỗi lần chạy** (`bot_hunt.py`):

1. Lấy top 200 vốn hóa cả hai sàn, với giá, P/E, P/B **lấy mới**.
2. Áp 5 bộ tiêu chuẩn của trang 🔍: KCI, Value-up, Magic Formula, Cổ tức cao, Graham phòng thủ. Tầng 1 chọn tối đa
   15 mã mỗi bộ.
3. Định giá sâu bằng đúng quy trình trang 🧮, rồi kiểm tầng 2 của từng bộ bằng BCTC DART.
4. Lập **danh sách săn**: mã đạt tầng 2 của ít nhất 1 bộ, có giá trị hợp lý qua cổng số và đang rẻ hơn giá trị ước
   tính. Xếp theo số bộ đạt → kết luận 🟢/🟡 → % chênh.
5. So với hôm trước: **🆕 mã mới vào · 🔁 đổi kết luận · ❌ rớt ra**.

Chạy thử 10/11/2026 mất khoảng 45 giây: 45 mã được định giá sâu, 21 mã trong danh sách săn.

## Cài đặt (làm một lần)

### 1. Tạo bot trên Telegram

1. Mở Telegram, tìm **@BotFather**, gửi `/newbot`.
2. Đặt tên, ví dụ `KCI Săn cổ phiếu`, và username kết thúc bằng `bot`, ví dụ `kci_san_bot`.
3. BotFather gửi lại một **token** dạng `123456789:AA...`. **Giữ bí mật token này.**

### 2. Thử trên máy

Mở file `.env` trong thư mục dự án, tự dán token vào:

```
TELEGRAM_BOT_TOKEN=<token BotFather gửi>
```

Mở bot vừa tạo trên Telegram, bấm **Start**. Rồi lấy chat id:

```bash
python bot_hunt.py --chat-id
```

Thêm dòng `TELEGRAM_CHAT_ID=<số vừa in ra>` vào `.env`, rồi gửi thử một báo cáo (`--force`: chạy cả ngày sàn nghỉ):

```bash
python bot_hunt.py --force
```

Chỉ muốn xem tin nhắn mà không gửi:

```bash
python bot_hunt.py --dry-run --force
```

### 3. Bật chạy tự động (GitHub Actions — máy không cần bật)

Trên GitHub, mở repo `kci-screener` → **Settings → Secrets and variables → Actions → New repository secret**. Thêm 3
secret:

| Tên | Giá trị |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token từ BotFather |
| `TELEGRAM_CHAT_ID` | chat id ở bước 2 |
| `DART_API_KEY` | key DART đang dùng trong `.env` |

Kiểm tra ngay: tab **Actions → Bot săn cổ phiếu → Run workflow**. Sau đó bot tự chạy 16:30 KST thứ 2–6. Lịch nằm ở
`.github/workflows/bot_hunt.yml`.

## Tùy chỉnh (biến môi trường / GitHub Variables)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `BOT_PRESETS` | `kci,valueup,magic,dividend,graham` | Các bộ tiêu chuẩn (thêm `graham_number`, `piotroski`) |
| `BOT_UNIVERSE` | `200` | Số mã top vốn hóa |
| `BOT_MAX_DEEP` | `15` | Số mã tối đa mỗi bộ đưa vào định giá sâu |

## Lưu ý

- Mỗi lần chạy đều lấy số liệu thị trường mới (giá, beta, Rf thời gian thực). Bot không dùng lại kết quả định giá cũ.
  Trạng thái "hôm trước" chỉ dùng để báo mã mới/rớt.
- Backtest 2021–25 cho thấy chỉ **phần định giá** có bằng chứng (IC +0,116 / +0,086). Các bộ tiêu chuẩn **chưa thắng
  trung bình danh sách gốc có ý nghĩa thống kê** (xem `HUONG_DAN_BACKTEST.md`).
- Danh sách là để **nghiên cứu tiếp, không phải khuyến nghị đầu tư**. Mở mã trong trang 🧮 để đi đủ 5 bước trước khi
  quyết định.
- Nếu lộ token: gửi `/revoke` cho @BotFather để lấy token mới, rồi cập nhật `.env` và GitHub Secret.
