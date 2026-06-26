"""
KCI Screener — App giao diện (Streamlit) chạy local trên macOS.

Chạy:
    pip install -r requirements.txt
    streamlit run app.py

Mở tự động ở http://localhost:8501
"""
from __future__ import annotations
import io
import os
import pandas as pd
import streamlit as st

# Trên Streamlit Cloud KHÔNG có .env → nạp key từ Streamlit Secrets vào os.environ
# để dart_api / toss_api (đọc os.environ) hoạt động. Local có .env thì bỏ qua.
try:
    for _k in ("DART_API_KEY", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"):
        if _k not in os.environ and _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

from screener import (TickerRegistry, fetch_batch, sector_aggregates,
                      export_excel, COLUMN_KR)
from valuation import Assumptions, value_batch, value_stock, recommend_method, _cap_conf
try:
    import coverage
except Exception:
    coverage = None
from financials import (fetch_financials, last_n, latest_actual, forward_eps,
                        balance_sheet, balance_sheet_of, assess_financials,
                        build_annual_series, build_quarter_series,
                        project_forecast, forecast_eps, LABELS_VI)

try:
    import dart_api
    dart_unavailable = not dart_api.available()
except Exception:
    dart_unavailable = True

st.set_page_config(page_title="KCI Screener", page_icon="📈", layout="wide")

# ---- Header ----
st.title("📈 KCI Screener — KOSPI/KOSDAQ")
st.caption("Nhập tên hoặc ticker → tự cào data hàng loạt → bảng đầy đủ + P/E toàn ngành")

@st.cache_resource
def _get_registry():
    return TickerRegistry()


reg = _get_registry()

# ---- Sidebar: cấu hình ----
with st.sidebar:
    st.header("⚙️ Cấu hình")
    prefer = st.selectbox(
        "Nguồn giá ưu tiên",
        ["pykrx", "toss", "naver"],
        help="toss = Toss Open API (cần .env). Fundamentals luôn lấy từ pykrx/Naver.",
    )
    if prefer == "toss":
        ok = bool(os.environ.get("TOSS_CLIENT_ID")) or os.path.exists(".env")
        st.success("Đã thấy cấu hình Toss (.env)") if ok else \
            st.warning("Chưa có .env cho Toss — sẽ tự fallback pykrx/Naver")

    st.divider()
    st.subheader("💰 Giả định định giá")
    st.caption("House Korea (KRW nominal) — chỉnh nếu cần. Để mặc định cũng OK.")
    rf = st.slider("Rf — TPCP Hàn 10Y (%)", 1.0, 6.0, 3.2, 0.1) / 100
    erp = st.slider("ERP — phần bù rủi ro VCSH (%)", 4.0, 9.0, 6.5, 0.1) / 100
    beta = st.slider("Beta (kẹp 0.5–1.8)", 0.5, 1.8, 1.0, 0.05)
    g_term = st.slider("g vĩnh viễn (%) — cap 2.5", 0.0, 2.5, 2.0, 0.1) / 100
    with st.expander("Tham số nâng cao (DCF/DDM 2 giai đoạn)"):
        g_high = st.slider("g giai đoạn đầu (%)", 0.0, 25.0, 8.0, 0.5) / 100
        years_high = st.slider("Số năm tăng trưởng cao", 3, 10, 5, 1)
        persistence = st.select_slider(
            "Hệ số duy trì siêu LN (S-RIM, w)", options=[0.6, 0.8, 0.9, 1.0], value=0.9)
    assume = Assumptions(rf=rf, erp=erp, beta=beta, g_terminal=g_term,
                         g_high=g_high, years_high=years_high, persistence=persistence)
    st.caption(f"→ Ke = {assume.ke()*100:.2f}% · g = {assume.g_term_capped()*100:.2f}%")

# ---- Danh mục cổ phiếu (browse theo ngành / chủ đề, giống Naver) ----
if "raw_input" not in st.session_state:
    st.session_state["raw_input"] = "삼성전자\nSK Hynix\n005490\nHyundai Motor"


# map tra cứu nhanh (tránh lọc df 4.4k dòng mỗi lần render multiselect)
_NAME_MAP = dict(zip(reg.df["ticker"],
                     reg.df["name_en"].fillna(reg.df["name_kr"]).fillna(reg.df["ticker"])))


def _label(t: str) -> str:
    return f"{t} · {_NAME_MAP.get(t, '?')}"


def _names_for(tickers: list[str]) -> list[str]:
    return [_NAME_MAP.get(t, t) for t in tickers]


def _load(tickers: list[str], append: bool):
    picked = "\n".join(_names_for(tickers))
    if append:
        cur = st.session_state["raw_input"].strip()
        st.session_state["raw_input"] = (cur + "\n" + picked).strip() if cur else picked
    else:
        st.session_state["raw_input"] = picked


def _clear():
    st.session_state["raw_input"] = ""


MAX_DEFAULT = 40  # nhóm > ngưỡng này: không tự chọn hết (tránh định giá hàng loạt chậm)


def _group_picker(tickers: list[str], key: str, label: str):
    """Multiselect + 2 nút nạp cho 1 nhóm mã."""
    n = len(tickers)
    default = tickers if n <= MAX_DEFAULT else []
    if n > MAX_DEFAULT:
        st.caption(f"⚠️ Nhóm lớn ({n} mã) — chưa chọn sẵn. Tự chọn mã cần (định giá "
                   f"nhiều mã sẽ chậm, ~0.4s/mã).")
    sel = st.multiselect(label, tickers, default=default, format_func=_label, key=key)
    c1, c2, _ = st.columns([1, 1, 2])
    c1.button("📋 Thay vào ô", key=key + "_rep", use_container_width=True,
              on_click=_load, args=(sel, False))
    c2.button("➕ Thêm vào ô", key=key + "_add", use_container_width=True,
              on_click=_load, args=(sel, True))


# build map: sector EN -> tickers, chủ đề -> tickers, 업종 KRV -> tickers (cache)
THEMES_ALL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "themes_all.csv")


