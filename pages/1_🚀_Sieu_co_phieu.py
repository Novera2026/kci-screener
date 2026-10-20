"""
🚀 Tìm siêu cổ phiếu TIỀM NĂNG — chạy khi cần.
Lọc mã có cấu trúc tăng (biểu đồ + tăng trưởng + tin tức), LOẠI mã đã chạy quá xa,
ưu tiên mã thuộc nhóm đang dẫn dắt thị trường (meta). Logic ở growth.py.
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
import growth as G                                   # noqa: E402
from financials import fetch_financials              # noqa: E402
from screener import TickerRegistry                  # noqa: E402

st.set_page_config(page_title="Siêu cổ phiếu — KCI", page_icon="🚀", layout="wide")
st.title("🚀 Tìm siêu cổ phiếu tiềm năng")
st.caption("Bấm **Tìm** khi cần. Kết quả: mã có cấu trúc tăng (biểu đồ + doanh thu/lợi nhuận "
           "+ tin tức), **chưa chạy quá xa**, ưu tiên mã thuộc **nhóm đang dẫn dắt (meta)**. "
           "Đây là bộ lọc theo quy tắc, không phải khuyến nghị mua.")

with st.expander("📖 Cách chấm điểm"):
    st.markdown("""
**1. Biểu đồ — Trend Template của Minervini (8 tiêu chí)**: giá trên MA150 & MA200 · MA150 > MA200 ·
MA200 đi lên ≥ 1 tháng · MA50 > MA150 & MA200 · giá > MA50 · ≥ 30% trên đáy 52 tuần ·
trong 25% từ đỉnh 52 tuần · **RS** (sức mạnh giá so với toàn thị trường, 1–99) ≥ ngưỡng.

**2. Giai đoạn — tránh đu đỉnh**
- 🔥 **Đã chạy xa** (mặc định LOẠI): cách MA50 > 25% · tăng > 200% từ đáy 52 tuần ·
  > +100% trong 3 tháng · RSI > 80. Mua lúc này rủi ro lớn: dễ điều chỉnh 20–40%.
- 🌿 **Đang chạy**: cách MA50 > 15% · tăng > 100% từ đáy · đã vượt đỉnh nền > 10%.
- 🌱 **Sớm**: còn gần nền / điểm mua — vùng rủi ro thấp, dư địa lớn nhất.

**3. Meta — nhóm dẫn dắt**: chấm sức mạnh từng **chủ đề** (259 chủ đề Naver) và **ngành** trên dữ liệu
vừa quét = RS trung vị + % thành viên đạt xu hướng; nhóm ít mã được co về mức trung bình để khỏi nhiễu.
Mã **đúng meta** nếu thuộc top chủ đề hoặc top 5 ngành.

**4. Tăng trưởng (CAN SLIM)**: DT & LN thuần quý so cùng kỳ ≥ 25% (lỗ → lãi cũng tính) · biên LN HĐ mở rộng ·
DT năm ≥ 15% · đồng thuận LN năm tới tăng.

**5. Tin tức (Naver)**: từ khóa xúc tác tích cực/tiêu cực trong tiêu đề; tin tổng hợp thị trường tính trung tính.

