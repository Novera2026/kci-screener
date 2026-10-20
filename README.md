# KCI Screener — Cào dữ liệu realtime hàng loạt KOSPI/KOSDAQ

Phiên bản hiện đại của VBA scraper: nhập **tên hoặc ticker** → hệ thống tự điền mã → tự kéo data tất cả mã → ra **bảng đầy đủ** + tính **chỉ số toàn ngành** (P/E, P/B, dividend yield...).

## Cài đặt

```bash
pip install -r requirements.txt
```

## Dùng nhanh

```bash
# So sánh P/E ngành ngân hàng (gõ tên tiếng Anh, Hàn, hoặc ticker đều được)
python screener.py "KB Financial" "Shinhan Financial" "Hana Financial" "Woori Financial"

# Không tham số -> chạy demo nhóm ngân hàng
python screener.py
```

Hoặc gọi trong Python:

```python
from screener import run
df, agg = run(
    ["삼성전자", "SK Hynix", "005490", "Hyundai Motor"],   # tên Hàn / Anh / ticker lẫn lộn OK
    out_path="nganh_chip.xlsx",
)
print(agg)   # bảng aggregate theo ngành
```

Kết quả: file Excel 2 sheet — **Stocks** (bảng đầy đủ từng mã) và **Sector** (chỉ số toàn ngành).

## Cách hoạt động (giống VBA, hiện đại hơn)

| VBA cũ | KCI Screener |
|---|---|
| Vùng lưu ticker trong sheet | `tickers.csv` (name_en, name_kr, ticker, sector, market) |
| Nhập tay ticker | Nhập **tên** → tự resolve ra mã (registry + toàn bộ KRX + fuzzy match) |
| IE automation (đã chết từ 6/2022) | pykrx (KRX official) + Naver JSON fallback |
| Ghi từng ô Excel | pandas DataFrame → Excel formatted tự động |
| — | Tự tính aggregate P/E, P/B, median toàn ngành |

## Các cột output

`Mã · Tên · Ngành · Giá · Đỉnh/Đáy 52T · % so đỉnh · Vốn hóa · P/E · P/B · EPS · BPS · Tỷ suất cổ tức · DPS · LN ròng (ước) · Nguồn`

## Chỉ số toàn ngành (sheet Sector)

- **agg_P/E = Σ Vốn hóa / Σ Lợi nhuận ròng** — đây là cách đúng để tính P/E toàn ngành (KHÔNG phải trung bình cộng P/E từng mã, vì trung bình cộng bị méo bởi mã P/E cực đoan / âm).
- **median_P/E** — tham chiếu, ít bị ảnh hưởng ngoại lệ.
- **mean_P/E** — chỉ để so sánh, không khuyến nghị làm chuẩn.
- **agg_P/B, median_P/B, median dividend yield, tổng vốn hóa.**

## Danh mục cổ phiếu (browse — TOÀN SÀN)

Hai tầng dữ liệu:
- **`tickers.csv`** — ~77 bluechip curated: có ngành EN (Bank, Semiconductor…) + cột `theme` (chủ đề) → định giá khuyến nghị tốt nhất + browse theo chủ đề.
- **`tickers_all.csv`** — **toàn bộ KOSPI+KOSDAQ (~4.400 mã)** kèm ngành KRX (업종), cào từ Naver bằng `build_full_registry.py`. `TickerRegistry` tự gộp 2 file.

Trong app, mục **🗂️ Danh mục cổ phiếu** có 3 tab:
- **📂 Nhóm lớn** — bluechip theo ngành EN (định giá tốt nhất).
- **🏷️ Theo chủ đề** (테마별): **285 chủ đề toàn sàn** — cào từ Naver 테마 (2차전지, 반도체 장비, 원자력발전, 로봇, 우주항공, 밸류업…) + chủ đề tuyển chọn (Value-up, HBM/AI, Cổ tức cao). File `themes_all.csv`.
- **🏭 Ngành KRX (toàn sàn)** (업종별): tất cả ~2.600 mã phân theo 79 ngành KRX, giống tab 업종별 của Naver.

