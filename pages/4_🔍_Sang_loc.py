"""
🔍 Sàng lọc cổ phiếu rẻ → định giá sâu danh sách rẻ → xếp hạng → mở mã trong 🧮 để quyết định.
Logic ở screen.py (có test); trang này là giao diện.
"""
from __future__ import annotations

import csv
import io
import os
import sys

import pandas as pd
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
try:
    for _k in ("DART_API_KEY",):
        if _k not in os.environ and _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

import screen as SC                                    # noqa: E402
import valuation as V                                  # noqa: E402
from screener import TickerRegistry                    # noqa: E402

st.set_page_config(page_title="Sàng lọc cổ phiếu rẻ — KCI", page_icon="🔍", layout="wide")
st.title("🔍 Sàng lọc cổ phiếu rẻ")
st.caption("① Chọn vũ trụ → ② **lọc rẻ** (P/E, P/B so với ngành) → ③ **định giá sâu** danh sách rẻ bằng đúng quy trình "
           "trang 🧮 (cổng số, chất lượng tài sản, đánh giá tổng quan) → ④ xếp hạng → mở mã trong 🧮 để quyết định. "
           "Mỗi mã định giá được lưu hồ sơ `dossiers/`.")


@st.cache_resource
def _reg():
    return TickerRegistry()


@st.cache_data(ttl=86400, show_spinner=False)
def _rf():
    from screener import fetch_kgb10y
    return fetch_kgb10y()


@st.cache_data(ttl=1800, show_spinner=False)
def _rows(tickers: tuple) -> list:
    from screener import fetch_many
    rows, _ = fetch_many(list(tickers), registry=_reg(), prefer="naver")
    return rows


@st.cache_data(show_spinner=False)
def _themes() -> list:
    p = os.path.join(_ROOT, "themes_all.csv")
    if not os.path.exists(p):
        return []
    return sorted({r["theme_kr"] for r in csv.DictReader(open(p, encoding="utf-8"))})


reg = _reg()

# ---------------------------------------------------------------------------- ① vũ trụ
st.markdown("### ① Vũ trụ")
kind = st.radio("Lấy danh sách từ", ["Top vốn hóa", "Ngành KRX", "Chủ đề", "Nhập tay"], horizontal=True)
if kind == "Top vốn hóa":
    c1, c2 = st.columns(2)
    mk = c1.selectbox("Sàn", ["ALL", "KOSPI", "KOSDAQ"], format_func=lambda x: "Cả hai sàn" if x == "ALL" else x)
    n_top = c2.slider("Số mã", 20, 300, 100, 10)
elif kind == "Ngành KRX":
    ind = st.selectbox("Ngành", sorted(reg.df["industry_kr"].dropna().unique()))
elif kind == "Chủ đề":
    th = st.selectbox("Chủ đề (Naver 테마)", _themes())
else:
    manual = st.text_area("Mã hoặc tên, mỗi dòng / dấu phẩy một mã", "005930, 000660, 105560, 009540, 012450")

st.markdown("### ② Lọc rẻ")
c1, c2, c3, c4 = st.columns(4)
max_pe = c1.number_input("P/E tối đa", 1.0, 100.0, 15.0, 1.0)
max_rel = c2.number_input("Điểm rẻ tối đa (P/E, P/B ÷ ngành)", 0.2, 1.5, 0.8, 0.05,
                          help="Trung bình của P/E ÷ trung vị ngành và P/B ÷ trung vị ngành. 0,8 = rẻ hơn ngành 20%")
min_div = c3.number_input("Cổ tức tối thiểu (%)", 0.0, 10.0, 0.0, 0.5)
skip_fin = c4.checkbox("Loại ngành tài chính", False,
                       help="Ngân hàng/bảo hiểm: chất lượng tài sản chưa chấm đầy đủ (không có nợ xấu trên DART)")

if st.button("🔎 Lọc rẻ", type="primary"):
    with st.spinner("Đang lấy danh sách và chỉ số…"):
        if kind == "Top vốn hóa":
            tks = SC.top_by_cap(mk, n_top, reg)
        elif kind == "Ngành KRX":
            tks = SC.by_industry(ind, reg)
        elif kind == "Chủ đề":
            tks = SC.by_theme(th)
        else:
            tks = [t for t in (reg.resolve(x.strip()) for x in manual.replace("\n", ",").split(",") if x.strip()) if t]
        tks = list(dict.fromkeys(tks))[:400]
        rows = _rows(tuple(tks)) if tks else []
    st.session_state["sc_rows"] = rows
    st.session_state["sc_cheap"] = SC.cheap_stage(rows, max_pe, max_rel, min_div, True, skip_fin)
    st.session_state.pop("sc_deep", None)

cheap = st.session_state.get("sc_cheap")
if not cheap:
    st.info("Chọn vũ trụ và bấm **Lọc rẻ**.")
    st.stop()

