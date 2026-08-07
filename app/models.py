# -*- coding: utf-8 -*-
"""
app/models.py — ฐานข้อมูล SQLite สำหรับบอทมวยพักยก

ตาราง:
- members     : สมาชิก (LINE id, ชื่อ, เครดิตคงเหลือ)
- matches     : คู่แข่งขัน (ชื่อคู่, สถานะ: เปิด/ปิด/สรุปแล้ว/ยกเลิก)
- price_boards: ราคาแต่ละช่วง (match_id, ข้อความต้นฉบับ, โหมด, red, blue, รับ, กลางอากาศ, จังหวะเวลา)
- bets        : ยอดแทง (member, match, board, มุม, จำนวน, สถานะ: ติด/ไม่ติด/ยกเลิก)
- txns        : การฝาก-ถอน-เติม (member, ประเภท, จำนวน, สลิป, หมายเหตุ)
- results     : ผลแข่งขันต่อคู่ (match, winner: แดง/เงิน/เสมอ)
- settle_log  : ผลได้-เสียต่อบิล (bet, payout, สถานะ)
- settings    : ค่าตั้งค่าระบบ (คีย์ลัดแอดมิน, ชื่อบัญชีฯ)
- keywords    : คลังคีย์ลัด 206 รายการ (สำหรับแอดมินแก้ไขได้)
"""

import sqlite3
import threading
import json
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.environ.get("MUAYTHAI_DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "muaythai.db"))

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.executescript("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")
    return _local.conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_user_id TEXT UNIQUE NOT NULL,
    member_code TEXT UNIQUE,
    display_name TEXT DEFAULT '',
    credit REAL NOT NULL DEFAULT 0,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- open | closed | settled | cancelled
    round_no INTEGER DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS price_boards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    raw_text TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'ต่อไป',     -- ต่อไป | รองเงิน | เสมอ | ยุติ
    red_pay REAL, red_win REAL,
    blue_pay REAL, blue_win REAL,
    accept_amt REAL DEFAULT 0,
    is_midair INTEGER NOT NULL DEFAULT 0,
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    board_id INTEGER NOT NULL REFERENCES price_boards(id) ON DELETE CASCADE,
    side TEXT NOT NULL,                    -- แดง | เงิน
    amount REAL NOT NULL,
    actual REAL NOT NULL,                  -- ยอดติดจริง (อาจถูกตัดตามป้ายรับ/เครดิต)
    full_cap INTEGER NOT NULL DEFAULT 0,   -- 1 = ติดเต็มจำนวน
    status TEXT NOT NULL DEFAULT 'ติด',    -- ติด | ไม่ติด | ยกเลิก
    reply_msg TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS txns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id),
    kind TEXT NOT NULL,                    -- ฝาก | ถอน | เติม
    amount REAL NOT NULL,
    proof TEXT DEFAULT '',                 -- ข้อความ/ลิงก์สลิป
    note TEXT DEFAULT '',
    admin_reply TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER UNIQUE NOT NULL REFERENCES matches(id),
    winner TEXT NOT NULL DEFAULT '',       -- แดง | เงิน | เสมอ | ยกเลิก
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS settle_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_id INTEGER NOT NULL REFERENCES bets(id),
    payout REAL NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '',       -- ชนะ | แพ้ | เสมอ-คืนต้น
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT UNIQUE NOT NULL,
    title TEXT DEFAULT '',
    keywords TEXT DEFAULT '',
    response_type TEXT DEFAULT 'TEXT',
    response TEXT DEFAULT '',
    media_key TEXT DEFAULT '',
    media_type TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT PRIMARY KEY,
    room_type TEXT NOT NULL,               -- play | finance | admin
    note TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    
    # ตรวจสอบและเพิ่มคอลัมน์ member_code ถ้ายังไม่มี (Migration)
    try:
        # SQLite ไม่ยอมให้เพิ่ม UNIQUE column โดยตรงผ่าน ALTER TABLE
        conn.execute("ALTER TABLE members ADD COLUMN member_code TEXT")
        conn.commit()
        
        # สร้าง Index เพื่อให้ค้นหาเร็วและเลียนแบบ Unique
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_member_code ON members(member_code)")
        conn.commit()
        
        # เจนรหัสให้สมาชิกเก่าที่ยังไม่มีรหัส
        rows = conn.execute("SELECT id FROM members WHERE member_code IS NULL").fetchall()
        for r in rows:
            mid = r["id"]
            code = f"M{mid:04d}"
            conn.execute("UPDATE members SET member_code=? WHERE id=?", (code, mid))
        conn.commit()
    except sqlite3.OperationalError:
        pass # คอลัมน์อาจจะมีอยู่แล้ว
        
    conn.commit()