Bấm **📋 Thay vào ô** / **➕ Thêm vào ô** để nạp vào ô định giá → 🚀. Nhóm > 40 mã không tự chọn hết (định giá hàng loạt chậm ~0.4s/mã).

Mã ngoài registry vẫn resolve/định giá được; ngành KRX (업종) được map sang bucket EN để vẫn gợi ý đúng phương pháp (vd 은행→Bank, 제약→Pharma).

### Cập nhật danh sách toàn sàn

```bash
python build_full_registry.py    # cào lại tickers_all.csv + themes_all.csv (~3-4 phút)
```

## Thêm mã / chủ đề thủ công

Mở `tickers.csv`, thêm dòng `name_en,name_kr,ticker,sector,market,theme`. Cột **`theme`** chứa nhiều chủ đề ngăn bằng `;` (vd `Bán dẫn;HBM/AI;Value-up`). Khi thêm mã nên verify code qua Naver `stockName` trước để khỏi gắn nhầm.

## Lưu ý nguồn dữ liệu

- **pykrx** (primary): lấy giá & 52w high/low chạy tốt mọi nơi. PER/PBR/EPS/BPS/market-cap lấy từ KRX MDC — chạy ổn trên **máy cá nhân**; một số môi trường server/sandbox chặn endpoint này.
- **Naver JSON** (fallback, `prefer="naver"`): chính nguồn VBA cũ dùng (`m.stock.naver.com/api`, `api.finance.naver.com`). Tự động bù field còn thiếu.
- Nếu một nguồn lỗi/thiếu, hệ thống tự thử nguồn kia và gộp data; cột `Nguồn` ghi rõ đã dùng nguồn nào.

```python
# ép dùng Naver làm chính nếu KRX bị chặn
run(["삼성전자","000660"], prefer="naver")
```

## Toss Securities Open API (nguồn giá realtime chính thức)

Toss API là nguồn **giá realtime chính thức** (OAuth2). Dùng cho **giá hiện tại + 52w (từ candles) + stock master**. Lưu ý: Market Data của Toss **không có sẵn PER/PBR/EPS/BPS** → fundamentals vẫn lấy từ pykrx/Naver, screener tự gộp.

### Thiết lập (làm trên máy bạn)

```bash
pip install -r requirements.txt
cp .env.example .env          # rồi điền Client ID / Secret thật vào .env
python verify_toss.py         # test token + giá + tải openapi spec
```

⚠️ **Bảo mật:**
- Credentials chỉ để trong `.env` (đã có `.gitignore` chặn commit). **Không** hardcode, **không** dán secret vào chat.
- Lấy key: app Toss증권 → 더보기 → Open API → 신청.
- Không có sandbox riêng — test bằng tiền nhỏ/đọc dữ liệu.

### Dùng Toss làm nguồn giá

```python
from screener import run
run(["삼성전자", "SK Hynix", "005490"], prefer="toss")   # Toss giá + pykrx/Naver fundamentals
```

### Endpoint Toss (đã xác minh với spec chính thức 6/2026)

Path thật đã đối chiếu từ `toss_openapi.json` (chạy `verify_toss.py` để tải lại):

| Việc | Endpoint | Ghi chú |
|---|---|---|
| Giá hiện tại | `GET /api/v1/prices?symbols=005930,000660` | **Batch tới 200 mã**, field `lastPrice`, bọc trong `{"result": [...]}` |
| Candles | `GET /api/v1/candles?symbol=005930&interval=1d&count=200` | `interval`: `1d`/`1m`; **count tối đa 200** → không đủ 52T chuẩn |
| Trần/sàn | `GET /api/v1/price-limits?symbol=005930` | `upperLimitPrice`/`lowerLimitPrice` |
| Thông tin mã | `GET /api/v1/stocks?symbols=...` | Phải truyền sẵn list — **không có** endpoint liệt kê toàn bộ theo market |

