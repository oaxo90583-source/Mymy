# -*- coding: utf-8 -*-
"""
app/web.py — FastAPI app:
- POST /webhook      : LINE Messaging API webhook (พร้อม Debug Log ละเอียด)
- GET  /             : พอร์ทแอดมิน (SPA)
- GET  /api/*        : REST API สำหรับแอดมิน (ดู/แก้/สรุปผล)
"""

import os
import hmac
import hashlib
import base64
import logging
from typing import Optional

from fastapi import FastAPI, Request, Header, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import bot, models, line_api

# ตั้งค่า Logging ให้แสดงผลบน Console ของ Render อย่างชัดเจน
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("muaythai_bot")

app = FastAPI(title="บอทคำนวณมวยพักยก")


@app.on_event("startup")
async def _startup():
    models.init_db()
    if not models.count_keywords():
        models.seed_keywords_from_json()
        logger.info("seeded %d keywords", models.count_keywords())

app.mount("/static", StaticFiles(directory=os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "webui")), name="static")

CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")


# ---------- webhook ----------

class _LINEEvent(BaseModel):
    type: str
    source: dict
    message: Optional[dict] = None
    postback: Optional[dict] = None


class _LINEPayload(BaseModel):
    events: list[_LINEEvent]


def _verify_signature(body: bytes, signature: str) -> bool:
    if not CHANNEL_SECRET or not signature:
        return True  # dev mode: ไม่มี secret → ยอมตลอด
    calc_sig = base64.b64encode(
        hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(calc_sig, signature)


@app.post("/webhook")
async def webhook(req: Request, x_line_signature: Optional[str] = Header(None)):
    body = await req.body()
    logger.info("🔔 รับ Webhook request, signature: %s, body length: %d", x_line_signature, len(body))
    
    if not _verify_signature(body, x_line_signature or ""):
        logger.error("❌ ล้มเหลว: Signature ไม่ถูกต้อง (Mismatch)")
        raise HTTPException(401, "signature mismatch")
    
    try:
        payload = _LINEPayload.model_validate_json(body)
    except Exception as e:
        logger.error("❌ ไม่สามารถแปลง JSON Payload จาก LINE ได้: %s | Body: %s", e, body.decode('utf-8', errors='ignore'))
        return {"ok": True}

    for ev in payload.events:
        logger.info("📩 ได้รับ Event type: %s", ev.type)
        if ev.type != "message" or not ev.message:
            continue
        msg = ev.message
        if msg.get("type") != "text":
            logger.info("ℹ️ ข้ามข้อความที่ไม่ใช่ข้อความตัวหนังสือ (Type: %s)", msg.get("type"))
            continue
        
        user_id = ev.source.get("userId", "")
        text_content = msg.get("text") or ""
        logger.info("💬 ข้อความจากผู้ใช้ [User ID: %s]: %s", user_id, text_content)
        
        display = text_content[:40]
        try:
            prof = line_api.get_profile(user_id)
            display = prof.get("displayName", display)
        except Exception as e:
            logger.warning("⚠️ ไม่สามารถดึง Profile ของผู้ใช้ %s ได้: %s", user_id, e)
            
        try:
            replies = bot.handle_message(user_id, display, text_content)
            logger.info("🤖 ผลลัพธ์ข้อความตอบกลับที่สร้างสำหรับ %s: %s", user_id, replies)
        except Exception as e:
            logger.error("🔥 เกิดข้อผิดพลาดใน bot.handle_message สำหรับ %s: %s", user_id, e, exc_info=True)
            replies = [line_api.text_message("⚠️ เกิดข้อผิดพลาดในการประมวลผลคำสั่ง กรุณาลองใหม่อีกครั้ง")]

        if msg.get("replyToken") and replies:
            try:
                resp = line_api.reply(msg["replyToken"], replies)
                logger.info("📤 ส่งข้อความกลับ LINE สำเร็จ | Status: %s | Body: %s", resp.status_code, resp.text)
                if resp.status_code != 200:
                    logger.error("❌ LINE API ปฏิเสธการส่งข้อความ Status %d: %s", resp.status_code, resp.text)
            except Exception as e:
                logger.error("🔥 เกิดข้อผิดพลาดขณะเรียก line_api.reply: %s", e, exc_info=True)
                
    return {"ok": True}


# ---------- admin pages ----------

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(os.path.dirname(
        os.path.dirname(__file__)), "webui", "index.html"))


