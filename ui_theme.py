"""
Giao diện chung kiểu Apple cho mọi trang — gọi apply() một lần ở app.py (chạy trước mỗi trang).

Màu, font, bo góc nằm ở .streamlit/config.toml; file này chỉ thêm HIỆU ỨNG mà config không làm được:
thanh bên & đầu trang kính mờ, thẻ nổi có bóng mềm, nút co giãn khi bấm, nội dung hiện dần.
Người dùng bật "giảm chuyển động" trong hệ điều hành → tắt hết chuyển động.
"""
import streamlit as st

_EASE = "cubic-bezier(0.25, 0.1, 0.25, 1)"      # đường cong chuẩn của Apple

CSS = f"""
<style>
:root {{
  --kci-card: #FFFFFF;
  --kci-line: rgba(0,0,0,0.06);
  --kci-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.05);
  --kci-shadow-hover: 0 2px 4px rgba(0,0,0,0.05), 0 12px 32px rgba(0,0,0,0.09);
}}
html, body, [class*="st-"] {{ -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }}

/* Tiêu đề: chữ khít như apple.com */
h1, h2, h3 {{ letter-spacing: -0.022em; }}
h1 {{ font-weight: 700 !important; }}

/* Đầu trang & thanh bên: kính mờ */
header[data-testid="stHeader"] {{
  background: rgba(245,245,247,0.72);
  backdrop-filter: saturate(180%) blur(20px); -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid var(--kci-line);
}}
section[data-testid="stSidebar"] > div:first-child {{
  background: rgba(255,255,255,0.72);
  backdrop-filter: saturate(180%) blur(20px); -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-right: 1px solid var(--kci-line);
}}
[data-testid="stSidebarNav"] a {{ border-radius: 10px; transition: background .2s {_EASE}; }}
[data-testid="stSidebarNav"] a:hover {{ background: rgba(0,113,227,0.08); }}

/* Nội dung hiện dần, trượt nhẹ lên */
@keyframes kciFadeUp {{ from {{ opacity: 0; transform: translateY(8px); }} to {{ opacity: 1; transform: none; }} }}
.block-container {{ animation: kciFadeUp .45s {_EASE} both; }}

/* Thẻ chỉ số (st.metric) */
[data-testid="stMetric"] {{
  background: var(--kci-card); border-radius: 18px; padding: 16px 20px;
  box-shadow: var(--kci-shadow); border: 1px solid var(--kci-line);
  transition: transform .3s {_EASE}, box-shadow .3s {_EASE};
}}
[data-testid="stMetric"]:hover {{ transform: translateY(-2px); box-shadow: var(--kci-shadow-hover); }}
[data-testid="stMetricLabel"] {{ color: #6E6E73; font-weight: 500; }}
[data-testid="stMetricValue"] {{ letter-spacing: -0.02em; font-weight: 600; }}

/* Bảng dữ liệu, expander, khung có viền: thẻ trắng bo tròn */
[data-testid="stDataFrame"], [data-testid="stTable"] {{
  background: var(--kci-card); border-radius: 14px; box-shadow: var(--kci-shadow); overflow: hidden;
}}
[data-testid="stExpander"] details {{
  background: var(--kci-card); border-radius: 14px !important; border: 1px solid var(--kci-line) !important;
  box-shadow: var(--kci-shadow); transition: box-shadow .3s {_EASE};
}}
[data-testid="stExpander"] details:hover {{ box-shadow: var(--kci-shadow-hover); }}
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {{ border-radius: 18px; }}

/* Nút: co giãn mềm khi rê / bấm */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button, [data-testid="stPageLink"] a {{
  transition: transform .2s {_EASE}, box-shadow .2s {_EASE}, background-color .2s {_EASE};
  font-weight: 500;
}}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
  transform: scale(1.02); box-shadow: 0 4px 14px rgba(0,113,227,0.18);
}}
.stButton > button:active, .stDownloadButton > button:active, .stFormSubmitButton > button:active {{
  transform: scale(0.97);
}}

/* Ô nhập: viền sáng xanh khi focus */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{
  transition: box-shadow .2s {_EASE}, border-color .2s {_EASE};
}}
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within, [data-baseweb="textarea"]:focus-within {{
  box-shadow: 0 0 0 4px rgba(0,113,227,0.18);
}}

/* Tab: gạch chân xanh mảnh */
[data-baseweb="tab-highlight"] {{ background-color: #0071E3; height: 2px; border-radius: 2px; }}

/* Thông báo (info/warning/success/error): bo tròn mềm */
[data-testid="stAlert"] > div {{ border-radius: 14px; }}

@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ animation: none !important; transition: none !important; }}
}}
</style>
"""


def apply() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