passed = [r for r in cheap if r["Qua lọc"]]
st.caption(f"{len(cheap)} mã trong vũ trụ · **{len(passed)} mã qua lọc rẻ**. Trung vị ngành tính trong chính vũ trụ "
           "(≥ 3 mã cùng ngành; thiếu thì so với cả danh sách — cột 'So với').")
cdf = pd.DataFrame(cheap)
st.dataframe(cdf.style.format({"Giá": "{:,.0f}", "P/E": "{:.1f}", "P/B": "{:.2f}", "P/E ÷ ngành": "{:.2f}",
                               "P/B ÷ ngành": "{:.2f}", "Cổ tức %": "{:.1f}", "Điểm rẻ": "{:.2f}"}, na_rep="—"),
             hide_index=True, width="stretch", height=320)

# ---------------------------------------------------------------------------- ③ định giá sâu
st.markdown("### ③ Định giá sâu danh sách rẻ")
if not passed:
    st.warning("Không mã nào qua lọc — nới điều kiện ở bước ②.")
    st.stop()
k = st.slider("Số mã rẻ nhất đưa vào định giá sâu", 1, min(40, len(passed)), min(15, len(passed)))
st.caption("Mỗi mã: BCTC Naver/DART, beta, dự phóng mặc định, các mô hình qua cổng số, chất lượng tài sản, đánh giá "
           "tổng quan. Khoảng 2–5 giây/mã (lần đầu lâu hơn vì tải DART).")
if st.button(f"🧮 Định giá {k} mã", type="primary"):
    _rf_v = _rf()
    a = V.Assumptions(rf=_rf_v or 0.032)
    rows = st.session_state["sc_rows"]
    by_tk = {r["ticker"]: r for r in rows}
    bar = st.progress(0.0, text="Đang định giá…")
    res = SC.deep_stage([r["Mã"] for r in passed[:k]], by_tk, rows, a, reg,
                        progress=lambda d, n: bar.progress(d / n, text=f"Đã định giá {d}/{n} mã"))
    bar.empty()
    st.session_state["sc_deep"] = res

deep = st.session_state.get("sc_deep")
if not deep:
    st.stop()

# ---------------------------------------------------------------------------- ④ xếp hạng
st.markdown("### ④ Xếp hạng")
ddf = pd.DataFrame(deep)
cols = ["Mã", "Tên", "Ngành", "Giá", "Giá trị hợp lý", "Chênh %", "Độ tin cậy", "Chất lượng TS", "Tổng quan",
        "Kết luận", "Trạng thái"]
ddf = ddf[[c for c in cols if c in ddf.columns]]


def _c(v):
    if isinstance(v, str):
        return ("background-color:#d9f2e3" if v.startswith("🟢") else "background-color:#fff3cd" if v.startswith("🟡")
                else "background-color:#f9dcdc" if v.startswith("🔴") else "")
    return ""


st.dataframe(ddf.style.format({"Giá": "{:,.0f}", "Giá trị hợp lý": "{:,.0f}", "Chênh %": "{:+.1f}",
                               "Tổng quan": "{:.0f}"}, na_rep="—").map(_c, subset=["Kết luận"]),
             hide_index=True, width="stretch")
n_g = sum(1 for r in deep if str(r.get("Kết luận", "")).startswith("🟢"))
st.caption(f"🟢 {n_g} mã rẻ và chất lượng tốt. Xếp theo kết luận → chênh lệch → chất lượng. Kết luận dùng quy tắc số "
           "(xem trang 🧮 ⑤). Backtest 2021–25 kiểm chứng phần **định giá** (IC +0,106); lọc P/E thấp và điểm tổng "
           "hợp chưa được kiểm chứng riêng. Đây là danh sách để nghiên cứu tiếp, không phải khuyến nghị đầu tư.")

c1, c2 = st.columns([3, 1])
pick = c1.selectbox("Mở một mã trong 🧮 Quy trình định giá để xem đủ 5 bước (đối thủ nước ngoài, bóc tách, kịch bản…)",
                    [f"{r['Mã']} · {r.get('Tên') or ''}" for r in deep])
if c2.button("Mở trong 🧮", width="stretch"):
    tk = pick.split(" · ")[0]
    st.session_state["vp_ticker"] = tk
    st.session_state["vp_query"] = tk
    _page = next(f for f in sorted(os.listdir(os.path.join(_ROOT, "pages"))) if "Dinh_gia" in f)
    st.switch_page(f"pages/{_page}")

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as xw:
    pd.DataFrame(deep).to_excel(xw, sheet_name="Xếp hạng", index=False)
    pd.DataFrame(cheap).to_excel(xw, sheet_name="Lọc rẻ", index=False)
st.download_button("⬇️ Kết quả sàng lọc (.xlsx)", buf.getvalue(), "sang_loc_kci.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