Trong app, Toss chỉ cấp **giá realtime** (chính xác nhất); 52T + fundamentals vẫn lấy từ pykrx/Naver.

```python
from toss_api import TossClient
TossClient().get_prices(["005930", "000660", "005490"])   # {code: price}, 1 request
```

## Module định giá (RIM / DCF / DDM / multiples)

`valuation.py` định giá deterministic theo **KCI Valuation Master Spec**: S-RIM, P/B–ROE justified, DDM (Gordon + 2 giai đoạn), DCF-earnings (proxy), relative P/E–P/B, kèm engine **`recommend_method(sector)`** chọn phương pháp theo entity type (§2/§8). Xem hướng dẫn chọn method ở **`HUONG_DAN_DINH_GIA.md`**.

```python
from screener import fetch_one, TickerRegistry
from valuation import Assumptions, value_stock
reg = TickerRegistry()
row = fetch_one("105560", registry=reg).to_dict()      # KB Financial
r = value_stock(row, Assumptions())                    # Ke, ROE, mọi method, khuyến nghị
print(r.auto_method, r.fair_value, r.upside_pct, r.confidence)
```

Input `net_income/market_cap/eps/bps` cũng là đầu vào cho pipeline L1 (`kci_raw_financials`, `kci_market_data`).

## Báo cáo tài chính & đánh giá cân đối (`financials.py`)

Lấy báo cáo tài chính **4 năm + 4 quý gần nhất** từ Naver (`/api/stock/{code}/finance/annual|quarter`): doanh thu, LN hoạt động, LN thuần, LN ròng cổ đông, ROE, biên LN, Nợ/VCSH (부채비율), thanh toán nhanh, EPS, BPS, PER, PBR, cổ tức — kèm **EPS dự tính** (cột consensus).

- **Bảng cân đối suy ra**: Nợ, Tổng tài sản, Nợ/Tài sản từ `부채비율 × VCSH` (VCSH ≈ BPS × số cp). Là ước tính.
- **`assess_financials()`** — chấm sức khỏe theo tiêu chuẩn đầu tư: Nợ/VCSH <100%, thanh toán nhanh >100%, ROE >10%, biên LN ròng >0, LN thuần dương & ổn định, doanh thu tăng trưởng → rating 🟢 An toàn / 🟡 Trung bình / 🔴 Cần thận trọng. Ngành tài chính (bank/bảo hiểm/chứng khoán) tự bỏ tiêu chí đòn bẩy.

Trong app: mục **🔎 Chi tiết 1 mã** hiện bảng 4 năm/4 quý + thẻ Tổng vốn hóa/Nợ/Tổng tài sản/Nợ-trên-TS/EPS dự tính + đánh giá. Tick **📑 Bổ sung tài chính vào bảng** để thêm cột tổng quan cho cả bảng (cào thêm/mã).

## Kiểm thử (chạy trước mỗi lần push)

```bash
pip install -r requirements-dev.txt
pytest                 # toán định giá, dự phóng, beta, DART, app khởi động (≈2s, không cần mạng)
KCI_E2E=1 pytest       # thêm bài chạy THẬT: cào mã + bảng định giá + EV/EBIT (cần mạng/DART key)
```

`tests/test_app.py` dùng Streamlit **AppTest** (thực thi toàn bộ script) và **pyflakes** (bắt tên chưa định nghĩa) — chặn lỗi kiểu `NameError` trước khi lên cloud. Phiên bản thư viện được chốt trong `requirements.txt`; nâng cấp thì sửa số ở đó rồi chạy `pytest`.

## 🚀 Siêu cổ phiếu tăng trưởng (trang riêng)

Trang **Sieu co phieu** trong thanh điều hướng quét cổ phiếu đang có cấu trúc tăng theo 3 trụ (logic ở `growth.py`):

