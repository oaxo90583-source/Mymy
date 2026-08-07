# -*- coding: utf-8 -*-
"""
app/line_api.py — client ของ LINE Messaging API (reply/push + โหลดรูป/สื่อบน cloud)
"""

import os
import base64
import requests

TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
BASE = "https://api.line.me/v2/bot"
MEDIA_BASE = "https://api-data.line.me/v2/bot"


def _headers(token: str = ""):
    return {"Authorization": f"Bearer {token or TOKEN}"}


def reply(reply_token: str, messages: list, token: str = "") -> requests.Response:
    return requests.post(f"{BASE}/message/reply", headers=_headers(token),
                         json={"replyToken": reply_token, "messages": messages}, timeout=20)


def push(user_id: str, messages: list, token: str = "") -> requests.Response:
    return requests.post(f"{BASE}/message/push", headers=_headers(token),
                         json={"to": user_id, "messages": messages}, timeout=20)


def text_message(t: str, user_id: str = None):
    if not user_id:
        return {"type": "text", "text": t}
    
    # รูปแบบ Mention ของ LINE: ใส่ @[name] ใน text และระบุ mentionee
    # ในที่นี้เราจะใส่ @ ที่หน้าข้อความ
    return {
        "type": "text",
        "text": f"@{t}",
        "mention": {
            "mentionees": [
                {
                    "index": 0,
                    "length": 1, # ความยาวของ @
                    "userId": user_id
                }
            ]
        }
    }


def image_message(url: str, preview_url: str = ""):
    return {"type": "image", "originalContentUrl": url,
            "previewImageUrl": preview_url or url}


def template_message(text: str, actions: list, title: str = "เมนูลัด"):
    return {"type": "template", "altText": text,
            "template": {"type": "buttons", "title": title, "text": text, "actions": actions}}


def flex_message(alt_text: str, contents: dict):
    return {"type": "flex", "altText": alt_text, "contents": contents}


def make_board_flex(title: str, red_text: str, blue_text: str, accept_text: str, mode: str = "ต่อไป") -> dict:
    """สร้าง Flex Message สำหรับเปิดราคา (การ์ดแนวนอน แยกสี แดง/น้ำเงินชัดเจน)"""
    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1E1E1E",
            "paddingAll": "10px",
            "contents": [
                {
                    "type": "text",
                    "text": f"🥊 {title} ({mode})",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "sm"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "10px",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "flex": 1,
                            "backgroundColor": "#FFEBEE",
                            "cornerRadius": "sm",
                            "paddingAll": "8px",
                            "alignItems": "center",
                            "contents": [
                                {"type": "text", "text": "🔴", "size": "sm", "flex": 0},
                                {"type": "text", "text": red_text, "color": "#C62828", "weight": "bold", "size": "sm", "wrap": True}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "flex": 1,
                            "backgroundColor": "#E3F2FD",
                            "cornerRadius": "sm",
                            "paddingAll": "8px",
                            "alignItems": "center",
                            "contents": [
                                {"type": "text", "text": "🔵", "size": "sm", "flex": 0},
                                {"type": "text", "text": blue_text, "color": "#1565C0", "weight": "bold", "size": "sm", "wrap": True}
                            ]
                        }
                    ]
                },
                {
                    "type": "text",
                    "text": f"💰 ป้ายรับ: {accept_text}",
                    "size": "xs",
                    "color": "#555555",
                    "align": "center",
                    "margin": "sm"
                }
            ]
        }
    }


def make_bet_result_flex(status: str, side: str, amount: float, credit_left: float, detail: str = "") -> dict:
    """สร้าง Flex Message สำหรับแจ้งผลการแทง (ติด / ไม่ติด)"""
    bg_color = "#E8F5E9" if "ติด" in status else "#FFEBEE"
    text_color = "#2E7D32" if "ติด" in status else "#C62828"
    return {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": bg_color,
            "paddingAll": "12px",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": status,
                    "color": text_color,
                    "weight": "bold",
                    "size": "md",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": f"ฝั่ง{side} จำนวน {amount:,.0f} บาท",
                    "size": "sm",
                    "color": "#333333",
                    "align": "center",
                    "weight": "bold"
                },
                {"type": "text", "text": detail, "size": "xs", "color": "#666666", "align": "center"} if detail else None,
                {
                    "type": "separator",
                    "margin": "sm",
                    "color": "#DDDDDD"
                },
                {
                    "type": "text",
                    "text": f"เครดิตคงเหลือ: {credit_left:,.2f} บาท",
                    "size": "xs",
                    "color": "#444444",
                    "align": "center",
                    "weight": "bold"
                }
            ]
        }
    }