@st.cache_data
def _build_maps():
    df = reg.df
    reg_tks = set(df["ticker"])
    secs = sorted(df["sector"].dropna().unique())
    ind = (df.dropna(subset=["industry_kr"])
             .groupby("industry_kr")["ticker"].apply(list).to_dict())
    # chủ đề curated (Việt) từ cột theme
    seen: dict[str, set] = {}
    tdf = df[["ticker", "theme"]].dropna(subset=["theme"])
    for tk, theme in zip(tdf["ticker"], tdf["theme"]):
        for x in str(theme).split(";"):
            x = x.strip()
            if x:
                seen.setdefault(x, set()).add(tk)
    # chủ đề Naver (테마, toàn sàn) từ themes_all.csv
    if os.path.exists(THEMES_ALL_PATH):
        tdf2 = pd.read_csv(THEMES_ALL_PATH, dtype=str)
        for theme, tk in zip(tdf2["theme_kr"], tdf2["ticker"]):
            tk = str(tk).zfill(6)
            if tk in reg_tks:
                seen.setdefault(theme, set()).add(tk)
    th_map = {k: sorted(v) for k, v in seen.items()}
    return secs, th_map, ind


@st.cache_data(ttl=1800, show_spinner=False)
def _top_by_cap(market: str, industry_kr: str, n: int) -> list[str]:
    """Top N mã theo vốn hóa (Naver xếp sẵn giảm dần), lọc theo ngành KRX nếu chọn.
    Chỉ lấy cổ phiếu thường (stockEndType='stock'); bỏ ưu đãi/ETF/SPAC qua registry."""
    import requests
    H = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
    markets = ["KOSPI", "KOSDAQ"] if market == "Cả hai sàn" else [market]
    reg_tks = set(reg.df["ticker"])
    want_ind = industry_kr != "— Toàn thị trường —"
    out: list[str] = []
    for mk in markets:
        page, max_pages = 1, (10 if want_ind else 2)
        while page <= max_pages and len(out) < n * (2 if market == "Cả hai sàn" else 1):
            try:
                j = requests.get(f"https://m.stock.naver.com/api/stocks/marketValue/{mk}",
                                 params={"page": page, "pageSize": 100},
                                 headers=H, timeout=10).json()
            except Exception:
                break
            stocks = j.get("stocks") or []
            if not stocks:
                break
            for s in stocks:
                tk = str(s.get("itemCode") or "").zfill(6)
                if s.get("stockEndType") != "stock" or tk not in reg_tks:
                    continue
                if want_ind and reg.industry_of(tk) != industry_kr:
                    continue
                if tk not in out:
                    out.append(tk)
            page += 1
    # gộp 2 sàn thì xếp lại theo thứ tự đã lấy (mỗi sàn đã giảm dần) rồi cắt N
    return out[:n]


sectors, theme_map, ind_map = _build_maps()
# chủ đề sắp theo số mã giảm dần
themes = sorted(theme_map.keys(), key=lambda k: -len(theme_map[k]))
# sắp ngành theo số mã giảm dần, đẩy "기타" (Khác) xuống cuối
industries = sorted(ind_map.keys(), key=lambda k: (k == "기타", -len(ind_map[k])))

st.subheader("🗂️ Danh mục cổ phiếu")
st.caption(f"{len(reg.df)} mã toàn sàn · {len(sectors)} nhóm lớn · {len(themes)} chủ đề "
           f"· {len(industries)} ngành KRX (업종). Chọn nhóm → nạp vào ô định giá.")
tab_sec, tab_theme, tab_ind, tab_top = st.tabs(
    ["📂 Nhóm lớn", "🏷️ Theo chủ đề", "🏭 Ngành KRX (toàn sàn)", "⚡ Top vốn hóa"])

with tab_sec:
    st.caption("Bluechip đã gắn ngành EN + định giá khuyến nghị tốt nhất.")
    sec = st.selectbox("Nhóm ngành", sectors, key="browse_sector")
    _group_picker(reg.df[reg.df["sector"] == sec]["ticker"].tolist(),
                  f"ms_sector_{sec}", "Mã trong nhóm (bỏ chọn mã không cần)")

with tab_theme:
    st.caption("Chủ đề toàn sàn (테마 Naver) + chủ đề tuyển chọn (Value-up, Cổ tức cao…). "
               "Gõ để tìm nhanh.")
    th = st.selectbox("Chủ đề (sắp theo số mã)", themes, key="browse_theme",
                      format_func=lambda k: f"{k} ({len(theme_map[k])})")
    _group_picker(theme_map.get(th, []), f"ms_theme_{th}",
                  "Mã trong chủ đề (bỏ chọn mã không cần)")

with tab_ind:
    st.caption("Toàn bộ cổ phiếu phân theo ngành KRX (업종) — giống tab 업종별 của Naver.")
    ind = st.selectbox("Ngành KRX (sắp theo số mã)", industries,
                       format_func=lambda k: f"{k} ({len(ind_map[k])})", key="browse_ind")
    _group_picker(ind_map.get(ind, []), f"ms_ind_{ind}",
                  "Mã trong ngành (bỏ chọn mã không cần)")

