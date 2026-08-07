# -*- coding: utf-8 -*-
"""
app/bot.py — core ของ LINE bot: ประมวลผลข้อความจากสมาชิก/แอดมิน

ความหมายการตอบ:
- แอดมินส่งคีย์ลัดราคา ("กลางอากาศ ⏎ 🔴ด52 ง53 ⏎ รับ4000") → เปิดราคา/เปิดคู่ + ส่งคำตอบตาม template
- สมาชิกพิมพ์ "ด500"/"แดง 500" → แทง โดยตรวจทุน + ป้ายรับ → ตอบ "ติด"/"ติดเต็มจำนวน"/"ไม่ติด"
- สมาชิกพิมพ์ "c" → เครดิตคงเหลือ, "cc" → ย้อนดูบิลล่าสุด + ผลล่วงหน้า
- แอดมิน: "จ" = เปิดราคาใหม่ (แดงชนะ), "จง" = น้ำเงินชนะ, "เสมอ", "ยุติ"/"ยก" = ยกเลิกคู่,
  "ฝาก{uid} {จำนวน}", "ถอน{uid} {จำนวน}", "เติม{uid} {จำนวน}"
"""

import os
import re
import json
import asyncio
import logging
import time
from dataclasses import dataclass

from app import calc, models, line_api

# ระบบป้องกันการส่งยอดซ้ำ (Duplicate Protection)
# key: member_id, value: (side, amount, timestamp)
_LAST_BETS_CACHE = {}

logger = logging.getLogger("muaythai_bot")

