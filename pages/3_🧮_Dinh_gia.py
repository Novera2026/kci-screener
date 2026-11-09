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
import sector_models as P_S                            # noqa: E402
import valuation as V                                  # noqa: E402
from screener import TickerRegistry                    # noqa: E402

st.set_page_config(page_title="Quy trình định giá — KCI", page_icon="🧮", layout="wide")
st.title("🧮 Quy trình định giá")
st.caption("Đi từng bước cho **một mã**: nhận diện → kiểm dữ liệu → phân tích → **dự phóng 3–5 năm (chỉnh "
           "được)** → chi phí vốn → định giá đa mô hình → kịch bản & độ nhạy → football field & nhật ký → kết luận. Mọi mô hình phải qua **cổng số** "
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
    st.info("Nhập một mã rồi bấm **Bắt đầu**. Trang sẽ đi lần lượt 8 bước và ra một báo cáo tóm tắt.")
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
drv_default = P.default_drivers(series, row, A, years=years, quarters=quarters, cyclical=idn["cyclical"],
                                 financial=idn["financial"])
basis = drv_default[0].pop("_basis", "") if drv_default else ""
coc = P.cost_of_capital(A, beta, mc, extras)
ke = coc["ke"]

pm = P.peer_medians(peer_rows, exclude=tk)
evidence = P.reported_evidence(series, quarters)


D_extra: list = []       # mô hình chuyên ngành thêm vào ⑥ (tab 🏭 điền)
D_eval = {"row": row, "series": series, "quarters": quarters, "extras": extras, "peers": peer_rows, "ticker": tk,
          "extra": D_extra}
LOG_PATH = os.path.join(_ROOT, "valuation_log.csv")


# ---------------------------------------------------------------------------- tiêu đề + bảng giả định
st.subheader(f"{name} · {tk}")
st.caption(f"{row.get('industry_kr') or '—'} · nguồn giá {row.get('source') or '—'} · "
           f"vốn hóa {mc / 1e12:,.1f} nghìn tỷ KRW" if mc else "")

_TAB_NAMES = ["① Nhận diện", "② Dữ liệu", "③ Phân tích", "🏭 Chuyên ngành", "④ Dự phóng", "⑤ Chi phí vốn",
              "⑥ Định giá", "⑦ Kịch bản & độ nhạy", "⑧ Football field & nhật ký", "📄 Kết luận & báo cáo"]
T = dict(zip(["1", "2", "3", "sec", "4", "5", "6", "7", "8", "end"], st.tabs(_TAB_NAMES)))

# 🏭 chuyên ngành trước ④: backlog / công suất sinh giả định cho ④; rNPV / EV-GWh thành mô hình ở ⑥
SPEC_MODES = {"none": "Không dùng", "backlog": "Backlog — đóng tàu, quốc phòng",
              "capacity": "Công suất GWh — pin", "rnpv": "rNPV danh mục thuốc — dược, biotech"}
special = None            # {"name", "checks", "summary"} cho báo cáo
spec_drivers, spec_basis = None, ""
shares = (mc / price) if (mc and price) else None
net_debt = extras.get("net_debt")
_hm = [c["net_profit"] / c["revenue"] * 100 for c in reals if c.get("net_profit") is not None and c.get("revenue")]
_m_med = max(V._median(_hm) or 0.0, 0.0)
# biên mặc định cho backlog / công suất: năm gần nhất (đơn hàng mới ký theo giá hiện tại — trung vị nhiều năm
# kéo cả các năm lỗ 2021–23 của đóng tàu về ~0%); năm gần nhất âm thì dùng trung vị
_m_last = _hm[-1] if _hm else 0.0
_m_def = round(_m_last if _m_last > 0 else _m_med, 1)
with T["sec"]:
    st.markdown("#### 🏭 Mô hình chuyên ngành")
    st.caption("Ba nhóm ngành không định giá đúng được bằng ROE/EPS quá khứ. App **không tự lấy** được đầu vào chính "
               "(수주잔고, công suất GWh, danh mục thuốc) — anh nhập từ **사업보고서 DART / tài liệu IR**. Mọi đầu "
               "vào phải qua **cổng số**; ❌ là mô hình bị loại, không có ô giải trình nào mở được cổng.")
    _def = P_S.SPECIAL_BUCKETS.get(idn["bucket"], "none")
    mode = st.selectbox("Mô hình", list(SPEC_MODES), index=list(SPEC_MODES).index(_def),
                        format_func=SPEC_MODES.get, key=f"spec_mode_{tk}")
    rev0 = reals[-1].get("revenue") if reals else None

    if mode == "backlog":
        c1, c2, c3, c4 = st.columns(4)
        bl = c1.number_input("수주잔고 (억원)", 0.0, value=0.0, step=1000.0, key=f"bl_{tk}",
                             help="사업보고서 → II. 사업의 내용 → 수주상황 (đơn vị 억원)")
        by = c2.number_input("Số năm đốt backlog", 1.0, 6.0, 3.0, 0.5, key=f"by_{tk}")
        bm = c3.number_input("Biên LN ròng trên backlog (%)", -20.0, 60.0, _m_def, 0.5, key=f"bm_{tk}",
                             help="Mặc định = biên LN ròng năm gần nhất")
        bb = c4.number_input("Book-to-bill đơn mới", 0.0, 3.0, 1.0, 0.1, key=f"bb_{tk}",
                             help="Đơn mới mỗi năm ÷ doanh thu năm đó. 1,0 = giữ quy mô")
        if rev0:
            st.caption(f"Doanh thu năm gần nhất {rev0:,.0f}억 · biên LN ròng năm gần nhất {_m_last:.1f}%, "
                       f"trung vị lịch sử {_m_med:.1f}%.")
        if bl > 0:
            res = P_S.backlog_drivers(series, bl, by, bm / 100, bb, years=years,
                                      payout=(P._payout_hist(reals) or 0.0))
            st.dataframe(pd.DataFrame(res["checks"]).astype({"Giá trị": str}), hide_index=True, width="stretch")
            if res["ok"]:
                st.dataframe(pd.DataFrame({"Năm": [d["Năm"] for d in res["drivers"]],
                                           "Doanh thu (억)": res["revenue"]}).style.format({"Doanh thu (억)": "{:,.0f}"}),
                             hide_index=True)
                if st.checkbox("Dùng làm giả định bước ④", True, key=f"bl_use_{tk}"):
                    spec_drivers = res["drivers"]
                    spec_basis = f"backlog {bl:,.0f}억 đốt trong {by:g} năm, biên {bm:.1f}%, book-to-bill {bb:.2f}"
            special = {"name": "Backlog", "checks": res["checks"],
                       "summary": f"수주잔고 {bl:,.0f}억 ({bl / rev0:.1f} năm doanh thu) · đốt {by:g} năm · biên "
                                  f"{bm:.1f}% · book-to-bill {bb:.2f}" if rev0 else ""}
        else:
            st.info("Nhập 수주잔고 để dựng dự phóng từ backlog.")

    elif mode == "capacity":
        c1, c2, c3 = st.columns(3)
        g0 = c1.number_input("Công suất hiện tại (GWh)", 0.0, value=0.0, step=10.0, key=f"g0_{tk}")
        gN = c2.number_input(f"Công suất năm thứ {years} (GWh)", 0.0, value=0.0, step=10.0, key=f"gN_{tk}")
        ut = c3.number_input("Hiệu suất sử dụng (%)", 1.0, 100.0, 70.0, 5.0, key=f"ut_{tk}") / 100
        imp = (rev0 / (g0 * ut)) if (rev0 and g0 > 0) else None
        c4, c5, c6 = st.columns(3)
        asp = c4.number_input("Giá bán (억원/GWh)", 0.0, value=float(round(imp or 0.0)), step=10.0, key=f"asp_{tk}_{g0}_{ut}",
                              help="Mặc định = doanh thu năm gần nhất ÷ (GWh hiện tại × hiệu suất)")
        cm = c5.number_input("Biên LN ròng (%)", -30.0, 60.0, _m_def, 0.5, key=f"cm_{tk}",
                             help="Mặc định = biên LN ròng năm gần nhất")
        evg = c6.number_input("EV/GWh đối chiếu (억원/GWh, 0 = bỏ)", 0.0, value=0.0, step=10.0, key=f"evg_{tk}")
        if g0 > 0 and gN > 0:
            path = [g0 + (gN - g0) * t / years for t in range(1, years + 1)]
            res = P_S.capacity_drivers(series, g0, path, ut, asp, cm / 100, payout=(P._payout_hist(reals) or 0.0))
            st.dataframe(pd.DataFrame(res["checks"]).astype({"Giá trị": str}), hide_index=True, width="stretch")
            ie = P_S.implied_ev_per_gwh(mc, net_debt, g0)
            if ie:
                st.caption(f"Thị trường đang trả **EV/GWh ≈ {ie:,.0f}억** cho công suất hiện tại"
                           + ("" if net_debt is not None else " (chưa trừ nợ ròng — thiếu DART)") + ".")
            if res["ok"] and st.checkbox("Dùng làm giả định bước ④", True, key=f"cap_use_{tk}"):
                spec_drivers = res["drivers"]
                spec_basis = f"công suất {g0:g}→{gN:g} GWh × hiệu suất {ut:.0%} × {asp:,.0f}억/GWh, biên {cm:.1f}%"
            if evg > 0 and res["ok"]:
                v = P_S.ev_per_gwh_value(gN, evg, net_debt or 0.0, shares)
                D_extra.append(lambda coc, v=v: {"name": f"EV/GWh × {gN:g} GWh (nhập tay)", "value": v,
                                                 "role": "cross"})
            special = {"name": "Công suất GWh", "checks": res["checks"],
                       "summary": f"{g0:g} → {gN:g} GWh · hiệu suất {ut:.0%} · {asp:,.0f}억/GWh · biên {cm:.1f}%"
                                  + (f" · EV/GWh {evg:,.0f}억" if evg else "")}
        else:
            st.info("Nhập công suất hiện tại và năm cuối để dựng dự phóng từ công suất.")

    elif mode == "rnpv":
        st.caption("Mỗi dòng một thuốc/ứng viên. **PoS** để trống = chuẩn theo pha (BIO/Informa 2011–2020: pha 1 "
                   "7,9% · pha 2 15,3% · pha 3 57,8% · nộp hồ sơ 90,6%); nhập tay không được vượt 1,5× chuẩn. "
                   "**Biên LN %** = biên sau thuế nếu tự bán, hoặc tỷ lệ royalty nếu chỉ thu phí bản quyền.")
        _cols = ["Tên", "Pha", "Doanh số đỉnh (억)", "Năm ra mắt", "PoS %", "Biên LN %", "Chi phí PT còn lại (억)"]
        ed_a = st.data_editor(pd.DataFrame(columns=_cols).astype({"Tên": str, "Pha": str}), num_rows="dynamic",
                              key=f"rn_{tk}", hide_index=True, width="stretch",
                              column_config={"Pha": st.column_config.SelectboxColumn(
                                  "Pha", options=list(P_S.POS_BY_PHASE), required=True)})
        c1, c2 = st.columns(2)
        _nc_def = float(-net_debt) if net_debt is not None else 0.0
        nc = c1.number_input("Tiền ròng (억, + = tiền > nợ vay)", value=_nc_def, step=100.0, key=f"nc_{tk}",
                             help="Mặc định từ DART (tiền − nợ vay)" if net_debt is not None else "Thiếu DART — nhập tay")
        oh = c2.number_input("Chi phí chung hằng năm (억)", 0.0, value=0.0, step=10.0, key=f"oh_{tk}",
                             help="Trừ như niên kim vĩnh viễn ÷ Ke")
        assets = [r for r in ed_a.to_dict("records") if str(r.get("Tên") or "").strip() not in ("", "nan", "None")]
        if assets:
            _role = "primary" if idn["bucket"] == "Pharma/Biotech" else "cross"
            this_year = _dt.date.today().year

            def _rn(coc, assets=assets, nc=nc, oh=oh):
                out = P_S.rnpv(assets, coc["ke"], shares, nc, this_year, overhead_eok=oh)
                return {"name": f"rNPV danh mục ({len(out['rows'])} tài sản)", "value": out["value"], "role": _role,
                        "blocked": None if out["ok"] else f"cổng rNPV: {out['n_fail']} vi phạm"}
            D_extra.append(_rn)
            out = P_S.rnpv(assets, ke, shares, nc, this_year, overhead_eok=oh)
            st.dataframe(pd.DataFrame(out["checks"]).astype({"Giá trị": str}), hide_index=True, width="stretch")
            if out["rows"]:
                st.dataframe(pd.DataFrame(out["rows"]).style.format(
                    {c: "{:,.0f}" for c in ("PV dòng tiền (억)", "PV chi phí PT (억)", "rNPV (억)")}),
                    hide_index=True, width="stretch")
                st.caption(f"Σ rNPV {out['sum_assets']:,.0f}억 + tiền ròng {nc:,.0f}억 − chi phí chung "
                           f"{out['overhead_pv']:,.0f}억 = **{out['equity']:,.0f}억**"
                           + (f" → **{out['value']:,.0f} KRW/cp**" if out["value"] else " → không công bố")
                           + f" · chiết khấu Ke {ke * 100:.2f}%.")
            special = {"name": "rNPV", "checks": out["checks"],
                       "summary": f"{len(assets)} tài sản · Σ rNPV {out['sum_assets']:,.0f}억 · tiền ròng {nc:,.0f}억"}
        else:
            st.info("Thêm dòng vào bảng để định giá danh mục thuốc.")

# ④ trước để có giả định người dùng sửa — các tab khác đọc kết quả
if spec_drivers:
    drv_default, basis = [dict(d) for d in spec_drivers], spec_basis
    import zlib
    drv_key = f"drv_{tk}_{years}_{g_term}_{zlib.crc32(spec_basis.encode())}"   # đổi đầu vào 🏭 → bảng ④ làm mới
with T["4"]:
    st.markdown("#### ④ Dự phóng nhiều năm — sửa trực tiếp các ô giả định")
    if spec_drivers:
        st.info(f"🏭 Giả định đang lấy từ **mô hình chuyên ngành**: {spec_basis}. Bỏ chọn ở tab 🏭 để quay về "
                "giả định từ lịch sử.")
    if not drv_default:
        st.warning("Không đủ dữ liệu (cần ≥ 2 năm doanh thu thực) để dự phóng.")
        drivers = []
    else:
        if not spec_drivers:
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

EV = P.evaluate(D_eval, A, drivers, idn, dq, evidence)
fc, fg, base, anchor, rim, ddm, rec = (EV[k] for k in ("fc", "fg", "base", "anchor", "rim", "ddm", "rec"))

with T["4"]:
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

with T["1"]:
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

with T["2"]:
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

with T["3"]:
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

with T["5"]:
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

with T["6"]:
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

# ---------------------------------------------------------------------------- ⑦ kịch bản & độ nhạy
with T["7"]:
    st.markdown("#### ⑦ Kịch bản Bear / Base / Bull")
    scen, sens = None, None
    if not drivers:
        st.warning("Cần dự phóng ở bước ④ để chạy kịch bản.")
    else:
        st.caption("Mỗi kịch bản dịch toàn bộ giả định ở bước ④ (tăng trưởng ± điểm %, biên LN × hệ số) rồi chạy "
                   "**lại đúng quy trình và cổng số** như kết luận chính. "
                   + ("Năm 1 đã có quý thực công bố nên **giữ nguyên** trong mọi kịch bản. " if evidence else "")
                   + "Sửa bảng dưới để đổi kịch bản.")
        sc_df = st.data_editor(
            pd.DataFrame([{"Kịch bản": k, "Δ tăng trưởng (điểm %)": v["dg"], "× biên LN": v["mm"],
                           "Xác suất": v["p"]} for k, v in P.SCENARIOS.items()]),
            key=f"sc_{tk}", hide_index=True, width="stretch", disabled=["Kịch bản"])
        spec = {r["Kịch bản"]: {"dg": float(r["Δ tăng trưởng (điểm %)"] or 0), "mm": float(r["× biên LN"] or 1),
                                "p": max(float(r["Xác suất"] or 0), 0.0)} for r in sc_df.to_dict("records")}
        scen = P.scenarios(D_eval, A, drivers, idn, dq, evidence, spec)
        sdf = pd.DataFrame(scen["rows"])
        num_cols = [c for c in sdf.columns if c in (EV["fc_model_name"], "Giá trị hợp lý")]
        st.dataframe(sdf.style.format({c: "{:,.0f}" for c in num_cols} | {"Xác suất": "{:.0%}"}, na_rep="⚪ không công bố"),
                     hide_index=True, width="stretch")
        c1, c2 = st.columns(2)
        c1.metric("Giá trị kỳ vọng (Σ xác suất × giá trị)", f"{scen['expected']:,.0f}" if scen["expected"] else "—",
                  f"{(scen['expected'] / price - 1) * 100:+.1f}%" if (scen["expected"] and price) else None)
        c2.metric("Dải Bear – Bull", f"{scen['low']:,.0f} – {scen['high']:,.0f}" if scen["low"] else "—")
        if not scen["informative"]:
            st.warning("Dự phóng ở bước ④ đang bị cổng số chặn nên **không mô hình chính nào dùng dự phóng** — các "
                       "kịch bản không làm đổi kết luận. Sửa giả định ở bước ④ cho hết ❌ để kịch bản có ý nghĩa.")
        elif scen["expected"] is None:
            st.caption("⚪ Giá trị kỳ vọng chỉ công bố khi **cả ba** kịch bản đều có giá trị qua cổng số — một kịch "
                       "bản bị chặn nghĩa là phân phối không đầy đủ, App không tự lấp số.")

        st.markdown("#### Độ nhạy: Ke × g dài hạn")
        sens = P.sensitivity(D_eval, A, drivers, idn, dq, evidence)
        grid = pd.DataFrame({f"g {g * 100:.1f}%": [r["values"][g] for r in sens["grid"]] for g in sens["g_grid"]},
                            index=[f"Ke {(EV['coc']['ke'] + r['ke_shift']) * 100:.2f}% ({r['ke_shift'] * 100:+.1f})"
                                   for r in sens["grid"]])

        def _c_up(v):
            if v is None or pd.isna(v) or not price:
                return "color:#999"
            u = v / price - 1
            return ("background-color:#d9f2e3" if u >= 0.2 else "background-color:#f9dcdc" if u <= -0.15 else "")
        st.dataframe(grid.style.format("{:,.0f}", na_rep="⚪").map(_c_up), width="stretch")
        st.caption(f"Giá trị hợp lý (đã qua cổng) khi Ke lệch ±1 điểm % và g dài hạn 1–2,5% · "
                   f"{sens['n_ok']}/{sens['n_all']} ô công bố được. ⚪ = ô đó không qua cổng số. "
                   "🟢 ≥ +20% so với giá · 🔴 ≤ −15%. g luôn bị chặn ≤ Rf.")

# ---------------------------------------------------------------------------- ⑧ football field & nhật ký
with T["8"]:
    st.markdown("#### ⑧ Football field")
    ff = P.football_field(row, rec, scen, sens)
    if ff and price:
        import altair as alt
        fdf = pd.DataFrame(ff)
        fdf["label"] = [d if len(d) <= 34 else d[:33] + "…" for d in fdf["Dải"]]   # màu đã cho biết nhóm
        order = list(fdf["label"])
        y = alt.Y("label:N", sort=order, title=None, axis=alt.Axis(labelLimit=150))
        bars = alt.Chart(fdf).mark_bar(height=14, cornerRadius=3).encode(
            x=alt.X("low:Q", title="KRW / cổ phiếu", scale=alt.Scale(zero=False, nice=True)), x2="high:Q", y=y,
            color=alt.Color("Nhóm:N", legend=alt.Legend(orient="bottom", title=None, columns=3, labelLimit=160)),
            tooltip=["Nhóm", "Dải", alt.Tooltip("low:Q", format=",.0f"), alt.Tooltip("high:Q", format=",.0f")])
        pts = alt.Chart(fdf[fdf["low"] == fdf["high"]]).mark_point(filled=True, size=90, shape="diamond").encode(
            x="low:Q", y=y, color="Nhóm:N")
        rule = alt.Chart(pd.DataFrame({"x": [price]})).mark_rule(strokeDash=[5, 3], color="#d62728").encode(x="x:Q")
        txt = alt.Chart(pd.DataFrame({"x": [price], "t": [f"Giá {price:,.0f}"]})).mark_text(
            align="left", dx=4, dy=-6, color="#d62728").encode(x="x:Q", text="t:N", y=alt.value(0))
        st.altair_chart((bars + pts + rule + txt).properties(height=34 * len(fdf) + 40), width="stretch")
        st.caption("Chỉ vẽ mô hình **đã qua cổng số**. Vạch đỏ = giá hiện tại. Kim cương = mô hình cho 1 con số.")
    else:
        st.info("Chưa đủ dữ liệu để vẽ.")

    st.markdown("#### 📓 Nhật ký định giá")
    st.caption("Lưu mỗi lần định giá (giá, giá trị hợp lý, giả định) để sau này so với giá thật. File "
               "`valuation_log.csv` trong thư mục App — trên Streamlit Cloud file sẽ mất khi App khởi động lại.")
    hist = P.log_read(LOG_PATH, tk)
    if hist:
        tdf = pd.DataFrame(P.log_track(hist, price))
        st.dataframe(tdf.style.format({"Giá lúc định giá": "{:,.0f}", "Giá trị hợp lý": "{:,.0f}",
                                       "Giá hôm nay": "{:,.0f}", "Giá đã đi %": "{:+.1f}",
                                       "Đi được về giá trị %": "{:+.0f}"}, na_rep="—"),
                     hide_index=True, width="stretch")
        st.caption("**Đi được về giá trị %**: 100% = giá đã chạm giá trị hợp lý ước tính lúc đó; âm = giá đi ngược.")
    else:
        st.caption("Chưa có lần định giá nào được lưu cho mã này.")

# ---------------------------------------------------------------------------- kết luận
ctx = {"ticker": tk, "name": name, "date": _dt.date.today().isoformat(), "price": price, "identify": idn,
       "dq": dq, "forecast": fc, "fgates": fg, "coc": EV["coc"], "conclusion": rec, "peers": EV["pm"],
       "anchor": anchor, "scen": scen, "sens": sens, "special": special}
md = P.report_markdown(ctx)

with T["end"]:
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
        if scen:
            pd.DataFrame(scen["rows"]).to_excel(xw, sheet_name="Kịch bản", index=False)
        if sens:
            grid.to_excel(xw, sheet_name="Độ nhạy")
        if peer_rows:
            pd.DataFrame(peer_rows).to_excel(xw, sheet_name="Peer", index=False)
    d1, d2, d3, d4 = st.columns(4)
    d1.download_button("⬇️ Báo cáo (.md)", md.encode("utf-8"), f"dinh_gia_{tk}_{ctx['date']}.md",
                       "text/markdown", width="stretch")
    d2.download_button("⬇️ Bảng tính (.xlsx)", buf.getvalue(), f"dinh_gia_{tk}_{ctx['date']}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    d3.download_button("⬇️ Báo cáo in PDF (.html)", P.report_html(md, f"Định giá {name} {tk}").encode("utf-8"),
                       f"dinh_gia_{tk}_{ctx['date']}.html", "text/html", width="stretch",
                       help="Mở file trong trình duyệt → In (⌘P) → Lưu thành PDF")
    if d4.button("📓 Lưu vào nhật ký", width="stretch", disabled=not rec["fair"],
                 help="Chỉ lưu được khi có giá trị hợp lý công bố"):
        P.log_append(LOG_PATH, P.log_entry(ctx, drivers, A, scen))
        st.toast("Đã lưu vào nhật ký định giá")
        st.rerun()

# tóm tắt luôn hiện ở đầu trang (dưới tiêu đề) — kết quả thay đổi ngay khi sửa giả định
st.sidebar.markdown("---")
st.sidebar.metric("Giá trị hợp lý", f"{rec['fair']:,.0f}" if rec["fair"] else "—",
                  f"{rec['upside']:+.1f}%" if rec["upside"] is not None else None)
st.sidebar.caption(f"Độ tin cậy {rec['confidence']} · {rec['status']}")
