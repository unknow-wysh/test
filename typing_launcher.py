#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打字练习独立启动器
由 exam_system.py 通过 subprocess 调用，避免 Tkinter 线程冲突（Calling Tcl from different apartment）
"""
import sys
import os

# 确保能找到 word_sprite 模块
if getattr(sys, 'frozen', False):
    _root = sys._MEIPASS
else:
    _root = os.path.dirname(os.path.abspath(__file__))

if _root not in sys.path:
    sys.path.insert(0, _root)

from word_sprite import Game_Main, Game_Info
Game_Main.center_pos()
from word_sprite.Game_View import GameStartWin
GameStartWin(title="打字练习").run()
