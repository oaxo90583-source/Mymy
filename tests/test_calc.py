# -*- coding: utf-8 -*-
"""ทดสอบหลักคำนวณราคาต่อรอง"""
from app.calc import (parse_price_token, parse_board_from_text, parse_bet,
                      check_bet_limit, settle, Price, PriceBoard)


def test_parse_price_token():
    p = parse_price_token("ด52")
    assert p.side == "แดง" and p.is_fav and p.pay_num == 52 and p.win_num == 1
    p = parse_price_token("ง 31")
    assert p.side == "น้ำเงิน" and p.pay_num == 31
    # ระดับ token เดี่ยว: เลข > 1 = ต่อตามค่า, แต่ระดับ board ตัวเลขน้อยจะถือเป็นฝ่ายรอง (ทดสอบใน test_board_midair)
    p = parse_price_token("ด 10/9")
    assert p.side == "แดง" and p.pay_num == 10 and p.win_num == 9
    p = parse_price_token("ง 10/1")
    assert p.side == "น้ำเงิน" and p.pay_num == 10 and p.win_num == 1
    p = parse_price_token("แดง500")
    assert p.side == "แดง" and p.pay_num == 500


def test_board_midair():
    txt = "กลางอากาศ ⏎ 🔴ด 52      ง 53 ⏎ รับ4000"
    b = parse_board_from_text(txt)
    assert b.mode == "ต่อไป"
    assert b.is_midair
    assert b.accept == 4000
    assert b.red.pay_num == 52
    assert b.blue.pay_num == 53


def test_board_start():
    txt = "🔵🔵🔵🔵 ⏎ ง 10/1     ด 61 ⏎ รับ10000"
    b = parse_board_from_text(txt)
    assert b.red.pay_num == 61 and b.blue.pay_num == 10


def test_board_pong_ngern():
    txt = "รองน้ำเงินอย่างเดียว ⏎ 🔴ด120/1 ⏎ รับ1000"
    b = parse_board_from_text(txt)
    assert b.mode == "รองเงิน"
    assert b.blue.pay_num == 120 and b.red is None


def test_board_pong_red():
    txt = "รองแดงอย่างเดียว ⏎ 🔵120/1 ⏎ รับ1000"
    b = parse_board_from_text(txt)
    assert b.mode == "รองเงิน"
    assert b.red.pay_num == 120


def test_board_ever():
    txt = "🔴เสมอแดง ⏎ 🔵เสมอน้ำเงิน ⏎ รับ2000"
    b = parse_board_from_text(txt)
    assert b.mode == "เสมอ"


def test_board_cancel():
    txt = "🔴🔴เปิดผิดราคา🔵🔵 ⏎ \"ยกเลิก\""
    b = parse_board_from_text(txt)
    assert b.mode == "ยุติ"


def test_board_double():
    txt = "🔴🔵กลางอากาศ ⏎ ต่อ ด54    ต่อ ง54 ⏎ รับ4000"
    b = parse_board_from_text(txt)
    assert b.red.pay_num == 54 and b.blue.pay_num == 54


def test_parse_bet():
    assert parse_bet("ด500") == ("แดง", 500.0)
    assert parse_bet("ง300") == ("น้ำเงิน", 300.0)
    assert parse_bet("แดง 500") == ("แดง", 500.0)
    assert parse_bet("✅ ,ด1000") == ("แดง", 1000.0)
    assert parse_bet("ด/500") == ("แดง", 500.0)
    assert parse_bet("ด.500") == ("แดง", 500.0)
    assert parse_bet("ง500ด") is None          # ไม่ติด
    assert parse_bet("ด1000ง") is None         # ไม่ติด
    assert parse_bet("งด") is None             # ไม่ติด


def test_bet_limit():
    stake, full, reason = check_bet_limit(1500, 1000, 1000)
    assert stake == 1000 and full and "1000" in reason
    stake, full, reason = check_bet_limit(500, 1000, 1000)
    assert stake == 500 and not full
    stake, full, reason = check_bet_limit(1000, 700, 1000)
    assert stake == 700 and full
    stake, full, reason = check_bet_limit(500, 0, 1000)
    assert stake == 0 and not full and "ไม่ติด" in reason


def test_payout():
    # ด 52: แทง 52 ได้ 1 → แทง 520 ได้ 10
    p = Price("แดง", True, 52, 1)
    assert abs(p.payout(520) - 10.0) < 1e-9
    # ง 10/1: แทง 100 ได้ 10
    p = Price("น้ำเงิน", False, 10, 1)
    assert abs(p.payout(100) - 10.0) < 1e-9
    # ง 10/9: แทง 900 ได้ 1000 → pay_num=9 (ต้องแทง), win_num=10 (ได้)
    p = Price("น้ำเงิน", False, 9, 10)
    assert abs(p.payout(900) - 1000.0) < 1e-9


def test_settle():
    board = PriceBoard("ต่อไป", red=Price("แดง", True, 52, 1),
                       blue=Price("น้ำเงิน", False, 53, 1))
    bets = [
        {"id": 1, "member": "A", "side": "แดง", "stake": 520,
         "price": Price("แดง", True, 52, 1)},
        {"id": 2, "member": "B", "side": "น้ำเงิน", "stake": 530,
         "price": Price("น้ำเงิน", False, 53, 1)},
    ]
    r = settle(board, "แดง", bets)
    assert r[0]["result"] == "ชนะ" and r[0]["payout"] == 10.0
    assert r[1]["result"] == "แพ้"
    r = settle(board, "เสมอ", bets)
    assert r[0]["result"] == "เสมอ-คืนต้น"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("ALL TESTS PASSED")
