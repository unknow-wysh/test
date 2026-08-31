# -*- coding: utf-8 -*-
"""
春考网络技术技能模拟系统 — 教师端局域网考试管理界面
基于 tkinter，集成 network.Server，提供考场控制、题目选择、学生监控、成绩汇总功能。

启动方式:
    python teacher_panel.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import os
import sys
import json
import re
import base64
import zipfile
import io
import shutil
import socket
import threading
import time
import tempfile
import hashlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# PyInstaller 路径处理 - 必须在所有文件操作之前
def _get_base_path():
    """获取程序运行时的基础路径（兼容PyInstaller打包）"""
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

BASE_PATH = _get_base_path()
os.chdir(BASE_PATH)  # 设置工作目录

# 导入加密工具
try:
    from crypto_utils import get_crypto_manager, load_encrypted_json, save_encrypted_json
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    print("警告: 加密模块未找到，将使用普通文件读取")
# 全局配置加载
def _load_teacher_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "network_port": 8888,
        "listen_backlog": 50,
        "score_mapping": {
            "single_choice": 2, "fill_blank": 10, "programming": 20,
            "web_design": 30, "network_device": 15, "graphic_design": 30
        },
        "ai_max_tokens": 8192,
    }

TEACHER_CONFIG = _load_teacher_config()
NETWORK_PORT = TEACHER_CONFIG.get("network_port", 8888)
LISTEN_BACKLOG = TEACHER_CONFIG.get("listen_backlog", 50)
MAX_STUDENT_UPLOAD_SIZE = int(
    TEACHER_CONFIG.get("max_student_upload_mb", 1024)
) * 1024 * 1024

# 日志系统
try:
    from logger import get_logger
    logger = get_logger("teacher_panel")
except ImportError:
    import logging
    logger = logging.getLogger("teacher_panel")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s"))
        logger.addHandler(ch)

# 导入网络通信层
import network

# ============================================================
# 路径配置（必须在 load_accounts 之前定义）
# ============================================================
EXAM_DIR = os.path.dirname(os.path.abspath(__file__))

# 用户可写数据目录（避免 C:\\Program Files 标准用户无写入权限）
_la = os.environ.get("LOCALAPPDATA")
_USER_DATA_DIR = os.path.join(_la, "CKWLYDT") if _la else os.path.join(os.path.expanduser("~"), "CKWLYDT")
try:
    os.makedirs(_USER_DATA_DIR, exist_ok=True)
except Exception:
    pass

# 导入账号管理
try:
    from exam_system import load_accounts
except ImportError:
    # 独立运行时使用本地加载
    def load_accounts():
        accounts_file = os.path.join(_USER_DATA_DIR, "accounts.json")
        if os.path.exists(accounts_file):
            with open(accounts_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
BANK_FILE = os.path.join(_USER_DATA_DIR, "question_bank.json")
RESULTS_DIR = os.path.join(_USER_DATA_DIR, "exam_results")
SUBMISSIONS_DIR = os.path.join(_USER_DATA_DIR, "submissions")  # 学生文件提交目录
EXAM_FILES_DIR = os.path.join(EXAM_DIR, "exam_files")

def _safe_material_name(value, default="material"):
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", text).strip("_")
    return text[:40] or default

def _path_is_inside(child, parent):
    try:
        child_abs = os.path.abspath(child)
        parent_abs = os.path.abspath(parent)
        return os.path.commonpath([child_abs, parent_abs]) == parent_abs
    except Exception:
        return False

def _resolve_material_source(path, base_dir=None):
    if not path or not str(path).strip():
        return ""
    raw_path = str(path).strip()
    candidates = []
    if os.path.isabs(raw_path):
        candidates.append(raw_path)
    else:
        if base_dir:
            candidates.append(os.path.join(base_dir, raw_path))
        candidates.append(os.path.join(EXAM_DIR, raw_path))
    for candidate in candidates:
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return ""

def _copy_material_folder_into_system(question, question_type, base_dir=None):
    if question_type not in ("web_design", "graphic_design"):
        return ""
    source_folder = _resolve_material_source(question.get("folder_path", ""), base_dir)
    if not source_folder:
        return ""
    if _path_is_inside(source_folder, EXAM_DIR):
        question["folder_path"] = os.path.relpath(source_folder, EXAM_DIR)
        return question["folder_path"]

    content = str(question.get("content", "")).strip()
    digest_text = "|".join([content, source_folder, str(question.get("id", ""))])
    digest = hashlib.md5(digest_text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    source_name = _safe_material_name(os.path.basename(source_folder), question_type)
    qid_name = _safe_material_name(question.get("id") or len(content), "q")
    target_root = os.path.join(EXAM_FILES_DIR, "imported_materials", question_type)
    target_dir = os.path.join(target_root, f"{qid_name}_{source_name}_{digest}")
    try:
        os.makedirs(target_root, exist_ok=True)
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)
        shutil.copytree(source_folder, target_dir)

        def _remap_material_file(value):
            if not value or not isinstance(value, str):
                return value
            candidates = [value] if os.path.isabs(value) else [
                os.path.join(base_dir or EXAM_DIR, value),
                os.path.join(source_folder, value),
            ]
            for src_file in candidates:
                if os.path.isfile(src_file) and _path_is_inside(src_file, source_folder):
                    rel_inside = os.path.relpath(src_file, source_folder)
                    return os.path.relpath(os.path.join(target_dir, rel_inside), EXAM_DIR)
            return value

        question["folder_path"] = os.path.relpath(target_dir, EXAM_DIR)
        if question.get("image"):
            question["image"] = _remap_material_file(question.get("image"))
        if isinstance(question.get("images"), list):
            question["images"] = [_remap_material_file(item) for item in question.get("images", [])]
        return question["folder_path"]
    except Exception as e:
        logger.error(f"[素材内置] 复制题目素材文件夹失败: {e}")
        return ""

def _get_reference_folder_value(question):
    for key in ("reference_folder", "reference_answer_folder", "answer_folder"):
        value = question.get(key)
        if value:
            return key, value
    return "reference_folder", ""

def _copy_reference_folder_into_system(question, question_type, base_dir=None):
    if question_type not in ("web_design", "graphic_design"):
        return ""
    field_name, raw_folder = _get_reference_folder_value(question)
    source_folder = _resolve_material_source(raw_folder, base_dir)
    if not source_folder:
        return ""
    if _path_is_inside(source_folder, EXAM_DIR):
        question[field_name] = os.path.relpath(source_folder, EXAM_DIR)
        question["reference_folder"] = question[field_name]
        return question["reference_folder"]

    content = str(question.get("content", "")).strip()
    digest_text = "|".join([content, source_folder, str(question.get("id", "")), "reference"])
    digest = hashlib.md5(digest_text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    source_name = _safe_material_name(os.path.basename(source_folder), "reference")
    qid_name = _safe_material_name(question.get("id") or len(content), "q")
    target_root = os.path.join(EXAM_FILES_DIR, "reference_answers", question_type)
    target_dir = os.path.join(target_root, f"{qid_name}_{source_name}_{digest}")
    try:
        os.makedirs(target_root, exist_ok=True)
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)
        shutil.copytree(source_folder, target_dir)
        rel_path = os.path.relpath(target_dir, EXAM_DIR)
        question[field_name] = rel_path
        question["reference_folder"] = rel_path
        return rel_path
    except Exception as e:
        logger.error(f"[答案内置] 复制参考答案文件夹失败: {e}")
        return ""

def _safe_relative_import_path(value):
    rel = str(value or "").strip().replace("/", os.sep).replace("\\", os.sep)
    rel = rel.lstrip("\\/")
    if not rel or os.path.isabs(rel):
        return ""
    norm = os.path.normpath(rel)
    if norm == "." or norm.startswith("..") or os.path.isabs(norm):
        return ""
    return norm

def _resolve_import_file_source(path, base_dir=None):
    raw_path = str(path or "").strip()
    if not raw_path:
        return ""
    candidates = []
    if os.path.isabs(raw_path):
        candidates.append(raw_path)
    else:
        if base_dir:
            candidates.append(os.path.join(base_dir, raw_path))
        candidates.append(os.path.join(EXAM_DIR, raw_path))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return ""

def _copy_import_file_into_system(question, field_name, base_dir=None, fallback_dir="exam_files"):
    source_file = _resolve_import_file_source(question.get(field_name, ""), base_dir)
    if not source_file:
        return ""
    if _path_is_inside(source_file, EXAM_DIR):
        question[field_name] = os.path.relpath(source_file, EXAM_DIR)
        return question[field_name]

    rel_value = _safe_relative_import_path(question.get(field_name, ""))
    if not rel_value:
        rel_value = os.path.join(fallback_dir, os.path.basename(source_file))
    target_file = os.path.join(EXAM_DIR, rel_value)
    try:
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        if os.path.abspath(source_file) != os.path.abspath(target_file):
            shutil.copy2(source_file, target_file)
        question[field_name] = os.path.relpath(target_file, EXAM_DIR)
        return question[field_name]
    except Exception as e:
        logger.error(f"[资源内置] 复制导入文件失败 {field_name}: {e}")
        return ""

def _copy_network_device_files_into_system(question, base_dir=None):
    _copy_import_file_into_system(
        question, "image", base_dir, os.path.join("question_images", "network_device_imported")
    )
    _copy_import_file_into_system(
        question, "answer_doc", base_dir, os.path.join("exam_files", "imported", "network_device_docs")
    )

def _safe_extract_zip(zip_file, target_dir, max_total_size=None):
    """安全解压 zip：限制总大小，并禁止解压到目标目录之外。"""
    target_abs = os.path.abspath(target_dir)
    total_size = 0
    for info in zip_file.infolist():
        if info.is_dir():
            continue
        total_size += info.file_size
        if max_total_size is not None and total_size > max_total_size:
            raise ValueError(f"压缩包过大: {total_size} 字节")
        dest_abs = os.path.abspath(os.path.join(target_abs, info.filename))
        if dest_abs != target_abs and not dest_abs.startswith(target_abs + os.sep):
            raise ValueError(f"压缩包包含不安全路径: {info.filename}")
    zip_file.extractall(target_abs)

# ============================================================
# 配色方案（与 exam_system.py 保持一致）
# ============================================================
COLOR_BG_TOP = "#1a6fb5"
COLOR_BG_MAIN = "#e8f0fe"
COLOR_WHITE = "#ffffff"
COLOR_TEXT_DARK = "#1a3a5c"
COLOR_TEXT_LIGHT = "#ffffff"
COLOR_ACCENT = "#2196F3"
COLOR_BTN = "#1976D2"
COLOR_BTN_HOVER = "#1565C0"
COLOR_SUCCESS = "#4CAF50"
COLOR_WARNING = "#FF9800"
COLOR_DANGER = "#f44336"
COLOR_SIDEBAR = "#0d3b66"
COLOR_BG_FRAME = "#f5f7fa"
COLOR_BORDER = "#c8d6e5"
COLOR_ONLINE = "#4CAF50"
COLOR_OFFLINE = "#9e9e9e"
COLOR_SUBMITTED = "#2196F3"

# ============================================================
# 字体配置
# ============================================================
FONT_TITLE = ("微软雅黑", 18, "bold")
FONT_SUBTITLE = ("微软雅黑", 14, "bold")
FONT_NORMAL = ("微软雅黑", 11)
FONT_SMALL = ("微软雅黑", 9)
FONT_TREE = ("微软雅黑", 10)
FONT_BOLD = ("微软雅黑", 11, "bold")

# ============================================================
# 题型名称映射
# ============================================================
QUESTION_TYPE_NAMES = {
    "single_choice": "单选题",
    "fill_blank": "填空题",
    "programming": "编程题",
    "web_design": "网页制作",
    "network_device": "网络设备",
    "graphic_design": "图形图像",
}

QUESTION_TYPE_ORDER = [
    "single_choice",
    "fill_blank",
    "programming",
    "web_design",
    "network_device",
    "graphic_design",
]


# ============================================================
# 工具函数
# ============================================================
def get_local_ip():
    """自动获取本机局域网 IP 地址。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except OSError:
            return "127.0.0.1"


def load_question_bank():
    """加载题库 JSON 文件，返回字典。支持加密文件。"""
    if os.path.exists(BANK_FILE):
        try:
            if CRYPTO_AVAILABLE:
                # 使用加密加载
                return load_encrypted_json(BANK_FILE)
            else:
                # 普通加载
                with open(BANK_FILE, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError, Exception) as e:
            print(f"加载题库失败: {e}")
            pass
    return {key: [] for key in QUESTION_TYPE_ORDER}


def truncate_text(text, max_len=30):
    """截断文本，超出长度加省略号。"""
    text = text.replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


# ============================================================
# 圆角按钮辅助类
# ============================================================
class RoundedButton(tk.Canvas):
    """自定义圆角按钮，通过 Canvas 绘制圆角矩形 + 文字。

    支持 hover 颜色变化和点击回调。
    """

    def __init__(
        self,
        parent,
        text="",
        width=120,
        height=36,
        radius=6,
        bg=COLOR_BTN,
        fg=COLOR_TEXT_LIGHT,
        hover_bg=COLOR_BTN_HOVER,
        font=FONT_NORMAL,
        command=None,
        **kwargs,
    ):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, **kwargs)
        self._text = text
        self._width = width
        self._height = height
        self._radius = radius
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg
        self._font = font
        self._command = command
        self._enabled = True

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

        self._draw(bg)

    def _draw(self, fill_color):
        """绘制圆角矩形按钮。"""
        self.delete("all")
        r = self._radius
        w = self._width
        h = self._height

        self.create_arc((0, 0, 2 * r, 2 * r), start=90, extent=90,
                        fill=fill_color, outline=fill_color)
        self.create_arc((w - 2 * r, 0, w, 2 * r), start=0, extent=90,
                        fill=fill_color, outline=fill_color)
        self.create_arc((w - 2 * r, h - 2 * r, w, h), start=270, extent=90,
                        fill=fill_color, outline=fill_color)
        self.create_arc((0, h - 2 * r, 2 * r, h), start=180, extent=90,
                        fill=fill_color, outline=fill_color)
        self.create_rectangle((r, 0, w - r, h), fill=fill_color, outline=fill_color)
        self.create_rectangle((0, r, w, h - r), fill=fill_color, outline=fill_color)

        self.create_text(
            w // 2, h // 2, text=self._text,
            fill=self._fg, font=self._font,
        )

    def _on_enter(self, event):
        if self._enabled:
            self._draw(self._hover_bg)

    def _on_leave(self, event):
        if self._enabled:
            self._draw(self._bg)

    def _on_click(self, event):
        if self._enabled and self._command:
            self._command()

    def config(self, text=None, command=None, state=None, bg=None):
        """模拟 Button.config 方法。"""
        if text is not None:
            self._text = text
        if command is not None:
            self._command = command
        if state is not None:
            self._enabled = (state == "normal")
        if bg is not None:
            self._bg = bg
        self._draw(self._bg)

    def configure(self, **kwargs):
        """兼容标准 tkinter Button.configure() API。"""
        self.config(**kwargs)


