"""ตรวจสอบ LINE Channel Access Token — ดึง bot profile / basic info"""
import os
import sys
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
if not TOKEN:
    print("ERROR: ตั้ งค่า LINE_CHANNEL_ACCESS_TOKEN ก่อน")
    raise SystemExit(1)

H = {"Authorization": f"Bearer {TOKEN}"}
BASE = "https://api.line.me/v2/bot"

try:
    r = requests.get(f"{BASE}/info/basic", headers=H, timeout=15)
    print("basic:", r.status_code, r.text)
    r = requests.get(f"{BASE}/info", headers=H, timeout=15)
    print("profile:", r.status_code, r.text)
except Exception as e:
    print("ERROR:", e)
