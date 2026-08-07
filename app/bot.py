# -*- coding: utf-8 -*-
import re
import logging
from typing import List, Optional
from . import models, calc, line_api

logger = logging.getLogger(__name__)

# คีย์ลัดแอดมิน: ฝาก/ถอน/เติม [ชื่อ/รหัส] [ยอด] [หมายเหตุ]
ADMIN_CMD = re.compile(r"^(ฝาก|ถอน|เติม)\s+(\S+)\s+([\d,]+)(?:\s+(.*))?$", re.I)

# คีย์ลัดผลมวย: dd, ff, sm, เสมอ, ยก, ยกเลิก, ยุติ, ล, p, เปิด, สรุปรายวัน
RESULT_CMD = re.compile(r"^([a-zA-Zก-ฮ]{1,10})(?:\s+(.*))?$", re.I)

def is_admin(user_id: str) -> bool:
    """ตรวจสอบสิทธิ์แอดมิน"""
    return models.is_admin(user_id)

def ensure_member(user_id: str, display_name: str) -> int:
    """ลงทะเบียนสมาชิกถ้ายังไม่มี"""
    return models.ensure_member(user_id, display_name)

def get_open_match() -> Optional[int]:
    """ดึง ID คู่ที่กำลังเปิดรับแทง"""
    return models.get_open_match()

def admin_credit_cmd(admin_uid: str, kind: str, target: str, amount: float) -> str:
    """ประมวลผลคำสั่ง ฝาก/ถอน/เติม ของแอดมิน"""
    target_id = models.find_member(target)
    if not target_id:
        return f"❌ ไม่พบสมาชิก '{target}' (กรุณาใช้ชื่อ LINE หรือรหัสลูกค้า Mxxxx)"
    
    if kind == "ฝาก" or kind == "เติม":
        models.adjust_credit(target_id, amount)
        models.log_txn(target_id, kind, amount, f"โดยแอดมิน {admin_uid[:6]}")
        msg = f"✅ เติมเงินให้ {target} จำนวน {amount:,.2f} สำเร็จ"
    else:  # ถอน
        current = models.get_member_credit(target_id)
        if current < amount:
            return f"⚠️ ยอดเงินไม่พอ (มี {current:,.2f} บาท)"
        models.adjust_credit(target_id, -amount)
        models.log_txn(target_id, "ถอน", amount, f"โดยแอดมิน {admin_uid[:6]}")
        msg = f"✅ ถอนเงินจาก {target} จำนวน {amount:,.2f} สำเร็จ"
    
    new_credit = models.get_member_credit(target_id)
    return f"{msg}\n💰 ยอดคงเหลือปัจจุบัน: {new_credit:,.2f} บาท"

# ---------- กระจายงานข้อความ ----------

