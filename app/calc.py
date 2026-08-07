# -*- coding: utf-8 -*-
"""
app/calc.py — หลักคำนวณราคาต่อรองมวยพักยก

กติกาหลัก (จากข้อมูลจริงของ LINE OA @344ylhcy):
- "ด52" / "ด 52"  = มุมแดงเป็นฝ่ายต่อ ราคา 52/1 (แทงแดง 52 บาท ได้ 1)
- "ง 31"          = มุมน้ำเงินเป็นฝ่ายรอง ราคา 31/1 (แทงน้ำเงิน 31 บาท ได้ 1)
- "ด10/1", "ง10/1"= เขียนแบบมี /1 ชัดเจน (ต่อ/รอง 10)
- "ด 10/9"        = ราคาไหลแบบเศษส่วน: แทง 9 ได้ 10 (ต่อ)
- "รง80"          = รองเงิน 80/1 เฉพาะมุมน้ำเงิน (⚠️ ไอคอนอาจเป็นแดง แต่ความหมายคือรองเงิน)
- "รด80"          = รองเงิน 80/1 เฉพาะมุมแดง
- "1010"          = เสมอไม่ได้ไม่เสีย (เสมอแดง/เสมอน้ำเงิน)
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

# ---------- โครงข้อมูลราคา ----------

@dataclass
class Price:
    """ราคาของหนึ่งฝ่าย เช่น ด 52 (ต่อ 52/1) หรือ ด 10/9 (ต่อ 10/9)"""
    side: str          # "แดง" | "น้ำเงิน"
    is_fav: bool       # ต่อ (True) หรือ รอง (True? —รองคือ False)
    pay_num: float     # ตัวบน: จำนวนที่ต้องแทง (หรือจำนวนที่ได้ในเศษส่วน)
    win_num: float     # ตัวล่าง: จำนวนที่ได้หากชนะ (1 ในกรณีปกติ)

    def payout(self, stake: float) -> float:
        """เงินชนะที่ได้รับเมื่อแทง `stake` (ไม่รวมต้น)
        - ด 52: แทง 52 ได้ 1 → แทง x ได้ x/52
        - ง 10/9: แทง 9 ได้ 10 → แทง x ได้ x*10/9
        """
        if self.pay_num == 0:
            raise ValueError("ราคา pay_num ไม่สามารถเป็น 0 ได้")
        return stake * self.win_num / self.pay_num

    def __repr__(self):
        kind = "ต่อ" if self.is_fav else "รอง"
        num = f"{int(self.pay_num)}" if self.pay_num == int(self.pay_num) else self.pay_num
        w = f"{int(self.win_num)}" if self.win_num == int(self.win_num) else self.win_num
        if w == "1":
            return f"[{self.side} {kind} {num}]"
        return f"[{self.side} {kind} {num}/{w}]"


@dataclass
class PriceBoard:
    """กระดานราคาในขณะนั้นของหนึ่งช่วงเวลา (หนึ่งราคา)"""
    mode: str               # "ต่อไป" | "รองเงิน" | "เสมอ" | "ยุติ"
    red: Optional[Price]    # ราคาฝ่ายแดง (อาจไม่มี)
    blue: Optional[Price]   # ราคาฝ่ายน้ำเงิน (อาจไม่มี)
    accept: float = 0.0     # ยอดรับต่อช่วง (จากคำว่า "รับ4000")
    is_midair: bool = False # กลางอากาศ (ไม่อนุญาตยก/ยุติ)
    note: str = ""          # หมายเหตุ เช่น "ราคากิ้กเดียว"

    def price_for(self, side: str) -> Optional[Price]:
        return self.red if side == "แดง" else self.blue

    def describe(self) -> str:
        parts = [f"โหมด: {self.mode}"]
        if self.red:
            parts.append(f"แดง: {self.red}")
        if self.blue:
            parts.append(f"น้ำเงิน: {self.blue}")
        if self.accept > 0:
            parts.append(f"รับ: {int(self.accept)}")
        if self.is_midair:
            parts.append("(กลางอากาศ — ไม่รับยก/ยุติ)")
        return " | ".join(parts)


# ---------- regex helpers ----------

_RE_NUM = r"(\d+(?:[.,]\d+)?)"

def parse_money(s: str) -> Optional[float]:
    """แปลงข้อความยอดเงินเป็นตัวเลข เช่น '500' → 500, '5,000' → 5000"""
    m = re.match(r"^\s*([\d,]+\.?\d*)\s*$", s or "")
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def parse_price_token(tok: str) -> Optional[Price]:
    """แปลง token เช่น 'ด52', 'ด 52', 'ด10/1', 'ด 10/9', 'ง 31', 'แดง500' เป็น Price"""
    tok = tok.replace("\u00a0", " ").strip()
    # ฝ่าย: ด/แดง = แดง, ง/น้/น้ำเงิน = น้ำเงิน
    if tok.startswith("แดง"):
        side, rest = "แดง", tok[2:]
    elif tok[:1] in ("ด", "แ"):
        side = "แดง"
        rest = tok[1:]
    elif tok.startswith("น้ำเงิน"):
        side, rest = "น้ำเงิน", tok[3:]
    elif tok.startswith("น้"):
        side = "น้ำเงิน"
        rest = tok[2:]
    elif tok[:1] == "ง":
        side, rest = "น้ำเงิน", tok[1:]
    else:
        return None
    # ทิ้งคำที่ไม่ใช่ตัวเลข เช่น งช, งป → ตัดให้เหลือเฉพาะเลข
    rest = re.sub(r"[^\d.,/]+", "", rest)
    if not rest:
        return None
    m = re.match(rf"^{_RE_NUM}(?:\s*[/.]\s*{_RE_NUM})?\s*$", rest)
    if not m:
        return None
    
    val1 = m.group(1).replace(",", "")
    val2 = m.group(2).replace(",", "") if m.group(2) else None
    
    # จัดการคีย์ลัดมวยพักยก (52 -> 5/2, 118 -> 11/8, 32 -> 3/2)
    if not val2:
        if val1 == "52": val1, val2 = "5", "2"
        elif val1 == "53": val1, val2 = "5", "3"
        elif val1 == "54": val1, val2 = "5", "4"
        elif val1 == "118": val1, val2 = "11", "8"
        elif val1 == "32": val1, val2 = "3", "2"
        elif val1 == "74": val1, val2 = "7", "4"
        elif val1 == "21": val1, val2 = "2", "1"
        elif val1 == "31": val1, val2 = "3", "1"
        elif val1 == "41": val1, val2 = "4", "1"
        elif val1 == "51": val1, val2 = "5", "1"
        elif val1 == "61": val1, val2 = "6", "1"
        elif val1 == "71": val1, val2 = "7", "1"
        elif val1 == "81": val1, val2 = "8", "1"
        elif val1 == "109": val1, val2 = "10", "9"
    
    pay = float(val1)
    win = float(val2) if val2 else 1.0
    
    # ในมวยพักยก: ตัวเลขที่มากกว่าคือฝ่ายต่อ (Fav), ตัวเลขที่น้อยกว่าคือฝ่ายรอง (Underdog)
    # ถ้า pay > win แสดงว่าเป็นฝ่ายต่อ (เช่น 2/1 -> pay=2, win=1)
    # ถ้า pay < win แสดงว่าเป็นฝ่ายรอง (เช่น 5/3 แทง 3 ได้ 5 -> pay=3, win=5)
    # แต่ถ้ามาเป็นโทเค็นเดียว เช่น "ด52" (ต่อ 5/2) -> pay=5, win=2 -> is_fav=True
    # ถ้ามาเป็น "ง53" (รอง 5/3) -> pay=3, win=5 -> is_fav=False
    
    is_fav = pay > win
    return Price(side=side, is_fav=is_fav, pay_num=pay, win_num=win)

def parse_board_from_text(text: str, require_accept: bool = False) -> Optional[PriceBoard]:
    """
    แปลงข้อความคีย์ลัดราคาของแอดมินเป็น PriceBoard

    รูปแบบที่รองรับ (สรุปจากข้อมูลจริง 206 รายการ):
    1. "กลางอากาศ ⏎ 🔴ด 52      ง 53 ⏎ รับ4000"      → กลางอากาศ + ราคาทั้ง 2 ฝ่าย
    2. "🔴🔴🔴🔴 ⏎ ง 10/1     ด 61 ⏎ รับ10000"        → ราคาเริ่มต้น (ต่อไป)
    3. "รง120: รองน้ำเงิน ⏎ 🔴ด120/1 ⏎ รับ1000"       → รองเงินฝ่ายน้ำเงิน
    4. "รด120: รองแดง ⏎ 🔵120/1 ⏎ รับ1000"            → รองเงินฝ่ายแดง
    5. "1010: เสมอแดง/เสมอน้ำเงิน"                     → เสมอ
    6. "x/xx/xxx: ยกเลิก"                              → ยุติ
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    full = "\n".join(lines)
    if not full.strip():
        return None

    midair = "กลางอากาศ" in full
    mode = "ต่อไป"
    is_ever = False
    is_cancel = False

    # โหมดรองเงินเฉพาะมุม: "รง80", "รด80" หรือข้อความ "รอง...เดียว"
    only_side = None
    m_only = re.search(r"รอง\s*(แดง|น้ำเงิน)", full)
    if m_only:
        only_side = m_only.group(1)
        mode = "รองเงิน"
    elif re.search(r"เสมอ(แดง|น้ำเงิน)?", full) and len(lines) <= 3 and not re.search(r"[ดง]\s*\d", full):
        is_ever = True
        mode = "เสมอ"
    elif re.search(r"ยกเลิก", full):
        is_cancel = True
        mode = "ยุติ"

    # ค้นหา token ราคา: "ด52", "ด 52", "ด10/1", "ด 10/9", "ง 31", "52" (ในโหมดรองเงิน)
    # ลบ emoji/ไอคอน แล้วรวม "ด"/"ง" กับเลขที่ตามมา (แยกคำด้วยช่องว่าง) ก่อน parse
    prices = []
    clean = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F🆘🚦📢📣❌✅⏎\u274C\u2705]", " ", full)
    clean = clean.replace("⏎", " ")
    clean = re.sub(r"([ดง])\s+(\d)", r"\1\2", clean)
    for tok in clean.split():
        t = tok.strip()
        if not t:
            continue
        # เลขลอยเดี่ยวที่ไม่อยู่หลังด/ง — ข้าม (โหมดรองเงินค้นหาก่อน)
        if re.match(r"^[\d/.,]+$", t):
            # ในโหมดรองเงิน: เลขลอย (รวมเศษส่วน 120/1) = ราคาของ only_side
            if mode == "รองเงิน":
                m_fr = re.match(rf"^{_RE_NUM}(?:\s*[/.]\s*{_RE_NUM})?$", t)
                if m_fr:
                    pay = float(m_fr.group(1).replace(",", ""))
                    win = float(m_fr.group(2).replace(",", "")) if m_fr.group(2) else 1.0
                    prices.append(Price(side=only_side, is_fav=False, pay_num=pay, win_num=win))
            continue
        p = parse_price_token(t)
        if p:
            prices.append(p)
            continue
        # ทิ้งคำที่ไม่ใช่ตัวเลข เช่น งช, งป → ตัดให้เหลือเฉพาะเลขเท่านั้น
    # โหมด "รองเงิน": ราคา x/1 เป็นของมุม only_side เสมอ (ไอคอนในป้ายอาจกลับสีได้)
    red = blue = None
    if mode == "รองเงิน":
        for p in prices:
            p.side = only_side
            p.is_fav = False
            if only_side == "แดง":
                red = p
            else:
                blue = p
    else:
        vals = [p.pay_num for p in prices]
        lo = min(vals) if vals else None
        fav_value = lo  # ฝั่ งต่อ (fav) = เลขน้อยกว่า (แทงยากล้วา = เลขมากกว่าเป็นรอง)
        for p in prices:
            if p.side == "แดง":
                p.is_fav = p.pay_num <= fav_value
                red = p
            else:
                p.is_fav = p.pay_num <= fav_value
                blue = p

    # รับเงิน
    accept = 0.0
    m_acc = re.search(r"รับ\s*([\d,]+)", full)
    if m_acc:
        accept = float(m_acc.group(1).replace(",", ""))

    note = ""
    if "กิ้กเดียว" in full or "ราคาเดียว" in full:
        note = "ราคากิ้กเดียว"

    if is_ever:
        return PriceBoard(mode="เสมอ", red=red, blue=blue, accept=accept, note=note)
    if is_cancel:
        return PriceBoard(mode="ยุติ", red=red, blue=blue, accept=accept, note=note)
    if only_side:
        return PriceBoard(mode="รองเงิน", red=red, blue=blue, accept=accept,
                          is_midair=midair, note=note)
    # ไม่มีราคาเลยในข้อความ → ไม่ใช่กระดานราคา
    if not red and not blue:
        return None
    if require_accept and not accept:
        return None
    return PriceBoard(mode="ต่อไป", red=red, blue=blue, accept=accept,
                      is_midair=midair, note=note)


