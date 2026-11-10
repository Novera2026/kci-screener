"""
🧮 Quy trình định giá 5 bước cho 1 mã: ① so sánh đối thủ trong & ngoài nước → ② phân tích & bóc tách tài chính →
③ chất lượng tài sản → ④ định giá theo mô hình hợp ngành → ⑤ kết luận & đánh giá tổng quan. Mỗi bước ghi vào hồ sơ
(dossier.py) cho bước sau và trang sàng lọc. Logic ở peers / analysis / pipeline / sector_models; trang này là giao diện.
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

import analysis as AN                                 # noqa: E402
import dcf as DCF                                      # noqa: E402
import dossier as DS                                   # noqa: E402
import peers as PR                                     # noqa: E402
import pipeline as P                                   # noqa: E402
import sector_models as P_S                            # noqa: E402
import valuation as V                                  # noqa: E402
from screener import TickerRegistry                    # noqa: E402

st.set_page_config(page_title="Quy trình định giá — KCI", page_icon="🧮", layout="wide")
st.title("🧮 Quy trình định giá")
st.caption("Năm bước cho **một mã**: ① so sánh đối thủ **trong & ngoài nước** → ② phân tích & bóc tách tài chính → "
           "③ chất lượng tài sản → ④ định giá theo mô hình hợp ngành → ⑤ kết luận & đánh giá tổng quan. Kết quả mỗi "
           "bước được **lưu vào hồ sơ** cho bước sau. Mọi mô hình định giá phải qua **cổng số** "
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
    # ① đối thủ trong nước: gợi ý Naver ∪ top vốn hóa cùng 업종 (chỉ cổ phiếu thường) — DÙNG LẠI cho phép neo ở ④
    import concurrent.futures as _cf
    peers, peer_fins = [], {}
    ptk = PR.domestic_candidates(tk, row.get("industry_kr"), reg, n=n_peers)
    if ptk:
        peers, _ = fetch_many(ptk, registry=reg, prefer="naver")
        with _cf.ThreadPoolExecutor(max_workers=5) as ex:
            peer_fins = dict(zip(ptk, ex.map(fetch_financials, ptk)))
    # ②③ BCTC đầy đủ DART (mã tài khoản IFRS)
    try:
        fs_full = AN.fetch_full(tk)
    except Exception:
        fs_full = None
    # EV/EBIT đối thủ trong nước: EV = vốn hóa + nợ ròng + thiểu số (DART), EBIT = LN HĐ năm gần nhất (Naver)
    peer_ev = []
    if peers:
        def _ev(r):
            try:
                pfs = AN.fetch_full(r["ticker"])
            except Exception:
                pfs = None
            ann = [c for c in (peer_fins.get(r["ticker"]) or {}).get("annual", []) if not c.get("is_consensus")]
            # EBIT 4 quý gần nhất (cùng cơ sở với mã đang định giá); thiếu quý → EBIT năm gần nhất
            ebit = DCF.ttm_ebit((peer_fins.get(r["ticker"]) or {}).get("quarter", []))
            if ebit is None:
                ebit = ann[-1].get("op_profit") if ann else None
            return {"Mã": r["ticker"], "Tên": r.get("name"), "EBIT 4 quý (억)": ebit,
                    "EV/EBIT": DCF.ev_ebit_of(r.get("market_cap"), ebit, pfs)}
        with _cf.ThreadPoolExecutor(max_workers=4) as ex:
            peer_ev = list(ex.map(_ev, peers))
    return {"row": row, "annual": fin.get("annual", []), "quarters": fin.get("quarter", []), "series": series,
            "beta": beta, "extras": extras, "peers": peers, "peer_fins": peer_fins, "fs_full": fs_full,
            "peer_ev": peer_ev}


@st.cache_data(ttl=3600, show_spinner=False)
def _foreign(codes: tuple) -> list:
    return PR.fetch_foreign(list(codes))


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
    n_peers = st.slider("Số đối thủ trong nước", 5, 20, 12)

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
fs_full = D.get("fs_full")
row["beta"] = beta
name = row.get("name") or tk
price, mc = row.get("price"), row.get("market_cap")
reals = [c for c in series if not c.get("is_consensus")]

# ---------------------------------------------------------------------------- tính toàn bộ pipeline
idn = P.identify(row)
dq = P.data_quality(row, series, quarters, beta)
ratios = P.ratio_table(series)
# ① đối thủ nước ngoài: mặc định theo nhóm ngành, người dùng sửa được (mã Reuters của Naver)
with st.sidebar:
    st.markdown("---")
    _fdef = ", ".join(PR.FOREIGN_PEERS.get(idn["bucket"], []))
    _fcodes = st.text_input("Đối thủ nước ngoài (mã Reuters)", _fdef, key=f"fx_{tk}",
                            help="vd TSM, MU.O, 7203.T, 0700.HK — để trống = không so nước ngoài")
foreign_codes = tuple(c.strip() for c in _fcodes.split(",") if c.strip())
with st.spinner("Đang lấy đối thủ nước ngoài…"):
    foreign = _foreign(foreign_codes) if foreign_codes else []
peer_set = PR.build_peer_set(row, D.get("annual", []), idn["bucket"], peer_rows, D.get("peer_fins", {}),
                             foreign_profiles=foreign, self_quarters=quarters)
decomp = AN.decompose(fs_full) if fs_full else None
quality = AN.quality(fs_full, (mc / 1e8) if mc else None, idn["bucket"]) if fs_full else None
from financials import assess_financials  # noqa: E402
fin_health = assess_financials(D.get("annual", []), row.get("sector"), row.get("industry_kr"))
# hồ sơ: lưu ngay phần dữ liệu (①②③) để bước sau / trang sàng lọc đọc lại
_meta = {"name": name, "industry_kr": row.get("industry_kr"),
         "bucket": idn["bucket"], "price": row.get("price")}
try:
    DS.save_section(tk, "peers", peer_set, meta=_meta)
    if decomp:
        DS.save_section(tk, "financials", {"decomp": decomp, "ratios": ratios, "data_quality": dq["checks"]})
    if quality:
        DS.save_section(tk, "quality", quality)
except Exception:
    pass

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
          "extra": D_extra, "fs_full": fs_full, "peer_ev": D.get("peer_ev") or []}
LOG_PATH = os.path.join(_ROOT, "valuation_log.csv")


# ---------------------------------------------------------------------------- tiêu đề + bảng giả định
st.subheader(f"{name} · {tk}")
st.caption(f"{row.get('industry_kr') or '—'} · nguồn giá {row.get('source') or '—'} · "
           f"vốn hóa {mc / 1e12:,.1f} nghìn tỷ KRW" if mc else "")

_OUTER = st.tabs(["① Đối thủ trong & ngoài nước", "② Phân tích & bóc tách tài chính", "③ Chất lượng tài sản",
                  "④ Định giá theo ngành", "⑤ Kết luận & đánh giá tổng quan"])
with _OUTER[3]:
    _INNER = st.tabs(["🏭 Chuyên ngành", "Dự phóng", "Chi phí vốn", "Các mô hình", "Kịch bản & độ nhạy",
                      "Football field & nhật ký"])
T = {"1": _OUTER[0], "2": _OUTER[1], "3": _OUTER[1], "q": _OUTER[2], "sec": _INNER[0], "4": _INNER[1],
     "5": _INNER[2], "6": _INNER[3], "7": _INNER[4], "8": _INNER[5], "end": _OUTER[4]}

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
    st.markdown("#### Dự phóng nhiều năm — sửa trực tiếp các ô giả định")
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
                                           for c in ("Tăng trưởng DT %", "Biên LN HĐ %", "Biên LN ròng %",
                                                     "Tỷ lệ cổ tức %")})
        drivers = ed.to_dict("records")

EV = P.evaluate(D_eval, A, drivers, idn, dq, evidence)
fc, fg, base, anchor, rim, ddm, rec = (EV[k] for k in ("fc", "fg", "base", "anchor", "rim", "ddm", "rec"))

with T["4"]:
    if fc:
        st.markdown("**Dự phóng năm**")
        fdf = pd.DataFrame(fc)
        st.dataframe(fdf.style.format({c: "{:,.0f}" for c in ("Doanh thu (억)", "LN HĐ (억)", "LN ròng (억)", "EPS",
                                                              "DPS", "BPS đầu kỳ", "BPS cuối kỳ")}
                                      | {c: "{:+.1f}" for c in ("Tăng trưởng DT %",)}
                                      | {c: "{:.1f}" for c in ("Biên LN HĐ %", "Biên LN ròng %", "ROE %")}, na_rep="—"),
                     hide_index=True, width="stretch")
        st.markdown("**Dự phóng quý** (2 năm đầu)")
        qfc = DCF.quarter_forecast(quarters, fc, EV.get("shares"))
        if qfc:
            # đồng thuận Naver (cột isConsensus) chỉ để đối chiếu, KHÔNG dùng trong mô hình
            _cns = {f"{str(c['period'])[:4]}.{str(c['period'])[4:6]}": c.get("eps") for c in quarters
                    if c.get("is_consensus") and str(c.get("period", ""))[:6].isdigit()}
            for r_ in qfc:
                r_["EPS đồng thuận (Naver)"] = _cns.get(r_["Quý"])
            _qdf = pd.DataFrame(qfc)

            def _qs(r_):
                return ["background-color:#eef6ff" if r_["Loại"] == "Dự phóng" else ""] * len(r_)
            st.dataframe(_qdf.style.apply(_qs, axis=1).format(
                {"DT (억)": "{:,.0f}", "LN HĐ (억)": "{:,.0f}", "LN ròng (억)": "{:,.0f}", "EPS": "{:,.0f}",
                 "EPS đồng thuận (Naver)": "{:,.0f}"}, na_rep="—"), hide_index=True, width="stretch")
            _w = DCF.seasonal_weights(quarters)
            st.caption("Quý dự phóng = (dự phóng năm − lũy kế quý thực) chia cho các quý còn lại → tổng các quý khớp "
                       "đúng dự phóng năm. "
                       + ("**Chia đều**: doanh thu 4 quý gần nhất lệch nhau > 1,3× — đó là xu hướng tăng/giảm, không "
                          "phải mùa vụ." if _w["_even"] else
                          f"Theo **mùa vụ** 4 quý thực gần nhất (Q1 {_w['03']:.0%} · Q2 {_w['06']:.0%} · Q3 "
                          f"{_w['09']:.0%} · Q4 {_w['12']:.0%} doanh thu).")
                       + " Cột đồng thuận Naver chỉ để đối chiếu.")
        else:
            st.caption("Chưa đủ dữ liệu quý để chia dự phóng.")
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
    st.markdown("#### Nhận diện doanh nghiệp")
    c1, c2, c3 = st.columns(3)
    c1.metric("Nhóm định giá", idn["bucket"])
    c2.metric("Ngành chu kỳ?", "Có" if idn["cyclical"] else "Không")
    c3.metric("Dữ liệu đủ cho mô hình chuẩn?", "Đủ" if idn["data_ok"] else "Chỉ proxy")
    st.markdown(f"**Mô hình chính theo ngành:** {idn['primary']}  \n**Đối chiếu:** {', '.join(idn['cross'])}")
    st.info(idn["note"])
    if not idn["data_ok"]:
        st.caption("Ngành cần dữ liệu sâu hơn (backlog/capex/SOTP/pipeline) mà App chưa có → độ tin cậy kết luận "
                   "bị chặn trần **Medium**.")

    st.markdown("#### So sánh với đối thủ trong & ngoài nước")
    _cols = ["Mã", "Tên", "Thị trường", "Vốn hóa (tỷ KRW)"] + PR.METRICS + ["Năm BCTC"]
    _tbl = pd.DataFrame([{**peer_set["self"], "Tên": f"▶ {peer_set['self']['Tên']}"}] + peer_set["domestic"]
                        + peer_set["foreign"])
    _tbl = _tbl[[c for c in _cols if c in _tbl.columns]]
    for _c in _tbl.columns:                        # None trong cột số → NaN để hiện "—" (Styler không coi None là NaN)
        if _c not in ("Mã", "Tên", "Thị trường", "Năm BCTC"):
            _tbl[_c] = pd.to_numeric(_tbl[_c], errors="coerce")

    def _hl(r):
        return ["background-color:#fff3cd; font-weight:600" if str(r["Tên"]).startswith("▶") else ""] * len(r)
    st.dataframe(_tbl.style.apply(_hl, axis=1).format(
        {"Vốn hóa (tỷ KRW)": "{:,.0f}", "P/E": "{:.1f}", "P/B": "{:.2f}"}
        | {m: "{:+.1f}" if "Tăng trưởng" in m else "{:.1f}" for m in PR.METRICS if m not in ("P/E", "P/B")},
        na_rep="—"), hide_index=True, width="stretch")
    st.caption(f"Trong nước: {len(peer_set['domestic'])} mã (gợi ý Naver + top vốn hóa cùng ngành KRX, chỉ cổ phiếu "
               f"thường) · nước ngoài: {len(peer_set['foreign'])} mã (Naver, đã quy KRW; sửa danh sách ở thanh bên). "
               "ROE nước ngoài ≈ P/B ÷ P/E. P/E ≤ 0 hoặc > 200 không vào trung vị"
               + (f" (loại: {', '.join(peer_set['foreign_excluded'])})" if peer_set["foreign_excluded"] else "") + ".")
    st.markdown("**Vị thế của mã so với trung vị đối thủ**")
    st.dataframe(pd.DataFrame(peer_set["position"]).style.format(
        {"Mã": "{:,.2f}", "Trung vị trong nước": "{:,.2f}", "Trung vị nước ngoài": "{:,.2f}",
         "Hạng trong nước (%)": "{:.0f}"}, na_rep="—"), hide_index=True, width="stretch")
    st.markdown("#### So sánh quý gần nhất")
    _q = peer_set.get("quarter") or {}
    _qrows = ([{**_q["self"], "Tên": f"▶ {_q['self']['Tên']}"}] if _q.get("self") else []) + \
        (_q.get("domestic") or []) + (_q.get("foreign") or [])
    if _qrows:
        _qt = pd.DataFrame(_qrows)
        for _c in _qt.columns:
            if _c not in ("Mã", "Tên", "Thị trường", "Quý"):
                _qt[_c] = pd.to_numeric(_qt[_c], errors="coerce")
        st.dataframe(_qt.style.apply(_hl, axis=1).format(
            {"DT quý (억)": "{:,.0f}", "LN HĐ quý (억)": "{:,.0f}", "DT 4 quý (억)": "{:,.0f}", "DT YoY %": "{:+.1f}",
             "LN HĐ YoY %": "{:+.1f}", "Biên LN HĐ quý %": "{:.1f}", "Biên LN ròng quý %": "{:.1f}",
             "P/E 4 quý": "{:.1f}"}, na_rep="—"), hide_index=True, width="stretch")
        _qs, _qmd, _qmf = _q.get("self") or {}, _q.get("median_domestic") or {}, _q.get("median_foreign") or {}
        _qcmp = [{"Chỉ số": m, "Mã": _qs.get(m), "Trung vị trong nước": _qmd.get(m), "Trung vị nước ngoài": _qmf.get(m)}
                 for m in PR.Q_METRICS + ["P/E 4 quý"]]
        st.dataframe(pd.DataFrame(_qcmp).style.format({"Mã": "{:,.1f}", "Trung vị trong nước": "{:,.1f}",
                                                       "Trung vị nước ngoài": "{:,.1f}"}, na_rep="—"),
                     hide_index=True, width="stretch")
        st.caption("YoY = so với **cùng quý năm trước** (khử mùa vụ). P/E 4 quý = vốn hóa ÷ LN ròng 4 quý gần nhất "
                   "(TTM) — phản ánh kết quả mới hơn P/E theo năm. Kỳ kế toán nước ngoài có thể lệch lịch (Micron "
                   "kết thúc quý tháng 2/5/8/11) — cột Quý ghi tháng kết thúc thực tế.")
    else:
        st.info("Chưa có dữ liệu quý.")
    _fd = peer_set["foreign_discount"]
    if _fd.get("P/E") is not None or _fd.get("P/B") is not None:
        st.info("🌐 **So với đối thủ nước ngoài:** "
                + " · ".join(f"{m} {'thấp' if v < 0 else 'cao'} hơn trung vị {abs(v):.0f}%"
                             for m, v in _fd.items() if v is not None)
                + ". Đối thủ nước ngoài **chỉ để so sánh**, không vào phép neo định giá ở bước ④ — cổ phiếu Hàn có "
                  "chiết khấu kinh niên, trộn bội số quốc tế sẽ đẩy giá trị hợp lý lên có hệ thống.")
    st.caption(f"💾 Đã lưu vào hồ sơ `dossiers/{tk}_{_dt.date.today():%Y%m%d}.json` — bước ④ dùng lại đúng danh sách "
               "đối thủ trong nước này làm phép neo.")

with T["2"]:
    st.markdown(f"#### Kiểm tra số liệu — {dq['n_fail']} ❌ · {dq['n_warn']} ⚠️")
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
    st.markdown("#### Tỉ số từng năm & chuẩn hóa")
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

    st.markdown("#### Bóc tách tài chính" + (f" — năm {decomp['year']} ({'hợp nhất' if decomp['fs_div'] == 'CFS' else 'riêng'}, DART)"
                                              if decomp else ""))
    if not decomp:
        st.info("Cần DART_API_KEY để bóc tách từ BCTC đầy đủ (DuPont, cơ cấu lợi nhuận, dòng tiền, vốn lưu động).")
    else:
        c1, c2, c3, c4 = st.columns(4)
        _dp = decomp["dupont"]
        c1.metric("Biên LN ròng", f"{_dp['Biên LN ròng (cổ đông mẹ) %']:.1f}%" if _dp["Biên LN ròng (cổ đông mẹ) %"] is not None else "—")
        c2.metric("× Vòng quay tài sản", f"{_dp['Vòng quay tài sản (lần)']:.2f}" if _dp["Vòng quay tài sản (lần)"] else "—")
        c3.metric("× Đòn bẩy (TS/VCSH)", f"{_dp['Đòn bẩy (TS/VCSH)']:.2f}" if _dp["Đòn bẩy (TS/VCSH)"] else "—")
        c4.metric("= ROE (DuPont)", f"{_dp['ROE = tích ba thành phần %']:.1f}%" if _dp["ROE = tích ba thành phần %"] is not None else "—")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("**Cơ cấu lợi nhuận trước thuế**")
            st.dataframe(pd.DataFrame(decomp["structure"]).style.format({"억원": "{:,.0f}", "% LN trước thuế": "{:.0f}%"},
                                                                         na_rep="—"), hide_index=True, width="stretch")
        with cc2:
            st.markdown("**Dòng tiền**")
            st.dataframe(pd.DataFrame([{"Chỉ tiêu": k, "Giá trị": v} for k, v in decomp["cash"].items()])
                         .style.format({"Giá trị": "{:,.2f}"}, na_rep="—"), hide_index=True, width="stretch")
        cc3, cc4 = st.columns(2)
        with cc3:
            st.markdown("**Vốn lưu động**")
            st.dataframe(pd.DataFrame([{"Chỉ tiêu": k, "Ngày": v} for k, v in decomp["working_capital"].items()])
                         .style.format({"Ngày": "{:.0f}"}, na_rep="—"), hide_index=True, width="stretch")
        with cc4:
            st.markdown("**Cân đối & nợ**")
            st.dataframe(pd.DataFrame([{"Chỉ tiêu": k, "Giá trị": v} for k, v in decomp["balance"].items()])
                         .style.format({"Giá trị": "{:,.1f}"}, na_rep="—"), hide_index=True, width="stretch")
        st.caption("'Khác (gồm một lần)' = LN trước thuế − LN hoạt động − tài chính ròng − công ty liên kết. Tỷ trọng "
                   "lớn ở dòng này là lợi nhuận khó lặp lại.")

with T["q"]:
    st.markdown("#### ③ Chất lượng tài sản" + (f" — hạng **{quality['grade']}** ({quality['score']:.0f}/100)"
                                              if quality and quality.get("score") is not None else ""))
    if not quality:
        st.info("Cần DART_API_KEY (BCTC đầy đủ) để chấm chất lượng tài sản.")
    else:
        if quality["fin_limited"]:
            st.warning("Ngành tài chính: DART không có nợ xấu / dự phòng dạng cấu trúc → chỉ chấm được phần chung; "
                       "hạng tối đa **B**. Chất lượng tài sản ngân hàng cần đọc thuyết minh 사업보고서.")
        st.dataframe(pd.DataFrame(quality["checks"]).astype({"Giá trị": str}), hide_index=True, width="stretch")
        st.caption("Mỗi chỉ tiêu có ngưỡng số cố định: ✅ = 1 điểm · ⚠️ = 0,5 · ❌ = 0. Hạng A ≥ 80 · B ≥ 65 · C ≥ 50 · "
                   "D < 50; có ≥ 2 ❌ thì tối đa hạng C. F-score tính trên 8/9 tiêu chí (thiếu số cổ phiếu phát hành).")

with T["5"]:
    st.markdown("#### Chi phí vốn")
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
    st.markdown("#### Các mô hình định giá")
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
    st.markdown("#### Bảng tính chi tiết từng mô hình")
    _fm = EV.get("fcff")
    with st.expander("💵 FCFF-DCF — dòng tiền tự do cho doanh nghiệp" + (f" → {_fm['value']:,.0f} KRW/cp" if _fm and _fm.get("value") else ""),
                     expanded=bool(_fm)):
        if not _fm:
            st.caption("Không tính được: cần BCTC DART đầy đủ, dự phóng có biên LN HĐ; không áp dụng cho ngành tài chính.")
        elif _fm.get("note"):
            st.warning(_fm["note"])
            st.dataframe(pd.DataFrame(_fm["rows"]).style.format(
                {c: "{:,.0f}" for c in ("Doanh thu (억)", "EBIT (억)", "NOPAT (억)", "Tái đầu tư (억)", "FCFF (억)",
                                        "PV FCFF (억)")} | {"Thuế %": "{:.1f}", "ROIC %": "{:.1f}",
                                                            "Hệ số chiết khấu": "{:.3f}"}, na_rep="—"),
                hide_index=True, width="stretch")
        else:
            st.dataframe(pd.DataFrame([{"Giả định": k, "Giá trị": v} for k, v in _fm["assumptions"].items()])
                         .style.format({"Giá trị": "{:,.2f}"}), hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame(_fm["rows"]).style.format(
                {c: "{:,.0f}" for c in ("Doanh thu (억)", "EBIT (억)", "NOPAT (억)", "Tái đầu tư (억)", "FCFF (억)",
                                        "PV FCFF (억)")} | {"Thuế %": "{:.1f}", "ROIC %": "{:.1f}",
                                                            "Hệ số chiết khấu": "{:.3f}"}, na_rep="—"),
                hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame(_fm["bridge"]).style.format({"억원": "{:,.0f}"}), hide_index=True, width="stretch")
            st.caption(f"Số cp {EV['shares']:,.0f} · giá trị cuối kỳ chiếm **{(_fm['tv_share'] or 0) * 100:.0f}%** EV"
                       + (" ⚠️ > 75%" if (_fm["tv_share"] or 0) > P.TV_SHARE_WARN else "")
                       + ". FCFF = EBIT·(1−thuế) − ΔDoanh thu ÷ (Doanh thu/Vốn đầu tư) — cách Damodaran, không cần "
                         "khấu hao (DART tóm tắt và Naver không tách khấu hao cho DN Hàn). Cuối kỳ: tái đầu tư = g ÷ "
                         "RONIC; RONIC = ROIC năm cuối kẹp [WACC; 30%].")
    _em = EV.get("ev_ebit")
    with st.expander("🏭 EV/EBIT 4 quý × trung vị đối thủ trong nước" + (f" → {_em['value']:,.0f} KRW/cp" if _em and _em.get("value") else "")):
        _pe = D.get("peer_ev") or []
        if _pe:
            st.dataframe(pd.DataFrame(_pe).style.format({"EBIT 4 quý (억)": "{:,.0f}", "EV/EBIT": "{:.1f}"}, na_rep="—"),
                         hide_index=True, width="stretch")
        if _em:
            st.dataframe(pd.DataFrame(_em["rows"]).style.format({"Giá trị": "{:,.1f}"}), hide_index=True, width="stretch")
        st.caption("EV = vốn hóa + nợ ròng + lợi ích thiểu số (DART); EBIT = LN HĐ **4 quý gần nhất** cho cả mã và đối "
                   "thủ (cùng cơ sở). Bội số ngoài (0; 40] "
                   f"bị loại; cần ≥ {V.MIN_PEERS} đối thủ hợp lệ. Dùng **EV/EBIT thay EV/EBITDA** vì nguồn dữ liệu "
                   "không tách khấu hao của DN Hàn — EBIT so EBIT nhất quán, không phải đoán khấu hao.")
    with st.expander("📘 RIM tường minh (thu nhập thặng dư)" + (f" → {rim['value']:,.0f} KRW/cp" if rim else "")):
        if rim:
            st.dataframe(pd.DataFrame(rim["rows"]).style.format(
                {c: "{:,.0f}" for c in ("BPS đầu kỳ", "EPS", "Ke × BPS", "Lợi nhuận thặng dư (RI)", "PV RI")}
                | {"ROE %": "{:.1f}", "Hệ số chiết khấu": "{:.3f}"}, na_rep="—"), hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame(rim["bridge"]).style.format({"KRW/cp": "{:,.0f}"}), hide_index=True,
                         width="stretch")
            st.caption(f"RI = EPS − Ke × BPS đầu kỳ, Ke = {ke * 100:.2f}%. Cuối kỳ: RI năm cuối × w/(1+Ke−w).")
        else:
            st.caption("Không tính được (BPS ≤ 0 hoặc thiếu dự phóng).")
    with st.expander("💰 DDM tường minh (cổ tức)" + (f" → {ddm['value']:,.0f} KRW/cp" if ddm else "")):
        if ddm:
            st.dataframe(pd.DataFrame(ddm["rows"]).style.format({"DPS": "{:,.0f}", "PV DPS": "{:,.0f}",
                                                                 "Hệ số chiết khấu": "{:.3f}"}),
                         hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame(ddm["bridge"]).style.format({"KRW/cp": "{:,.0f}"}), hide_index=True,
                         width="stretch")
        else:
            st.caption("Không tính được (không trả cổ tức ở năm cuối dự phóng).")
    with st.expander("📐 Lõi KCI và bội số — đầu vào công thức"):
        _bps, _eps = V._num(row.get("bps")), V._num(row.get("eps"))
        _nm = V.normalized_metrics(reals) if reals else {}
        _inp = [("Giá", price), ("EPS (trailing)", _eps), ("BPS", _bps),
                ("ROE trailing %", (_eps / _bps * 100) if (_eps and _bps) else None),
                ("ROE trung vị nhiều năm (mid-cycle) %", (_nm.get("median_roe") or 0) * 100 if _nm.get("median_roe") is not None else None),
                ("EPS dự phóng năm 1", fc[0]["EPS"] if fc else None), ("Ke %", ke * 100), ("g dài hạn %", coc["g_term"] * 100),
                ("w (S-RIM)", w), ("P/E trung vị đối thủ", pm["pe"]), ("P/B trung vị đối thủ", pm["pb"])]
        st.dataframe(pd.DataFrame([{"Đầu vào": k, "Giá trị": v} for k, v in _inp])
                     .style.format({"Giá trị": "{:,.2f}"}, na_rep="—"), hide_index=True, width="stretch")
        st.markdown("- **S-RIM** = BPS + (ROE − Ke)·BPS·w/(1+Ke−w)\n- **P/B–ROE** = BPS·(ROE − g)/(Ke − g) "
                    "(ROE < Ke → g = 0)\n- **P/E × trung vị** = EPS × P/E trung vị đối thủ · **Forward P/E** = EPS dự "
                    "phóng năm 1 × P/E trung vị\n- **P/B × trung vị** = BPS × P/B trung vị đối thủ")
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
    st.markdown("#### Kịch bản Bear / Base / Bull")
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
    st.markdown("#### Football field")
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
overall = P.overall_assessment(rec, fin_health, quality, peer_set)
ctx.update(peer_set=peer_set, decomp=decomp, quality=quality, overall=overall)
md = P.report_markdown(ctx)
try:
    DS.save_section(tk, "valuation", {"fair": rec["fair"], "low": rec["low"], "high": rec["high"],
                                      "upside": rec["upside"], "confidence": rec["confidence"],
                                      "status": rec["status"], "models": rec["rows"], "ke": ke,
                                      "drivers": drivers, "expected_scenario": (scen or {}).get("expected")})
    DS.save_section(tk, "overall", overall)
except Exception:
    pass

with T["end"]:
    st.markdown("#### Đánh giá tổng quan")
    st.markdown(f"### {overall['verdict']}")
    _pc = st.columns(5)
    for _i, (_k, _v) in enumerate(overall["pillars"].items()):
        _pc[_i].metric(_k, f"{_v:.0f}/100" if _v is not None else "—")
    _pc[4].metric("Tổng (trung bình trụ có điểm)", f"{overall['total']:.0f}/100" if overall["total"] is not None else "—")
    st.caption("**Định giá** = 50 + chênh lệch % (không công bố → không chấm) · **Sức khỏe tài chính** = tiêu chí đầu "
               "tư cơ bản · **Chất lượng tài sản** = bước ③ · **Vị thế cạnh tranh** = hạng trong nước của tăng trưởng, "
               "biên LN HĐ, ROE. Kết luận theo quy tắc số: không thể 'rẻ và chất lượng tốt' khi chưa công bố giá trị "
               "hoặc chất lượng hạng D / ≥ 3 ❌. Backtest 2021–25 mới kiểm chứng phần **định giá** (IC +0,106); điểm "
               "tổng hợp là bản tóm tắt, chưa được kiểm chứng như một tín hiệu.")
    st.markdown("#### Kết luận định giá")
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
        pd.DataFrame([peer_set["self"]] + peer_set["domestic"] + peer_set["foreign"]).to_excel(
            xw, sheet_name="Đối thủ", index=False)
        pd.DataFrame(peer_set["position"]).to_excel(xw, sheet_name="Vị thế", index=False)
        _qq = peer_set.get("quarter") or {}
        _qr = ([_qq["self"]] if _qq.get("self") else []) + (_qq.get("domestic") or []) + (_qq.get("foreign") or [])
        if _qr:
            pd.DataFrame(_qr).to_excel(xw, sheet_name="Quý gần nhất", index=False)
        _qf = DCF.quarter_forecast(quarters, fc, EV.get("shares"))
        if _qf:
            pd.DataFrame(_qf).to_excel(xw, sheet_name="Dự phóng quý", index=False)
        if EV.get("fcff"):
            pd.DataFrame(EV["fcff"]["rows"]).to_excel(xw, sheet_name="FCFF-DCF", index=False)
            pd.DataFrame(EV["fcff"]["bridge"]).to_excel(xw, sheet_name="FCFF cầu nối", index=False)
        if rim:
            pd.DataFrame(rim["rows"]).to_excel(xw, sheet_name="RIM", index=False)
        if decomp:
            pd.DataFrame(decomp["structure"]).to_excel(xw, sheet_name="Cơ cấu LN", index=False)
        if quality:
            pd.DataFrame(quality["checks"]).astype({"Giá trị": str}).to_excel(xw, sheet_name="Chất lượng TS",
                                                                                index=False)
        pd.DataFrame([{"Trụ": k, "Điểm": v} for k, v in overall["pillars"].items()]).to_excel(
            xw, sheet_name="Tổng quan", index=False)
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
