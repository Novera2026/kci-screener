"""
🧮 Quy trình định giá — đi từng bước cho 1 mã: nhận diện → dữ liệu → phân tích → dự phóng → chi phí vốn →
định giá đa mô hình → kết luận + báo cáo. Logic ở pipeline.py (thuần, có test); trang này chỉ là giao diện.
"""
from __future__ import annotations

import datetime as _dt
import io
import os
import sys

import pandas as pd
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
try:                                                   # cloud: key nằm trong Secrets
    for _k in ("DART_API_KEY", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"):
        if _k not in os.environ and _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

import pipeline as P                                   # noqa: E402
import valuation as V                                  # noqa: E402
from screener import TickerRegistry                    # noqa: E402

st.set_page_config(page_title="Quy trình định giá — KCI", page_icon="🧮", layout="wide")
st.title("🧮 Quy trình định giá")
st.caption("Đi từng bước cho **một mã**: nhận diện → kiểm dữ liệu → phân tích → **dự phóng 3–5 năm (chỉnh "
           "được)** → chi phí vốn → định giá đa mô hình → kết luận. Mọi mô hình phải qua **cổng số** "
           f"(giá trị trong {V.RELIABLE_BAND[0]}–{V.RELIABLE_BAND[1]}× giá và {V.IRRECONCILABLE[0]}–"
           f"{V.IRRECONCILABLE[1]}× phép neo peer) mới được vào kết luận.")


# ---------------------------------------------------------------------------- dữ liệu (cache)
@st.cache_resource
def _reg():
    return TickerRegistry()


@st.cache_data(ttl=86400, show_spinner=False)
def _rf_live():
    from screener import fetch_kgb10y
    return fetch_kgb10y()


@st.cache_data(ttl=3600, show_spinner=False)
def _load(tk: str, n_peers: int) -> dict:
    """Mọi dữ liệu mạng cho 1 mã — cache 1 giờ. Đổi giả định không cào lại."""
    from screener import fetch_one, fetch_many, fetch_weekly_closes, beta_vs_market
    from financials import fetch_financials, build_annual_series
    reg = _reg()
    row = fetch_one(tk, registry=reg, prefer="naver").to_dict()
    fin = fetch_financials(tk)
    series = build_annual_series(tk, fin.get("annual", []), row.get("market_cap"), row.get("price"),
                                 years=6, quarters=fin.get("quarter"))
    try:
        beta = beta_vs_market(fetch_weekly_closes(tk), fetch_weekly_closes("KOSPI"))
    except Exception:
        beta = None
    extras = {}
    try:
        import dart_api
        extras = dart_api.valuation_extras(tk) or {}
    except Exception:
        pass
    peers = []
    ik = row.get("industry_kr")
    if ik:
        ptk = P.industry_peers(ik, exclude=tk, n=n_peers)
        if ptk:
            peers, _ = fetch_many(ptk, registry=reg, prefer="naver")
    return {"row": row, "quarters": fin.get("quarter", []), "series": series, "beta": beta,
            "extras": extras, "peers": peers}


reg = _reg()

# ---------------------------------------------------------------------------- sidebar: giả định
with st.sidebar:
    st.header("⚙️ Giả định")
    _rf = _rf_live()
    rf = st.number_input("Rf — TPCP Hàn 10 năm (%)", 0.0, 10.0,
                         round((_rf or 0.032) * 100, 2), 0.05,
                         help="Mặc định lấy live từ FRED" if _rf else "FRED lỗi → 3,2%") / 100
    erp = st.number_input("ERP (%)", 2.0, 10.0, 4.87, 0.05, help="Damodaran, Hàn Quốc 01/2026") / 100
    g_term = st.number_input("g dài hạn (%)", 0.0, 2.5, 2.0, 0.1, help="Trần 2,5% và ≤ Rf") / 100
    w = st.select_slider("w — độ bền siêu lợi nhuận (RIM)", [0.8, 0.85, 0.9, 0.95, 1.0], 0.9)
    years = st.slider("Số năm dự phóng", 3, 5, 5)
    n_peers = st.slider("Số mã peer cùng ngành", 5, 20, 12)

A = V.Assumptions(rf=rf, erp=erp, g_terminal=g_term, persistence=w, years_high=years)

# ---------------------------------------------------------------------------- chọn mã
c_in, c_btn = st.columns([4, 1])
q = c_in.text_input("Mã hoặc tên cổ phiếu", value=st.session_state.get("vp_query", ""),
                    placeholder="vd 005930, Samsung, 삼성전자, KB금융")
if c_btn.button("▶️ Bắt đầu", type="primary", width="stretch") and q.strip():
    tk_new = reg.resolve(q.strip())
    st.session_state["vp_query"] = q.strip()
    st.session_state["vp_ticker"] = tk_new
    if not tk_new:
        st.error(f"Không nhận diện được “{q}”.")

tk = st.session_state.get("vp_ticker")
if not tk:
    st.info("Nhập một mã rồi bấm **Bắt đầu**. Trang sẽ đi lần lượt 6 bước và ra một báo cáo tóm tắt.")
    st.stop()

with st.spinner(f"Đang lấy dữ liệu {tk} (giá, BCTC Naver/DART, beta, peer cùng ngành)…"):
    D = _load(tk, n_peers)
row, series, quarters = dict(D["row"]), D["series"], D["quarters"]
beta, extras, peer_rows = D["beta"], D["extras"], D["peers"]
row["beta"] = beta
name = row.get("name") or tk
price, mc = row.get("price"), row.get("market_cap")
reals = [c for c in series if not c.get("is_consensus")]

# ---------------------------------------------------------------------------- tính toàn bộ pipeline
idn = P.identify(row)
dq = P.data_quality(row, series, quarters, beta)
ratios = P.ratio_table(series)

drv_key = f"drv_{tk}_{years}_{g_term}"
drv_default = P.default_drivers(series, row, A, years=years, quarters=quarters, cyclical=idn["cyclical"])
basis = drv_default[0].pop("_basis", "") if drv_default else ""
coc = P.cost_of_capital(A, beta, mc, extras)
ke = coc["ke"]

pm = P.peer_medians(peer_rows, exclude=tk)
evidence = P.reported_evidence(series, quarters)


def _run(drivers: list[dict]):
    fc = P.run_forecast(series, row, drivers)
    fg = P.forecast_gates(series, fc, financial=idn["financial"], evidence=evidence)
    r_fwd = dict(row)
    if fc:
        r_fwd["fwd_eps"] = fc[0]["EPS"]
        r_fwd["fwd_roe"] = fc[0]["EPS"] / fc[0]["BPS đầu kỳ"] if fc[0]["BPS đầu kỳ"] else None
    base = V.value_stock(r_fwd, A, pm["pe"], pm["pb"], history=reals or None)
    midc = "mid-cycle" in base.auto_method
    anchor = P.peer_anchor(base.methods, midc)
    bps0 = V._num(row.get("bps"))
    rim = P.rim_explicit(bps0, fc, ke, w)
    ddm = P.ddm_explicit(fc, ke, coc["g_term"])
    fc_block = None if fg["ok"] else f"cổng dự phóng: {fg['n_fail']} vi phạm"
    cands = [
        {"name": f"Lõi KCI — {base.auto_method}", "value": base.fair_raw, "role": "primary",
         "blocked": None if base.reliable else base.status},
        {"name": f"RIM tường minh ({len(fc)} năm dự phóng)", "value": rim["value"] if rim else None,
         "role": "primary", "blocked": fc_block},
        {"name": f"DDM tường minh ({len(fc)} năm)", "value": ddm["value"] if ddm else None,
         "role": "primary" if idn["dividend_model"] else "cross", "blocked": fc_block},
        {"name": "P/E × trung vị peer", "value": base.methods.get("P/E vs median ngành"), "role": "cross"},
        {"name": "P/B × trung vị peer", "value": base.methods.get("P/B vs median ngành"), "role": "cross"},
    ]
    if extras.get("cfo") is not None and mc and price:
        fcff = V.fcff_value(extras.get("cfo"), extras.get("capex"), extras.get("debt"), extras.get("net_debt"),
                            mc / price, ke, coc["kd"], coc["tax"], A.g_high, A.years_high, coc["g_term"],
                            mc / 1e8)
        cands.append({"name": "FCFF-DCF (DART, tăng trưởng phẳng)", "value": fcff, "role": "cross"})
    caps = [dq["cap"], None if idn["data_ok"] else "Medium"]
    rec = P.reconcile(price, cands, anchor, caps)
    return fc, fg, base, anchor, rim, ddm, rec


# ---------------------------------------------------------------------------- tiêu đề + bảng giả định
st.subheader(f"{name} · {tk}")
st.caption(f"{row.get('industry_kr') or '—'} · nguồn giá {row.get('source') or '—'} · "
           f"vốn hóa {mc / 1e12:,.1f} nghìn tỷ KRW" if mc else "")

tabs = st.tabs(["① Nhận diện", "② Dữ liệu", "③ Phân tích", "④ Dự phóng", "⑤ Chi phí vốn",
                "⑥ Định giá", "📄 Kết luận & báo cáo"])

# ④ trước để có giả định người dùng sửa — các tab khác đọc kết quả
with tabs[3]:
    st.markdown("#### ④ Dự phóng nhiều năm — sửa trực tiếp các ô giả định")
    if not drv_default:
        st.warning("Không đủ dữ liệu (cần ≥ 2 năm doanh thu thực) để dự phóng.")
        drivers = []
    else:
        st.caption(f"Mặc định: năm 1 theo **{basis or 'CAGR × biên xu hướng'}**; tăng trưởng giảm dần về "
                   f"g dài hạn {coc['g_term'] * 100:.1f}% ở năm cuối"
                   + ("; biên LN trượt về **trung vị lịch sử (mid-cycle)** vì là ngành chu kỳ / đang lỗ"
                      if idn["cyclical"] else "") + "; tỷ lệ cổ tức = trung vị lịch sử.")
        if st.button("↺ Khôi phục giả định mặc định"):
            st.session_state.pop(drv_key, None)
            st.rerun()
        ed = st.data_editor(pd.DataFrame(drv_default), key=drv_key, hide_index=True, width="stretch",
                            disabled=["Năm"],
                            column_config={c: st.column_config.NumberColumn(c, format="%.2f")
                                           for c in ("Tăng trưởng DT %", "Biên LN ròng %", "Tỷ lệ cổ tức %")})
        drivers = ed.to_dict("records")

fc, fg, base, anchor, rim, ddm, rec = _run(drivers)

with tabs[3]:
    if fc:
        fdf = pd.DataFrame(fc)
        st.dataframe(fdf.style.format({c: "{:,.0f}" for c in ("Doanh thu (억)", "LN ròng (억)", "EPS", "DPS",
                                                              "BPS đầu kỳ", "BPS cuối kỳ")}
                                      | {c: "{:+.1f}" for c in ("Tăng trưởng DT %",)}
                                      | {c: "{:.1f}" for c in ("Biên LN ròng %", "ROE %")}, na_rep="—"),
                     hide_index=True, width="stretch")
        st.markdown("**Cổng dự phóng (bằng số):** " + ("✅ mọi giả định trong dải" if fg["ok"] else
                    f"❌ {fg['n_fail']} vi phạm → RIM/DDM tường minh **bị loại** khỏi kết luận cho tới khi sửa"))
        if not fg["ok"]:
            st.caption("Sửa các ô giả định ở bảng trên cho tới khi hết ❌. Nếu năm 1 lấy từ quý thực mà vẫn ❌ "
                       "(vd LN > doanh thu do lãi bất thường một lần), đó là số không lặp lại — hãy hạ biên LN.")
            st.dataframe(pd.DataFrame(fg["checks"]).astype({"Giá trị": str}), hide_index=True, width="stretch")
        st.caption(f"Dải cho phép: tăng trưởng DT {P.FC_GROWTH_BAND[0] * 100:.0f}% … +{P.FC_GROWTH_BAND[1] * 100:.0f}%"
                   f"/năm · biên LN ròng ≤ max lịch sử + {P.FC_MARGIN_HEADROOM * 100:.0f} điểm và ≤ "
                   f"{P.FC_MARGIN_MAX * 100:.0f}% · ROE ≤ {P.FC_ROE_MAX * 100:.0f}% · BPS > 0."
                   + (f" Năm 1 đã có **{evidence['k']} quý thực** (DT {evidence['growth'] * 100:+.0f}%, biên "
                      f"{evidence['margin'] * 100:.1f}%) → dải năm 1 nới theo số đã công bố." if evidence else ""))

with tabs[0]:
    st.markdown("#### ① Nhận diện doanh nghiệp")
    c1, c2, c3 = st.columns(3)
    c1.metric("Nhóm định giá", idn["bucket"])
    c2.metric("Ngành chu kỳ?", "Có" if idn["cyclical"] else "Không")
    c3.metric("Dữ liệu đủ cho mô hình chuẩn?", "Đủ" if idn["data_ok"] else "Chỉ proxy")
    st.markdown(f"**Mô hình chính theo ngành:** {idn['primary']}  \n**Đối chiếu:** {', '.join(idn['cross'])}")
    st.info(idn["note"])
    if not idn["data_ok"]:
        st.caption("Ngành cần dữ liệu sâu hơn (backlog/capex/SOTP/pipeline) mà App chưa có → độ tin cậy kết luận "
                   "bị chặn trần **Medium**.")

with tabs[1]:
    st.markdown(f"#### ② Dữ liệu & chất lượng — {dq['n_fail']} ❌ · {dq['n_warn']} ⚠️")
    if dq["cap"]:
        st.warning(f"Trần độ tin cậy do dữ liệu: **{dq['cap']}**")
    st.dataframe(pd.DataFrame(dq["checks"]).astype({"Giá trị": str}), hide_index=True, width="stretch")
    if reals:
        keys = ["revenue", "op_profit", "net_profit", "equity", "assets", "liabilities", "eps", "bps", "dps"]
        lbl = {"revenue": "Doanh thu (억)", "op_profit": "LN HĐ (억)", "net_profit": "LN ròng (억)",
               "equity": "VCSH (억)", "assets": "Tổng TS (억)", "liabilities": "Nợ (억)", "eps": "EPS",
               "bps": "BPS", "dps": "DPS"}
        tbl = pd.DataFrame({str(c.get("period", ""))[:4] + (" ·DART" if c.get("_dart") else ""):
                            [c.get(k) for k in keys] for c in reals}, index=[lbl[k] for k in keys])
        st.dataframe(tbl.style.format("{:,.0f}", na_rep="—"), width="stretch")

with tabs[2]:
    st.markdown("#### ③ Phân tích & chuẩn hóa")
    if ratios:
        rdf = pd.DataFrame(ratios)
        st.dataframe(rdf.style.format({c: "{:,.1f}" for c in rdf.columns if c not in ("Năm", "Năm bất thường")}
                                      | {"Doanh thu (억)": "{:,.0f}"}, na_rep="—"),
                     hide_index=True, width="stretch")
        nm = V.normalized_metrics(reals)
        c1, c2, c3 = st.columns(3)
        c1.metric("ROE trung vị (mid-cycle)", f"{nm['median_roe'] * 100:.1f}%" if nm["median_roe"] is not None else "—")
        c2.metric("Biên LN ròng trung vị", f"{nm['median_net_margin'] * 100:.1f}%"
                  if nm["median_net_margin"] is not None else "—")
        c3.metric("Số năm", nm["n_years"])
        if any(r["Năm bất thường"] for r in ratios):
            st.caption("Năm bất thường được đánh dấu: lõi KCI ngành chu kỳ dùng **trung vị** nhiều năm nên tự khử "
                       "các năm này; với ngành khác hãy cân nhắc khi sửa giả định ở bước ④.")
    else:
        st.warning("Không có BCTC năm thực.")

with tabs[4]:
    st.markdown("#### ⑤ Chi phí vốn")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rf", f"{coc['rf'] * 100:.2f}%")
    c2.metric("β dùng", f"{coc['beta_used']:.2f}",
              f"hồi quy {coc['beta_raw']:.2f} → Blume" if coc["beta_raw"] else "không có β riêng", delta_color="off")
    c3.metric("Ke = Rf + β·ERP", f"{ke * 100:.2f}%")
    c4.metric("WACC", f"{coc['wacc'] * 100:.2f}%" if coc["wacc"] else "—",
              f"nợ vay {coc['debt_eok']:,.0f}억" if coc["debt_eok"] is not None else "cần DART", delta_color="off")
    st.caption(f"ERP {coc['erp'] * 100:.2f}% · Kd ≈ Rf + 1,5% = {coc['kd'] * 100:.2f}% · thuế {coc['tax'] * 100:.0f}% · "
               f"g dài hạn {coc['g_term'] * 100:.1f}%. β hồi quy 52 tuần vs KOSPI, kẹp 0,5–1,8 rồi Blume 0,67β+0,33.")

with tabs[5]:
    st.markdown("#### ⑥ Định giá đa mô hình")
    st.caption(f"Peer cùng ngành KRX: {pm['n']} mã · P/E trung vị **{pm['pe']:.1f}** ({pm['n_pe']} mã hợp lệ)"
               if pm["pe"] else f"Peer cùng ngành KRX: {pm['n']} mã · P/E trung vị: — (cần ≥ {V.MIN_PEERS} mã hợp lệ)")
    st.caption((f"P/B trung vị **{pm['pb']:.2f}** ({pm['n_pb']} mã)" if pm["pb"] else "P/B trung vị: —")
               + (f" · **phép neo peer {anchor:,.0f} KRW**" if anchor else " · chưa có phép neo peer (cần ≥ 2 phương pháp tương đối)"))
    mt = pd.DataFrame([{"Mô hình": r["name"], "Vai trò": "chính" if r["role"] == "primary" else "đối chiếu",
                        "Giá trị/cp": r.get("value"),
                        "× giá": (r["value"] / price) if (r.get("value") and price) else None,
                        "Qua cổng": "✅" if r["pass"] else "❌", "Lý do": r["why"]} for r in rec["rows"]])
    st.dataframe(mt.style.format({"Giá trị/cp": "{:,.0f}", "× giá": "{:.2f}"}, na_rep="—"),
                 hide_index=True, width="stretch")
    if rim:
        st.caption(f"RIM tường minh: giá trị cuối kỳ chiếm **{rim['tv_share'] * 100:.0f}%**"
                   + (" ⚠️ > 75% — kết quả phụ thuộc mạnh vào giả định dài hạn" if rim["tv_share"] > P.TV_SHARE_WARN
                      else "") + f" · w = {w}.")
    with st.expander("Tất cả phương pháp của lõi KCI (số thô)"):
        st.dataframe(pd.DataFrame([{"Phương pháp": k, "Giá trị/cp": v,
                                    "× giá": (v / price) if (v and price) else None}
                                   for k, v in base.methods.items()])
                     .style.format({"Giá trị/cp": "{:,.0f}", "× giá": "{:.2f}"}, na_rep="—"),
                     hide_index=True, width="stretch")
        if base.flags:
            st.caption("Cờ lõi KCI: " + " · ".join(base.flags))
    if peer_rows:
        with st.expander(f"Bảng peer ({len(peer_rows)} mã)"):
            st.dataframe(pd.DataFrame(peer_rows)[["ticker", "name", "price", "market_cap", "per", "pbr"]]
                         .rename(columns={"ticker": "Mã", "name": "Tên", "price": "Giá", "market_cap": "Vốn hóa",
                                          "per": "P/E", "pbr": "P/B"}), hide_index=True, width="stretch")

# ---------------------------------------------------------------------------- kết luận
ctx = {"ticker": tk, "name": name, "date": _dt.date.today().isoformat(), "price": price, "identify": idn,
       "dq": dq, "forecast": fc, "fgates": fg, "coc": coc, "conclusion": rec, "peers": pm, "anchor": anchor}
md = P.report_markdown(ctx)

with tabs[6]:
    st.markdown("#### Kết luận")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Giá hiện tại", f"{price:,.0f}" if price else "—")
    c2.metric("Giá trị hợp lý", f"{rec['fair']:,.0f}" if rec["fair"] else "—",
              f"{rec['upside']:+.1f}%" if rec["upside"] is not None else None)
    c3.metric("Khoảng", f"{rec['low']:,.0f} – {rec['high']:,.0f}" if rec["fair"] else "—")
    c4.metric("Độ tin cậy", rec["confidence"])
    (st.success if rec["fair"] else st.warning)(f"{rec['status']}" + (f" · {rec['verdict']}" if rec.get("verdict") else ""))
    st.markdown("---")
    st.markdown(md)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([{"Mã": tk, "Tên": name, "Giá": price, "Giá trị hợp lý": rec["fair"], "Thấp": rec["low"],
                       "Cao": rec["high"], "Chênh %": rec["upside"], "Độ tin cậy": rec["confidence"],
                       "Trạng thái": rec["status"], "Ke": ke}]).to_excel(xw, sheet_name="Tóm tắt", index=False)
        pd.DataFrame(dq["checks"]).astype({"Giá trị": str}).to_excel(xw, sheet_name="Kiểm dữ liệu", index=False)
        pd.DataFrame(ratios).to_excel(xw, sheet_name="Phân tích", index=False)
        pd.DataFrame(fc).to_excel(xw, sheet_name="Dự phóng", index=False)
        pd.DataFrame(rec["rows"]).to_excel(xw, sheet_name="Mô hình", index=False)
        if peer_rows:
            pd.DataFrame(peer_rows).to_excel(xw, sheet_name="Peer", index=False)
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Báo cáo (.md)", md.encode("utf-8"), f"dinh_gia_{tk}_{ctx['date']}.md",
                       "text/markdown", width="stretch")
    d2.download_button("⬇️ Bảng tính (.xlsx)", buf.getvalue(), f"dinh_gia_{tk}_{ctx['date']}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")

# tóm tắt luôn hiện ở đầu trang (dưới tiêu đề) — kết quả thay đổi ngay khi sửa giả định
st.sidebar.markdown("---")
st.sidebar.metric("Giá trị hợp lý", f"{rec['fair']:,.0f}" if rec["fair"] else "—",
                  f"{rec['upside']:+.1f}%" if rec["upside"] is not None else None)
st.sidebar.caption(f"Độ tin cậy {rec['confidence']} · {rec['status']}")
