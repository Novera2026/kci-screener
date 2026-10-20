"""
🚀 Siêu cổ phiếu tăng trưởng — quét toàn sàn theo 3 trụ: Biểu đồ · Tăng trưởng · Tin tức.
Logic nằm ở growth.py; trang này chỉ là giao diện.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import growth as G                                   # noqa: E402
from financials import fetch_financials              # noqa: E402
from screener import TickerRegistry                  # noqa: E402

st.set_page_config(page_title="Siêu cổ phiếu — KCI", page_icon="🚀", layout="wide")
st.title("🚀 Siêu cổ phiếu tăng trưởng")
st.caption("Quét cổ phiếu đang có cấu trúc tăng: **biểu đồ** (Trend Template) + "
           "**doanh thu & lợi nhuận** tăng mạnh + **tin tức** xúc tác. "
           "Đây là bộ lọc theo quy tắc, không phải khuyến nghị mua.")

with st.expander("📖 Phương pháp chấm điểm"):
    st.markdown("""
**1. Biểu đồ — Trend Template của Mark Minervini (8 tiêu chí)**
Giá trên MA150 & MA200 · MA150 > MA200 · MA200 đi lên ≥ 1 tháng · MA50 > MA150 & MA200 ·
Giá > MA50 · Giá ≥ 30% trên đáy 52 tuần · Giá trong 25% từ đỉnh 52 tuần ·
**RS** (sức mạnh giá tương đối, 1–99, kiểu IBD: so với toàn bộ mã được quét) ≥ ngưỡng.
Tín hiệu phụ: **Tích lũy** (khối lượng ngày tăng / ngày giảm, >1 là có tiền vào),
**Nền siết** (biên độ 10 phiên ÷ 50 phiên, càng nhỏ càng chặt), **Gần điểm mua** (≤5% dưới đỉnh + nền chặt).

**2. Tăng trưởng — kiểu CAN SLIM**
DT quý so cùng kỳ ≥ 25% · LN thuần quý so cùng kỳ ≥ 25% (hoặc lỗ → lãi) ·
Biên LN HĐ mở rộng · DT năm tăng ≥ 15% · Đồng thuận LN năm tới tăng.

**3. Tin tức — Naver**
Có tin xúc tác tích cực (수주, 공급계약, 신고가, 흑자전환, 목표가 상향…) · tích cực > tiêu cực ·
được chú ý (≥ 3 tin/7 ngày). Tin tổng hợp thị trường (코스피, 증시…) tính là trung tính.
*Chỉ đọc tiêu đề theo từ khóa — chưa hiểu ngữ cảnh, hãy đọc tin gốc.*

