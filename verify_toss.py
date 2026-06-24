"""
Test kết nối Toss API — CHẠY TRÊN MÁY BẠN (nơi có .env và mạng tới Toss).

  pip install -r requirements.txt
  cp .env.example .env   # rồi điền Client ID/Secret thật
  python verify_toss.py

Script sẽ: lấy token -> thử giá Samsung -> 52w -> tải openapi spec để đối chiếu endpoint.
Không in secret ra màn hình.
"""
from toss_api import TossClient

def main():
    try:
        cli = TossClient()
    except RuntimeError as e:
        print("[X]", e)
        return
    print("[1] Phát hành token...", end=" ")
    try:
        cli._get_token()
        print("OK")
    except Exception as e:
        print("LỖI:", str(e)[:120]); return

    print("[2] Giá Samsung (005930):")
    try:
        print("    ", cli.get_price("005930"))
    except Exception as e:
        print("    LỖI:", str(e)[:120], "(kiểm tra lại path /v1/market/price trong openapi.json)")

    print("[3] 52w high/low:")
    try:
        print("    ", cli.get_52w_high_low("005930"))
    except Exception as e:
        print("    LỖI:", str(e)[:120])

    print("[4] Tải OpenAPI spec để đối chiếu endpoint...", end=" ")
    try:
        p = cli.download_openapi_spec("toss_openapi.json")
        print("đã lưu:", p)
        print("    -> Mở file này để xác minh path/field thật, chỉnh toss_api.py nếu cần.")
    except Exception as e:
        print("LỖI:", str(e)[:120])

if __name__ == "__main__":
    main()
