# -*- coding: utf-8 -*-
"""ทดสอบ end-to-end: admin เปิดราคา → member แทง → ตรวจยอด → ประกาศผล"""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ADMIN = "Uadmin00000000000000000000000000"
MEM1 = "Umember11111111111111111111111111"
os.environ["LINE_ADMIN_IDS"] = ADMIN  # ต้อง set ก่อน import bot ( ADMIN_IDS อ่านตอน import)

from app import bot, models, line_api  # noqa: E402

# patch line_api ให้ mock reply
line_api.reply = lambda t, m, token="": None
line_api.get_profile = lambda uid, token="": {"displayName": "Test"}

def main():
    models.init_db()

    print("1) แอดมินเปิดราคา กลางอากาศ")
    r = bot.handle_message(ADMIN, "แอด", "กลางอากาศ ⏎ 🔴ด52    ง53 ⏎ รับ4000")
    print("   ", [m.get("text") for m in r])

    print("2) เติมเครดิตสมาชิก 1000")
    r = bot.handle_message(ADMIN, "แอด", f"เติม{MEM1} 1000")
    print("   ", r[0].get("text"))

    print("3) สมาชิกแทง ด500")
    r = bot.handle_message(MEM1, "ตู่", "ด500")
    print("   ", r[0].get("text"))

    print("4) สมาชิกแทง ง1500 (เกินเครดิต)")
    r = bot.handle_message(MEM1, "ตู่", "ง1500")
    print("   ", r[0].get("text"))

    print("5) สมาชิกตรวจเครดิต c")
    r = bot.handle_message(MEM1, "ตู่", "c")
    print("   ", r[0].get("text"))

    print("6) สมาชิกพิมพ์ ง500ด (ควรไม่ติด)")
    r = bot.handle_message(MEM1, "ตู่", "ง500ด")
    print("   ", r[0].get("text"))

    print("7) แอดมินประกาศผล: เงินชนะ (จง)")
    r = bot.handle_message(ADMIN, "แอด", "จง")
    print("   ", [m.get("text") for m in r])

    print("8) ตรวจ credits")
    conn = models.get_conn()
    for uid, label in ((ADMIN, "แอดมิน"), (MEM1, "สมาชิก")):
        mid = models.find_member(uid)
        credit = conn.execute("SELECT credit FROM members WHERE id=?", (mid,)).fetchone()["credit"]
        print(f"   {label}: {credit:,.2f}")

    print("9) ตรวจ settle_log")
    for row in conn.execute("SELECT * FROM settle_log"):
        print("  ", dict(row))

    print("10) คีย์ลัด: บช")
    r = bot.handle_message(MEM1, "ตู่", "บช")
    print("   ", r[0].get("text"))

    print("11) non-admin เปิดราคา (ควรโดนปฎิเสธ)")
    r = bot.handle_message(MEM1, "ตู่", "กลางอากาศ ⏎ 🔴ด52 ง53 ⏎ รับ4000")
    print("   ", r[0].get("text"))

    print("\nALL E2E DONE")


if __name__ == "__main__":
    main()