**Điểm tổng** = 40% Biểu đồ + 40% Tăng trưởng + 20% Tin tức.
🚀 **Siêu tăng trưởng**: biểu đồ ≥ 7/8, tăng trưởng ≥ 80%, RS ≥ 80 · 📈 **Đang setup**: biểu đồ ≥ 6/8, tăng trưởng ≥ 60% · 👀 **Theo dõi**: còn lại.
""")


# ---------------------------------------------------------------- cache
@st.cache_resource
def _reg():
    return TickerRegistry()


@st.cache_resource
def _store() -> dict:
    """Cache dùng chung giữa các luồng (st.cache_data không gọi an toàn từ luồng phụ)."""
    return {}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _universe(min_cap: float, markets: tuple) -> list:
    return G.universe_by_cap(min_cap, markets)


reg = _reg()
store = _store()
_today = str(_dt.date.today())
_hour = _dt.datetime.now().strftime("%Y%m%d%H")


def _daily(tk):
    k = ("d", tk, _today)
    if k not in store:
        store[k] = G.fetch_daily(tk)
    return store[k]


def _fin(tk):
    k = ("f", tk, _today)
    if k not in store:
        store[k] = fetch_financials(tk)
    return store[k]


def _news(tk):
    k = ("n", tk, _hour)
    if k not in store:
        store[k] = G.fetch_news(tk)
    return store[k]


def _fmt_pct(x):
    if x is None:
        return "—"
    if x == float("inf"):
        return "Chuyển lãi"
    return f"{x:+.0f}%"


# ---------------------------------------------------------------- điều khiển
c1, c2, c3 = st.columns([2, 1.4, 1.2])
scope = c1.radio("Phạm vi quét", ["Toàn sàn (lọc vốn hóa)", "Danh sách ở trang chính",
                                  "Nhập tay"], horizontal=True)
markets = c2.multiselect("Sàn", ["KOSPI", "KOSDAQ"], default=["KOSPI", "KOSDAQ"])
min_cap = c3.number_input("Vốn hóa tối thiểu (억)", 100, 1_000_000, 1000, 500,
                          help="1.000억 ≈ 1 nghìn tỷ won. Bỏ bớt mã quá nhỏ, thanh khoản kém.")
manual = ""
if scope == "Nhập tay":
    manual = st.text_area("Danh sách mã (mỗi dòng 1 mã / tên)", "000660\n042700\n192820",
                          height=100)

with st.expander("⚙️ Ngưỡng lọc"):
    t1, t2, t3, t4, t5 = st.columns(5)
    chart_min = t1.slider("Biểu đồ tối thiểu (/8)", 4, 8, 6)
    rs_min = t2.slider("RS tối thiểu", 50, 95, 70, 5)
    rev_th = t3.slider("DT quý YoY ≥ (%)", 10, 100, 25, 5)
    np_th = t4.slider("LN quý YoY ≥ (%)", 10, 100, 25, 5)
    top_n = t5.slider("Số mã phân tích sâu", 10, 80, 40, 5,
                      help="Sau bước lọc biểu đồ, lấy N mã mạnh nhất để kéo BCTC + tin tức.")

run = st.button("🔎 Quét", type="primary")

# ---------------------------------------------------------------- quét
if run:
    if scope == "Toàn sàn (lọc vốn hóa)":
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

    # Bước 1: biểu đồ cho toàn vũ trụ
    bar = st.progress(0.0, text=f"Bước 1/2 · Tải biểu đồ {len(uni)} mã…")
    daily = G.parallel_map(_daily, [u["ticker"] for u in uni],
                           progress=lambda d, n: bar.progress(d / n,
                           text=f"Bước 1/2 · Biểu đồ {d}/{n} mã"))
    mets = {tk: G.chart_metrics(d) for tk, d in daily.items() if d}
    mets = {tk: m for tk, m in mets.items() if m}
    full = scope.startswith("Toàn sàn")
    ref = store.get(("rsdist", _today)) if not full else None
    G.assign_rs(mets, ref=ref)
    if full and len(mets) >= 300:            # lưu phân phối RS toàn sàn cho các lần quét ngắn
        store[("rsdist", _today)] = [m["rs_raw"] for m in mets.values()]
    for m in mets.values():
        G.finalize_chart(m, rs_min)
    passed = [tk for tk, m in mets.items() if m["chart_pass"] >= chart_min]
    short = sorted(passed, key=lambda t: (-mets[t]["chart_pass"], -mets[t]["rs"]))[:top_n]

    # Bước 2: BCTC + tin tức cho danh sách rút gọn
    bar.progress(0.0, text=f"Bước 2/2 · Phân tích sâu {len(short)} mã…")
    fins = G.parallel_map(_fin, short)
    bar.progress(0.5, text="Bước 2/2 · Đọc tin tức…")
    news = G.parallel_map(_news, short)
    bar.empty()

    rows, details = [], {}
    for tk in short:
        m = mets[tk]
        g = G.growth_metrics(fins.get(tk) or {})
        gc = G.growth_criteria(g, rev_th, np_th)
        nm = G.news_metrics(news.get(tk) or [])
        chart_pct = m["chart_pass"] / 8 * 100
        g_pct, n_pct = G.score_pct(gc), G.score_pct(nm["crit"])
        total = 0.4 * chart_pct + 0.4 * (g_pct or 0) + 0.2 * (n_pct or 0)
        ind = reg.industry_of(tk) or reg.sector_of(tk) or ""
        rows.append({
            "Mã": tk, "Tên": names.get(tk, tk), "Ngành": ind,
            "Nhãn": G.label(m["chart_pass"], g_pct, m.get("rs")),
            "Điểm tổng": round(total),
            "Biểu đồ /8": m["chart_pass"], "RS": m.get("rs"),
            "Tăng trưởng %": round(g_pct) if g_pct is not None else None,
            "Tin tức %": round(n_pct) if n_pct is not None else None,
            "DT quý YoY": _fmt_pct(g.get("rev_yoy")),
            "LN quý YoY": _fmt_pct(g.get("np_yoy")),
            "Giá": m["price"],
            "Từ đỉnh 52T %": round(m["pct_from_hi"], 1),
            "3 tháng %": round(m["ret_3m"], 1),
            "Tích lũy KL": round(m["ud_ratio"], 2) if m.get("ud_ratio") else None,
            "Nền siết": round(m["tight"], 2) if m.get("tight") is not None else None,
            "Gần điểm mua": "🎯" if m["near_pivot"] else "",
            "Vốn hóa (억)": round(caps[tk]) if caps.get(tk) else None,
        })
        details[tk] = {"m": m, "g": g, "gc": gc, "nm": nm, "d": daily.get(tk)}
    _tier = {"🚀": 0, "📈": 1, "👀": 2}
    rows.sort(key=lambda r: (_tier.get(r["Nhãn"][:1], 3), -r["Điểm tổng"]))
    st.session_state["growth_scan"] = {
        "rows": rows, "details": details, "at": _dt.datetime.now().strftime("%H:%M %d/%m"),
        "n_uni": len(uni), "n_chart": len(mets), "n_pass": len(passed),
        "rs_basis": ("toàn sàn" if (full or ref) else "chỉ trong danh sách này")}

# ---------------------------------------------------------------- kết quả
res = st.session_state.get("growth_scan")
if res:
    rows, details = res["rows"], res["details"]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Mã đã quét", f"{res['n_uni']:,}")
    k2.metric(f"Đạt biểu đồ ≥ {chart_min}/8", f"{res['n_pass']:,}")
    k3.metric("Phân tích sâu", len(rows))
    k4.metric("🚀 Siêu tăng trưởng", sum(1 for r in rows if r["Nhãn"].startswith("🚀")))
    st.caption(f"Quét lúc {res['at']} · giá theo phiên đóng cửa gần nhất (Naver). "
               f"RS xếp hạng so với **{res.get('rs_basis', 'danh sách')}**.")
    if res.get("rs_basis") == "chỉ trong danh sách này":
        st.info("ℹ️ RS đang chỉ so các mã trong danh sách với nhau nên dễ méo. "
                "Quét **Toàn sàn** một lần trong ngày, sau đó các danh sách ngắn sẽ tự "
                "xếp hạng RS so với cả thị trường.")

    if not rows:
        st.info("Không mã nào đạt ngưỡng biểu đồ. Hạ 'Biểu đồ tối thiểu' hoặc 'RS tối thiểu'.")
    else:
        df = pd.DataFrame(rows)

        # Toss: giá realtime + cảnh báo (tùy chọn)
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
                "Điểm tổng": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d"),
                "Tăng trưởng %": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d%%"),
                "Tin tức %": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d%%"),
                "Giá": st.column_config.NumberColumn(format="%,.0f"),
                "Vốn hóa (억)": st.column_config.NumberColumn(format="%,.0f"),
            })
        st.download_button("⬇️ Tải CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"sieu_co_phieu_{_today}.csv", mime="text/csv")

        # ------------------------------------------------ chi tiết
        st.subheader("🔎 Chi tiết 1 mã")
        pick = st.selectbox("Chọn mã", [f"{r['Mã']} · {r['Tên']} · {r['Nhãn']}" for r in rows])
        tk = pick.split(" · ")[0]
        dt = details[tk]
        m, g, gc, nm, d = dt["m"], dt["g"], dt["gc"], dt["nm"], dt["d"]

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
            st.caption(f"Tích lũy KL {m.get('ud_ratio') and round(m['ud_ratio'], 2)} · "
                       f"Nền siết {m.get('tight') and round(m['tight'], 2)} · "
                       f"Từ đỉnh 52T {m['pct_from_hi']:+.1f}%")
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

st.caption("⚠️ Bộ lọc kỹ thuật/cơ bản theo quy tắc cố định — không phải khuyến nghị đầu tư. "
           "Luôn tự kiểm tra biểu đồ, BCTC gốc và tin tức trước khi quyết định.")
