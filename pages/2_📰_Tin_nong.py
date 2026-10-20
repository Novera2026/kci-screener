"""
📰 Tin nóng & chủ đề dẫn dắt — theo dõi realtime CHỦ ĐỀ, TIN GIÁ TRỊ CAO và CÔNG BỐ DART.
Logic ở hotnews.py; trang này chỉ là giao diện (tự làm mới định kỳ).
"""
from __future__ import annotations

import collections
import csv
import datetime as _dt
import os
import sys

import pandas as pd
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import hotnews as H                                  # noqa: E402
from screener import TickerRegistry                  # noqa: E402

st.set_page_config(page_title="Tin nóng — KCI", page_icon="📰", layout="wide")
st.title("📰 Tin nóng & chủ đề dẫn dắt")
st.caption("Cổ phiếu Hàn thường chạy theo **chủ đề + tin nóng**. Trang này gom realtime: chủ đề "
           "tăng mạnh nhất hôm nay, tin được **chấm điểm giá trị** (ưu tiên sự kiện làm thay đổi "
           "giá trị doanh nghiệp), và **công bố chính thức trên DART**.")


# ---------------------------------------------------------------- dữ liệu (cache ngắn)
@st.cache_resource
def _ref():
    reg = TickerRegistry()
    names = [(str(n), str(t).zfill(6)) for n, t in zip(reg.df["name_kr"], reg.df["ticker"])
             if isinstance(n, str) and len(n) >= 3]
    names.sort(key=lambda x: -len(x[0]))
    code2name = {t: n for n, t in names}
    for n, t in zip(reg.df["name_kr"], reg.df["ticker"]):
        if isinstance(n, str):
            code2name.setdefault(str(t).zfill(6), n)
    themes_of = collections.defaultdict(list)
    path = os.path.join(_ROOT, "themes_all.csv")
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="utf-8")):
            themes_of[str(r["ticker"]).zfill(6)].append(r["theme_kr"])
    return names, code2name, dict(themes_of)


@st.cache_data(ttl=180, show_spinner=False)
def _themes():
    return H.theme_ranking()


@st.cache_data(ttl=180, show_spinner=False)
def _details(nos: tuple):
    return H.parallel(H.theme_detail, list(nos))


@st.cache_data(ttl=180, show_spinner=False)
def _market_news():
    return (H.market_news("flashnews", 40) + H.market_news("mainnews", 20)
            + H.market_news("ranknews", 20))


@st.cache_data(ttl=300, show_spinner=False)
def _stock_news(pairs: tuple):
    res = H.parallel(lambda k: H.stock_news(k[0], 10, k[1]), list(pairs))
    return [x for v in res.values() for x in (v or [])]


@st.cache_data(ttl=600, show_spinner=False)
def _dart(days: int):
    return H.dart_disclosures(days)


@st.cache_data(ttl=60, show_spinner=False)
def _rt(tickers: tuple):
    return H.realtime(list(tickers))


def _clear():
    for f in (_themes, _details, _market_news, _stock_news, _dart, _rt):
        f.clear()


NAMES, CODE2NAME, THEMES_OF = _ref()


def _fmt_chg(x):
    return "—" if x is None else f"{x:+.2f}%"


def _ago(h):
    if h is None or h > 48:
        return ""
    return f"{int(h * 60)} phút trước" if h < 1 else f"{h:.0f} giờ trước"


# ---------------------------------------------------------------- điều khiển
r1, r2, r3 = st.columns([1.2, 1, 1])
auto = r1.toggle("🔁 Tự làm mới", value=True,
                 help="Tự tải lại chủ đề, tin và công bố theo chu kỳ (chỉ phần bảng, không tải lại cả trang).")
every = r2.selectbox("Chu kỳ", [3, 5, 10, 15], index=1, format_func=lambda m: f"{m} phút",
                     disabled=not auto)
if r3.button("🔄 Làm mới ngay", use_container_width=False):
    _clear()