- **Biểu đồ** — Trend Template 8 tiêu chí của Minervini (MA50/150/200, đỉnh/đáy 52 tuần, RS 1–99 kiểu IBD) + tín hiệu phụ: tích lũy khối lượng, nền siết, gần điểm mua.
- **Tăng trưởng** — DT/LN thuần quý so cùng kỳ (≥25%), biên LN HĐ mở rộng, DT năm, đồng thuận LN năm tới.
- **Tin tức** — từ khóa xúc tác tích cực/tiêu cực trong tiêu đề tin Naver (tin tổng hợp thị trường tính trung tính).

**Giai đoạn** (lọc đu đỉnh): 🔥 Đã chạy xa = cách MA50 > 25% / tăng > 200% từ đáy 52T / > +100% trong 3 tháng / RSI > 80 → mặc định LOẠI (liệt kê riêng kèm lý do); 🌿 Đang chạy; 🌱 Sớm (gần nền).
**Meta** (nhóm dẫn dắt): xếp hạng 259 chủ đề Naver + ngành theo RS trung vị & % thành viên đạt xu hướng (co nhóm ít mã về trung bình); mặc định chỉ hiện mã thuộc top chủ đề / top 5 ngành.

Điểm tiềm năng = 35% biểu đồ + 35% tăng trưởng + 20% meta + 10% tin tức.

**Rà soát lựa chọn** (lớp kiểm tra cuối, 0–100, hạng A/B/C/D): mỗi mã có câu giải thích *vì sao được chọn* và bảng điểm từng hạng mục (Xu hướng 25 · Điểm vào 20 · Tăng trưởng 25 · Meta 15 · Tin 10 · Dòng tiền 5), trừ điểm rủi ro: LN đột biến từ nền thấp, LN tăng mà DT đứng yên, biên LN thu hẹp, tin nghiêm trọng (유상증자/전환사채/소송/횡령…), RSI > 70, dấu hiệu phân phối, thanh khoản < 10억/phiên, bị sàn cảnh báo (Toss). A ≥ 80 Chọn mạnh · B ≥ 65 Đáng cân nhắc · C ≥ 50 Theo dõi · D Yếu. Quét toàn sàn (vốn hóa ≥ 1.000억, ~1.300 mã) mất khoảng 1 phút, theo 2 bước: lọc biểu đồ toàn bộ → phân tích sâu N mã mạnh nhất.
Toss (tùy chọn): giá realtime + cảnh báo mã của sàn (투자경고, 단기과열, VI…). Toss **không** có tin tức hay BCTC nên các phần đó lấy từ Naver.

## 📰 Tin nóng & chủ đề dẫn dắt (trang riêng)

Trang **Tin nong** (logic ở `hotnews.py`) theo dõi realtime, **tự làm mới** theo chu kỳ 3–15 phút:

- **🔥 Chủ đề nóng** — xếp hạng 264 chủ đề Naver theo % tăng hôm nay, độ rộng (% mã cùng tăng), mã dẫn đầu; xem từng chủ đề: mô tả, thành viên + lý do thuộc chủ đề, tin của chủ đề; nút **gửi cả chủ đề sang trang 🚀 Siêu cổ phiếu** để lọc.
- **⭐ Tin giá trị cao** — gom tin nhanh / tin chính / tin đọc nhiều + tin của các mã dẫn đầu, **chấm điểm giá trị 0–100**: 🟢 xúc tác mạnh (hợp đồng, kết quả kỷ lục, chuyển lãi, phê duyệt, M&A, 상한가) · 🔴 rủi ro (유상증자, CB, kiện tụng) · 🔵 phân tích · ⚪ tin chung/thị trường; cộng điểm khi là 특징주, có số tiền, thuộc chủ đề nóng, mới ≤ 3 giờ. Nhận diện mã theo ranh giới từ tiếng Hàn (không nhầm "하이브리드" thành 하이브).
- **📢 Công bố DART** — công bố chính thức của DN niêm yết, chỉ giữ loại có giá trị (hợp đồng cung cấp, kết quả sơ bộ, chuyển giao công nghệ, cấp phép, cổ phiếu quỹ, M&A…) và rủi ro (phát hành thêm, CB/BW, tham ô, xem xét niêm yết…), kèm % giá hôm nay.