def parse_bet(text: str) -> Optional[Tuple[str, float]]:
    """
    แปลงข้อความแทงของสมาชิกเป็น (ฝ่าย, ยอดเงิน)

    รองรับรูปแบบจากกติกา กก3:
    - "ด500" / "ด 500" / "แดง500" / "แดง 500"
    - "✅ ,ด500" / "ด/500" / "ด.500" / " ิด500" (มือลั่น)
    - "งด", "ดง", "ด1000ง", "ง500ด" → ไม่ติด (คืน None พร้อม flag ไม่ติด)

    คืน: (side, amount) หรือ None
    """
    t = text.strip()
    # ตรวจกรณีไม่ติด: มีทั้งดและงในข้อความเดียว (เช่น "ง500ด", "ด1000ง", "งด", "ดง")
    joined_red = re.search(r"ด\d", t) or re.search(r"แดง\d", t)
    joined_blue = re.search(r"ง\d", t) or re.search(r"น้\d", t)
    # มุมตามหลังเลข (ง500ด, ด1000ง) หรือคำตามหลัง (ด1000นะ) = ไม่ติด
    if re.search(r"\d+[งด]", t) or re.search(r"(ด|ง)\d+\s*[งด]", t) or re.search(r"(?:ด|ง)\d+(?:นะ|ครับ|ค่ะ)", t):
        return None  # ไม่ติด (ง500ด / ด1000ง / ด1000นะ)
    if joined_red and joined_blue:
        return None  # ไม่ติด (ง500ด / ด1000ง / งด / ดง)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]", "", t)
    t = t.replace("/", " ").replace(".", " ").replace(",", "").strip()

    # แบบรวม: "ด500", "แดง500"
    m = re.search(r"(?:ด|แดง)(\d+)", t)
    if m:
        return "แดง", float(m.group(1))
    m = re.search(r"(?:ง|น้ำเงิน)(\d+)", t)
    if m:
        return "น้ำเงิน", float(m.group(1))
    # แบบแยก: "แดง 500", "ง 500"
    tokens = t.split()
    if len(tokens) >= 2:
        side_tok, amt_tok = tokens[-2], tokens[-1]
        amt = parse_money(amt_tok)
        if amt:
            if side_tok in ("แดง", "ด"):
                return "แดง", amt
            if side_tok in ("ง", "น้ำเงิน", "น้"):
                return "น้ำเงิน", amt
    return None


