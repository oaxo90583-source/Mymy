# -*- coding: utf-8 -*-
"""
app/seed.py — โหลดคีย์ลัด 206 รายการจาก all_details.json ลงฐานข้อมูล
"""

import os
import sys
from app import models

DEFAULT_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                           "line_scraper", "all_details.json")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MUAYTHAI_KEYWORDS_JSON", DEFAULT_SRC)
    models.init_db()
    n = models.seed_keywords_from_json(src)
    print(f"โหลดคีย์ลัด {n} รายการ จาก {src}")


if __name__ == "__main__":
    main()
