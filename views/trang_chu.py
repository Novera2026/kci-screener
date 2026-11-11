"""
🏠 Trang chủ — tổng quan nhanh: chỉ số, chủ đề nóng hôm nay, mega trend, kết quả bot săn cổ phiếu.
Chỉ dùng dữ liệu nhẹ (Naver realtime) để mở nhanh; định giá sâu nằm ở các trang chi tiết.
Logic ở megatrend.py / hotnews.py; trang này chỉ là giao diện.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

import pandas as pd
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import hotnews as H                                  # noqa: E402
import megatrend as MT                               # noqa: E402

st.set_page_config(page_title="Trang chủ — KCI", page_icon="🏠", layout="wide")
_KST = _dt.timezone(_dt.timedelta(hours=9))


# ---------------------------------------------------------------- dữ liệu (cache rất ngắn, chỉ để rerun nhanh)
@st.cache_data(ttl=60, show_spinner=False)
def _themes():
    return H.theme_ranking()


@st.cache_data(ttl=60, show_spinner=False)
def _indices():
    return MT.market_indices()


@st.cache_data(ttl=60, show_spinner=False)
def _details(nos: tuple):
    return H.parallel(lambda no: H.theme_detail(no, 100), list(nos))     # đủ thành viên (chủ đề lớn > 40 mã)


def _refresh():
    for f in (_themes, _indices, _details):
        f.clear()


def _open_valuation(tk: str):
    """Mở mã trong 🧮 — trang đó tự lấy số liệu thị trường mới."""
    st.session_state["vp_ticker"] = tk
    st.session_state["vp_query"] = tk
    st.session_state["vp_query_box"] = tk
    st.session_state["vp_force"] = True
    _page = next(f for f in sorted(os.listdir(os.path.join(_ROOT, "pages"))) if "Dinh_gia" in f)
    st.switch_page(f"pages/{_page}")


def _color(v):
    if not isinstance(v, (int, float)) or pd.isna(v):
        return ""
    return "color:#137333;font-weight:600" if v > 0 else "color:#c0392b;font-weight:600" if v < 0 else ""


# ---------------------------------------------------------------- đầu trang
h1, h2 = st.columns([5, 1])
h1.title("🏠 Tổng quan thị trường Hàn")
h2.write("")
h2.button("🔄 Làm mới", on_click=_refresh, width="stretch")

with st.spinner("Đang lấy chỉ số & chủ đề realtime (Naver)..."):
    idx = _indices()
    themes, mstatus = _themes()

_ST = {"OPEN": "🟢 Mở cửa", "CLOSE": "⚪ Đóng cửa", "PREOPEN": "🟡 Chờ mở"}
cols = st.columns(max(len(idx), 1) + 1)
for c, r in zip(cols, idx):
    c.metric("KOSPI" if r["code"] == "KOSPI" else "KOSDAQ",
             f"{r['price']:,.2f}" if r["price"] is not None else "—",
             f"{r['chg_pt']:+,.2f} ({r['chg']:+.2f}%)" if r["chg"] is not None and r["chg_pt"] is not None else None)
cols[-1].metric("Thị trường", _ST.get(mstatus, mstatus or "—"))
st.caption(f"🕒 Lấy lúc **{_dt.datetime.now(_KST):%H:%M %d/%m/%Y}** giờ Hàn Quốc · nguồn Naver realtime.")
if not themes:
    st.error("Không lấy được danh sách chủ đề từ Naver (lỗi mạng?). Bấm 🔄 Làm mới để thử lại.")
    st.stop()

# ---------------------------------------------------------------- mega trend
st.subheader("🌐 Mega trend hôm nay")
st.caption("Mỗi xu hướng gom vài chủ đề Naver (danh mục chọn tay trong `megatrend.py`). "
           "**% hôm nay** = trung bình các chủ đề con · **Lan tỏa** = tỷ lệ mã đang tăng.")
tt = MT.trend_table(themes)
tdf = pd.DataFrame([{
    "Xu hướng": f"{r['icon']} {r['name']}",
    "% hôm nay": r["chg"], "Lan tỏa %": r["breadth"],
    "Mã tăng/tổng": f"{r['rise']}/{r['total']}",
    "Chủ đề con mạnh nhất": f"{r['best']} ({r['best_chg']:+.2f}%)" if r["best"] and r["best_chg"] is not None else "—",
    "Gồm": r["desc"],
} for r in tt])
st.caption("👆 **Bấm vào một dòng** để xem toàn bộ cổ phiếu trong nhóm xu hướng đó.")
_ev = st.dataframe(tdf.style.format({"% hôm nay": "{:+.2f}", "Lan tỏa %": "{:.0f}"}, na_rep="—")
                   .map(_color, subset=["% hôm nay"])
                   .bar(subset=["Lan tỏa %"], color="#cdebd8", vmin=0, vmax=100),
                   hide_index=True, width="stretch", height=35 * (len(tdf) + 1) + 3,   # hiện đủ, không cuộn trong bảng
                   on_select="rerun", selection_mode="single-row", key="mt_table")
_rows = (_ev.selection.rows if _ev is not None and hasattr(_ev, "selection") else []) or []
_r = tt[_rows[0]] if _rows else tt[0]                  # chưa chọn → xu hướng mạnh nhất hôm nay
_key = _r["key"]
_m = next(x for x in MT.MEGATRENDS if x["key"] == _key)
st.markdown(f"#### {_r['icon']} {_r['name']} — cổ phiếu trong nhóm" + ("" if _rows else " _(mạnh nhất hôm nay)_"))
st.caption(_m["desc"])
with st.spinner("Đang lấy thành viên các chủ đề..."):
    _det = _details(tuple(_m["themes"]))
_tnames = {t["no"]: t["name"] for t in themes}
members = MT.trend_leaders(_key, _det, top=None, theme_names=_tnames)
if members:
    _up = sum(1 for s in members if (s.get("chg") or 0) > 0)
    _dn = sum(1 for s in members if (s.get("chg") or 0) < 0)
    st.caption(f"{len(members)} mã · {_up} tăng · {_dn} giảm · xếp theo % hôm nay. Một mã có thể thuộc nhiều chủ đề con.")
    ldf = pd.DataFrame([{"Mã": s["ticker"], "Tên": s["name"], "Giá": s["price"], "% hôm nay": s["chg"],
                         "GTGD (억)": s["tv_eok"], "Vốn hóa (억)": s["cap_eok"],
                         "Chủ đề con": ", ".join(s.get("themes") or []),
                         "Lý do thuộc chủ đề": s["reason"]} for s in members])
    st.dataframe(ldf.style.format({"Giá": "{:,.0f}", "% hôm nay": "{:+.2f}", "GTGD (억)": "{:,.0f}",
                                   "Vốn hóa (억)": "{:,.0f}"}, na_rep="—").map(_color, subset=["% hôm nay"]),
                 hide_index=True, width="stretch", height=min(35 * (len(ldf) + 1) + 3, 520))
    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("Định giá một mã", [f"{s['ticker']} · {s['name']}" for s in members],
                        key=f"home_pick_mt_{_key}")
    if c2.button("Mở trong 🧮", key="home_open_mt", width="stretch"):
        _open_valuation(pick.split(" · ")[0])
else:
    st.info("Chưa lấy được thành viên của xu hướng này.")

# ---------------------------------------------------------------- chủ đề nóng
st.subheader("🔥 Chủ đề nóng hôm nay")
hot = H.hot_themes(themes, top=10)
if hot:
    hdf = pd.DataFrame([{"Chủ đề": t["name"], "% hôm nay": t["chg"], "Lan tỏa %": t["breadth"],
                         "Tăng": t["rise"], "Giảm": t["fall"], "Số mã": t["total"]} for t in hot])
    st.dataframe(hdf.style.format({"% hôm nay": "{:+.2f}", "Lan tỏa %": "{:.0f}"}, na_rep="—")
                 .map(_color, subset=["% hôm nay"]), hide_index=True, width="stretch")
    st.page_link("pages/2_📰_Tin_nong.py", label="Xem tin & công bố gắn với chủ đề nóng →", icon="📰")
else:
    st.info("Hôm nay chưa có chủ đề nào tăng đủ rộng (≥ 5 mã).")

# ---------------------------------------------------------------- tóm tắt phiên (Facebook)
st.subheader("📝 Tóm tắt phiên — đăng Facebook")
st.caption("Viết tự động từ số liệu realtime ở trên (chỉ số, mega trend, mã tăng mạnh trong nhóm dẫn dắt). Bấm biểu "
           "tượng 📋 ở góc khung để copy. Trước giờ đóng cửa (15:30) là **diễn biến phiên**, sau đó là **tổng kết phiên** "
           "— bấm 🔄 Làm mới để lấy số mới nhất.")
_lead = [t for t in tt if t.get("chg") is not None][:3]
with st.spinner("Đang lấy mã tăng mạnh của các nhóm dẫn dắt..."):
    _mv = {}
    for _t in _lead:
        _mm = next(x for x in MT.MEGATRENDS if x["key"] == _t["key"])
        _mv[_t["key"]] = MT.trend_leaders(_t["key"], _details(tuple(_mm["themes"])), top=5)
_post = MT.session_summary(idx, tt, _mv, _dt.datetime.now(_KST), mstatus)
st.code(_post, language=None, wrap_lines=True)

# ---------------------------------------------------------------- bot
st.subheader("🤖 Bot săn cổ phiếu")
st.caption("Bot lọc qua các **bộ tiêu chuẩn** (tầng 1: rẻ theo chỉ số) rồi **định giá sâu** từng mã qua cổng số "
           "(tầng 2). Chọn phạm vi săn bên dưới — mọi số lấy mới lúc bấm chạy.")


@st.cache_resource
def _reg():
    from screener import TickerRegistry
    return TickerRegistry()


@st.cache_data(show_spinner=False)
def _theme_names() -> list[str]:
    import csv
    path = os.path.join(_ROOT, "themes_all.csv")
    if not os.path.exists(path):
        return []
    return sorted({r["theme_kr"] for r in csv.DictReader(open(path, encoding="utf-8"))})


import bot_hunt as BH                                # noqa: E402
import screen as SC                                  # noqa: E402

reg = _reg()
_mkt_of = dict(zip(reg.df["ticker"].astype(str).str.zfill(6), reg.df["market"]))
_ind_of = dict(zip(reg.df["ticker"].astype(str).str.zfill(6), reg.df["industry_kr"]))
_MK = {"ALL": "Cả hai sàn", "KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ"}

kind = st.segmented_control("Phạm vi săn", ["Top vốn hóa", "Ngành KRX", "Chủ đề Naver", "Mega trend"],
                            default="Top vốn hóa", key="hb_kind") or "Top vốn hóa"
c1, c2 = st.columns(2)
if kind == "Top vốn hóa":
    mk = c1.selectbox("Sàn", list(_MK), format_func=_MK.get, key="hb_mk")
    n_top = c2.select_slider("Top vốn hóa", [50, 100, 200, 300, 400], value=200, key="hb_n")
    scope = f"top {n_top} vốn hóa {_MK[mk]}"
elif kind == "Ngành KRX":
    ind = c1.selectbox("Ngành", sorted(reg.df["industry_kr"].dropna().unique()), key="hb_ind")
    mk = c2.selectbox("Sàn", list(_MK), format_func=_MK.get, key="hb_mk_i")
    scope = f"ngành {ind}" + ("" if mk == "ALL" else f" · {mk}")
elif kind == "Chủ đề Naver":
    th = c1.selectbox("Chủ đề", _theme_names(), key="hb_th")
    mk = c2.selectbox("Sàn", list(_MK), format_func=_MK.get, key="hb_mk_t")
    scope = f"chủ đề {th}" + ("" if mk == "ALL" else f" · {mk}")
else:
    _mt = {f"{m['icon']} {m['name']}": m["key"] for m in MT.MEGATRENDS}
    mt_lab = c1.selectbox("Mega trend", list(_mt), key="hb_mt")
    mk = c2.selectbox("Sàn", list(_MK), format_func=_MK.get, key="hb_mk_m")
    scope = f"mega trend {mt_lab}" + ("" if mk == "ALL" else f" · {mk}")

c3, c4 = st.columns([3, 1])
presets = c3.multiselect("Bộ tiêu chuẩn", list(SC.PRESETS), default=BH.DEFAULT_PRESETS,
                         format_func=lambda k: SC.PRESETS[k]["name"].split(" — ")[0], key="hb_presets")
max_deep = c4.slider("Định giá sâu tối đa / bộ", 3, 15, 8, key="hb_deep",
                     help="Mỗi bộ lấy tối đa chừng này mã qua tầng 1 để định giá sâu. Nhiều hơn = lâu hơn.")

if st.button("🤖 Cho bot săn", type="primary", disabled=not presets, key="hb_run"):
    with st.status(f"Bot đang săn trong {scope}…", expanded=True) as _sts:
        if kind == "Top vốn hóa":
            tks = None
        elif kind == "Ngành KRX":
            tks = SC.by_industry(ind, reg)
        elif kind == "Chủ đề Naver":
            tks = SC.by_theme(th)
        else:
            st.write("Lấy thành viên các chủ đề của mega trend…")
            tks = MT.trend_tickers(_mt[mt_lab])
        if tks is not None and mk != "ALL":
            tks = [t for t in tks if _mkt_of.get(t) == mk]
        if tks is not None and not tks:
            _sts.update(label="Phạm vi này không có mã nào.", state="error")
            st.stop()
        out = BH.hunt(presets, universe=n_top if kind == "Top vốn hóa" else 200, max_deep=max_deep,
                      progress=st.write, tickers=tks, market=mk, scope=scope)
        st.session_state["home_hunt"] = out
        _sts.update(label=f"Xong · {len(out['picks'])} mã đạt chuẩn trong {out['meta']['universe']} mã "
                          f"({out['meta']['scope']})", state="complete", expanded=False)

hunt = st.session_state.get("home_hunt")
if hunt:
    _meta = hunt["meta"]
    st.caption(f"Kết quả săn lúc **{_meta['at']:%H:%M %d/%m/%Y}** KST · {_meta.get('scope', '')} · "
               f"{_meta['universe']} mã · định giá sâu {_meta['n_deep']} mã.")
    picks = hunt["picks"]
    if not picks:
        st.info("Không mã nào qua đủ 2 tầng trong phạm vi này. Thử mở rộng phạm vi hoặc thêm bộ tiêu chuẩn.")
    else:
        pdf = pd.DataFrame([{
            "Mã": p["ticker"], "Tên": p["name"], "Sàn": _mkt_of.get(p["ticker"], "—"),
            "Ngành": p.get("industry") or _ind_of.get(p["ticker"]) or "—",
            "Kết luận": p["verdict"] or "—", "Giá": p["price"], "Giá trị hợp lý": p["fair"],
            "Chênh %": p["upside"], "Chất lượng TS": p.get("quality") or "—", "Số bộ đạt": len(p["presets"]),
            "Bộ tiêu chuẩn": ", ".join(SC.PRESETS[k]["name"].split(" — ")[0] for k in p["presets"]),
        } for p in picks])
        f1, f2 = st.columns(2)
        fm = f1.segmented_control("Lọc sàn", ["Tất cả", "KOSPI", "KOSDAQ"], default="Tất cả", key="hb_fm") or "Tất cả"
        inds = sorted(pdf["Ngành"].unique())
        fi = f2.multiselect("Lọc ngành", inds, key="hb_fi")
        view = pdf[(pdf["Sàn"] == fm) if fm != "Tất cả" else pdf["Sàn"].notna()]
        if fi:
            view = view[view["Ngành"].isin(fi)]
        st.dataframe(view.style.format({"Giá": "{:,.0f}", "Giá trị hợp lý": "{:,.0f}", "Chênh %": "{:+.1f}"},
                                       na_rep="—").map(_color, subset=["Chênh %"]),
                     hide_index=True, width="stretch")
        st.caption("Xếp theo **chênh lệch định giá**. 🟢 rẻ & chất lượng tốt · 🟡 rẻ nhưng cần kiểm. Backtest 2021–25 (top "
                   "200): định giá xếp hạng có tín hiệu yếu (IC +0,08), nhưng **danh sách kiểu này chưa thắng trung bình "
                   "thị trường** (top 12 ≈ +0,4%/năm, không có ý nghĩa thống kê) — để nghiên cứu tiếp, không phải danh "
                   "mục mua.")
        if len(view):
            c1, c2 = st.columns([3, 1])
            pick = c1.selectbox("Định giá đủ 5 bước một mã", [f"{r['Mã']} · {r['Tên']}" for _, r in view.iterrows()],
                                key="home_pick_hunt")
            if c2.button("Mở trong 🧮", key="home_open_hunt", width="stretch"):
                _open_valuation(pick.split(" · ")[0])

bot = MT.load_bot_state(os.path.join(_ROOT, "bot_state.json"))
if bot:
    try:
        _at = _dt.datetime.fromisoformat(bot["at"]).strftime("%H:%M %d/%m/%Y")
    except (KeyError, ValueError):
        _at = "—"
    with st.expander(f"📨 Lần chạy tự động gần nhất · {_at} KST · {len(bot['picks'])} mã"):
        st.dataframe(pd.DataFrame([{"Mã": tk, "Tên": p.get("name"), "Sàn": _mkt_of.get(tk, "—"),
                                    "Kết luận": p.get("verdict") or "—", "Số bộ đạt": len(p.get("presets") or [])}
                                   for tk, p in bot["picks"].items()]), hide_index=True, width="stretch")
        st.caption("Danh sách lúc bot chạy — giá & giá trị hợp lý tính lại mới khi mở mã trong 🧮.")

st.divider()
st.caption("Không phải khuyến nghị đầu tư. Chủ đề & xu hướng phản ánh dòng tiền ngắn hạn, cần đọc kèm định giá.")