def is_admin(line_user_id: str) -> bool:
    """ตรวจสอบสิทธิ์แอดมิน จาก is_admin flag ในฐานข้อมูล หรือจาก LINE_ADMIN_IDS env"""
    # ตรวจสอบจาก env ก่อน (เพิ่มประสิทธิภาพ)
    admin_ids_env = os.environ.get("LINE_ADMIN_IDS", "")
    if admin_ids_env:
        ids = [x.strip() for x in admin_ids_env.split(",") if x.strip()]
        if line_user_id in ids:
            return True
    # ตรวจสอบจากฐานข้อมูล
    conn = get_conn()
    row = conn.execute("SELECT is_admin FROM members WHERE line_user_id=?", (line_user_id,)).fetchone()
    return bool(row and row["is_admin"])


def ensure_member(line_user_id: str, display_name: str = "") -> int:
    """ลงทะเบียนสมาชิกถ้ายังไม่มี ส่งคืน member_id"""
    conn = get_conn()
    row = conn.execute("SELECT id FROM members WHERE line_user_id=?", (line_user_id,)).fetchone()
    if row:
        # อัปเดตชื่อถ้ามีการส่งมา
        if display_name:
            conn.execute("UPDATE members SET display_name=? WHERE id=?", (display_name, row["id"]))
            conn.commit()
        return row["id"]
    return new_member(line_user_id, display_name)


