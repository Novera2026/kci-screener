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
    return H.parallel(H.theme_detail, list(nos))


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
st.dataframe(tdf.style.format({"% hôm nay": "{:+.2f}", "Lan tỏa %": "{:.0f}"}, na_rep="—")
             .map(_color, subset=["% hôm nay"])
             .bar(subset=["Lan tỏa %"], color="#cdebd8", vmin=0, vmax=100),
             hide_index=True, width="stretch", height=35 * (len(tdf) + 1) + 3)   # hiện đủ, không cuộn trong bảng

_tmap = {f"{r['icon']} {r['name']}": r["key"] for r in tt}
sel = st.selectbox("Xem mã dẫn dắt của xu hướng", list(_tmap))
_key = _tmap[sel]
_m = next(x for x in MT.MEGATRENDS if x["key"] == _key)
with st.spinner("Đang lấy thành viên các chủ đề..."):
    _det = _details(tuple(_m["themes"]))
leaders = MT.trend_leaders(_key, _det, top=12)
if leaders:
    ldf = pd.DataFrame([{"Mã": s["ticker"], "Tên": s["name"], "Giá": s["price"], "% hôm nay": s["chg"],
                         "GTGD (억)": s["tv_eok"], "Vốn hóa (억)": s["cap_eok"],
                         "Lý do thuộc chủ đề": s["reason"]} for s in leaders])
    st.dataframe(ldf.style.format({"Giá": "{:,.0f}", "% hôm nay": "{:+.2f}", "GTGD (억)": "{:,.0f}",
                                   "Vốn hóa (억)": "{:,.0f}"}, na_rep="—").map(_color, subset=["% hôm nay"]),
                 hide_index=True, width="stretch")
    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("Định giá một mã", [f"{s['ticker']} · {s['name']}" for s in leaders], key="home_pick_mt")
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

# ---------------------------------------------------------------- bot
st.subheader("🤖 Bot săn cổ phiếu — lần chạy gần nhất")
bot = MT.load_bot_state(os.path.join(_ROOT, "bot_state.json"))
if not bot:
    st.info("Chưa có kết quả bot trong App. Bot hiện gửi kết quả qua Telegram; bước tiếp theo là cho bot lưu "
            "kết quả vào chỗ App đọc được (Google Sheet hoặc repo) để hiện ở đây.")
    st.page_link("pages/4_🔍_Sang_loc.py", label="Tự chạy sàng lọc ngay trong App →", icon="🔍")
else:
    try:
        _at = _dt.datetime.fromisoformat(bot["at"]).strftime("%H:%M %d/%m/%Y")
    except (KeyError, ValueError):
        _at = "—"
    bdf = pd.DataFrame([{"Mã": tk, "Tên": p.get("name"), "Kết luận": p.get("verdict") or "—",
                         "Số bộ đạt": len(p.get("presets") or []),
                         "Bộ tiêu chuẩn": ", ".join(p.get("presets") or [])}
                        for tk, p in bot["picks"].items()])
    st.caption(f"Bot chạy lúc **{_at}** KST · {len(bdf)} mã. Chỉ là danh sách để nghiên cứu tiếp — giá & giá trị "
               "hợp lý **tính lại mới** khi mở mã trong 🧮, không dùng lại số lúc bot chạy.")
    if len(bdf):
        st.dataframe(bdf, hide_index=True, width="stretch")
        c1, c2 = st.columns([3, 1])
        pick = c1.selectbox("Định giá một mã của bot", [f"{r['Mã']} · {r['Tên']}" for _, r in bdf.iterrows()],
                            key="home_pick_bot")
        if c2.button("Mở trong 🧮", key="home_open_bot", width="stretch"):
            _open_valuation(pick.split(" · ")[0])

st.divider()
st.caption("Không phải khuyến nghị đầu tư. Chủ đề & xu hướng phản ánh dòng tiền ngắn hạn, cần đọc kèm định giá.")