def _require_admin(x_admin_token: Optional[str] = Header(None)):
    expected = os.environ.get("ADMIN_TOKEN", "muaythai-admin-dev")
    if x_admin_token != expected:
        raise HTTPException(403, "admin token ผิด")


class _MatchIn(BaseModel):
    name: str


class _BetIn(BaseModel):
    match_id: int
    user_id: str
    side: str
    amount: float


class _ResultIn(BaseModel):
    match_id: int
    winner: str
    note: str = ""


@app.get("/api/matches", dependencies=[Depends(_require_admin)])
def api_matches(status: Optional[str] = None):
    return models.list_matches(status)


@app.post("/api/matches", dependencies=[Depends(_require_admin)])
def api_new_match(m: _MatchIn):
    return {"id": models.new_match(m.name)}


@app.post("/api/matches/{match_id}/result", dependencies=[Depends(_require_admin)])
def api_set_result(match_id: int, r: _ResultIn):
    models.set_result(match_id, r.winner, r.note)
    rows = models.settle_match(match_id)
    return {"settled": rows, "total_payout": sum(x["payout"] for x in rows)}


@app.get("/api/bets", dependencies=[Depends(_require_admin)])
def api_bets(match_id: Optional[int] = None):
    return models.list_bets(match_id)


@app.get("/api/members", dependencies=[Depends(_require_admin)])
def api_members():
    return models.list_members()


@app.post("/api/credit", dependencies=[Depends(_require_admin)])
def api_credit(payload: dict):
    """{user_id, kind: ฝาก|ถอน|เติม, amount}"""
    kind = payload.get("kind", "เติม")
    amount = float(payload.get("amount", 0))
    if amount <= 0 or kind not in ("ฝาก", "ถอน", "เติม"):
        raise HTTPException(400, "ข้อมูลไม่ถูกต้อง")
    mid = models.find_member(payload.get("user_id", ""))
    if not mid:
        mid = models.new_member(payload.get("user_id", ""))
    sign = 1 if kind in ("ฝาก", "เติม") else -1
    if kind == "ถอน" and models.get_member_credit(mid) < amount:
        raise HTTPException(400, "เครดิตไม่พอ")
    models.adjust_credit(mid, sign * amount)
    models.add_txn(mid, kind, amount, note=payload.get("note", ""))
    return {"ok": True, "new_credit": models.get_member_credit(mid)}


@app.get("/api/keywords", dependencies=[Depends(_keyword_reply := None) or Depends(_require_admin)])
def api_keywords():
    conn = models.get_conn()
    return [dict(r) for r in conn.execute("SELECT * FROM keywords ORDER BY id").fetchall()]


@app.get("/api/pool", dependencies=[Depends(_require_admin)])
def api_pool(match_id: Optional[int] = None):
    """ยอดรวมเจ้ามือ (รวมบิลที่ติด) จำลอง: ผลลบ = เจ้ามือต้องจ่าย, บวก = เจ้ามือกำไร"""
    conn = models.get_conn()
    q = ("SELECT b.match_id, b.side, SUM(b.actual) AS total, "
         "pb.red_pay, pb.red_win, pb.blue_pay, pb.blue_win, m.status "
         "FROM bets b JOIN price_boards pb ON pb.id=b.board_id "
         "JOIN matches m ON m.id=b.match_id "
         "WHERE b.status='ติด'")
    params = []
    if match_id:
        q += " AND b.match_id=?"
        params.append(match_id)
    q += " GROUP BY b.match_id, b.side"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]