def make_settle_flex(match_name: str, winner: str, summary_rows: list) -> dict:
    """สร้าง Flex Message สรุปยอดหลังจบมวยแบบพรีเมียม (ผู้เล่น, ทุน, ได้เสีย, ยอดสุทธิ)"""
    win_color = "#C62828" if winner == "แดง" else ("#1565C0" if winner == "น้ำเงิน" else "#555555")
    win_label = "🔴 แดงชนะ" if winner == "แดง" else ("🔵 น้ำเงินชนะ" if winner == "น้ำเงิน" else "⚖️ เสมอ / ยกเลิก")
    
    header_box = {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#1E1E1E",
        "paddingAll": "12px",
        "cornerRadius": "md",
        "contents": [
            {
                "type": "text",
                "text": f"🥊 สรุปยอดหลังจบมวย {match_name}",
                "color": "#FFD700",
                "weight": "bold",
                "size": "sm",
                "align": "center"
            },
            {
                "type": "text",
                "text": f"ผลการแข่งขัน: {win_label}",
                "color": "#FFFFFF",
                "size": "xs",
                "align": "center",
                "margin": "xs"
            }
        ]
    }
    
    table_header = {
        "type": "box",
        "layout": "horizontal",
        "paddingAll": "8px",
        "backgroundColor": "#2C2C2C",
        "cornerRadius": "sm",
        "contents": [
            {"type": "text", "text": "ผู้เล่น", "size": "xs", "weight": "bold", "flex": 3, "color": "#FFFFFF"},
            {"type": "text", "text": "ทุน", "size": "xs", "weight": "bold", "flex": 2, "align": "end", "color": "#CCCCCC"},
            {"type": "text", "text": "ได้เสีย", "size": "xs", "weight": "bold", "flex": 2, "align": "end", "color": "#FFD700"},
            {"type": "text", "text": "ยอดสุทธิ", "size": "xs", "weight": "bold", "flex": 2, "align": "end", "color": "#FFFFFF"}
        ]
    }
    
    contents = [header_box, table_header, {"type": "separator", "margin": "sm"}]
    
    for idx, s in enumerate(summary_rows[:10], 1):
        is_win = s["profit"] > 0
        is_loss = s["profit"] < 0
        pl_color = "#4CAF50" if is_win else ("#FF5252" if is_loss else "#AAAAAA")
        
        if is_win:
            pl_text = f"▲ +{s['profit']:,.0f}"
        elif is_loss:
            pl_text = f"▼ {s['profit']:,.0f}"
        else:
            pl_text = "0"
            
        bg_color = "#F9F9F9" if idx % 2 == 0 else "#FFFFFF"
        
        row_box = {
            "type": "box",
            "layout": "horizontal",
            "paddingVertical": "8px",
            "paddingHorizontal": "6px",
            "backgroundColor": bg_color,
            "cornerRadius": "sm",
            "alignItems": "center",
            "contents": [
                {"type": "text", "text": f"{idx}. {s['name']}", "size": "xs", "flex": 3, "wrap": True, "color": "#222222", "weight": "bold"},
                {"type": "text", "text": f"{s['capital']:,.0f}", "size": "xs", "flex": 2, "align": "end", "color": "#666666"},
                {"type": "text", "text": pl_text, "size": "xs", "weight": "bold", "flex": 2, "align": "end", "color": pl_color},
                {"type": "text", "text": f"{s['balance']:,.0f}", "size": "xs", "weight": "bold", "flex": 2, "align": "end", "color": "#111111"}
            ]
        }
        contents.append(row_box)

    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "xs",
            "paddingAll": "10px",
            "contents": contents
        }
    }


def make_wallet_menu_flex() -> dict:
    """สร้าง Flex Message สำหรับห้องฝากถอน (ปุ่มกดด่วน กระชับ)"""
    return {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": "💳 เมนูฝาก-ถอน / เครดิต",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#1E1E1E",
                    "align": "center"
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#2E7D32",
                    "height": "sm",
                    "action": {"type": "message", "label": "ดูเครดิต (c)", "text": "c"}
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "color": "#1565C0",
                    "height": "sm",
                    "action": {"type": "message", "label": "ดูบิลล่าสุด (cc)", "text": "cc"}
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "button",
                            "style": "link",
                            "height": "sm",
                            "action": {"type": "message", "label": "ฝากเงิน", "text": "ฝาก 1000"}
                        },
                        {
                            "type": "button",
                            "style": "link",
                            "height": "sm",
                            "action": {"type": "message", "label": "ถอนเงิน", "text": "ถอน 500"}
                        }
                    ]
                }
            ]
        }
    }


def postback_action(label: str, data: str):
    return {"type": "postback", "label": label, "data": data}


def get_media_content(message_id: str, token: str = "") -> bytes:
    """โหลดไฟล์ภาพ/วิดีโอที่ผู้ใช้ส่งมา (เช่น สลิป)"""
    r = requests.get(f"{MEDIA_BASE}/message/{message_id}/content", headers=_headers(token), timeout=60)
    r.raise_for_status()
    return r.content


def get_profile(user_id: str, token: str = "") -> dict:
    r = requests.get(f"{BASE}/profile/{user_id}", headers=_headers(token), timeout=20)
    r.raise_for_status()
    return r.json()


def upload_base64(base64_data: str, filename: str = "proof.jpg") -> str:
    """อัปโหลดภาพขึ้น cloud เก็บ URL (สำรอง — ใช้ URL ส่วนตัวของ LINE)"""
    return f"data:image/jpeg;base64,{base64_data}"