with tab_top:
    st.caption("Lấy nhanh các mã VỐN HÓA LỚN NHẤT (toàn thị trường hoặc trong 1 ngành KRX) "
               "để soi cổ phiếu đầu ngành / tìm hàng rẻ. Nạp vào ô rồi bấm 🚀.")
    t1, t2, t3 = st.columns([1, 2, 1])
    top_mkt = t1.selectbox("Sàn", ["KOSPI", "KOSDAQ", "Cả hai sàn"], key="top_mkt")
    top_ind = t2.selectbox("Ngành KRX", ["— Toàn thị trường —"] + industries,
                           format_func=lambda k: k if k.startswith("—")
                           else f"{k} ({len(ind_map[k])})", key="top_ind")
    top_n = t3.select_slider("Số mã (N)", options=[5, 10, 15, 20, 30, 50], value=10,
                             key="top_n")
    if st.button("🔎 Lấy Top N theo vốn hóa", key="top_fetch"):
        with st.spinner("Đang lấy bảng xếp hạng vốn hóa từ Naver..."):
            st.session_state["top_result"] = _top_by_cap(top_mkt, top_ind, top_n)
    tickers_top = st.session_state.get("top_result", [])
    if tickers_top:
        st.success(f"Tìm thấy {len(tickers_top)} mã vốn hóa lớn nhất.")
        _group_picker(tickers_top, "ms_top", "Top mã (bỏ chọn mã không cần)")
col1, col2 = st.columns([3, 1])
with col1:
    raw = st.text_area(
        "Danh sách mã (mỗi dòng 1 mã — tên Hàn/Anh hoặc ticker đều được)",
        height=160, key="raw_input",
    )
with col2:
    st.write("")
    st.write("")
    run_btn = st.button("🚀 Cào & định giá", type="primary", use_container_width=True)
    st.button("🧹 Xóa ô", use_container_width=True, on_click=_clear)
    st.caption("Mẹo: dán cả danh sách từ Excel cũng được.")

@st.cache_data(ttl=3600, show_spinner=False)
def _fin(ticker: str) -> dict:
    return fetch_financials(ticker)


def _period_label(c: dict) -> str:
    p = c["period"]
    base = f"{p[:4]}.{p[4:6]}" if len(p) >= 6 and p[:4].isdigit() else str(p)
    if c.get("is_forecast"):
        return base + " (Dự phóng)"
    return base + (" (E)" if c.get("is_consensus") else "")


def _cell_fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if isinstance(v, (int, float)):
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        if float(v).is_integer():
            return f"{int(v)}"
        return f"{v:,.2f}"
    return str(v)


def _fin_table_styled(cols: list[dict], fields: list[str]):
    """Bảng chuyển vị có MÀU: cột dự phóng nền xanh nhạt, số âm đỏ."""
    data, fc_cols = {}, []
    for c in cols:
        lbl = _period_label(c)
        data[lbl] = [c.get(f) for f in fields]
        if c.get("is_forecast"):
            fc_cols.append(lbl)
    df = pd.DataFrame(data, index=[LABELS_VI.get(f, f) for f in fields])

    def _hl_col(s):
        return ["background-color:#eef2ff" if s.name in fc_cols else "" for _ in s]

    def _neg(v):
        return "color:#c0392b;font-weight:600" if isinstance(v, (int, float)) and v < 0 else ""

    return (df.style.format(_cell_fmt, na_rep="—")
            .apply(_hl_col, axis=0).map(_neg))


def _fin_table_combined(annual_cols: list[dict], quarter_cols: list[dict],
                        fields: list[str]):
    """GỘP năm + quý vào 1 bảng: cột năm trước (nhãn 'Năm YYYY'), rồi cột quý
    ('Quý YY.MM'). Cột dự phóng nền xanh, số âm đỏ. Field nào quý không có → '—'."""
    data, fc_cols, year_cols = {}, [], []

    def _add(cols, kind):
        for c in cols:
            p = str(c.get("period", ""))
            if kind == "Y":
                lbl = ("Năm " + p[:4]) if p[:4].isdigit() else f"Năm {p}"
            else:
                lbl = "Quý " + (f"{p[2:4]}.{p[4:6]}" if len(p) >= 6 and p[:4].isdigit()
                                else p)
            if c.get("is_forecast"):
                lbl += " (DP)"
            elif c.get("is_consensus"):
                lbl += " (E)"
            while lbl in data:          # tránh trùng nhãn
                lbl += " "
            data[lbl] = [c.get(f) for f in fields]
            if c.get("is_forecast"):
                fc_cols.append(lbl)
            if kind == "Y":
                year_cols.append(lbl)

    _add(annual_cols, "Y")
    _add(quarter_cols, "Q")
    df = pd.DataFrame(data, index=[LABELS_VI.get(f, f) for f in fields])

    def _hl_col(s):
        if s.name in fc_cols:
            return ["background-color:#eef2ff" for _ in s]      # dự phóng: xanh
        if s.name in year_cols:
            return ["background-color:#fafafa" for _ in s]      # cột năm: xám nhạt
        return ["" for _ in s]                                   # cột quý: trắng

    def _neg(v):
        return "color:#c0392b;font-weight:600" if isinstance(v, (int, float)) and v < 0 else ""

    return (df.style.format(_cell_fmt, na_rep="—")
            .apply(_hl_col, axis=0).map(_neg))