def get_open_match() -> Optional[int]:
    """ดึง ID คู่ที่กำลังเปิดรับแทง (status='open')"""
    conn = get_conn()
    row = conn.execute("SELECT id FROM matches WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def log_txn(member_id: int, kind: str, amount: float, note: str = "") -> int:
    """บันทึกรายการฝาก/ถอน/เติม (alias ของ add_txn)"""
    return add_txn(member_id, kind, amount, note=note)


def find_keyword(text: str) -> Optional[str]:
    """ค้นหาคีย์เวิร์ดที่ตรงกับข้อความ ส่งคืน response หรือ None"""
    conn = get_conn()
    rows = conn.execute("SELECT keywords, response FROM keywords WHERE active=1").fetchall()
    text_lower = text.strip().lower()
    for row in rows:
        kws = [k.strip().lower() for k in (row["keywords"] or "").split() if k.strip()]
        if text_lower in kws:
            return row["response"]
    return None


def ensure_admin(line_user_id: str) -> int:
    """รับประกันว่า user เป็นแอดมิน (ใช้ในระหว่างพัฒนา) — ส่งคืน member_id"""
    conn = get_conn()
    row = conn.execute("SELECT id FROM members WHERE line_user_id=?", (line_user_id,)).fetchone()
    if row:
        return row["id"]
    
    # ถ้ายังไม่มี ให้สร้างใหม่พร้อมรหัส
    mid = new_member(line_user_id, "Admin")
    conn.execute("UPDATE members SET is_admin=1 WHERE id=?", (mid,))
    conn.commit()
    return mid


def new_match(name: str) -> int:
    conn = get_conn()
    conn.execute("INSERT INTO matches(name) VALUES(?)", (name,))
    conn.commit()
    return conn.execute("SELECT id FROM matches ORDER BY id DESC LIMIT 1").fetchone()["id"]


def close_match(match_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE matches SET status='closed' WHERE id=?", (match_id,))
    conn.commit()


def add_board(match_id: int, raw_text: str, mode: str, red, blue, accept_amt: float,
              is_midair: bool, note: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO price_boards(match_id, raw_text, mode, red_pay, red_win, blue_pay, blue_win, "
        "accept_amt, is_midair, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (match_id, raw_text, mode,
         red.pay_num if red else None, red.win_num if red else None,
         blue.pay_num if blue else None, blue.win_num if blue else None,
         accept_amt, int(is_midair), note))
    conn.commit()
    return cur.lastrowid


def get_member_credit(member_id: int) -> float:
    conn = get_conn()
    row = conn.execute("SELECT credit FROM members WHERE id=?", (member_id,)).fetchone()
    return float(row["credit"]) if row else 0.0


def adjust_credit(member_id: int, delta: float) -> float:
    """ปรับเครดิต (บวก/ลบ) ส่งคืนยอดใหม่"""
    conn = get_conn()
    conn.execute("UPDATE members SET credit = credit + ? WHERE id=?", (delta, member_id))
    conn.commit()
    row = conn.execute("SELECT credit FROM members WHERE id=?", (member_id,)).fetchone()
    return float(row["credit"])


def find_member(query: str) -> Optional[int]:
    """ค้นหาสมาชิกจาก line_user_id, member_code, หรือ display_name"""
    conn = get_conn()
    # ค้นหาจาก line_user_id
    row = conn.execute("SELECT id FROM members WHERE line_user_id=?", (query,)).fetchone()
    if row:
        return row["id"]
    # ค้นหาจาก member_code (เช่น M0001)
    row = conn.execute("SELECT id FROM members WHERE member_code=?", (query.upper(),)).fetchone()
    if row:
        return row["id"]
    # ค้นหาจาก display_name (ตรงตัวอักษร)
    row = conn.execute("SELECT id FROM members WHERE display_name=?", (query,)).fetchone()
    return row["id"] if row else None


def new_member(line_user_id: str, display_name: str = "") -> int:
    conn = get_conn()
    conn.execute("INSERT INTO members(line_user_id, display_name) VALUES(?,?)",
                 (line_user_id, display_name))
    conn.commit()
    row = conn.execute("SELECT id FROM members WHERE line_user_id=?", (line_user_id,)).fetchone()
    mid = row["id"]
    
    # สร้างรหัสลูกค้าอัตโนมัติ (เช่น M0001)
    code = f"M{mid:04d}"
    conn.execute("UPDATE members SET member_code=? WHERE id=?", (code, mid))
    conn.commit()
    return mid

def get_member_by_code(code: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM members WHERE member_code=?", (code,)).fetchone()
    return dict(row) if row else None

def get_member_info(member_id: int) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    return dict(row) if row else None


def open_board_for_side(match_id: int, side: str) -> float:
    """ยอดรับคงเหลือของฝั่งนั้นในกระดานล่าสุด (ป้ายรับ - ยอดที่ไม่แน่นอนแล้ว)"""
    conn = get_conn()
    col = "red_pay" if side == "แดง" else "blue_pay"
    board = conn.execute(
        "SELECT accept_amt, id FROM price_boards WHERE match_id=? ORDER BY id DESC LIMIT 1",
        (match_id,)).fetchone()
    if not board:
        return 0.0
    taken = conn.execute(
        "SELECT COALESCE(SUM(actual),0) AS t FROM bets WHERE board_id=? AND side=? AND status='ติด'",
        (board["id"], side)).fetchone()["t"]
    return max(0.0, float(board["accept_amt"]) - taken)


def add_bet(member_id: int, match_id: int, board_id: int, side: str, amount: float,
            actual: float, full_cap: bool, status: str = "ติด", reply_msg: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO bets(member_id, match_id, board_id, side, amount, actual, full_cap, status, reply_msg) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (member_id, match_id, board_id, side, amount, actual, int(full_cap), status, reply_msg))
    conn.commit()
    return cur.lastrowid


def cancel_last_bet(member_id: int, match_id: int) -> Optional[int]:
    """ยกเลิกบิลล่าสุดของสมาชิกในคู่นั้น คืนยอดเครดิต"""
    conn = get_conn()
    bet = conn.execute(
        "SELECT * FROM bets WHERE member_id=? AND match_id=? AND status='ติด' ORDER BY id DESC LIMIT 1",
        (member_id, match_id)).fetchone()
    if not bet:
        return None
    conn.execute("UPDATE bets SET status='ยกเลิก' WHERE id=?", (bet["id"],))
    adjust_credit(member_id, float(bet["actual"]))
    conn.commit()
    return bet["id"]


def add_txn(member_id: int, kind: str, amount: float, proof: str = "", note: str = "") -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO txns(member_id, kind, amount, proof, note) VALUES(?,?,?,?,?)",
                       (member_id, kind, amount, proof, note))
    conn.commit()
    return cur.lastrowid


def set_result(match_id: int, winner: str, note: str = "") -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO results(match_id, winner, note) VALUES(?,?,?) "
        "ON CONFLICT(match_id) DO UPDATE SET winner=excluded.winner, note=excluded.note",
        (match_id, winner, note))
    conn.commit()


def settle_match(match_id: int) -> list:
    """คำนวณผลได้-เสียทุกบิลของคู่นี้ คืนรายการ settle"""
    conn = get_conn()
    res = conn.execute("SELECT winner FROM results WHERE match_id=?", (match_id,)).fetchone()
    if not res:
        raise ValueError("ยังไม่มีผลแข่งขันสำหรับคู่นี้")
    winner = res["winner"]
    bets = conn.execute(
        "SELECT * FROM bets WHERE match_id=? AND status='ติด'", (match_id,)).fetchall()
    out = []
    for b in bets:
        if winner in ("ยกเลิก", "ยุติ"):
            payout, result, note = 0.0, "ยกเลิก", "คืนยอดเต็ม"
            adjust_credit(b["member_id"], float(b["actual"]))
        elif winner == "เสมอ":
            payout, result, note = 0.0, "เสมอ-คืนต้น", "เสมอไม่ได้ไม่เสีย"
            adjust_credit(b["member_id"], float(b["actual"]))
        else:
            won = b["side"] == winner
            if won:
                from app.calc import Price
                board = conn.execute("SELECT * FROM price_boards WHERE id=?", (b["board_id"],)).fetchone()
                pay = float(board["red_pay"] if b["side"] == "แดง" else board["blue_pay"])
                win = float(board["red_win"] if b["side"] == "แดง" else board["blue_win"])
                price = Price(b["side"], pay > win, pay, win)
                payout = round(float(b["actual"]) * price.win_num / price.pay_num, 2)
                result, note = "ชนะ", f"ได้ {payout:,.2f} (รวมคืนต้น {payout + float(b['actual']):,.2f})"
                adjust_credit(b["member_id"], float(b["actual"]) + payout)
            else:
                # กฎมวยพักยก: แพ้เสียครึ่ง (คืนต้น 50%)
                loss_amt = float(b["actual"]) / 2.0
                payout = 0.0
                result = "แพ้ (เสียครึ่ง)"
                note = f"เสียครึ่ง {loss_amt:,.2f} (คืนต้น {loss_amt:,.2f})"
                adjust_credit(b["member_id"], loss_amt)
        conn.execute("INSERT INTO settle_log(bet_id, payout, result, note) VALUES(?,?,?,?)",
                     (b["id"], payout, result, note))
        out.append(dict(bet_id=b["id"], side=b["side"], actual=float(b["actual"]),
                        payout=payout, result=result, note=note))
    conn.execute("UPDATE matches SET status='settled' WHERE id=?", (match_id,))
    conn.commit()
    return out


def list_matches(status: Optional[str] = None) -> list:
    conn = get_conn()
    q = "SELECT * FROM matches"
    params = []
    if status:
        q += " WHERE status=?"
        params.append(status)
    return [dict(r) for r in conn.execute(q + " ORDER BY id DESC", params).fetchall()]


def list_bets(match_id: Optional[int] = None, member_id: Optional[int] = None) -> list:
    conn = get_conn()
    q = "SELECT * FROM bets WHERE 1=1"
    params = []
    if match_id is not None:
        q += " AND match_id=?"
        params.append(match_id)
    if member_id is not None:
        q += " AND member_id=?"
        params.append(member_id)
    return [dict(r) for r in conn.execute(q + " ORDER BY id DESC", params).fetchall()]


def list_members() -> list:
    conn = get_conn()
    return [dict(r) for r in conn.execute("SELECT * FROM members ORDER BY id").fetchall()]


def count_keywords() -> int:
    conn = get_conn()
    return conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]


