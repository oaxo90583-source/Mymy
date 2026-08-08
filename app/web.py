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
    count = models.count_keywords()
    logger.info("Current keywords count: %d", count)
    if count == 0:
        logger.info("Database is empty, starting seed process...")
        json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets_keywords.json")
        n = models.seed_keywords_from_json(json_path)
        logger.info("Successfully seeded %d keywords from %s", n, json_path)
    
    # เพิ่ม/อัปเดตคีย์เวิร์ดสำคัญจากรูปภาพเพื่อให้บอทพร้อมใช้งานทันที
    important_kws = [
        {"t": "f23", "k": "f23", "r": "🔵ง 23/1  ด 14/1\nรับ3000"},
        {"t": "ด21_กลางอากาศ", "k": "ด21", "r": "กลางอากาศ\n🔴ด 21  ง 32\nรับ4000"},
        {"t": "d21_ปกติ", "k": "d21", "r": "🔴🔴🔴🔴\nด 21  ง 53\nรับ20000"},
        {"t": "d41_ปกติ", "k": "d41", "r": "🔴🔴🔴🔴\nด 41  ง 52\nรับ20000"},
        {"t": "ด41_กลางอากาศ", "k": "ด41", "r": "กลางอากาศ\n🔴ด 41  ง 21\nรับ4000"}
    ]
    for kw in important_kws:
        models.add_keyword(kw["t"], kw["k"], kw["r"], upsert=True)
    logger.info("Updated important screenshot keywords")

WELCOME_TEXT = (
    "สวัสดี {display_name} ยินดรับเข้าสู่กลุ่ม! "
    "⚠️ อ่านกฎกลุ่มก่อนทัก: ห้ามส่งลิงก์โฆษณาค้าขาย/ส่งสแปม "
    "💡 สอบถาม/ฝาก-ถอน/เติมเครดิต ใช้เมนูด้านล่าง"
)

SLIP_NOTICE = (
    "📄 พบลูกค้าส่งรูปสีปเข้ามา "
    "⚠️ บอทไม่สามารถตรวจสอบความจริงของสีปได้โดยอัตโนมัติ "
    "แอดมินต้องตรวจสอบกับธนาคารก่อนเติมเครดิต"
)

app.mount("/static", StaticFiles(directory=os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "webui")), name="static")

CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")


# ---------- webhook ----------

class _LINEEvent(BaseModel):
    type: str
    source: dict
    replyToken: Optional[str] = None
    message: Optional[dict] = None
    postback: Optional[dict] = None

    @property
    def group_id(self) -> Optional[str]:
        return self.source.get("groupId") or self.source.get("roomId")

    @property
    def user_id(self) -> str:
        return self.source.get("userId", "")


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
        user_id = ev.user_id
        group_id = ev.group_id
        text_content = ""

        # ส่งข้อความต้อนรับแท็กคนเข้ากลุ่ม (LINE เข้าห้อง 30 คน/กลุ่ม/ครั้ง)
        if ev.type == "memberJoined" and group_id and user_id:
            display = "ผู้ใช้งานใหม่"
            try:
                display = line_api.get_profile(user_id).get("displayName", display)
            except Exception:
                pass
            line_api.push(group_id, [
                {"type": "text", "text": f"@ ทักทาย {display} ที่เข้าห้องใหม่",
                 "mention": {"mentionees": [{"index": 0, "length": 1, "userId": user_id}]}}
            ])
            continue

        if ev.type == "memberLeft":
            continue

        if ev.type == "message" and ev.message:
            msg = ev.message
            if msg.get("type") == "text":
                text_content = msg.get("text") or ""
            elif msg.get("type") in ("image", "sticker", "video", "audio", "file", "location"):
                # รูปสีป/ไฟล์อื่นๆ: แอดมินไม่ได้อยู่ในห้อง — ต้องส่ง forward ให้แอดมิน
                if ev.replyToken and group_id:
                    try:
                        prof = line_api.get_profile(user_id)
                        d = prof.get("displayName", "ผู้ใช้")
                        c = line_api.text_message(f"📎 {d} ส่งรูป{msg.get('type', '')}เข้ามา (แอดมินตรวจสอบสีปเอง)")
                        c2 = line_api.text_message(f"🆔 LINE ID: {user_id}", user_id)
                        line_api.reply(ev.replyToken, [c])
                        admin_ids = [x.strip() for x in
                                     __import__('os').environ.get("LINE_ADMIN_IDS", "").split(",")
                                     if x.strip()]
                        for adm in admin_ids[:3]:
                            line_api.push(adm, [c, c2])
                    except Exception as e:
                        logger.warning("Forward error: %s", e)
                continue
        elif ev.type == "postback" and ev.postback:
            text_content = ev.postback.get("data") or ""

        if not text_content:
            continue
            
        logger.info("💬 [%s] in [%s]: %s", user_id[:8], group_id or "private", text_content)
        
        display = "User"
        try:
            prof = line_api.get_profile(user_id)
            display = prof.get("displayName", display)
        except Exception:
            pass
            
        try:
            # ส่ง group_id (roomId/groupId) เข้าไปด้วยเพื่อให้บอทแยกแยะห้องได้
            replies = bot.handle_message(user_id, display, text_content, room_id=group_id)
        except Exception as e:
            logger.error("🔥 Error: %s", e, exc_info=True)
            replies = [line_api.text_message("⚠️ เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง")]

        if ev.replyToken and replies:
            # กรองค่า None ออกจากรายการข้อความ (ถ้ามี)
            replies = [r for r in replies if r]
            if replies:
                try:
                    line_api.reply(ev.replyToken, replies)
                except Exception as e:
                    logger.error("🔥 Reply Error: %s", e)
                
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


@app.get("/api/keywords", dependencies=[Depends(_require_admin)])
def api_keywords():
    conn = models.get_conn()
    return [dict(r) for r in conn.execute("SELECT * FROM keywords ORDER BY id").fetchall()]


@app.post("/api/keywords", dependencies=[Depends(_require_admin)])
def api_add_keyword(payload: dict):
    return {"id": models.add_keyword(payload.get("title", ""), payload.get("keywords", ""), payload.get("response", ""))}


@app.post("/api/keywords/{kw_id}", dependencies=[Depends(_require_admin)])
def api_update_keyword(kw_id: int, payload: dict):
    models.update_keyword(kw_id, payload.get("title", ""), payload.get("keywords", ""), payload.get("response", ""))
    return {"ok": True}


@app.post("/api/matches/{match_id}/price", dependencies=[Depends(_require_admin)])
def api_update_price(match_id: int, payload: dict):
    # payload: {raw_text, mode, red_pay, red_win, blue_pay, blue_win, accept_amt}
    models.update_latest_board(
        match_id, 
        payload.get("raw_text", ""), 
        payload.get("mode", "ต่อไป"),
        payload.get("red_pay"), 
        payload.get("red_win"),
        payload.get("blue_pay"), 
        payload.get("blue_win"),
        payload.get("accept_amt", 0)
    )
    return {"ok": True}


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