@st.fragment(run_every=(f"{every}m" if auto else None))
def board():
    themes, status = _themes()
    hot = H.hot_themes(themes, top=15)
    hk = H.hot_keywords(hot)
    hot_names = {t["name"] for t in hot}
    details = _details(tuple(t["no"] for t in hot[:10]))

    # tin: thị trường + tin theo mã của các mã dẫn đầu 5 chủ đề nóng nhất
    leaders = []
    for t in hot[:5]:
        for s in (details.get(t["no"]) or {}).get("stocks", [])[:3]:
            leaders.append((s["ticker"], s["name"]))
    raw_news = _market_news() + _stock_news(tuple(leaders))
    uniq = list({n["url"]: n for n in raw_news}.values())
    now = _dt.datetime.now()
    news = sorted((H.score_news(n, hk, NAMES, THEMES_OF, now) for n in uniq),
                  key=lambda x: (-x["score"], x["age_h"]))
    dart, dart_err = _dart(1)

    # ---- KPI
    up = sum(1 for t in themes if (t["chg"] or 0) > 0)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Thị trường", {"OPEN": "Đang mở", "CLOSE": "Đã đóng"}.get(status, status or "—"))
    k2.metric("Chủ đề đang tăng", f"{up}/{len(themes)}")
    k3.metric("Nóng nhất", hot[0]["name"] if hot else "—",
              _fmt_chg(hot[0]["chg"]) if hot else None)
    k4.metric("Tin giá trị cao (≥ 70)", sum(1 for n in news if n["score"] >= 70))
    k5.metric("Công bố DART đáng chú ý", sum(1 for d in dart if d["score"] >= 70))
    st.caption(f"Cập nhật {now:%H:%M:%S}" + (f" · tự làm mới mỗi {every} phút" if auto else ""))

    tab1, tab2, tab3 = st.tabs(["🔥 Chủ đề nóng", "⭐ Tin giá trị cao", "📢 Công bố DART"])

    # ================================================= TAB 1 — chủ đề
    with tab1:
        if not hot:
            st.info("Hôm nay chưa có chủ đề nào tăng (≥ 5 mã). Xem bảng đầy đủ bên dưới.")
        rows = []
        for i, t in enumerate(hot):
            st_ = (details.get(t["no"]) or {}).get("stocks", [])
            rows.append({"#": i + 1, "Chủ đề": t["name"], "% hôm nay": t["chg"],
                         "Tăng / tổng": f"{t['rise']}/{t['total']}",
                         "Độ rộng %": round(t["breadth"] or 0),
                         "Mã dẫn đầu": " · ".join(f"{s['name']} {s['chg']:+.1f}%"
                                                  for s in st_[:3] if s.get("chg") is not None)})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={
                             "% hôm nay": st.column_config.NumberColumn(format="%+.2f%%"),
                             "Độ rộng %": st.column_config.ProgressColumn(
                                 "Độ rộng (% mã tăng)", min_value=0, max_value=100, format="%d%%"),
                             "Mã dẫn đầu": st.column_config.TextColumn(width="large")})
            st.caption("**Độ rộng** = % mã trong chủ đề đang tăng. Chủ đề vừa tăng mạnh vừa rộng "
                       "(nhiều mã cùng tăng) là dòng tiền thật; tăng mạnh nhưng hẹp thường chỉ do 1–2 mã.")
        cold = [t for t in themes if t["total"] >= 5][-5:][::-1]
        if cold:
            st.caption("❄️ Giảm mạnh nhất: " + " · ".join(f"{t['name']} {t['chg']:+.2f}%" for t in cold))

        st.markdown("#### 🔎 Xem một chủ đề")
        opts = hot + [t for t in themes if t["name"] not in hot_names and t["total"] >= 3]
        pick = st.selectbox("Chủ đề", range(len(opts)),
                            format_func=lambda i: f"{opts[i]['name']} ({_fmt_chg(opts[i]['chg'])})",
                            key="news_theme")
        th = opts[pick] if opts else None
        if th:
            det = details.get(th["no"]) or _details((th["no"],)).get(th["no"]) or {}
            if det.get("desc"):
                st.info(det["desc"])
            mem = det.get("stocks", [])
            if mem:
                st.dataframe(pd.DataFrame([{
                    "Mã": s["ticker"], "Tên": s["name"], "Giá": s["price"], "% hôm nay": s["chg"],
                    "GTGD (억)": s["tv_eok"], "Vốn hóa (억)": s["cap_eok"],
                    "Vì sao thuộc chủ đề": s["reason"]} for s in mem]),
                    hide_index=True, width="stretch",
                    column_config={
                        "Mã": st.column_config.TextColumn(pinned=True),
                        "Giá": st.column_config.NumberColumn(format="%,.0f"),
                        "% hôm nay": st.column_config.NumberColumn(format="%+.2f%%"),
                        "GTGD (억)": st.column_config.NumberColumn(format="%,.0f"),
                        "Vốn hóa (억)": st.column_config.NumberColumn(format="%,.0f"),
                        "Vì sao thuộc chủ đề": st.column_config.TextColumn(width="large")})
                b1, b2 = st.columns([1, 2])
                if b1.button(f"🚀 Gửi {len(mem)} mã sang 'Siêu cổ phiếu'", key="handoff"):
                    st.session_state["handoff_list"] = {
                        "label": th["name"], "tickers": [s["ticker"] for s in mem]}
                if st.session_state.get("handoff_list", {}).get("label") == th["name"]:
                    b2.page_link("pages/1_🚀_Sieu_co_phieu.py",
                                 label="➡️ Mở trang Siêu cổ phiếu để lọc chủ đề này", icon="🚀")

                # tin của chủ đề: tin theo mã các thành viên mạnh nhất
                tnews = _stock_news(tuple((s["ticker"], s["name"]) for s in mem[:5]))
                tnews = sorted((H.score_news(n, hk, NAMES, THEMES_OF, now)
                                for n in {x["url"]: x for x in tnews}.values()),
                               key=lambda x: (-x["score"], x["age_h"]))
                tnews = [n for n in tnews if n["stocks"]][:10]
                if tnews:
                    st.markdown("**Tin của chủ đề** (xếp theo giá trị)")
                    for n in tnews:
                        nm = ", ".join(CODE2NAME.get(c, c) for c in n["stocks"])
                        st.markdown(f"`{n['score']}` {n['kind']} [{n['title']}]({n['url']}) — "
                                    f"{nm} · {n['office']} · {_ago(n['age_h'])}")

    # ================================================= TAB 2 — tin giá trị cao
    with tab2:
        f1, f2, f3 = st.columns([1, 1.6, 1])
        min_sc = f1.slider("Điểm giá trị tối thiểu", 0, 100, 60, 5, key="news_min")
        kinds = sorted({n["kind"] for n in news})
        default_k = [k for k in kinds if not k.startswith("⚪")]
        sel_k = f2.multiselect("Loại tin", kinds, default=default_k, key="news_kinds")
        only_hot = f3.checkbox("Chỉ tin thuộc chủ đề nóng", value=False, key="news_hot")
        flt = [n for n in news if n["score"] >= min_sc and n["kind"] in sel_k
               and (not only_hot or n["themes"])]
        rt = _rt(tuple(sorted({c for n in flt for c in n["stocks"]})))

        def _stk(n):
            out = []
            for c in n["stocks"]:
                r = rt.get(c, {})
                out.append(f"{CODE2NAME.get(c, c)} {_fmt_chg(r.get('chg'))}")
            return " · ".join(out)

        if not flt:
            st.info("Không có tin thỏa bộ lọc — hạ 'Điểm giá trị tối thiểu' hoặc thêm loại tin.")
        else:
            st.markdown("**Nổi bật nhất**")
            for n in flt[:5]:
                with st.container(border=True):
                    st.markdown(f"**`{n['score']}` {n['kind']} · [{n['title']}]({n['url']})**")
                    meta = [x for x in (_stk(n), ", ".join(n["themes"]), n["office"],
                                        _ago(n["age_h"])) if x]
                    st.caption(" · ".join(meta) + (f" — vì: {', '.join(n['why'])}" if n["why"] else ""))
                    if n.get("summary"):
                        st.write(n["summary"][:220] + ("…" if len(n["summary"]) > 220 else ""))
            st.dataframe(pd.DataFrame([{
                "Điểm": n["score"], "Loại": n["kind"], "Tiêu đề": n["title"],
                "Mã liên quan": _stk(n), "Chủ đề nóng": ", ".join(n["themes"]),
                "Vì sao giá trị": ", ".join(n["why"]), "Nguồn": n["office"],
                "Giờ": n["dt"].strftime("%d/%m %H:%M") if n.get("dt") else "",
                "Link": n["url"]} for n in flt]),
                hide_index=True, width="stretch",
                column_config={
                    "Điểm": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
                    "Tiêu đề": st.column_config.TextColumn(width="large"),
                    "Link": st.column_config.LinkColumn(display_text="Đọc ↗")})
        st.caption("Điểm giá trị: 🟢 Xúc tác mạnh (hợp đồng, kết quả kỷ lục, chuyển lãi, phê duyệt, "
                   "M&A, 상한가…) 70 · 🔴 Rủi ro (유상증자, CB, kiện tụng…) 60 · 🔵 Phân tích/dự báo 50 · "
                   "🟢 Tích cực 45 · ⚪ Tin chung 25 · ⚪ Thị trường chung 30; cộng thêm: 특징주 +15, có số "
                   "tiền +10, thuộc chủ đề nóng +10, có mã cụ thể +5, mới ≤ 3 giờ +10. "
                   "Chấm theo từ khóa trong tiêu đề — hãy đọc tin gốc.")

    # ================================================= TAB 3 — DART
    with tab3:
        g1, g2, g3 = st.columns([1, 1, 1.4])
        days = g1.selectbox("Khoảng thời gian", [1, 2, 3, 5], format_func=lambda d: f"{d} ngày",
                            key="dart_days")
        dmin = g2.slider("Điểm tối thiểu", 0, 100, 60, 5, key="dart_min")
        pol_sel = g3.multiselect("Chiều", ["🟢 Xúc tác", "🔴 Rủi ro", "⚪ Trọng yếu"],
                                 default=["🟢 Xúc tác", "🔴 Rủi ro", "⚪ Trọng yếu"], key="dart_pol")
        dl, derr = (dart, dart_err) if days == 1 else _dart(days)
        if derr:
            st.warning(f"DART: {derr}. Cần `DART_API_KEY` trong `.env` (local) hoặc Secrets (cloud).")
        polname = {1: "🟢 Xúc tác", -1: "🔴 Rủi ro", 0: "⚪ Trọng yếu"}
        dl = [d for d in dl if d["score"] >= dmin and polname[d["pol"]] in pol_sel]
        if not dl and not derr:
            st.info("Không có công bố thỏa bộ lọc.")
        elif dl:
            rtd = _rt(tuple(sorted({d["ticker"] for d in dl})))
            c1_, c2_, c3_ = st.columns(3)
            c1_.metric("🟢 Xúc tác", sum(1 for d in dl if d["pol"] > 0))
            c2_.metric("🔴 Rủi ro", sum(1 for d in dl if d["pol"] < 0))
            c3_.metric("⚪ Trọng yếu", sum(1 for d in dl if d["pol"] == 0))
            st.dataframe(pd.DataFrame([{
                "Điểm": d["score"], "Chiều": polname[d["pol"]],
                "Ngày": f"{d['date'][6:8]}/{d['date'][4:6]}", "Sàn": d["market"],
                "Mã": d["ticker"], "Tên": d["name"],
                "% hôm nay": (rtd.get(d["ticker"]) or {}).get("chg"),
                "Loại công bố": d["label"],
                "Chủ đề nóng": ", ".join(x for x in THEMES_OF.get(d["ticker"], []) if x in hot_names),
                "Báo cáo gốc": d["report"], "Link": d["url"]}
                for d in sorted(dl, key=lambda x: (-x["score"], x["rcept_no"]))]),
                hide_index=True, width="stretch",
                column_config={
                    "Điểm": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
                    "% hôm nay": st.column_config.NumberColumn(format="%+.2f%%"),
                    "Báo cáo gốc": st.column_config.TextColumn(width="medium"),
                    "Link": st.column_config.LinkColumn(display_text="DART ↗")})
        st.caption("Công bố chính thức của doanh nghiệp niêm yết — nguồn tin giá trị nhất. Chỉ giữ loại có "
                   "giá trị: hợp đồng cung cấp, kết quả kinh doanh sơ bộ, chuyển giao công nghệ, cấp phép, "
                   "mua/hủy cổ phiếu quỹ, M&A… (🟢/⚪) và rủi ro: phát hành thêm, CB/BW, kiện tụng, tham ô, "
                   "xem xét niêm yết… (🔴). Bỏ các báo cáo thủ tục (sở hữu cổ đông nội bộ, IR, đính chính "
                   "bị trừ 10 điểm).")


board()
st.caption("⚠️ Công cụ ưu tiên đọc tin — không phải khuyến nghị đầu tư. Tin chạy theo chủ đề ở Hàn biến "
           "động rất nhanh; luôn đọc tin gốc / công bố gốc trước khi quyết định.")