def seed_keywords_from_json(json_path: Optional[str] = None) -> int:
    """โหลดคีย์ลัดจาก all_details.json ของ LINE OA ลงตาราง keywords"""
    if json_path is None:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        json_path = os.environ.get(
            "MUAYTHAI_KEYWORDS_JSON",
            os.path.join(project_root, "assets_keywords.json"))
        if not json_path or not os.path.exists(json_path):
            return 0
    import json
    data = json.load(open(json_path, encoding="utf-8"))
    conn = get_conn()
    n = 0
    for it in data:
        item_id = str(it.get("instantResponseId") or "")
        if not item_id:
            continue
        balloons = it.get("balloons") or []
        for b in balloons:
            resp = b.get("text") or ""
            media_key = b.get("key") or ""
            media_type = b.get("contentType") or ""
            if not resp and not media_key:
                continue
            rtype = "IMAGE" if (media_key and not resp) else ("VIDEO" if media_type == "VIDEO" else "TEXT")
            conn.execute(
                "INSERT OR IGNORE INTO keywords(item_id, title, keywords, response_type, "
                "response, media_key, media_type) VALUES(?,?,?,?,?,?,?)",
                (item_id, it.get("title") or "", " ".join(it.get("keywords") or []),
                 rtype, resp, media_key, media_type))
            n += 1
    conn.commit()
    return n