# ---- màu cho bảng chính ----
def _c_debt(v):
    if v is None or pd.isna(v):
        return ""
    if v < 100:
        return "background-color:#d7f5dd"
    if v < 200:
        return "background-color:#fff3cd"
    return "background-color:#f8d7da"


def _c_d2a(v):          # Nợ / Tổng tài sản (%): <50 an toàn · <70 vừa · ≥70 rủi ro
    if v is None or pd.isna(v):
        return ""
    if v < 50:
        return "background-color:#d7f5dd"
    if v < 70:
        return "background-color:#fff3cd"
    return "background-color:#f8d7da"


def _c_roe(v):
    if v is None or pd.isna(v):
        return ""
    if v >= 15:
        return "background-color:#b7e9c4"
    if v >= 10:
        return "background-color:#d7f5dd"
    if v < 0:
        return "background-color:#f8d7da"
    return ""


def _c_tag(v):
    if not isinstance(v, str):
        return ""
    if "Rẻ" in v or "🟢" in v or "An toàn" in v:
        return "background-color:#d7f5dd"
    if "Đắt" in v or "🔴" in v or "thận trọng" in v:
        return "background-color:#f8d7da"
    if "Trung bình" in v or "🟡" in v:
        return "background-color:#fff3cd"
    return ""


def _c_neg(v):
    return "color:#c0392b;font-weight:600" if isinstance(v, (int, float)) and v < 0 else ""


# ---- Run: chỉ cào dữ liệu (network) rồi lưu vào session_state ----
if run_btn:
    queries = [x.strip() for x in raw.replace(",", "\n").splitlines() if x.strip()]
    if not queries:
        st.error("Chưa nhập mã nào.")
        st.stop()
    prog = st.progress(0.0, text="Đang resolve & cào dữ liệu (5 luồng song song)...")
    from screener import fetch_many
    n_q = len(queries)

    def _on_prog(done, total):
        prog.progress(done / total if total else 1.0,
                      text=f"Đang cào song song: {done}/{total} mã")

    rows, unresolved = fetch_many(queries, registry=reg, prefer=prefer,
                                  max_workers=5, retries=1, progress=_on_prog)
    prog.empty()
    if not rows:
        st.error("Không cào được mã nào. Kiểm tra tên/ticker hoặc kết nối mạng.")
        st.stop()
    st.session_state["screen"] = {"records": rows, "unresolved": unresolved}