# ============================================================
# 教师端主界面
# ============================================================
class TeacherPanel:
    """教师端局域网考试管理界面。

    提供考场控制、题目选择、学生实时监控、成绩汇总四大功能模块。
    集成 network.Server 实现与学生端的 TCP 通信。
    """

    def __init__(self):
        """初始化窗口并构建全部 UI。"""
        self.root = tk.Tk()
        self.root.title("春考网络技术技能模拟系统 — 教师端")
        self.root.geometry("1200x800")
        self.root.configure(bg=COLOR_BG_MAIN)

        # 窗口居中
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - 1200) // 2
        y = (sh - 800) // 2
        self.root.geometry(f"1200x800+{x}+{y}")
        self.root.minsize(1000, 700)

        # 网络服务
        self.server = None  # network.Server 实例
        self.is_listening = False
        self.allow_join = tk.BooleanVar(value=True)

        # 题库数据
        self.question_bank = load_question_bank()
        # 题目复选变量: {type_key: {qid: tk.BooleanVar}}
        self.question_vars = {}

        # 当前考试题目列表
        self.exam_questions = []
        self.exam_in_progress = False
        self.current_exam_data = None
        self.current_exam_start_ts = None

        # 定时开始倒计时状态
        self.countdown_active = False
        self.countdown_remaining = 0
        self.countdown_after_id = None

        # 成绩汇总数据
        self.score_data = []
        self._ui_thread_id = threading.get_ident()
        self._submit_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="submit-worker")
        self._submit_pending = 0
        self._submit_lock = threading.Lock()
        self._submit_file_streams = {}
        self._submit_file_lock = threading.Lock()
        self._teacher_task_lock = threading.Lock()
        self._teacher_task_name = ""

        # 提交文件保存路径 - 从 exam_config.json 加载
        cfg_path = os.path.join(_USER_DATA_DIR, "exam_config.json")
        folder_cfg = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    folder_cfg = json.load(f)
            except Exception:
                pass
        self.submissions_dir = folder_cfg.get("submissions_dir", "")

        # 构建界面
        self._build_ui()

        # 自动启动监听
        self.root.after(300, self._start_listen)

        # 消息处理器注册标志
        self._msg_handlers_registered = False

        # 定时刷新定时器 ID
        self._refresh_timer_id = None

        # 窗口关闭处理
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ============================================================
    # UI 构建
    # ============================================================
    def _build_ui(self):
        """构建整体界面布局。

        结构:
            顶部: 考场控制区
            下方左侧 (40%): 题目选择区
            下方右侧 (60%): 学生监控区(上) + 成绩汇总区(下)
        """
        # --- 顶部：考场控制区 ---
        self._create_exam_control_area()

        # --- 下方：左右分栏 ---
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # 左侧：题目选择区
        left_frame = tk.Frame(main_paned, bg=COLOR_BG_MAIN)
        main_paned.add(left_frame, weight=4)
        self._create_question_selection_area(left_frame)

        # 右侧：上下分栏
        right_frame = tk.Frame(main_paned, bg=COLOR_BG_MAIN)
        main_paned.add(right_frame, weight=6)

        right_paned = ttk.PanedWindow(right_frame, orient=tk.VERTICAL)
        right_paned.pack(fill=tk.BOTH, expand=True)

        monitor_frame = tk.Frame(right_paned, bg=COLOR_BG_MAIN)
        right_paned.add(monitor_frame, weight=5)
        self._create_student_monitor_area(monitor_frame)

        score_frame = tk.Frame(right_paned, bg=COLOR_BG_MAIN)
        right_paned.add(score_frame, weight=3)
        self._create_score_summary_area(score_frame)

    # ============================================================
    # 1. 考场控制区（顶部）
    # ============================================================
    def _create_exam_control_area(self):
        """创建顶部考场控制区。"""
        top_frame = tk.Frame(self.root, bg=COLOR_BG_TOP, height=110)
        top_frame.pack(fill=tk.X, padx=8, pady=8)
        top_frame.pack_propagate(False)

        # 第一行：IP、端口、监听按钮
        row1 = tk.Frame(top_frame, bg=COLOR_BG_TOP)
        row1.pack(fill=tk.X, padx=15, pady=(12, 4))

        tk.Label(row1, text="本机 IP:", bg=COLOR_BG_TOP,
                 fg=COLOR_TEXT_LIGHT, font=FONT_NORMAL).pack(
            side=tk.LEFT, padx=(0, 5))

        local_ip = get_local_ip()
        self.ip_label = tk.Label(
            row1, text=local_ip, bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
            font=FONT_NORMAL, width=16, relief=tk.SUNKEN, bd=1)
        self.ip_label.pack(side=tk.LEFT, padx=(0, 15))

        tk.Label(row1, text="端口:", bg=COLOR_BG_TOP,
                 fg=COLOR_TEXT_LIGHT, font=FONT_NORMAL).pack(
            side=tk.LEFT, padx=(0, 5))

        self.port_var = tk.StringVar(value="8888")
        tk.Entry(row1, textvariable=self.port_var, font=FONT_NORMAL,
                 width=8, justify=tk.CENTER).pack(side=tk.LEFT, padx=(0, 15))

        self.allow_cb = tk.Checkbutton(
            row1, text="允许加入", variable=self.allow_join,
            bg=COLOR_BG_TOP, fg=COLOR_TEXT_LIGHT, font=FONT_NORMAL,
            selectcolor=COLOR_BG_TOP, activebackground=COLOR_BG_TOP,
            activeforeground=COLOR_TEXT_LIGHT)
        self.allow_cb.pack(side=tk.LEFT, padx=(0, 15))

        tk.Label(row1, text="考试时长(分):", bg=COLOR_BG_TOP,
                 fg=COLOR_TEXT_LIGHT, font=FONT_NORMAL).pack(
            side=tk.LEFT, padx=(0, 5))

        self.duration_var = tk.StringVar(value="60")
        tk.Entry(row1, textvariable=self.duration_var, font=FONT_NORMAL,
                 width=6, justify=tk.CENTER).pack(side=tk.LEFT, padx=(0, 15))

        tk.Label(row1, text="延迟(分):", bg=COLOR_BG_TOP,
                 fg=COLOR_TEXT_LIGHT, font=FONT_NORMAL).pack(
            side=tk.LEFT, padx=(0, 5))

        self.delay_var = tk.StringVar(value="0")
        tk.Entry(row1, textvariable=self.delay_var, font=FONT_NORMAL,
                 width=6, justify=tk.CENTER).pack(side=tk.LEFT, padx=(0, 15))

        self.student_count_label = tk.Label(
            row1, text="已连接: 0 人", bg=COLOR_BG_TOP,
            fg=COLOR_TEXT_LIGHT, font=FONT_NORMAL)
        self.student_count_label.pack(side=tk.RIGHT)

        self.status_label = tk.Label(
            row1, text="未监听", bg=COLOR_BG_TOP, fg="#FFAB40", font=FONT_NORMAL)
        self.status_label.pack(side=tk.RIGHT, padx=(0, 20))

        # 第二行：考试控制 + 题目管理按钮
        row2 = tk.Frame(top_frame, bg=COLOR_BG_TOP)
        row2.pack(fill=tk.X, padx=15, pady=(4, 10))

        self.start_exam_btn = RoundedButton(
            row2, text="开始考试", width=120, height=34,
            bg="#FF9800", hover_bg="#F57C00",
            command=self._start_exam)
        self.start_exam_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.schedule_btn = RoundedButton(
            row2, text="定时开始", width=120, height=34,
            bg="#009688", hover_bg="#00796B",
            command=self._start_countdown)
        self.schedule_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.cancel_schedule_btn = RoundedButton(
            row2, text="取消定时", width=120, height=34,
            bg="#757575", hover_bg="#616161",
            command=self._cancel_countdown)

        self.force_btn = RoundedButton(
            row2, text="强制收卷", width=120, height=34,
            bg=COLOR_DANGER, hover_bg="#E53935",
            command=self._force_submit_all)
        self.force_btn.pack(side=tk.LEFT, padx=(0, 10))

        sep = tk.Frame(row2, width=1, bg="#555555")
        sep.pack(side=tk.LEFT, fill=tk.Y, padx=(5, 5), pady=4)

        self.add_question_btn = RoundedButton(
            row2, text="逐题添加", width=110, height=34,
            bg="#2196F3", hover_bg="#1976D2",
            command=self._open_add_question_dialog)
        self.add_question_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.distribute_questions_btn = RoundedButton(
            row2, text="下发题目", width=110, height=34,
            bg="#FF5722", hover_bg="#E64A19",
            command=self._distribute_questions)
        self.distribute_questions_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.import_questions_btn = RoundedButton(
            row2, text="导入题库", width=110, height=34,
            bg="#607D8B", hover_bg="#455A64",
            command=self._import_questions_to_bank)
        self.import_questions_btn.pack(side=tk.LEFT, padx=(0, 10))

        sep2 = tk.Frame(row2, width=1, bg="#555555")
        sep2.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8), pady=4)

        self.folder_settings_btn = RoundedButton(
            row2, text="文件夹路径", width=100, height=34,
            bg="#546E7A", hover_bg="#455A64",
            command=self._open_folder_settings)
        self.folder_settings_btn.pack(side=tk.LEFT)

    # ============================================================
    # 2. 题目选择区（左侧，40%）
    # ============================================================
    def _create_question_selection_area(self, parent):
        """创建左侧题目选择区。"""
        outer = tk.LabelFrame(
            parent, text="题目选择", font=FONT_SUBTITLE,
            bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, padx=5, pady=5)
        outer.pack(fill=tk.BOTH, expand=True)

        # 顶部全选/全不选按钮
        btn_row = tk.Frame(outer, bg=COLOR_BG_MAIN)
        btn_row.pack(fill=tk.X, pady=(0, 5))

        tk.Button(btn_row, text="全部选中", font=FONT_SMALL,
                  bg=COLOR_BTN, fg=COLOR_TEXT_LIGHT,
                  command=self._select_all_questions).pack(
            side=tk.LEFT, padx=(0, 5))

        tk.Button(btn_row, text="全部取消", font=FONT_SMALL,
                  bg="#757575", fg=COLOR_TEXT_LIGHT,
                  command=self._deselect_all_questions).pack(side=tk.LEFT)

        self.question_stat_label = tk.Label(
            btn_row, text="已选 0 题 / 总分 0 分",
            bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, font=FONT_SMALL)
        self.question_stat_label.pack(side=tk.RIGHT)

        # Canvas + Scrollbar 可滚动区域
        canvas_frame = tk.Frame(outer, bg=COLOR_BG_MAIN)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.q_canvas = tk.Canvas(canvas_frame, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL,
                                 command=self.q_canvas.yview)
        self.q_scroll_frame = tk.Frame(self.q_canvas, bg=COLOR_BG_MAIN)

        self.q_scroll_frame.bind(
            "<Configure>",
            lambda e: self.q_canvas.configure(
                scrollregion=self.q_canvas.bbox("all")))

        self.q_canvas.create_window((0, 0), window=self.q_scroll_frame, anchor="nw")
        self.q_canvas.configure(yscrollcommand=scrollbar.set)

        self.q_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮绑定
        self.q_canvas.bind("<Enter>", lambda e: self.q_canvas.bind_all(
            "<MouseWheel>",
            lambda ev: self.q_canvas.yview_scroll(
                int(-1 * (ev.delta / 120)), "units")))
        self.q_canvas.bind("<Leave>",
                           lambda e: self.q_canvas.unbind_all("<MouseWheel>"))

        # 为每个题型创建折叠区域
        self._question_sections = {}
        self._build_question_sections()
        self._update_question_stats()

    def _build_question_sections(self):
        """构建每个题型的折叠选择题区域。"""
        for type_key in QUESTION_TYPE_ORDER:
            questions = self.question_bank.get(type_key, [])
            if not questions:
                continue

            type_name = QUESTION_TYPE_NAMES.get(type_key, type_key)

            section_frame = tk.Frame(
                self.q_scroll_frame, bg=COLOR_BG_MAIN, bd=1, relief=tk.GROOVE)
            section_frame.pack(fill=tk.X, pady=2)

            # 标题栏
            title_bar = tk.Frame(section_frame, bg=COLOR_BTN, cursor="hand2")
            title_bar.pack(fill=tk.X)

            is_expanded = tk.BooleanVar(value=True)
            title_text = f"▼ {type_name}（{len(questions)} 道）"

            title_btn = tk.Label(
                title_bar, text=title_text, bg=COLOR_BTN,
                fg=COLOR_TEXT_LIGHT, font=FONT_NORMAL, anchor=tk.W)
            title_btn.pack(fill=tk.X, padx=8, pady=4)

            # 全选按钮
            tk.Button(
                title_bar, text="全选", font=FONT_SMALL,
                bg=COLOR_ACCENT, fg=COLOR_TEXT_LIGHT,
                command=lambda tk=type_key: self._select_type_all(tk)
            ).pack(side=tk.RIGHT, padx=5, pady=2)

            # 内容区
            content_frame = tk.Frame(section_frame, bg=COLOR_WHITE)
            content_frame.pack(fill=tk.X)

            inner_canvas = tk.Canvas(
                content_frame, bg=COLOR_WHITE, highlightthickness=0,
                height=min(len(questions) * 28, 200))
            inner_scroll = tk.Scrollbar(
                content_frame, orient=tk.VERTICAL, command=inner_canvas.yview)
            inner_frame = tk.Frame(inner_canvas, bg=COLOR_WHITE)

            inner_frame.bind(
                "<Configure>",
                lambda e, c=inner_canvas: c.configure(scrollregion=c.bbox("all")))
            inner_canvas.create_window((0, 0), window=inner_frame, anchor="nw")
            inner_canvas.configure(yscrollcommand=inner_scroll.set)

            inner_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            inner_scroll.pack(side=tk.RIGHT, fill=tk.Y)

            # 每道题创建复选框
            self.question_vars[type_key] = {}
            for q in questions:
                qid = q.get("id", 0)
                var = tk.BooleanVar(value=False)
                self.question_vars[type_key][qid] = var

                score = q.get("score", 0)
                content = truncate_text(q.get("content", ""), 28)
                label_text = f"[{qid}] {content} ({score}分)"

                tk.Checkbutton(
                    inner_frame, text=label_text, variable=var,
                    bg=COLOR_WHITE, anchor=tk.W, font=FONT_SMALL,
                    command=self._update_question_stats
                ).pack(fill=tk.X, padx=5, pady=1)

            # 折叠/展开逻辑
            def make_toggle(cf, btn, iv, tn, nq):
                def toggle(e=None):
                    if iv.get():
                        cf.pack_forget()
                        iv.set(False)
                        btn.configure(text=f"▶ {tn}（{nq} 道）")
                    else:
                        cf.pack(fill=tk.X)
                        iv.set(True)
                        btn.configure(text=f"▼ {tn}（{nq} 道）")
                return toggle

            toggle_fn = make_toggle(
                content_frame, title_btn, is_expanded, type_name, len(questions))
            title_bar.bind("<Button-1>", toggle_fn)
            title_btn.bind("<Button-1>", toggle_fn)

            self._question_sections[type_key] = (
                section_frame, content_frame, title_btn, is_expanded)

    def _select_type_all(self, type_key):
        """全选或取消全选某个题型。"""
        if type_key not in self.question_vars:
            return
        vars_dict = self.question_vars[type_key]
        all_selected = all(v.get() for v in vars_dict.values())
        new_state = not all_selected
        for v in vars_dict.values():
            v.set(new_state)
        self._update_question_stats()

    def _select_all_questions(self):
        """全部选中所有题目。"""
        for type_key in self.question_vars:
            for v in self.question_vars[type_key].values():
                v.set(True)
        self._update_question_stats()

    def _deselect_all_questions(self):
        """全部取消选中所有题目。"""
        for type_key in self.question_vars:
            for v in self.question_vars[type_key].values():
                v.set(False)
        self._update_question_stats()

    def _update_question_stats(self):
        """更新题目选择统计标签。"""
        total_count = 0
        total_score = 0
        for type_key in self.question_vars:
            questions = self.question_bank.get(type_key, [])
            q_map = {q.get("id"): q for q in questions}
            for qid, var in self.question_vars[type_key].items():
                if var.get():
                    total_count += 1
                    q = q_map.get(qid, {})
                    total_score += q.get("score", 0)
        self.question_stat_label.configure(
            text=f"已选 {total_count} 题 / 总分 {total_score} 分")

    def get_selected_questions(self):
        """获取当前选中的题目列表。

        Returns:
            list[dict]: 选中的题目，含 _type 和 _qid 字段
        """
        selected = []
        for type_key in self.question_vars:
            questions = self.question_bank.get(type_key, [])
            q_map = {q.get("id"): q for q in questions}
            for qid, var in self.question_vars[type_key].items():
                if var.get():
                    q = dict(q_map.get(qid, {}))
                    q["_type"] = type_key
                    q["type"] = type_key   # 直接设置 type，确保学生端题目类型正确
                    q["_qid"] = qid
                    selected.append(q)
        return selected

    # ============================================================
    # 3. 学生监控区（右侧上方，60%）
    # ============================================================
    def _create_student_monitor_area(self, parent):
        """创建学生实时监控区。"""
        outer = tk.LabelFrame(
            parent, text="学生监控", font=FONT_SUBTITLE,
            bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, padx=5, pady=5)
        outer.pack(fill=tk.BOTH, expand=True)

        columns = ("exam_id", "name", "ip", "status")
        self.monitor_tree = ttk.Treeview(
            outer, columns=columns, show="headings", height=8)

        self.monitor_tree.heading("exam_id", text="准考证号")
        self.monitor_tree.heading("name", text="姓名")
        self.monitor_tree.heading("ip", text="IP 地址")
        self.monitor_tree.heading("status", text="状态")

        self.monitor_tree.column("exam_id", width=130, anchor=tk.CENTER)
        self.monitor_tree.column("name", width=100, anchor=tk.CENTER)
        self.monitor_tree.column("ip", width=130, anchor=tk.CENTER)
        self.monitor_tree.column("status", width=70, anchor=tk.CENTER)

        tree_scroll = tk.Scrollbar(
            outer, orient=tk.VERTICAL, command=self.monitor_tree.yview)
        self.monitor_tree.configure(yscrollcommand=tree_scroll.set)

        self.monitor_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 右键菜单
        self._monitor_context_menu = tk.Menu(self.root, tearoff=0)
        self._monitor_context_menu.add_command(
            label="补时间", command=self._extend_time)
        self._monitor_context_menu.add_command(
            label="强制交卷", command=self._force_submit_one)
        self._monitor_context_menu.add_command(
            label="发送消息", command=self._send_message_to_student)

        self.monitor_tree.bind("<Button-3>", self._on_monitor_right_click)

    def _on_monitor_right_click(self, event):
        """右键点击弹出菜单。"""
        item = self.monitor_tree.identify_row(event.y)
        if item:
            self.monitor_tree.selection_set(item)
            self._monitor_context_menu.post(event.x_root, event.y_root)

    def _extend_time(self):
        """补时间对话框。"""
        selection = self.monitor_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中一名学生")
            return

        item = selection[0]
        values = self.monitor_tree.item(item, "values")
        if not values:
            return
        student_name = values[1]

        dialog = tk.Toplevel(self.root)
        dialog.title("补时间")
        dialog.geometry("360x180")
        dialog.resizable(False, False)
        dialog.configure(bg=COLOR_BG_MAIN)
        dialog.transient(self.root)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.root.winfo_x() + (1200 - 360) // 2
        y = self.root.winfo_y() + (800 - 180) // 2
        dialog.geometry(f"+{x}+{y}")

        tk.Label(dialog, text=f"为学生「{student_name}」延长考试时间",
                 bg=COLOR_BG_MAIN, font=FONT_NORMAL).pack(pady=(15, 5))

        entry_frame = tk.Frame(dialog, bg=COLOR_BG_MAIN)
        entry_frame.pack()
        tk.Label(entry_frame, text="延长", bg=COLOR_BG_MAIN,
                 font=FONT_NORMAL).pack(side=tk.LEFT)
        minutes_var = tk.StringVar(value="5")
        tk.Entry(entry_frame, textvariable=minutes_var, width=5,
                 font=FONT_NORMAL, justify=tk.CENTER).pack(side=tk.LEFT)
        tk.Label(entry_frame, text="分钟", bg=COLOR_BG_MAIN,
                 font=FONT_NORMAL).pack(side=tk.LEFT)

        def do_extend():
            try:
                mins = int(minutes_var.get())
            except ValueError:
                messagebox.showwarning("输入错误", "请输入有效的分钟数")
                return
            if self.server:
                self.server.send_to(
                    student_name, "TIME_EXTEND",
                    {"extend_minutes": mins, "name": student_name})
            self.status_label.configure(
                text=f"已为 {student_name} 延长 {mins} 分钟")
            dialog.destroy()

        tk.Button(dialog, text="确认", font=FONT_NORMAL,
                  bg=COLOR_BTN, fg=COLOR_TEXT_LIGHT,
                  command=do_extend).pack(pady=10)

    def _force_submit_one(self):
        """强制单个学生交卷。"""
        selection = self.monitor_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中一名学生")
            return

        item = selection[0]
        values = self.monitor_tree.item(item, "values")
        if not values:
            return
        student_name = values[1]

        if not messagebox.askyesno("确认", f"确定强制 {student_name} 交卷吗？"):
            return

        if self.server:
            self.server.force_submit(student_name)
        self.status_label.configure(
            text=f"已向 {student_name} 发送强制交卷指令")

    def _send_message_to_student(self):
        """向选中学生发送自定义消息。"""
        selection = self.monitor_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中一名学生")
            return

        item = selection[0]
        values = self.monitor_tree.item(item, "values")
        if not values:
            return
        student_name = values[1]

        dialog = tk.Toplevel(self.root)
        dialog.title("发送消息")
        dialog.geometry("420x240")
        dialog.resizable(True, True)
        dialog.minsize(380, 200)
        dialog.configure(bg=COLOR_BG_MAIN)
        dialog.transient(self.root)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.root.winfo_x() + (1200 - 420) // 2
        y = self.root.winfo_y() + (800 - 240) // 2
        dialog.geometry(f"+{x}+{y}")

        tk.Label(dialog, text=f"发送消息给「{student_name}」",
                 bg=COLOR_BG_MAIN, font=FONT_NORMAL).pack(pady=(15, 5))

        msg_text = tk.Text(dialog, height=4, font=FONT_NORMAL)
        msg_text.pack(fill=tk.X, padx=20, pady=5)

        def do_send():
            msg = msg_text.get("1.0", tk.END).strip()
            if not msg:
                return
            if self.server:
                self.server.send_to(
                    student_name, "BROADCAST_MSG",
                    {"message": msg, "name": student_name})
            self.status_label.configure(text=f"已向 {student_name} 发送消息")
            dialog.destroy()

        tk.Button(dialog, text="发送", font=FONT_NORMAL,
                  bg=COLOR_BTN, fg=COLOR_TEXT_LIGHT,
                  command=do_send).pack(pady=10)

    def _refresh_monitor(self):
        """定时刷新学生监控表格（每2秒）。"""
        if not self.is_listening or not self.server:
            self._refresh_timer_id = self.root.after(2000, self._refresh_monitor)
            return

        # 获取服务器状态（线程安全）
        status_list = self.server.get_student_status()
        logger.debug(f"student_history 条目数: {len(status_list)}")

        # 以 exam_id 为唯一标识，构建当前在线/已交卷学生的集合
        current_ids = set()
        status_by_id = {}
        for info in status_list:
            exam_id = info.get("exam_id", "")
            if exam_id:
                current_ids.add(exam_id)
                status_by_id[exam_id] = info

        # 构建现有条目映射：exam_id → item_id（同时清理不在当前列表中的条目）
        existing_items = {}
        stale_items = []
        for item in self.monitor_tree.get_children():
            values = self.monitor_tree.item(item, "values")
            if values and len(values) >= 1:
                tree_exam_id = values[0]  # 第一列是准考证号
                if tree_exam_id and tree_exam_id in current_ids:
                    existing_items[tree_exam_id] = item
                else:
                    stale_items.append(item)

        # 删除已断线的学生行
        for item in stale_items:
            self.monitor_tree.delete(item)

        # 更新或插入学生数据
        for exam_id, info in status_by_id.items():
            name = info.get("name", "未知")
            ip = info.get("ip", "")
            online = info.get("online", False)
            submitted = info.get("submitted", False)

            if submitted:
                status_text = "已交卷"
            elif online:
                status_text = "在线"
            else:
                status_text = "离线"

            if exam_id in existing_items:
                self.monitor_tree.item(
                    existing_items[exam_id],
                    values=(exam_id, name, ip, status_text))
            else:
                self.monitor_tree.insert(
                    "", tk.END,
                    values=(exam_id, name, ip, status_text))

        # 更新连接计数
        online_count = sum(1 for s in status_list if s.get("online", False))
        self.student_count_label.configure(text=f"已连接: {online_count} 人")

        self._refresh_timer_id = self.root.after(2000, self._refresh_monitor)

    # ============================================================
    # 4. 登录日志区（右侧下方）
    # ============================================================
    def _create_score_summary_area(self, parent):
        """创建右侧下方区域：包含登录日志和成绩分析两个标签页。"""
        self.score_notebook = ttk.Notebook(parent)
        self.score_notebook.pack(fill=tk.BOTH, expand=True)

        # 标签页1：登录日志
        log_tab = tk.Frame(self.score_notebook, bg=COLOR_BG_MAIN)
        self.score_notebook.add(log_tab, text="登录日志")

        outer = tk.LabelFrame(
            log_tab, text="登录日志", font=FONT_SUBTITLE,
            bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, padx=5, pady=5)
        outer.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            outer, height=6, font=("Consolas", 10),
            bg="#f5f5f5", fg=COLOR_TEXT_DARK, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 标签页2：成绩分析
        analysis_tab = tk.Frame(self.score_notebook, bg=COLOR_BG_MAIN)
        self.score_notebook.add(analysis_tab, text="成绩分析")
        self._build_analysis_tab(analysis_tab)

        # 切换标签页时自动刷新分析数据
        self.score_notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _log(self, message):
        """向登录日志区添加一行消息。"""
        if threading.get_ident() != getattr(self, "_ui_thread_id", None):
            try:
                self.root.after(0, lambda m=message: self._log(m))
            except Exception:
                pass
            return
        self.log_text.configure(state=tk.NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _begin_teacher_task(self, task_name):
        """防止下发、开考、强制交卷等大任务同时运行。"""
        with self._teacher_task_lock:
            if self._teacher_task_name:
                messagebox.showwarning(
                    "请稍等",
                    f"当前正在执行：{self._teacher_task_name}\n请完成后再操作。"
                )
                return False
            self._teacher_task_name = task_name
        self._log(f"开始任务：{task_name}")
        return True

    def _end_teacher_task(self, task_name=None):
        """结束教师端大任务锁。"""
        with self._teacher_task_lock:
            if task_name is None or self._teacher_task_name == task_name:
                finished = self._teacher_task_name
                self._teacher_task_name = ""
            else:
                finished = ""
        if finished:
            self._log(f"完成任务：{finished}")

    def _add_score_row(self, exam_id, name, score, submit_time=""):
        """添加一行成绩到内存数据（用于导出）。"""
        if not submit_time:
            submit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.score_data.append((exam_id, name, score, submit_time))

    # ============================================================
    # 成绩分析仪表盘
    # ============================================================
    def _build_analysis_tab(self, parent):
        """构建成绩分析标签页内容。"""
        # 区域A — 分数分布直方图
        hist_frame = tk.LabelFrame(
            parent, text="分数分布", font=FONT_SUBTITLE,
            bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, padx=5, pady=5)
        hist_frame.pack(fill="x", padx=5, pady=(5, 5))
        self.analysis_canvas = tk.Canvas(hist_frame, width=400, height=200,
                                         bg=COLOR_WHITE, highlightthickness=0)
        self.analysis_canvas.pack(pady=5)

        # 区域B — 各题正确率表格
        table_frame = tk.LabelFrame(
            parent, text="各题正确率", font=FONT_SUBTITLE,
            bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, padx=5, pady=5)
        table_frame.pack(fill="both", expand=True, padx=5, pady=5)

        columns = ("题号", "题型", "分值", "正确人数", "总作答", "正确率")
        self.accuracy_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=6)
        for col in columns:
            self.accuracy_tree.heading(col, text=col)
            self.accuracy_tree.column(col, anchor="center", width=70)
        self.accuracy_tree.column("题型", width=90)
        self.accuracy_tree.column("正确率", width=70)

        tree_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.accuracy_tree.yview)
        self.accuracy_tree.configure(yscrollcommand=tree_scroll.set)
        self.accuracy_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        # 区域C — 汇总统计
        self.analysis_summary_var = tk.StringVar(value="尚未有考试数据")
        summary_label = tk.Label(parent, textvariable=self.analysis_summary_var,
                                 font=("微软雅黑", 11), bg=COLOR_BG_MAIN,
                                 fg=COLOR_TEXT_DARK, justify="left")
        summary_label.pack(fill="x", padx=5, pady=(5, 10))

    def _on_tab_changed(self, event=None):
        """标签页切换时刷新分析数据。"""
        try:
            current = self.score_notebook.index(self.score_notebook.select())
            if current == 1:  # "成绩分析"是第二个标签
                self._refresh_analysis()
        except Exception:
            pass

    def _refresh_analysis(self):
        """刷新成绩分析仪表盘的所有数据。"""
        score_mapping = TEACHER_CONFIG.get("score_mapping", {
            "single_choice": 2, "fill_blank": 10, "programming": 20,
            "web_design": 30, "network_device": 15, "graphic_design": 30
        })
        # 计算总分
        total_possible_score = 0
        for q in self.exam_questions:
            qtype = q["type"]
            total_possible_score += score_mapping.get(qtype, 0)

        # --- 区域A: 直方图 ---
        self.analysis_canvas.delete("all")
        w, h = 400, 200

        scores = [row[2] for row in self.score_data if isinstance(row[2], (int, float))]
        if not scores or total_possible_score <= 0:
            self.analysis_canvas.create_text(w // 2, h // 2, text="暂无成绩数据",
                                             font=("微软雅黑", 12), fill="#999")
        else:
            # 分 5 个区间
            bounds = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
            labels_x = ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]
            colors = ["#f44336", "#FF9800", "#FFC107", "#8BC34A", "#4CAF50"]
            counts = [0] * 5
            for s in scores:
                pct = s / total_possible_score
                for i, (lo, hi) in enumerate(bounds):
                    if lo <= pct < hi or (i == 4 and pct >= 0.8):
                        counts[i] += 1
                        break

            max_count = max(counts) if counts else 1
            bar_area_left = 50
            bar_area_right = w - 10
            bar_area_top = 10
            bar_area_bottom = h - 35
            usable_w = bar_area_right - bar_area_left
            usable_h = bar_area_bottom - bar_area_top
            bar_w = usable_w // 5 - 8

            for i in range(5):
                bar_h = int(usable_h * counts[i] / max_count) if max_count > 0 else 0
                x1 = bar_area_left + i * (usable_w // 5) + 4
                y1 = bar_area_bottom - bar_h
                x2 = x1 + bar_w
                y2 = bar_area_bottom
                self.analysis_canvas.create_rectangle(x1, y1, x2, y2, fill=colors[i], outline="")
                # 人数标注
                if counts[i] > 0:
                    self.analysis_canvas.create_text(
                        (x1 + x2) // 2, y1 - 8, text=str(counts[i]),
                        font=("微软雅黑", 8), fill=COLOR_TEXT_DARK)
                # X轴标注
                self.analysis_canvas.create_text(
                    (x1 + x2) // 2, h - 18, text=labels_x[i],
                    font=("微软雅黑", 7), fill=COLOR_TEXT_DARK)

            # Y轴标签
            self.analysis_canvas.create_text(15, h // 2, text="人数",
                                             font=("微软雅黑", 8), fill=COLOR_TEXT_DARK)

        # --- 区域B: 各题正确率 ---
        for item in self.accuracy_tree.get_children():
            self.accuracy_tree.delete(item)

        # 从 submit_history 解析每道题的正确情况
        total_submitters = 0
        q_stats = {}  # qid -> {"correct": int, "total": int, "type": str, "score": int}
        for q in self.exam_questions:
            qid = q.get("_qid")
            if qid is not None:
                qtype = q["type"]
                q_score = q.get("score", 0)
                if q_score == 0:
                    q_score = score_mapping.get(qtype, 0)
                q_stats[qid] = {"correct": 0, "total": 0, "type": qtype, "score": q_score}

        if self.server:
            for submit_entry in self.server.submit_history:
                total_submitters += 1
                ans = submit_entry.get("answers", {})
                student_name = submit_entry.get("name", "")
                for qid_str, student_answer in ans.items():
                    try:
                        qid_int = int(qid_str)
                    except (ValueError, TypeError):
                        continue
                    if qid_int not in q_stats:
                        continue
                    q_stats[qid_int]["total"] += 1
                    # 判断是否正确
                    q = None
                    for eq in self.exam_questions:
                        if eq.get("_qid") == qid_int:
                            q = eq
                            break
                    if q is None:
                        continue
                    is_correct = self._check_answer_correct(q, student_answer)
                    if is_correct:
                        q_stats[qid_int]["correct"] += 1

        # 填充 Treeview
        type_labels = {"single_choice": "单选", "fill_blank": "填空", "programming": "编程",
                       "web_design": "网页", "network_device": "网络", "graphic_design": "图形"}
        for i, q in enumerate(self.exam_questions, 1):
            qid = q.get("_qid")
            stats = q_stats.get(qid) if qid is not None else None
            qtype = q["type"]
            q_score = q.get("score", 0)
            if q_score == 0:
                q_score = score_mapping.get(qtype, 0)
            correct_cnt = stats["correct"] if stats else 0
            total_cnt = stats["total"] if stats else 0
            rate_str = f"{correct_cnt / total_cnt * 100:.0f}%" if total_cnt > 0 else "-"
            item_id = self.accuracy_tree.insert("", "end", values=(
                i, type_labels.get(qtype, qtype), q_score,
                correct_cnt, total_cnt, rate_str
            ))
            # 正确率低于30%标红
            if total_cnt > 0 and correct_cnt / total_cnt < 0.3:
                self.accuracy_tree.tag_configure("low", foreground="red")
                self.accuracy_tree.item(item_id, tags=("low",))

        # --- 区域C: 汇总统计 ---
        if scores:
            exam_count = len(scores)
            avg_score = sum(scores) / exam_count
            max_score = max(scores)
            min_score = min(scores)
            pass_count = sum(1 for s in scores if s >= total_possible_score * 0.6)
            pass_rate = pass_count / exam_count * 100 if exam_count > 0 else 0
            summary = (
                f"实考人数：{exam_count}  |  平均分：{avg_score:.1f}  |  "
                f"最高分：{max_score:.0f}  |  最低分：{min_score:.0f}  |  "
                f"及格率(>=60%)：{pass_rate:.1f}% ({pass_count}/{exam_count})"
            )
        else:
            summary = "实考人数：0  |  平均分：-  |  最高分：-  |  最低分：-  |  及格率：-"
        self.analysis_summary_var.set(summary)

    def _check_answer_correct(self, q, student_answer):
        """判断学生某题的答案是否正确。"""
        qtype = q["type"]
        if qtype == "single_choice":
            correct = q.get("answer", -1)
            try:
                return int(student_answer) == int(correct)
            except (ValueError, TypeError):
                return False
        elif qtype == "fill_blank":
            blanks = q.get("answer", [])
            if not isinstance(blanks, list):
                try:
                    return int(student_answer) == int(blanks)
                except (ValueError, TypeError):
                    return str(student_answer).strip() == str(blanks).strip()
            # 每个空匹配
            if not isinstance(student_answer, dict):
                return False
            for idx, blank_info in enumerate(blanks):
                correct_val = blank_info.get("answer") if isinstance(blank_info, dict) else blank_info
                key = str(blank_info.get("label", idx + 1)) if isinstance(blank_info, dict) else str(idx)
                student_val = student_answer.get(str(idx + 1), student_answer.get(key, ""))
                try:
                    if int(student_val) != int(correct_val):
                        return False
                except (ValueError, TypeError):
                    if str(student_val).strip() != str(correct_val).strip():
                        return False
            return True
        else:
            # 编程/网页/网络/图形不自动判断，返回 True 视为"有作答即参与统计"
            return True

    # ============================================================
    # 自动判分
    # ============================================================
    def _auto_score(self, student_name, answers):
        """对学生提交的答案进行自动判分。

        单选题：按 answer 索引匹配
        填空题：按每个空的 answer 索引匹配
        编程/网页/网络设备/图形：暂不自动判分（0分）
        分值从 config.json 的 score_mapping 读取，题目无 score 字段时使用配置默认值

        Returns:
            (得分, 满分) 元组
        """
        score_mapping = TEACHER_CONFIG.get("score_mapping", {
            "single_choice": 2, "fill_blank": 10, "programming": 20,
            "web_design": 30, "network_device": 15, "graphic_design": 30
        })
        total_score = 0
        max_score = 0

        # 构建 qid -> question 查找表
        q_lookup = {}
        for q in self.exam_questions:
            qid = q.get("_qid")
            if qid is not None:
                q_lookup[qid] = q

        for qid_str, student_answer in answers.items():
            try:
                qid = int(qid_str)
            except (ValueError, TypeError):
                continue

            q = q_lookup.get(qid)
            if not q:
                continue

            q_type = q.get("_type", "")
            q_score = q.get("score", 0) or score_mapping.get(q_type, 0)

            if q_type in ("programming", "web_design", "network_device", "graphic_design"):
                max_score += q_score
                continue

            max_score += q_score

            if q_type == "single_choice":
                correct_idx = q.get("answer")
                try:
                    student_idx = int(student_answer)
                except (ValueError, TypeError):
                    student_idx = -1
                if student_idx == correct_idx:
                    total_score += q_score

            elif q_type == "fill_blank":
                blanks = q.get("blanks", [])
                if isinstance(student_answer, dict) and blanks:
                    blank_score = q_score / len(blanks)
                    for i, blank in enumerate(blanks):
                        label = blank.get("label", f"【{i+1}】")
                        correct_idx = blank.get("answer")
                        student_letter = student_answer.get(label, "")
                        letter_map = {chr(65 + j): j for j in range(26)}
                        student_idx = letter_map.get(
                            str(student_letter).upper(), -1)
                        if student_idx == correct_idx:
                            total_score += blank_score

        return round(total_score, 1), round(max_score, 1)

    def _open_folder_settings(self):
        """打开文件夹路径设置弹窗。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("文件夹路径设置")
        dialog.geometry("780x200")
        dialog.resizable(True, True)
        dialog.minsize(680, 160)
        dialog.configure(bg=COLOR_BG_MAIN)
        dialog.transient(self.root)
        dialog.grab_set()

        dialog.update_idletasks()
        dw = dialog.winfo_width()
        dh = dialog.winfo_height()
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        dialog.geometry(f"+{(sw - dw) // 2}+{(sh - dh) // 2}")

        frm = tk.Frame(dialog, bg=COLOR_BG_MAIN, padx=20, pady=20)
        frm.pack(fill=tk.BOTH, expand=True)

        sub_var = tk.StringVar(value=self.submissions_dir)

        def _browse():
            path = filedialog.askdirectory(title="选择提交文件保存文件夹")
            if path:
                sub_var.set(path)

        row = tk.Frame(frm, bg=COLOR_BG_MAIN)
        row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(row, text="提交文件保存路径:", bg=COLOR_BG_MAIN,
                 fg=COLOR_TEXT_DARK, font=FONT_NORMAL, width=16, anchor=tk.W).pack(side=tk.LEFT)
        tk.Entry(row, textvariable=sub_var, font=FONT_NORMAL, width=50).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(row, text="浏览...", font=FONT_NORMAL, width=8,
                  command=_browse).pack(side=tk.LEFT)

        tk.Label(frm, text="学生交卷后文件将保存到此目录，留空则使用默认路径", bg=COLOR_BG_MAIN,
                 fg="#888", font=("微软雅黑", 10)).pack(anchor=tk.W, pady=(0, 12))

        def _save_settings():
            self.submissions_dir = sub_var.get().strip()
            cfg_path = os.path.join(_USER_DATA_DIR, "exam_config.json")
            try:
                cfg = {}
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                cfg["submissions_dir"] = self.submissions_dir
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror("保存失败", f"无法保存设置：{e}")
            dialog.destroy()

        btn_row = tk.Frame(frm, bg=COLOR_BG_MAIN)
        btn_row.pack(fill=tk.X)

        tk.Button(btn_row, text="保存", font=FONT_NORMAL,
                  bg=COLOR_SUCCESS, fg=COLOR_TEXT_LIGHT, width=10,
                  command=_save_settings).pack(side=tk.RIGHT, padx=(5, 0))
        tk.Button(btn_row, text="取消", font=FONT_NORMAL,
                  bg="#757575", fg=COLOR_TEXT_LIGHT, width=10,
                  command=dialog.destroy).pack(side=tk.RIGHT)

    # ============================================================
    # 文件接收
    # ============================================================
    def _save_submitted_files(self, exam_id, answers):
        """从交卷答案中提取文件提交，保存到 submissions/{exam_id}/ 目录。

        Returns:
            int: 成功保存的文件数
        """
        if not exam_id:
            return 0

        TYPE_NAMES = {
            "programming": "编程题",
            "web_design": "网页设计题",
            "graphic_design": "图形设计题",
        }

        saved_count = 0
        for qid_str, answer in answers.items():
            # 只处理文件类提交
            if not isinstance(answer, dict) or answer.get("_type") not in ("file", "file_stream"):
                continue

            qid = qid_str
            q_type = answer.get("q_type", "")
            type_name = TYPE_NAMES.get(q_type, q_type)

            # 确定目标目录: submissions/{exam_id}/Q{qid}_{type}/
            folder_name = f"Q{qid}_{type_name}" if type_name else f"Q{qid}"
            save_root = self.submissions_dir or SUBMISSIONS_DIR
            target_dir = os.path.join(save_root, exam_id, folder_name)

            try:
                if answer.get("_type") == "file_stream":
                    upload_id = answer.get("upload_id", "")
                    with self._submit_file_lock:
                        stream = self._submit_file_streams.get(upload_id)
                    if not stream or not stream.get("complete") or not os.path.exists(stream.get("path", "")):
                        self._log(f"文件接收 Q{qid}: 分块文件尚未完整接收，跳过")
                        continue
                    if os.path.exists(target_dir):
                        shutil.rmtree(target_dir)
                    os.makedirs(target_dir, exist_ok=True)
                    with zipfile.ZipFile(stream["path"]) as zf:
                        _safe_extract_zip(zf, target_dir, max_total_size=MAX_STUDENT_UPLOAD_SIZE)
                        file_count = len(zf.namelist())
                    saved_count += file_count
                    self._log(
                        f"文件接收 Q{qid}: {exam_id}/{folder_name} "
                        f"（分块接收 {file_count} 个文件）"
                    )
                    try:
                        os.remove(stream["path"])
                    except OSError:
                        pass
                    with self._submit_file_lock:
                        self._submit_file_streams.pop(upload_id, None)
                    continue

                data_b64 = answer.get("data", "")
                if not data_b64:
                    continue
                # 解码 base64 → zip → 解压
                zip_bytes = base64.b64decode(data_b64)
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    # 安全：防止 zip 炸弹
                    total_size = sum(
                        info.file_size for info in zf.infolist()
                        if not info.is_dir()
                    )
                    if total_size > 50 * 1024 * 1024:  # 50MB 上限
                        self._log(
                            f"文件接收 Q{qid}: 压缩包过大 ({total_size} 字节)，跳过"
                        )
                        continue

                    # 清理旧文件，写入新文件
                    if os.path.exists(target_dir):
                        shutil.rmtree(target_dir)
                    os.makedirs(target_dir, exist_ok=True)
                    _safe_extract_zip(zf, target_dir, max_total_size=50 * 1024 * 1024)

                file_count = len(zf.namelist())
                saved_count += file_count
                self._log(
                    f"文件接收 Q{qid}: {exam_id}/{folder_name} "
                    f"（{file_count} 个文件）"
                )
            except Exception as e:
                self._log(f"文件接收 Q{qid}: 失败 - {e}")

        return saved_count

    # ============================================================
    # 服务器消息处理
    # ============================================================
    def _register_server_handlers(self):
        """注册服务器端消息处理器。"""
        if not self.server or self._msg_handlers_registered:
            return

        def handle_submit_file_start(client_sock, data):
            """开始接收学生交卷文件分块。"""
            upload_id = str(data.get("upload_id", "")).strip()
            if not upload_id:
                return
            expected_size = int(data.get("size", 0) or 0)
            max_stream_size = MAX_STUDENT_UPLOAD_SIZE
            if expected_size < 0 or expected_size > max_stream_size:
                self.root.after(0, lambda: self._log(
                    f"拒绝接收异常交卷文件：大小 {expected_size} 字节"
                ))
                return
            safe_upload = "".join(
                c if c.isalnum() or c in "-_." else "_" for c in upload_id
            )
            stream_dir = os.path.join(_USER_DATA_DIR, "exam_cache", "submit_streams")
            os.makedirs(stream_dir, exist_ok=True)
            zip_path = os.path.join(stream_dir, f"{safe_upload}.zip.part")
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except OSError:
                pass
            with self._submit_file_lock:
                self._submit_file_streams[upload_id] = {
                    "path": zip_path,
                    "name": data.get("name", ""),
                    "exam_id": data.get("exam_id", ""),
                    "qid": data.get("qid", ""),
                    "q_type": data.get("q_type", ""),
                    "size": expected_size,
                    "sha256": data.get("sha256", ""),
                    "received": 0,
                    "chunks": 0,
                    "complete": False,
                }
            self.root.after(0, lambda: self._log(
                f"开始接收 {data.get('name', '')} Q{data.get('qid', '')} 交卷文件"
            ))

        def handle_submit_file_chunk(client_sock, data):
            """接收学生交卷文件数据块。"""
            upload_id = str(data.get("upload_id", "")).strip()
            if not upload_id:
                return
            with self._submit_file_lock:
                stream = self._submit_file_streams.get(upload_id)
            if not stream:
                return
            try:
                chunk = base64.b64decode(data.get("data", ""))
                expected_size = int(stream.get("size", 0) or 0)
                if expected_size and stream.get("received", 0) + len(chunk) > expected_size:
                    raise ValueError("接收数据超过声明大小")
                with open(stream["path"], "ab") as f:
                    f.write(chunk)
                with self._submit_file_lock:
                    stream["received"] += len(chunk)
                    stream["chunks"] += 1
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda err=err: self._log(f"接收交卷文件块失败: {err}"))

        def handle_submit_file_end(client_sock, data):
            """交卷文件分块接收完成，校验后等待 SUBMIT 引用保存。"""
            upload_id = str(data.get("upload_id", "")).strip()
            if not upload_id:
                return
            with self._submit_file_lock:
                stream = self._submit_file_streams.get(upload_id)
            if not stream:
                return
            try:
                expected_size = int(data.get("size", stream.get("size", 0)) or 0)
                actual_size = os.path.getsize(stream["path"]) if os.path.exists(stream["path"]) else 0
                if expected_size and actual_size != expected_size:
                    raise ValueError(f"大小不一致 {actual_size}/{expected_size}")
                expected_sha = data.get("sha256") or stream.get("sha256", "")
                if expected_sha:
                    digest = hashlib.sha256()
                    with open(stream["path"], "rb") as f:
                        while True:
                            chunk = f.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                    if digest.hexdigest() != expected_sha:
                        raise ValueError("校验失败")
                with self._submit_file_lock:
                    stream["complete"] = True
                    stream["received"] = actual_size
                self.root.after(0, lambda: self._log(
                    f"{stream.get('name', '')} Q{stream.get('qid', '')} 交卷文件接收完成"
                ))
            except Exception as e:
                with self._submit_file_lock:
                    self._submit_file_streams.pop(upload_id, None)
                try:
                    os.remove(stream.get("path", ""))
                except OSError:
                    pass
                err = str(e)
                self.root.after(0, lambda err=err: self._log(f"交卷文件接收失败: {err}"))

        def handle_submit(client_sock, data):
            """处理学生交卷：立即确认，后台判分和保存文件，避免50人同时交卷卡死。"""
            name = data.get("name", "未知")
            answers = data.get("answers", {})

            hist = self.server.get_history(name)
            exam_id = hist.get("exam_id", "") if hist else data.get("exam_id", "")
            submit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.server.update_history(name, {
                "submitted": True,
                "submit_time": submit_time,
                "answers": answers,
                "score": "处理中",
            })

            # 先回复确认，学生端不等待教师端保存大文件
            resp = network.make_message(
                "SUBMIT_ACK", {"status": "OK", "name": name, "score": "处理中"})
            try:
                raw = json.dumps(resp, ensure_ascii=False) + "\n"
                client_sock.sendall(raw.encode("utf-8"))
            except OSError:
                pass

            with self._submit_lock:
                self._submit_pending += 1
                pending = self._submit_pending
            self.root.after(0, lambda n=name, p=pending: (
                self.status_label.configure(text=f"已收到 {n} 交卷，后台处理中（队列 {p}）"),
                self._log(f"已收到 {n} 交卷，开始后台保存文件")
            ))

            try:
                self._submit_executor.submit(
                    self._process_submit_background,
                    name, exam_id or name, answers, data.get("answer_document", ""), submit_time
                )
            except Exception as e:
                logger.error(f"提交后台任务失败: {e}")
                err = str(e)
                self.root.after(0, lambda n=name, err=err: self._log(f"{n} 交卷后台任务启动失败: {err}"))

        def handle_login(client_sock, data):
            """处理学生登录，加入允许加入检查与账号验证。"""
            try:
                safe_login = dict(data)
                if "id_number" in safe_login:
                    safe_login["id_number"] = "***"
                logger.info(f"收到 LOGIN 请求: {safe_login}")
                if not self.allow_join.get():
                    logger.warning("不允许加入，拒绝")
                    resp = network.make_message(
                        "LOGIN_RESP",
                        {"status": "REJECTED", "reason": "考场暂不允许加入"})
                    try:
                        raw = json.dumps(resp, ensure_ascii=False) + "\n"
                        client_sock.sendall(raw.encode("utf-8"))
                    except OSError:
                        pass
                    try:
                        client_sock.close()
                    except OSError:
                        pass
                    self.root.after(0, lambda: messagebox.showwarning(
                        "拒绝连接", "有学生尝试连接但被拒绝：考场暂不允许加入。请勾选「允许加入」后再试。"))
                    return

                name = data.get("name", "未知")
                exam_id = data.get("exam_id", "")
                id_number = data.get("id_number", "")

                # 不验证账号 —— 任意登录均可连接
                ip = ""
                try:
                    ip = client_sock.getpeername()[0]
                except OSError:
                    pass

                self.server.add_student(client_sock, {
                    "name": name,
                    "exam_id": exam_id,
                    "ip": ip,
                    "login_time": time.time(),
                })

                old_hist = self.server.get_history(name)
                if old_hist:
                    self.server.update_history(name, {
                        "exam_id": exam_id or old_hist.get("exam_id", ""),
                        "ip": ip,
                        "online": True,
                        "reconnect_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    logger.info(f"学生 {name}({exam_id}) 已重连")
                    self.root.after(0, lambda n=name: self._log(f"{n} 已断线重连"))
                else:
                    self.server.add_history(name, {
                        "name": name,
                        "exam_id": exam_id,
                        "ip": ip,
                        "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "online": True,
                        "submitted": False,
                        "answers": {},
                        "answers_count": 0,
                    })

                logger.info(f"学生 {name}({exam_id}) 已添加到记录，当前历史记录数: {len(self.server.student_history)}")

                resp = network.make_message(
                    "LOGIN_RESP", {"status": "OK", "name": name})
                try:
                    raw = json.dumps(resp, ensure_ascii=False) + "\n"
                    client_sock.sendall(raw.encode("utf-8"))
                except OSError:
                    pass

                self.root.after(
                    0,
                    lambda: self.status_label.configure(text=f"学生 {name} 已登录"))
                self.root.after(
                    0,
                    lambda: self._log(f"学生 {name}（{exam_id}）已成功连接！"))
                # 立即刷新监控表格
                self.root.after(0, self._refresh_monitor)
                if self.exam_in_progress and self.current_exam_data:
                    self.root.after(
                        800,
                        lambda n=name: threading.Thread(
                            target=self._resend_current_exam_to_student,
                            args=(n,),
                            daemon=True,
                        ).start()
                    )
            except Exception as e:
                logger.error(f"handle_login 异常: {e}")
                import traceback
                traceback.print_exc()
                err = str(e)
                self.root.after(
                    0,
                    lambda err=err: messagebox.showerror("错误", f"处理学生登录时出错：{err}"))

        self.server.register_handler("SUBMIT_FILE_START", handle_submit_file_start)
        self.server.register_handler("SUBMIT_FILE_CHUNK", handle_submit_file_chunk)
        self.server.register_handler("SUBMIT_FILE_END", handle_submit_file_end)
        self.server.register_handler("SUBMIT", handle_submit)
        self.server.register_handler("LOGIN", handle_login)
        self._msg_handlers_registered = True

    def _resend_current_exam_to_student(self, student_name):
        """学生断线重连后，自动补发正在进行的考试。"""
        if not self.server or not self.exam_in_progress or not self.current_exam_data:
            return
        try:
            exam_data = dict(self.current_exam_data)
            elapsed = int(time.time() - (self.current_exam_start_ts or time.time()))
            original_duration = int(exam_data.get("duration_minutes", 60))
            remaining_seconds = max(60, original_duration * 60 - elapsed)
            exam_data["duration_minutes"] = max(1, (remaining_seconds + 59) // 60)
            exam_data["remaining_seconds"] = remaining_seconds
            exam_data["reconnect_resume"] = True
            exam_data["student_count"] = self.server.student_count
            msg = network.make_message("EXAM_START", exam_data)
            raw = json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + "\n"
            ok, err = self.server.send_pre_serialized(student_name, raw.encode("utf-8"))
            if ok:
                self._log(f"已向 {student_name} 补发当前考试，恢复考试页面")
            else:
                self._log(f"向 {student_name} 补发考试失败：{err}")
        except Exception as e:
            logger.error(f"补发当前考试失败: {e}")
            self._log(f"向 {student_name} 补发考试失败：{e}")

    def _process_submit_background(self, name, exam_id, answers, answer_doc, submit_time):
        """后台处理交卷内容，避免网络接收线程和界面线程卡顿。"""
        score = 0
        max_score = 0
        saved_files = 0
        try:
            score, max_score = self._auto_score(name, answers)
            saved_files = self._save_submitted_files(exam_id or name, answers)

            if answer_doc:
                try:
                    save_root = self.submissions_dir or SUBMISSIONS_DIR
                    doc_dir = os.path.join(save_root, exam_id or name)
                    os.makedirs(doc_dir, exist_ok=True)
                    doc_filename = f"{exam_id or name}_答题记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    doc_path = os.path.join(doc_dir, doc_filename)
                    with open(doc_path, "w", encoding="utf-8") as f:
                        f.write(answer_doc)
                    self._log(f"答题记录已保存：{doc_path}")
                except Exception as e:
                    self._log(f"答题记录保存失败: {e}")

            if self.server:
                self.server.update_history(name, {
                    "submitted": True,
                    "submit_time": submit_time,
                    "answers": answers,
                    "score": score,
                })

            def _finish_ui():
                self._add_score_row(exam_id, name, f"{score}/{max_score}", submit_time)
                self._log(
                    f"{name} 已交卷，得分 {score}/{max_score}"
                    + (f"（接收文件 {saved_files} 个）" if saved_files else "")
                )
                self.status_label.configure(text=f"{name} 已交卷，得分 {score}/{max_score}")

            self.root.after(0, _finish_ui)
        except Exception as e:
            import traceback
            logger.error(f"_process_submit_background 异常: {e}\n{traceback.format_exc()}")
            err = str(e)
            self.root.after(0, lambda n=name, err=err: self._log(f"处理 {n} 交卷时出错: {err}"))
        finally:
            with self._submit_lock:
                self._submit_pending = max(0, self._submit_pending - 1)

    # ============================================================
    # 服务器启停
    # ============================================================
    def _toggle_listen(self):
        """开始/停止监听切换。"""
        if self.is_listening:
            self._stop_listen()
        else:
            self._start_listen()

    def _start_listen(self):
        """启动服务器监听。"""
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("错误", "端口号必须是整数")
            return

        try:
            self.server = network.Server(host="0.0.0.0", port=port)
            self._register_server_handlers()

            self.server_thread = threading.Thread(target=self.server.start, daemon=True)
            self.server_thread.start()

            # 等待最多 3 秒确认服务器 socket 已就绪
            for _ in range(30):
                time.sleep(0.1)
                if self.server._running:
                    break

            if not self.server._running:
                messagebox.showerror("启动失败", "服务器线程未能启动，请重试")
                self.server.stop()
                self.server = None
                return

            # 用实际连接测试验证端口是否在监听
            import socket
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(1.0)
            try:
                test_sock.connect(("127.0.0.1", port))
                test_sock.close()
                verified = True
            except Exception:
                verified = False

            if not verified:
                messagebox.showerror("启动失败", f"端口 {port} 绑定失败，可能被防火墙或其他程序占用")
                self.server.stop()
                self.server = None
                return

            self.is_listening = True
            self.status_label.configure(text="监听中...", fg=COLOR_SUCCESS)
            self._log(f"服务器已在端口 {port} 启动，等待学生连接...")

            self._refresh_timer_id = self.root.after(2000, self._refresh_monitor)
        except Exception as e:
            messagebox.showerror("启动异常", f"启动服务器时发生错误：{e}")
            import traceback
            traceback.print_exc()

    def _stop_listen(self):
        """停止服务器监听。"""
        if not self.is_listening:
            return

        if self.server:
            self.server.stop()
            self.server = None

        self.is_listening = False
        self._msg_handlers_registered = False

        if self._refresh_timer_id:
            self.root.after_cancel(self._refresh_timer_id)
            self._refresh_timer_id = None

        self.status_label.configure(text="已停止", fg=COLOR_DANGER)
        self.student_count_label.configure(text="已连接: 0 人")

        for item in self.monitor_tree.get_children():
            self.monitor_tree.delete(item)

    # ============================================================
    # 考试控制
    # ============================================================
    def _start_exam(self):
        """开始考试：获取选中题目，逐个发送 EXAM_START 并显示进度条。"""
        if not self.is_listening or not self.server:
            messagebox.showwarning("提示", "请先启动服务器监听")
            return
        task_name = "开始考试"
        if not self._begin_teacher_task(task_name):
            return

        selected = self.get_selected_questions()
        if not selected:
            self._end_teacher_task(task_name)
            messagebox.showwarning("提示", "请先在左侧选择题库题目")
            return

        try:
            duration = int(self.duration_var.get())
        except ValueError:
            self._end_teacher_task(task_name)
            messagebox.showerror("错误", "考试时长必须是整数（分钟）")
            return

        student_names = self.server.get_student_list()
        total_students = len(student_names)

        if total_students == 0:
            if not messagebox.askyesno(
                    "提示", "当前没有在线学生，是否仍然开始考试？"):
                self._end_teacher_task(task_name)
                return
            # 用户选择继续时重新获取最新学生列表（弹框等待期间可能有新学生连接）
            student_names = self.server.get_student_list()
            total_students = len(student_names)
            logger.info(f"重新获取学生列表: {student_names}")
        else:
            if not messagebox.askyesno(
                    "确认开始考试",
                    f"即将向 {total_students} 名在线学生发送考试\n"
                    f"题目数量: {len(selected)} 道\n"
                    f"考试时长: {duration} 分钟\n\n确定开始？"):
                self._end_teacher_task(task_name)
                return
            # 确认后重新获取最新在线学生（避免弹框期间有学生掉线）
            student_names = self.server.get_student_list()
            total_students = len(student_names)
            logger.info(f"确认后学生列表: {student_names}")

        logger.info(f"开始考试，在线学生: {student_names}，题目数: {len(selected)}")

        self.exam_questions = selected

        # 禁用按钮
        self.start_exam_btn.configure(state="disabled", text="发送中...")
        self.schedule_btn.configure(state="disabled", text="发送中...")
        self.status_label.configure(
            text="正在发送考试开始指令...", fg=COLOR_WARNING)
        self.root.update()

        # 创建进度弹窗
        progress_dlg = self._create_progress_dialog(max(total_students, 1), title="考试下发进度")
        progress_dlg.start_time = time.time()

        # 构造 exam_data
        exam_data = {
            "questions": selected,
            "duration_minutes": duration,
            "total_score": sum(q.get("score", 0) for q in selected),
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "student_count": total_students,
        }
        self.current_exam_data = dict(exam_data)
        self.current_exam_data["folders"] = {}
        self.current_exam_start_ts = time.time()

        # 后台线程：并行发送考试开始指令（不发文件夹，文件夹由"下发题目"负责）
        def _do_broadcast():
            try:
                # 文件夹数据留空 — 学生端已在"下发题目"阶段收到并保存
                exam_data["folders"] = {}

                # 预序列化消息（一次，避免为每名学生重复序列化）
                msg = network.make_message("EXAM_START", exam_data)
                raw = json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + "\n"
                raw_bytes = raw.encode("utf-8")
                bytes_per_student = len(raw_bytes)
                # 将每名学生字节数传到主线程，供速率计算
                self.root.after(
                    0, lambda: (
                        setattr(progress_dlg, 'bytes_per_student', bytes_per_student),
                        progress_dlg.prog_size.configure(
                            text=f"已发送: 0 MB / {total_students * bytes_per_student / 1024 / 1024:.1f} MB")
                    ) if progress_dlg.winfo_exists() else None)
                self._log(f"EXAM_START 消息序列化: {bytes_per_student} bytes，并行发送中...")

                success_list, fail_list = self._bulk_send_pre_serialized(
                    student_names,
                    raw_bytes,
                    on_done=lambda name, ok, err, count:
                        self._update_progress(progress_dlg, count, total_students, name, ok),
                    label="考试开始下发"
                )

                self.root.after(
                    1000,
                    lambda: self._on_exam_sent(
                        progress_dlg, len(selected), duration,
                        success_list, fail_list))

            except Exception as e:
                import traceback
                traceback.print_exc()
                logger.error(f"_do_broadcast 异常: {e}\n{traceback.format_exc()}")
                err = str(e)
                self.root.after(0, lambda: self._end_teacher_task(task_name))
                self.root.after(0, lambda err=err: messagebox.showerror(
                    "发送失败",
                    f"考试下发过程出错：\n{err}"
                ))

        threading.Thread(target=_do_broadcast, daemon=True).start()

    def _bulk_send_pre_serialized(self, student_names, raw_bytes, on_done=None, label="发送"):
        """后台限流批量发送预序列化消息，避免50人同时大包下发卡死。"""
        total = len(student_names)
        if total <= 0:
            return [], []

        msg_size_mb = len(raw_bytes) / 1024 / 1024
        if msg_size_mb >= 10:
            max_workers = min(total, 5)
        elif msg_size_mb >= 3:
            max_workers = min(total, 8)
        else:
            max_workers = min(total, 15)

        success_list = []
        fail_list = []
        logger.info(f"{label}: {total} 人, 单人 {msg_size_mb:.2f}MB, 并发 {max_workers}")

        def _send_one(name):
            try:
                ok, err = self.server.send_pre_serialized(name, raw_bytes)
                return name, ok, err
            except Exception as e:
                return name, False, str(e)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_send_one, name): name for name in student_names}
            for future in as_completed(futures):
                name, ok, err = future.result()
                if ok:
                    success_list.append(name)
                else:
                    fail_list.append((name, err))
                if on_done:
                    done_count = len(success_list) + len(fail_list)
                    self.root.after(0, lambda n=name, s=ok, e=err, c=done_count:
                                    on_done(n, s, e, c))

        return success_list, fail_list

    def _create_progress_dialog(self, total, title="题目下发进度"):
        """创建题目下发进度弹窗。

        Args:
            total: 总学生数
            title: 对话框标题
        """
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("440x360")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        # 居中显示
        dlg.update_idletasks()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"440x360+{(sw - 440) // 2}+{(sh - 360) // 2}")
        dlg.configure(bg=COLOR_BG_MAIN)
        dlg.lift()          # 置顶
        dlg.focus_force()    # 强制聚焦

        # 初始化速率计算相关属性
        dlg.start_time = 0
        dlg.bytes_per_student = 0
        dlg.completed = 0
        dlg.total_students = total

        # 标题
        tk.Label(
            dlg, text="正在向学生发送题目...",
            font=("微软雅黑", 12, "bold"),
            bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK
        ).pack(pady=(15, 10))

        # 进度条
        dlg.prog_bar = ttk.Progressbar(
            dlg, length=390, mode="determinate", maximum=total)
        dlg.prog_bar.pack(pady=(0, 10))
        dlg.prog_bar["value"] = 0

        # 计数标签（含百分比）
        dlg.prog_label = tk.Label(
            dlg, text=f"0 / {total} (0%)",
            font=("微软雅黑", 11), bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK)
        dlg.prog_label.pack()

        # 已传输数据量标签
        dlg.prog_size = tk.Label(
            dlg, text="已发送: 0 MB / 0 MB",
            font=("微软雅黑", 10), bg=COLOR_BG_MAIN, fg=COLOR_TEXT_LIGHT)
        dlg.prog_size.pack(pady=(0, 3))

        # 传输速率标签
        dlg.prog_rate = tk.Label(
            dlg, text="速率: --",
            font=("微软雅黑", 10), bg=COLOR_BG_MAIN, fg=COLOR_TEXT_LIGHT)
        dlg.prog_rate.pack(pady=(0, 5))

        # 状态标签
        dlg.prog_status = tk.Label(
            dlg, text="准备中...", fg="#666",
            font=("微软雅黑", 10), bg=COLOR_BG_MAIN)
        dlg.prog_status.pack(pady=(5, 10))

        # 日志区
        log_frame = tk.Frame(dlg, bg=COLOR_BG_MAIN)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 15))

        dlg.prog_log = scrolledtext.ScrolledText(
            log_frame, height=8, font=("微软雅黑", 9),
            state="disabled", bg="#fafafa")
        dlg.prog_log.pack(fill=tk.BOTH, expand=True)

        # 确保对话框显示
        dlg.update()
        return dlg

    def _update_progress(self, dlg, current, total, name, success):
        """更新进度弹窗（线程安全，通过 after 调用）。"""
        if not dlg.winfo_exists():
            return
        dlg.prog_bar["value"] = current

        # 百分比
        pct = int(current / total * 100) if total > 0 else 0
        dlg.prog_label.configure(text=f"{current} / {total} ({pct}%)")

        # 传输速率 & 已发送数据量
        if hasattr(dlg, 'start_time') and hasattr(dlg, 'bytes_per_student'):
            elapsed = time.time() - dlg.start_time
            if elapsed > 0 and current > 0:
                bytes_sent = current * dlg.bytes_per_student
                # 速率
                rate = bytes_sent / elapsed
                if rate > 1024 * 1024:
                    rate_str = f"{rate / 1024 / 1024:.1f} MB/s"
                elif rate > 1024:
                    rate_str = f"{rate / 1024:.1f} KB/s"
                else:
                    rate_str = f"{rate:.0f} B/s"
                dlg.prog_rate.configure(text=f"速率: {rate_str}")
                # 已发送数据量
                total_bytes = total * dlg.bytes_per_student
                sent_mb = bytes_sent / 1024 / 1024
                total_mb = total_bytes / 1024 / 1024
                dlg.prog_size.configure(
                    text=f"已发送: {sent_mb:.1f} MB / {total_mb:.1f} MB")
            elif current == 0:
                dlg.prog_rate.configure(text="速率: --")
                dlg.prog_size.configure(text="已发送: 0 MB / 0 MB")

        status_text = f"已发送 {name}：{'成功 ✓' if success else '失败 ✗'}"
        dlg.prog_status.configure(
            text=status_text,
            fg=COLOR_SUCCESS if success else COLOR_DANGER)

        dlg.prog_log.configure(state="normal")
        tag = "ok" if success else "fail"
        dlg.prog_log.tag_config("ok", foreground=COLOR_SUCCESS)
        dlg.prog_log.tag_config("fail", foreground=COLOR_DANGER)
        icon = "✓" if success else "✗"
        dlg.prog_log.insert(tk.END, f"[{current}/{total}] {icon} {name}\n", tag)
        dlg.prog_log.see(tk.END)
        dlg.prog_log.configure(state="disabled")
        # 实时刷新
        dlg.update_idletasks()

    def _update_progress_status(self, dlg, status_text):
        """仅更新进度弹窗的状态文字（不打日志）。"""
        if not dlg.winfo_exists():
            return
        dlg.prog_status.configure(text=status_text, fg="#666")
        dlg.update_idletasks()

    def _on_exam_sent(self, dlg, q_count, duration,
                      success_list, fail_list):
        """全部发送完成后的处理。"""
        self._end_teacher_task("开始考试")
        self.exam_in_progress = True
        self.start_exam_btn.configure(state="normal", text="开始考试")
        self.schedule_btn.configure(state="normal", text="定时开始")

        success_n = len(success_list)
        fail_n = len(fail_list)
        total = success_n + fail_n

        if dlg.winfo_exists():
            dlg.destroy()

        if fail_n > 0:
            fail_names = ", ".join(n for n, _ in fail_list)
            self.status_label.configure(
                text=f"考试已开始(部分失败) | 成功 {success_n}/{total} | "
                     f"{q_count} 题 | {duration} 分钟",
                fg=COLOR_WARNING)
            messagebox.showwarning(
                "考试下发完成",
                f"成功: {success_n}/{total} 名学生\n"
                f"失败: {fail_n} 名\n"
                f"失败名单: {fail_names}")
        else:
            self.status_label.configure(
                text=f"考试已开始 | {q_count} 题 | {duration} 分钟",
                fg=COLOR_SUCCESS)
            messagebox.showinfo(
                "考试已开始",
                f"已成功下发至全部 {total} 名学生\n"
                f"{q_count} 道题，{duration} 分钟")

    # ============================================================
    # 定时开始倒计时
    # ============================================================
    def _start_countdown(self):
        """启动定时考试倒计时。"""
        if not self.is_listening or not self.server:
            messagebox.showwarning("提示", "请先启动服务器监听")
            return

        selected = self.get_selected_questions()
        if not selected:
            messagebox.showwarning("提示", "请先在左侧选择题库题目")
            return

        try:
            delay_minutes = int(self.delay_var.get())
        except ValueError:
            messagebox.showerror("错误", "延迟必须是整数（分钟）")
            return

        if delay_minutes <= 0:
            messagebox.showwarning(
                "提示",
                "延迟时间必须大于 0 分钟，请使用「开始考试」立即开始")
            return

        self.exam_questions = selected
        self.countdown_remaining = delay_minutes * 60
        self.countdown_active = True

        # 更新按钮状态
        self.start_exam_btn.configure(state="disabled")
        self.schedule_btn.configure(state="disabled", text="倒计时中...")
        self.cancel_schedule_btn.pack(
            side=tk.LEFT, padx=(0, 10), before=self.force_btn)

        # 广播初始倒计时
        self.server.broadcast("COUNTDOWN", {
            "remaining_seconds": self.countdown_remaining,
            "total_minutes": delay_minutes,
        })

        mins, secs = divmod(self.countdown_remaining, 60)
        self.status_label.configure(
            text=f"倒计时 {mins:02d}:{secs:02d} 后自动开始考试",
            fg="#009688")
        self.root.update()

        self._tick_countdown()

    def _cancel_countdown(self):
        """取消定时考试倒计时。"""
        if not self.countdown_active:
            return
        self.countdown_active = False
        if self.countdown_after_id:
            self.root.after_cancel(self.countdown_after_id)
            self.countdown_after_id = None

        # 通知学生取消倒计时
        self.server.broadcast("COUNTDOWN", {
            "remaining_seconds": 0,
            "cancelled": True,
        })

        # 恢复按钮状态
        self.start_exam_btn.configure(state="normal")
        self.schedule_btn.configure(state="normal", text="定时开始")
        self.cancel_schedule_btn.pack_forget()
        self.status_label.configure(text="已取消定时", fg=COLOR_TEXT_LIGHT)

    def _tick_countdown(self):
        """倒计时每秒 tick。"""
        if not self.countdown_active:
            return

        if self.countdown_remaining <= 0:
            # 倒计时结束 → 自动开始考试
            self.countdown_active = False
            self.cancel_schedule_btn.pack_forget()
            self.schedule_btn.configure(state="normal", text="定时开始")
            self.start_exam_btn.configure(state="normal")
            self._start_exam()
            return

        mins, secs = divmod(self.countdown_remaining, 60)
        self.status_label.configure(
            text=f"倒计时 {mins:02d}:{secs:02d} 后自动开始考试",
            fg="#009688")

        # 每 5 秒广播一次倒计时给学生（减少网络负担）
        if self.countdown_remaining % 5 == 0:
            self.server.broadcast("COUNTDOWN", {
                "remaining_seconds": self.countdown_remaining,
            })

        self.countdown_remaining -= 1
        self.countdown_after_id = self.root.after(1000, self._tick_countdown)

    def _pack_exam_folders(self, selected_questions):
        """只打包选中题目各自的答题文件夹为 base64 zip。

        每道需要文件夹的题目（web_design / graphic_design）单独打包，
        避免一次性发送整个 exam_files/website（可能 200MB+）。

        Returns:
            dict: {str(qid): "base64_zip"} — 每道题一个条目
        """
        folders = {}

        for q in selected_questions:
            qid = q.get("_qid")
            if qid is None:
                continue
            q_type = q.get("_type", q.get("type", ""))

            if q_type in ("web_design", "graphic_design"):
                # 找到题目指定的素材文件夹
                folder_path = q.get("folder_path", "")
                if not folder_path:
                    continue
                # 解析路径：兼容绝对路径和相对路径
                if os.path.isabs(folder_path) and os.path.isdir(folder_path):
                    src_dir = folder_path
                else:
                    src_dir = os.path.join(EXAM_DIR, folder_path)
                    if not os.path.isdir(src_dir):
                        continue

                buf = io.BytesIO()
                try:
                    file_list = []
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        for root, dirs, files in os.walk(src_dir):
                            for fname in files:
                                file_path = os.path.join(root, fname)
                                arcname = os.path.relpath(file_path, src_dir)
                                zf.write(file_path, arcname)
                                file_list.append(arcname)
                    if not file_list:
                        continue
                    buf.seek(0)
                    folders[str(qid)] = base64.b64encode(buf.read()).decode("ascii")
                except Exception as e:
                    self._log(f"打包题目 {qid} 文件夹失败: {e}")

            elif q_type == "programming":
                # 编程题：打包 prog.c 模板文件为 zip
                prog_source = os.path.join(EXAM_DIR, "template_prog.c")
                if not os.path.isfile(prog_source):
                    # 没有 template_prog.c 就不打包，学生端会自动创建默认模板
                    continue
                try:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.write(prog_source, "prog.c")
                    buf.seek(0)
                    folders[str(qid)] = base64.b64encode(buf.read()).decode("ascii")
                except Exception as e:
                    self._log(f"打包编程题 {qid} 模板失败: {e}")

        return folders

    def _resolve_question_folder_source(self, question):
        """返回题目需要下发的源文件夹或文件。"""
        q_type = question.get("_type", question.get("type", ""))
        if q_type in ("web_design", "graphic_design"):
            folder_path = question.get("folder_path", "")
            if not folder_path:
                return None
            if os.path.isabs(folder_path) and os.path.isdir(folder_path):
                return folder_path
            src_dir = os.path.join(EXAM_DIR, folder_path)
            return src_dir if os.path.isdir(src_dir) else None
        if q_type == "programming":
            prog_source = os.path.join(EXAM_DIR, "template_prog.c")
            return prog_source if os.path.isfile(prog_source) else None
        return None

    def _build_stream_folder_packages(self, selected_questions):
        """把题目素材打成临时 zip 文件，供大文件分块传输使用。"""
        packages = []
        temp_dir = tempfile.mkdtemp(prefix="exam_send_", dir=tempfile.gettempdir())
        try:
            for question in selected_questions:
                qid = question.get("_qid")
                if qid is None:
                    continue
                source = self._resolve_question_folder_source(question)
                if not source:
                    continue

                q_type = question.get("_type", question.get("type", ""))
                zip_path = os.path.join(temp_dir, f"q{qid}.zip")
                file_count = 0
                try:
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        if q_type == "programming" and os.path.isfile(source):
                            zf.write(source, "prog.c")
                            file_count = 1
                        elif os.path.isdir(source):
                            for root_dir, _dirs, files in os.walk(source):
                                for fname in files:
                                    file_path = os.path.join(root_dir, fname)
                                    arcname = os.path.relpath(file_path, source)
                                    zf.write(file_path, arcname)
                                    file_count += 1
                    if file_count <= 0:
                        try:
                            os.remove(zip_path)
                        except OSError:
                            pass
                        continue
                    with open(zip_path, "rb") as f:
                        digest = hashlib.sha256()
                        while True:
                            chunk = f.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                    packages.append({
                        "qid": int(qid),
                        "type": q_type,
                        "path": zip_path,
                        "size": os.path.getsize(zip_path),
                        "sha256": digest.hexdigest(),
                        "file_count": file_count,
                    })
                except Exception as e:
                    self._log(f"打包题目 {qid} 文件夹失败: {e}")
                    try:
                        if os.path.exists(zip_path):
                            os.remove(zip_path)
                    except OSError:
                        pass
            return temp_dir, packages
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _send_folder_package_to_student(self, student_name, package,
                                        batch_id, chunk_size=1024 * 1024):
        """把单个 zip 素材包分块发给一名学生。"""
        meta = {
            "batch_id": batch_id,
            "qid": package["qid"],
            "size": package["size"],
            "sha256": package["sha256"],
            "file_count": package.get("file_count", 0),
            "chunk_size": chunk_size,
        }
        msg = network.make_message("FOLDER_STREAM_START", meta)
        raw = json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + "\n"
        ok, err = self.server.send_pre_serialized(student_name, raw.encode("utf-8"))
        if not ok:
            return False, err

        seq = 0
        sent = 0
        with open(package["path"], "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                data = {
                    "batch_id": batch_id,
                    "qid": package["qid"],
                    "seq": seq,
                    "data": base64.b64encode(chunk).decode("ascii"),
                }
                msg = network.make_message("FOLDER_STREAM_CHUNK", data)
                raw = json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + "\n"
                ok, err = self.server.send_pre_serialized(student_name, raw.encode("utf-8"))
                if not ok:
                    return False, err
                sent += len(chunk)
                seq += 1

        msg = network.make_message("FOLDER_STREAM_END", {
            "batch_id": batch_id,
            "qid": package["qid"],
            "chunks": seq,
            "size": sent,
            "sha256": package["sha256"],
        })
        raw = json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + "\n"
        return self.server.send_pre_serialized(student_name, raw.encode("utf-8"))

    def _bulk_send_folder_packages(self, student_names, packages, batch_id,
                                   on_done=None):
        """限流发送大素材包，避免 50 台学生机同时把教师机拖死。"""
        if not student_names:
            return [], []
        if not packages:
            return list(student_names), []

        total_mb = sum(p["size"] for p in packages) / 1024 / 1024
        if total_mb >= 500:
            max_workers = 2
        elif total_mb >= 100:
            max_workers = 3
        else:
            max_workers = 5
        max_workers = min(max_workers, len(student_names))

        success_list = []
        fail_list = []
        self._log(
            f"素材分块发送: {len(student_names)} 名学生, "
            f"每人 {total_mb:.1f}MB, 并发 {max_workers}"
        )

        def _send_one(name):
            for package in packages:
                ok, err = self._send_folder_package_to_student(
                    name, package, batch_id
                )
                if not ok:
                    return name, False, err
            return name, True, None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_send_one, name): name
                       for name in student_names}
            for idx, future in enumerate(as_completed(futures), 1):
                name, ok, err = future.result()
                if ok:
                    success_list.append(name)
                else:
                    fail_list.append((name, err))
                if on_done:
                    self.root.after(0, lambda n=name, o=ok, e=err, c=idx:
                                    on_done(n, o, e, c))

        return success_list, fail_list

    def _force_submit_all(self):
        """强制所有在线学生交卷。"""
        if not self.is_listening or not self.server:
            messagebox.showwarning("提示", "服务器未启动")
            return
        task_name = "强制交卷"
        if not self._begin_teacher_task(task_name):
            return

        count = self.server.student_count
        if count == 0:
            self._end_teacher_task(task_name)
            messagebox.showinfo("提示", "没有在线学生")
            return

        if not messagebox.askyesno(
                "确认", f"确定强制 {count} 名在线学生交卷吗？"):
            self._end_teacher_task(task_name)
            return

        student_names = self.server.get_student_list()
        msg = network.make_message("FORCE_SUBMIT", {})
        raw = json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + "\n"
        raw_bytes = raw.encode("utf-8")
        self.status_label.configure(text=f"正在向 {count} 名学生发送强制交卷指令...")

        def _do_force_submit():
            try:
                success, fail = self._bulk_send_pre_serialized(
                    student_names, raw_bytes, label="强制交卷"
                )
                self.root.after(0, lambda: self.status_label.configure(
                    text=f"强制交卷已发送：成功 {len(success)}/{len(student_names)}",
                    fg=COLOR_SUCCESS if not fail else COLOR_WARNING
                ))
                if fail:
                    self.root.after(0, lambda: self._log(
                        "强制交卷部分失败：" + ", ".join(name for name, _ in fail)
                    ))
            except Exception as e:
                logger.error(f"强制交卷发送异常: {e}")
                err = str(e)
                self.root.after(0, lambda err=err: self._log(f"强制交卷发送异常: {err}"))
            finally:
                self.root.after(0, lambda: self._end_teacher_task(task_name))

        threading.Thread(target=_do_force_submit, daemon=True).start()

    # ============================================================
    # 题目上传与下发功能
    # ============================================================
    def _import_questions_to_bank(self):
        """从文件导入题目到本地题库。

        支持导入标准 question_bank.json 格式的文件，
        将题目合并到当前题库中，并刷新题目选择界面。
        """
        file_path = filedialog.askopenfilename(
            title="选择题目文件（JSON格式）",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            initialdir=EXAM_DIR)
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                imported_bank = json.load(f)
        except Exception as e:
            messagebox.showerror("导入失败", f"无法读取文件：{e}")
            return

        if not isinstance(imported_bank, dict):
            messagebox.showerror("导入失败", "文件格式不正确，期望字典类型的 JSON")
            return

        # 合并到当前题库
        merged_count = 0
        for type_key in QUESTION_TYPE_ORDER:
            imported_questions = imported_bank.get(type_key, [])
            if not isinstance(imported_questions, list):
                continue
            current_questions = self.question_bank.get(type_key, [])
            # 用 id 去重，若 id 相同则覆盖，否则追加
            existing_ids = {q.get("id") for q in current_questions if q.get("id")}
            for q in imported_questions:
                qid = q.get("id")
                if isinstance(q, dict) and type_key in ("web_design", "graphic_design"):
                    if q.get("folder_path"):
                        _copy_material_folder_into_system(q, type_key, os.path.dirname(file_path))
                    _copy_reference_folder_into_system(q, type_key, os.path.dirname(file_path))
                elif isinstance(q, dict) and type_key == "network_device":
                    _copy_network_device_files_into_system(q, os.path.dirname(file_path))
                if qid is not None and qid not in existing_ids:
                    current_questions.append(q)
                    existing_ids.add(qid)
                    merged_count += 1
                elif qid is None:
                    # 无 id 的题目直接追加
                    current_questions.append(q)
                    merged_count += 1
            self.question_bank[type_key] = current_questions

        # 保存到 question_bank.json
        try:
            with open(BANK_FILE, "w", encoding="utf-8") as f:
                json.dump(self.question_bank, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("保存失败", f"无法写入题库文件：{e}")
            return

        # 刷新界面
        self._refresh_question_sections()
        self._log(f"题目导入成功：新增 {merged_count} 道题")
        messagebox.showinfo("导入成功",
            f"成功导入 {merged_count} 道题目到题库！\n题库文件已保存。")

    def _refresh_question_sections(self):
        """刷新题目选择区的全部内容（导入新题后调用）。"""
        # 清空旧复选框变量
        self.question_vars = {}
        # 销毁旧的滚动区域内容
        for widget in self.q_scroll_frame.winfo_children():
            widget.destroy()
        # 重新构建题目区域
        self._question_sections = {}
        self._build_question_sections()
        self._update_question_stats()

    # ============================================================
    # 逐题添加表单
    # ============================================================
    def _open_add_question_dialog(self):
        """打开逐题添加表单对话框，支持多种题目类型。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("逐题添加")
        dlg.geometry("700x750")
        dlg.configure(bg=COLOR_BG_MAIN)
        dlg.transient(self.root)
        dlg.grab_set()

        dlg.update_idletasks()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"700x750+{(sw-700)//2}+{(sh-750)//2}")

        # 类型映射
        type_display_to_key = {QUESTION_TYPE_NAMES.get(t, t): t for t in QUESTION_TYPE_ORDER}
        type_var = tk.StringVar(value=list(type_display_to_key.keys())[0])

        # 顶部类型选择栏
        top_bar = tk.Frame(dlg, bg=COLOR_BG_TOP, height=44)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

        tk.Label(top_bar, text="题目类型：", font=FONT_NORMAL,
                 bg=COLOR_BG_TOP, fg=COLOR_TEXT_LIGHT).pack(
            side=tk.LEFT, padx=(15, 5), pady=10)

        type_combo = ttk.Combobox(
            top_bar, textvariable=type_var,
            values=list(type_display_to_key.keys()),
            state="readonly", width=18, font=FONT_NORMAL)
        type_combo.pack(side=tk.LEFT, pady=10)

        # 滚动表单区
        canvas_outer = tk.Frame(dlg, bg=COLOR_BG_MAIN)
        canvas_outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(canvas_outer, bg=COLOR_BG_MAIN, highlightthickness=0)
        sb = tk.Scrollbar(canvas_outer, orient=tk.VERTICAL, command=canvas.yview)
        form_frame = tk.Frame(canvas, bg=COLOR_BG_MAIN)

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        form_frame.bind("<Configure>", _on_frame_configure)
        canvas.create_window((0, 0), window=form_frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        form_vars = {}

        def _save_question():
            """保存题目到题库。"""
            q_type = type_display_to_key[type_var.get()]
            content_widget = form_vars.get("content")
            content = content_widget.get("1.0", "end-1c").strip() if content_widget else ""

            if not content:
                messagebox.showwarning("提示", "请输入题目内容")
                return

            # 生成新 ID
            existing = self.question_bank.get(q_type, [])
            max_id = max((q.get("id", 0) for q in existing
                         if isinstance(q.get("id"), int)), default=999)
            question = {"content": content, "id": max_id + 1}

            # ---- 单选题 ----
            if q_type == "single_choice":
                entries = form_vars.get("options", [])
                options = [e.get().strip() for e in entries]
                if not all(options):
                    messagebox.showwarning("提示", "请填写所有选项（A-D）")
                    return
                question["options"] = options
                question["answer"] = form_vars["answer"].get()

            # ---- 填空题 ----
            elif q_type == "fill_blank":
                # 参考代码
                code_widget = form_vars.get("code")
                if code_widget:
                    code = code_widget.get("1.0", "end-1c").strip()
                    if code:
                        question["code"] = code
                # 共享选项
                option_entries = form_vars.get("option_entries", [])
                question["shared_options"] = [
                    o.get().strip() for o in option_entries if o.get().strip()]
                # 空位答案
                letter_to_idx = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4,
                                 "F": 5, "G": 6, "H": 7, "I": 8, "J": 9}
                blanks = []
                for b in form_vars.get("blanks", []):
                    ans_letter = b["answer_var"].get().strip()
                    ans_idx = letter_to_idx.get(ans_letter, 0)
                    blanks.append({
                        "label": b["label_var"].get().strip(),
                        "answer": ans_idx
                    })
                if not blanks:
                    messagebox.showwarning("提示", "至少需要添加一个空位")
                    return
                question["blanks"] = blanks

            # ---- 编程题 ----
            elif q_type == "programming":
                ref_widget = form_vars.get("reference_answer")
                question["reference_answer"] = ref_widget.get("1.0", "end-1c").strip() if ref_widget else ""

            # ---- 网页制作题 ----
            elif q_type == "web_design":
                fp = form_vars.get("folder_path")
                if fp and fp.get().strip():
                    question["folder_path"] = fp.get().strip()
                    _copy_material_folder_into_system(question, q_type)

            # ---- 网络设备题 ----
            elif q_type == "network_device":
                # 考生输入字段
                text_fields = form_vars.get("text_fields", [])
                question["text_fields"] = [{"label": tf.get().strip()} for tf in text_fields if tf.get().strip()]
                # 参考答案
                ref_widget = form_vars.get("reference_answer")
                question["reference_answer"] = ref_widget.get("1.0", "end-1c").strip() if ref_widget else ""
                # 答案文档
                ad = form_vars.get("answer_doc")
                if ad and ad.get().strip():
                    question["answer_doc"] = ad.get().strip()

            # ---- 图形图像题 ----
            elif q_type == "graphic_design":
                fp = form_vars.get("folder_path")
                if fp and fp.get().strip():
                    question["folder_path"] = fp.get().strip()
                    _copy_material_folder_into_system(question, q_type)

            # ---- 来源（所有题型通用） ----
            src_var = form_vars.get("source")
            if src_var and isinstance(src_var, tk.StringVar) and src_var.get().strip():
                question["source"] = src_var.get().strip()

            try:
                question["score"] = int(form_vars["score"].get())
            except (ValueError, KeyError):
                question["score"] = 2

            diff = form_vars.get("difficulty")
            question["difficulty"] = diff.get() if diff else "中"
            question["locked"] = False

            # 追加到题库
            if q_type not in self.question_bank:
                self.question_bank[q_type] = []
            self.question_bank[q_type].append(question)

            # 保存文件（加密）
            try:
                if CRYPTO_AVAILABLE:
                    save_encrypted_json(BANK_FILE, self.question_bank)
                else:
                    with open(BANK_FILE, "w", encoding="utf-8") as f:
                        json.dump(self.question_bank, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror("保存失败", str(e))
                return

            self._refresh_question_sections()
            self._log(f"逐题添加：{content[:30]}...")
            messagebox.showinfo("成功", "题目已添加到题库！")
            dlg.destroy()

        def _build_form(*args):
            """根据选题类型重建表单字段。"""
            for w in form_frame.winfo_children():
                w.destroy()
            form_vars.clear()

            q_type = type_display_to_key[type_var.get()]
            r = 0

            # ---- 题目内容 ----
            tk.Label(form_frame, text="题目内容 *", font=FONT_BOLD,
                     bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK).grid(
                row=r, column=0, sticky="nw", padx=15, pady=(12, 2))
            ct = scrolledtext.ScrolledText(
                form_frame, width=62, height=4, font=FONT_NORMAL)
            ct.grid(row=r, column=1, columnspan=3, padx=(0, 15),
                    pady=(12, 2), sticky="ew")
            form_vars["content"] = ct
            r += 1

            # ---- 单选题：选项 + 答案 ----
            if q_type == "single_choice":
                opt_lf = tk.LabelFrame(
                    form_frame, text="选项（A-D）",
                    bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, font=FONT_NORMAL)
                opt_lf.grid(row=r, column=0, columnspan=4,
                            sticky="ew", padx=15, pady=(6, 2))
                entries = []
                for i, lbl in enumerate(["A", "B", "C", "D"]):
                    tk.Label(opt_lf, text=f"{lbl}：",
                             bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                             font=FONT_SMALL).grid(
                        row=i, column=0, sticky="w", padx=8, pady=3)
                    e = tk.Entry(opt_lf, width=45, font=FONT_NORMAL)
                    e.grid(row=i, column=1, sticky="ew", padx=5, pady=3)
                    entries.append(e)
                form_vars["options"] = entries
                r += 1

                ans_lf = tk.LabelFrame(
                    form_frame, text="正确答案",
                    bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, font=FONT_NORMAL)
                ans_lf.grid(row=r, column=0, columnspan=4,
                            sticky="ew", padx=15, pady=(2, 6))
                av = tk.IntVar(value=0)
                for i in range(4):
                    tk.Radiobutton(
                        ans_lf, text=chr(65 + i),
                        variable=av, value=i,
                        bg=COLOR_BG_MAIN, font=FONT_NORMAL).pack(
                        side=tk.LEFT, padx=12, pady=4)
                form_vars["answer"] = av
                r += 1

            # ---- 填空题：参考代码 + 备选项 A~J + 空位答案 ----
            elif q_type == "fill_blank":
                # 参考代码（可选）
                tk.Label(form_frame, text="参考代码（可选）",
                         bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                         font=FONT_SMALL).grid(
                    row=r, column=0, sticky="nw", padx=15, pady=4)
                code_txt = scrolledtext.ScrolledText(
                    form_frame, width=62, height=4, font=("Consolas", 9))
                code_txt.grid(row=r, column=1, columnspan=3, padx=(0, 15),
                        pady=4, sticky="ew")
                form_vars["code"] = code_txt
                r += 1

                # 备选项 A~J
                opts_lf = tk.LabelFrame(
                    form_frame, text="备选项（共10个，A~J）",
                    bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, font=FONT_SMALL)
                opts_lf.grid(row=r, column=0, columnspan=4,
                             sticky="ew", padx=15, pady=(4, 2))
                option_entries = []
                letters = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
                for i, letter in enumerate(letters):
                    row_f = tk.Frame(opts_lf, bg=COLOR_BG_MAIN)
                    row_f.pack(fill="x", pady=1)
                    tk.Label(row_f, text=f"  {letter}.",
                             font=FONT_SMALL, bg=COLOR_BG_MAIN,
                             fg=COLOR_TEXT_DARK, width=3, anchor="e").pack(
                        side="left", padx=(0, 5))
                    ov = tk.StringVar()
                    tk.Entry(row_f, textvariable=ov, width=45,
                             font=FONT_SMALL).pack(
                        side="left", fill="x", expand=True)
                    option_entries.append(ov)
                form_vars["option_entries"] = option_entries
                r += 1

                # 空位答案（5个）
                blanks_lf = tk.LabelFrame(
                    form_frame, text="空位答案（共5个空位）",
                    bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, font=FONT_SMALL)
                blanks_lf.grid(row=r, column=0, columnspan=4,
                               sticky="ew", padx=15, pady=(4, 6))
                blanks_list = []
                for idx in range(5):
                    bf = tk.Frame(blanks_lf, bg=COLOR_BG_MAIN, bd=1, relief="groove")
                    bf.pack(fill="x", pady=2, padx=4)
                    tk.Label(bf, text=f"空位 {idx+1}：",
                             font=FONT_SMALL, bg=COLOR_BG_MAIN,
                             fg=COLOR_TEXT_DARK).pack(side="left", padx=(6, 4))
                    label_var = tk.StringVar(value=f"【{idx+1}】")
                    tk.Entry(bf, textvariable=label_var, width=8,
                             font=FONT_SMALL).pack(side="left", padx=4)
                    tk.Label(bf, text="  答案",
                             font=FONT_SMALL, bg=COLOR_BG_MAIN,
                             fg=COLOR_TEXT_DARK).pack(side="left", padx=(8, 4))
                    answer_var = tk.StringVar(value="A")
                    ttk.Combobox(bf, textvariable=answer_var, values=letters,
                                 state="readonly", font=FONT_SMALL, width=4).pack(
                        side="left")
                    blanks_list.append({"label_var": label_var, "answer_var": answer_var})
                form_vars["blanks"] = blanks_list
                r += 1

                tk.Label(
                    form_frame,
                    text="（题目内容中用【1】【2】标记空位）",
                    bg=COLOR_BG_MAIN, fg="#888888",
                    font=("微软雅黑", 8)).grid(
                    row=r, column=1, columnspan=3,
                    sticky="w", padx=(0, 15), pady=(0, 6))
                r += 1

            # ---- 编程题：参考答案 + 来源 ----
            elif q_type == "programming":
                tk.Label(form_frame, text="参考答案（代码）",
                         bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                         font=FONT_NORMAL).grid(
                    row=r, column=0, sticky="nw", padx=15, pady=4)
                rt = scrolledtext.ScrolledText(
                    form_frame, width=62, height=8, font=("Consolas", 10))
                rt.grid(row=r, column=1, columnspan=3, padx=(0, 15),
                        pady=4, sticky="ew")
                form_vars["reference_answer"] = rt
                r += 1

            # ---- 网页制作题：素材文件夹 ----
            elif q_type == "web_design":
                tk.Label(form_frame, text="素材文件夹",
                         bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                         font=FONT_NORMAL).grid(
                    row=r, column=0, sticky="w", padx=15, pady=4)
                fp_frame = tk.Frame(form_frame, bg=COLOR_BG_MAIN)
                fp_frame.grid(row=r, column=1, columnspan=3,
                              sticky="w", padx=(0, 15), pady=4)
                fpv = tk.StringVar()
                fpe = tk.Entry(fp_frame, textvariable=fpv,
                               width=32, font=FONT_NORMAL)
                fpe.pack(side=tk.LEFT)
                tk.Button(
                    fp_frame, text="浏览", font=FONT_SMALL,
                    command=lambda: fpv.set(
                        filedialog.askdirectory(
                            title="选择素材文件夹",
                            initialdir=EXAM_DIR) or "")).pack(
                    side=tk.LEFT, padx=(5, 0))
                form_vars["folder_path"] = fpv
                r += 1

            # ---- 网络设备题：考生输入字段 + 参考答案 + 答案文档 ----
            elif q_type == "network_device":
                # 考生输入字段
                tf_lf = tk.LabelFrame(
                    form_frame, text="考生输入字段（动态添加）",
                    bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK, font=FONT_NORMAL)
                tf_lf.grid(row=r, column=0, columnspan=4,
                           sticky="ew", padx=15, pady=(6, 2))
                tf_container = tk.Frame(tf_lf, bg=COLOR_BG_MAIN)
                tf_container.pack(fill="x", padx=8, pady=4)
                text_field_entries = []

                def _add_tf(default_text=""):
                    idx = len(text_field_entries)
                    row_f = tk.Frame(tf_container, bg=COLOR_BG_MAIN)
                    row_f.pack(fill="x", pady=1)
                    tk.Label(row_f, text=f"字段{idx+1}：",
                             font=FONT_SMALL, bg=COLOR_BG_MAIN,
                             fg=COLOR_TEXT_DARK).pack(side=tk.LEFT)
                    ev = tk.StringVar(value=default_text or f"(1) 字段{idx+1}")
                    e = tk.Entry(row_f, textvariable=ev, width=35,
                                 font=FONT_SMALL)
                    e.pack(side=tk.LEFT, fill="x", expand=True, padx=4)
                    def _del(r=row_f, e=e):
                        r.destroy()
                        text_field_entries.remove(e)
                    tk.Button(row_f, text="×", font=FONT_SMALL,
                              fg="red", bg=COLOR_BG_MAIN, bd=0,
                              cursor="hand2", command=_del).pack(side=tk.LEFT)
                    text_field_entries.append(e)

                tk.Button(tf_container, text="+ 添加字段", font=FONT_SMALL,
                          bg="#e3f2fd", fg=COLOR_ACCENT, bd=1, relief="solid",
                          cursor="hand2",
                          command=lambda: _add_tf()).pack(anchor="w", pady=2)
                _add_tf()
                form_vars["text_fields"] = text_field_entries
                r += 1

                # 参考答案
                tk.Label(form_frame, text="参考答案",
                         bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                         font=FONT_NORMAL).grid(
                    row=r, column=0, sticky="nw", padx=15, pady=4)
                rt = scrolledtext.ScrolledText(
                    form_frame, width=62, height=5, font=FONT_NORMAL)
                rt.grid(row=r, column=1, columnspan=3, padx=(0, 15),
                        pady=4, sticky="ew")
                form_vars["reference_answer"] = rt
                r += 1

                # 答案文档
                tk.Label(form_frame, text="答案文档\n（Word，可选）",
                         bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                         font=FONT_SMALL).grid(
                    row=r, column=0, sticky="nw", padx=15, pady=4)
                ad_frame = tk.Frame(form_frame, bg=COLOR_BG_MAIN)
                ad_frame.grid(row=r, column=1, columnspan=3,
                              sticky="w", padx=(0, 15), pady=4)
                adv = tk.StringVar()
                ade = tk.Entry(ad_frame, textvariable=adv, width=35,
                               font=FONT_SMALL, state="readonly",
                               readonlybackground=COLOR_WHITE)
                ade.pack(side=tk.LEFT)
                tk.Button(
                    ad_frame, text="浏览...", font=FONT_SMALL,
                    command=lambda: adv.set(
                        filedialog.askopenfilename(
                            title="选择答案 Word 文档",
                            filetypes=[("Word 文档", "*.docx *.doc"),
                                       ("所有文件", "*.*")],
                            initialdir=EXAM_DIR) or "")).pack(
                    side=tk.LEFT, padx=(5, 0))
                form_vars["answer_doc"] = adv
                r += 1

            # ---- 图形图像题：素材文件夹 ----
            elif q_type == "graphic_design":
                tk.Label(form_frame, text="素材文件夹",
                         bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                         font=FONT_NORMAL).grid(
                    row=r, column=0, sticky="w", padx=15, pady=4)
                fp_frame = tk.Frame(form_frame, bg=COLOR_BG_MAIN)
                fp_frame.grid(row=r, column=1, columnspan=3,
                              sticky="w", padx=(0, 15), pady=4)
                fpv = tk.StringVar()
                fpe = tk.Entry(fp_frame, textvariable=fpv,
                               width=32, font=FONT_NORMAL)
                fpe.pack(side=tk.LEFT)
                tk.Button(
                    fp_frame, text="浏览", font=FONT_SMALL,
                    command=lambda: fpv.set(
                        filedialog.askdirectory(
                            title="选择素材文件夹",
                            initialdir=EXAM_DIR) or "")).pack(
                    side=tk.LEFT, padx=(5, 0))
                form_vars["folder_path"] = fpv
                r += 1

            # ---- 来源（所有题型通用） ----
            tk.Label(form_frame, text="来源（选填）",
                     bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                     font=FONT_SMALL).grid(
                row=r, column=0, sticky="nw", padx=15, pady=2)
            sv2 = tk.StringVar()
            tk.Entry(form_frame, textvariable=sv2, width=45,
                     font=FONT_SMALL).grid(
                row=r, column=1, columnspan=3, padx=(0, 15),
                pady=2, sticky="ew")
            form_vars["source"] = sv2
            r += 1

            # ---- 分值 ----
            default_scores = {
                "single_choice": "2", "fill_blank": "10", "programming": "20",
                "web_design": "30", "network_device": "15", "graphic_design": "30"
            }
            tk.Label(form_frame, text="分值 *",
                     bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                     font=FONT_NORMAL).grid(
                row=r, column=0, sticky="w", padx=15, pady=(8, 4))
            sv = tk.StringVar(value=default_scores.get(q_type, "2"))
            tk.Spinbox(form_frame, from_=1, to=100,
                       textvariable=sv, width=12,
                       font=FONT_NORMAL).grid(
                row=r, column=1, sticky="w", padx=(0, 15), pady=(8, 4))
            form_vars["score"] = sv
            r += 1

            # ---- 难度 ----
            tk.Label(form_frame, text="难度",
                     bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DARK,
                     font=FONT_NORMAL).grid(
                row=r, column=0, sticky="w", padx=15, pady=4)
            dv = tk.StringVar(value="中")
            ttk.Combobox(form_frame, textvariable=dv,
                         values=["易", "中", "难"],
                         state="readonly", width=12,
                         font=FONT_NORMAL).grid(
                row=r, column=1, sticky="w", padx=(0, 15), pady=4)
            form_vars["difficulty"] = dv
            r += 1

            # ---- 底部按钮 ----
            btn_f = tk.Frame(form_frame, bg=COLOR_BG_MAIN)
            btn_f.grid(row=r, column=0, columnspan=4, pady=18)
            tk.Button(
                btn_f, text="保存到题库",
                bg=COLOR_SUCCESS, fg=COLOR_TEXT_LIGHT,
                font=FONT_BOLD, width=16, height=1,
                command=_save_question).pack(side=tk.LEFT, padx=8)
            tk.Button(
                btn_f, text="取消",
                bg="#757575", fg=COLOR_TEXT_LIGHT,
                font=FONT_NORMAL, width=12, height=1,
                command=dlg.destroy).pack(side=tk.LEFT, padx=8)

        type_combo.bind("<<ComboboxSelected>>", _build_form)
        _build_form()

    def _distribute_questions(self):
        """将选中的题目下发到所有在线学生的电脑。

        通过 QUESTION_BANK_SYNC 消息将题目推送到学生端，
        学生端接收后保存到本地 question_bank.json。

        优化：预序列化消息 + 线程池并行发送 → 速度提升 N 倍（N=学生数）
        """
        if not self.is_listening or not self.server:
            messagebox.showwarning("提示", "请先启动服务器监听")
            return
        task_name = "下发题目"
        if not self._begin_teacher_task(task_name):
            return

        selected = self.get_selected_questions()
        if not selected:
            self._end_teacher_task(task_name)
            messagebox.showwarning("提示", "请先在左侧选择题库题目")
            return

        student_names = self.server.get_student_list()
        total = len(student_names)
        if total == 0:
            self._end_teacher_task(task_name)
            messagebox.showwarning("提示", "当前没有在线学生，无法下发")
            return

        if not messagebox.askyesno(
                "确认下发",
                f"即将向 {total} 名在线学生下发 "
                f"{len(selected)} 道题目\n\n确定？"):
            self._end_teacher_task(task_name)
            return

        self._log(f"开始下发 {len(selected)} 道题给 {total} 名学生...")
        self.status_label.configure(text="正在打包题目文件夹...", fg=COLOR_WARNING)
        self.root.update()

        # 进度弹窗
        dlg = self._create_progress_dialog(max(total, 1), title="题目下发进度")
        dlg.start_time = time.time()  # 记录开始时间，供速率计算用

        def _do_distribute():
            success, fail = [], []

            # 1. 打包文件夹数据为临时 zip，后续分块发送
            self.root.after(0, lambda: self._update_progress_status(
                dlg, "正在打包题目文件夹..."))
            temp_dir = None
            folder_packages = []
            batch_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
            try:
                temp_dir, folder_packages = self._build_stream_folder_packages(selected)
                folder_count = len(folder_packages)
                total_size_mb = sum(p["size"] for p in folder_packages) / 1024 / 1024
                self._log(f"打包完成: {folder_count} 个素材包, {total_size_mb:.1f}MB")
                self.root.after(0, lambda: self._update_progress_status(
                    dlg, f"打包完成: {folder_count} 个素材包 ({total_size_mb:.1f}MB)，准备发送题目清单..."))
            except Exception as e:
                self._log(f"打包文件夹失败: {e}")
                temp_dir = None
                folder_packages = []

            # 2. 先下发题目清单；素材包单独走分块通道，避免巨大 JSON 卡死
            msg = network.make_message("QUESTION_BANK_SYNC", {
                "questions": selected,
                "folders": {},
                "folder_stream": {
                    "batch_id": batch_id,
                    "count": len(folder_packages),
                    "total_size": sum(p["size"] for p in folder_packages),
                    "items": [
                        {
                            "qid": p["qid"],
                            "size": p["size"],
                            "sha256": p["sha256"],
                            "file_count": p.get("file_count", 0),
                        }
                        for p in folder_packages
                    ],
                },
                "sync_mode": "selected",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            # separators 去掉多余空格，减少 JSON 体积
            raw = json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + "\n"
            raw_bytes = raw.encode("utf-8")
            bytes_per_student = len(raw_bytes)
            # 同步设置到对话框（在主线程执行，确保 _update_progress 能读到）
            self.root.after(0, lambda: setattr(dlg, 'bytes_per_student', bytes_per_student))
            total_bytes_mb = total * bytes_per_student / 1024 / 1024
            self.root.after(0, lambda: dlg.prog_size.configure(
                text=f"已发送: 0 MB / {total_bytes_mb:.1f} MB"))
            msg_size_mb = bytes_per_student / 1024 / 1024
            self._log(f"题目清单预序列化完成: {msg_size_mb:.2f}MB，开始发送...")

            # 3. 限流并行发送：大包降低并发，避免50台学生机同时接收时卡死
            dlg.completed = 0   # 在对话框上记录已完成数，供 _update_progress 更新
            dlg.total_students = total
            completed_names = set()

            def _on_one_done(name, ok, err):
                """单个学生发送完成后的处理（在主线程执行）。"""
                if name in completed_names:
                    return
                completed_names.add(name)
                if ok:
                    success.append(name)
                else:
                    fail.append((name, err))
                dlg.completed += 1
                cnt = dlg.completed
                self._update_progress(dlg, cnt, total, name, ok)
                if cnt >= total:
                    # 全部完成，延迟 1000ms 让最后一条日志和速率显示出来
                    self.root.after(1000, lambda:
                        self._on_distribute_done(dlg, len(selected), success, fail))

            try:
                list_success, list_fail = self._bulk_send_pre_serialized(
                    student_names,
                    raw_bytes,
                    label="题目清单下发"
                )
                if list_fail:
                    for name, err in list_fail:
                        self.root.after(0, lambda n=name, e=err:
                                        _on_one_done(n, False, e))

                if not folder_packages:
                    for name in list_success:
                        self.root.after(0, lambda n=name:
                                        _on_one_done(n, True, None))
                    return

                self.root.after(0, lambda: self._update_progress_status(
                    dlg, "题目清单已发送，正在分块下发素材包..."))
                material_success, material_fail = self._bulk_send_folder_packages(
                    list_success,
                    folder_packages,
                    batch_id,
                    on_done=lambda name, ok, err, count: _on_one_done(name, ok, err)
                )
            except Exception as e:
                logger.error(f"题目下发异常: {e}")
                err = str(e)
                self.root.after(0, lambda err=err: (
                    self._end_teacher_task(task_name),
                    self._log(f"题目下发异常: {err}"),
                    messagebox.showerror("下发失败", f"题目下发过程出错：\n{err}")
                ))
            finally:
                if temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)

        threading.Thread(target=_do_distribute, daemon=True).start()

    def _on_distribute_done(self, dlg, q_count,
                            success_list, fail_list, is_full=False):
        """题目下发完成后的处理。"""
        self._end_teacher_task("下发题目")
        if dlg.winfo_exists():
            dlg.destroy()

        success_n = len(success_list)
        fail_n = len(fail_list)
        total = success_n + fail_n

        mode_text = "完整题库" if is_full else f"{q_count} 道题"
        self.status_label.configure(
            text=f"题目下发完成: 成功 {success_n}/{total}",
            fg=COLOR_SUCCESS if fail_n == 0 else COLOR_WARNING)

        if fail_n > 0:
            fail_names = ", ".join(n for n, _ in fail_list)
            messagebox.showwarning(
                "下发完成（部分失败）",
                f"模式: {mode_text}\n\n"
                f"成功: {success_n}/{total} 名学生\n"
                f"失败: {fail_n} 名\n"
                f"失败名单: {fail_names}")
        else:
            messagebox.showinfo(
                "下发完成",
                f"已成功将 {mode_text} 下发给全部 {total} 名学生！\n"
                f"学生端接收后会自动保存到本地题库。")

    # ============================================================
    # 窗口关闭
    # ============================================================
    def _on_close(self):
        """窗口关闭时的清理工作。"""
        if self.countdown_active:
            self.countdown_active = False
            if self.countdown_after_id:
                self.root.after_cancel(self.countdown_after_id)
                self.countdown_after_id = None
        if self.is_listening and self.server:
            if messagebox.askyesno("确认退出", "服务器仍在运行，确定退出吗？"):
                self.server.stop()
                if hasattr(self, 'server_thread') and self.server_thread:
                    self.server_thread.join(timeout=3)
                try:
                    self._submit_executor.shutdown(wait=False, cancel_futures=False)
                except Exception:
                    pass
                self.root.destroy()
        else:
            try:
                self._submit_executor.shutdown(wait=False, cancel_futures=False)
            except Exception:
                pass
            self.root.destroy()

    # ============================================================
    # 启动入口
    # ============================================================
    def run(self):
        """启动教师端主循环。"""
        self.root.mainloop()


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    app = TeacherPanel()
    app.run()
