"""
KCI — điểm vào của App (Streamlit). Chỉ khai báo các trang, tên tiếng Việt và thứ tự trên thanh bên.

Chạy:
    pip install -r requirements.txt
    streamlit run app.py

Streamlit Cloud dùng file này làm Main file (không đổi). Nội dung từng trang nằm ở views/ và pages/.
"""
import streamlit as st

PAGES = [
    st.Page("views/tong_hop.py", title="Định giá tổng hợp", icon="📈", default=True),
    st.Page("pages/1_🚀_Sieu_co_phieu.py", title="Siêu cổ phiếu", icon="🚀", url_path="Sieu_co_phieu"),
    st.Page("pages/2_📰_Tin_nong.py", title="Tin nóng", icon="📰", url_path="Tin_nong"),
    st.Page("pages/4_🔍_Sang_loc.py", title="Sàng lọc", icon="🔍", url_path="Sang_loc"),
    st.Page("pages/3_🧮_Dinh_gia.py", title="Định giá", icon="🧮", url_path="Dinh_gia"),
]

st.navigation(PAGES).run()