# ---- Render kết quả (tồn tại qua các lần rerun để panel chi tiết tương tác được) ----
if "screen" in st.session_state:
    rows = st.session_state["screen"]["records"]
    unresolved = st.session_state["screen"]["unresolved"]
    df = pd.DataFrame(rows)
    df["pct_from_high"] = df.apply(
        lambda r: round((r["price"] / r["high_52w"] - 1) * 100, 1)
        if r.get("price") and r.get("high_52w") else None, axis=1)
    agg = sector_aggregates(df)

    med = df.groupby("sector")["per"].transform("median")
    def _tag(per, m):
        if per is None or m is None or pd.isna(per) or pd.isna(m):
            return ""
        if per < m * 0.85:
            return "🟢 Rẻ vs ngành"
        if per > m * 1.15:
            return "🔴 Đắt vs ngành"
        return "⚪ Ngang ngành"
    df["valuation_tag"] = [_tag(p, m) for p, m in zip(df["per"], med)]

    # EPS/ROE dự phóng (công thức) -> nuôi cả bảng & định giá forward. Bỏ qua nếu list quá lớn.
    fwd_eps_col, fwd_roe_col = [], []
    if len(df) <= 80:
        for tk, mc, px, bps in zip(df["ticker"], df["market_cap"], df["price"], df["bps"]):
            try:
                fc = project_forecast(
                    [c for c in _fin(tk)["annual"] if not c.get("is_consensus")], mc, px)
            except Exception:
                fc = None
            fwd_eps_col.append(fc.get("eps") if fc else None)
            fwd_roe_col.append((fc["eps"] / bps) if (fc and fc.get("eps") and bps) else None)
    else:
        fwd_eps_col = [None] * len(df)
        fwd_roe_col = [None] * len(df)
    df["fwd_eps"] = fwd_eps_col
    df["fwd_roe"] = fwd_roe_col

    if unresolved:
        st.warning(f"Chưa resolve được: {', '.join(unresolved)}")

    # ---- Bảng chính (có màu) ----
    st.subheader("📋 Bảng dữ liệu")
    enrich = st.checkbox(
        "📑 Bổ sung số liệu tài chính (doanh thu, LN thuần, ROE, nợ, EPS dự tính, đánh giá "
        f"— cào thêm ~0.5s/mã · {len(df)} mã)", value=len(df) <= 60)
    # Tự động gộp cột theo kỳ NGAY khi cào xong (không cần set tay).
    # Danh sách lớn (>40 mã) mới phải bật thủ công để tránh chậm/quá nhiều cột.
    if len(df) <= 40:
        expand_ts = True
    else:
        expand_ts = st.checkbox(
            f"📅 Gộp số liệu tài chính THEO KỲ vào bảng ({len(df)} mã — bật thủ công "
            "vì danh sách lớn)", value=False)
    _tm, _tscope = ["revenue", "net_profit", "roe"], "Năm + Quý"   # mặc định
    if expand_ts:
        with st.expander("⚙️ Tùy chỉnh cột theo kỳ "
                         "(mặc định: Doanh thu / LN thuần / ROE · Năm + Quý)"):
            _tm = st.multiselect(
                "Chỉ tiêu hiển thị theo kỳ",
                ["revenue", "op_profit", "net_profit", "roe", "net_margin",
                 "debt_ratio", "assets", "liabilities", "eps", "bps"],
                default=["revenue", "net_profit", "roe"],
                format_func=lambda k: LABELS_VI.get(k, k))
            _tscope = st.radio("Phạm vi kỳ", ["Năm", "Năm + Quý"], index=1,
                               horizontal=True, key="ts_scope")
    else:
        _tm = []

    def _ind(i):
        s = df["sector"].iloc[i]
        if isinstance(s, str) and s and s != "None":
            return s
        if "industry_kr" in df.columns:
            k = df["industry_kr"].iloc[i]
            if isinstance(k, str) and k:
                return k
        return "—"

    disp = pd.DataFrame({
        "Mã": df["ticker"].values, "Tên": df["name"].values,
        "Ngành": [_ind(i) for i in range(len(df))],
        "Giá": df["price"].values, "% so đỉnh": df["pct_from_high"].values,
        "Vốn hóa (억)": [m / 1e8 if pd.notna(m) else None for m in df["market_cap"]],
        "P/E": df["per"].values, "P/B": df["pbr"].values,
        "EPS": df["eps"].values, "BPS": df["bps"].values,
        "Tỷ suất CT %": df["div_yield"].values,
    })

    color_cols = {}        # tên cột -> hàm màu
    if enrich:
        rev, opp, net, roe, debt = [], [], [], [], []
        assets, liab, d2a, epsf, rating = [], [], [], [], []
        pbar = st.progress(0.0, text="Đang cào báo cáo tài chính & cân đối...")
        for i, tk in enumerate(df["ticker"]):
            f = _fin(tk)
            la = latest_actual(f["annual"])
            mc_i, px_i = df["market_cap"].iloc[i], df["price"].iloc[i]
            bs_i = balance_sheet_of(la, mc_i, px_i,
                                    la.get("bps") if la else None,
                                    la.get("debt_ratio") if la else None)
            rev.append(la.get("revenue") if la else None)
            opp.append(la.get("op_profit") if la else None)
            net.append(la.get("net_profit") if la else None)
            roe.append(la.get("roe") if la else None)
            debt.append(la.get("debt_ratio") if la else None)
            assets.append(bs_i.get("assets"))
            liab.append(bs_i.get("liabilities"))
            d2a.append(bs_i.get("debt_to_assets"))
            epsf.append(df["fwd_eps"].iloc[i] if df["fwd_eps"].iloc[i] is not None
                        else forecast_eps(f["annual"], mc_i, px_i))
            rating.append(assess_financials(
                f["annual"], df["sector"].iloc[i],
                df["industry_kr"].iloc[i] if "industry_kr" in df.columns else None
            )["rating"])
            pbar.progress((i + 1) / len(df))
        pbar.empty()
        disp["Doanh thu (억)"] = rev
        disp["LN HĐ (억)"] = opp
        disp["LN thuần (억)"] = net
        disp["ROE %"] = roe
        disp["Tổng tài sản (억)"] = assets
        disp["Nợ (억)"] = liab
        disp["Nợ/VCSH %"] = debt
        disp["Nợ/TS %"] = d2a
        disp["EPS dự tính"] = epsf
        disp["Đánh giá"] = rating
        color_cols.update({"ROE %": _c_roe, "Nợ/VCSH %": _c_debt, "Nợ/TS %": _c_d2a,
                           "Đánh giá": _c_tag, "LN HĐ (억)": _c_neg,
                           "LN thuần (억)": _c_neg})

    # ---- Tùy chọn: trải số liệu tài chính theo kỳ thành cột (gộp vào bảng chính) ----
    _ts_fmt: dict = {}
    if expand_ts and _tm:
        ylabels, qlabels, cellmap = [], [], {}
        with st.spinner("Đang nạp chuỗi tài chính theo kỳ..."):
            for i, tk in enumerate(df["ticker"]):
                f = _fin(tk)
                mc_i, px_i = df["market_cap"].iloc[i], df["price"].iloc[i]
                seq = [("Y", c) for c in
                       build_annual_series(tk, f["annual"], mc_i, px_i, years=4)]
                if _tscope == "Năm + Quý":
                    seq += [("Q", c) for c in
                            build_quarter_series(f["quarter"], mc_i, px_i, 4)]
                for kind, c in seq:
                    p = str(c.get("period", ""))
                    e = "E" if c.get("is_forecast") else ""
                    if kind == "Y":
                        plabel = f"'{p[2:4]}{e}"
                        if plabel not in ylabels:
                            ylabels.append(plabel)
                    else:
                        plabel = (f"Q{p[2:4]}.{p[4:6]}" if len(p) >= 6 else f"Q{p}") + e
                        if plabel not in qlabels:
                            qlabels.append(plabel)
                    for m in _tm:
                        cellmap.setdefault((m, plabel), {})[tk] = c.get(m)
        for m in _tm:
            for plabel in ylabels + qlabels:
                if (m, plabel) in cellmap:
                    col = f"{LABELS_VI.get(m, m)} {plabel}"
                    disp[col] = [cellmap[(m, plabel)].get(tk) for tk in df["ticker"]]
                    _ts_fmt[col] = "{:.1f}" if m in ("roe", "net_margin", "debt_ratio") \
                        else "{:,.0f}"

    # ---- Định giá theo P/E DỰ KIẾN (forward): dùng EPS dự phóng ----
    _fe = list(df["fwd_eps"])
    _px = list(df["price"])
    _mp = list(med)   # median P/E ngành (theo cross-section)
    disp["P/E dự kiến"] = [round(px / fe, 2) if (fe and fe > 0 and px) else None
                          for fe, px in zip(_fe, _px)]
    disp["Giá hợp lý (P/E)"] = [round(fe * mp) if (fe and fe > 0 and mp and mp > 0) else None
                               for fe, mp in zip(_fe, _mp)]
    disp["Upside P/E %"] = [round((tp / px - 1) * 100, 1) if (tp and px) else None
                           for tp, px in zip(disp["Giá hợp lý (P/E)"], _px)]

    disp["Định giá"] = df["valuation_tag"].values
    disp["Nguồn"] = df["source"].values
    color_cols.update({"Định giá": _c_tag, "% so đỉnh": _c_neg, "EPS": _c_neg,
                       "Upside P/E %": _c_neg})

    fmt = {"Giá": "{:,.0f}", "% so đỉnh": "{:+.1f}", "Vốn hóa (억)": "{:,.0f}",
           "P/E": "{:.2f}", "P/B": "{:.2f}", "EPS": "{:,.0f}", "BPS": "{:,.0f}",
           "Tỷ suất CT %": "{:.2f}", "Doanh thu (억)": "{:,.0f}", "LN HĐ (억)": "{:,.0f}",
           "LN thuần (억)": "{:,.0f}", "ROE %": "{:.1f}", "Tổng tài sản (억)": "{:,.0f}",
           "Nợ (억)": "{:,.0f}", "Nợ/VCSH %": "{:.0f}", "Nợ/TS %": "{:.0f}",
           "EPS dự tính": "{:,.0f}",
           "P/E dự kiến": "{:.2f}", "Giá hợp lý (P/E)": "{:,.0f}",
           "Upside P/E %": "{:+.1f}"}
    fmt.update(_ts_fmt)

    # ---- Sắp xếp lại: ĐỊNH DANH → cột theo KỲ (Năm/Quý) → ĐỊNH GIÁ & TỔNG QUAN → Nguồn ----
    _HEAD = ["Mã", "Tên", "Ngành"]
    _TAIL = [
        # Giá & thị trường
        "Giá", "% so đỉnh", "Vốn hóa (억)",
        # Định giá (multiples + P/E dự kiến)
        "P/E", "P/E dự kiến", "P/B", "Giá hợp lý (P/E)", "Upside P/E %", "Định giá",
        "Tỷ suất CT %",
        # Lợi nhuận & sinh lời (tổng quan)
        "EPS", "EPS dự tính", "BPS", "ROE %", "Doanh thu (억)", "LN HĐ (억)", "LN thuần (억)",
        # Cân đối & sức khỏe
        "Tổng tài sản (억)", "Nợ (억)", "Nợ/VCSH %", "Nợ/TS %", "Đánh giá",
    ]
    _known = set(_HEAD) | set(_TAIL) | {"Nguồn"}
    _period = [c for c in disp.columns if c not in _known]   # cột theo kỳ (động) → ĐỨNG TRƯỚC
    _final = [c for c in _HEAD if c in disp.columns] + _period \
        + [c for c in _TAIL if c in disp.columns] \
        + (["Nguồn"] if "Nguồn" in disp.columns else [])
    disp = disp[_final]

    sty = disp.style.format({k: v for k, v in fmt.items() if k in disp.columns},
                            na_rep="—")
    for col, fn in color_cols.items():
        if col in disp.columns:
            sty = sty.map(fn, subset=[col])
    # ghim cột Mã + Tên để cuộn ngang vẫn theo dõi được
    col_cfg = {"Mã": st.column_config.Column(pinned=True),
               "Tên": st.column_config.Column(pinned=True, width="medium")}
    st.dataframe(sty, use_container_width=True, hide_index=True, column_config=col_cfg)
    st.caption("📌 Cột Mã + Tên được ghim (cuộn ngang vẫn thấy). 🟢 tốt/rẻ · 🟡 trung bình · "
               "🔴 đắt/rủi ro. Vốn hóa, LN, Tài sản, Nợ đơn vị 억원 (×100 triệu KRW). "
               "Cân đối lấy số gốc DART nếu có .env key, nếu không là ước tính.")

    # ---- Aggregate ngành ----
    st.subheader("🏭 Chỉ số toàn ngành")
    st.caption("agg_P/E = ΣVốn hóa / ΣLN ròng (chuẩn) — median để tham chiếu.")
    st.dataframe(agg, use_container_width=True, hide_index=True)

    # ---- Định giá ----
    st.subheader("💰 Định giá")
    st.caption("Intrinsic làm base, multiples chỉ cross-check (KCI Valuation Spec §2/§8). "
               "Fair value = số quá khứ; **Fwd upside** = theo EPS/ROE dự phóng (công thức). "
               "Đổi giả định ở sidebar là cập nhật ngay.")
    val_results, _med = value_batch(df, assume)
    vdf = pd.DataFrame([r.to_dict() for r in val_results])
    VAL_KR = {
        "ticker": "Mã", "name": "Tên", "sector": "Ngành", "price": "Giá",
        "fair_value": "Fair value", "upside_%": "Upside %",
        "fwd_upside_%": "Fwd upside %", "auto_method": "PP áp dụng",
        "confidence": "Độ tin cậy",
    }
    summary_cols = ["ticker", "name", "sector", "price", "fair_value", "upside_%",
                    "fwd_upside_%", "auto_method", "confidence"]
    vsum = vdf[summary_cols].copy()
    vsum.columns = [VAL_KR.get(c, c) for c in summary_cols]
    vsty = vsum.style.format({"Giá": "{:,.0f}", "Fair value": "{:,.0f}",
                              "Upside %": "{:+.1f}", "Fwd upside %": "{:+.1f}"},
                             na_rep="—")
    for c in ("Upside %", "Fwd upside %"):
        vsty = vsty.map(_c_neg, subset=[c])
    st.dataframe(vsty, use_container_width=True, hide_index=True)

    # ---- Chi tiết 1 mã: định giá + tài chính 4 năm/4 quý + cân đối ----
    st.subheader("🔎 Chi tiết 1 mã")
    names = {f"{r.ticker} · {r.name or '?'}": r for r in val_results}
    pick = st.selectbox("Chọn mã để xem định giá + báo cáo tài chính", list(names.keys()))
    r = names[pick]
    drow = df[df["ticker"] == r.ticker].iloc[0].to_dict()

    # Cyclical (bán dẫn/thép/đóng tàu/ô tô/pin): nạp lịch sử DART → định giá MID-CYCLE
    # (median ROE qua chu kỳ, khử đỉnh/đáy) đáng tin hơn trailing 1 năm ở bảng nhanh.
    try:
        _hist = [c for c in build_annual_series(
                    r.ticker, _fin(r.ticker)["annual"],
                    drow.get("market_cap"), drow.get("price"), years=6)
                 if not c.get("is_consensus")]
        _m = _med.get(drow.get("sector"), {}) if isinstance(_med, dict) else {}
        if _hist:
            r = value_stock(drow, assume, _m.get("pe"), _m.get("pb"), history=_hist)
    except Exception:
        pass

    # Độ phủ dữ liệu (Naver+DART) → chặn trần confidence cho mã mới niêm yết/thiếu lịch sử
    cov = None
    if coverage is not None:
        try:
            cov = coverage.assess_coverage(r.ticker, r.sector)
        except Exception:
            cov = None
    disp_conf = _cap_conf(r.confidence, cov.get("confidence_cap")) if cov else r.confidence

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Giá hiện tại", f"{r.price:,.0f}" if r.price else "—")
    c2.metric(f"Fair value ({r.auto_method})",
              f"{r.fair_value:,.0f}" if r.fair_value else "—",
              f"{r.upside_pct:+.1f}%" if r.upside_pct is not None else None)
    c3.metric("Fair value dự phóng (Fwd)",
              f"{r.fwd_fair:,.0f}" if r.fwd_fair else "—",
              f"{r.fwd_upside_pct:+.1f}%" if r.fwd_upside_pct is not None else None)
    c4.metric("Độ tin cậy định giá", disp_conf,
              "↓ chặn trần" if disp_conf != r.confidence else None)
    if cov:
        st.caption("📊 Độ phủ dữ liệu: " + coverage.coverage_badge(cov))
        if cov.get("is_new_listing"):
            st.warning("🆕 Mã mới niêm yết — " + cov["note"])
    st.markdown(f"**PP chuẩn cho ngành _{r.sector}_:** {r.primary} "
                f"· _cross-check:_ {', '.join(r.cross)}")
    st.info(r.note)
    if getattr(r, "implied_roe", None) is not None:
        cur = f" · ROE hiện tại ~{r.roe*100:.0f}%" if r.roe else ""
        st.caption(f"🔁 **Reverse check:** thị giá đang ngụ ý **ROE ~{r.implied_roe*100:.0f}%**{cur}. "
                   "Chênh càng lớn = thị trường định giá tăng trưởng/siêu chu kỳ mà fair value proxy "
                   "(P/B-ROE quá khứ) KHÔNG bắt được — đừng đọc upside âm như lệnh 'bán'.")
    method_tbl = pd.DataFrame(
        [{"Phương pháp": k, "Fair value": (round(v) if v else None),
          "Upside %": (round((v / r.price - 1) * 100, 1) if (v and r.price) else None)}
         for k, v in r.methods.items()])
    st.dataframe(method_tbl, use_container_width=True, hide_index=True)
    if r.flags:
        st.warning("⚠️ " + " · ".join(r.flags))

    # --- Báo cáo tài chính ---
    st.markdown("#### 🧾 Báo cáo tài chính & Cân đối")
    fin = _fin(r.ticker)
    if not fin["annual"] and not fin["quarter"]:
        st.warning("Không lấy được báo cáo tài chính cho mã này.")
    else:
        mc, px = drow.get("market_cap"), drow.get("price")
        # chuỗi 5 năm thực (DART nếu có) + 1 cột dự phóng bằng công thức
        annual_series = build_annual_series(r.ticker, fin["annual"], mc, px, years=5)
        reals = [c for c in annual_series if not c.get("is_consensus")]
        la = reals[-1] if reals else None
        fcast = next((c for c in annual_series if c.get("is_forecast")), None)
        feps = fcast.get("eps") if fcast else forward_eps(fin["annual"], fin["quarter"])
        bs = balance_sheet_of(la, mc, px,
                              la.get("bps") if la else None,
                              la.get("debt_ratio") if la else None)
        mc_eok = round(mc / 1e8) if mc else None
        b1, b2, b3, b4, b5 = st.columns(5)
        b1.metric("Tổng vốn hóa (억)", f"{mc_eok:,}" if mc_eok else "—")
        b2.metric("Tổng tài sản (억)", f"{bs['assets']:,}" if bs["assets"] else "—")
        b3.metric("Nợ (억)", f"{bs['liabilities']:,}" if bs["liabilities"] else "—")
        b4.metric("Nợ/Tài sản %", f"{bs['debt_to_assets']}" if bs["debt_to_assets"] is not None else "—")
        b5.metric("EPS dự tính", f"{feps:,.0f}" if feps else "—")
        src = bs.get("source", "Ước tính")
        cagr = f" · Doanh thu dự phóng theo CAGR {fcast['_cagr']:+.1f}%/năm" if fcast else ""
        st.caption(f"Đơn vị 억원 = ×100 triệu KRW. Cân đối: **{src}** "
                   f"({'số gốc DART' if src == 'DART' else 'suy từ 부채비율×VCSH — ước tính'})."
                   f"{cagr}")
        if dart_unavailable:
            st.caption("ℹ️ Chưa có `DART_API_KEY` trong .env → lịch sử dùng Naver (≤3 năm thực). "
                       "Thêm key OpenDART để có đủ 5 năm thực + cân đối gốc.")

        fields = ["revenue", "op_profit", "net_profit", "roe", "net_margin",
                  "debt_ratio", "assets", "liabilities", "eps", "bps", "per", "pbr"]
        st.markdown("**Tài chính tổng hợp — 5 năm + 4 quý gần nhất + dự phóng** "
                    "(cột _Năm_ nền xám, cột _Quý_ nền trắng, cột _dự phóng_ nền xanh; "
                    "quý không có Tổng tài sản/Nợ → hiển thị —)")
        st.dataframe(
            _fin_table_combined(annual_series,
                                build_quarter_series(fin["quarter"], mc, px, 4),
                                fields),
            use_container_width=True)

        # --- Đánh giá cân đối theo tiêu chuẩn đầu tư ---
        asmt = assess_financials(fin["annual"], drow.get("sector"),
                                 drow.get("industry_kr"))
        st.markdown(f"**Đánh giá cân đối tài chính: {asmt['rating']}** — {asmt['summary']}")
        chk = pd.DataFrame([{"Tiêu chí": c["name"], "Giá trị": c["value"],
                             "": c["verdict"], "Ghi chú": c["note"]}
                            for c in asmt["checks"]])
        st.dataframe(chk, use_container_width=True, hide_index=True)

    # ---- Download Excel (kèm sheet "Bảng" đúng như màn hình) ----
    tmp = "kci_screen_output.xlsx"
    export_excel(df.reindex(columns=[c for c in df.columns if c in COLUMN_KR]
                            + ["valuation_tag"]), agg, tmp, val=vdf, table_df=disp)
    with open(tmp, "rb") as f:
        st.download_button("⬇️ Tải Excel", f.read(), file_name=tmp,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.success(f"Xong: {len(df)} mã · {df['per'].notna().sum()} mã có P/E")


# ============================================================================
# 🔬 Backtest — kiểm định độ tin cậy thực nghiệm (top-level, luôn hiển thị)
# ============================================================================
st.divider()
with st.expander("🔬 Backtest — đo sai số & tín hiệu của hệ thống (chạy chậm, cần mạng KRX)"):
    st.caption("Định giá tại thời điểm QUÁ KHỨ bằng dữ liệu có-tại-lúc-đó rồi so với "
               "GIÁ THỰC sau N tháng. Đo: sai số định giá · IC (upside có dự báo được "
               "return không) · hiệu lực theo confidence. Đây là bằng chứng độ tin cậy.")
    try:
        import backtest as _bt
    except Exception as _e:
        _bt = None
        st.error(f"Không nạp được backtest.py: {_e}")
    if _bt is not None:
        cba, cbb = st.columns(2)
        bt_universe = cba.text_area(
            "Mã backtest (mỗi dòng 1 ticker)",
            value="005930\n000660\n035420\n105560\n055550\n005380\n000270\n"
                  "005490\n051910\n207940\n068270\n012450\n015760\n017670",
            height=160)
        bt_asof = cbb.text_input("Mốc as-of (YYYY-MM-DD, cách nhau dấu phẩy)",
                                 value="2023-06-30, 2023-12-29, 2024-06-28")
        bt_h = cbb.slider("Horizon (tháng)", 3, 24, 12, 3)
        cbb.caption("⚠️ Chạy mất vài phút. Cần mạng KRX (pykrx fundamental).")
        if st.button("🚀 Chạy backtest", type="primary"):
            tickers = [x.strip().zfill(6) for x in
                       bt_universe.replace(",", "\n").splitlines() if x.strip()]
            asofs = [x.strip() for x in bt_asof.split(",") if x.strip()]
            bdf, bm = None, None
            with st.spinner(f"Đang chạy {len(tickers)}×{len(asofs)} điểm..."):
                try:
                    bdf, bm = _bt.run_backtest(tickers, asofs, horizon_months=bt_h,
                                               a=assume, verbose=False)
                except Exception as e:
                    st.error(f"Lỗi backtest: {e}")
            if bm and bm.get("n"):
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Số điểm", bm["n"])
                k2.metric("Sai số (median)", f"{bm.get('median_abs_error')}")
                k3.metric("IC (Spearman)", f"{bm.get('IC_spearman')}")
                k4.metric("Spread rẻ−đắt",
                          f"{bm.get('spread_rẻ_trừ_đắt', '—')}")
                st.markdown(_bt.report_markdown(bdf, bm, bt_h))
                st.dataframe(bdf, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Tải CSV backtest",
                    bdf.to_csv(index=False).encode("utf-8-sig"),
                    file_name="backtest_results.csv", mime="text/csv")
            elif bm is not None:
                st.warning("Không thu được điểm dữ liệu nào — kiểm tra mạng KRX, "
                           "danh sách mã, hoặc mốc as-of.")
