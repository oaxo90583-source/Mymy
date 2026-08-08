# -*- coding: utf-8 -*-
"""ทดสอบ end-to-end: เปิดราคา, แทง, ยกบิลรายคน, ประกาศผล, cc คาดการณ์"""
import os, shutil, sys

DB = '/tmp/mymy_e2e.db'
if os.path.exists(DB):
    os.remove(DB)
os.environ['MUAYTHAI_DB_PATH'] = DB

from app import bot, models, calc, line_api
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

models.init_db()

# สมาชิก + แอดมิน
admin_uid = 'Uadmin'
user1 = 'Uuser1'
user2 = 'Uuser2'
models.ensure_member(admin_uid, 'แอดมิน')
models.get_conn().execute("UPDATE members SET is_admin=1 WHERE line_user_id=?", (admin_uid,))
models.get_conn().commit()
models.ensure_member(user1, 'คนแดง'); models.ensure_member(user2, 'คนเงิน')
m1 = models.ensure_member(user1, 'คนแดง')
m2 = models.ensure_member(user2, 'คนเงิน')

# ประเภทลูกค้า
models.set_member_type(m1, 'credit')
models.set_member_type(m2, 'cash')
assert models.get_member_type(m1) == 'credit'
assert models.get_member_type(m2) == 'cash'
print('member type ok')

# เติมเครดิต
models.adjust_credit(m1, 10000)
models.adjust_credit(m2, 8000)

models.set_room_type('room_play', 'play', 'test')
models.set_room_type('room_admin', 'admin', 'test')
models.set_room_type('room_finance', 'finance', 'test')

# เปิดราคา: แดงต่อ 5/2, เงินรอง 3/1, รับฝั่งละ 3000 (แยกมุม)
board = calc.parse_board_from_text('ด 52  ง 31\nรับ3000')
assert board is not None
assert board.red is not None and board.red.is_fav is False, 'แดง 5/2 เป็นรอง (จ่ายมากกว่า)'
assert board.blue is not None and board.blue.is_fav is True, 'เงิน 3/1 เป็นต่อ (จ่ายน้อยกว่า)'
assert board.accept == 3000

msgs = bot.handle_message(admin_uid, 'แอดมิน', 'ด 52  ง 31\nรับ3000', room_id='room_play')
print('board msg:', msgs[0].get('type'), msgs[0].get('altText', ''))
assert msgs[0].get('type') == 'flex'

match_id = models.get_open_match()

# แทง 6000 (เกินป้าย) - ต้องติดแค่ 3000
for uid, name, text, expect in [('Uuser1', 'คนแดง', 'ด6000', 3000),
                                 ('Uuser2', 'คนเงิน', 'ง6000', 3000)]:
    m = models.ensure_member(uid, name)
    models.adjust_credit(m, 10000)
    out = bot.handle_message(uid, name, text, room_id='room_play')
    assert 'ติด 3,000' in out[0]['text'], f'expected ติด 3,000 in {out[0]["text"]}'
    print('bet ok:', text, '->', out[0]['text'])

# ง500ด ต้องไม่ติด (กฏเดิม)
out = bot.handle_message('Uuser1', 'คนแดง', 'ง500ด', room_id='room_play')
assert out == [], f'expected no reply for ง500ด, got {out}'
print('ง500ด block ok')

# cc คาดการณ์: คนแดง แทงแดง 3000 ราคา 5/2 (ต่อ) → ถ้ายกชนะ: 3000*2/5=1200
msgs = bot._handle_realtime_summary(m1)
txt = '\n'.join(m['text'] for m in msgs)
assert 'คาดการณ์' in txt or 'คาดการณ์ยอด' in txt or any('คาดการณ์' in m.get('text','') for m in msgs), txt
print('cc projection ok:', txt[:300])

# ยกบิลรายคน
bets = models.list_bets(match_id=match_id, member_id=m1)
bet_id = bets[0]['id']
out = bot.handle_message('Uuser1', 'คนแดง', f'ยกบิล {bet_id}', room_id='room_finance')
assert 'ยกเลิกระบิล' in out[0]['text'], out
assert models.get_member_credit(m1) == 20000, f'credit should be restored: {models.get_member_credit(m1)}'
print('cancel bet by id ok:', out[0]['text'])

# แทงใหม่แล้วประกาศผล
models.adjust_credit(m1, 3000)
bot.handle_message('Uuser1', 'คนแดง', 'ด6000', room_id='room_play')
out = bot.handle_message(admin_uid, 'แอดมิน', 'dd', room_id='room_admin')
print('result msg type:', out[0].get('type'), 'alt:', out[0].get('altText'))

# ตรวจ payout: หลังยกบิล เครดิต = 20000, เติม 3000 แทงแดง 6000 (ติด 3000) เหลือ 20000, ชนะคืน 3000+7500 = 30500
c1 = models.get_member_credit(m1)
assert c1 == 30500, f'แดงควรได้ 30500 แต่ได้ {c1}'
print(f'แดงรองชนะ payout ok: {c1:,.0f}')

# คนเงินแพ้: เครดิต = 8000+10000-3000 = 15000
c2 = models.get_member_credit(m2)
assert c2 == 15000, f'เงินแพ้ควรเหลือ 15000 แต่เหลือ {c2}'
print(f'เงินแพ้ ok: {c2:,.0f}')

# เคสเงินชนะ
os.remove(DB)
models.init_db()
models.ensure_admin('Uadmin')
models.set_member_type(models.find_member('Uadmin'), 'credit')
ma = models.find_member('Uadmin')
bot.handle_message('Uadmin', 'แอด', 'ด 21  ง 53\nรับ2000', room_id='room_play')
mid = models.get_open_match()
models.adjust_credit(ma, 20000)
bot.handle_message('Uadmin', 'แอด', 'ง2000', room_id='room_play')
bot.handle_message('Uadmin', 'แอด', 'ff', room_id='room_admin')
# ง53 = เงินรอง 5/3 แทง 2000 → profit = 2000*5/3 = 3333.33
c = models.get_member_credit(ma)
expected = 20000 - 2000 + 2000 + 3333.33
assert abs(c - expected) < 0.01, f'เงินรองชนะ: expected {expected}, got {c}'
print(f'เงินรองชนะ ok: {c:,.0f}')

# สรุปรายวัน
s = models.get_daily_summary()
print('daily summary ok:', s)

# duplicate keywords fix
from app import models as m2_mod
m2_mod.add_keyword('t_f23', 'f23', 'resp1', upsert=True)
m2_mod.add_keyword('t_f23', 'f23', 'resp2', upsert=True)
cnt = m2_mod.get_conn().execute("SELECT COUNT(*) FROM keywords WHERE title='t_f23'").fetchone()[0]
assert cnt == 1, f'expected 1 keyword, got {cnt}'
print('upsert keyword ok, no duplicate')

print('\n=== ALL E2E TESTS PASSED ===')
