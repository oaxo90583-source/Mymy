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
from dataclasses import dataclass

from app import calc, models, line_api

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
RESULT_CMD = re.compile(r"^(จ[งงง]|จง|เสมอ|ยก(?:เลิก)?|ยุติ|x{1,3})\s*(.*)$")


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

    # ===== แอดมิน: คีย์ลัดราคามวย (สมาชิกต้องมีป้ายรับ/ข้อความกลางอากาศจึงจะเป็นราคา) =====
    board = calc.parse_board_from_text(text, require_accept=not admin)
    if board is not None:
        return _handle_board(admin, mid, text, board)

    # ===== แอดมิน: คำสั่งผล/ยกเลิก/ฝากถอน =====
    if admin:
        m = ADMIN_CMD.match(text)
        if m:
            kind, target, amount, _ = m.group(1), m.group(2), float(m.group(3).replace(",", "")), m.group(4)
            if kind in ("ฝาก", "เติม"):
                return [line_api.text_message(admin_credit_cmd(user_id, kind, target, amount))]
            return [line_api.text_message(admin_credit_cmd(user_id, kind, target, amount))]
        m = RESULT_CMD.match(text)
        if m:
            return _handle_result_cmd(text, m)

    # ===== สมาชิก: แทงมวย =====
    bet = calc.parse_bet(text)
    if bet:
        return _handle_bet(mid, text, bet, admin)

    # ===== สมาชิก: เครดิต =====
    if text.lower() in ("c", "เครดิต"):
        return [line_api.text_message(_credit_text(mid))]
    if text.lower() == "cc":
        return _handle_last_bets(mid)
    if text.lower() in ("ยก", "ยกเลิก"):
        bid = models.cancel_last_bet(mid, get_open_match())
        return [line_api.text_message("✅ ยกเลิกบิลล่าสุดแล้ว" if bid else "⚠️ ไม่มีบิลให้ยกเลิก")]

    # ===== คีย์ลัดทั่วไป (จากคลัง 206 รายการ) =====
    kw = _keyword_reply(text, mid)
    if kw:
        return kw

    return [line_api.text_message("ไม่เข้าใจข้อความครับ พิมพ์ \"ช่วยเหลือ\" เพื่อดูคีย์ลัด")]


def _handle_board(admin: bool, mid: int, text: str, board: calc.PriceBoard) -> list:
    if not admin:
        return [line_api.text_message("⚠️ นี่คือราคา แอดมินเท่านั้นที่เปิดได้")]
    # เปิดคู่ใหม่ถ้ายังไม่มีคู่ open
    mid_match = get_open_match()
    if not mid_match:
        mid_match = models.new_match(f"คู่ที่ {len(models.list_matches())+1}")
    bid = models.add_board(mid_match, text, board.mode, board.red, board.blue,
                           board.accept, board.is_midair, board.note)
    lines = ["📊 เปิดราคาแล้ว"]
    if board.is_midair:
        lines.append("⚪ กลางอากาศ")
    if board.mode == "รองเงิน":
        side = "แดง" if board.red else "เงิน"
        p = board.red or board.blue
        lines.append(f"🔹 รองเงิน {side} {p.pay_num}/1")
    else:
        if board.red:
            fav = "ต่อ" if board.red.is_fav else "รอง"
            lines.append(f"🔴 แดง{fav} {board.red.pay_num:,.0f}/{board.red.win_num:,.0f}")
        if board.blue:
            fav = "ต่อ" if board.blue.is_fav else "รอง"
            lines.append(f"🔵 เงิน{fav} {board.blue.pay_num:,.0f}/{board.blue.win_num:,.0f}")
        if board.accept:
            lines.append(f"💰 ป้ายรับ {board.accept:,.0f}")
    lines.append(f"เครดิต: {_credit_text(mid)}")
    return [line_api.text_message("\n".join(lines))]


def _handle_result_cmd(text: str, m) -> list:
    match_id = get_open_match()
    if not match_id:
        return [line_api.text_message("⚠️ ยังไม่มีคู่ที่เปิดอยู่")]
    cmd = m.group(1).strip()
    if cmd in ("ยก", "ยกเลิก", "ยุติ", "x", "xx", "xxx"):
        models.close_match(match_id)
        models.set_result(match_id, "ยกเลิก")
        return [line_api.text_message("🚫 ยกเลิกคู่นี้แล้ว (คืนยอดทุกบิล)")]
    winner = "แดง" if cmd == "จ" else ("เงิน" if cmd == "จง" else "เสมอ")
    models.set_result(match_id, winner)
    try:
        rows = models.settle_match(match_id)
    except ValueError:
        return [line_api.text_message(f"🏁 ผล: {winner}ชนะ (รอประกาศผลเพื่อคำนวณ)")]
    total_pay = sum(r["payout"] for r in rows if r["result"] == "ชนะ")
    total_stake = sum(r["actual"] for r in rows)
    return [
        line_api.text_message(
            f"🏁 ผล: {winner}ชนะ\n"
            f"💸 จ่ายรวม {total_pay:,.2f} จากยอดติดรวม {total_stake:,.2f}\n"
            f"จำนวนบิล: {len(rows)}"),
        line_api.text_message("\n".join(
            f"- {r['side']} {r['actual']:,.0f} → {r['note']}" for r in rows))]


def _handle_bet(mid: int, text: str, bet, admin: bool) -> list:
    side, amount = bet
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
    if full_cap:
        note = f"✅ ติดเต็มจำนวน {actual:,.0f} (ป้ายรับหมด)"
    elif abs(actual - amount) > 0.005:
        note = f"✅ ติด {actual:,.0f} จากที่พิมพ์ {amount:,.0f}"
    else:
        note = f"✅ ติด {actual:,.0f}"
    return [line_api.text_message(f"{note}\n{side} {price_pay:,.0f} → ได้ {actual*price_win/price_pay:,.2f}\n"
                                  f"💰 เครดิตเหลือ {models.get_member_credit(mid):,.2f}")]


def _handle_last_bets(mid: int) -> list:
    rows = models.list_bets(member_id=mid)[:5]
    if not rows:
        return [line_api.text_message("ยังไม่มีบิลแทง")]
    out = ["📋 บิลล่าสุด 5 รายการ:"]
    for r in rows:
        out.append(f"- {r['side']} {r['amount']:,.0f} (ติดจริง {r['actual']:,.0f}) [{r['status']}]")
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
        "📖 คีย์ลัด:\n"
        "🔸 สมาชิก: ด500 / ง500 / แดง500 = แทง, c = เครดิต, cc = ดูบิล, ยก = ยกเลิกบิลล่าสุด\n"
        "🔸 ราคา: แอดมินพิมพ์ราคาเหมือนป้าย (เช่น กลางอากาศ ⏎ 🔴ด52 ง53 ⏎ รับ4000)\n"
        "🔸 ผล: จ = แดงชนะ, จง = เงินชนะ, เสมอ, ยก/ยุติ/xxx = ยกเลิกคู่\n"
        "🔸 เงิน: ฝาก{uid} {จำนวน}, ถอน{uid} {จำนวน}, เติม{uid} {จำนวน}\n"
        "🔸 อื่นๆ: บช/บัญชี = เลขบัญชี, สลิป = ส่งสลิปหลังโอน, งช/ดช = ชิ้นวัด"
    )
