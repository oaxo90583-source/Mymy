# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ADMIN = "Uadmin00000000000000000000000000"
os.environ["LINE_ADMIN_IDS"] = ADMIN

from app import bot  # noqa: E402
print("ADMIN_IDS in module:", bot.ADMIN_IDS)
print("is_admin(ADMIN):", bot.is_admin(ADMIN))
print("is_admin(unknown):", bot.is_admin("Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"))