# --- ค่าเริ่มต้นจาก env / config.yaml ---
try:
    import yaml
    _cfg_path = os.environ.get("MUAYTHAI_CONFIG", os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config.yaml"))
    if os.path.exists(_cfg_path):
        _CFG = yaml.safe_load(open(_cfg_path)) or {}
    else:
        _CFG = {}
except ImportError:
    _CFG = {}

ADMIN_IDS = [a.strip() for a in os.environ.get("LINE_ADMIN_IDS", "").split(",") if a.strip()] \
    or _CFG.get("admin_ids", [])
BANK_ACCOUNT = os.environ.get("MUAYTHAI_BANK", _CFG.get("bank_account", "959-243-7898"))
BANK_NAME = os.environ.get("MUAYTHAI_BANK_NAME", _CFG.get("bank_name", "ไทยพานิชย์"))
MAX_CAP = float(os.environ.get("MUAYTHAI_MAX_CAP", _CFG.get("max_cap", 0)))  # 0 = ไม่มีเพดาน
MEDIA_URLS = _CFG.get("media_urls", {})   # key: {originalContentUrl, previewImageUrl}


def is_admin(user_id: str) -> bool:
    if user_id in ADMIN_IDS:
        return True
    m = models.find_member(user_id)
    return bool(m and models.get_conn().execute(
        "SELECT is_admin FROM members WHERE id=?", (m,)).fetchone()["is_admin"])


def ensure_member(user_id: str, display_name: str = "") -> int:
    mid = models.find_member(user_id)
    if not mid:
        mid = models.new_member(user_id, display_name)
    return mid


def get_open_match() -> int:
    rows = models.list_matches("open")
    return rows[0]["id"] if rows else 0


def _credit_text(mid: int) -> str:
    return f"เครดิตคงเหลือ: {models.get_member_credit(mid):,.2f} บาท"


# ---------- คำสั่งแอดมิน ----------
ADMIN_CMD = re.compile(
    r"^(ฝาก|ถอน|เติม)\s*([Uu][0-9A-Za-z]{8,40})?\s*([\d,]+)(?:\s+(.*))?$")
RESULT_CMD = re.compile(r"^(dd|ff|sm|เสมอ|ล|p|เปิด|ยก(?:เลิก)?|ยุติ|x{1,3}|จ[งงง]|จง|แก้ผล|สรุปรายวัน)\s*(.*)$", re.I)


def admin_credit_cmd(user_id: str, kind: str, target_id: str, amount: float) -> str:
    if kind == "เติม":
        mid = models.find_member(target_id)
        if not mid:
            mid = models.new_member(target_id)
        models.adjust_credit(mid, amount)
        models.add_txn(mid, "เติม", amount)
        return f"✅ เติม {amount:,.2f} ให้ {target_id[:8]} สำเร็จ"
    if kind == "ฝาก":
        mid = models.find_member(target_id)
        if not mid:
            mid = models.new_member(target_id)
        models.adjust_credit(mid, amount)
        models.add_txn(mid, "ฝาก", amount)
        return f"✅ บันทึกฝากรับ {amount:,.2f} ของ {target_id[:8]}"
    if kind == "ถอน":
        mid = models.find_member(target_id)
        if not mid:
            return "❌ ไม่พบสมาชิกนี้"
        if models.get_member_credit(mid) < amount:
            return "⚠️ เครดิตไม่พอถอน"
        models.adjust_credit(mid, -amount)
        models.add_txn(mid, "ถอน", amount)
        return f"✅ อนุมัติถอน {amount:,.2f} ของ {target_id[:8]}"
    return ""


# ---------- กระจายงานข้อความ ----------

def handle_message(user_id: str, display_name: str, text: str) -> list:
    """คืนรายการข้อความ LINE {type,text} ที่ต้องตอบกลับ"""
    text = text.strip()
    if not text:
        return [line_api.text_message("ส่งราคาได้เลยครับ (เช่น กลางอากาศ ⏎ 🔴ด52 ง53 ⏎ รับ4000)")]

    admin = is_admin(user_id)
    mid = ensure_member(user_id, display_name)

    # ===== แอดมิน: คำสั่งจัดการระบบ (เช็คก่อนเพื่อไม่ให้คีย์ลัดทั่วไปแย่งตอบ) =====
    if admin:
        # 1. เช็คคำสั่งประกาศผล/ปิดรับ/สรุปยอด (dd, ff, sm, ล, p, เปิด, สรุปรายวัน)
        m_res = RESULT_CMD.match(text)
        if m_res:
            cmd_check = m_res.group(1).strip().lower()
            # คำสั่งเหล่านี้ให้ทำงานทันที
            if cmd_check in ("dd", "ff", "sm", "เสมอ", "ล", "p", "เปิด", "ยก", "ยกเลิก", "ยุติ", "แก้ผล", "สรุปรายวัน") or len(cmd_check) > 1:
                return _handle_result_cmd(text, m_res)

        # 2. เช็คคำสั่งฝาก/ถอน/เติม
        m_adm = ADMIN_CMD.match(text)
        if m_adm:
            kind, target, amount, _ = m_adm.group(1), m_adm.group(2), float(m_adm.group(3).replace(",", "")), m_adm.group(4)
            return [line_api.text_message(admin_credit_cmd(user_id, kind, target, amount))]

    # ===== แอดมิน: คีย์ลัดราคามวย (สมาชิกต้องมีป้ายรับ/ข้อความกลางอากาศจึงจะเป็นราคา) =====
    board = calc.parse_board_from_text(text, require_accept=not admin)
    if board is not None:
        return _handle_board(admin, mid, text, board)

    # ===== แอดมิน: คำสั่งจัดการอื่นๆ =====
    if admin:
        # ยอดเจ้ามือ
        if text.strip() in ("ยอดเจ้ามือ", "เจ้ามือ"):
            match_id = get_open_match()
            if not match_id:
                # ถ้าไม่มีคู่เปิด ลองหาคู่ล่าสุดที่จบไป
                conn = models.get_conn()
                last = conn.execute("SELECT id, name FROM matches ORDER BY id DESC LIMIT 1").fetchone()
                if last: match_id = last["id"]
                else: return [line_api.text_message("⚠️ ยังไม่มีคู่ในระบบ")]
            
            bets = models.list_bets(match_id=match_id)
            total_red = sum(b["actual"] for b in bets if b["side"] == "แดง" and b["status"] == "ติด")
            total_blue = sum(b["actual"] for b in bets if b["side"] == "น้ำเงิน" and b["status"] == "ติด")
            
            # คำนวณความเสี่ยง (เสียสูงสุด)
            # ดึงราคาล่าสุด
            conn = models.get_conn()
            board = conn.execute("SELECT * FROM price_boards WHERE match_id=? ORDER BY id DESC LIMIT 1", (match_id,)).fetchone()
            risk_text = ""
            if board:
                # คำนวณกำไรถ้าแดงชนะ (ได้จากน้ำเงิน - จ่ายแดง)
                payout_red = sum(calc.Price("แดง", True, board["red_pay"], board["red_win"]).payout(b["actual"]) for b in bets if b["side"] == "แดง" and b["status"] == "ติด")
                # คำนวณกำไรถ้าน้ำเงินชนะ (ได้จากแดง - จ่ายน้ำเงิน)
                payout_blue = sum(calc.Price("น้ำเงิน", False, board["blue_pay"], board["blue_win"]).payout(b["actual"]) for b in bets if b["side"] == "น้ำเงิน" and b["status"] == "ติด")
                risk_text = f"\n📈 ถ้าแดงชนะ: {total_blue - payout_red:,.2f}\n📈 ถ้าน้ำเงินชนะ: {total_red - payout_blue:,.2f}"

            return [line_api.text_message(f"📊 ยอดรวมเจ้ามือ:\n🔴 แดง: {total_red:,.2f}\n🔵 น้ำเงิน: {total_blue:,.2f}\n💰 ยอดรวม: {total_red + total_blue:,.2f}{risk_text}")]

        # ป้าย [จำนวน] หรือ แก้ป้าย [จำนวน]
        m_accept = re.match(r"^(?:แก้)?ป้าย\s*([\d,]+)$", text)
        if m_accept:
            match_id = get_open_match()
            if not match_id:
                return [line_api.text_message("⚠️ ยังไม่มีคู่เปิดอยู่")]
            new_accept = float(m_accept.group(1).replace(",", ""))
            conn = models.get_conn()
            # อัปเดตบอร์ดล่าสุด
            conn.execute("UPDATE price_boards SET accept_amt=? WHERE match_id=? ORDER BY id DESC LIMIT 1", (new_accept, match_id))
            conn.commit()
            return [line_api.text_message(f"✅ ตั้งวงเงินรับเป็น {new_accept:,.2f} สำเร็จ")]

        # แก้ยอด [ชื่อ] [จำนวน] หรือ แก้ยอด [uid] [จำนวน]
        m_edit_credit = re.match(r"^แก้ยอด\s+(\S+)\s+([\d,]+)$", text)
        if m_edit_credit:
            target, amount = m_edit_credit.group(1), float(m_edit_credit.group(2).replace(",", ""))
            mid = models.find_member(target)
            if not mid:
                conn = models.get_conn()
                row = conn.execute("SELECT id FROM members WHERE display_name LIKE ?", (f"%{target}%",)).fetchone()
                if row:
                    mid = row["id"]
            if not mid:
                return [line_api.text_message(f"⚠️ ไม่พบสมาชิก {target}")]
            conn = models.get_conn()
            conn.execute("UPDATE members SET credit=? WHERE id=?", (amount, mid))
            conn.commit()
            return [line_api.text_message(f"✅ ปรับยอดเครดิตสมาชิก ID {mid} เป็น {amount:,.2f} สำเร็จ")]

    # ===== สมาชิก: แทงมวย =====
    bet = calc.parse_bet(text)
    if bet:
        return _handle_bet(mid, text, bet, admin)
    
    # ตรวจสอบว่าพิมพ์ผิดรูปแบบหรือไม่ (เช่น ลืมใส่ยอด หรือ ลืมใส่มุม)
    bet_err = calc.parse_bet_error(text)
    if bet_err:
        return [line_api.text_message(bet_err)]

    # ===== สมาชิก: เครดิต / เมนูฝากถอน =====
    if text.lower() in ("c", "ช", "เครดิต", "ฝากถอน", "กระเป๋า"):
        return [line_api.flex_message("เมนูฝากถอน", line_api.make_wallet_menu_flex())]
    if text.lower() in ("cc", "ชช"):
        return _handle_last_bets(mid)
    
    # ===== ดึงผลการแทงย้อนหลัง (ทวน / ดูคู่) =====
    # รูปแบบ: "ทวน1", "ดูคู่1", "ดูคู่1 @ชื่อ"
    m_review = re.match(r"^(?:ทวน|ดูคู่)\s*(\d+)(?:\s+(.*))?$", text, re.I)
    if m_review:
        match_no = int(m_review.group(1))
        target_name = m_review.group(2)
        return _handle_review_bets(mid, match_no, target_name, admin)

    if text.lower() in ("ยก", "ยกเลิก"):
        bid = models.cancel_last_bet(mid, get_open_match())
        return [line_api.text_message("✅ ยกเลิกบิลล่าสุดแล้ว" if bid else "⚠️ ไม่มีบิลให้ยกเลิก")]

    # ===== คีย์ลัดทั่วไป (จากคลัง 206 รายการ) =====
    kw_messages = _keyword_reply(text, mid)
    if kw_messages:
        # ถ้าเป็นแอดมินส่งคีย์ลัด ตรวจสอบว่าคำตอบของคีย์ลัดนั้นเป็นราคาต่อรองหรือไม่
        if admin:
            for msg in kw_messages:
                if msg.get("type") == "text":
                    resp_text = msg.get("text", "")
                    # ลอง parse ราคาจากคำตอบของคีย์เวิร์ด
                    board = calc.parse_board_from_text(resp_text, require_accept=False)
                    if board:
                        # ถ้าเป็นราคา ให้เปิดบอร์ดในระบบด้วย
                        _handle_board(admin, mid, resp_text, board)
                        logger.info("✅ เปิดราคาอัตโนมัติจากคีย์เวิร์ด: %s", text)
        return kw_messages

    return [line_api.text_message("ไม่เข้าใจข้อความครับ พิมพ์ \"ช่วยเหลือ\" เพื่อดูคีย์ลัด")]


def _handle_board(admin: bool, mid: int, text: str, board: calc.PriceBoard) -> list:
    if not admin:
        return [line_api.text_message("⚠️ นี่คือราคา แอดมินเท่านั้นที่เปิดได้")]
    mid_match = get_open_match()
    if not mid_match:
        mid_match = models.new_match(f"คู่ที่ {len(models.list_matches())+1}")
    bid = models.add_board(mid_match, text, board.mode, board.red, board.blue,
                           board.accept, board.is_midair, board.note)
    
    red_str = f"ต่อ {board.red.pay_num:,.0f}/{board.red.win_num:,.0f}" if board.red else "-"
    blue_str = f"รอง {board.blue.pay_num:,.0f}/{board.blue.win_num:,.0f}" if board.blue else "-"
    accept_str = f"{board.accept:,.0f} บาท" if board.accept > 0 else "ไม่จำกัด"
    
    flex_content = line_api.make_board_flex(f"คู่ที่ {mid_match}", red_str, blue_str, accept_str, board.mode)
    return [line_api.flex_message("เปิดราคาใหม่", flex_content)]


def _handle_result_cmd(text: str, m) -> list:
    cmd = m.group(1).strip().lower()
    
    # คำสั่งปิด/เปิดรับแทงทันที (ล / p / เปิด)
    if cmd in ("ล", "p"):
        match_id = get_open_match()
        if not match_id:
            return [line_api.text_message("⚠️ ยังไม่มีคู่ที่เปิดอยู่")]
        conn = models.get_conn()
        conn.execute("UPDATE matches SET status='closed' WHERE id=?", (match_id,))
        conn.commit()
        return [line_api.text_message("🔒 ปิดรับแทงคู่นี้แล้ว (ล/p)")]
    if cmd == "เปิด":
        match_id = get_open_match()
        if not match_id:
            return [line_api.text_message("⚠️ ยังไม่มีคู่ในระบบ")]
        conn = models.get_conn()
        conn.execute("UPDATE matches SET status='open' WHERE id=?", (match_id,))
        conn.commit()
        return [line_api.text_message("🔓 เปิดรับแทงต่อแล้ว")]

    match_id = get_open_match()
    if not match_id:
        return [line_api.text_message("⚠️ ยังไม่มีคู่ที่เปิดอยู่")]
        
    match_row = models.get_conn().execute("SELECT name FROM matches WHERE id=?", (match_id,)).fetchone()
    match_name = match_row["name"] if match_row else f"คู่ที่ {match_id}"
        
    if cmd in ("ยก", "ยกเลิก", "ยุติ", "x", "xx", "xxx"):
        models.close_match(match_id)
        models.set_result(match_id, "ยกเลิก")
        return [line_api.text_message("🚫 ยกเลิกคู่นี้แล้ว (คืนยอดทุกบิล)")]

    if cmd == "แก้ผล":
        sub_cmd = m.group(2).strip().lower()
        if not sub_cmd:
            return [line_api.text_message("⚠️ กรุณาระบุผลที่ต้องการแก้ เช่น แก้ผล dd")]
        winner = "แดง" if sub_cmd == "dd" else ("น้ำเงิน" if sub_cmd == "ff" else "เสมอ")
        # ค้นหาคู่ล่าสุดที่จบไปแล้ว (settled)
        conn = models.get_conn()
        last_match = conn.execute("SELECT id, name FROM matches WHERE status='settled' ORDER BY id DESC LIMIT 1").fetchone()
        if not last_match:
            return [line_api.text_message("⚠️ ไม่พบคู่ที่จบไปแล้วให้แก้ไข")]
        match_id = last_match["id"]
        match_name = last_match["name"]
        # ในระบบนี้ settle_match จะทำการคืนเครดิตและคำนวณใหม่ถ้าเรียกซ้ำ (ขึ้นอยู่กับการออกแบบ models.py)
        # แต่เพื่อความปลอดภัย แจ้งว่ากำลังแก้ไข
        models.set_result(match_id, winner)
        models.settle_match(match_id)
        return [line_api.text_message(f"✅ แก้ไขผลคู่ {match_name} เป็น {winner}ชนะ และคำนวณยอดใหม่แล้ว")]
        
    if cmd == "สรุปรายวัน":
        summary = models.get_daily_summary()
        profit_label = "กำไร" if summary["house_profit"] >= 0 else "ขาดทุน"
        msg = (
            f"📅 สรุปยอดเจ้ามือรายวัน ({summary['date']})\n"
            f"--------------------------\n"
            f"🥊 จำนวนคู่ทั้งหมด: {summary['match_count']} คู่\n"
            f"💰 ยอดแทงรวม: {summary['total_bet']:,.2f} บาท\n"
            f"📈 {profit_label}สุทธิ: {abs(summary['house_profit']):,.2f} บาท\n"
            f"--------------------------\n"
            f"📥 ยอดฝาก/เติม: {summary['deposits']:,.2f} บาท\n"
            f"📤 ยอดถอน: {summary['withdrawals']:,.2f} บาท"
        )
        return [line_api.text_message(msg)]

    winner = "แดง" if cmd in ("dd", "จ") else ("น้ำเงิน" if cmd in ("ff", "จง") else "เสมอ")
    
    # รวบรวมเครดิตก่อนเล่น (capital) ของแต่ละคนที่มีการแทง
    conn = models.get_conn()
    bets = conn.execute("SELECT DISTINCT member_id FROM bets WHERE match_id=? AND status='ติด'", (match_id,)).fetchall()
    member_caps = {}
    for b in bets:
        mid = b["member_id"]
        m_info = conn.execute("SELECT credit FROM members WHERE id=?", (mid,)).fetchone()
        member_caps[mid] = float(m_info["credit"]) if m_info else 0.0

    models.set_result(match_id, winner)
    try:
        rows = models.settle_match(match_id)
    except ValueError:
        return [line_api.text_message(f"🏁 ผล: {winner}ชนะ (รอประกาศผลเพื่อคำนวณ)")]
        
    summary_rows = []
    # จัดกลุ่มตามสมาชิก
    member_bets_map = {}
    for r in rows:
        # ดึงข้อมูล bet เพื่อดู member_id
        b_info = conn.execute("SELECT member_id, actual, side FROM bets WHERE id=?", (r["bet_id"],)).fetchone()
        if not b_info:
            continue
        mid = b_info["member_id"]
        if mid not in member_bets_map:
            member_bets_map[mid] = []
        member_bets_map[mid].append(r)
        
    for mid, m_rows in member_bets_map.items():
        m_info = conn.execute("SELECT display_name, line_user_id, credit FROM members WHERE id=?", (mid,)).fetchone()
        name = m_info["display_name"] or f"สมาชิก {mid}"
        balance = float(m_info["credit"])
        
        # ทุนเดิม = เครดิตสุทธิปัจจุบัน - กำไรสุทธิในคู่นี้
        # กำไรสุทธิในคู่นี้ = ผลรวมของ (payout ของบิลที่ชนะ) - ผลรวมของ (actual ของบิลที่แพ้/2)
        m_profit = 0
        for r in m_rows:
            if r["result"] == "ชนะ":
                m_profit += r["payout"]
            elif "แพ้" in r["result"]:
                # ใน models.py เราคืนให้ครึ่งหนึ่ง (actual/2) ดังนั้นกำไรคือ -(actual/2)
                m_profit -= (r["actual"] / 2.0)
        
        capital = balance - m_profit
        
        summary_rows.append({
            "name": name,
            "capital": capital,
            "profit": m_profit,
            "balance": balance
        })

    flex_settle = line_api.make_settle_flex(match_name, winner, summary_rows)
    return [line_api.flex_message("สรุปยอดหลังจบมวย", flex_settle)]


def _handle_bet(mid: int, text: str, bet, admin: bool) -> list:
    side, amount = bet
    
    # ตรวจสอบการส่งยอดซ้ำภายใน 10 วินาที
    now = time.time()
    if mid in _LAST_BETS_CACHE:
        last_side, last_amount, last_ts = _LAST_BETS_CACHE[mid]
        if side == last_side and amount == last_amount and (now - last_ts) < 10:
            return [line_api.text_message("⚠️ คุณเพิ่งส่งยอดนี้ไปเมื่อสักครู่ ระบบป้องกันการส่งซ้ำเพื่อกันมือลั่นครับ")]
    
    # บันทึกลง Cache
    _LAST_BETS_CACHE[mid] = (side, amount, now)

    match_id = get_open_match()
    if not match_id:
        return [line_api.text_message("⚠️ ยังไม่มีคู่ที่เปิดอยู่")]
    conn = models.get_conn()
    board = conn.execute(
        "SELECT * FROM price_boards WHERE match_id=? ORDER BY id DESC LIMIT 1",
        (match_id,)).fetchone()
    if not board:
        return [line_api.text_message("⚠️ ยังไม่มีราคาในคู่นี้")]
    if board["mode"] == "ยกเลิก":
        return [line_api.text_message("🚫 คู่นี้ถูกยกเลิกแล้ว")]
    price_pay = float(board["red_pay" if side == "แดง" else "blue_pay"] or 50)
    price_win = float(board["red_win" if side == "แดง" else "blue_win"] or 1)
    avail = models.open_board_for_side(match_id, side)
    credit = models.get_member_credit(mid)
    actual, ok, reason = calc.check_bet_limit(amount, credit, avail)
    if not ok:
        return [line_api.text_message(reason)]
    if actual <= 0:
        return [line_api.text_message("❌ ไม่ติด")]
    full_cap = abs(actual - avail) < 0.01
    status = "ติด"
    models.adjust_credit(mid, -actual)
    models.add_bet(mid, match_id, board["id"], side, amount, actual, full_cap, status)
    status_label = "✅ ติดเดิมพัน"
    if full_cap:
        detail = f"ติดเต็มจำนวน (ป้ายรับหมด)"
    elif abs(actual - amount) > 0.005:
        detail = f"ติด {actual:,.0f} (จากที่พิมพ์ {amount:,.0f})"
    else:
        detail = f"ติด {actual:,.0f} บาท"
        
    flex_bet = line_api.make_bet_result_flex(status_label, side, actual, models.get_member_credit(mid), detail)
    return [line_api.flex_message("ผลการแทง", flex_bet)]


def _handle_last_bets(mid: int) -> list:
    rows = models.list_bets(member_id=mid)[:10]
    if not rows:
        return [line_api.text_message("ยังไม่มีบิลแทง")]
    
    out = ["📋 รายการแทงล่าสุด (เรียลไทม์):"]
    total_est = 0.0
    for r in rows:
        status_text = r['status']
        if r['status'] == 'ติด':
            # คำนวณยอดได้เสียเบื้องต้น (ถ้ามีผลแล้วจะแสดงผลจริง)
            status_text = "รอนับผล"
        out.append(f"- {r['side']} {r['amount']:,.0f} (ติด {r['actual']:,.0f}) [{status_text}]")
    
    out.append("")
    out.append(_credit_text(mid))
    return [line_api.text_message("\n".join(out))]


def _keyword_reply(text: str, mid: int) -> list:
    """คำตอบจากคลังคัญลัด 206 รายการ (คำตรง/คำย่อ)"""
    conn = models.get_conn()
    norm = re.sub(r"[\s\U0001F000-\U0001FAFF\u2600-\u27BF]", "", text)
    if not norm:
        return []
    # 1) ตรงตัว: title หรือ keyword ทั้งคำ
    row = conn.execute(
        "SELECT * FROM keywords WHERE active=1 AND (title=? OR keywords LIKE ?) LIMIT 1",
        (text.strip(), f"% {norm} %" if " " in norm else norm)).fetchone()
    # 2) keyword แยกคำ: ถ้าคำหนึ่งใน keyword ตรง
    if not row:
        row = conn.execute(
            "SELECT * FROM keywords WHERE active=1 AND (' '||keywords||' ') LIKE ? LIMIT 1",
            (f"% {norm} %",)).fetchone()
    if not row:
        return []
    if text.strip().lower() in ("ช่วยเหลือ", "menu", "เมนู"):
        return [line_api.text_message(_help_text())]
    if row["response_type"] in ("IMAGE", "VIDEO") and row["media_key"]:
        url = MEDIA_URLS.get(row["media_key"])
        if url:
            return [line_api.image_message(url["originalContentUrl"],
                                           url.get("previewImageUrl"))]
        # fallback: ใช้เลขบัญชี/ข้อความแทน
        resp = _fallback_text(row)
        if resp:
            return [line_api.text_message(resp)]
        return [line_api.text_message(f"[{row['title']}] — ต้องตั้งค่า URL สื่อใน config.yaml")]
    resp = (row["response"] or "").replace("{ธนาคาร}", BANK_NAME).replace("{เลขบัญชี}", BANK_ACCOUNT)
    return [line_api.text_message(resp)]


def _fallback_text(row: dict) -> str:
    """เมื่อไม่มี URL สื่อ: คืนข้อความแทนภาพ (เช่น เลขบัญชี)"""
    t = (row["title"] or "").lower()
    if "บช" in t or "บัญชี" in t or "เลข" in t:
        return f"🏦 บัญชี {BANK_NAME}\nเลขบัญชี: {BANK_ACCOUNT}"
    if "สลิป" in t:
        return "💳 โอนแล้วส่งสลิปมาที่แชทนี้เลย"
    if "งช" in t:
        return "🟦 งชิ้นวัด (ต้องตั้งค่า URL สื่อใน config.yaml)"
    if "ดช" in t:
        return "🟥 ดชิ้นวัด (ต้องตั้งค่า URL สื่อใน config.yaml)"
    return ""


def _help_text() -> str:
    return (
        "🥊 เมนูช่วยเหลือ บอทมวยพักยก\n"
        "--------------------------\n"
        "📌 สำหรับสมาชิก:\n"
        "• แทงมวย: พิมพ์ [ด/ง][ยอด] เช่น ด500, ง1000\n"
        "• เช็คเครดิต: พิมพ์ c หรือ เครดิต\n"
        "• ดูบิลล่าสุด: พิมพ์ cc (ดูได้เสียเรียลไทม์)\n"
        "• ทวนบิลรายคู่: พิมพ์ ทวน[เลขคู่] เช่น ทวน1\n"
        "• ยกเลิกบิลล่าสุด: พิมพ์ ยก หรือ ยกเลิก\n"
        "• ข้อมูลบัญชี: พิมพ์ บช หรือ บัญชี\n"
        "\n"
        "👑 สำหรับแอดมิน:\n"
        "• เปิดราคา: พิมพ์คีย์ลัด (ตร, ง54, f23) หรือพิมพ์ราคาตรงๆ\n"
        "• สรุปผล: จ (แดงชนะ), จง (น้ำเงินชนะ), เสมอ, ยก (ยกเลิกคู่)\n"
        "• จัดการเงิน: เติม[uid] [ยอด], ถอน[uid] [ยอด]\n"
        "• ทวนบิลลูกค้า: ทวน[เลขคู่] @ชื่อลูกค้า"
    )

def _handle_review_bets(mid: int, match_no: int, target_name: str = None, is_admin: bool = False) -> list:
    """ดึงประวัติการแทงรายคู่มาทวนให้ดู (เช่น ทวน1)"""
    matches = models.list_matches()
    if not matches:
        return [line_api.text_message("⚠️ ยังไม่มีรายการแข่งขันในระบบ")]
    # หาคู่ที่ match_no (เรียงจากเก่าไปใหม่)
    matches.reverse() 
    if match_no > len(matches) or match_no <= 0:
        return [line_api.text_message(f"⚠️ ไม่พบข้อมูลคู่ที่ {match_no}")]
    
    match = matches[match_no - 1]
    target_mid = mid
    display_label = "ของคุณ"
    
    if is_admin and target_name:
        # แอดมินทวนให้ลูกค้า (ค้นหาจากชื่อแสดงผล)
        conn = models.get_conn()
        row = conn.execute("SELECT id, display_name FROM members WHERE display_name LIKE ?", (f"%{target_name}%",)).fetchone()
        if row:
            target_mid = row["id"]
            display_label = f"ของ {row['display_name']}"
        else:
            return [line_api.text_message(f"⚠️ ไม่พบสมาชิกชื่อ {target_name}")]

    rows = models.list_bets(match_id=match["id"], member_id=target_mid)
    if not rows:
        return [line_api.text_message(f"📋 ไม่พบรายการแทงคู่ที่ {match_no} {display_label}")]
    
    out = [f"📋 ประวัติคู่ที่ {match_no} ({match['name']}) {display_label}:"]
    for r in rows:
        out.append(f"- {r['side']} {r['amount']:,.0f} (ติดจริง {r['actual']:,.0f}) [{r['status']}]")
    
    return [line_api.text_message("\n".join(out))]