def handle_message(user_id: str, display_name: str, text: str, room_id: str = None) -> list:
    """คืนรายการข้อความ LINE {type,text} ที่ต้องตอบกลับ"""
    text = text.strip()
    if not text:
        return []

    admin = is_admin(user_id)
    mid = ensure_member(user_id, display_name)
    room_type = models.get_room_type(room_id) if room_id else "private"

    # ===== 0. คำสั่งตั้งค่าห้อง (เฉพาะแอดมิน) =====
    if admin and room_id:
        if text == "ตั้งห้องเล่น":
            models.set_room_type(room_id, "play", "ห้องสำหรับสมาชิกแทง")
            return [line_api.text_message("✅ ตั้งค่าเป็น [ห้องเล่น] เรียบร้อย\n(รับเฉพาะคำสั่งแทง/ดูราคา)")]
        if text == "ตั้งห้องฝากถอน":
            models.set_room_type(room_id, "finance", "ห้องสำหรับจัดการเงิน")
            return [line_api.text_message("✅ ตั้งค่าเป็น [ห้องฝากถอน] เรียบร้อย\n(รับเฉพาะคำสั่งฝาก/ถอน/เติม/c/cc)")]
        if text == "ตั้งห้องแอดมิน":
            models.set_room_type(room_id, "admin", "ห้องสำหรับคุมบอร์ด")
            return [line_api.text_message("✅ ตั้งค่าเป็น [ห้องแอดมิน] เรียบร้อย\n(คุมบอร์ด/ประกาศผล/สรุปยอด/ทวน)")]

    # ===== 1. ห้องฝากถอน (Finance Room) / ส่วนตัว (Private) =====
    if room_type in ("finance", "private"):
        # เช็คเครดิต (c)
        if text.lower() == "c":
            m = models.get_member_info(mid)
            code = m["member_code"] if m else "N/A"
            return [line_api.text_message(f"👤 {m['display_name']} ({code})\n💰 เครดิตคงเหลือ: {m['credit']:,.2f} บาท")]
        
        # ดูยอดได้เสียเรียลไทม์ (cc)
        if text.lower() == "cc":
            return _handle_realtime_summary(mid)
            
        # คำสั่งจัดการเงิน (เฉพาะแอดมิน)
        if admin:
            m_adm = ADMIN_CMD.match(text)
            if m_adm:
                kind, target, amount, _ = m_adm.group(1), m_adm.group(2), float(m_adm.group(3).replace(",", "")), m_adm.group(4)
                return [line_api.text_message(admin_credit_cmd(user_id, kind, target, amount))]

    # ===== 2. ห้องเล่น (Play Room) / ส่วนตัว (Private) =====
    if room_type in ("play", "private"):
        # แทงมวย (ด500, ง1000)
        bet = calc.parse_bet(text)
        if bet:
            return _handle_bet(mid, text, bet, admin)
        
        # แอดมินเปิดราคา (คีย์ลัดราคา)
        if admin:
            board = calc.parse_board_from_text(text, require_accept=False)
            if board is not None:
                return _handle_board(admin, mid, text, board)

    # ===== 3. ห้องแอดมิน (Admin Room) / ส่วนตัว (Private) =====
    if admin and room_type in ("admin", "private"):
        # ประกาศผล / ปิดรับ / สรุปรายวัน
        m_res = RESULT_CMD.match(text)
        if m_res:
            cmd_check = m_res.group(1).strip().lower()
            if cmd_check in ("dd", "ff", "sm", "เสมอ", "ล", "p", "เปิด", "ยก", "ยกเลิก", "ยุติ", "แก้ผล", "สรุปรายวัน"):
                return _handle_result_cmd(text, m_res)
        
        # ทวนผลรายคู่: ทวน[เลขคู่] [ชื่อ/รหัส]
        m_review = re.match(r"^(?:ทวน|ดูคู่)\s*(\d+)(?:\s+(.*))?$", text, re.I)
        if m_review:
            match_no = int(m_review.group(1))
            target_name = m_review.group(2)
            return _handle_review_bets(mid, match_no, target_name, admin)

        # ยอดเจ้ามือเรียลไทม์
        if text.strip() in ("ยอดเจ้ามือ", "เจ้ามือ"):
            return _handle_house_summary()

        # ป้ายรับ
        m_accept = re.match(r"^(?:แก้)?ป้าย\s*([\d,]+)$", text)
        if m_accept:
            return _handle_set_limit(text, m_accept)

    # 4. คีย์ลัดทั่วไป (ถ้าไม่ตรงคำสั่งด้านบน และเป็นคีย์ที่ตั้งไว้)
    kw_messages = _keyword_reply(text, mid)
    if kw_messages:
        return kw_messages

    # ถ้าไม่ตรงคีย์ใดๆ เลย ให้เงียบ (Strict Matching)
    return []

def _handle_realtime_summary(mid: int) -> list:
    """แสดงยอดได้เสียเรียลไทม์ (cc)"""
    rows = models.list_bets(member_id=mid)[:10]
    if not rows:
        return [line_api.text_message("ยังไม่มีรายการแทงในขณะนี้")]
    
    out = ["📋 ยอดได้เสียเรียลไทม์:"]
    for r in rows:
        status = "รอนับผล" if r['status'] == 'ติด' else r['status']
        out.append(f"- {r['side']} {r['amount']:,.0f} (ติด {r['actual']:,.0f}) [{status}]")
    
    m = models.get_member_info(mid)
    out.append(f"\n💰 เครดิตคงเหลือ: {m['credit']:,.2f} บาท")
    return [line_api.text_message("\n".join(out))]