**Điểm tiềm năng** = 35% Biểu đồ + 35% Tăng trưởng + 20% Meta + 10% Tin tức.
🚀 **Siêu cổ phiếu tiềm năng** = biểu đồ ≥ 7/8 · tăng trưởng ≥ 80% · RS ≥ 80 · đúng meta · chưa chạy xa.
""")


# ---------------------------------------------------------------- cache
@st.cache_resource
def _reg():
    return TickerRegistry()


@st.cache_resource
def _store() -> dict:
    """Cache dùng chung giữa các luồng & các lần tìm trong ngày."""
    return {}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _universe(min_cap: float, markets: tuple) -> list:
    return G.universe_by_cap(min_cap, markets)


@st.cache_data(show_spinner=False)
def _themes():
    themes = collections.defaultdict(list)
    path = os.path.join(_ROOT, "themes_all.csv")
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="utf-8")):
            themes[r["theme_kr"]].append(str(r["ticker"]).zfill(6))
    themes_of = collections.defaultdict(list)
    for th, tks in themes.items():
        for t in tks:
            themes_of[t].append(th)
    return dict(themes), dict(themes_of)


reg = _reg()
store = _store()
_today = str(_dt.date.today())
_hour = _dt.datetime.now().strftime("%Y%m%d%H")
THEMES, THEMES_OF = _themes()


def _cached(kind, tk, key, fn):
    k = (kind, tk, key)
    if k not in store:
        store[k] = fn(tk)
    return store[k]


def _daily(tk):
    return _cached("d", tk, _today, G.fetch_daily)


def _fin(tk):
    return _cached("f", tk, _today, fetch_financials)


def _news(tk):
    return _cached("n", tk, _hour, G.fetch_news)


def _fmt_pct(x):
    if x is None:
        return "—"
    if x == float("inf"):
        return "Chuyển lãi"
    if x > 999:
        return ">+999%"
    return f"{x:+.0f}%"


# ---------------------------------------------------------------- điều khiển
c1, c2, c3 = st.columns([2, 1.4, 1.2])
scope = c1.radio("Phạm vi tìm", ["Toàn sàn (lọc vốn hóa)", "Danh sách ở trang chính",
                                 "Nhập tay"], horizontal=True)
markets = c2.multiselect("Sàn", ["KOSPI", "KOSDAQ"], default=["KOSPI", "KOSDAQ"])
min_cap = c3.number_input("Vốn hóa tối thiểu (억)", 100, 1_000_000, 1000, 500,
                          help="1.000억 ≈ 1 nghìn tỷ won. Bỏ mã quá nhỏ, thanh khoản kém.")
manual = ""
if scope == "Nhập tay":
    manual = st.text_area("Danh sách mã (mỗi dòng 1 mã / tên)", "192820\n028670\n006400",
                          height=100)

f1, f2 = st.columns([1.6, 1])
stages_ok = f1.multiselect("Giai đoạn cho phép", ["🌱 Sớm", "🌿 Đang chạy", "🔥 Đã chạy xa"],
                           default=["🌱 Sớm", "🌿 Đang chạy"],
                           help="Mặc định LOẠI mã đã chạy quá xa để tránh mua đu đỉnh.")
only_meta = f2.checkbox("Chỉ mã đúng meta (nhóm dẫn dắt)", value=True)

with st.expander("⚙️ Ngưỡng nâng cao"):
    a1, a2, a3, a4 = st.columns(4)
    lim = {"ext_ma50": a1.slider("'Chạy xa' khi cách MA50 > (%)", 15, 60, 25, 5),
           "from_low": a2.slider("… hoặc tăng từ đáy 52T > (%)", 100, 500, 200, 25),
           "ret_3m": a3.slider("… hoặc tăng 3 tháng > (%)", 50, 200, 100, 10),
           "rsi": a4.slider("… hoặc RSI > ", 70, 90, 80, 1)}
    b1, b2, b3, b4, b5, b6 = st.columns(6)
    chart_min = b1.slider("Biểu đồ tối thiểu (/8)", 4, 8, 6)
    rs_min = b2.slider("RS tối thiểu", 50, 95, 70, 5)
    rev_th = b3.slider("DT quý YoY ≥ (%)", 10, 100, 25, 5)
    np_th = b4.slider("LN quý YoY ≥ (%)", 10, 100, 25, 5)
    top_k = b5.slider("Meta = top chủ đề", 5, 30, 10, 1)
    top_n = b6.slider("Số mã phân tích sâu", 10, 80, 40, 5)

run = st.button("🔎 Tìm siêu cổ phiếu tiềm năng", type="primary")

# ---------------------------------------------------------------- tìm
if run:
    full = scope.startswith("Toàn sàn")
    if full:
        if not markets:
            st.error("Chọn ít nhất 1 sàn.")
            st.stop()
        uni = _universe(float(min_cap), tuple(markets))
    else:
        raw = (st.session_state.get("raw_input", "") if scope == "Danh sách ở trang chính"
               else manual)
        qs = [x.strip() for x in raw.replace(",", "\n").splitlines() if x.strip()]
        uni = []
        for q in qs:
            tk = reg.resolve(q)
            if tk:
                uni.append({"ticker": tk, "name": reg.name_of(tk) or q, "market": "",
                            "cap_eok": None, "price": None})
        if not uni:
            st.error("Không có mã hợp lệ. Ở trang chính hãy nhập danh sách trước, "
                     "hoặc chọn 'Nhập tay'.")
            st.stop()
    names = {u["ticker"]: u["name"] for u in uni}
    caps = {u["ticker"]: u["cap_eok"] for u in uni}

    # Bước 1 — biểu đồ toàn vũ trụ
    bar = st.progress(0.0, text=f"Bước 1/2 · Tải biểu đồ {len(uni)} mã…")
    daily = G.parallel_map(_daily, [u["ticker"] for u in uni],
                           progress=lambda d, n: bar.progress(d / n,
                           text=f"Bước 1/2 · Biểu đồ {d}/{n} mã"))
    mets = {tk: G.chart_metrics(d) for tk, d in daily.items() if d}
    mets = {tk: m for tk, m in mets.items() if m}

    ref = None if full else store.get(("rsdist", _today))
    G.assign_rs(mets, ref=ref)
    for m in mets.values():
        G.finalize_chart(m, rs_min)
        m["stage"], m["stage_why"] = G.stage_of(m, lim)

    ind_of = {tk: reg.industry_of(tk) for tk in mets}
    if full and len(mets) >= 300:
        inds = collections.defaultdict(list)
        for tk, ind in ind_of.items():
            if ind:
                inds[ind].append(tk)
        theme_rank = G.group_strength(mets, THEMES)
        ind_rank = G.group_strength(mets, inds)
        store[("rsdist", _today)] = [m["rs_raw"] for m in mets.values()]
        store[("meta", _today)] = (theme_rank, ind_rank)
    else:
        theme_rank, ind_rank = store.get(("meta", _today), ({}, {}))
    meta_ok = bool(theme_rank)

    for tk, m in mets.items():
        m["meta"] = (G.meta_for(tk, THEMES_OF, ind_of, theme_rank, ind_rank, top_k)
                     if meta_ok else {"in_meta": None, "meta_pct": None, "theme": None,
                                      "theme_rank": None, "ind_rank": None})

    trend = [tk for tk, m in mets.items() if m["chart_pass"] >= chart_min]
    extended = [tk for tk in trend if mets[tk]["stage"].startswith("🔥")]
    cand = [tk for tk in trend if mets[tk]["stage"] in stages_ok]
    hidden_meta = []
    if only_meta and meta_ok:
        hidden_meta = [tk for tk in cand if not mets[tk]["meta"]["in_meta"]]
        cand = [tk for tk in cand if mets[tk]["meta"]["in_meta"]]
    cand.sort(key=lambda t: (-(mets[t]["meta"]["meta_pct"] or 0) * 0.5
                             - mets[t]["chart_pass"] * 5 - mets[t]["rs"] * 0.5))
    short = cand[:top_n]

    # Bước 2 — BCTC + tin tức cho ứng viên
    bar.progress(0.0, text=f"Bước 2/2 · Phân tích sâu {len(short)} mã…")
    fins = G.parallel_map(_fin, short)
    bar.progress(0.5, text="Bước 2/2 · Đọc tin tức…")
    news = G.parallel_map(_news, short)
    bar.empty()

    rows, details = [], {}
    for tk in short:
        m = mets[tk]
        mt = m["meta"]
        g = G.growth_metrics(fins.get(tk) or {})
        gc = G.growth_criteria(g, rev_th, np_th)
        nm = G.news_metrics(news.get(tk) or [])
        chart_pct = m["chart_pass"] / 8 * 100
        g_pct, n_pct = G.score_pct(gc), G.score_pct(nm["crit"])
        meta_pct = mt["meta_pct"] or 0
        total = 0.35 * chart_pct + 0.35 * (g_pct or 0) + 0.20 * meta_pct + 0.10 * (n_pct or 0)
        in_meta = mt["in_meta"] if mt["in_meta"] is not None else True
        rows.append({
            "Mã": tk, "Tên": names.get(tk, tk),
            "Nhãn": G.label(m["chart_pass"], g_pct, m.get("rs"), m["stage"], in_meta),
            "Điểm tiềm năng": round(total),
            "Giai đoạn": m["stage"],
            "Meta": ("✓ " if mt["in_meta"] else "") + (mt.get("why") or "—"),
            "Biểu đồ /8": m["chart_pass"], "RS": m.get("rs"),
            "Tăng trưởng %": round(g_pct) if g_pct is not None else None,
            "Tin tức %": round(n_pct) if n_pct is not None else None,
            "DT quý YoY": _fmt_pct(g.get("rev_yoy")),
            "LN quý YoY": _fmt_pct(g.get("np_yoy")),
            "Giá": m["price"],
            "Cách MA50 %": round(m["ext_ma50"], 1) if m.get("ext_ma50") is not None else None,
            "Từ đáy 52T %": round(m["pct_from_lo"]) if m.get("pct_from_lo") is not None else None,
            "Từ đỉnh 52T %": round(m["pct_from_hi"], 1),
            "RSI": round(m["rsi14"]) if m.get("rsi14") is not None else None,
            "Nền siết": round(m["tight"], 2) if m.get("tight") is not None else None,
            "Gần điểm mua": "🎯" if m["near_pivot"] else "",
            "Ngành": ind_of.get(tk) or "",
            "Vốn hóa (억)": round(caps[tk]) if caps.get(tk) else None,
        })
        details[tk] = {"m": m, "g": g, "gc": gc, "nm": nm, "d": daily.get(tk)}
    _tier = {"🚀": 0, "📈": 1, "👀": 2, "🔥": 3}
    rows.sort(key=lambda r: (_tier.get(r["Nhãn"][:1], 4), -r["Điểm tiềm năng"]))

    ext_rows = sorted(({"Mã": tk, "Tên": names.get(tk, tk), "RS": mets[tk]["rs"],
                        "Biểu đồ /8": mets[tk]["chart_pass"],
                        "Lý do loại": "; ".join(mets[tk]["stage_why"])} for tk in extended),
                      key=lambda r: -r["RS"])
    meta_rows = [{"#": v["rank"], "Chủ đề": k, "RS trung vị": v["med_rs"],
                  "% đạt xu hướng": round(v["breadth"]), "3 tháng %": round(v["ret3m"]),
                  "Số mã": v["n"]}
                 for k, v in sorted(theme_rank.items(), key=lambda kv: kv[1]["rank"])[:top_k]]
    ind_rows = [{"#": v["rank"], "Ngành": k, "RS trung vị": v["med_rs"],
                 "% đạt xu hướng": round(v["breadth"]), "3 tháng %": round(v["ret3m"]),
                 "Số mã": v["n"]}
                for k, v in sorted(ind_rank.items(), key=lambda kv: kv[1]["rank"])[:5]]

    st.session_state["growth_scan"] = {
        "rows": rows, "details": details, "ext_rows": ext_rows, "meta_rows": meta_rows,
        "ind_rows": ind_rows, "meta_ok": meta_ok,
        "at": _dt.datetime.now().strftime("%H:%M %d/%m"),
        "n_uni": len(uni), "n_trend": len(trend), "n_ext": len(extended),
        "n_cand": len(cand), "n_hidden_meta": len(hidden_meta),
        "hidden_meta": [f"{names.get(t, t)} ({mets[t]['meta'].get('why')})"
                        for t in sorted(hidden_meta, key=lambda t: -mets[t]['rs'])[:8]],
        "rs_basis": "toàn sàn" if (full or ref) else "chỉ danh sách này"}

# ---------------------------------------------------------------- kết quả
res = st.session_state.get("growth_scan")
if res:
    rows, details = res["rows"], res["details"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Mã đã quét", f"{res['n_uni']:,}")
    k2.metric("Có xu hướng tăng", f"{res['n_trend']:,}")
    k3.metric("🔥 Loại vì chạy xa", f"{res['n_ext']:,}")
    k4.metric("Ứng viên tiềm năng", f"{res['n_cand']:,}")
    k5.metric("🚀 Siêu CP tiềm năng", sum(1 for r in rows if r["Nhãn"].startswith("🚀")))
    st.caption(f"Tìm lúc {res['at']} · giá đóng cửa gần nhất (Naver) · "
               f"RS xếp hạng so với **{res['rs_basis']}**.")
    if not res["meta_ok"]:
        st.info("ℹ️ Chưa xác định được meta (nhóm dẫn dắt) hôm nay. Chạy **Toàn sàn** một lần — "
                "sau đó các danh sách ngắn sẽ dùng meta + RS của cả thị trường.")

    if res["meta_rows"]:
        with st.expander("🧭 Meta hôm nay — nhóm đang dẫn dắt", expanded=True):
            m1, m2 = st.columns([1.6, 1])
            m1.dataframe(pd.DataFrame(res["meta_rows"]), hide_index=True, width="stretch")
            m2.dataframe(pd.DataFrame(res["ind_rows"]), hide_index=True, width="stretch")
            st.caption("Xếp hạng theo dữ liệu vừa quét: RS trung vị của nhóm + % thành viên "
                       "đạt xu hướng ≥ 6/8. Meta xoay vòng theo dòng tiền — kiểm lại mỗi ngày.")

    if not rows:
        st.info("Không mã nào thỏa bộ lọc. Thử nới: bỏ 'Chỉ mã đúng meta', thêm giai đoạn "
                "🌿, hoặc hạ 'Biểu đồ tối thiểu'.")
    else:
        df = pd.DataFrame(rows)
        if st.checkbox("⚡ Cập nhật giá realtime + cảnh báo mã từ Toss", value=False):
            with st.spinner("Đang gọi Toss…"):
                prices, warns, err = G.toss_overlay(df["Mã"].tolist())
            if err:
                st.warning(f"Toss chưa dùng được ({err}). Nếu là 401: key trong `.env` "
                           "(TOSS_CLIENT_ID / TOSS_CLIENT_SECRET) đã hết hiệu lực — "
                           "phát hành key mới trong app Toss증권 → Open API.")
            else:
                df.insert(df.columns.get_loc("Giá") + 1, "Giá Toss",
                          [prices.get(t) for t in df["Mã"]])
                df["Cảnh báo Toss"] = [", ".join(warns.get(t, [])) for t in df["Mã"]]
                if warns:
                    st.warning(f"⚠️ {len(warns)} mã đang bị sàn cảnh báo — xem cột 'Cảnh báo Toss'.")

        st.dataframe(
            df, hide_index=True, width="stretch", height=min(40 + 35 * len(df), 720),
            column_config={
                "Mã": st.column_config.TextColumn(pinned=True),
                "Tên": st.column_config.TextColumn(pinned=True),
                "Điểm tiềm năng": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d"),
                "Tăng trưởng %": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d%%"),
                "Tin tức %": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d%%"),
                "Giá": st.column_config.NumberColumn(format="%,.0f"),
                "Vốn hóa (억)": st.column_config.NumberColumn(format="%,.0f"),
            })
        st.download_button("⬇️ Tải CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"sieu_co_phieu_tiem_nang_{_today}.csv", mime="text/csv")

    if res.get("n_hidden_meta"):
        st.caption(f"👁️ {res['n_hidden_meta']} mã có xu hướng tốt nhưng **không thuộc meta** nên bị ẩn"
                   f" (vd: {', '.join(res['hidden_meta'][:5])}). Bỏ chọn 'Chỉ mã đúng meta' để xem.")

    if res["ext_rows"]:
        with st.expander(f"🔥 Đã chạy xa — đã loại ({len(res['ext_rows'])} mã, không nên đuổi)"):
            st.dataframe(pd.DataFrame(res["ext_rows"]), hide_index=True, width="stretch")
            st.caption("Các mã này vẫn có xu hướng tăng nhưng đã giãn xa khỏi nền giá. "
                       "Theo dõi để chờ nền giá mới (điều chỉnh/tích lũy) thay vì mua đuổi.")

    # ------------------------------------------------ chi tiết
    if rows:
        st.subheader("🔎 Chi tiết 1 mã")
        pick = st.selectbox("Chọn mã", [f"{r['Mã']} · {r['Tên']} · {r['Nhãn']}" for r in rows])
        tk = pick.split(" · ")[0]
        dt = details[tk]
        m, g, gc, nm, d = dt["m"], dt["g"], dt["gc"], dt["nm"], dt["d"]

        mt = m["meta"]
        st.markdown(f"**Giai đoạn: {m['stage']}** — {'; '.join(m['stage_why'])} · "
                    f"**Meta:** {'✓' if mt['in_meta'] else '✗'} {mt.get('why') or '—'}")
        if d:
            px = pd.DataFrame({"Giá": d["close"]},
                              index=pd.to_datetime(d["date"], format="%Y%m%d"))
            for n_ in (50, 150, 200):
                px[f"MA{n_}"] = px["Giá"].rolling(n_).mean()
            st.line_chart(px.tail(260), height=320)
            vol = pd.DataFrame({"Khối lượng": d["volume"]},
                               index=pd.to_datetime(d["date"], format="%Y%m%d")).tail(120)
            st.bar_chart(vol, height=140)

        a1, a2, a3 = st.columns(3)
        with a1:
            st.markdown(f"**📈 Biểu đồ — {m['chart_pass']}/8** · RS {m.get('rs')}")
            for k, ok in m["crit"].items():
                st.write(("✅ " if ok else "❌ ") + k)
            _flo = m.get("pct_from_lo")
            st.caption(f"Cách MA50 {m['ext_ma50']:+.0f}% · từ đáy 52T "
                       f"{'+%.0f%%' % _flo if _flo is not None else '—'} · "
                       f"RSI {m['rsi14']:.0f} · nền siết {m.get('tight') and round(m['tight'], 2)}")
        with a2:
            gp = G.score_pct(gc)
            st.markdown(f"**💹 Tăng trưởng — {round(gp) if gp is not None else '—'}%**"
                        f" · quý {g.get('q_period', '—')}")
            for k, ok in gc.items():
                st.write(("— " if ok is None else "✅ " if ok else "❌ ") + k)
            st.caption(f"DT YoY {_fmt_pct(g.get('rev_yoy'))} · LN YoY {_fmt_pct(g.get('np_yoy'))} "
                       f"· LN năm tới {_fmt_pct(g.get('np_fwd'))}")
        with a3:
            npc = G.score_pct(nm["crit"])
            st.markdown(f"**📰 Tin tức — {round(npc) if npc is not None else '—'}%** "
                        f"· {nm['n7']} tin/7 ngày · +{nm['pos']}/−{nm['neg']}")
            for k, ok in nm["crit"].items():
                st.write(("✅ " if ok else "❌ ") + k)

        if g.get("quarters"):
            qt = pd.DataFrame([{
                "Quý": f"{c['period'][:4]}.{c['period'][4:6]}",
                "Doanh thu (억)": c.get("revenue"), "LN HĐ (억)": c.get("op_profit"),
                "LN thuần (억)": c.get("net_profit"),
                "Biên LN HĐ %": (round(c["op_profit"] / c["revenue"] * 100, 1)
                                 if c.get("op_profit") is not None and c.get("revenue")
                                 else None)} for c in g["quarters"]])
            st.dataframe(qt, hide_index=True, width="stretch",
                         column_config={k: st.column_config.NumberColumn(format="%,.0f")
                                        for k in ("Doanh thu (억)", "LN HĐ (억)", "LN thuần (억)")})

        st.markdown("**Tin gần nhất** (✅ tích cực · ⚠️ tiêu cực · · trung tính)")
        for it in nm["items"]:
            ic = "✅" if it.get("tag", 0) > 0 else "⚠️" if it.get("tag", 0) < 0 else "·"
            st.markdown(f"{ic} `{it['dt'][4:6]}/{it['dt'][6:8]}` [{it['title']}]({it['url']}) "
                        f"— {it['office']}")

st.caption("⚠️ Bộ lọc theo quy tắc cố định — không phải khuyến nghị đầu tư. "
           "Luôn tự kiểm tra biểu đồ, BCTC gốc và tin tức trước khi quyết định.")