def check_bet_limit(stake: float, member_credit: float, pool: float) -> Tuple[float, bool, str]:
    """
    ตรวจว่าแทง `stake` ได้ไหม เทียบกับ:
    - member_credit = ทรัพย์สมาชิกคงเหลือ (เช่น 1000)
    - pool = ป้ายรับของฝ่ายนั้น (เช่น 1000)
    คืน (ยอดที่ติดจริง, ติดเต็ม, เหตุผล)

    กติกาจาก req:
    - ทรัพย์ 1000 แทง 1500 → "ติดเต็มจำนวน 1000" แล้วถ้าแทงอีก → "ไม่ติด"
    - ป้ายรับ 1000 แต่ทรัพย์ 700 → "ติด 700" แล้วแทงอีก → "ไม่ติด"
    """
    limit = min(member_credit, pool)
    if stake > limit:
        if limit <= 0:
            return 0.0, False, "ไม่ติด"
        return limit, True, f"ติดเต็มจำนวน {int(limit)}"
    return stake, True, f"ติด {int(stake)}"


def settle(board: PriceBoard, winner: str, bets: list) -> list:
    """
    คำนวณผลได้เสียของแต่ละบิลหลังประกาศผล
    winner: "แดง" | "น้ำเงิน" | "เสมอ"
    bets: list of dict {id, member, side, stake, price: Price}
    คืน list {id, member, side, stake, payout, result, note}
    """
    out = []
    for b in bets:
        if winner == "เสมอ":
            out.append({**b, "payout": 0.0, "result": "เสมอ-คืนต้น", "note": "เสมอไม่ได้ไม่เสีย"})
            continue
        won = b["side"] == winner
        if won:
            payout = b["price"].payout(b["stake"])
            out.append({**b, "payout": round(payout, 2), "result": "ชนะ",
                        "note": f"ได้ {round(payout,2):,.2f} (รวมคืนต้น {round(payout+b['stake'],2):,.2f})"})
        else:
            out.append({**b, "payout": 0.0, "result": "แพ้", "note": f"เสีย {b['stake']:,.2f}"})
    return out