def _handle_house_summary() -> list:
    """แสดงยอดรวมเจ้ามือ (Admin Only)"""
    match_id = get_open_match()
    if not match_id:
        conn = models.get_conn()
        last = conn.execute("SELECT id, name FROM matches ORDER BY id DESC LIMIT 1").fetchone()
        if last: match_id = last["id"]
        else: return [line_api.text_message("⚠️ ยังไม่มีคู่ในระบบ")]
    
    bets = models.list_bets(match_id=match_id)
    total_red = sum(b["actual"] for b in bets if b["side"] == "แดง" and b["status"] == "ติด")
    total_blue = sum(b["actual"] for b in bets if b["side"] == "น้ำเงิน" and b["status"] == "ติด")
    
    conn = models.get_conn()
    board = conn.execute("SELECT * FROM price_boards WHERE match_id=? ORDER BY id DESC LIMIT 1", (match_id,)).fetchone()
    risk_text = ""
    if board:
        # คำนวณความเสี่ยงเบื้องต้น
        p_red = calc.Price("แดง", True, board["red_pay"], board["red_win"])
        p_blue = calc.Price("น้ำเงิน", False, board["blue_pay"], board["blue_win"])
        payout_red = sum(p_red.payout(b["actual"]) for b in bets if b["side"] == "แดง" and b["status"] == "ติด")
        payout_blue = sum(p_blue.payout(b["actual"]) for b in bets if b["side"] == "น้ำเงิน" and b["status"] == "ติด")
        risk_text = f"\n📈 ถ้าแดงชนะ: {total_blue - payout_red:,.2f}\n📈 ถ้าน้ำเงินชนะ: {total_red - payout_blue:,.2f}"

    return [line_api.text_message(f"📊 ยอดรวมเจ้ามือ (คู่ {match_id}):\n🔴 แดง: {total_red:,.2f}\n🔵 น้ำเงิน: {total_blue:,.2f}\n💰 ยอดรวม: {total_red + total_blue:,.2f}{risk_text}")]

def _handle_set_limit(text: str, m) -> list:
    match_id = get_open_match()
    if not match_id:
        return [line_api.text_message("⚠️ ยังไม่มีคู่เปิดอยู่")]
    new_accept = float(m.group(1).replace(",", ""))
    conn = models.get_conn()
    conn.execute("UPDATE price_boards SET accept_amt=? WHERE match_id=? ORDER BY id DESC LIMIT 1", (match_id,))
    conn.commit()
    return [line_api.text_message(f"✅ ตั้งวงเงินรับเป็น {new_accept:,.2f} สำเร็จ")]

def _handle_review_bets(mid: int, match_no: int, target: str, admin: bool) -> list:
    """ดึงประวัติการแทงย้อนหลัง (ทวน)"""
    if admin and target:
        target_id = models.find_member(target)
        if not target_id: return [line_api.text_message(f"❌ ไม่พบสมาชิก {target}")]
        member_id = target_id
    else:
        member_id = mid
        
    history = models.get_member_match_history(member_id, match_no)
    if not history:
        return [line_api.text_message(f"⚠️ ไม่พบประวัติการแทงคู่ที่ {match_no}")]
    
    m_info = models.get_member_info(member_id)
    out = [f"📝 ประวัติการแทงคู่ที่ {match_no}\n👤 {m_info['display_name']} ({m_info['member_code']})"]
    for h in history:
        res = f" [{h['settle_result']}]" if h['settle_result'] else ""
        out.append(f"- {h['side']} {h['amount']:,.0f} (ติด {h['actual']:,.0f}){res}")
    
    return [line_api.text_message("\n".join(out))]