def update_keyword(kw_id: int, title: str, keywords: str, response: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE keywords SET title=?, keywords=?, response=? WHERE id=?",
                 (title, keywords, response, kw_id))
    conn.commit()

def delete_keyword(kw_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM keywords WHERE id=?", (kw_id,))
    conn.commit()

def add_keyword(title: str, keywords: str, response: str) -> int:
    conn = get_conn()
    import uuid
    item_id = str(uuid.uuid4())
    cur = conn.execute("INSERT INTO keywords(item_id, title, keywords, response) VALUES(?,?,?,?)",
                       (item_id, title, keywords, response))
    conn.commit()
    return cur.lastrowid

def update_latest_board(match_id: int, raw_text: str, mode: str, red_pay: float, red_win: float, blue_pay: float, blue_win: float, accept_amt: float) -> None:
    conn = get_conn()
    # หา board ล่าสุดของคู่นี้
    row = conn.execute("SELECT id FROM price_boards WHERE match_id=? ORDER BY id DESC LIMIT 1", (match_id,)).fetchone()
    if row:
        conn.execute("UPDATE price_boards SET raw_text=?, mode=?, red_pay=?, red_win=?, blue_pay=?, blue_win=?, accept_amt=? WHERE id=?",
                     (raw_text, mode, red_pay, red_win, blue_pay, blue_win, accept_amt, row["id"]))
        conn.commit()

def set_room_type(room_id: str, room_type: str, note: str = "") -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO rooms(room_id, room_type, note) VALUES(?,?,?) "
        "ON CONFLICT(room_id) DO UPDATE SET room_type=excluded.room_type, note=excluded.note, updated_at=datetime('now','localtime')",
        (room_id, room_type, note))
    conn.commit()