def parse_bet_error(text: str) -> Optional[str]:
    """ตรวจสอบว่าข้อความดูเหมือนจะแทงแต่ผิดรูปแบบหรือไม่"""
    t = text.strip()
    if not t: return None
    
    # ถ้าขึ้นต้นด้วย ด/ง/แดง/น้ำเงิน แต่ไม่มีตัวเลขตามหลังเลย
    if re.match(r"^(ด|ง|แดง|น้ำเงิน)$", t):
        return "⚠️ กรุณาใส่ยอดเงินด้วยครับ เช่น ด500"
    
    # ถ้ามีทั้ง ด และ ง ในข้อความเดียว (และไม่ใช่ป้ายราคาที่มีคำว่า 'รับ')
    if (re.search(r"ด|แดง", t) and re.search(r"ง|น้ำเงิน|น้", t)) and "รับ" not in t:
        return "❌ ไม่ติด: ห้ามพิมพ์ทั้งแดงและน้ำเงินในบิลเดียวครับ"
        
    # ถ้ามีตัวเลขลอยๆ แต่ไม่มีมุม (และไม่ใช่คำสั่งเช็คยอด)
    if re.match(r"^\d+$", t) and t.lower() not in ("c", "cc"):
        return f"⚠️ คุณพิมพ์ '{t}' กรุณาระบุมุมด้วยครับ เช่น ด{t} หรือ ง{t}"
        
    return None