def _handle_bet(mid: int, text: str, bet: tuple, admin: bool) -> list:
    side, amount = bet
    match_id = get_open_match()
    if not match_id:
        return [line_api.text_message("⚠️ ยังไม่มีคู่เปิดรับแทง")]
    
    # ดึงราคาล่าสุด
    conn = models.get_conn()
    board_row = conn.execute("SELECT * FROM price_boards WHERE match_id=? ORDER BY id DESC LIMIT 1", (match_id,)).fetchone()
    if not board_row:
        return [line_api.text_message("⚠️ ยังไม่มีการตั้งราคา")]
    
    # เช็คป้ายรับ
    current_bets = models.list_bets(match_id=match_id)
    total_side = sum(b["actual"] for b in current_bets if b["side"] == side and b["status"] == "ติด")
    limit = board_row["accept_amt"]
    
    if limit > 0 and total_side >= limit:
        return [line_api.text_message("ไม่ติด")]
    
    # เช็คทุนลูกค้า
    credit = models.get_member_credit(mid)
    if credit <= 0:
        return [line_api.text_message("ไม่ติด")]
    
    # คำนวณยอดที่ติดจริง
    actual = amount
    full_cap = False
    
    # ติดตามป้าย
    if limit > 0 and (total_side + amount) > limit:
        actual = limit - total_side
        full_cap = True
        
    # ติดตามทุน
    if actual > credit:
        actual = credit
        full_cap = False # ใช้คำว่า ติดเต็มจำนวนทุน แทน
        
    if actual <= 0:
        return [line_api.text_message("ไม่ติด")]
        
    # บันทึกบิล
    models.add_bet(mid, match_id, board_row["id"], side, amount, actual)
    models.adjust_credit(mid, -actual)
    
    # แจ้งเตือนตามกติกา
    if actual < amount:
        if credit < amount and actual == credit:
            msg = f"ติดเต็มจำนวน {actual:,.0f}"
        else:
            msg = f"ติด {actual:,.0f}"
    else:
        msg = f"ติด {actual:,.0f}"
        
    return [line_api.text_message(msg)]

def _handle_board(admin: bool, mid: int, text: str, board: calc.PriceBoard) -> list:
    mid_match = get_open_match()
    if not mid_match:
        mid_match = models.new_match(f"คู่ที่ {len(models.list_matches())+1}")
    bid = models.add_board(mid_match, text, board.mode, board.red.pay_num if board.red else 0, 
                           board.red.win_num if board.red else 0, board.blue.pay_num if board.blue else 0,
                           board.blue.win_num if board.blue else 0, board.accept)
    
    return [line_api.text_message(f"✅ เปิดราคาใหม่: {text}")]

def _handle_result_cmd(text: str, m) -> list:
    cmd = m.group(1).strip().lower()
    
    if cmd == "สรุปรายวัน":
        summary = models.get_daily_summary()
        msg = (f"📅 สรุปยอดรายวัน ({summary['date']})\n"
               f"🥊 แข่งขันทั้งหมด: {summary['match_count']} คู่\n"
               f"💰 ยอดแทงรวม: {summary['total_bet']:,.2f}\n"
               f"🏦 กำไรเจ้ามือ: {summary['house_profit']:,.2f}\n"
               f"📥 ยอดฝาก/เติม: {summary['deposits']:,.2f}\n"
               f"📤 ยอดถอน: {summary['withdrawals']:,.2f}")
        return [line_api.text_message(msg)]

    match_id = get_open_match()
    if not match_id:
        return [line_api.text_message("⚠️ ยังไม่มีคู่ที่เปิดอยู่")]
        
    if cmd in ("ล", "p"):
        models.get_conn().execute("UPDATE matches SET status='closed' WHERE id=?", (match_id,))
        models.get_conn().commit()
        return [line_api.text_message("🔒 ปิดรับแทงคู่นี้แล้ว")]
        
    if cmd == "เปิด":
        models.get_conn().execute("UPDATE matches SET status='open' WHERE id=?", (match_id,))
        models.get_conn().commit()
        return [line_api.text_message("🔓 เปิดรับแทงต่อแล้ว")]

    if cmd in ("ยก", "ยกเลิก", "ยุติ"):
        models.set_result(match_id, "ยกเลิก")
        models.settle_match(match_id)
        models.close_match(match_id)
        return [line_api.text_message("🚫 ยกเลิกคู่นี้แล้ว (คืนยอดทุกบิล)")]

    if cmd in ("dd", "ff", "sm", "เสมอ"):
        winner = "แดง" if cmd == "dd" else ("น้ำเงิน" if cmd == "ff" else "เสมอ")
        models.set_result(match_id, winner)
        models.settle_match(match_id)
        models.close_match(match_id)
        return [line_api.text_message(f"✅ สรุปผลคู่ที่ {match_id}: {winner} ชนะ")]

    return []

def _keyword_reply(text: str, mid: int) -> list:
    res = models.find_keyword(text)
    if res:
        return [line_api.text_message(res)]
    return []
