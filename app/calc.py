# -*- coding: utf-8 -*-
import re
from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass
class Price:
    side: str          # "แดง" | "น้ำเงิน"
    is_fav: bool       # ต่อ (True) หรือ รอง (False)
    pay_num: float     # ตัวบน (เช่น 2 ใน 2/1 หรือ 5 ใน 5/3)
    win_num: float     # ตัวล่าง (เช่น 1 ใน 2/1 หรือ 3 ใน 5/3)

    def payout(self, stake: float) -> float:
        """คำนวณกำไร (ไม่รวมต้น) ตามกฎมวยพักยก"""
        n1, n2 = self.pay_num, self.win_num
        if n1 == 0 or n2 == 0: return 0.0
        
        if self.is_fav:
            # ฝ่ายต่อ: แทงมาก ได้น้อย (เช่น ต่อ 2/1 -> แทง 2 ได้ 1, ต่อ 5/4 -> แทง 5 ได้ 4)
            ทุน, กำไร = max(n1, n2), min(n1, n2)
        else:
            # ฝ่ายรอง: แทงน้อย ได้มาก (เช่น รอง 5/3 -> แทง 3 ได้ 5, รอง 2/1 -> แทง 1 ได้ 2)
            ทุน, กำไร = min(n1, n2), max(n1, n2)
            
        return stake * (กำไร / ทุน)

@dataclass
class PriceBoard:
    mode: str               # "ต่อไป" | "รองเงิน" | "เสมอ" | "ยุติ"
    red: Optional[Price]
    blue: Optional[Price]
    accept: float = 0.0

def parse_price_token(tok: str) -> Optional[Price]:
    tok = tok.replace(" ", "").strip()
    if not tok: return None
    
    # แยกฝั่ง
    side = "แดง" if tok[0] in ("ด", "แ") else ("น้ำเงิน" if tok[0] in ("ง", "น") else None)
    if not side: return None
    
    # แยกตัวเลข (รองรับ 2/1, 5/3, 10/9 หรือ 52 ที่หมายถึง 5/2)
    rest = re.sub(r"[^\d/]", "", tok[1:])
    if not rest: return None
    
    if "/" in rest:
        parts = rest.split("/")
        p1, p2 = float(parts[0]), float(parts[1])
    else:
        # เคสตัวเลขติดกัน เช่น 52, 53, 32
        if len(rest) == 2:
            # 2 หลัก → ตัวบน 1 หลัก / ตัวล่าง 1 หลัก (52 = 5/2)
            p1, p2 = float(rest[0]), float(rest[1])
        elif len(rest) == 3:
            # 3 หลัก → ตัวบน 2 หลัก / ตัวล่าง 1 หลัก (ด121 = 12/1, ด132 = 13/2)
            p1, p2 = float(rest[:-1]), float(rest[-1])
        else:
            # 4 หลักขึ้นไป → ตัวบน 3 หลัก / ตัวล่าง 1 หลัก (ด1212 = 121/2)
            p1, p2 = float(rest[:-1]), float(rest[-1])
            
    # is_fav จะถูกกำหนดใหม่ใน parse_board_from_text
    return Price(side=side, is_fav=True, pay_num=p1, win_num=p2)

def parse_board_from_text(text: str, require_accept: bool = False) -> Optional[PriceBoard]:
    # ป้องกันการตรวจจับผิดพลาดจากข้อความอธิบายยาวๆ
    if len(text) > 200:
        return None

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    full = " ".join(lines)
    
    # ตรวจสอบโหมดพิเศษ (ต้องเป็นข้อความสั้นๆ)
    if len(full) < 20:
        if "เสมอ" in full: return PriceBoard("เสมอ", None, None)
        if any(x in full for x in ("ยกเลิก", "ยุติ", "ยก")): return PriceBoard("ยุติ", None, None)
    
    # ค้นหาป้ายรับ
    accept = 0.0
    m_acc = re.search(r"รับ\s*([\d,]+)", full)
    if m_acc:
        accept = float(m_acc.group(1).replace(",", ""))
    
    if require_accept and accept <= 0: return None

    # ค้นหาราคา ด... ง...
    prices = []
    # ปรับ regex ให้แม่นยำขึ้น ป้องกันการจับคำทั่วไป
    tokens = re.findall(r"[ดแงน]\d+/?\d*", full.replace(" ", ""))
    for t in tokens:
        p = parse_price_token(t)
        if p: prices.append(p)
    
    # ถ้าไม่มีราคาเลย หรือมีแค่ราคาเดียวแต่ไม่มีป้ายรับ ให้ถือว่าไม่ใช่การเปิดราคา
    if not prices or (len(prices) < 2 and accept <= 0 and "ต่อ" not in full and "รอง" not in full):
        return None
    
    # ตัดสินว่าใครต่อใครรอง:
    # ฝั่งต่อ (fav) คือฝั่งที่ "แทงมากได้น้อย" → ตัวบน > ตัวล่าง (เช่น ต่อ 2/1, 5/4, 10/9)
    # ฝั่งรอง (under) คือฝั่งที่ "แทงน้อยได้มาก" → ตัวบน <= ตัวล่าง (เช่น รอง 5/3, 3/1)
    # หมายเหตุ: ใช้ pay_num > win_num ไม่ใช่เปรียบเทียบตั้วเลขระหว่างสองฝั่ง
    # เพราะฝั่งรองอาจมีตัวบนเล็กกว่าฝั่งต่อได้ (เช่น ด52 vs ง31: แดงต่อ 5/2, เงินรอง 3/1)
    red = blue = None
    for p in prices:
        if p.side == "แดง": red = p
        else: blue = p
    # ฝั่งต่อ (fav) = ฝั่งที่ต้องจ่ายน้อยกว่า → เปรียบ pay_num สองฝั่ง
    # ฝั่งรอง (under) = ฝั่งที่จ่ายมากกว่า
    if red is not None and blue is not None and red.pay_num != blue.pay_num:
        red.is_fav = red.pay_num < blue.pay_num
        blue.is_fav = blue.pay_num < red.pay_num
    else:
        # ราคาเท่ากันหรือไม่มีครบสองฝั่ง ใช้กฏตัวบน/ตัวล่างแทน
        for p in prices:
            p.is_fav = p.pay_num > p.win_num
    return PriceBoard("ต่อไป", red, blue, accept)

def parse_bet(text: str) -> Optional[Tuple[str, float]]:
    """ด500 -> ('แดง', 500.0)"""
    m = re.match(r"^([ดแงน])\s*([\d,]+)$", text.replace(" ", ""), re.I)
    if not m: return None
    side = "แดง" if m.group(1) in ("ด", "แ") else "น้ำเงิน"
    amount = float(m.group(2).replace(",", ""))
    return side, amount
