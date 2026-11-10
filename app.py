"""
KCI — điểm vào của App (Streamlit). Chỉ khai báo các trang, tên tiếng Việt và thứ tự trên thanh bên.

Chạy:
    pip install -r requirements.txt
    streamlit run app.py

Streamlit Cloud dùng file này làm Main file (không đổi). Nội dung từng trang nằm ở views/ và pages/.
"""
import os

import streamlit as st

import reload_guard
import ui_theme

# Sau mỗi lần deploy: module dùng chung đã đổi trên đĩa → nạp lại cả bộ (tránh trang mới gọi hàm mà module cũ
# trong bộ nhớ chưa có — lỗi ImportError fetch_kgb10y_detail 11/11/2026).
reload_guard.drop_stale(os.path.dirname(os.path.abspath(__file__)))

PAGES = [
    st.Page("views/trang_chu.py", title="Trang chủ", icon="🏠", default=True),
    st.Page("views/tong_hop.py", title="Định giá tổng hợp", icon="📈", url_path="Tong_hop"),
    st.Page("pages/1_🚀_Sieu_co_phieu.py", title="Siêu cổ phiếu", icon="🚀", url_path="Sieu_co_phieu"),
    st.Page("pages/2_📰_Tin_nong.py", title="Tin nóng", icon="📰", url_path="Tin_nong"),
    st.Page("pages/4_🔍_Sang_loc.py", title="Sàng lọc", icon="🔍", url_path="Sang_loc"),
    st.Page("pages/3_🧮_Dinh_gia.py", title="Định giá", icon="🧮", url_path="Dinh_gia"),
]

_nav = st.navigation(PAGES)
ui_theme.apply()          # giao diện kiểu Apple cho mọi trang (màu/font ở .streamlit/config.toml)
_nav.run()
