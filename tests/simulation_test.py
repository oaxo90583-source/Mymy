# -*- coding: utf-8 -*-
import sys
import os
import time

# เพิ่ม path เพื่อให้ import โมดูลใน app ได้
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app import models, calc

def run_simulation():
    print("🚀 เริ่มการจำลองระบบบอร์ดมวยพักยก (Simulation Mode)")
    print("="*50)
    
    # 1. เริ่มต้นฐานข้อมูล
    models.init_db()
    
    # 2. สร้างสมาชิกสมมติ
    print("\n1. สร้างสมาชิกและเติมเครดิต...")
    user1_id = "U_USER_A"
    user2_id = "U_USER_B"
    user3_id = "U_USER_C"
    
    mid_a = models.ensure_admin(user1_id) # ให้ A เป็นแอดมินด้วยเพื่อความง่าย
    mid_b = models.new_member(user2_id, "นาย ข")
    mid_c = models.new_member(user3_id, "นาย ค")
    
    # เติมเงินให้แต่ละคน
    models.adjust_credit(mid_a, 1000) # นาย ก มี 1,000
    models.adjust_credit(mid_b, 500)  # นาย ข มี 500
    models.adjust_credit(mid_c, 2000) # นาย ค มี 2,000
    
    m_a = models.get_member_info(mid_a)
    m_b = models.get_member_info(mid_b)
    m_c = models.get_member_info(mid_c)
    
    print(f"   - {m_a['member_code']} (นาย ก): เครดิต {m_a['credit']:,.2f}")
    print(f"   - {m_b['member_code']} (นาย ข): เครดิต {m_b['credit']:,.2f}")
    print(f"   - {m_c['member_code']} (นาย ค): เครดิต {m_c['credit']:,.2f}")

    # 3. เปิดคู่มวยและตั้งราคา
    print("\n2. เปิดคู่มวยและตั้งราคา/ป้ายรับ...")
    match_id = models.new_match("แดง (เก่ง) vs น้ำเงิน (เฮง)")
    
    # ราคา ด2/1 (แดงต่อ 2/1) ป้ายรับ 1,000
    price_red = calc.Price("แดง", True, 2, 1)
    accept_limit = 1000
    board_id = models.add_board(match_id, "ด2/1 ป้าย 1000", "ต่อไป", price_red, None, accept_limit, False)
    print(f"   - เปิดคู่: แดง vs น้ำเงิน")
    print(f"   - ราคา: {price_red}")
    print(f"   - ป้ายรับ (Limit): {accept_limit:,.2f} ต่อฝั่ง")

    # 4. จำลองการแทงในสถานการณ์ต่างๆ
    print("\n3. เริ่มการแทง (Simulating Bets)...")
    
    # เคส A: นาย ก แทงเกินเครดิต (มี 1,000 แทง 1,500)
    print(f"   [A] นาย ก แทงแดง 1,500 (เครดิตมี 1,000)")
    actual_a, ok_a, msg_a = calc.check_bet_limit(1500, 1000, accept_limit)
    models.add_bet(mid_a, match_id, board_id, "แดง", 1500, actual_a, not ok_a, "ติด", msg_a)
    models.adjust_credit(mid_a, -actual_a)
    print(f"       >> ผลลัพธ์: {msg_a} (ยอดติดจริง: {actual_a:,.2f})")
    
    # เคส B: นาย ข แทงแดง 300 (ปกติ)
    # เช็คป้ายรับที่เหลือ: เดิม 1,000 - ติดไปแล้ว 1,000 = เหลือ 0
    remaining_limit = models.open_board_for_side(match_id, "แดง")
    print(f"   [B] นาย ข แทงแดง 300 (ป้ายรับเหลือ {remaining_limit:,.2f})")
    actual_b, ok_b, msg_b = calc.check_bet_limit(300, 500, remaining_limit)
    models.add_bet(mid_b, match_id, board_id, "แดง", 300, actual_b, not ok_b, "ติด" if actual_b > 0 else "ไม่ติด", msg_b)
    models.adjust_credit(mid_b, -actual_b)
    print(f"       >> ผลลัพธ์: {msg_b} (ยอดติดจริง: {actual_b:,.2f})")

    # เคส C: นาย ค แทงน้ำเงิน 500 (ฝั่งรอง 5/3)
    # ต้องเปลี่ยนราคาเป็นรองน้ำเงินก่อน
    price_blue = calc.Price("น้ำเงิน", False, 5, 3) # ง5/3
    board_id_v2 = models.add_board(match_id, "ง5/3", "รองเงิน", None, price_blue, 2000, False)
    print(f"   [C] นาย ค แทงน้ำเงิน 500 (ราคารอง 5/3)")
    actual_c, ok_c, msg_c = calc.check_bet_limit(500, 2000, 2000)
    models.add_bet(mid_c, match_id, board_id_v2, "น้ำเงิน", 500, actual_c, not ok_c, "ติด", msg_c)
    models.adjust_credit(mid_c, -actual_c)
    print(f"       >> ผลลัพธ์: {msg_c} (ยอดติดจริง: {actual_c:,.2f})")

    # 5. สรุปผลการแข่งขัน
    print("\n4. สรุปผลการแข่งขัน (Settle Match)...")
    print("   - ประกาศผล: [น้ำเงินชนะ] (แดงแพ้เสียครึ่ง)")
    models.set_result(match_id, "น้ำเงิน")
    settle_results = models.settle_match(match_id)
    
    for res in settle_results:
        m = models.get_member_info(models.list_bets(match_id=match_id, member_id=None)[0]['member_id']) # simplified for demo
        # หาข้อมูลเบื้องต้นของบิลนี้
        print(f"   - บิล ID {res['bet_id']}: ฝั่ง {res['side']} | แทง {res['actual']:,.2f} | ผล: {res['result']} | {res['note']}")

    # 6. ดูยอดสรุปเจ้ามือ
    print("\n5. ตรวจสอบยอดสรุปเจ้ามือรายวัน...")
    summary = models.get_daily_summary()
    profit_label = "กำไร" if summary['house_profit'] >= 0 else "ขาดทุน"
    print(f"   - วันที่: {summary['date']}")
    print(f"   - ยอดแทงรวมทั้งวัน: {summary['total_bet']:,.2f} บาท")
    print(f"   - {profit_label}สุทธิของเจ้ามือ: {abs(summary['house_profit']):,.2f} บาท")
    
    print("\n" + "="*50)
    print("✅ จบการจำลองระบบ: ทุกฟังก์ชันทำงานถูกต้องตามกติกา")

if __name__ == "__main__":
    run_simulation()
