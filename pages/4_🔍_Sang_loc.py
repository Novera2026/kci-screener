"""
🔍 Sàng lọc cổ phiếu rẻ theo BỘ TIÊU CHUẨN có sẵn (Graham, Value-up, Magic Formula, Piotroski, cổ tức, KCI) →
định giá sâu tự động danh sách qua lọc → xếp hạng → mở mã trong 🧮. Một nút bấm; ngưỡng chỉnh được nhưng không bắt
buộc. Logic ở screen.py (có test); trang này là giao diện.
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
st.caption("Chọn **bộ tiêu chuẩn** → bấm **Chạy**. App tự lọc vũ trụ (tầng 1: giá, P/E, P/B, ROE, cổ tức), **định giá "
           "sâu** mọi mã qua lọc bằng đúng quy trình trang 🧮, kiểm tầng 2 bằng BCTC DART (F-score, thanh toán, năm lỗ, "
           "chất lượng tài sản) rồi xếp hạng. Mỗi mã định giá được lưu hồ sơ `dossiers/`.")
MAX_DEEP = 30


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

# ---------------------------------------------------------------------------- bộ tiêu chuẩn
keys = list(SC.PRESETS)
key = st.selectbox("Bộ tiêu chuẩn", keys, format_func=lambda k: SC.PRESETS[k]["name"])
pr = SC.PRESETS[key]
st.info(f"**{pr['name']}** — {pr['desc']}\n\n📚 {pr['source']}" + (f"\n\n⚙️ Điều chỉnh: {pr['adapt']}" if pr.get("adapt") else ""))
_bt = SC.BACKTEST_2021_2025.get(key)
if _bt:
    _sig = _bt["t"] is not None and abs(_bt["t"]) >= 2
    (st.success if (_bt["vs_univ"] > 0 and _sig) else st.warning)(
        f"📊 **Backtest 2021–25** (top 200, giữ 12 tháng từ 15/4, đủ 2 tầng): vượt **trung bình vũ trụ** "
        f"{_bt['vs_univ'] * 100:+.1f}%/năm (t = {_bt['t']:.2f}, thắng {_bt['years_beat']} năm) · vượt KOSPI "
        f"{_bt['vs_kospi'] * 100:+.1f}%/năm · {_bt['avg_n']:.0f} mã/năm · mất > 30%: {_bt['loss30']:.0%}. "
        + ("Có ý nghĩa thống kê." if _sig else "**Chưa có ý nghĩa thống kê (|t| < 2)** — chưa chứng minh được bộ "
           "này chọn mã tốt hơn mua đều cả vũ trụ. Vượt KOSPI phần lớn do vũ trụ là các mã lớn *hiện tại* (thiên lệch "
           "sống sót)."))
with st.expander("📊 So sánh backtest tất cả bộ tiêu chuẩn"):
    st.dataframe(pd.DataFrame([{"Bộ": SC.PRESETS[k]["name"], "Vượt vũ trụ %/năm": v["vs_univ"] * 100, "t": v["t"],
                                "Năm thắng vũ trụ": v["years_beat"], "Vượt KOSPI %/năm": v["vs_kospi"] * 100,
                                "Mã/năm": v["avg_n"], "Mất > 30%": (v["loss30"] or 0) * 100}
                               for k, v in SC.BACKTEST_2021_2025.items()])
                 .style.format({"Vượt vũ trụ %/năm": "{:+.1f}", "t": "{:.2f}", "Vượt KOSPI %/năm": "{:+.1f}",
                                "Mã/năm": "{:.0f}", "Mất > 30%": "{:.0f}%"}), hide_index=True, width="stretch")
    st.caption("Lợi suất chỉ tính giá (bất lợi cho bộ cổ tức). 5 năm là mẫu nhỏ. Năm 2025 (bùng nổ bán dẫn/quốc phòng) "
               "các bộ 'rẻ' đều thua mạnh vì loại các mã tăng trưởng đắt. Chạy lại: `python backtest_presets.py 200` "
               "(xem HUONG_DAN_BACKTEST.md).")

with st.expander("Vũ trụ (mặc định: top 200 vốn hóa cả hai sàn)"):
    kind = st.radio("Lấy danh sách từ", ["Top vốn hóa", "Ngành KRX", "Chủ đề", "Nhập tay"], horizontal=True)
    if kind == "Top vốn hóa":
        c1, c2 = st.columns(2)
        mk = c1.selectbox("Sàn", ["ALL", "KOSPI", "KOSDAQ"], format_func=lambda x: "Cả hai sàn" if x == "ALL" else x)
        n_top = c2.slider("Số mã", 50, 400, 200, 50)
    elif kind == "Ngành KRX":
        ind = st.selectbox("Ngành", sorted(reg.df["industry_kr"].dropna().unique()))
    elif kind == "Chủ đề":
        th = st.selectbox("Chủ đề (Naver 테마)", _themes())
    else:
        manual = st.text_area("Mã hoặc tên, cách nhau bởi dấu phẩy / xuống dòng", "005930, 000660, 105560")

_LABELS = {"max_pe": ("P/E tối đa", 1.0), "max_pb": ("P/B tối đa", 0.1), "max_pe_pb": ("P/E × P/B tối đa", 0.5),
           "min_roe": ("ROE tối thiểu (%)", 1.0), "min_div": ("Cổ tức tối thiểu (%)", 0.5),
           "max_rel": ("Điểm rẻ tối đa (÷ ngành)", 0.05), "max_payout": ("Tỷ lệ chi trả tối đa", 0.05),
           "min_mcap": ("Vốn hóa tối thiểu (tỷ KRW)", 100.0), "pb_bottom_pct": ("Nhóm P/B thấp nhất (tỷ lệ)", 0.05),
           "magic_top": ("Số mã Magic Formula", 5.0)}
_LABELS2 = {"min_fscore": ("F-score tối thiểu (/8)", 1.0), "min_current_ratio": ("Thanh toán hiện hành tối thiểu", 0.1),
            "max_debt_equity": ("Nợ/VCSH tối đa (lần)", 0.1)}
ov1, ov2 = {}, {}
with st.expander("⚙️ Chỉnh ngưỡng (không bắt buộc — mặc định là tiêu chuẩn của bộ đã chọn)"):
    cols = st.columns(3)
    i = 0
    for k, v in pr["stage1"].items():
        if k in _LABELS and isinstance(v, (int, float)) and not isinstance(v, bool):
            lab, step = _LABELS[k]
            shown = v / 1e9 if k == "min_mcap" else v
            nv = cols[i % 3].number_input(lab, value=float(shown), step=step, key=f"o1_{key}_{k}")
            ov1[k] = nv * 1e9 if k == "min_mcap" else (int(nv) if k == "magic_top" else nv)
            i += 1
    for k, v in pr["stage2"].items():
        if k in _LABELS2:
            lab, step = _LABELS2[k]
            ov2[k] = cols[i % 3].number_input(lab, value=float(v), step=step, key=f"o2_{key}_{k}")
            i += 1
    n_deep = st.slider("Tối đa số mã định giá sâu", 5, MAX_DEEP, 20)

if st.button("▶️ Chạy sàng lọc", type="primary"):
    with st.spinner("① Lấy vũ trụ và chỉ số thị trường…"):
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
    st1 = SC.apply_preset(rows, key, ov1)
    passed = [r for r in st1 if r["Qua lọc"]][:n_deep]
    deep = []
    if passed:
        bar = st.progress(0.0, text="② Định giá sâu…")
        a = V.Assumptions(rf=_rf() or 0.032)
        deep = SC.deep_stage([r["Mã"] for r in passed], {r["ticker"]: r for r in rows}, rows, a, reg,
                             progress=lambda d, n: bar.progress(d / n, text=f"② Định giá sâu {d}/{n} mã"),
                             preset=key, overrides2=ov2)
        bar.empty()
    st.session_state["sc"] = {"key": key, "st1": st1, "deep": deep, "n_univ": len(rows),
                              "n_pass": sum(1 for r in st1 if r["Qua lọc"])}

res = st.session_state.get("sc")
if not res:
    st.stop()
if res["key"] != key:
    st.warning("Kết quả bên dưới là của bộ tiêu chuẩn trước — bấm **Chạy sàng lọc** để chạy bộ đang chọn.")

deep = res["deep"]
n_ok = sum(1 for r in deep if r.get("Đạt tiêu chuẩn") == "✅")
n_g = sum(1 for r in deep if str(r.get("Kết luận", "")).startswith("🟢") and r.get("Đạt tiêu chuẩn") == "✅")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Vũ trụ", res["n_univ"])
c2.metric("Qua tầng 1", res["n_pass"])
c3.metric("Định giá sâu → đạt tầng 2", f"{n_ok}/{len(deep)}")
c4.metric("Đạt chuẩn & 🟢", n_g)

st.markdown("### Kết quả")
if not deep:
    st.warning("Không mã nào qua tầng 1 — thử vũ trụ lớn hơn hoặc bộ tiêu chuẩn khác.")
else:
    ddf = pd.DataFrame(deep)
    cols = ["Đạt tiêu chuẩn", "Mã", "Tên", "Ngành", "Giá", "Giá trị hợp lý", "Chênh %", "Chất lượng TS", "F-score",
            "Tổng quan", "Kết luận", "Lý do chưa đạt"]
    ddf = ddf[[c for c in cols if c in ddf.columns]]

    def _c(v):
        if isinstance(v, str):
            return ("background-color:#d9f2e3" if v.startswith(("🟢", "✅")) else
                    "background-color:#fff3cd" if v.startswith("🟡") else
                    "background-color:#f9dcdc" if v.startswith(("🔴", "❌")) else "")
        return ""
    st.dataframe(ddf.style.format({"Giá": "{:,.0f}", "Giá trị hợp lý": "{:,.0f}", "Chênh %": "{:+.1f}",
                                   "Tổng quan": "{:.0f}", "F-score": "{:.0f}"}, na_rep="—")
                 .map(_c, subset=[c for c in ("Đạt tiêu chuẩn", "Kết luận") if c in ddf.columns]),
                 hide_index=True, width="stretch")
    st.caption("Xếp: đạt tiêu chuẩn tầng 2 → kết luận (🟢 trước) → chênh lệch → chất lượng. Backtest 2021–25 kiểm chứng "
               "phần **định giá** của quy trình (IC +0,106); các bộ tiêu chuẩn là quy tắc kinh điển, **chưa được backtest "
               "riêng trên thị trường Hàn** trong App. Đây là danh sách để nghiên cứu tiếp, không phải khuyến nghị đầu tư.")

    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("Mở một mã trong 🧮 Quy trình định giá (đủ 5 bước: đối thủ nước ngoài, bóc tách, kịch bản…)",
                        [f"{r['Mã']} · {r.get('Tên') or ''}" for r in deep])
    if c2.button("Mở trong 🧮", width="stretch"):
        tk = pick.split(" · ")[0]
        st.session_state["vp_ticker"] = tk
        st.session_state["vp_query"] = tk
        _page = next(f for f in sorted(os.listdir(os.path.join(_ROOT, "pages"))) if "Dinh_gia" in f)
        st.switch_page(f"pages/{_page}")

with st.expander(f"Tầng 1 — toàn vũ trụ ({res['n_pass']}/{res['n_univ']} qua lọc)"):
    st1 = pd.DataFrame(res["st1"])
    show = [c for c in ["Qua lọc", "Mã", "Tên", "Ngành", "Giá", "P/E", "P/B", "ROE %", "Cổ tức %", "P/E ÷ ngành",
                        "P/B ÷ ngành", "Điểm rẻ", "Biên an toàn Graham %", "Hạng Magic", "Vốn hóa (tỷ KRW)",
                        "Lý do loại"] if c in st1.columns]
    st.dataframe(st1[show].style.format({"Giá": "{:,.0f}", "P/E": "{:.1f}", "P/B": "{:.2f}", "ROE %": "{:.1f}",
                                         "Cổ tức %": "{:.1f}", "P/E ÷ ngành": "{:.2f}", "P/B ÷ ngành": "{:.2f}",
                                         "Điểm rẻ": "{:.2f}", "Biên an toàn Graham %": "{:+.0f}",
                                         "Hạng Magic": "{:.0f}", "Vốn hóa (tỷ KRW)": "{:,.0f}"}, na_rep="—"),
                 hide_index=True, width="stretch", height=360)

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as xw:
    pd.DataFrame(deep or [{}]).to_excel(xw, sheet_name="Kết quả", index=False)
    pd.DataFrame(res["st1"]).to_excel(xw, sheet_name="Tầng 1", index=False)
st.download_button("⬇️ Kết quả sàng lọc (.xlsx)", buf.getvalue(), f"sang_loc_{res['key']}.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
