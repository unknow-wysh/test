# -*- coding: utf-8 -*-
"""WordSprite 打字游戏独立启动器"""
import sys, os

# 动态计算当前脚本所在目录，兼容任意安装路径
_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from word_sprite import Game_Main, Game_Info
Game_Main.center_pos()
from word_sprite.Game_View import GameStartWin
GameStartWin(title="打字练习").run()
