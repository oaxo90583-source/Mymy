# -*- coding: utf-8 -*-
"""
run.py — รันบอท (FastAPI):

  python3 run.py               # dev: http://0.0.0.0:8000
  uvicorn app.web:app          # เช่นเดียวกัน

env ที่ใช้ (หรือตั้งใน config.yaml):
  LINE_CHANNEL_ACCESS_TOKEN   — Channel Access Token ของ LINE Messaging API
  LINE_CHANNEL_SECRET         — Channel Secret (สำหรับ verify webhook)
  LINE_ADMIN_IDS              — LINE User ID ของแอดมิน (คั่นคอมมา)
  ADMIN_TOKEN                 — token เข้าพอร์ทแอดมินเว็บ
  MUAYTHAI_DB_PATH            — ตำแหน่งไฟล์ SQLite (default: data/muaythai.db)
  MUAYTHAI_CONFIG             — ตำแหน่ง config.yaml
"""

import os
import sys
import uvicorn

sys.path.insert(0, os.path.dirname(__file__))

from app import models  # noqa: E402

models.init_db()

if __name__ == "__main__":
    uvicorn.run("app.web:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                log_level="info")