def get_room_type(room_id: str) -> Optional[str]:
    conn = get_conn()
    row = conn.execute("SELECT room_type FROM rooms WHERE room_id=?", (room_id,)).fetchone()
    return row["room_type"] if row else None

def list_rooms() -> list:
    conn = get_conn()
    return [dict(r) for r in conn.execute("SELECT * FROM rooms").fetchall()]

def get_member_match_history(member_id: int, match_id: int) -> list:
    """ดึงประวัติการแทงของสมาชิกเฉพาะคู่ที่ระบุ"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT b.*, pb.raw_text, pb.red_pay, pb.red_win, pb.blue_pay, pb.blue_win, s.payout, s.result as settle_result "
        "FROM bets b "
        "JOIN price_boards pb ON b.board_id = pb.id "
        "LEFT JOIN settle_log s ON b.id = s.bet_id "
        "WHERE b.member_id=? AND b.match_id=? "
        "ORDER BY b.id ASC",
        (member_id, match_id)
    ).fetchall()
    return [dict(r) for r in rows]

def get_daily_summary(date_str: Optional[str] = None) -> dict:
    """
    สรุปยอดรายวันของเจ้ามือ
    date_str: วันที่ในรูปแบบ 'YYYY-MM-DD' (ถ้าไม่ระบุจะใช้ปัจจุบัน)
    """
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    conn = get_conn()
    
    # 1. ยอดแทงรวม (บิลที่สถานะ 'ติด')
    total_bet = conn.execute(
        "SELECT COALESCE(SUM(actual), 0) FROM bets WHERE created_at LIKE ?",
        (f"{date_str}%",)
    ).fetchone()[0]
    
    # 2. จำนวนคู่ที่แข่งขันวันนี้
    match_count = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE created_at LIKE ?",
        (f"{date_str}%",)
    ).fetchone()[0]
    
    # 3. กำไร/ขาดทุนสุทธิของเจ้ามือ (คำนวณจาก settle_log)
    # เจ้ามือจะได้เงินจากบิลที่สมาชิก 'แพ้' และเสียเงินจากบิลที่สมาชิก 'ชนะ'
    # ในระบบนี้:
    # - ถ้าสมาชิกแพ้: เสียครึ่ง (actual/2) -> เจ้ามือได้กำไร actual/2
    # - ถ้าสมาชิกชนะ: ได้ payout -> เจ้ามือเสีย payout
    # - ถ้าเสมอ/ยกเลิก: payout=0 -> เจ้ามือไม่เสียและไม่ได้
    
    # ดึงข้อมูลบิลที่ settle แล้ววันนี้
    settled_bets = conn.execute(
        "SELECT b.actual, s.payout, s.result "
        "FROM bets b "
        "JOIN settle_log s ON b.id = s.bet_id "
        "WHERE b.created_at LIKE ?",
        (f"{date_str}%",)
    ).fetchall()
    
    house_profit = 0.0
    for b in settled_bets:
        if b["result"] == "ชนะ":
            # สมาชิกชนะ เจ้ามือเสีย payout
            house_profit -= float(b["payout"])
        elif "แพ้" in b["result"]:
            # สมาชิกแพ้เสียครึ่ง เจ้ามือได้กำไร actual/2
            house_profit += (float(b["actual"]) / 2.0)
            
    # 4. ยอดฝาก/ถอน/เติม วันนี้
    txns = conn.execute(
        "SELECT kind, SUM(amount) as total FROM txns WHERE created_at LIKE ? GROUP BY kind",
        (f"{date_str}%",)
    ).fetchall()
    
    txn_summary = {t["kind"]: t["total"] for t in txns}
    
    return {
        "date": date_str,
        "total_bet": total_bet,
        "match_count": match_count,
        "house_profit": house_profit,
        "deposits": txn_summary.get("ฝาก", 0.0) + txn_summary.get("เติม", 0.0),
        "withdrawals": txn_summary.get("ถอน", 0.0)
    }
