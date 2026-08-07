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


def text_message(t: str):
    return {"type": "text", "text": t}


def image_message(url: str, preview_url: str = ""):
    return {"type": "image", "originalContentUrl": url,
            "previewImageUrl": preview_url or url}


def template_message(text: str, actions: list, title: str = "เมนูลัด"):
    return {"type": "template", "altText": text,
            "template": {"type": "buttons", "title": title, "text": text, "actions": actions}}


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
