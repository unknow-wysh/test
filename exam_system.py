# -*- coding: utf-8 -*-
"""
山东省2027年春季高考技能测试网络技术类专业考试系统（考生练习用）
复刻版 v2.0 - 支持自定义题型数量、题目上传
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import os
import sys
import json
import re
import base64
import hashlib
import zipfile
import io
import shutil
from PIL import Image, ImageTk
from datetime import datetime, timedelta
import subprocess  # 局域网考试：启动教师端
from network import Client  # 局域网考试：网络通信客户端
import threading
import random
import time
import _assets  # 内嵌图片资源（Logo + 微信二维码 base64）

# PyInstaller 路径处理 - 必须在所有文件操作之前
def _get_base_path():
    """获取程序运行时的基础路径（兼容PyInstaller打包）"""
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

BASE_PATH = _get_base_path()
os.chdir(BASE_PATH)  # 设置工作目录

# 延迟导入加密工具（避免启动时加载 cryptography 拖慢速度）
# crypto_utils 仅在 load_question_bank/save_question_bank 中按需导入
CRYPTO_AVAILABLE = None  # None = 未检测，True = 可用，False = 不可用
def _check_crypto():
    global CRYPTO_AVAILABLE
    if CRYPTO_AVAILABLE is not None:
        return CRYPTO_AVAILABLE
    try:
        from crypto_utils import load_encrypted_json, save_encrypted_json
        CRYPTO_AVAILABLE = True
    except ImportError:
        CRYPTO_AVAILABLE = False
    return CRYPTO_AVAILABLE

# ==================== 打包后子进程模式处理 ====================
# PyInstaller 打包后只有一个 exe，通过 --mode 参数切换功能模块
if "--mode" in sys.argv:
    try:
        idx = sys.argv.index("--mode")
        mode = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    except (ValueError, IndexError):
        mode = ""
    if mode == "teacher":
        from teacher_panel import TeacherPanel
        TeacherPanel().run()
    elif mode == "typing":
        from word_sprite import Game_Main, Game_Info
        Game_Main.center_pos()
        from word_sprite.Game_View import GameStartWin
        GameStartWin(title="打字练习").run()
    sys.exit(0)

# ==================== 屏幕自适应缩放 ====================
import ctypes
_SCREEN_W = ctypes.windll.user32.GetSystemMetrics(0)
_SCREEN_H = ctypes.windll.user32.GetSystemMetrics(1)
_BASE_W, _BASE_H = 1920, 1080
SCALE = _SCREEN_W / _BASE_W
SCALE = max(0.8, min(SCALE, 1.5))  # 限制范围 0.8~1.5

def _s(w, h=None):
    """缩放尺寸"""
    if h is None:
        return int(w * SCALE)
    return int(w * SCALE), int(h * SCALE)

def _sg(w, h):
    """缩放尺寸并以屏幕居中定位，返回 f'{w}x{h}+{x}+{y}'"""
    sw, sh = _s(w, h)
    x = (_SCREEN_W - sw) // 2
    y = (_SCREEN_H - sh) // 2
    return f"{sw}x{sh}+{x}+{y}"

def _sf(size):
    """缩放字体大小（最小 9）"""
    return max(9, int(size * SCALE))

# ==================== 路径配置 ====================
if getattr(sys, 'frozen', False):
    # PyInstaller onedir 模式： bundled 文件在 sys._MEIPASS（临时目录，只读）
    EXAM_DIR = sys._MEIPASS
    # 数据目录：exe 所在目录（持久），不要用 sys._MEIPASS（临时，关闭后丢失）
    DATA_DIR = os.path.dirname(sys.executable)
else:
    EXAM_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = EXAM_DIR
EXAM_FILES_DIR = os.path.join(EXAM_DIR, "exam_files")
PROG_DIR = os.path.join(EXAM_FILES_DIR, "prog")
WEBSITE_DIR = os.path.join(EXAM_FILES_DIR, "website")
ENSP_DIR = os.path.join(EXAM_FILES_DIR, "ensp")
GRAPHIC_DIR = os.path.join(EXAM_FILES_DIR, "graphic")
BANK_FILE = os.path.join(EXAM_DIR, "question_bank.json")
CONFIG_FILE = os.path.join(EXAM_DIR, "exam_config.json")
IMAGE_DIR = os.path.join(EXAM_DIR, "question_images")

# ==================== 路径解析工具 ====================
def _resolve_path(path):
    """将题库中存储的路径解析为绝对路径。
    兼容旧数据（绝对路径）和新数据（相对于 EXAM_DIR 的路径）。
    路径为空或 None 时返回空字符串。

    新增「按文件夹名回退查找」：当原始路径在本地不存在时，
    提取路径最后一级文件夹名，在打包目录（exam_files/website/、
    exam_files/graphic/、exam_files/ensp/ 等）中匹配同名文件夹。
    这解决了打包后换到其他电脑时 DW/PS 题目素材找不到的问题。
    """
    if not path or not path.strip():
        return ""
    # 已是绝对路径且存在 → 直接使用（兼容旧题库）
    if os.path.isabs(path) and os.path.exists(path):
        return path
    # 相对路径 → 拼 EXAM_DIR
    resolved = os.path.join(EXAM_DIR, path)
    if os.path.exists(resolved):
        return resolved

    # ===== 按文件夹名回退查找（打包后跨电脑兼容） =====
    # 提取路径最后一级组件（文件夹名或文件名）
    clean_path = path.strip().rstrip("\\/")
    folder_name = os.path.basename(clean_path)
    if folder_name:
        # 在打包目录中按优先级搜索同名文件夹
        search_dirs = [
            ("exam_files", "website"),
            ("exam_files", "graphic"),
            ("exam_files", "ensp"),
            ("exam_files", ""),       # exam_files 根目录
            ("", ""),                  # EXAM_DIR 根目录
        ]
        for parent, sub in search_dirs:
            candidate = os.path.join(EXAM_DIR, parent, sub, folder_name)
            if os.path.isdir(candidate) and os.listdir(candidate):
                return candidate

    # 绝对路径但文件已不存在 → 仍返回原路径（让调用方处理错误）
    if os.path.isabs(path):
        return path
    # 相对路径但文件不存在 → 同样返回解析后路径
    return resolved

def _to_rel_path(abs_path):
    """将绝对路径转为相对于 EXAM_DIR 的路径，用于存入题库。
    如果路径不在 EXAM_DIR 下，则复制到 exam_files/ 并返回相对路径。
    """
    if not abs_path or not abs_path.strip():
        return ""
    try:
        rel = os.path.relpath(abs_path, EXAM_DIR)
        # 如果相对路径不以 .. 开头（即确实在 EXAM_DIR 下）
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    # 路径不在项目目录下 → 复制到 exam_files/imported/
    import_dir = os.path.join(EXAM_FILES_DIR, "imported")
    os.makedirs(import_dir, exist_ok=True)
    base = os.path.basename(abs_path)
    # 避免重名
    name, ext = os.path.splitext(base)
    dst = os.path.join(import_dir, base)
    counter = 1
    while os.path.exists(dst):
        dst = os.path.join(import_dir, f"{name}_{counter}{ext}")
        counter += 1
    shutil.copy2(abs_path, dst) if os.path.isfile(abs_path) else shutil.copytree(abs_path, dst)
    return os.path.relpath(dst, EXAM_DIR)

def _safe_material_name(value, default="material"):
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", text).strip("_")
    return text[:40] or default

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
    resolved = _resolve_path(raw_path)
    if resolved and os.path.isdir(resolved):
        return os.path.abspath(resolved)
    return ""

def _path_is_inside(child, parent):
    try:
        child_abs = os.path.abspath(child)
        parent_abs = os.path.abspath(parent)
        return os.path.commonpath([child_abs, parent_abs]) == parent_abs
    except Exception:
        return False

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
    resolved = _resolve_path(raw_path)
    if resolved and os.path.isfile(resolved):
        return os.path.abspath(resolved)
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

def _atomic_write_text(path, text, encoding="utf-8"):
    """先写临时文件，再替换目标文件，避免断电/崩溃写坏原文件。"""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = os.path.join(folder, f".{os.path.basename(path)}.tmp")
    with open(temp_path, "w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)

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

def _cleanup_question_files(q):
    """删除题目关联的素材文件/文件夹（仅清理 EXAM_DIR 下的内容，安全边界）。
    
    清理范围：
    - web_design / graphic_design: folder_path 指向的素材文件夹
    - 所有题型: image 配图文件
    - network_device: answer_doc 答案文档
    - 附带清理：question_images 下按文件名模式匹配的孤立图片
    """
    import traceback as _tb

    def _safe_delete(filepath):
        """安全删除文件/文件夹，仅限 EXAM_DIR 内"""
        if not filepath or not os.path.exists(filepath):
            return False
        ap = os.path.abspath(filepath)
        exam_abs = os.path.abspath(EXAM_DIR)
        if not ap.startswith(exam_abs + os.sep) and ap != exam_abs:
            return False  # 不在安全边界内
        if os.path.isdir(filepath):
            allowed_roots = [
                os.path.join(EXAM_FILES_DIR, "imported"),
                os.path.join(EXAM_FILES_DIR, "imported_materials"),
                os.path.join(EXAM_FILES_DIR, "lan_received"),
                os.path.join(EXAM_FILES_DIR, "reference_answers"),
            ]
            if not any(_path_is_inside(ap, root) for root in allowed_roots):
                return False
        try:
            if os.path.isdir(filepath):
                shutil.rmtree(filepath)
            else:
                os.remove(filepath)
            return True
        except Exception:
            print(f"[cleanup] 删除失败: {filepath}\n{_tb.format_exc()}", flush=True)
            return False

    paths = []
    exam_abs = os.path.abspath(EXAM_DIR)

    # 素材文件夹 (DW/PS 题型)
    fp = q.get("folder_path", "")
    if fp:
        rp = _resolve_path(fp)
        if rp:
            paths.append(rp)
        # ★ 兜底：直接拼接路径也尝试清理（防止 _resolve_path 返回不存在的路径）
        if not rp or not os.path.exists(rp):
            direct = os.path.join(EXAM_DIR, fp.lstrip("\\/"))
            if os.path.exists(direct):
                paths.append(direct)

    for ref_key in ("reference_folder", "reference_answer_folder", "answer_folder"):
        ref_folder = q.get(ref_key, "")
        if not ref_folder:
            continue
        rp = _resolve_path(ref_folder)
        if rp:
            paths.append(rp)
        if not rp or not os.path.exists(rp):
            direct = os.path.join(EXAM_DIR, str(ref_folder).lstrip("\\/"))
            if os.path.exists(direct):
                paths.append(direct)

    # 配图文件
    img = q.get("image", "")
    if img:
        rp = _resolve_path(img)
        if rp:
            paths.append(rp)
        # ★ 兜底
        if not rp or not os.path.exists(rp):
            direct = os.path.join(EXAM_DIR, img.lstrip("\\/"))
            if os.path.exists(direct):
                paths.append(direct)

    # 答案文档 (网络设备题)
    images = q.get("images", [])
    if isinstance(images, list):
        for img_item in images:
            if not img_item:
                continue
            rp = _resolve_path(str(img_item))
            if rp:
                paths.append(rp)
            if not rp or not os.path.exists(rp):
                direct = os.path.join(EXAM_DIR, str(img_item).lstrip("\\/"))
                if os.path.exists(direct):
                    paths.append(direct)

    ad = q.get("answer_doc", "")
    if ad:
        rp = _resolve_path(ad)
        if rp:
            paths.append(rp)
        if not rp or not os.path.exists(rp):
            direct = os.path.join(EXAM_DIR, ad.lstrip("\\/"))
            if os.path.exists(direct):
                paths.append(direct)

    # ★ 兜底：按文件名模式匹配清理 question_images 下的图片
    # 图片存储格式：question_images/{type}_{index}_{timestamp}.{ext}
    qtype = q.get("type", q.get("_type", ""))
    qid = q.get("id", q.get("_qid", ""))
    if qtype and (qid is not None):
        img_dir = os.path.join(EXAM_DIR, "question_images")
        if os.path.isdir(img_dir):
            # 匹配模式：{type}_* 开头的所有图片（同题型所有图）
            # 但我们只清理属于这道题的图。由于历史原因不一定有精确命名，
            # 这里采用保守策略：如果 folder_path / image 都未能定位到图，
            # 不做模糊匹配以避免误删。模糊匹配留给手动清理。
            pass

    # 执行安全删除
    deleted_any = False
    for path in paths:
        if _safe_delete(path):
            deleted_any = True
            print(f"[cleanup] 已删除: {path}", flush=True)

    if not deleted_any and (fp or img or ad):
        print(f"[cleanup] 未找到可删除的文件，qid={qid}, fp={fp}, img={img}, ad={ad}", flush=True)

ACTIVATION_FILE = os.path.join(EXAM_DIR, "activation_codes.json")
UNLOCKS_FILE = os.path.join(EXAM_DIR, "student_unlocks.json")
EXTRA_BANK_FILE = os.path.join(EXAM_DIR, "extra_questions.json")
SAVE_PATH_FILE = os.path.join(EXAM_DIR, "save_path.txt")

# 加载全局配置
def load_app_config():
    config_path = os.path.join(EXAM_DIR, "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # 默认配置
    return {
        "network_port": 8888,
        "connect_timeout_ms": 5000,
        "popup_auto_close_ms": 10000,
        "warning_dismiss_ms": 3500,
        "file_max_size_mb": 10,
        "zip_max_size_mb": 50,
        "max_chars_default": 5000,
        "listen_backlog": 50,
        "score_mapping": {
            "single_choice": 2, "fill_blank": 10, "programming": 20,
            "web_design": 30, "network_device": 15, "graphic_design": 30
        }
    }

APP_CONFIG = load_app_config()
MAX_STUDENT_UPLOAD_SIZE = int(
    APP_CONFIG.get("max_student_upload_mb", 1024)
) * 1024 * 1024

QUESTION_TYPE_ORDER = [
    "single_choice",
    "fill_blank",
    "programming",
    "web_design",
    "network_device",
    "graphic_design",
]

# 日志系统
from logger import get_logger
logger = get_logger("exam_system")


# 字母到索引的映射（A~J → 0~9）
LETTER_TO_IDX = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4,
                 "F": 5, "G": 6, "H": 7, "I": 8, "J": 9}

# GCC 候选路径列表（判题编译用，按优先级排序）
# 全覆盖：Dev-C++ 各版本、Embarcadero、MSYS2、TDM、CodeBlocks、独立 MinGW
GCC_CANDIDATE_PATHS = [
    # 新版 Dev-C++（Embarcadero / Orwell）MinGW64
    r"C:\Program Files (x86)\Dev-Cpp\MinGW64\bin\gcc.exe",
    r"C:\Program Files\Dev-Cpp\MinGW64\bin\gcc.exe",
    r"C:\Dev-Cpp\MinGW64\bin\gcc.exe",
    r"D:\Dev-Cpp\MinGW64\bin\gcc.exe",
    r"E:\Dev-Cpp\MinGW64\bin\gcc.exe",
    r"F:\Dev-Cpp\MinGW64\bin\gcc.exe",
    # Dev-C++ MinGW32（32位版）
    r"C:\Program Files (x86)\Dev-Cpp\MinGW32\bin\gcc.exe",
    r"C:\Program Files\Dev-Cpp\MinGW32\bin\gcc.exe",
    r"C:\Dev-Cpp\MinGW32\bin\gcc.exe",
    r"D:\Dev-Cpp\MinGW32\bin\gcc.exe",
    # Dev-C++ TDM-GCC 变体
    r"C:\Program Files (x86)\Dev-Cpp\TDM-GCC-64\bin\gcc.exe",
    r"C:\Program Files (x86)\Dev-Cpp\TDM-GCC-32\bin\gcc.exe",
    r"D:\Dev-Cpp\TDM-GCC-64\bin\gcc.exe",
    # Embarcadero Dev-C++（新版）
    r"C:\Program Files (x86)\Embarcadero\Dev-Cpp\MinGW64\bin\gcc.exe",
    r"C:\Program Files\Embarcadero\Dev-Cpp\MinGW64\bin\gcc.exe",
    r"C:\Program Files (x86)\Embarcadero\Dev-Cpp\TDM-GCC-64\bin\gcc.exe",
    r"D:\Embarcadero\Dev-Cpp\MinGW64\bin\gcc.exe",
    # 老版 Bloodshed Dev-C++（gcc 在根 bin 下，无 MinGW 子目录）
    r"C:\Dev-Cpp\bin\gcc.exe",
    r"C:\Program Files (x86)\Dev-Cpp\bin\gcc.exe",
    r"D:\Dev-Cpp\bin\gcc.exe",
    # CodeBlocks 自带的 MinGW
    r"C:\Program Files (x86)\CodeBlocks\MinGW\bin\gcc.exe",
    r"C:\Program Files\CodeBlocks\MinGW\bin\gcc.exe",
    r"D:\CodeBlocks\MinGW\bin\gcc.exe",
    # MSYS2 环境
    r"C:\msys64\mingw64\bin\gcc.exe",
    r"C:\msys64\mingw32\bin\gcc.exe",
    r"C:\msys64\ucrt64\bin\gcc.exe",
    r"C:\msys64\clang64\bin\gcc.exe",
    r"D:\msys64\mingw64\bin\gcc.exe",
    # 独立 MinGW / MinGW-w64 / TDM-GCC
    r"C:\MinGW\bin\gcc.exe",
    r"C:\mingw64\bin\gcc.exe",
    r"C:\mingw32\bin\gcc.exe",
    r"C:\TDM-GCC-64\bin\gcc.exe",
    r"C:\TDM-GCC-32\bin\gcc.exe",
    r"D:\MinGW\bin\gcc.exe",
    r"D:\mingw64\bin\gcc.exe",
    # WinLibs 独立 GCC
    r"C:\winlibs\mingw64\bin\gcc.exe",
    r"C:\winlibs\mingw32\bin\gcc.exe",
    # Cygwin
    r"C:\cygwin64\bin\gcc.exe",
    r"C:\cygwin\bin\gcc.exe",
    r"D:\cygwin64\bin\gcc.exe",
]
GCC_PATH = None  # 延迟探测，由 _detect_gcc_path() 填充

def _find_gcc_in_dir(base_dir, max_depth=3):
    """在指定目录下递归搜索 gcc.exe（限深度，避免全盘扫描）"""
    if not os.path.isdir(base_dir):
        return None
    try:
        for root, dirs, _ in os.walk(base_dir):
            depth = root[len(base_dir):].count(os.sep)
            if depth > max_depth:
                dirs.clear()
                continue
            if "gcc.exe" in os.listdir(root):
                return os.path.join(root, "gcc.exe")
    except (PermissionError, OSError):
        pass
    return None


def _detect_gcc_from_registry():
    """从 Windows 注册表探测 Dev-C++ 安装路径，返回 gcc.exe 路径或 None"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    # Dev-C++ 的注册表位置（多个版本）
    reg_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Dev-C++"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Dev-C++"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Dev-C++"),
        (winreg.HKEY_CURRENT_USER, r"Software\Embarcadero\Dev-C++"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Embarcadero\Dev-C++"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Embarcadero\Dev-C++"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Bloodshed\Dev-C++"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Bloodshed\Dev-C++"),
    ]
    for hkey, subkey in reg_paths:
        try:
            with winreg.OpenKey(hkey, subkey) as key:
                try:
                    install_path, _ = winreg.QueryValueEx(key, "")
                except OSError:
                    continue
                if install_path:
                    # 常见 MinGW 子目录
                    for mingw_dir in ["MinGW64", "MinGW32", "MinGW", "TDM-GCC-64", "TDM-GCC-32", ""]:
                        candidate = os.path.join(install_path, mingw_dir, "bin", "gcc.exe") if mingw_dir \
                                    else os.path.join(install_path, "bin", "gcc.exe")
                        if os.path.exists(candidate):
                            return candidate
        except OSError:
            continue
    return None


def _detect_gcc_from_devcpp_exe():
    """通过查找 devcpp.exe 反推 Dev-C++ 安装路径，再定位 gcc.exe。"""
    import shutil as _shutil
    # 先试 PATH 里有没有 devcpp.exe
    devcpp_exe = _shutil.which("devcpp.exe") or _shutil.which("DevCpp.exe")
    if devcpp_exe:
        install_dir = os.path.dirname(devcpp_exe)
        # devcpp.exe 可能在 Dev-Cpp/ 或 Dev-Cpp/bin/
        for mingw_dir in ["MinGW64", "MinGW32", "MinGW", "TDM-GCC-64", "TDM-GCC-32", ""]:
            if mingw_dir:
                candidate = os.path.join(install_dir, mingw_dir, "bin", "gcc.exe")
            else:
                candidate = os.path.join(install_dir, "bin", "gcc.exe")
            if os.path.exists(candidate):
                return candidate
        # 如果上面没找到，往上级目录找（devcpp.exe 可能在 bin/ 下）
        parent = os.path.dirname(install_dir)
        if parent and os.path.basename(parent).lower() == "bin":
            parent = os.path.dirname(parent)  # 再往上一层的 Dev-Cpp 根目录
        for mingw_dir in ["MinGW64", "MinGW32", "MinGW", "TDM-GCC-64", "TDM-GCC-32", ""]:
            if mingw_dir:
                candidate = os.path.join(parent, mingw_dir, "bin", "gcc.exe")
            else:
                candidate = os.path.join(parent, "bin", "gcc.exe")
            if os.path.exists(candidate):
                return candidate
    return None


def _detect_gcc_from_shortcuts():
    """从开始菜单/桌面快捷方式反推 Dev-C++ 安装路径，再定位 gcc.exe"""
    import glob as _glob
    # 常见快捷方式位置
    shortcut_dirs = [
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        r"C:\Users\Public\Desktop",
        os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
    ]
    for base in shortcut_dirs:
        if not os.path.isdir(base):
            continue
        # 查找所有包含 "Dev-C++" 或 "DevCpp" 的 .lnk 文件
        for pattern in ["*Dev-C++*.lnk", "*DevCpp*.lnk", "*Dev-C*.lnk"]:
            for lnk in _glob.glob(os.path.join(base, pattern)):
                try:
                    # 解析 .lnk 文件目标路径
                    target = _resolve_shortcut(lnk)
                    if target and os.path.exists(target):
                        # target 可能是 devcpp.exe，找到其所在目录
                        if os.path.isfile(target):
                            install_dir = os.path.dirname(target)
                        else:
                            install_dir = target
                        # 往上级目录找（如果 devcpp.exe 在 bin/ 下）
                        if os.path.basename(install_dir).lower() == "bin":
                            install_dir = os.path.dirname(install_dir)
                        # 查找 gcc.exe
                        for mingw_dir in ["MinGW64", "MinGW32", "MinGW", "TDM-GCC-64", "TDM-GCC-32", ""]:
                            if mingw_dir:
                                candidate = os.path.join(install_dir, mingw_dir, "bin", "gcc.exe")
                            else:
                                candidate = os.path.join(install_dir, "bin", "gcc.exe")
                            if os.path.exists(candidate):
                                return candidate
                except Exception:
                    continue
    return None


def _resolve_shortcut(lnk_path):
    """解析 .lnk 快捷方式文件，返回目标路径"""
    try:
        import pythoncom
        from win32com.client import Dispatch
        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(lnk_path)
        return shortcut.Targetpath
    except ImportError:
        # 如果没有 pywin32，尝试用 ctypes
        try:
            import ctypes
            from ctypes import wintypes
            # 使用 IShellLink + IPersistFile（需要 COM）
            # 这里简化为直接返回 None，让其他方法处理
            pass
        except Exception:
            pass
    return None


def _detect_gcc_path():
    """在候选路径中查找可用的 GCC，返回第一个存在的路径，找不到返回 None"""
    global GCC_PATH
    if GCC_PATH and os.path.exists(GCC_PATH):
        return GCC_PATH

    # ① 候选路径（精确匹配，最快）
    for candidate in GCC_CANDIDATE_PATHS:
        if os.path.exists(candidate):
            GCC_PATH = candidate
            logger.debug(f"GCC 已检出（候选路径）: {GCC_PATH}")
            return GCC_PATH

    # ② Windows 注册表（Dev-C++ 安装信息）
    reg_path = _detect_gcc_from_registry()
    if reg_path:
        GCC_PATH = reg_path
        logger.debug(f"GCC 已检出（注册表）: {GCC_PATH}")
        return GCC_PATH

    # ③ 通过 devcpp.exe 反推安装路径（新增）
    devcpp_path = _detect_gcc_from_devcpp_exe()
    if devcpp_path:
        GCC_PATH = devcpp_path
        logger.debug(f"GCC 已检出（devcpp.exe 反推）: {GCC_PATH}")
        return GCC_PATH

    # ④ PATH 环境变量
    GCC_PATH = shutil.which("gcc") or shutil.which("gcc.exe")
    if GCC_PATH:
        logger.debug(f"GCC 从 PATH 检出: {GCC_PATH}")
        return GCC_PATH

    # ⑤ 递归搜索常见安装目录（最后手段）
    search_dirs = [
        r"C:\Program Files (x86)\Dev-Cpp",
        r"C:\Program Files\Dev-Cpp",
        r"C:\Dev-Cpp",
        r"D:\Dev-Cpp",
        r"E:\Dev-Cpp",
        r"F:\Dev-Cpp",
        r"C:\Program Files (x86)\Embarcadero",
        r"C:\Program Files\Embarcadero",
        r"D:\Embarcadero",
        r"C:\Program Files (x86)\CodeBlocks",
        r"C:\Program Files\CodeBlocks",
        r"D:\CodeBlocks",
        r"C:\MinGW",
        r"D:\MinGW",
        r"C:\mingw64",
        r"D:\mingw64",
        r"C:\mingw32",
        r"C:\TDM-GCC-64",
        r"D:\TDM-GCC-64",
        r"C:\TDM-GCC-32",
        r"C:\msys64",
        r"D:\msys64",
        r"C:\cygwin64",
        r"D:\cygwin64",
        r"C:\winlibs",
        r"D:\winlibs",
        r"C:\LLVM",
        r"D:\LLVM",
    ]
    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        found = _find_gcc_in_dir(base)
        if found:
            GCC_PATH = found
            logger.debug(f"GCC 已检出（递归搜索）: {GCC_PATH}")
            return GCC_PATH

    # ⑥ 从开始菜单/桌面快捷方式反推安装路径
    shortcut_path = _detect_gcc_from_shortcuts()
    if shortcut_path:
        GCC_PATH = shortcut_path
        logger.debug(f"GCC 已检出（快捷方式）: {GCC_PATH}")
        return GCC_PATH

    return None

# 考试配置
EXAM_DURATION = 60 * 60

# 局域网考试答卷缓存目录
EXAM_CACHE_DIR = os.path.join(DATA_DIR, "exam_cache")

# 答题记录文档存储目录（本地考试模式）— 保存到用户「文档」文件夹下
def _get_documents_folder():
    """获取 Windows「文档」文件夹路径，兼容重定向/非系统盘"""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_PERSONAL = 5 即「文档」文件夹
        ctypes.windll.shell32.SHGetFolderPathW(0, 5, 0, 0, buf)
        return buf.value
    except Exception:
        return os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Documents")

ANSWER_RECORDS_DIR = os.path.join(_get_documents_folder(), "网络技术考试答题记录")

# 颜色方案
COLOR_BG_TOP = "#1a6fb5"
COLOR_BG_BOTTOM = "#0d4f8a"
COLOR_BG_MAIN = "#e8f0fe"
COLOR_WHITE = "#ffffff"
COLOR_TEXT_DARK = "#1a3a5c"
COLOR_TEXT_LIGHT = "#ffffff"
COLOR_ACCENT = "#2196F3"
COLOR_BTN = "#1976D2"
COLOR_BTN_HOVER = "#1565C0"
COLOR_SUCCESS = "#4CAF50"
COLOR_WARNING = "#FF9800"
COLOR_BG = "#f0f4f8"   # 对话框背景色
COLOR_DANGER = "#f44336"
COLOR_SIDEBAR = "#0d3b66"
COLOR_ANSWERED = "#4CAF50"
COLOR_ANSWERING = "#FF9800"
COLOR_UNANSWERED = "#9e9e9e"
COLOR_MARKED = "#9C27B0"


def load_question_bank():
    """加载题库 JSON 文件，返回字典。支持加密文件（按需加载 crypto）。"""
    if os.path.exists(BANK_FILE):
        # 优先尝试明文加载（绝大多数场景，包括教师端下发）
        try:
            with open(BANK_FILE, "r", encoding="utf-8-sig") as f:
                bank = json.load(f)
            # 迁移：补全 difficulty 字段（默认"中"）
            modified = False
            for key, pool in bank.items():
                if not isinstance(pool, list):
                    continue
                for q in pool:
                    if isinstance(q, dict) and "difficulty" not in q:
                        q["difficulty"] = "中"
                        modified = True
            # 迁移：补全 reference_answer 字段
            for key in ("programming", "network_device"):
                pool = bank.get(key, [])
                for q in pool:
                    if isinstance(q, dict):
                        if "reference_answer" not in q:
                            q["reference_answer"] = "" if key == "programming" else {}
                            modified = True
                        elif key == "network_device" and isinstance(q["reference_answer"], str) and q["reference_answer"] != "":
                            old_ref = q["reference_answer"]
                            text_fields = q.get("text_fields", [])
                            ref_dict = {}
                            if text_fields:
                                parts = re.split(r'\n*(?:【【?|###\s*)\n*', old_ref) if old_ref else []
                                if parts[0].strip() == "":
                                    parts = parts[1:]
                                for i, tf in enumerate(text_fields):
                                    label = tf.get("label", "")
                                    if i < len(parts):
                                        ref_dict[label] = parts[i].strip().rstrip("】").strip()
                                    else:
                                        ref_dict[label] = ""
                            if not ref_dict:
                                for tf in text_fields:
                                    ref_dict[tf.get("label", "")] = ""
                            q["reference_answer"] = ref_dict
                            modified = True
                        elif key == "network_device" and not isinstance(q["reference_answer"], dict):
                            text_fields = q.get("text_fields", [])
                            ref_dict = {}
                            for tf in text_fields:
                                ref_dict[tf.get("label", "")] = ""
                            q["reference_answer"] = ref_dict
                            modified = True
            if modified:
                save_question_bank(bank)
            return bank
        except Exception as e:
            print(f"[load_question_bank] 明文加载失败: {e}")
            # 如果 crypto_utils 可用，再尝试加密加载（兜底）
            if _check_crypto():
                try:
                    from crypto_utils import load_encrypted_json
                    bank = load_encrypted_json(BANK_FILE)
                    print(f"[load_question_bank] 加密加载成功")
                    return bank
                except Exception as e2:
                    print(f"[load_question_bank] 加密加载也失败: {e2}")
            # 两路都失败：尝试从 .bak 备份恢复
            bak_path = BANK_FILE + ".bak"
            if os.path.exists(bak_path):
                try:
                    with open(bak_path, "r", encoding="utf-8-sig") as f:
                        bank = json.load(f)
                    print(f"[load_question_bank] 从备份恢复成功")
                    return bank
                except Exception:
                    pass
            print(f"[load_question_bank] 所有加载方式均失败，返回空题库")
    # 文件不存在或加载失败：返回默认空结构
    return {"single_choice": [], "fill_blank": [], "programming": [], "web_design": [], "network_device": [], "graphic_design": []}


def save_question_bank(bank):
    """保存题库到 JSON 文件。支持加密保存（按需加载 crypto）。"""
    try:
        # 安全检查：如果题库全为空，先备份现有文件（防止异常覆盖丢失数据）
        all_empty = all(len(pool) == 0 for pool in bank.values() if isinstance(pool, list))
        if all_empty and os.path.exists(BANK_FILE):
            import shutil
            bak_path = BANK_FILE + ".bak"
            shutil.copy2(BANK_FILE, bak_path)
            print(f"[save_question_bank] 警告：题库全为空，已备份原文件到 {bak_path}")

        if _check_crypto():
            # 使用加密保存（延迟导入 crypto_utils）
            from crypto_utils import save_encrypted_json
            save_encrypted_json(BANK_FILE, bank)
        else:
            # 普通保存
            json_text = json.dumps(bank, ensure_ascii=False, indent=2)
            _atomic_write_text(BANK_FILE, json_text, encoding="utf-8")
    except (IOError, PermissionError, Exception) as e:
        messagebox.showerror("保存失败", f"题库保存失败：{e}")


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"single_choice": 5, "fill_blank": 2, "programming": 1, "web_design": 1, "network_device": 1, "graphic_design": 1}


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except (IOError, PermissionError) as e:
        messagebox.showerror("保存失败", f"配置保存失败：{e}")


ACCOUNTS_FILE = os.path.join(EXAM_DIR, "accounts.json")

# ==================== 激活码系统 ====================
def load_activation_codes():
    if os.path.exists(ACTIVATION_FILE):
        with open(ACTIVATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_activation_codes(codes):
    with open(ACTIVATION_FILE, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, indent=2)

def load_student_unlocks():
    if os.path.exists(UNLOCKS_FILE):
        with open(UNLOCKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_student_unlocks(unlocks):
    with open(UNLOCKS_FILE, "w", encoding="utf-8") as f:
        json.dump(unlocks, f, ensure_ascii=False, indent=2)

def load_extra_questions():
    """加载加量题库（兼容三种格式）"""
    if not os.path.exists(EXTRA_BANK_FILE):
        return []
    with open(EXTRA_BANK_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 格式1: {"questions": [...]}
    if isinstance(data, dict) and "questions" in data:
        return data["questions"]
    # 格式2: {"single_choice": [...], "fill_blank": [...]} — 和主题库一致
    if isinstance(data, dict):
        result = []
        for qtype, pool in data.items():
            if isinstance(pool, list):
                for q in pool:
                    if isinstance(q, dict):
                        q["type"] = q.get("type", qtype)
                    result.append(q)
        return result
    # 格式3: [...]
    if isinstance(data, list):
        return data
    return []

def generate_activation_code():
    """生成格式为 XXXX-XXXX 的随机码"""
    import random, string
    chars = string.ascii_uppercase + string.digits
    a = ''.join(random.choices(chars, k=4))
    b = ''.join(random.choices(chars, k=4))
    return f"{a}-{b}"

def use_activation_code(code, exam_id):
    """固定码 sdck_wlydt，自动解锁题库中所有锁定题。"""
    if code != "SDCK_WLYDT":
        return None
    bank = load_question_bank()
    all_locked = []
    for qtype, pool in bank.items():
        if isinstance(pool, list):
            for q in pool:
                if isinstance(q, dict) and q.get("locked"):
                    qid = q.get("_qid") or q.get("id")
                    if qid:
                        all_locked.append(qid)
    if not all_locked:
        return None
    unlocks = load_student_unlocks()
    if exam_id not in unlocks:
        unlocks[exam_id] = []
    new_qids = [q for q in all_locked if q not in unlocks[exam_id]]
    unlocks[exam_id].extend(all_locked)
    unlocks[exam_id] = list(set(unlocks[exam_id]))
    save_student_unlocks(unlocks)
    return all_locked


# 硬编码超级管理员账号 — 不可删除、始终存在
_BUILTIN_ADMIN_ID = "admin"
_BUILTIN_ADMIN = {"password": "admin", "name": "超级管理员", "is_admin": True}

def load_accounts():
    """加载账号数据，确保硬编码超级管理员始终存在。

    流程：
    1. 读取 accounts.json（明文 JSON）
    2. 如果文件不存在或格式异常，返回包含 admin 的默认账号
    3. 确保 admin 账号始终存在于返回结果中（不覆盖已有的 admin）
    """
    accounts = {}
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 判断是否是加密格式（包含 _encrypted 和 data 字段）
            if isinstance(data, dict) and "_encrypted" in data and "data" in data:
                # 加密格式无法读取，忽略文件内容，使用默认账号
                pass
            elif isinstance(data, dict):
                accounts = data
        except Exception:
            pass

    # 确保硬编码超级管理员始终存在
    if _BUILTIN_ADMIN_ID not in accounts:
        accounts[_BUILTIN_ADMIN_ID] = _BUILTIN_ADMIN.copy()
        save_accounts(accounts)

    return accounts


def save_accounts(accounts):
    """保存账号数据到 accounts.json（明文格式）。"""
    # 保存前确保 admin 不会丢失
    if _BUILTIN_ADMIN_ID not in accounts:
        accounts[_BUILTIN_ADMIN_ID] = _BUILTIN_ADMIN.copy()
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


# ★ PyInstaller console=False 时 stdout 指向 NUL，flush 触发 OSError 22
#    将所有 print 调用包装为安全版本，防止打包后崩溃
import builtins
_original_print = builtins.print
def _safe_print(*args, **kwargs):
    try:
        _original_print(*args, **kwargs)
    except OSError:
        pass
builtins.print = _safe_print


class ExamSystem:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()  # 加载期间隐藏主窗口，避免白屏闪屏
        self.root.title("山东省2027年春季高考技能测试网络技术类专业考试系统（考生练习用）")
        self.root.state('zoomed')
        self.root.configure(bg=COLOR_BG_MAIN)

        # ── 启动加载页面（Tkinter Splash）──
        # launcher 模式下跳过内部 splash（由 launcher.py 显示加载页面，避免双窗口）
        self._launcher_mode = os.environ.get("EXAM_LAUNCHER_ACTIVE") == "1"
        self._splash_win = None
        self._splash_bar = None
        self._splash_label = None
        if not self._launcher_mode:
            self._create_splash()
        self._update_splash("正在初始化界面组件...", 15)
        self.root.update()
        self._report_progress(15, "正在初始化界面组件...")

        # ESC 退出全屏（调试用）
        self.root.bind('<Escape>', lambda e: self.root.attributes('-fullscreen', False))

        # 窗口关闭协议
        self.root.protocol('WM_DELETE_WINDOW', self._on_root_close)

        # 修复 Tkinter Windows 下最小化后无法恢复的已知 bug
        self._iconified = False
        self._closing = False
        self._was_fullscreen = False
        def _on_unmap(event):
            # Unmap 事件触发时 state() 可能尚未变为 iconic，不依赖 state() 判断，避免漏判
            if self._closing:
                return
            self._iconified = True
        def _on_map(event):
            if self._closing:
                return
            if self._iconified:
                self._iconified = False
                self.root.after(50, self._restore_window)
        def _on_focus_in(event):
            if self._closing:
                return
            if self._iconified:
                self._iconified = False
                self.root.after(50, self._restore_window)
        def _poll_minimize_state():
            # 兜底轮询：若 Unmap 漏判导致标志未置位，这里补上
            try:
                if self.root.winfo_exists() and self.root.state() == 'iconic':
                    self._iconified = True
            except Exception:
                pass
            self.root.after(500, _poll_minimize_state)
        self.root.bind('<Unmap>', _on_unmap, add='+')
        self.root.bind('<Map>', _on_map, add='+')
        self.root.bind('<FocusIn>', _on_focus_in, add='+')
        self.root.after(500, _poll_minimize_state)

        self._update_splash("正在加载账号配置...", 30)
        self.root.update()
        self._report_progress(30, "正在加载账号配置...")

        self.exam_id = ""
        self.id_number = ""
        self.user_name = ""
        self.is_admin = False
        self.accounts = load_accounts()
        self.question_config = load_config()
        # 从配置加载考试文件夹路径（有则覆盖默认值）
        self._apply_folder_config()

        # ★ 题库延迟加载：登录时才加载，启动时只初始化空字典
        #    （show_login 不需要题库，do_login 时会调 load_question_bank()）
        self.question_bank = {}
        self.questions = []
        self.folder_path_vars = {}
        self.selected_qids = {}  # 精准选题：题型 -> 选中的题目ID列表

        self.current_question = 0
        self.answers = {}
        self.submitted = set()
        self.marked = set()
        self.question_buttons = []  # 安全初始化，刷题模式下 _update_sidebar_buttons 不再因缺失属性而崩溃
        self.wrong_questions = set()  # 错题 qid 集合
        self._submit_ack_timeout_id = None  # SUBMIT_ACK 超时 ID
        self.practice_code_content = {}  # qid -> 编辑器中的代码内容
        self._judge_result_widgets = {}  # qid -> 判题结果面板 widgets
        # 刷题模式 UI 复用（防止 Canvas 反复销毁重建导致空白）
        self._pf_info = None    # info_frame（题号/题型）
        self._pf_outer = None   # content_outer（Canvas 外框）
        self._pf_canvas = None  # content_canvas
        self._pf_canvas_window = None  # Canvas 内窗口 item id
        self._pf_frame = None   # content_frame（题目内容区）
        self._pf_scroll = None  # content_scrollbar
        self._pf_nav = None     # nav_frame（上一题/下一题）
        self.exam_started = False
        self._locked_question = None  # 当前处于答题状态的题目 qid
        self._float_window = None     # 答题时的悬浮编辑窗口
        self._prog_folder_map = {}  # 编程题文件夹映射（延迟填充）
        self._dw_folder_map = {}    # DW网页制作题文件夹映射（延迟填充）
        self._ps_folder_map = {}    # PS图形图像题文件夹映射（延迟填充）
        self.time_remaining = EXAM_DURATION
        self.timer_running = False
        self._timer_lock = threading.Lock()

        # 局域网考试模式
        self.lan_mode = False
        self.network_client = None
        self.exam_duration = EXAM_DURATION  # 可被 EXAM_START 覆盖
        self.lan_students_count = 0  # 已连接学生数
        self._lan_countdown_label = None  # 倒计时标签（show_lan_waiting 中创建）

        # 局域网断线恢复
        self._lan_disconnected = False
        self._lan_pending_answers = {}  # 断线期间缓存的答卷
        self._folder_streams = {}
        self._folder_stream_progress = {}

        self._update_splash("正在准备登录界面...", 60)
        self.root.update()
        self._report_progress(60, "正在准备登录界面...")

        # 延迟检查 GCC 编译器（避免阻塞启动流程）
        self.root.after(500, self._check_gcc)

        # ★ 题库不在此加载，登录时才加载（减少启动等待时间）
        self._report_progress(80, "正在构建登录界面...")
        self._update_splash("正在构建登录界面...", 80)
        self.root.update()

        self.show_login()

        # ★ 登录界面构建完成后报告 100% 并关闭 splash
        self._report_progress(100, "启动完成")
        self._update_splash("启动完成", 100)
        self.root.update()
        self.root.after(100, self._close_splash)  # 100ms 后关闭，让 100% 状态短暂可见

    def _check_gcc(self):
        """后台检查 GCC（不阻塞启动）"""
        _detect_gcc_path()
        if not GCC_PATH or not os.path.exists(GCC_PATH):
            # 收集一些诊断信息，帮助定位问题
            hint = "未找到 GCC 编译器（gcc.exe）。\n"
            hint += "编程题自动判题功能将不可用。\n\n"
            hint += "请确认：\n"
            hint += " 1. Dev-C++ 已安装（含 MinGW 编译器组件）\n"
            hint += " 2. 或已安装 MinGW / MSYS2 / Cygwin\n\n"
            hint += "如果已安装 Dev-C++ 但仍提示此消息，\n"
            hint += "请检查 Dev-C++ 安装目录下是否存在\n"
            hint += "  MinGW64\\bin\\gcc.exe 或类似路径。"
            messagebox.showwarning("编译器缺失", hint)

    # ═══════════════════════════════════════════════════════════════
    # 启动加载页面（Tkinter Splash Screen）
    # ═══════════════════════════════════════════════════════════════
    def _create_splash(self):
        """创建启动加载页面 — 无边框居中窗口 + 进度条"""
        splash = tk.Toplevel(self.root)
        splash.overrideredirect(True)          # 无标题栏
        splash.attributes('-topmost', True)    # 总在最前
        splash.configure(bg='#ffffff')

        # 尺寸 & 居中
        w, h = 480, 260
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        splash.geometry(f"{w}x{h}+{x}+{y}")

        # 顶部蓝色装饰线
        tk.Frame(splash, bg=COLOR_BG_TOP, height=5).pack(fill='x')

        # 标题
        tk.Label(splash, text="网络技术考试系统",
                 font=("Microsoft YaHei", 18, "bold"),
                 fg=COLOR_BG_TOP, bg='#ffffff').pack(pady=(32, 4))

        # 副标题
        tk.Label(splash, text="山东省春季高考技能测试 · 考生练习系统",
                 font=("Microsoft YaHei", 9),
                 fg='#999999', bg='#ffffff').pack(pady=(0, 26))

        # 进度条容器（灰色背景）
        bar_frame = tk.Frame(splash, bg='#e8e8e8', height=10, width=400)
        bar_frame.pack(pady=(4, 14))
        bar_frame.pack_propagate(False)

        # 进度条（蓝色填充，初始宽度为 0）
        self._splash_bar = tk.Frame(bar_frame, bg=COLOR_ACCENT, height=10, width=0)
        self._splash_bar.place(x=0, y=0)

        # 状态文字
        self._splash_label = tk.Label(splash, text="正在初始化...",
                                       font=("Microsoft YaHei", 10),
                                       fg='#666666', bg='#ffffff')
        self._splash_label.pack(pady=(2, 0))

        # 底部版权
        tk.Label(splash, text="Powered by Senior Developer",
                 font=("Consolas", 8),
                 fg='#cccccc', bg='#ffffff').pack(side='bottom', pady=(14, 12))

        self._splash_win = splash
        self._splash_frame = bar_frame

    def _update_splash(self, text, percent):
        """更新加载进度文字和进度条"""
        if not hasattr(self, '_splash_win') or self._splash_win is None:
            return
        try:
            if self._splash_win.winfo_exists():
                self._splash_label.config(text=text)
                new_w = int(400 * percent / 100)
                self._splash_bar.place_configure(width=new_w)
                self._splash_win.update_idletasks()
        except Exception:
            pass

    def _report_progress(self, pct, text=""):
        """向 launcher 汇报加载进度（通过 stdout 协议）。
        格式：PROGRESS:<百分比>:<文字>
        launcher.py 解析后直接更新进度条，替代假动画。
        """
        try:
            print(f"PROGRESS:{pct}:{text}", flush=True)
        except Exception:
            pass

    def _close_splash(self):
        """关闭启动加载页面，显示主窗口"""
        if hasattr(self, '_splash_win') and self._splash_win is not None:
            try:
                if self._splash_win.winfo_exists():
                    self._splash_win.destroy()
            except Exception:
                pass
            self._splash_win = None
            self._splash_bar = None
            self._splash_label = None
        self.root.deiconify()  # 显示主窗口
        # ★ 通知 launcher：主窗口已就绪，可以关闭 splash
        # PyInstaller console=False 时 stdout 可能是 NUL，flush 会触发 OSError
        try:
            print("READY", flush=True)
        except OSError:
            pass

    def _screen_size(self):
        if not self.root.attributes('-fullscreen'):
            self.root.update_idletasks()
            return self.root.winfo_width(), self.root.winfo_height()
        return self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    # ==================== 滚动条自动显隐 + 鼠标滚轮封装 ====================
    @staticmethod
    def _setup_auto_scroll(canvas, scrollbar, **pack_kwargs):
        """为 Canvas+Scrollbar 组合添加：① 内容溢出时自动显示滑条，未溢出隐藏；② 区域内鼠标滚轮滚动。"""
        def _check(event=None):
            canvas.update_idletasks()
            bbox = canvas.bbox("all")
            if bbox and bbox[3] > canvas.winfo_height():
                if not scrollbar.winfo_ismapped():
                    scrollbar.pack(**pack_kwargs)
            else:
                if scrollbar.winfo_ismapped():
                    scrollbar.pack_forget()
        
        canvas.bind("<Configure>", _check, add="+")
        canvas.after(50, _check)
        
        # 鼠标滚轮滚动 - 直接在 Canvas 上绑定
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        # 当鼠标在 Canvas 或其子控件上时使用鼠标滚轮滚动
        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")
        
        canvas.bind("<Enter>", _bind_mousewheel, add="+")
        canvas.bind("<Leave>", _unbind_mousewheel, add="+")
        
        # 同时绑定到 Canvas 本身，确保在 Canvas 上直接滚动时也能工作
        canvas.bind("<MouseWheel>", _on_mousewheel, add="+")

    @staticmethod
    def _bind_area_mousewheel_to_canvas(canvas, area_widget):
        """让鼠标位于指定区域及其所有子控件上时，都能用滚轮滚动 Canvas。"""
        def _scroll_units(event):
            if getattr(event, "num", None) == 4:
                return -3
            if getattr(event, "num", None) == 5:
                return 3
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return 0
            return int(-1 * (delta / 120)) or (-1 if delta > 0 else 1)

        def _on_mousewheel(event):
            units = _scroll_units(event)
            if units:
                canvas.yview_scroll(units, "units")
            return "break"

        def _bind_recursive(widget):
            try:
                widget.bind("<MouseWheel>", _on_mousewheel, add="+")
                widget.bind("<Button-4>", _on_mousewheel, add="+")
                widget.bind("<Button-5>", _on_mousewheel, add="+")
                for child in widget.winfo_children():
                    _bind_recursive(child)
            except Exception:
                pass

        _bind_recursive(area_widget)
        canvas.bind("<MouseWheel>", _on_mousewheel, add="+")
        canvas.bind("<Button-4>", _on_mousewheel, add="+")
        canvas.bind("<Button-5>", _on_mousewheel, add="+")

    @staticmethod
    def _setup_text_auto_scroll(text_widget, scrollbar, max_lines=12, **pack_kwargs):
        """为 Text+Scrollbar 组合添加：内容自适应高度（最高max_lines行），溢出时自动显隐滑条 + 鼠标滚轮滚动。"""
        def _auto_height():
            text_widget.update_idletasks()
            end_index = text_widget.index("end-1c")
            line_count = max(int(end_index.split(".")[0]), 1)
            if line_count <= max_lines:
                text_widget.configure(height=line_count)
                if scrollbar.winfo_ismapped():
                    scrollbar.pack_forget()
            else:
                text_widget.configure(height=max_lines)
                if not scrollbar.winfo_ismapped():
                    scrollbar.pack(**pack_kwargs)
        
        def _on_modified(event=None):
            text_widget.edit_modified(False)
            _auto_height()
        
        text_widget.bind("<<Modified>>", _on_modified, add="+")
        text_widget.bind("<Configure>", lambda e: text_widget.after(50, _auto_height), add="+")
        text_widget.after(100, _auto_height)
        
        # 鼠标滚轮滚动 - 直接在 Text 控件上绑定
        def _on_mousewheel(event):
            text_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"  # 阻止事件继续传播
        
        # 当鼠标在 Text 控件或其父控件上时使用鼠标滚轮滚动
        def _bind_mousewheel(event):
            text_widget.bind_all("<MouseWheel>", _on_mousewheel)
        
        def _unbind_mousewheel(event):
            text_widget.unbind_all("<MouseWheel>")
        
        text_widget.bind("<Enter>", _bind_mousewheel, add="+")
        text_widget.bind("<Leave>", _unbind_mousewheel, add="+")
        
        # 同时绑定到 Text 控件本身
        text_widget.bind("<MouseWheel>", _on_mousewheel, add="+")

    def _add_mousewheel_support(self, widget):
        """递归地为 widget 内所有 Canvas 和 Text 控件添加鼠标滚轮支持"""
        # 为当前 widget 添加鼠标滚轮支持（如果是 Canvas 或 Text）
        if isinstance(widget, tk.Canvas):
            def _on_mousewheel(event):
                widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
            
            def _bind_mousewheel(event):
                widget.bind_all("<MouseWheel>", _on_mousewheel)
            
            def _unbind_mousewheel(event):
                widget.unbind_all("<MouseWheel>")
            
            widget.bind("<Enter>", _bind_mousewheel, add="+")
            widget.bind("<Leave>", _unbind_mousewheel, add="+")
            widget.bind("<MouseWheel>", _on_mousewheel, add="+")
        
        elif isinstance(widget, tk.Text):
            def _on_mousewheel(event):
                widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return "break"
            
            def _bind_mousewheel(event):
                widget.bind_all("<MouseWheel>", _on_mousewheel)
            
            def _unbind_mousewheel(event):
                widget.unbind_all("<MouseWheel>")
            
            widget.bind("<Enter>", _bind_mousewheel, add="+")
            widget.bind("<Leave>", _unbind_mousewheel, add="+")
            widget.bind("<MouseWheel>", _on_mousewheel, add="+")
        
        # 递归处理所有子控件
        try:
            for child in widget.winfo_children():
                self._add_mousewheel_support(child)
        except Exception:
            pass

    def _clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def _create_gradient_bg(self, canvas, width, height, c1, c2):
        steps = 100
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        for i in range(steps):
            r = r1 + (r2 - r1) * i // steps
            g = g1 + (g2 - g1) * i // steps
            b = b1 + (b2 - b1) * i // steps
            y1, y2 = i * height // steps, (i + 1) * height // steps
            canvas.create_rectangle(0, y1, width, y2, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")

    def _merge_unlocked_questions(self):
        """标记锁定题状态，不隐藏。管理员和局域网模式跳过。"""
        if self.is_admin or getattr(self, 'lan_mode', False):
            self._unlocked_ids = set()
            return
        unlocks = load_student_unlocks()
        self._unlocked_ids = set(unlocks.get(self.exam_id, []))
        for qtype in self.question_bank:
            pool = self.question_bank[qtype]
            if not isinstance(pool, list):
                continue
            for q in pool:
                qid = q.get("_qid") or q.get("id")
                q["_locked"] = bool(q.get("locked")) and qid not in self._unlocked_ids

    @staticmethod
    def _teacher_question_key(question, question_type):
        """生成题目去重键，避免每次局域网考试重复写入同一道题。"""
        def _stable_json(value):
            try:
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
            except Exception:
                return str(value)

        content = str(question.get("content", "")).strip()
        return "|".join([
            question_type,
            content,
            _stable_json(question.get("options", "")),
            _stable_json(question.get("shared_options", "")),
            _stable_json(question.get("blanks", "")),
            _stable_json(question.get("answer", "")),
            _stable_json(question.get("reference_answer", "")),
        ])

    def _find_received_folder_for_question(self, question):
        """按教师端原始题号找到本次局域网下发的素材文件夹。"""
        folder_map = getattr(self, "_lan_received_folders", {}) or {}
        for key in ("_teacher_qid", "_qid", "id"):
            qid = question.get(key)
            if qid is None:
                continue
            try:
                folder = folder_map.get(int(qid))
            except Exception:
                folder = folder_map.get(qid)
            if folder and os.path.isdir(folder):
                return folder
        return ""

    def _persist_received_material_folder(self, question, question_type):
        """把局域网下发的操作题素材复制到本地题库资源目录。"""
        if question_type not in ("web_design", "graphic_design"):
            return ""

        source_folder = self._find_received_folder_for_question(question)
        if not source_folder:
            existing_folder = _resolve_path(question.get("folder_path", ""))
            if existing_folder and os.path.isdir(existing_folder):
                return question.get("folder_path", "")
            return ""

        content = str(question.get("content", "")).strip()
        digest = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()[:12]
        qid_part = str(question.get("id") or question.get("_qid") or "q")
        safe_qid = re.sub(r"[^0-9A-Za-z_-]+", "_", qid_part).strip("_") or "q"
        target_root = os.path.join(EXAM_FILES_DIR, "lan_received", question_type)
        target_dir = os.path.join(target_root, f"{safe_qid}_{digest}")

        try:
            os.makedirs(target_root, exist_ok=True)
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            shutil.copytree(source_folder, target_dir)
            return os.path.relpath(target_dir, EXAM_DIR)
        except Exception as e:
            logger.error(f"[学生端] 保存局域网素材文件夹失败: {e}")
            return ""

    def _persist_teacher_questions_to_bank(self, questions_data, source_label="局域网教师下发"):
        """把教师端下发/开考带来的题目合并保存到学生本地题库。"""
        if not questions_data:
            return 0, 0

        incoming = []
        if isinstance(questions_data, dict):
            for question_type in QUESTION_TYPE_ORDER:
                pool = questions_data.get(question_type, [])
                if not isinstance(pool, list):
                    continue
                for question in pool:
                    if isinstance(question, dict):
                        item = dict(question)
                        item.setdefault("type", question_type)
                        incoming.append(item)
        elif isinstance(questions_data, list):
            incoming = [question for question in questions_data if isinstance(question, dict)]
        else:
            return 0, 0

        if not incoming:
            return 0, 0

        bank = load_question_bank()
        for question_type in QUESTION_TYPE_ORDER:
            if not isinstance(bank.get(question_type), list):
                bank[question_type] = []

        existing_keys = {
            question_type: {
                self._teacher_question_key(question, question_type)
                for question in bank.get(question_type, [])
                if isinstance(question, dict)
            }
            for question_type in QUESTION_TYPE_ORDER
        }

        max_ids = {}
        for question_type in QUESTION_TYPE_ORDER:
            ids = []
            for question in bank.get(question_type, []):
                try:
                    ids.append(int(question.get("id", 0)))
                except Exception:
                    pass
            max_ids[question_type] = max(ids) if ids else 999

        added = 0
        updated = 0
        for question in incoming:
            question_type = question.get("type") or question.get("_type") or "single_choice"
            if question_type not in QUESTION_TYPE_ORDER:
                continue

            saved_question = dict(question)
            for runtime_key in ("_qid", "_bank_idx", "_locked", "title", "image_data"):
                saved_question.pop(runtime_key, None)
            saved_question.pop("type", None)
            saved_question.pop("_type", None)

            saved_question.setdefault("difficulty", "中")
            saved_question.setdefault("source", source_label)
            if "reference_answer" not in saved_question:
                saved_question["reference_answer"] = {} if question_type == "network_device" else ""

            local_folder_path = self._persist_received_material_folder(question, question_type)
            if local_folder_path:
                saved_question["folder_path"] = local_folder_path

            question_key = self._teacher_question_key(saved_question, question_type)
            if question_key in existing_keys[question_type]:
                if local_folder_path:
                    for existing_question in bank.get(question_type, []):
                        if not isinstance(existing_question, dict):
                            continue
                        if self._teacher_question_key(existing_question, question_type) != question_key:
                            continue
                        old_folder = existing_question.get("folder_path", "")
                        if old_folder != local_folder_path or not _resolve_path(old_folder) or not os.path.isdir(_resolve_path(old_folder)):
                            existing_question["folder_path"] = local_folder_path
                            existing_question["source"] = existing_question.get("source") or source_label
                            updated += 1
                        break
                continue

            if not saved_question.get("id"):
                max_ids[question_type] += 1
                saved_question["id"] = max_ids[question_type]

            bank[question_type].append(saved_question)
            existing_keys[question_type].add(question_key)
            added += 1

        if added or updated:
            save_question_bank(bank)
            self.question_bank = bank
            logger.info(f"[学生端] 已保存教师下发题目 {added}/{len(incoming)} 道到本地题库，修复素材路径 {updated} 道")
        return added, len(incoming)

    def _build_questions(self):
        """根据配置从题库中选题（优先使用精准选题）"""
        bank = self.question_bank
        cfg = self.question_config
        selected = getattr(self, 'selected_qids', {})
        qid = 1
        questions = []

        type_specs = [
            ("single_choice", "C语言单选题"),
            ("fill_blank", "C语言选择填空题"),
            ("programming", "C语言编程题"),
            ("web_design", "网页制作操作题"),
            ("network_device", "网络设备安装与调试"),
            ("graphic_design", "图形图像处理操作题"),
        ]

        for qtype, title in type_specs:
            pool = [q for q in bank.get(qtype, []) if not q.get("_locked")]
            sids = selected.get(qtype, [])
            count = cfg.get(qtype, 0)

            if sids:
                # 精准选题：优先使用用户选定的题目
                # 构建ID到题目的映射，使用与_show_question_picker相同的逻辑
                id_to_q = {}
                for i, q in enumerate(pool):
                    qid_val = q.get("id", i + 1000)
                    id_to_q[qid_val] = q
                
                found_count = 0
                for sid in sids:
                    if sid in id_to_q:
                        q = dict(id_to_q[sid])
                        q["_qid"] = qid
                        q["type"] = qtype
                        q["title"] = title
                        questions.append(q)
                        qid += 1
                        found_count += 1
                        if found_count >= count:  # 达到配置数量就停止
                            break
                
                # 如果精准选题找到的题目数量不足，用循环选取补足
                if found_count < count:
                    # 先找出还未被选中的题目索引
                    used_indices = set()
                    for sid in sids:
                        if sid in id_to_q:
                            # 找到题目在pool中的索引
                            for i, q in enumerate(pool):
                                qid_val = q.get("id", i + 1000)
                                if qid_val == sid:
                                    used_indices.add(i)
                                    break
                    
                    for i in range(count - found_count):
                        if pool:
                            # 从未被选中的题目中循环选取
                            for attempt in range(len(pool)):
                                idx = (found_count + i + attempt) % len(pool)
                                if idx not in used_indices:
                                    q = dict(pool[idx])
                                    q["_qid"] = qid
                                    q["type"] = qtype
                                    q["title"] = title
                                    questions.append(q)
                                    qid += 1
                                    used_indices.add(idx)
                                    break
            else:
                # 随机不重复选择，避免 count>len(pool) 时重复出题
                import random
                indices = list(range(len(pool)))
                random.shuffle(indices)
                for i in range(count):
                    idx = indices[i % len(pool)]
                    q = dict(pool[idx])
                    q["_qid"] = qid
                    q["type"] = qtype
                    q["title"] = title
                    questions.append(q)
                    qid += 1

        return questions

    # ==================== 登录 ====================
    def _load_login_memory(self):
        """从本地缓存读取上次成功登录的信息。失败/不存在时返回空字符串。"""
        cache_path = os.path.join(EXAM_FILES_DIR, "login_cache.json")
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("exam_id", ""), data.get("id_number", "")
        except Exception:
            pass
        return "", ""

    def _save_login_memory(self, exam_id, id_number):
        """登录成功后将本次的准考证号和身份证号写入本地缓存。"""
        cache_path = os.path.join(EXAM_FILES_DIR, "login_cache.json")
        try:
            data = {}
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["exam_id"] = exam_id
            data["id_number"] = id_number
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass  # 写入失败不影响正常流程

    def _save_lan_config(self, ip, port):
        """记忆上次使用的教师机 IP 和端口。"""
        cache_path = os.path.join(EXAM_FILES_DIR, "login_cache.json")
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            data = {}
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["lan_ip"] = ip
            data["lan_port"] = port
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_lan_config(self):
        """从本地缓存读取上次使用的教师机 IP 和端口。"""
        cache_path = os.path.join(EXAM_FILES_DIR, "login_cache.json")
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("lan_ip", ""), data.get("lan_port", "")
        except Exception:
            pass
        return "", ""

    def show_login(self):
        self._clear_window()
        self.root.deiconify()
        self.root.attributes('-fullscreen', False)
        self.root.state('zoomed')
        sw, sh = self._screen_size()
        cx, cy = sw // 2, sh // 2

        canvas = tk.Canvas(self.root, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._create_gradient_bg(canvas, sw, sh, "#1a6fb5", "#0d4f8a")

        # 云朵装饰
        for cxx, cyy, rx, ry in [(150, 70, 120, 50), (250, 50, 130, 55), (sw-100, 90, 130, 55), (sw-20, 60, 120, 50)]:
            canvas.create_oval(cxx - rx, cyy - ry, cxx + rx, cyy + ry, fill="#ffffff", stipple="gray25", outline="")

        # ---- Logo ----
        logo_y = cy - 340
        logo_size = 130
        logo_r = 14
        try:
            logo_img = _assets.load_logo().resize((logo_size, logo_size), Image.LANCZOS)
            # 2x 超采样圆角底板（消除锯齿）
            from PIL import ImageDraw
            scale = 2
            pad = 4
            bg_big = Image.new("RGBA", ((logo_size + pad) * scale, (logo_size + pad) * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(bg_big)
            draw.rounded_rectangle([0, 0, (logo_size + pad) * scale - 1, (logo_size + pad) * scale - 1],
                                   radius=logo_r * scale, fill="#ffffff")
            bg = bg_big.resize((logo_size + pad, logo_size + pad), Image.LANCZOS)
            bg.paste(logo_img, (pad // 2, pad // 2), logo_img if logo_img.mode == "RGBA" else None)
            self._logo_tk = ImageTk.PhotoImage(bg)
            canvas.create_image(cx, logo_y, image=self._logo_tk, anchor="center")
        except Exception:
            pass

        canvas.create_text(cx, cy - 240, text="山东省2027年春季高考技能测试",
                          fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 28, "bold"))
        canvas.create_text(cx, cy - 190, text="网络技术类专业考试系统（模拟）",
                          fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 28, "bold"))

        form_frame = tk.Frame(canvas, bg="#0d4f8a")
        canvas.create_window(cx, cy + 30, window=form_frame)

        tk.Label(form_frame, text="准考证号：", fg=COLOR_TEXT_LIGHT, bg="#0d4f8a",
                font=("微软雅黑", 14)).grid(row=0, column=0, sticky="e", pady=10, padx=(0, 10))
        self.entry_exam_id = tk.Entry(form_frame, font=("微软雅黑", 14), width=25, bd=2, relief="solid")
        self.entry_exam_id.grid(row=0, column=1, pady=10, ipady=4)
        # 不再硬编码预填值，由 _load_login_memory() 统一处理

        tk.Label(form_frame, text="身份证号：", fg=COLOR_TEXT_LIGHT, bg="#0d4f8a",
                font=("微软雅黑", 14)).grid(row=1, column=0, sticky="e", pady=10, padx=(0, 10))
        self.entry_id_number = tk.Entry(form_frame, font=("微软雅黑", 14), width=25, bd=2, relief="solid")
        self.entry_id_number.grid(row=1, column=1, pady=10, ipady=4)
        self.entry_id_number.insert(0, "")

        # 从本地缓存加载上次成功登录的信息
        saved_exam_id, saved_id_number = self._load_login_memory()
        if saved_exam_id:
            self.entry_exam_id.insert(0, saved_exam_id)
        if saved_id_number:
            self.entry_id_number.insert(0, saved_id_number)

        tk.Button(form_frame, text="登  录", font=("微软雅黑", 16, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2", activebackground=COLOR_BTN_HOVER,
                 command=self.do_login).grid(row=2, column=0, columnspan=2, pady=(25, 5), padx=10, sticky="ew")

        # 注册 + 解锁 等宽并排
        btn_row = tk.Frame(form_frame, bg="#0d4f8a")
        btn_row.grid(row=3, column=0, columnspan=2, pady=5, padx=10, sticky="ew")
        tk.Button(btn_row, text="注册账号", font=("微软雅黑", 14, "bold"),
                 bg="#4CAF50", fg=COLOR_WHITE, bd=0, pady=8,
                 cursor="hand2", activebackground="#43A047",
                 command=self.show_register_dialog).pack(side="left", fill="x", expand=True, padx=(0, 3))
        tk.Button(btn_row, text="解锁题目", font=("微软雅黑", 14, "bold"),
                 bg="#4CAF50", fg=COLOR_WHITE, bd=0, pady=8,
                 cursor="hand2", activebackground="#43A047",
                 command=self.show_activation_dialog).pack(side="left", fill="x", expand=True, padx=(3, 0))

        tk.Button(form_frame, text="局域网考试", font=("微软雅黑", 14, "bold"),
                 bg="#1565C0", fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2", activebackground=COLOR_BTN_HOVER,
                 command=self.show_lan_connect_dialog).grid(row=4, column=0, columnspan=2, pady=5, padx=10, sticky="ew")

        # 回车键触发登录
        self.entry_exam_id.bind("<Return>", lambda e: self.do_login())
        self.entry_id_number.bind("<Return>", lambda e: self.do_login())

        # 底部信息
        canvas.create_text(cx, sh - 30, text="v2.1 | 管理员/考生账号分离 | 全屏考试",
                          fill="#7fb3d8", font=("微软雅黑", 10))

        # ---- 微信二维码 ----
        try:
            qr_img = _assets.load_qrcode()
            qr_w, qr_h = qr_img.size
            qr_target = 320
            ratio = qr_target / max(qr_w, qr_h)
            qr_img = qr_img.resize((int(qr_w * ratio), int(qr_h * ratio)), Image.LANCZOS)
            self._qr_tk = ImageTk.PhotoImage(qr_img)
            qr_x = sw - 200
            qr_y = sh - 220
            canvas.create_image(qr_x, qr_y, image=self._qr_tk, anchor="center")
            canvas.create_text(qr_x, qr_y + qr_target // 2 + 20,
                              text="领取资料或咨询请扫码添加微信", fill=COLOR_TEXT_LIGHT,
                              font=("微软雅黑", 16, "bold"))
        except Exception:
            pass

        self.entry_exam_id.focus()

    def _show_loading_toast(self, text="正在加载..."):
        """显示一个轻量级加载提示窗口（非阻塞）"""
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        sw, sh = self._screen_size()
        tw, th = 300, 80
        toast.geometry(f"{tw}x{th}+{sw//2-tw//2}+{sh//2-th//2}")
        toast.configure(bg="#1a6fb5")
        frame = tk.Frame(toast, bg="#1a6fb5")
        frame.pack(expand=True, fill="both", padx=2, pady=2)
        tk.Label(frame, text=text, font=("微软雅黑", 14, "bold"),
                fg="white", bg="#1a6fb5").pack(expand=True)
        toast.update()
        return toast

    def do_login(self):
        exam_id = self.entry_exam_id.get().strip()
        id_number = self.entry_id_number.get().strip()
        if not exam_id or not id_number:
            messagebox.showwarning("提示", "请输入准考证号和身份证号！")
            return

        # 硬编码超级管理员优先验证 — 即使 accounts.json 丢失也能登录
        if exam_id == _BUILTIN_ADMIN_ID and id_number == _BUILTIN_ADMIN["password"]:
            self.exam_id = exam_id
            self.id_number = id_number
            self.user_name = _BUILTIN_ADMIN["name"]
            self.is_admin = True
            self._save_login_memory(exam_id, id_number)
            # ★ 题库在此加载（启动时已跳过），显示加载提示
            toast = self._show_loading_toast("正在加载题库，请稍候...")
            self.root.update()
            self.question_bank = load_question_bank()
            self._merge_unlocked_questions()
            self._load_folder_map()
            toast.destroy()
            self.show_admin_panel()
            return

        # 检查账号
        self.accounts = load_accounts()
        account = self.accounts.get(exam_id)
        if account and account.get("password") == id_number:
            self.exam_id = exam_id
            self.id_number = id_number
            self.user_name = account.get("name", exam_id)
            self.is_admin = account.get("is_admin", False)
            self._save_login_memory(exam_id, id_number)
            # ★ 题库在此加载（启动时已跳过），显示加载提示
            toast = self._show_loading_toast("正在加载题库，请稍候...")
            self.root.update()
            self.question_bank = load_question_bank()
            # 合并已解锁的加量题
            self._merge_unlocked_questions()
            self._load_folder_map()
            toast.destroy()

            if self.is_admin:
                self.show_admin_panel()
            else:
                self.show_config()
        else:
            messagebox.showwarning("提示", "准考证号或身份证号错误！")

    def show_register_dialog(self):
        """注册新用户（仅限普通用户）"""
        dlg = tk.Toplevel(self.root)
        dlg.title("注册新用户")
        dlg.geometry("450x380")
        dlg.resizable(True, True)
        dlg.minsize(400, 320)
        dlg.configure(bg=COLOR_BG)
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-225}+{sh//2-190}")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="注册新用户", font=("微软雅黑", 16, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 10))

        form = tk.Frame(dlg, bg=COLOR_BG)
        form.pack(padx=30)

        tk.Label(form, text="准考证号：", font=("微软雅黑", 12),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        exam_id_var = tk.StringVar()
        tk.Entry(form, textvariable=exam_id_var, font=("微软雅黑", 12),
                width=28, bd=1, relief="solid").pack(pady=(0, 8), ipady=3)

        tk.Label(form, text="姓　　名：", font=("微软雅黑", 12),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        name_var = tk.StringVar()
        tk.Entry(form, textvariable=name_var, font=("微软雅黑", 12),
                width=28, bd=1, relief="solid").pack(pady=(0, 8), ipady=3)

        tk.Label(form, text="身份证号：", font=("微软雅黑", 12),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        id_var = tk.StringVar()
        tk.Entry(form, textvariable=id_var, font=("微软雅黑", 12),
                width=28, bd=1, relief="solid").pack(pady=(0, 10), ipady=3)

        def do_register():
            exam_id = exam_id_var.get().strip()
            name = name_var.get().strip()
            id_number = id_var.get().strip()
            if not exam_id or not name or not id_number:
                messagebox.showwarning("提示", "所有字段均为必填")
                return
            accounts = load_accounts()
            if exam_id in accounts:
                messagebox.showwarning("提示", "该准考证号已注册")
                return
            accounts[exam_id] = {
                "password": id_number,
                "name": name,
                "is_admin": False
            }
            save_accounts(accounts)
            messagebox.showinfo("成功", f"注册成功！准考证号：{exam_id}")
            dlg.destroy()

        tk.Button(dlg, text="确认注册", font=("微软雅黑", 13, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=30, pady=6,
                 cursor="hand2", command=do_register).pack(pady=(5, 10))

    def show_activation_dialog(self):
        """学生输入激活码解锁题目"""
        exam_id = self.entry_exam_id.get().strip()
        if not exam_id:
            messagebox.showwarning("提示", "请先输入准考证号！")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("解锁题目")
        dlg.geometry("440x300")
        dlg.resizable(True, True)
        dlg.minsize(380, 240)
        dlg.configure(bg=COLOR_BG)
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-220}+{sh//2-130}")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="输入激活码解锁加量题目", font=("微软雅黑", 14, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(20, 5))
        tk.Label(dlg, text="激活码由老师通过微信下发", font=("微软雅黑", 10),
                fg="#888", bg=COLOR_BG).pack(pady=(0, 15))

        entry_frame = tk.Frame(dlg, bg=COLOR_BG)
        entry_frame.pack()
        code_var = tk.StringVar()
        code_entry = tk.Entry(entry_frame, textvariable=code_var, font=("Consolas", 20),
                              width=14, justify="center", bd=2, relief="solid")
        code_entry.pack(ipady=6)

        result_var = tk.StringVar()
        result_lbl = tk.Label(dlg, textvariable=result_var, font=("微软雅黑", 11),
                             fg=COLOR_SUCCESS, bg=COLOR_BG)
        result_lbl.pack(pady=10)

        def do_activate():
            code = code_var.get().strip().upper()
            if not code or len(code) < 7:
                messagebox.showwarning("提示", "请输入完整激活码")
                return
            qids = use_activation_code(code, exam_id)
            if qids is None:
                result_var.set("激活码无效或已被使用")
                result_lbl.configure(fg=COLOR_DANGER)
            else:
                result_var.set(f"成功解锁 {len(qids)} 道加量题！")
                result_lbl.configure(fg=COLOR_SUCCESS)
                code_entry.configure(state="disabled")

        tk.Button(dlg, text="激活", font=("微软雅黑", 14, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=30, pady=6,
                 cursor="hand2", command=do_activate).pack(pady=(5, 10))

        code_entry.bind("<Return>", lambda e: do_activate())
        code_entry.focus()

    def show_lan_connect_dialog(self):
        """弹出连接教师机窗口"""
        exam_id = self.entry_exam_id.get().strip()
        id_number = self.entry_id_number.get().strip()
        if not exam_id or not id_number:
            messagebox.showwarning("提示", "请先在登录界面输入准考证号和身份证号！")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("连接教师机")
        dialog.geometry("420x320")
        dialog.resizable(True, True)
        dialog.minsize(380, 280)
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self.root)

        sw, sh = self._screen_size()
        dialog.geometry(f"+{sw//2-200}+{sh//2-150}")

        tk.Label(dialog, text="连接教师机", font=("微软雅黑", 18, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(20, 15))

        # 加载上次记忆的 IP 和端口
        saved_ip, saved_port = self._load_lan_config()
        default_ip = saved_ip if saved_ip else "127.0.0.1"
        default_port = saved_port if saved_port else "8888"

        # IP 地址
        ip_frame = tk.Frame(dialog, bg=COLOR_BG)
        ip_frame.pack(pady=5)
        tk.Label(ip_frame, text="教师机IP：", font=("微软雅黑", 12),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(side="left", padx=(0, 10))
        ip_var = tk.StringVar(value=default_ip)
        ip_entry = tk.Entry(ip_frame, textvariable=ip_var, font=("微软雅黑", 12), width=20, bd=1, relief="solid")
        ip_entry.pack(side="left", ipady=3)

        # 端口号
        port_frame = tk.Frame(dialog, bg=COLOR_BG)
        port_frame.pack(pady=5)
        tk.Label(port_frame, text="端口号：", font=("微软雅黑", 12),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(side="left", padx=(0, 10))
        port_var = tk.StringVar(value=default_port)
        port_entry = tk.Entry(port_frame, textvariable=port_var, font=("微软雅黑", 12), width=10, bd=1, relief="solid")
        port_entry.pack(side="left", ipady=3)

        # 状态标签
        status_var = tk.StringVar()
        status_label = tk.Label(dialog, textvariable=status_var, font=("微软雅黑", 10),
                               fg=COLOR_ACCENT, bg=COLOR_BG)
        status_label.pack(pady=(10, 5))

        def do_connect():
            host = ip_var.get().strip()
            port_str = port_var.get().strip()
            if not host:
                messagebox.showwarning("提示", "请输入教师机IP地址！")
                return
            try:
                port = int(port_str)
            except ValueError:
                messagebox.showwarning("提示", "端口号必须为数字！")
                return

            # 记忆本次输入的 IP 和端口，下次自动填入
            self._save_lan_config(host, port_str)

            status_var.set("正在连接教师机...")
            status_label.config(fg=COLOR_ACCENT)
            dialog.update()

            def _check_timeout(prev_status):
                """5秒后仍无回复则提示超时"""
                if status_var.get() == prev_status and self.network_client and self.network_client.is_connected:
                    messagebox.showerror("连接超时", "已发送登录请求但教师端未回复。\n请确认：\n1. 教师端已点击「开始监听」\n2. 端口号一致（默认 8888）")
                    status_var.set("连接超时：教师端无响应")
                    status_label.config(fg=COLOR_DANGER)

            try:
                self.exam_id = exam_id
                self.id_number = id_number
                # 检查账号
                self.accounts = load_accounts()
                account = self.accounts.get(exam_id)
                if account:
                    self.user_name = account.get("name", exam_id)
                else:
                    self.user_name = exam_id

                # 创建客户端（传入正确的姓名和准考证号）
                self.network_client = Client(
                    student_name=self.user_name,
                    exam_id=self.exam_id,
                    id_number=self.id_number
                )

                # 注册消息处理器（必须在 connect 之前，因为 connect 会自动发 LOGIN）
                self.network_client.register_handler("LOGIN_RESP", on_login_resp)
                self._register_lan_handlers()

                # 连接到教师机（connect 会自动发送 LOGIN）
                logger.info(f"尝试连接 {host}:{port}, exam_id={self.exam_id}, name={self.user_name}")
                self.network_client.connect(host, port)
                if self.network_client.is_connected:
                    logger.info("TCP连接成功，已发送 LOGIN")
                    status_var.set("已发送登录请求，等待教师确认...")
                    # 5 秒超时：若无回复则提示
                    self._conn_timeout_id = dialog.after(APP_CONFIG.get("connect_timeout_ms", 5000), lambda: _check_timeout(status_var.get()))
                else:
                    logger.error("TCP连接失败")
                    messagebox.showerror("连接失败", "无法连接到教师机。\n请确认：\n1. 教师端已点击「开始监听」\n2. IP 地址和端口号正确")
                    status_var.set("连接失败：无法连接到教师机")
                    status_label.config(fg=COLOR_DANGER)
                    self.network_client = None
            except Exception as e:
                logger.error(f"连接异常: {e}")
                import traceback
                traceback.print_exc()
                status_var.set(f"连接失败：{str(e)}")
                status_label.config(fg=COLOR_DANGER)
                self.network_client = None

        def on_login_resp(data):
            """处理连接阶段的 LOGIN_RESP"""
            if data.get("status") == "OK":
                if getattr(self, "lan_mode", False):
                    self._lan_disconnected = False
                    self.root.after(0, lambda: self._show_time_warning(
                        "网络已恢复，已重新连接教师端", is_critical=False))
                    self.root.after(0, self._send_lan_status)
                    return
                # 必须通过 root.after 在主线程执行 tkinter 操作，避免线程安全问题
                def _on_login_ok():
                    if hasattr(self, '_conn_timeout_id') and self._conn_timeout_id:
                        try:
                            dialog.after_cancel(self._conn_timeout_id)
                        except Exception:
                            pass
                        self._conn_timeout_id = None
                    try:
                        dialog.destroy()
                    except Exception:
                        pass
                    self.show_lan_waiting()
                self.root.after(0, _on_login_ok)
            else:
                def _on_login_fail():
                    status_var.set(f"登录失败：{data.get('reason', '教师拒绝了连接')}")
                    status_label.config(fg=COLOR_DANGER)
                    if self.network_client:
                        self.network_client.disconnect()
                        self.network_client = None
                self.root.after(0, _on_login_fail)

        btn_frame = tk.Frame(dialog, bg=COLOR_BG)
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="连  接", font=("微软雅黑", 13, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=25, pady=6,
                 cursor="hand2", activebackground=COLOR_BTN_HOVER,
                 command=do_connect).pack(side="left", padx=(0, 15))

        def _cancel_connect():
            if hasattr(self, '_conn_timeout_id') and self._conn_timeout_id:
                dialog.after_cancel(self._conn_timeout_id)
                self._conn_timeout_id = None
            dialog.destroy()

        tk.Button(btn_frame, text="取  消", font=("微软雅黑", 13),
                 bg="#e0e0e0", fg=COLOR_TEXT_DARK, bd=0, padx=25, pady=6,
                 cursor="hand2", command=_cancel_connect).pack(side="left")

    def _register_lan_handlers(self):
        """注册局域网考试消息处理器"""
        if not self.network_client:
            return

        # 注册断线回调
        def on_disconnect(data=None):
            self._lan_disconnected = True
            logger.warning("检测到网络断开")
            self.root.after(0, lambda: self._show_time_warning(
                "网络已断开，可继续答题，答案不会丢失", is_critical=False))
        self.network_client.register_handler("DISCONNECTED", on_disconnect)

        def on_exam_start(data):
            """收到 EXAM_START"""
            print(f"[学生端 on_exam_start] ★★★ 收到 EXAM_START！题目数={len(data.get('questions', []))}，data keys={list(data.keys())}", flush=True)
            logger.info(f"[学生端] 收到 EXAM_START，题目数={len(data.get('questions', []))}")
            # ★ 用 root.after 安全地在主线程更新 UI 和启动考试
            def _safe_start():
                self.lan_status_var.set(f"★★★ 收到考试！题目数={len(data.get('questions', []))}")
                self.start_lan_exam(data)
            self.root.after(0, _safe_start)

        def on_time_extend(data):
            """收到 TIME_EXTEND"""
            extend_minutes = data.get("extend_minutes", 0)
            with self._timer_lock:
                self.exam_duration += extend_minutes * 60
                self.time_remaining += extend_minutes * 60
            self.root.after(0, lambda: self._update_timer_display())
            self.root.after(0, lambda: self._show_time_warning(
                f"教师已延长考试时间 +{extend_minutes} 分钟，剩余 {self.time_remaining//60} 分钟",
                is_critical=False))

        def on_force_submit(data):
            """收到 FORCE_SUBMIT"""
            self.root.after(0, self._do_lan_force_submit)

        def on_broadcast_msg(data):
            """收到 BROADCAST_MSG"""
            msg_text = data.get("message", "")
            # 更新学生人数（如有）
            if "student_count" in data:
                self.lan_students_count = data["student_count"]
            if msg_text:
                self.root.after(0, lambda: self._show_time_warning(msg_text, is_critical=False))

        def on_exam_over(data):
            """收到 EXAM_OVER"""
            self.root.after(0, lambda: messagebox.showinfo("考试结束", "教师已结束考试，系统将自动交卷。"))
            self.root.after(500, self.submit_all)

        def on_submit_ack(data):
            """收到 SUBMIT_ACK"""
            if self._submit_ack_timeout_id:
                self.root.after_cancel(self._submit_ack_timeout_id)
                self._submit_ack_timeout_id = None
            self.root.after(0, self._do_submit)

        def on_countdown(data):
            """收到 COUNTDOWN 倒计时消息"""
            if self._lan_countdown_label is None:
                return
            cancelled = data.get("cancelled", False)
            if cancelled:
                self.root.after(0, lambda: self._lan_countdown_label.configure(
                    text="定时已取消"))
                return
            remaining = data.get("remaining_seconds", 0)
            mins, secs = divmod(remaining, 60)
            self.root.after(0, lambda: self._lan_countdown_label.configure(
                text=f"考试将在 {mins:02d}:{secs:02d} 后自动开始"))

        def on_question_bank_sync(data):
            """收到教师下发的题目，保存到本地题库"""
            import shutil
            questions = data.get("questions", [])
            sync_mode = data.get("sync_mode", "selected")
            timestamp = data.get("timestamp", "")
            folders_data = data.get("folders", {})
            if not questions:
                return
            self._last_question_bank_sync_questions = questions

            # 解压教师端下发的文件夹数据
            if folders_data:
                self._extract_lan_folders(folders_data)

            # 解压教师端下发的配图（从 image_data 字段还原到本地 IMAGE_DIR）
            if isinstance(questions, list):
                questions_all = questions
            elif isinstance(questions, dict):
                questions_all = []
                for type_key in QUESTION_TYPE_ORDER:
                    questions_all.extend(questions.get(type_key, []))
            else:
                questions_all = []
            img_count = 0
            for q in questions_all:
                img_data = q.pop("image_data", None)
                if not img_data:
                    continue
                filename = img_data.get("filename", "")
                b64_data = img_data.get("data", "")
                if not filename or not b64_data:
                    continue
                try:
                    os.makedirs(IMAGE_DIR, exist_ok=True)
                    dest_path = os.path.join(IMAGE_DIR, filename)
                    with open(dest_path, "wb") as f:
                        f.write(base64.b64decode(b64_data))
                    # 更新题目中的 image 字段为本地相对路径
                    q["image"] = os.path.join("question_images", filename)
                    img_count += 1
                except Exception as e:
                    logger.error(f"保存配图失败 {filename}: {e}")
            if img_count > 0:
                logger.info(f"已从教师端接收并保存 {img_count} 张配图")

            # 确定保存路径（使用 BANK_FILE，持久目录）
            bank_path = BANK_FILE

            try:
                if sync_mode == "full":
                    # 完整题库替换
                    with open(bank_path, "w", encoding="utf-8") as f:
                        json.dump(questions, f, ensure_ascii=False, indent=2)
                    q_count = sum(len(questions.get(k, [])) for k in QUESTION_TYPE_ORDER) if isinstance(questions, dict) else len(questions)
                    msg = f"教师已下发完整题库（{timestamp}），已保存到本地"
                else:
                    # 增量合并：按题目内容去重，避免不同电脑题目 id 撞车导致漏保存
                    added_count, q_count = self._persist_teacher_questions_to_bank(
                        questions, source_label="局域网教师下发"
                    )
                    if added_count:
                        msg = f"教师已下发 {q_count} 道题（{timestamp}），新增 {added_count} 道，已合并到本地题库"
                    else:
                        msg = f"教师已下发 {q_count} 道题（{timestamp}），本地题库已存在这些题"

                # 更新内存中的题库（如果已加载）
                if hasattr(self, 'question_bank') and self.question_bank is not None:
                    try:
                        self.question_bank = load_question_bank()
                    except Exception:
                        pass

                # 显示通知（主线程）
                def _show_notification():
                    # 在等待界面显示通知
                    if hasattr(self, '_lan_countdown_label') and self._lan_countdown_label:
                        self._show_time_warning(msg, is_critical=False)
                    messagebox.showinfo("题目已接收", msg)

                self.root.after(0, _show_notification)
                logger.info(f"题目同步完成: {msg}")

            except Exception as e:
                logger.error(f"保存下发题目失败: {e}")
                self.root.after(0, lambda: messagebox.showerror(
                    "接收失败", f"保存教师下发的题目时出错：\n{e}"))

        self.network_client.register_handler("EXAM_START", on_exam_start)
        self.network_client.register_handler("TIME_EXTEND", on_time_extend)
        self.network_client.register_handler("FORCE_SUBMIT", on_force_submit)
        self.network_client.register_handler("BROADCAST_MSG", on_broadcast_msg)
        self.network_client.register_handler("EXAM_OVER", on_exam_over)
        self.network_client.register_handler("SUBMIT_ACK", on_submit_ack)
        self.network_client.register_handler("COUNTDOWN", on_countdown)
        self.network_client.register_handler("QUESTION_BANK_SYNC", on_question_bank_sync)
        self.network_client.register_handler("FOLDER_STREAM_START", self._on_folder_stream_start)
        self.network_client.register_handler("FOLDER_STREAM_CHUNK", self._on_folder_stream_chunk)
        self.network_client.register_handler("FOLDER_STREAM_END", self._on_folder_stream_end)

        # 清理超过 7 天的离线缓存文件
        self._cleanup_old_cache()

    def _extract_lan_folders(self, folders_data):
        """从教师端下发的 base64 zip 数据中解压文件夹到临时目录。

        将每个题目的文件夹数据解码、解压到 exam_cache/l_folders/qid/ 目录，
        并将路径存入 self._lan_received_folders，供 _setup_its_data 使用。

        Args:
            folders_data: dict, {str(qid): "base64_zip_string"}
        """
        if not folders_data:
            return

        if not hasattr(self, '_lan_received_folders') or self._lan_received_folders is None:
            self._lan_received_folders = {}

        base_dir = os.path.join(EXAM_CACHE_DIR, "l_folders")
        os.makedirs(base_dir, exist_ok=True)

        for qid_str, b64_data in folders_data.items():
            if not b64_data:
                continue
            try:
                zip_bytes = base64.b64decode(b64_data)
                target_dir = os.path.join(base_dir, f"q{qid_str}")
                # 清理旧目录
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir, ignore_errors=True)
                os.makedirs(target_dir, exist_ok=True)

                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    _safe_extract_zip(zf, target_dir, max_total_size=2 * 1024 * 1024 * 1024)

                self._lan_received_folders[int(qid_str)] = target_dir
                logger.info(f"[学生端] 解压题目 {qid_str} 文件夹到 {target_dir}")
            except Exception as e:
                logger.error(f"[学生端] 解压题目 {qid_str} 文件夹失败: {e}")
                print(f"[学生端] 解压题目 {qid_str} 文件夹失败: {e}", flush=True)

        # 解压完成后立即持久化映射表
        self._save_folder_map()


    def _on_folder_stream_start(self, data):
        """开始接收教师端分块下发的题目素材包。"""
        try:
            if not hasattr(self, "_folder_streams") or self._folder_streams is None:
                self._folder_streams = {}
            batch_id = str(data.get("batch_id", "default"))
            qid = int(data.get("qid"))
            key = f"{batch_id}:{qid}"
            stream_dir = os.path.join(EXAM_CACHE_DIR, "incoming_streams", batch_id)
            os.makedirs(stream_dir, exist_ok=True)
            zip_path = os.path.join(stream_dir, f"q{qid}.zip.part")
            if os.path.exists(zip_path):
                os.remove(zip_path)
            self._folder_streams[key] = {
                "qid": qid,
                "path": zip_path,
                "size": int(data.get("size", 0) or 0),
                "sha256": data.get("sha256", ""),
                "received": 0,
                "chunks": 0,
                "last_percent": -1,
            }
            size_mb = self._folder_streams[key]["size"] / 1024 / 1024
            self._show_lan_transfer_status(
                f"正在接收题目 {qid} 素材：0%（约 {size_mb:.1f}MB）"
            )
            logger.info(f"[学生端] 开始接收题目 {qid} 素材包")
        except Exception as e:
            logger.error(f"[学生端] 初始化素材包接收失败: {e}")

    def _show_lan_transfer_status(self, message):
        """在等待页/考试页显示局域网素材或交卷传输状态。"""
        def _update():
            updated = False
            try:
                label = getattr(self, "_lan_countdown_label", None)
                if label and label.winfo_exists():
                    label.configure(text=message)
                    updated = True
            except Exception:
                pass
            try:
                status_var = getattr(self, "lan_status_var", None)
                if status_var is not None:
                    status_var.set(message)
                    updated = True
            except Exception:
                pass
            if not updated:
                logger.info(message)
        try:
            self.root.after(0, _update)
        except Exception:
            logger.info(message)

    def _on_folder_stream_chunk(self, data):
        """接收教师端分块下发的题目素材包数据块。"""
        try:
            batch_id = str(data.get("batch_id", "default"))
            qid = int(data.get("qid"))
            key = f"{batch_id}:{qid}"
            stream = getattr(self, "_folder_streams", {}).get(key)
            if not stream:
                self._on_folder_stream_start({
                    "batch_id": batch_id,
                    "qid": qid,
                    "size": 0,
                    "sha256": "",
                })
                stream = self._folder_streams.get(key)
            if not stream:
                return
            chunk = base64.b64decode(data.get("data", ""))
            with open(stream["path"], "ab") as f:
                f.write(chunk)
            stream["received"] += len(chunk)
            stream["chunks"] += 1
            total_size = int(stream.get("size", 0) or 0)
            if total_size > 0:
                percent = min(100, int(stream["received"] * 100 / total_size))
                bucket = percent // 5
                if bucket != stream.get("last_percent", -1):
                    stream["last_percent"] = bucket
                    self._show_lan_transfer_status(
                        f"正在接收题目 {qid} 素材：{percent}%"
                    )
        except Exception as e:
            logger.error(f"[学生端] 接收素材包数据块失败: {e}")

    def _on_folder_stream_end(self, data):
        """素材包接收完毕后校验、解压并写入本地映射。"""
        try:
            batch_id = str(data.get("batch_id", "default"))
            qid = int(data.get("qid"))
            key = f"{batch_id}:{qid}"
            stream = getattr(self, "_folder_streams", {}).pop(key, None)
            if not stream:
                logger.warning(f"[学生端] 未找到题目 {qid} 的素材包接收记录")
                return

            expected_size = int(data.get("size", stream.get("size", 0)) or 0)
            actual_size = os.path.getsize(stream["path"]) if os.path.exists(stream["path"]) else 0
            if expected_size and actual_size != expected_size:
                raise ValueError(f"素材包大小不一致: {actual_size}/{expected_size}")

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
                    raise ValueError("素材包校验失败")

            base_dir = os.path.join(EXAM_CACHE_DIR, "l_folders")
            os.makedirs(base_dir, exist_ok=True)
            target_dir = os.path.join(base_dir, f"q{qid}")
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            os.makedirs(target_dir, exist_ok=True)

            with zipfile.ZipFile(stream["path"]) as zf:
                _safe_extract_zip(zf, target_dir, max_total_size=2 * 1024 * 1024 * 1024)

            if not hasattr(self, "_lan_received_folders") or self._lan_received_folders is None:
                self._lan_received_folders = {}
            self._lan_received_folders[qid] = target_dir
            self._save_folder_map()

            questions = getattr(self, "_last_question_bank_sync_questions", None)
            if questions:
                try:
                    self._persist_teacher_questions_to_bank(
                        questions, source_label="局域网教师下发"
                    )
                except Exception as persist_error:
                    logger.error(f"[学生端] 素材接收后更新本地题库失败: {persist_error}")

            logger.info(f"[学生端] 题目 {qid} 素材包接收完成: {target_dir}")
            self._show_lan_transfer_status(f"题目 {qid} 素材接收完成")
        except Exception as e:
            logger.error(f"[学生端] 完成素材包接收失败: {e}")
            print(f"[学生端] 完成素材包接收失败: {e}", flush=True)
            self._show_lan_transfer_status(f"题目 {qid if 'qid' in locals() else ''} 素材接收失败")


    def _cleanup_old_cache(self):
        """清理 exam_cache 中超过 7 天的旧缓存文件。"""
        import time as _time
        if not os.path.exists(EXAM_CACHE_DIR):
            return
        cutoff = _time.time() - 7 * 24 * 3600
        for fname in os.listdir(EXAM_CACHE_DIR):
            fpath = os.path.join(EXAM_CACHE_DIR, fname)
            try:
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
            except OSError:
                pass

    def _save_folder_map(self):
        """将 _lan_received_folders 映射到磁盘，重启后恢复。

        在 _extract_lan_folders() 之后调用，确保文件夹解压后立马持久化。
        """
        if not hasattr(self, '_lan_received_folders') or not self._lan_received_folders:
            return
        try:
            os.makedirs(EXAM_CACHE_DIR, exist_ok=True)
            map_path = os.path.join(EXAM_CACHE_DIR, "folder_map.json")
            # int 键转 str（JSON 要求）
            data = {str(k): v for k, v in self._lan_received_folders.items()}
            with open(map_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            logger.info(f"[_save_folder_map] 已保存 {len(data)} 个文件夹映射")
        except Exception as e:
            logger.error(f"_save_folder_map 失败: {e}")

    def _load_folder_map(self):
        """从磁盘恢复 _lan_received_folders 映射（启动时调用）。"""
        if not hasattr(self, '_lan_received_folders') or self._lan_received_folders is None:
            self._lan_received_folders = {}
        try:
            map_path = os.path.join(EXAM_CACHE_DIR, "folder_map.json")
            if not os.path.exists(map_path):
                return
            with open(map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # str 键转回 int
            restored = {int(k): v for k, v in data.items()}
            self._lan_received_folders.update(restored)
            logger.info(f"[_load_folder_map] 恢复 {len(restored)} 个文件夹映射")
        except Exception as e:
            logger.error(f"_load_folder_map 失败: {e}")

    # ==================== 局域网考试：等待界面 ====================
    def show_lan_waiting(self):
        """局域网考试等待界面"""
        self._clear_window()
        self.root.deiconify()
        self.root.state('zoomed')
        sw, sh = self._screen_size()
        cx, cy = sw // 2, sh // 2

        canvas = tk.Canvas(self.root, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._create_gradient_bg(canvas, sw, sh, "#1a6fb5", "#0d4f8a")

        canvas.create_text(cx, cy - 120, text="已连接到教师机",
                          fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 30, "bold"))
        canvas.create_text(cx, cy - 60, text="等待考试开始...",
                          fill="#ffcc00", font=("微软雅黑", 22))

        # 考生信息
        info_lines = [
            f"准考证号：{self.exam_id}",
            f"姓名：{self.user_name}",
        ]
        for i, line in enumerate(info_lines):
            canvas.create_text(cx, cy + 10 + i * 35, text=line,
                              fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 14))

        # 学生人数
        self.lan_status_var = tk.StringVar(value="")
        tk.Label(canvas, textvariable=self.lan_status_var, font=("微软雅黑", 12),
                fg="#7fb3d8", bg="#0d4f8a").place(x=cx, y=cy + 100, anchor="center")

        # 倒计时显示（教师定时开始考试时显示）
        self._lan_countdown_label = tk.Label(
            canvas, text="", font=("微软雅黑", 16, "bold"),
            fg="#ffcc00", bg="#0d4f8a")
        self._lan_countdown_label.place(x=cx, y=cy + 135, anchor="center")

        canvas.create_text(cx, sh - 30, text="请耐心等待教师开始考试 | ESC 退出全屏",
                          fill="#7fb3d8", font=("微软雅黑", 10))

        # 定期更新状态显示
        def update_status():
            if self.lan_mode:
                return  # 已进入考试，停止
            cnt = self.lan_students_count
            if cnt > 0:
                self.lan_status_var.set(f"当前已连接 {cnt} 名学生")
            else:
                self.lan_status_var.set("等待中...")
            self.root.after(3000, update_status)
        self.root.after(3000, update_status)

    # ==================== 局域网考试模式 ====================
    def start_lan_exam(self, exam_data):
        """局域网考试开始"""
        print(f"[学生端 start_lan_exam] ★★★ 进入 start_lan_exam！题目数={len(exam_data.get('questions', []))}", flush=True)
        logger.info(f"[学生端] start_lan_exam，题目数={len(exam_data.get('questions', []))}")
        try:
            self.lan_mode = True

            # 先解压教师端下发的文件夹数据，后续保存题库时可写入本地素材路径
            folders_data = exam_data.get("folders", {})
            if folders_data:
                print(f"[学生端] 收到文件夹数据: {len(folders_data)} 个", flush=True)
                self._extract_lan_folders(folders_data)
            else:
                print(f"[学生端] 未收到文件夹数据", flush=True)

            # 解析老师下发的题目，并永久合并到学生本地题库
            raw_questions_data = exam_data.get("questions", [])
            self._current_its_session_id = "lan-{exam_id}-{start}-{count}".format(
                exam_id=self.exam_id,
                start=exam_data.get("start_time", ""),
                count=len(raw_questions_data) if isinstance(raw_questions_data, list)
                else sum(len(raw_questions_data.get(k, [])) for k in QUESTION_TYPE_ORDER)
            )
            try:
                self._persist_teacher_questions_to_bank(
                    raw_questions_data, source_label="局域网考试"
                )
            except Exception as persist_error:
                logger.error(f"[学生端] 保存局域网考试题目失败: {persist_error}")

            if isinstance(raw_questions_data, dict):
                questions_data = []
                for question_type in QUESTION_TYPE_ORDER:
                    for question in raw_questions_data.get(question_type, []):
                        if isinstance(question, dict):
                            item = dict(question)
                            item.setdefault("type", question_type)
                            questions_data.append(item)
            else:
                questions_data = raw_questions_data or []
            self.questions = []
            for q in questions_data:
                qc = dict(q)
                if "_teacher_qid" not in qc and "_qid" in qc:
                    qc["_teacher_qid"] = qc.get("_qid")
                qc["_qid"] = len(self.questions) + 1
                if "type" not in qc or qc.get("type") is None:
                    qc["type"] = qc.get("_type", "single_choice")
                if "_type" not in qc or qc.get("_type") is None:
                    qc["_type"] = qc.get("type", "single_choice")
                # 调试：记录收到的题目类型
                if qc["type"] == "web_design":
                    print(f"[start_lan_exam] web_design题: _qid={qc['_qid']}, content={str(qc.get('content',''))[:40]}", flush=True)
                self.questions.append(qc)

            # 考试时长（秒）
            duration_minutes = exam_data.get("duration_minutes", 60)
            remaining_seconds = exam_data.get("remaining_seconds")
            self.exam_duration = duration_minutes * 60
            if remaining_seconds is not None:
                try:
                    self.time_remaining = max(1, int(remaining_seconds))
                except Exception:
                    self.time_remaining = self.exam_duration
            else:
                self.time_remaining = self.exam_duration

            # 重置/恢复作答状态：断线重连补发试卷时保留本机已有答案
            reconnect_resume = bool(exam_data.get("reconnect_resume"))
            if not reconnect_resume:
                self.answers = {}
                self.submitted = set()
                self.marked = set()
                self.current_question = 0
            else:
                self.answers = getattr(self, "answers", {}) or {}
                self.submitted = getattr(self, "submitted", set()) or set()
                self.marked = getattr(self, "marked", set()) or set()
                self.current_question = min(getattr(self, "current_question", 0), max(0, len(self.questions) - 1))
            self.lan_students_count = exam_data.get("student_count", 0)
            self._locked_question = None
            self._float_window = None
            # 注意：不清空 _lan_received_folders，
            # on_question_bank_sync 可能已提前解压好文件夹

            self.exam_started = True
            self.timer_running = True

            # 显示考试界面
            self.show_exam_interface()
            self._start_timer()

            # 初始化 ITSData（使用教师端下发的文件夹内容）
            self._setup_its_data()

            logger.info(f"[学生端] start_lan_exam 完成")

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"start_lan_exam 异常: {e}\n{traceback.format_exc()}")


    def _send_lan_status(self):
        """发送学生状态给教师端（含断线重连检测）"""
        if not self.lan_mode or not self.network_client:
            return
        # 断线重连检测
        if self._lan_disconnected and self.network_client.is_connected:
            self._lan_disconnected = False
            logger.info("网络已恢复连接，自动重新上报状态")
            # 重新上报状态
            try:
                total = len(self.questions)
                answered = sum(1 for q in self.questions if self._is_answered(q))
                current_qid = self.questions[self.current_question]["_qid"] if self.questions else 0
                self.network_client.send("STUDENT_STATUS", {
                    "answered": answered,
                    "total": total,
                    "current_qid": current_qid,
                    "exam_id": self.exam_id
                })
            except Exception as e:
                logger.error(f"重连后上报状态失败: {e}")
            # 尝试上传缓存的答卷
            if self._lan_pending_answers:
                self._do_lan_submit()
            return
        if not self.network_client.is_connected:
            if not self._lan_disconnected:
                self._lan_disconnected = True
                logger.warning("网络已断开，可继续答题，答案将本地缓存")
                self.root.after(0, lambda: self._show_time_warning(
                    "网络已断开，可继续答题，答案不会丢失", is_critical=False))
            return
        total = len(self.questions)
        answered = sum(1 for q in self.questions if self._is_answered(q))
        current_qid = self.questions[self.current_question]["_qid"] if self.questions else 0
        try:
            self.network_client.send("STUDENT_STATUS", {
                "answered": answered,
                "total": total,
                "current_qid": current_qid,
                "exam_id": self.exam_id
            })
        except Exception:
            pass

    def _do_lan_force_submit(self):
        """局域网强制交卷"""
        self.timer_running = False
        self._do_submit()

    # ==================== 题目配置界面（考生版） ====================
    def show_config(self):
        self._clear_window()
        self.root.deiconify()
        self.root.state('zoomed')
        sw, sh = self._screen_size()
        cx, cy = sw // 2, sh // 2

        canvas = tk.Canvas(self.root, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._create_gradient_bg(canvas, sw, sh, "#1a6fb5", "#0d4f8a")

        canvas.create_text(cx, 55, text=f"题目配置 — 考生：{self.user_name}",
                          fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 24, "bold"))

        # 配置卡片
        card = tk.Frame(canvas, bg=COLOR_WHITE, bd=0, highlightbackground="#2980b9", highlightthickness=2)
        canvas.create_window(cx, cy + 10, window=card, width=650, height=570)

        tk.Label(card, text="设置各题型数量（题库总量显示在括号内）",
                font=("微软雅黑", 12), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(pady=(10, 10))

        bank = self.question_bank
        cfg = self.question_config

        type_info = [
            ("单选题", "single_choice", len(bank.get("single_choice", []))),
            ("选择填空题", "fill_blank", len(bank.get("fill_blank", []))),
            ("编程题", "programming", len(bank.get("programming", []))),
            ("DW", "web_design", len(bank.get("web_design", []))),
            ("ENSP", "network_device", len(bank.get("network_device", []))),
            ("PS", "graphic_design", len(bank.get("graphic_design", []))),
        ]

        self.config_vars = {}
        if not hasattr(self, 'selected_qids'):
            self.selected_qids = {}
        for label, key, pool_size in type_info:
            row = tk.Frame(card, bg=COLOR_WHITE)
            row.pack(fill="x", padx=40, pady=4)

            tk.Label(row, text=f"{label}：", font=("微软雅黑", 13),
                    fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, width=10, anchor="e").pack(side="left", padx=(0, 10))

            var = tk.IntVar(value=min(cfg.get(key, 0), pool_size) if pool_size > 0 else 0)
            self.config_vars[key] = var

            is_checkbox = key in ("web_design", "graphic_design")

            if is_checkbox:
                cb_text = f"包含 1 道{label}题"
                cb = tk.Checkbutton(row, text=cb_text, variable=var,
                                   onvalue=1, offvalue=0,
                                   font=("微软雅黑", 12), bg=COLOR_WHITE,
                                   activebackground=COLOR_WHITE)
                cb.pack(side="left", padx=10)
                if var.get() == 0:
                    cb.deselect()
                else:
                    cb.select()
            else:
                def _make_range_validator(v, ps):
                    def _validate(*args):
                        try:
                            val = v.get()
                        except Exception:
                            return
                        if val > ps:
                            v.set(ps)
                        elif val < 0:
                            v.set(0)
                    return _validate
                var.trace_add("write", _make_range_validator(var, pool_size))

                entry = tk.Entry(row, textvariable=var, width=6, justify="center",
                               font=("微软雅黑", 13), bd=1, relief="solid")
                entry.pack(side="left", padx=10)

            tk.Label(row, text=f"（题库共 {pool_size:>3} 道）", font=("微软雅黑", 10),
                    fg="#999", bg=COLOR_WHITE).pack(side="left", padx=10)

            locked_count = sum(1 for q in bank.get(key, []) if isinstance(q, dict) and q.get("_locked"))
            if locked_count:
                lock_row = tk.Frame(card, bg=COLOR_WHITE)
                lock_row.pack(fill="x", padx=40)
                tk.Label(lock_row, text=f"🔒 其中 {locked_count} 题需激活码解锁", font=("微软雅黑", 9),
                        fg="#E65100", bg=COLOR_WHITE).pack(anchor="w", padx=(120, 0))

            # 精准选题按钮
            if pool_size > 0:
                sids = self.selected_qids.get(key, [])
                sel_label = f"已选{len(sids)}题" if sids else "精准选题"
                tk.Button(row, text=sel_label, font=("微软雅黑", 9),
                          bg="#4CAF50" if sids else "#FF9800", fg=COLOR_WHITE,
                          bd=0, padx=8, pady=2, cursor="hand2",
                          command=lambda k=key, l=label: self._show_question_picker(k, l)
                          ).pack(side="left", padx=(10, 0))

        # ===== 按钮区：三行分组 =====
        btn_area = tk.Frame(card, bg=COLOR_WHITE)
        btn_area.pack(pady=(15, 10))

        # 第一行：主操作
        row1 = tk.Frame(btn_area, bg=COLOR_WHITE)
        row1.pack(pady=(0, 6))
        tk.Button(row1, text="确认配置，进入考试", font=("微软雅黑", 14, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=30, pady=10,
                 cursor="hand2", command=self.confirm_config).pack()

        # 第二行：练习工具
        row2 = tk.Frame(btn_area, bg=COLOR_WHITE)
        row2.pack(pady=6)
        btns_r2 = [
            ("题库刷题", COLOR_WARNING, self.start_practice),
            ("打字练习", "#00BCD4", self.open_typing_practice),
        ]
        for text, color, cmd in btns_r2:
            tk.Button(row2, text=text, font=("微软雅黑", 12, "bold"),
                     bg=color, fg=COLOR_WHITE, bd=0, padx=24, pady=7,
                     cursor="hand2", command=cmd).pack(side="left", padx=8)

        # 第三行：返回
        row3 = tk.Frame(btn_area, bg=COLOR_WHITE)
        row3.pack(pady=(6, 0))
        tk.Button(row3, text="返回登录", font=("微软雅黑", 12),
                 bg="#e0e0e0", fg=COLOR_TEXT_DARK, bd=0, padx=24, pady=7,
                 cursor="hand2", command=self.show_login).pack()

        canvas.create_text(cx, sh - 20, text=f"考生模式 — {self.user_name} | ESC 退出全屏",
                          fill="#7fb3d8", font=("微软雅黑", 10))

        # 总分预览
        self.config_summary_var = tk.StringVar()
        self._update_config_summary()
        tk.Label(card, textvariable=self.config_summary_var, font=("微软雅黑", 11, "bold"),
                fg=COLOR_ACCENT, bg=COLOR_WHITE).pack(pady=(5, 10))

        # trace the config vars to update summary
        for var in self.config_vars.values():
            var.trace_add("write", lambda *a: self._update_config_summary())

    # ==================== 管理员面板 ====================
    def show_admin_panel(self):
        self._clear_window()
        self.root.state('zoomed')
        sw, sh = self._screen_size()
        cx, cy = sw // 2, sh // 2

        canvas = tk.Canvas(self.root, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._create_gradient_bg(canvas, sw, sh, "#1a6fb5", "#0d4f8a")

        canvas.create_text(cx, 55, text=f"管理员面板 — 欢迎，{self.user_name}",
                          fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 26, "bold"))

        # 配置卡片
        card = tk.Frame(canvas, bg=COLOR_WHITE, bd=0, highlightbackground="#2980b9", highlightthickness=2)
        canvas.create_window(cx, cy + 10, window=card, width=750, height=690)

        tk.Label(card, text="设置各题型数量（题库总量显示在括号内）",
                font=("微软雅黑", 12), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(pady=(15, 20))

        bank = self.question_bank
        cfg = self.question_config

        type_info = [
            ("单选题", "single_choice", len(bank.get("single_choice", []))),
            ("选择填空题", "fill_blank", len(bank.get("fill_blank", []))),
            ("编程题", "programming", len(bank.get("programming", []))),
            ("DW", "web_design", len(bank.get("web_design", []))),
            ("ENSP", "network_device", len(bank.get("network_device", []))),
            ("PS", "graphic_design", len(bank.get("graphic_design", []))),
        ]

        self.config_vars = {}
        if not hasattr(self, 'selected_qids'):
            self.selected_qids = {}
        self._pool_labels = {}
        self._picker_btns = {}
        for label, key, pool_size in type_info:
            row = tk.Frame(card, bg=COLOR_WHITE)
            row.pack(fill="x", padx=40, pady=4)

            tk.Label(row, text=f"{label}：", font=("微软雅黑", 13),
                    fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, width=10, anchor="e").pack(side="left", padx=(0, 10))

            var = tk.IntVar(value=min(cfg.get(key, 0), pool_size) if pool_size > 0 else 0)
            self.config_vars[key] = var

            is_checkbox = key in ("web_design", "graphic_design")

            if is_checkbox:
                cb_text = f"包含 1 道{label}题"
                cb = tk.Checkbutton(row, text=cb_text, variable=var,
                                   onvalue=1, offvalue=0,
                                   font=("微软雅黑", 12), bg=COLOR_WHITE,
                                   activebackground=COLOR_WHITE)
                cb.pack(side="left", padx=10)
                if var.get() == 0:
                    cb.deselect()
                else:
                    cb.select()
            else:
                def _make_range_validator(v, ps):
                    def _validate(*args):
                        try:
                            val = v.get()
                        except Exception:
                            return
                        if val > ps:
                            v.set(ps)
                        elif val < 0:
                            v.set(0)
                    return _validate
                var.trace_add("write", _make_range_validator(var, pool_size))

                entry = tk.Entry(row, textvariable=var, width=6, justify="center",
                               font=("微软雅黑", 13), bd=1, relief="solid")
                entry.pack(side="left", padx=10)

            pool_var = tk.StringVar(value=f"（题库共 {pool_size:>3} 道）")
            self._pool_labels[key] = pool_var
            locked_count = sum(1 for q in bank.get(key, []) if isinstance(q, dict) and q.get("_locked"))
            if locked_count:
                pool_var.set(f"（题库共 {pool_size:>3} 道，🔒{locked_count}题需解锁）")
            tk.Label(row, textvariable=pool_var, font=("微软雅黑", 11),
                    fg="#2196F3", bg=COLOR_WHITE).pack(side="left", padx=(10, 0))

            # 精准选题按钮
            if pool_size > 0:
                sids = self.selected_qids.get(key, [])
                sel_label = f"已选{len(sids)}题" if sids else "精准选题"
                picker_btn = tk.Button(row, text=sel_label, font=("微软雅黑", 9),
                          bg="#4CAF50" if sids else "#FF9800", fg=COLOR_WHITE,
                          bd=0, padx=8, pady=2, cursor="hand2",
                          command=lambda k=key, l=label: self._show_question_picker(k, l))
                picker_btn.pack(side="left", padx=10)
                self._picker_btns[key] = picker_btn

        # ===== 按钮区：三行分组 =====
        btn_area = tk.Frame(card, bg=COLOR_WHITE)
        btn_area.pack(pady=(20, 10))

        # 第一行：主操作
        row1 = tk.Frame(btn_area, bg=COLOR_WHITE)
        row1.pack(pady=(0, 6))
        tk.Button(row1, text="确认配置，进入考试", font=("微软雅黑", 14, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=30, pady=10,
                 cursor="hand2", command=self.confirm_config).pack(side="left", padx=8)

        # 第二行：练习与考试管理
        row2 = tk.Frame(btn_area, bg=COLOR_WHITE)
        row2.pack(pady=6)
        btns_r2 = [
            ("题库刷题", COLOR_WARNING, self.start_practice),
            ("打字练习", "#00BCD4", self.open_typing_practice),
            ("局域网考试管理", "#1565C0", self._start_teacher_panel),
            ("题目管理", "#4CAF50", self.show_question_manager),
        ]
        for text, color, cmd in btns_r2:
            tk.Button(row2, text=text, font=("微软雅黑", 11, "bold"),
                     bg=color, fg=COLOR_WHITE, bd=0, padx=16, pady=7,
                     cursor="hand2", command=cmd).pack(side="left", padx=6)

        # 第三行：题目维护与管理
        row3 = tk.Frame(btn_area, bg=COLOR_WHITE)
        row3.pack(pady=6)
        btns_r3 = [
            ("逐题添加（表单）", "#4CAF50", self.show_add_question_dialog),
            ("上传题目 (JSON)", COLOR_ACCENT, self.import_questions),
            ("账号管理", "#6A1B9A", self.show_account_management),
            ("批量删除题目", "#D32F2F", self.show_batch_delete_dialog),
        ]
        for text, color, cmd in btns_r3:
            tk.Button(row3, text=text, font=("微软雅黑", 11, "bold"),
                     bg=color, fg=COLOR_WHITE, bd=0, padx=16, pady=7,
                     cursor="hand2", command=cmd).pack(side="left", padx=6)

        # 第四行：返回
        row4 = tk.Frame(btn_area, bg=COLOR_WHITE)
        row4.pack(pady=(6, 0))
        tk.Button(row4, text="返回登录", font=("微软雅黑", 12),
                 bg="#e0e0e0", fg=COLOR_TEXT_DARK, bd=0, padx=20, pady=8,
                 cursor="hand2", command=self.show_login).pack()

        # 总分预览
        self.config_summary_var = tk.StringVar()
        self._update_config_summary()
        tk.Label(card, textvariable=self.config_summary_var, font=("微软雅黑", 11, "bold"),
                fg=COLOR_ACCENT, bg=COLOR_WHITE).pack(pady=(5, 10))

        # trace the config vars to update summary
        for var in self.config_vars.values():
            var.trace_add("write", lambda *a: self._update_config_summary())

        canvas.create_text(cx, sh - 20, text=f"管理员模式 — {self.user_name} | 可上传/批量删除题目 | ESC 退出全屏",
                          fill="#7fb3d8", font=("微软雅黑", 10))

    def show_extra_bank_manager(self):
        """管理员：加量题库管理——查看、添加、删除加量题"""
        dlg = tk.Toplevel(self.root)
        dlg.title("加量题库管理")
        dlg.geometry("800x580")
        dlg.resizable(True, True)
        dlg.minsize(650, 450)
        dlg.configure(bg=COLOR_BG)
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-400}+{sh//2-290}")

        tk.Label(dlg, text="加量题库管理", font=("微软雅黑", 18, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 5))

        # 按钮区
        btn_bar = tk.Frame(dlg, bg=COLOR_BG)
        btn_bar.pack(fill="x", padx=20, pady=5)
        tk.Button(btn_bar, text="+ 添加题目", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                 cursor="hand2", command=lambda: self._add_extra_question(dlg)).pack(side="left")

        # 题目列表
        list_frame = tk.Frame(dlg, bg=COLOR_WHITE, bd=1, relief="solid",
                             highlightbackground="#ddd", highlightthickness=1)
        list_frame.pack(fill="both", expand=True, padx=20, pady=10)

        canvas = tk.Canvas(list_frame, bg=COLOR_WHITE, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLOR_WHITE)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=740)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        scrollbar.pack(side="right", fill="y", pady=5)

        type_names = {"single_choice": "单选题", "fill_blank": "选择填空题", "programming": "编程题",
                      "web_design": "网页制作", "network_device": "网络设备", "graphic_design": "图形图像"}

        def refresh_list():
            for w in inner.winfo_children():
                w.destroy()
            extra = load_extra_questions()
            if not extra:
                tk.Label(inner, text='暂无加量题，点击「添加题目」开始', font=("微软雅黑", 11),
                        fg="#999", bg=COLOR_WHITE).pack(pady=30)
                return
            for i, q in enumerate(extra):
                qid = q.get("_qid") or q.get("id", f"?")
                qtype = type_names.get(q.get("type", ""), q.get("type", "?"))
                title = q.get("title", "无标题")[:40]
                frame = tk.Frame(inner, bg=COLOR_WHITE, bd=0)
                frame.pack(fill="x", pady=1)
                tk.Label(frame, text=f"#{i+1}", font=("微软雅黑", 9),
                        fg="#999", bg=COLOR_WHITE, width=4).pack(side="left")
                tk.Label(frame, text=f"[{qtype}]", font=("微软雅黑", 9, "bold"),
                        fg=COLOR_ACCENT, bg=COLOR_WHITE, width=12, anchor="w").pack(side="left")
                tk.Label(frame, text=title, font=("微软雅黑", 10),
                        fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, anchor="w").pack(side="left", fill="x", expand=True)
                tk.Button(frame, text="删除", font=("微软雅黑", 8),
                         bg=COLOR_DANGER, fg=COLOR_WHITE, bd=0, padx=8,
                         cursor="hand2",
                         command=lambda idx=i: self._delete_extra_question(idx, refresh_list)).pack(side="right", padx=5)

        refresh_list()

        tk.Button(dlg, text="关闭", font=("微软雅黑", 12),
                 bg="#e0e0e0", fg=COLOR_TEXT_DARK, bd=0, padx=30, pady=6,
                 cursor="hand2", command=dlg.destroy).pack(pady=(0, 15))
        
        # 为所有滚动区域添加鼠标滚轮支持
        self._add_mousewheel_support(dlg)

    def _add_extra_question(self, parent_dlg):
        """弹窗添加一道加量题"""
        dlg = tk.Toplevel(parent_dlg)
        dlg.title("添加加量题")
        dlg.geometry("650x550")
        dlg.resizable(True, True)
        dlg.minsize(500, 380)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(parent_dlg)
        dlg.grab_set()

        tk.Label(dlg, text="添加加量题", font=("微软雅黑", 16, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 10))

        form = tk.Frame(dlg, bg=COLOR_BG)
        form.pack(fill="both", expand=True, padx=20)

        # 题型选择
        tk.Label(form, text="题型：", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        type_var = tk.StringVar(value="single_choice")
        type_options = ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]
        type_names = {"single_choice": "单选题", "fill_blank": "选择填空题", "programming": "编程题",
                      "web_design": "网页制作操作题", "network_device": "网络设备安装与调试", "graphic_design": "图形图像处理操作题"}
        type_menu = tk.OptionMenu(form, type_var, *type_options,
                                  command=lambda v: type_label_var.set(type_names.get(v, v)))
        type_menu.configure(font=("微软雅黑", 11))
        type_menu.pack(anchor="w", pady=(0, 10))

        type_label_var = tk.StringVar(value="单选题")
        tk.Label(form, textvariable=type_label_var, font=("微软雅黑", 9),
                fg="#888", bg=COLOR_BG).pack(anchor="w", pady=(0, 10))

        # 题目标题
        tk.Label(form, text="题目标题：", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        title_text = tk.Text(form, font=("微软雅黑", 11), height=3, wrap="word")
        title_text.pack(fill="x", pady=(0, 10))

        # 选项/答案
        option_frame = tk.Frame(form, bg=COLOR_BG)
        option_frame.pack(fill="x", pady=(0, 10))
        tk.Label(option_frame, text="选项（每行一个，如 A. xxx）：", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        option_text = tk.Text(option_frame, font=("微软雅黑", 11), height=4, wrap="word")
        option_text.pack(fill="x")

        tk.Label(form, text="正确答案：", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        answer_var = tk.StringVar()
        tk.Entry(form, textvariable=answer_var, font=("微软雅黑", 11),
                width=20).pack(anchor="w", pady=(0, 10))

        tk.Label(form, text="分值：", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        score_var = tk.IntVar(value=5)
        tk.Spinbox(form, from_=1, to=50, textvariable=score_var,
                   font=("微软雅黑", 11), width=5).pack(anchor="w", pady=(0, 15))

        def save():
            title = title_text.get("1.0", "end-1c").strip()
            if not title:
                messagebox.showwarning("提示", "请输入题目标题")
                return
            options_raw = option_text.get("1.0", "end-1c").strip()
            options = [o.strip() for o in options_raw.split("\n") if o.strip()] if options_raw else []
            answer = answer_var.get().strip()
            qtype = type_var.get()

            extra = load_extra_questions()
            new_q = {
                "_qid": f"extra_{len(extra)+1:03d}",
                "type": qtype,
                "title": title,
                "answer": answer,
                "score": score_var.get(),
                "difficulty": "中"
            }
            if options:
                new_q["options"] = options
            extra.append(new_q)
            with open(EXTRA_BANK_FILE, "w", encoding="utf-8") as f:
                json.dump(extra, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("成功", "题目已添加")
            dlg.destroy()
            parent_dlg.destroy()
            self.show_extra_bank_manager()

        tk.Button(form, text="保存", font=("微软雅黑", 13, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=30, pady=8,
                 cursor="hand2", command=save).pack(pady=(0, 10))

    def _delete_extra_question(self, index, refresh_callback):
        """删除指定加量题"""
        if not messagebox.askyesno("确认", "确定删除这道加量题？\n\n删除后题目及其关联素材文件将一并清理。"):
            return
        extra = load_extra_questions()
        if 0 <= index < len(extra):
            q = extra[index]
            _cleanup_question_files(q)  # 先清理关联素材文件
            extra.pop(index)
            with open(EXTRA_BANK_FILE, "w", encoding="utf-8") as f:
                json.dump(extra, f, ensure_ascii=False, indent=2)
        refresh_callback()

    def show_activation_manager(self):
        """管理员：激活码管理面板（固定码 sdck_wlydt）"""
        FIXED_CODE = "SDCK_WLYDT"
        dlg = tk.Toplevel(self.root)
        dlg.title("激活码管理")
        dlg.geometry("650x400")
        dlg.resizable(True, True)
        dlg.minsize(550, 320)
        dlg.configure(bg=COLOR_BG)
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-300}+{sh//2-175}")

        tk.Label(dlg, text="激活码管理", font=("微软雅黑", 18, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 5))

        # 固定码显示
        code_frame = tk.Frame(dlg, bg=COLOR_WHITE, bd=1, relief="solid",
                             highlightbackground="#ddd", highlightthickness=1)
        code_frame.pack(fill="x", padx=20, pady=10)

        tk.Label(code_frame, text="固定激活码（学生输入此码解锁）", font=("微软雅黑", 11, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(pady=(10, 5))
        tk.Label(code_frame, text=FIXED_CODE, font=("Consolas", 24, "bold"),
                fg="#E65100", bg=COLOR_WHITE).pack(pady=5)
        tk.Label(code_frame, text="此码可重复使用，不限次数", font=("微软雅黑", 9),
                fg="#888", bg=COLOR_WHITE).pack(pady=(0, 10))

        # 绑定题目
        codes = load_activation_codes()
        code_entry = None
        for c in codes:
            if c.get("code") == FIXED_CODE:
                code_entry = c
                break
        if not code_entry:
            code_entry = {"code": FIXED_CODE, "question_ids": [], "used_by": "", "used_at": ""}
            codes.append(code_entry)
            save_activation_codes(codes)

        q_count = len(code_entry.get("question_ids", []))
        tk.Label(dlg, text=f"已绑定 {q_count} 道锁定题", font=("微软雅黑", 12),
                fg="#555", bg=COLOR_BG).pack(pady=5)

        btn_f = tk.Frame(dlg, bg=COLOR_BG)
        btn_f.pack(pady=10)
        tk.Button(btn_f, text="选择要解锁的题目", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2",
                 command=lambda: self._add_questions_to_code(code_entry, dlg)).pack(side="left", padx=10)
        tk.Button(btn_f, text="关闭", font=("微软雅黑", 12),
                 bg="#e0e0e0", fg=COLOR_TEXT_DARK, bd=0, padx=30, pady=8,
                 cursor="hand2", command=dlg.destroy).pack(side="left", padx=10)

    def _add_questions_to_code(self, code_entry, parent_dlg):
        """为激活码绑定锁定题（从主题库读取 locked=True 的题目）"""
        bank = load_question_bank()
        locked_qs = []
        for qtype, pool in bank.items():
            if isinstance(pool, list):
                for q in pool:
                    if isinstance(q, dict) and q.get("locked"):
                        locked_qs.append(q)

        if not locked_qs:
            messagebox.showwarning("提示", "主题库中没有锁定题。请先在题目管理中将题目标记为锁定。")
            return

        dlg = tk.Toplevel(parent_dlg)
        dlg.title(f"绑定锁定题 — {code_entry['code']}")
        dlg.geometry("750x550")
        dlg.resizable(True, True)
        dlg.minsize(600, 400)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(parent_dlg)
        dlg.grab_set()

        tk.Label(dlg, text=f"为 {code_entry['code']} 选择要解锁的题目", font=("微软雅黑", 13, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 10))

        list_frame = tk.Frame(dlg, bg=COLOR_WHITE)
        list_frame.pack(fill="both", expand=True, padx=15, pady=5)

        canvas = tk.Canvas(list_frame, bg=COLOR_WHITE, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLOR_WHITE)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=640)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        scrollbar.pack(side="right", fill="y", pady=5)

        current_ids = set(code_entry.get("question_ids", []))
        check_vars = {}

        type_names = {"single_choice": "单选题", "fill_blank": "选择填空题", "programming": "编程题",
                      "web_design": "网页制作", "network_device": "网络设备", "graphic_design": "图形图像"}

        for q in locked_qs:
            qid = q.get("_qid") or q.get("id")
            qtype = type_names.get(q.get("type", ""), q.get("type", ""))
            title = q.get("title", "")[:35]
            var = tk.BooleanVar(value=qid in current_ids)
            check_vars[qid] = var
            cb = tk.Checkbutton(inner, text=f"[{qtype}] {title}", variable=var,
                               font=("微软雅黑", 10), bg=COLOR_WHITE, anchor="w")
            cb.pack(fill="x", padx=10, pady=1)

        def save_binding():
            code_entry["question_ids"] = [qid for qid, var in check_vars.items() if var.get()]
            codes = load_activation_codes()
            for c in codes:
                if c["code"] == code_entry["code"]:
                    c["question_ids"] = code_entry["question_ids"]
                    break
            save_activation_codes(codes)
            dlg.destroy()
            parent_dlg.destroy()
            self.show_activation_manager()

        tk.Button(dlg, text="保存", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=30, pady=6,
                 cursor="hand2", command=save_binding).pack(pady=(10, 15))

    def _update_config_summary(self):
        total = sum(v.get() for v in self.config_vars.values())
        scores = APP_CONFIG.get("score_mapping", {
            "single_choice": 2, "fill_blank": 10, "programming": 20,
            "web_design": 30, "network_device": 15, "graphic_design": 30
        })
        total_score = sum(v.get() * scores.get(k, 0) for k, v in self.config_vars.items())
        self.config_summary_var.set(f"共 {total} 题 | 预估总分约 {total_score} 分")

    def _refresh_admin_pool_display(self):
        """刷新管理员面板的题库数量显示，不重建整个面板"""
        # 重新加载题库
        self.question_bank = load_question_bank()
        bank = self.question_bank

        type_keys = ["single_choice", "fill_blank", "programming",
                     "web_design", "network_device", "graphic_design"]

        for key in type_keys:
            pool = bank.get(key, [])
            pool_size = len(pool)

            # 更新题库数量标签
            if key in self._pool_labels:
                locked = sum(1 for q in pool if isinstance(q, dict) and q.get("_locked"))
                tip = f"，🔒{locked}题需解锁" if locked else ""
                self._pool_labels[key].set(f"（题库共 {pool_size:>3} 道{tip}）")

            # 更新精准选题按钮
            if key in self._picker_btns:
                btn = self._picker_btns[key]
                if pool_size > 0:
                    sids = self.selected_qids.get(key, [])
                    sel_label = f"已选{len(sids)}题" if sids else "精准选题"
                    btn.config(text=sel_label,
                               bg="#4CAF50" if sids else "#FF9800")
                else:
                    btn.config(text="题库为空", bg="#999999")

            # 约束 config_vars 值不超过新题库量
            if key in self.config_vars:
                var = self.config_vars[key]
                if var.get() > pool_size:
                    var.set(pool_size)
                if pool_size == 0:
                    var.set(0)

        self._update_config_summary()

    def show_batch_delete_dialog(self):
        """批量删除题目对话框：按题型分组展示，可多选删除"""
        dialog = tk.Toplevel(self.root)
        dialog.title("批量删除题目")
        dialog.geometry("900x700")
        dialog.resizable(True, True)
        dialog.minsize(650, 450)
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self.root)
        dialog.grab_set()

        # 标题
        tk.Label(dialog, text="批量删除题目", font=("微软雅黑", 16, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 5))
        tk.Label(dialog, text="勾选要删除的题目，点击底部按钮执行删除",
                font=("微软雅黑", 10), fg="#666666", bg=COLOR_BG).pack(pady=(0, 10))

        # 可滚动画布
        canvas = tk.Canvas(dialog, bg=COLOR_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        content_frame = tk.Frame(canvas, bg=COLOR_BG)

        content_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=content_frame, anchor="nw", width=810)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True, padx=(15, 0), pady=5)
        scrollbar.pack(side="right", fill="y", padx=(0, 5), pady=5)

        # 绑定鼠标滚轮（Enter/Leave 模式）
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        def _on_canvas_enter(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
        def _on_canvas_leave(event):
            canvas.unbind_all("<MouseWheel>")
        canvas.bind("<Enter>", _on_canvas_enter)
        canvas.bind("<Leave>", _on_canvas_leave)

        type_order = ["single_choice", "fill_blank", "programming",
                      "web_design", "network_device", "graphic_design"]
        group_titles = {
            "single_choice": "单选题",
            "fill_blank": "选择填空题",
            "programming": "编程题",
            "web_design": "网页制作操作题",
            "network_device": "网络设备安装与调试",
            "graphic_design": "图形图像处理操作题"
        }

        # 每个题型的勾选变量列表
        self._batch_del_vars = {}  # qtype -> [tk.BooleanVar, ...]
        self._batch_del_questions = {}  # qtype -> [q, ...]

        for qtype in type_order:
            pool = self.question_bank.get(qtype, [])
            if not pool:
                continue
            title = group_titles.get(qtype, qtype)

            # 分组标题栏
            group_header = tk.Frame(content_frame, bg="#e3f2fd")
            group_header.pack(fill="x", pady=(10, 3))

            tk.Label(group_header, text=f"{title}（共 {len(pool)} 道）",
                    font=("微软雅黑", 11, "bold"), fg=COLOR_TEXT_DARK,
                    bg="#e3f2fd").pack(side="left", padx=10, pady=5)

            # 全选复选框 + 题目变量列表
            group_vars = []
            self._batch_del_vars[qtype] = group_vars
            self._batch_del_questions[qtype] = pool

            master_var = tk.BooleanVar(value=False)
            def make_master_toggle(gv, mv):
                def _toggle():
                    val = mv.get()
                    for v in gv:
                        v.set(val)
                return _toggle
            def make_sync_master(gv, mv):
                """子项变化时同步主复选框状态"""
                def _sync(*args):
                    if not gv:
                        mv.set(False)
                        return
                    all_checked = all(v.get() for v in gv)
                    mv.set(all_checked)
                return _sync

            master_cb = ttk.Checkbutton(group_header, variable=master_var,
                                        command=make_master_toggle(group_vars, master_var))
            master_cb.pack(side="right", padx=(0, 10), pady=5)
            tk.Label(group_header, text="全选此题型",
                    font=("微软雅黑", 9), fg="#1976D2",
                    bg="#e3f2fd").pack(side="right", pady=5)

            # 题目列表
            sync_fn = make_sync_master(group_vars, master_var)
            for idx, q in enumerate(pool):
                row = tk.Frame(content_frame, bg=COLOR_WHITE)
                row.pack(fill="x", padx=5, pady=1)

                var = tk.BooleanVar(value=False)
                var.trace_add("write", sync_fn)
                group_vars.append(var)

                cb = ttk.Checkbutton(row, variable=var)
                cb.pack(side="left", padx=(5, 10))

                q_title = q.get("title", f"第{idx+1}题")
                if len(q_title) > 50:
                    q_title = q_title[:50] + "..."
                tk.Label(row, text=f"[{idx+1}] {q_title}",
                        font=("微软雅黑", 9), fg=COLOR_TEXT_DARK,
                        bg=COLOR_WHITE, anchor="w").pack(side="left")

        # 底部操作栏
        bottom_frame = tk.Frame(dialog, bg=COLOR_BG)
        bottom_frame.pack(fill="x", padx=20, pady=(10, 15))

        def select_all():
            for gv in self._batch_del_vars.values():
                for v in gv:
                    v.set(True)

        def delete_selected():
            # 统计选中的题目
            to_delete = []  # [(qtype, index_in_pool), ...]
            for qtype in type_order:
                vars_list = self._batch_del_vars.get(qtype, [])
                pool = self._batch_del_questions.get(qtype, [])
                for i, var in enumerate(vars_list):
                    if var.get():
                        to_delete.append((qtype, i))

            if not to_delete:
                messagebox.showinfo("提示", "请至少勾选一道题目")
                return

            count = len(to_delete)
            confirm = messagebox.askyesno(
                "确认删除",
                f"即将删除 {count} 道题目，删除后不可恢复。\n\n确认删除？"
            )
            if not confirm:
                return

            # 按 qtype 分组，倒序删除（避免索引错位）
            from collections import defaultdict
            grouped = defaultdict(list)
            for qtype, idx in to_delete:
                grouped[qtype].append(idx)

            for qtype, indices in grouped.items():
                # 从大到小排序索引，倒序删除
                pool = self.question_bank.get(qtype, [])
                for idx in sorted(indices, reverse=True):
                    if 0 <= idx < len(pool):
                        _cleanup_question_files(pool[idx])  # 先清理关联文件
                        pool.pop(idx)

            save_question_bank(self.question_bank)
            dialog.destroy()
            messagebox.showinfo("删除完成", f"已删除 {count} 道题目")

            # 刷新管理员面板题目数量显示
            self._refresh_admin_pool_display()

        tk.Button(bottom_frame, text="全选", font=("微软雅黑", 11, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=25, pady=8,
                 cursor="hand2", command=select_all).pack(side="left", padx=10)

        tk.Button(bottom_frame, text="删除选中", font=("微软雅黑", 11, "bold"),
                 bg="#D32F2F", fg=COLOR_WHITE, bd=0, padx=25, pady=8,
                 cursor="hand2", command=delete_selected).pack(side="left", padx=10)
        
        # 为所有滚动区域添加鼠标滚轮支持
        self._add_mousewheel_support(dialog)

    def confirm_config(self):
        try:
            total = sum(v.get() for v in self.config_vars.values())
            if total == 0:
                messagebox.showwarning("提示", "请至少选择一道题目！")
                return
            for key, var in self.config_vars.items():
                self.question_config[key] = var.get()
            save_config(self.question_config)
            self._merge_unlocked_questions()
            self.questions = self._build_questions()
            # 重置作答状态，避免上次考试答案影响
            self.answers = {}
            self.submitted = set()
            self.marked = set()
            self.current_question = 0
            self.show_notice()
        except Exception as e:
            import traceback
            messagebox.showerror("配置错误",
                f"确认配置时发生异常：\n{str(e)}\n\n详细信息：\n{traceback.format_exc()}")

    def _show_question_picker(self, qtype, qtype_label):
        """打开指定题型的精准选题对话框。"""
        pool = self.question_bank.get(qtype, [])
        if not pool:
            messagebox.showinfo("提示", f"当前题库中无{qtype_label}")
            return

        current_sids = self.selected_qids.get(qtype, [])
        # Map: question id -> index in pool
        id_to_idx = {}
        for i, q in enumerate(pool):
            qid = q.get("id", i + 1000)
            id_to_idx[qid] = i

        dialog = tk.Toplevel(self.root)
        dialog.title(f"精准选题 - {qtype_label}")
        dialog.geometry("800x600")
        dialog.resizable(True, True)
        dialog.minsize(600, 400)
        dialog.configure(bg=COLOR_WHITE)
        dialog.transient(self.root)
        dialog.grab_set()

        # Title
        header = tk.Frame(dialog, bg=COLOR_BG)
        header.pack(fill="x")
        tk.Label(header, text=f"{qtype_label} — 题库共 {len(pool)} 道，勾选需要出题的题目",
                font=("微软雅黑", 13, "bold"), fg=COLOR_WHITE, bg=COLOR_BG,
                padx=15, pady=10).pack(anchor="w")

        # Scrollable checkbox area
        canvas_frame = tk.Frame(dialog, bg=COLOR_WHITE)
        canvas_frame.pack(fill="both", expand=True, padx=10, pady=5)

        canvas = tk.Canvas(canvas_frame, bg=COLOR_WHITE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=COLOR_WHITE)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw", width=700)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ExamSystem._setup_auto_scroll(canvas, scrollbar, side="right", fill="y")

        # Checkboxes
        check_vars = {}
        for i, q in enumerate(pool):
            qid = q.get("id", i + 1000)
            content = q.get("content", "")
            # Show first 60 chars as preview
            preview = content.replace("\n", " ")[:60] + ("..." if len(content) > 60 else "")
            diff = q.get("difficulty", "中")

            row = tk.Frame(scroll_frame, bg=COLOR_WHITE)
            row.pack(fill="x", pady=2)

            var = tk.BooleanVar(value=(qid in current_sids))
            check_vars[qid] = var
            cb = tk.Checkbutton(row, variable=var, bg=COLOR_WHITE,
                               font=("微软雅黑", 10))
            cb.pack(side="left")

            info = f"#{qid} [{diff}] {preview}"
            tk.Label(row, text=info, font=("微软雅黑", 10),
                    fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, anchor="w",
                    wraplength=620, justify="left").pack(side="left", padx=(5, 0))

        # Bottom buttons
        btn_frame = tk.Frame(dialog, bg="#f5f5f5")
        btn_frame.pack(fill="x", pady=(10, 0))

        select_all_btn = tk.Button(btn_frame, text="全选", font=("微软雅黑", 10),
                                   bg="#78909C", fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                                   cursor="hand2",
                                   command=lambda: [v.set(True) for v in check_vars.values()])
        select_all_btn.pack(side="left", padx=(15, 5))

        deselect_all_btn = tk.Button(btn_frame, text="取消全选", font=("微软雅黑", 10),
                                     bg="#B0BEC5", fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                                     cursor="hand2",
                                     command=lambda: [v.set(False) for v in check_vars.values()])
        deselect_all_btn.pack(side="left", padx=5)
        
        clear_selection_btn = tk.Button(btn_frame, text="清空选择", font=("微软雅黑", 10),
                                        bg="#FF7043", fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                                        cursor="hand2",
                                        command=lambda: [v.set(False) for v in check_vars.values()])
        clear_selection_btn.pack(side="left", padx=5)

        def do_confirm():
            sids = sorted([qid for qid, var in check_vars.items() if var.get()])
            current_config_count = self.question_config.get(qtype, 0)
            
            # 如果选中的题目数量与当前配置数量不一致，询问用户
            if sids and len(sids) != current_config_count:
                response = messagebox.askyesno("数量不一致",
                    f"当前配置的{qtype_label}数量为 {current_config_count} 题，\n"
                    f"但您选中了 {len(sids)} 题。\n\n"
                    f"是否将配置数量更新为 {len(sids)} 题？")
                if response:
                    # 更新配置数量
                    self.question_config[qtype] = len(sids)
                    if qtype in self.config_vars:
                        self.config_vars[qtype].set(len(sids))
                else:
                    # 用户选择不更新，只保存选中的题目ID
                    pass
            
            self.selected_qids[qtype] = sids
            
            # 如果配置数量为0但选中了题目，自动更新配置数量
            if sids and current_config_count == 0:
                self.question_config[qtype] = len(sids)
                if qtype in self.config_vars:
                    self.config_vars[qtype].set(len(sids))
            
            # Refresh the panel to update button labels
            if self.is_admin:
                self.show_admin_panel()
            else:
                self.show_config()
            dialog.destroy()

        tk.Button(btn_frame, text="确认", font=("微软雅黑", 11, "bold"),
                  bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=25, pady=6,
                  cursor="hand2", command=do_confirm).pack(side="right", padx=(0, 15))

        tk.Button(btn_frame, text="取消", font=("微软雅黑", 11),
                  bg="#e0e0e0", fg=COLOR_TEXT_DARK, bd=0, padx=25, pady=6,
                  cursor="hand2", command=dialog.destroy).pack(side="right", padx=5)

    def import_questions(self):
        filepath = filedialog.askopenfilename(
            title="选择题目文件", filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")])
        if not filepath:
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            added = 0
            skipped = 0
            for qtype in ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]:
                if qtype in data and isinstance(data[qtype], list):
                    # 构建现有题库该题型的 content 集合，用于去重
                    existing_contents = {q.get("content", "").strip() for q in self.question_bank.get(qtype, [])}
                    for q in data[qtype]:
                        # 去重：检查 content 是否已存在
                        new_content = q.get("content", "").strip()
                        if new_content and new_content in existing_contents:
                            skipped += 1
                            continue
                        q["id"] = len(self.question_bank.get(qtype, [])) + 1000 + added

                        # 兼容：answer_code -> reference_answer
                        if "answer_code" in q and "reference_answer" not in q:
                            q["reference_answer"] = q.pop("answer_code")

                        # 确保 reference_answer 字段存在且格式正确
                        if "reference_answer" not in q:
                            q["reference_answer"] = "" if qtype == "programming" else {}
                        elif qtype == "network_device":
                            if isinstance(q["reference_answer"], str):
                                text_fields = q.get("text_fields", [])
                                ref_dict = {}
                                for tf in text_fields:
                                    ref_dict[tf.get("label", "")] = ""
                                q["reference_answer"] = ref_dict
                            elif not isinstance(q["reference_answer"], dict):
                                text_fields = q.get("text_fields", [])
                                ref_dict = {}
                                for tf in text_fields:
                                    ref_dict[tf.get("label", "")] = ""
                                q["reference_answer"] = ref_dict

                        # 处理编程题测试用例：从 JSON 导入并保存为文件
                        if qtype == "programming" and "test_cases" in q:
                            tc_dir = os.path.join(IMAGE_DIR, "test_cases", f"prog_{q['id']}")
                            os.makedirs(tc_dir, exist_ok=True)
                            for i, tc in enumerate(q["test_cases"], 1):
                                in_text = tc.get("in", tc.get("input", ""))
                                out_text = tc.get("out", tc.get("expected", ""))
                                with open(os.path.join(tc_dir, f"{i}.in"), "w", encoding="utf-8") as f:
                                    f.write(in_text)
                                with open(os.path.join(tc_dir, f"{i}.out"), "w", encoding="utf-8") as f:
                                    f.write(out_text)
                            del q["test_cases"]

                        # DW/PS题：如未指定图片，自动从文件夹中读取"样图"
                        if qtype in ("web_design", "graphic_design"):
                            if q.get("folder_path"):
                                _copy_material_folder_into_system(q, qtype, os.path.dirname(filepath))
                            _copy_reference_folder_into_system(q, qtype, os.path.dirname(filepath))
                        elif qtype == "network_device":
                            _copy_network_device_files_into_system(q, os.path.dirname(filepath))

                        if qtype in ("web_design", "graphic_design") and not q.get("image") and q.get("folder_path"):
                            folder = _resolve_path(q["folder_path"])
                            for test_ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif",
                                             ".PNG", ".JPG", ".JPEG", ".BMP", ".GIF"):
                                sample_path = os.path.join(folder, f"样图{test_ext}")
                                if os.path.exists(sample_path):
                                    os.makedirs(IMAGE_DIR, exist_ok=True)
                                    ext = os.path.splitext(sample_path)[1]
                                    img_name = f"{qtype}_{q['id']}_{len(self.question_bank.get(qtype,[]))+1}{ext}"
                                    dest = os.path.join(IMAGE_DIR, img_name)
                                    shutil.copy2(sample_path, dest)
                                    q["image"] = os.path.join("question_images", img_name)
                                    break

                        self.question_bank.setdefault(qtype, []).append(q)
                        existing_contents.add(new_content)
                        added += 1

            save_question_bank(self.question_bank)
            msg = f"成功导入 {added} 道题目！"
            if skipped > 0:
                msg += f"\n跳过 {skipped} 道重复题目"
            msg += (
                f"\n\n单选题: {len(self.question_bank.get('single_choice',[]))} 道\n"
                f"填空题: {len(self.question_bank.get('fill_blank',[]))} 道\n"
                f"编程题: {len(self.question_bank.get('programming',[]))} 道\n"
                f"网页题: {len(self.question_bank.get('web_design',[]))} 道\n"
                f"网络设备题: {len(self.question_bank.get('network_device',[]))} 道\n"
                f"图形图像题: {len(self.question_bank.get('graphic_design',[]))} 道"
            )
            messagebox.showinfo("导入成功", msg)
            self.show_admin_panel()
        except Exception as e:
            messagebox.showerror("导入失败", f"文件解析错误：{str(e)}")

    def export_template(self):
        filepath = filedialog.asksaveasfilename(
            title="导出题目模板", defaultextension=".json",
            filetypes=[("JSON文件", "*.json")], initialfile="题目模板.json")
        if not filepath:
            return
        template = {
            "single_choice": [
                {
                    "content": "1. 题干内容",
                    "options": ["A. 选项A", "B. 选项B", "C. 选项C", "D. 选项D"],
                    "answer": 0,
                    "score": 2,
                    "difficulty": "中"
                }
            ],
            "fill_blank": [
                {
                    "content": "1. 题干，用【1】【2】标记空位\n\n#include <stdio.h>\n...",
                    "code": "可选的参考代码",
                    "shared_options": ["0", "j<3", "a[i][j]>max", "j", "i<4", "max=a[i][j]", "i", "j<4", "a[i][0]", "0"],
                    "blanks": [
                        {"label": "【1】", "answer": 2},
                        {"label": "【2】", "answer": 5}
                    ],
                    "score": 10,
                    "difficulty": "中"
                }
            ],
            "programming": [
                {
                    "content": "【程序功能】\n题目描述...",
                    "reference_answer": "参考代码（可选，字符串）",
                    "test_cases": [
                        {"in": "78", "out": "1:18"},
                        {"in": "120", "out": "2:0"}
                    ],
                    "score": 20,
                    "difficulty": "中"
                }
            ],
            "web_design": [
                {
                    "content": "【网页制作要求】\n题目描述...",
                    "score": 30,
                    "difficulty": "中"
                }
            ],
            "network_device": [
                {
                    "content": "【网络拓扑】\n某公司网络拓扑如下...\n\n【调试要求】\n1. 配置VLAN...\n2. 配置OSPF...",
                    "text_fields": [
                        {"label": "(1) 执行 display vlan 命令后的输出结果"},
                        {"label": "(2) 执行 display ip routing-table 命令后的输出结果"}
                    ],
                    "score": 15,
                    "difficulty": "中"
                }
            ],
            "graphic_design": [
                {
                    "content": "【图形图像处理要求】\n使用Photoshop完成以下设计...",
                    "score": 30,
                    "difficulty": "中"
                }
            ]
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
        messagebox.showinfo("导出成功", f"模板已保存至：\n{filepath}")

    # ==================== 题目配置管理 ====================
    TEMPLATES_FILE = os.path.join(EXAM_DIR, "exam_templates.json")

    @staticmethod
    def _load_templates():
        """加载题目配置文件。"""
        try:
            with open(ExamSystem.TEMPLATES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("templates", [])
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    @staticmethod
    def _save_templates(templates):
        """保存题目配置文件。"""
        with open(ExamSystem.TEMPLATES_FILE, "w", encoding="utf-8") as f:
            json.dump({"templates": templates}, f, ensure_ascii=False, indent=2)

    def _save_exam_template(self):
        """保存当前题目配置。"""
        # 弹出输入框获取配置名称
        dialog = tk.Toplevel(self.root)
        dialog.title("保存题目配置")
        dialog.geometry("400x180")
        dialog.resizable(True, True)
        dialog.minsize(340, 150)
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.update_idletasks()
        dw = dialog.winfo_width()
        dh = dialog.winfo_height()
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        dialog.geometry(f"+{(sw - dw) // 2}+{(sh - dh) // 2}")

        frm = tk.Frame(dialog, bg=COLOR_BG, padx=20, pady=20)
        frm.pack(fill=tk.BOTH, expand=True)
        tk.Label(frm, text="配置名称：", font=("微软雅黑", 11), bg=COLOR_BG).pack(anchor="w")
        name_var = tk.StringVar()
        entry = tk.Entry(frm, textvariable=name_var, font=("微软雅黑", 11), width=30)
        entry.pack(fill="x", pady=(5, 10))
        entry.focus_set()

        def do_save():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("提示", "请输入配置名称")
                return
            templates = ExamSystem._load_templates()
            q_counts = {k: v.get() for k, v in self.config_vars.items()}
            try:
                duration_minutes = int(self.duration_var.get())
            except (ValueError, AttributeError):
                duration_minutes = 60
            template = {
                "name": name,
                "questions_count": q_counts,
                "duration_minutes": duration_minutes,
                "selected_qids": getattr(self, 'selected_qids', {}),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            # 覆盖同名配置
            templates = [t for t in templates if t.get("name") != name]
            templates.append(template)
            ExamSystem._save_templates(templates)
            messagebox.showinfo("成功", f"题目配置「{name}」已保存")
            dialog.destroy()

        tk.Button(frm, text="保存", font=("微软雅黑", 11),
                  bg=COLOR_SUCCESS, fg=COLOR_WHITE, width=10,
                  command=do_save).pack(side="right", padx=(5, 0))
        tk.Button(frm, text="取消", font=("微软雅黑", 11),
                  bg="#757575", fg=COLOR_WHITE, width=10,
                  command=dialog.destroy).pack(side="right")

        dialog.bind("<Return>", lambda e: do_save())

    def _load_exam_template(self):
        """加载题目配置。"""
        templates = ExamSystem._load_templates()
        if not templates:
            messagebox.showinfo("提示", "暂无已保存的题目配置")
            return

        names = [t.get("name", "未命名") for t in templates]
        dialog = tk.Toplevel(self.root)
        dialog.title("加载题目配置")
        dialog.geometry("700x500")
        dialog.resizable(True, True)
        dialog.minsize(550, 380)
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.update_idletasks()
        dw = dialog.winfo_width()
        dh = dialog.winfo_height()
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        dialog.geometry(f"+{(sw - dw) // 2}+{(sh - dh) // 2}")

        frm = tk.Frame(dialog, bg=COLOR_BG, padx=15, pady=15)
        frm.pack(fill=tk.BOTH, expand=True)
        tk.Label(frm, text="选择要加载的题目配置：", font=("微软雅黑", 12, "bold"), bg=COLOR_BG).pack(anchor="w", pady=(0, 10))

        listbox = tk.Listbox(frm, font=("微软雅黑", 11), selectmode=tk.SINGLE)
        listbox.pack(fill=tk.BOTH, expand=True)
        type_names_cn = {"single_choice": "单选题", "fill_blank": "填空题", "programming": "编程题",
                         "web_design": "网页设计", "network_device": "网络设备", "graphic_design": "图形图像"}
        for i, name in enumerate(names):
            listbox.insert(tk.END, f"{i + 1}. {name}")
            t = templates[i]
            qc = t.get('questions_count', {})
            parts = [f"{type_names_cn.get(k, k)}:{v}" for k, v in qc.items()]
            desc = f"    {'  '.join(parts)} | {t.get('duration_minutes', '?')}分钟 | {t.get('created_at', '')}"
            listbox.insert(tk.END, desc)

        def do_load():
            sel = listbox.curselection()
            if not sel:
                messagebox.showwarning("提示", "请选择题目配置")
                return
            # 找到实际的配置索引
            idx = sel[0] // 2  # 每个配置占2行
            if idx >= len(templates):
                return
            template = templates[idx]
            q_counts = template.get("questions_count", {})
            selected_qids = template.get("selected_qids", {})
            duration = template.get("duration_minutes", 60)
            # 恢复精准选题
            self.selected_qids = selected_qids
            # 设置各题型数量（优先使用 selected_qids，否则用 questions_count）
            for key, var in self.config_vars.items():
                sids = selected_qids.get(key, [])
                if sids:
                    self.question_config[key] = len(sids)
                    var.set(len(sids))
                else:
                    count = q_counts.get(key, 0)
                    pool = len(self.question_bank.get(key, []))
                    self.question_config[key] = min(count, pool)
                    var.set(min(count, pool))
            # 设置考试时长
            if hasattr(self, 'duration_var'):
                self.duration_var.set(str(duration))
            # 刷新面板以更新按钮标签
            if self.is_admin:
                self.show_admin_panel()
            else:
                self.show_config()
            messagebox.showinfo("加载成功", f"已加载题目配置「{template.get('name', '')}」")
            dialog.destroy()

        btn_frame = tk.Frame(frm, bg=COLOR_BG)
        btn_frame.pack(fill="x", pady=(10, 0))
        tk.Button(btn_frame, text="加载", font=("微软雅黑", 11),
                  bg=COLOR_ACCENT, fg=COLOR_WHITE, width=10,
                  command=do_load).pack(side="right", padx=(5, 0))
        tk.Button(btn_frame, text="取消", font=("微软雅黑", 11),
                  bg="#757575", fg=COLOR_WHITE, width=10,
                  command=dialog.destroy).pack(side="right")

    def _delete_exam_template(self):
        """删除题目配置。"""
        templates = ExamSystem._load_templates()
        if not templates:
            messagebox.showinfo("提示", "暂无已保存的题目配置")
            return

        names = [t.get("name", "未命名") for t in templates]
        dialog = tk.Toplevel(self.root)
        dialog.title("删除题目配置")
        dialog.geometry("540x450")
        dialog.resizable(True, True)
        dialog.minsize(420, 340)
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.update_idletasks()
        dw = dialog.winfo_width()
        dh = dialog.winfo_height()
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        dialog.geometry(f"+{(sw - dw) // 2}+{(sh - dh) // 2}")

        frm = tk.Frame(dialog, bg=COLOR_BG, padx=15, pady=15)
        frm.pack(fill=tk.BOTH, expand=True)
        tk.Label(frm, text="选择要删除的题目配置：", font=("微软雅黑", 12, "bold"), bg=COLOR_BG).pack(anchor="w", pady=(0, 10))

        listbox = tk.Listbox(frm, font=("微软雅黑", 11), selectmode=tk.MULTIPLE)
        listbox.pack(fill=tk.BOTH, expand=True)
        for name in names:
            listbox.insert(tk.END, name)

        def do_delete():
            sels = listbox.curselection()
            if not sels:
                messagebox.showwarning("提示", "请选择要删除的题目配置")
                return
            indices = sorted([int(s) for s in sels], reverse=True)
            if not messagebox.askyesno("确认", f"确定删除选中的 {len(indices)} 个题目配置吗？"):
                return
            new_templates = [t for i, t in enumerate(templates) if i not in indices]
            ExamSystem._save_templates(new_templates)
            messagebox.showinfo("完成", f"已删除 {len(indices)} 个题目配置")
            dialog.destroy()

        btn_frame = tk.Frame(frm, bg=COLOR_BG)
        btn_frame.pack(fill="x", pady=(10, 0))
        tk.Button(btn_frame, text="删除", font=("微软雅黑", 11),
                  bg=COLOR_DANGER, fg=COLOR_WHITE, width=10,
                  command=do_delete).pack(side="right", padx=(5, 0))
        tk.Button(btn_frame, text="取消", font=("微软雅黑", 11),
                  bg="#757575", fg=COLOR_WHITE, width=10,
                  command=dialog.destroy).pack(side="right")

    # ==================== 参考答案管理对话框 ====================
    def show_reference_answer_manager(self):
        bank = self.question_bank
        # 收集所有编程题和网络设备题
        items = []
        for qtype in ("programming", "network_device"):
            for q in bank.get(qtype, []):
                items.append((qtype, q))

        dlg = tk.Toplevel(self.root)
        dlg.title("参考答案管理")
        dlg.geometry("1200x700")
        dlg.resizable(True, True)
        dlg.minsize(800, 500)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        sw, sh = self._screen_size()
        dlg.geometry(f"+{sw//2-600}+{sh//2-350}")

        tk.Label(dlg, text="参考答案管理（编程题 & 网络设备题）", font=("微软雅黑", 16, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 10))

        # 列表区域
        list_canvas = tk.Canvas(dlg, bg=COLOR_BG, highlightthickness=0)
        list_scroll = tk.Scrollbar(dlg, orient="vertical", command=list_canvas.yview)
        list_frame = tk.Frame(list_canvas, bg=COLOR_BG)
        list_frame.bind("<Configure>", lambda e: list_canvas.configure(scrollregion=list_canvas.bbox("all")))
        list_canvas.create_window((0, 0), window=list_frame, anchor="nw", width=1160)
        list_canvas.configure(yscrollcommand=list_scroll.set)
        list_canvas.pack(side="left", fill="both", expand=True, padx=(20, 0), pady=10)
        list_scroll.pack(side="right", fill="y", pady=10, padx=(0, 20))
        ExamSystem._setup_auto_scroll(list_canvas, list_scroll, side="right", fill="y", pady=10, padx=(0, 20))

        type_labels = {"programming": "编程题", "network_device": "网络设备题"}

        def render_list():
            for w in list_frame.winfo_children():
                w.destroy()
            if not items:
                tk.Label(list_frame, text="暂无编程题或网络设备题", font=("微软雅黑", 12),
                        fg="#999", bg=COLOR_BG).pack(pady=30)
                return

            for qtype, q in items:
                row = tk.Frame(list_frame, bg=COLOR_WHITE, bd=1, relief="solid")
                row.pack(fill="x", pady=2, padx=5)

                info_frame = tk.Frame(row, bg=COLOR_WHITE)
                info_frame.pack(side="left", fill="x", padx=8, pady=6)

                qid = q.get("id", "?")
                content_preview = q.get("content", "")[:80]
                ref = q.get("reference_answer", "")
                if isinstance(ref, dict):
                    has_ref = "有" if any(v.strip() for v in ref.values()) else "无"
                else:
                    has_ref = "有" if ref.strip() else "无"

                tk.Label(info_frame, text=f"#{qid}  [{type_labels.get(qtype, qtype)}]",
                        font=("微软雅黑", 10, "bold"), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(anchor="w")
                tk.Label(info_frame, text=content_preview,
                        font=("微软雅黑", 9), fg="#666", bg=COLOR_WHITE, wraplength=500, anchor="w").pack(anchor="w", pady=(1, 0))
                tk.Label(info_frame, text=f"参考答案：{has_ref}",
                        font=("微软雅黑", 9), fg=COLOR_SUCCESS if has_ref == "有" else "#999", bg=COLOR_WHITE).pack(anchor="w", pady=(1, 0))

                def make_edit_cmd(qtype, q, dlg_ref):
                    def cmd():
                        self._edit_reference_answer(qtype, q, dlg_ref)
                    return cmd

                tk.Button(row, text="编辑答案", font=("微软雅黑", 9),
                         bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=8, pady=4,
                         cursor="hand2",
                         command=make_edit_cmd(qtype, q, dlg)).pack(side="left", padx=(6, 0))

        render_list()

        btn_frame = tk.Frame(dlg, bg=COLOR_BG)
        btn_frame.pack(fill="x", padx=20, pady=(5, 15))
        tk.Button(btn_frame, text="关闭", font=("微软雅黑", 11),
                 bg="#ccc", fg="#333", bd=0, padx=20, pady=8,
                 cursor="hand2", command=dlg.destroy).pack(side="right")

    def _edit_reference_answer(self, qtype, q, parent_dlg):
        """弹出子对话框编辑单个题目的参考答案"""
        sub = tk.Toplevel(parent_dlg)
        sub.title(f"编辑参考答案 - #{q.get('id', '?')}")
        sub.geometry("1200x700")
        sub.resizable(True, True)
        sub.minsize(800, 500)
        sub.configure(bg=COLOR_BG)
        sub.transient(parent_dlg)
        sub.grab_set()
        sw, sh = self._screen_size()
        sub.geometry(f"+{sw//2-600}+{sh//2-350}")

        tk.Label(sub, text="题目内容（只读）", font=("微软雅黑", 11, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w", padx=15, pady=(15, 5))

        content_frame = tk.Frame(sub, bg=COLOR_WHITE, bd=1, relief="solid")
        content_frame.pack(fill="x", padx=15, pady=(0, 10))
        content_txt = tk.Text(content_frame, font=("微软雅黑", 10), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                             wrap="word", bd=0, padx=10, pady=8, height=1)
        content_txt.pack(fill="x")
        content_txt.insert("1.0", q.get("content", ""))
        content_txt.configure(state="disabled")
        # 自适应高度：根据内容行数设定 height
        content_txt.update_idletasks()
        content_lines = max(int(content_txt.index("end-1c").split(".")[0]), 1)
        content_txt.configure(height=min(content_lines, 12))

        ref = q.get("reference_answer", "")
        text_fields = q.get("text_fields", [])

        # 网络设备题：按 text_fields 逐字段编辑答案
        if isinstance(ref, dict) and text_fields:
            tk.Label(sub, text="参考答案（逐字段设置）", font=("微软雅黑", 11, "bold"),
                    fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w", padx=15, pady=(5, 5))

            tk.Label(sub,
                    text="支持格式: 精确值 | 3000~3999(范围) | val1|val2(多值) | *关键词*(包含) | /正则/",
                    font=("微软雅黑", 8), fg="#888", bg=COLOR_BG).pack(anchor="w", padx=15, pady=(0, 8))

            answer_frame = tk.Frame(sub, bg=COLOR_WHITE, bd=1, relief="solid")
            answer_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

            # 可滚动区域
            ans_canvas = tk.Canvas(answer_frame, bg=COLOR_WHITE, highlightthickness=0)
            ans_scroll = tk.Scrollbar(answer_frame, orient="vertical", command=ans_canvas.yview)
            ans_inner = tk.Frame(ans_canvas, bg=COLOR_WHITE)
            ans_inner.bind("<Configure>", lambda e: ans_canvas.configure(scrollregion=ans_canvas.bbox("all")))
            ans_canvas.create_window((0, 0), window=ans_inner, anchor="nw", width=1150)
            ans_canvas.configure(yscrollcommand=ans_scroll.set)
            ans_canvas.pack(side="left", fill="both", expand=True)
            ans_scroll.pack(side="right", fill="y")

            field_entries = {}  # label -> StringVar
            for tf in text_fields:
                label = tf.get("label", "")
                row = tk.Frame(ans_inner, bg=COLOR_WHITE)
                row.pack(fill="x", padx=10, pady=(6, 0))

                tk.Label(row, text=f"{label}：", font=("微软雅黑", 10, "bold"),
                        fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, anchor="w", width=20).pack(side="left")

                val = ref.get(label, "")
                sv = tk.StringVar(value=val)
                field_entries[label] = sv

                entry_frame = tk.Frame(row, bg="#e8f0fe", bd=1)
                entry_frame.pack(side="left", fill="x", expand=True)
                tk.Entry(entry_frame, textvariable=sv, font=("Consolas", 10),
                        bg=COLOR_WHITE, fg=COLOR_TEXT_DARK, bd=0).pack(fill="x", padx=4, pady=3)

            def do_save():
                new_ref = {}
                for tf in text_fields:
                    label = tf.get("label", "")
                    sv = field_entries.get(label)
                    if sv:
                        new_ref[label] = sv.get().strip()
                q["reference_answer"] = new_ref
                save_question_bank(self.question_bank)
                messagebox.showinfo("保存成功", "参考答案已保存")
                sub.destroy()
                self._refresh_answer_manager_list(parent_dlg)

        # 编程题或其他：单个文本答案框
        else:
            tk.Label(sub, text="参考答案（支持 Markdown）", font=("微软雅黑", 11, "bold"),
                    fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w", padx=15, pady=(5, 5))

            answer_frame = tk.Frame(sub, bg=COLOR_WHITE, bd=1, relief="solid")
            answer_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))
            answer_txt = tk.Text(answer_frame, font=("Consolas", 10), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                                wrap="word", bd=0, padx=10, pady=8)
            answer_txt.pack(fill="both", expand=True)
            answer_txt.insert("1.0", ref if isinstance(ref, str) else str(ref))

            def do_save():
                new_answer = answer_txt.get("1.0", "end-1c").strip()
                q["reference_answer"] = new_answer
                save_question_bank(self.question_bank)
                messagebox.showinfo("保存成功", "参考答案已保存")
                sub.destroy()
                self._refresh_answer_manager_list(parent_dlg)

        btn_row = tk.Frame(sub, bg=COLOR_BG)
        btn_row.pack(fill="x", padx=15, pady=(0, 10))
        tk.Button(btn_row, text="保存", font=("微软雅黑", 11, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                 cursor="hand2", command=do_save).pack(side="right", padx=(5, 0))
        tk.Button(btn_row, text="取消", font=("微软雅黑", 11),
                 bg="#ccc", fg="#333", bd=0, padx=20, pady=6,
                 cursor="hand2", command=sub.destroy).pack(side="right")

    def _refresh_answer_manager_list(self, parent_dlg):
        """刷新参考答案管理对话框的列表"""
        for w in parent_dlg.winfo_children():
            if isinstance(w, tk.Canvas):
                for child in w.winfo_children():
                    if isinstance(child, tk.Frame):
                        for w2 in child.winfo_children():
                            w2.destroy()
                        type_labels = {"programming": "编程题", "network_device": "网络设备题"}
                        for qtype2, q2 in [(qt, qq) for qt in ("programming", "network_device") for qq in self.question_bank.get(qt, [])]:
                            row = tk.Frame(child, bg=COLOR_WHITE, bd=1, relief="solid")
                            row.pack(fill="x", pady=2, padx=5)
                            info_frame = tk.Frame(row, bg=COLOR_WHITE)
                            info_frame.pack(side="left", fill="x", padx=8, pady=6)
                            qid2 = q2.get("id", "?")
                            cp2 = q2.get("content", "")[:80]
                            ref2 = q2.get("reference_answer", "")
                            if isinstance(ref2, dict):
                                hr2 = "有" if any(v.strip() for v in ref2.values()) else "无"
                            else:
                                hr2 = "有" if ref2.strip() else "无"
                            tk.Label(info_frame, text=f"#{qid2}  [{type_labels.get(qtype2, qtype2)}]",
                                    font=("微软雅黑", 10, "bold"), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(anchor="w")
                            tk.Label(info_frame, text=cp2, font=("微软雅黑", 9), fg="#666", bg=COLOR_WHITE,
                                    wraplength=500, anchor="w").pack(anchor="w", pady=(1, 0))
                            tk.Label(info_frame, text=f"参考答案：{hr2}",
                                    font=("微软雅黑", 9), fg=COLOR_SUCCESS if hr2 == "有" else "#999",
                                    bg=COLOR_WHITE).pack(anchor="w", pady=(1, 0))
                            def make_edit2(qt3, q3, pdlg):
                                def cmd2():
                                    self._edit_reference_answer(qt3, q3, pdlg)
                                return cmd2
                            tk.Button(row, text="编辑答案", font=("微软雅黑", 9),
                                     bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=8, pady=4,
                                     cursor="hand2",
                                     command=make_edit2(qtype2, q2, parent_dlg)).pack(side="left", padx=(6, 0))

    # ==================== 逐题添加对话框 ====================
    TYPE_NAMES = {
        "single_choice": "单选题", "fill_blank": "填空题", "programming": "编程题",
        "web_design": "网页设计题", "network_device": "网络设备题", "graphic_design": "图形图像题"
    }

    def show_add_question_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("逐题添加")
        dlg.geometry("1050x780+100+80")
        dlg.resizable(True, True)
        dlg.minsize(700, 500)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.root)
        dlg.grab_set()

        # 题型选择
        top = tk.Frame(dlg, bg=COLOR_BG)
        top.pack(fill="x", padx=20, pady=(15, 5))
        tk.Label(top, text="题型：", font=("微软雅黑", 12), bg=COLOR_BG).pack(side="left")
        # 中英文题型映射：下拉框显示中文，内部判断使用英文 key
        TYPE_MAP = [
            ("单选题", "single_choice"),
            ("填空题", "fill_blank"),
            ("编程题", "programming"),
            ("网页制作题", "web_design"),
            ("网络设备题", "network_device"),
            ("图形图像题", "graphic_design"),
        ]
        type_labels = [label for label, _ in TYPE_MAP]
        label_to_key = {label: key for label, key in TYPE_MAP}
        type_var = tk.StringVar(value="单选题")
        type_cb = ttk.Combobox(top, textvariable=type_var, values=type_labels,
                               state="readonly", font=("微软雅黑", 11), width=16)
        type_cb.pack(side="left", padx=10)

        # 题目来源
        tk.Label(top, text="来源：", font=("微软雅黑", 12), bg=COLOR_BG).pack(side="left", padx=(20, 5))
        source_var = tk.StringVar()
        tk.Entry(top, textvariable=source_var, font=("微软雅黑", 11), width=20, bd=1,
                 relief="solid").pack(side="left")

        # 图片上传
        img_frame = tk.Frame(dlg, bg=COLOR_BG)
        img_frame.pack(fill="x", padx=20, pady=(0, 5))
        img_path_var = tk.StringVar()
        img_preview_label = None

        def select_image():
            nonlocal img_preview_label
            fp = filedialog.askopenfilename(title="选择题干图片",
                filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.bmp"), ("所有文件", "*.*")])
            if not fp:
                return
            img_path_var.set(fp)
            # 预览
            try:
                pil_img = Image.open(fp)
                pil_img.thumbnail((200, 150), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(pil_img)
                if img_preview_label:
                    img_preview_label.destroy()
                img_preview_label = tk.Label(img_frame, image=photo, bg=COLOR_BG, bd=1, relief="solid")
                img_preview_label.image = photo
                img_preview_label._pil_ref = pil_img
                img_preview_label.pack(side="top", pady=(5, 5))
            except Exception:
                pass
        def clear_image():
            nonlocal img_preview_label
            img_path_var.set("")
            if img_preview_label:
                img_preview_label.destroy()
                img_preview_label = None

        img_btn_row = tk.Frame(img_frame, bg=COLOR_BG)
        img_btn_row.pack(fill="x")
        tk.Button(img_btn_row, text="选择图片（可选）", font=("微软雅黑", 10),
                  bg="#e3f2fd", fg="#1565c0", bd=1, relief="solid",
                  cursor="hand2", padx=10, pady=3, command=select_image).pack(side="left")
        tk.Button(img_btn_row, text="清除图片", font=("微软雅黑", 9),
                  bg="#ffebee", fg="#c62828", bd=1, relief="solid",
                  cursor="hand2", padx=8, pady=3, command=clear_image).pack(side="left", padx=5)

        # 动态表单区（滚动）
        form_canvas = tk.Canvas(dlg, bg=COLOR_BG, highlightthickness=0)
        form_scroll = tk.Scrollbar(dlg, orient="vertical", command=form_canvas.yview)
        form_frame = tk.Frame(form_canvas, bg=COLOR_BG)
        form_canvas.configure(yscrollcommand=form_scroll.set)
        form_canvas.pack(side="left", fill="both", expand=True, padx=(20, 0), pady=10)
        form_scroll.pack(side="right", fill="y", pady=10, padx=(0, 20))
        form_canvas.create_window((0, 0), window=form_frame, anchor="nw",
                                  tags="form_win", width=610)
        form_frame.bind("<Configure>", lambda e: form_canvas.configure(scrollregion=form_canvas.bbox("all")))
        ExamSystem._setup_auto_scroll(form_canvas, form_scroll, side="right", fill="y", pady=10, padx=(0, 20))

        # 一对一，存储每个类型的子控件引用
        type_widgets = {}

        def clear_form():
            for w in form_frame.winfo_children():
                w.destroy()
            type_widgets.clear()

        def build_single_choice():
            f = tk.Frame(form_frame, bg=COLOR_WHITE, bd=0, highlightbackground="#ddd",
                         highlightthickness=1)
            f.pack(fill="x", pady=5)
            # 题干
            tk.Label(f, text="题干", font=("微软雅黑", 11, "bold"), bg=COLOR_WHITE).pack(anchor="w", padx=12, pady=(10, 2))
            ct_frame = tk.Frame(f, bg=COLOR_WHITE)
            ct_frame.pack(fill="x", padx=12, pady=(0, 8))
            content_txt = tk.Text(ct_frame, height=3, font=("微软雅黑", 10), wrap="word", bd=1, relief="solid")
            ct_scroll = tk.Scrollbar(ct_frame, orient="vertical", command=content_txt.yview)
            content_txt.configure(yscrollcommand=ct_scroll.set)
            content_txt.pack(side="left", fill="both", expand=True)
            ExamSystem._setup_text_auto_scroll(content_txt, ct_scroll, side="right", fill="y")
            # 选项
            opts_frame = tk.Frame(f, bg=COLOR_WHITE)
            opts_frame.pack(fill="x", padx=12, pady=(0, 8))
            opts = []
            for i, label in enumerate(["A", "B", "C", "D"]):
                tk.Label(opts_frame, text=f"{label}.", font=("微软雅黑", 10), bg=COLOR_WHITE).grid(row=i, column=0,
                                                                                                   sticky="e", padx=(0, 5), pady=2)
                ev = tk.Entry(opts_frame, font=("微软雅黑", 10), bd=1, relief="solid")
                ev.grid(row=i, column=1, sticky="ew", pady=2)
                opts.append(ev)
            opts_frame.columnconfigure(1, weight=1)
            # 答案
            ans_frame = tk.Frame(f, bg=COLOR_WHITE)
            ans_frame.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(ans_frame, text="正确答案", font=("微软雅黑", 10), bg=COLOR_WHITE).pack(side="left")
            ans_var = tk.StringVar(value="0")
            for i, label in enumerate(["A", "B", "C", "D"]):
                tk.Radiobutton(ans_frame, text=label, variable=ans_var, value=str(i),
                               bg=COLOR_WHITE, font=("微软雅黑", 10)).pack(side="left", padx=4)
            # 分值
            sc_frame = tk.Frame(f, bg=COLOR_WHITE)
            sc_frame.pack(fill="x", padx=12, pady=(0, 12))
            tk.Label(sc_frame, text="分值", font=("微软雅黑", 10), bg=COLOR_WHITE).pack(side="left")
            sc_var = tk.StringVar(value="2")
            tk.Entry(sc_frame, textvariable=sc_var, font=("微软雅黑", 10), width=6, bd=1,
                     relief="solid").pack(side="left", padx=5)

            type_widgets["single_choice"] = {
                "content": content_txt, "options": opts, "answer": ans_var, "score": sc_var
            }

        def build_fill_blank():
            f = tk.Frame(form_frame, bg=COLOR_WHITE, bd=0, highlightbackground="#ddd",
                         highlightthickness=1)
            f.pack(fill="x", pady=5)
            tk.Label(f, text="题干（用【1】【2】标记空位）", font=("微软雅黑", 11, "bold"), bg=COLOR_WHITE).pack(
                anchor="w", padx=12, pady=(10, 2))
            ct_frame = tk.Frame(f, bg=COLOR_WHITE)
            ct_frame.pack(fill="x", padx=12, pady=(0, 8))
            content_txt = tk.Text(ct_frame, height=4, font=("Microsoft YaHei Mono", 10), wrap="word", bd=1,
                                  relief="solid")
            ct_scroll = tk.Scrollbar(ct_frame, orient="vertical", command=content_txt.yview)
            content_txt.configure(yscrollcommand=ct_scroll.set)
            content_txt.pack(side="left", fill="both", expand=True)
            ExamSystem._setup_text_auto_scroll(content_txt, ct_scroll, side="right", fill="y")
            # 代码区（可选）
            tk.Label(f, text="参考代码（可选）", font=("微软雅黑", 10, "bold"), bg=COLOR_WHITE).pack(anchor="w", padx=12, pady=(0, 2))
            code_txt = tk.Text(f, height=4, font=("Consolas", 9), wrap="none", bd=1, relief="solid")
            code_txt.pack(fill="x", padx=12, pady=(0, 8))
            # 共享选项区
            opts_frame = tk.Frame(f, bg=COLOR_WHITE)
            opts_frame.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(opts_frame, text="备选项（共10个，A~J）", font=("微软雅黑", 10, "bold"), bg=COLOR_WHITE).pack(anchor="w")
            option_entries = []
            letters = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
            for i, letter in enumerate(letters):
                row_f = tk.Frame(opts_frame, bg=COLOR_WHITE)
                row_f.pack(fill="x", pady=1)
                tk.Label(row_f, text=f"  {letter}.", font=("微软雅黑", 9), bg=COLOR_WHITE, width=3,
                         anchor="e").pack(side="left", padx=(0, 5))
                ov = tk.StringVar()
                tk.Entry(row_f, textvariable=ov, font=("微软雅黑", 9), bd=1, relief="solid").pack(
                    side="left", fill="x", expand=True)
                option_entries.append(ov)
            # 空位答案区
            blanks_frame = tk.Frame(f, bg=COLOR_WHITE)
            blanks_frame.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(blanks_frame, text="空位答案（共5个空位）", font=("微软雅黑", 10, "bold"), bg=COLOR_WHITE).pack(anchor="w")
            blanks_list = []
            blanks_container = tk.Frame(blanks_frame, bg=COLOR_WHITE)
            blanks_container.pack(fill="x", pady=(4, 0))
            for idx in range(5):
                bf = tk.Frame(blanks_container, bg=COLOR_WHITE, bd=1, relief="groove")
                bf.pack(fill="x", pady=2, padx=4)
                top_row = tk.Frame(bf, bg=COLOR_WHITE)
                top_row.pack(fill="x", padx=6, pady=(4, 2))
                tk.Label(top_row, text=f"空位 {idx+1}：", font=("微软雅黑", 9), bg=COLOR_WHITE).pack(side="left")
                label_var = tk.StringVar(value=f"【{idx+1}】")
                tk.Entry(top_row, textvariable=label_var, font=("微软雅黑", 9), width=8, bd=1,
                         relief="solid").pack(side="left", padx=4)
                tk.Label(top_row, text="  答案", font=("微软雅黑", 9), bg=COLOR_WHITE).pack(side="left", padx=(8, 4))
                answer_var = tk.StringVar(value="A")
                combo = ttk.Combobox(top_row, textvariable=answer_var, values=letters,
                                     state="readonly", font=("微软雅黑", 9), width=4)
                combo.pack(side="left")
                blanks_list.append({"label_var": label_var, "answer_var": answer_var})
            # 分值
            sc_frame = tk.Frame(f, bg=COLOR_WHITE)
            sc_frame.pack(fill="x", padx=12, pady=(0, 12))
            tk.Label(sc_frame, text="分值", font=("微软雅黑", 10), bg=COLOR_WHITE).pack(side="left")
            sc_var = tk.StringVar(value="10")
            tk.Entry(sc_frame, textvariable=sc_var, font=("微软雅黑", 10), width=6, bd=1,
                     relief="solid").pack(side="left", padx=5)

            type_widgets["fill_blank"] = {
                "content": content_txt, "code": code_txt, "option_entries": option_entries,
                "blanks": blanks_list, "score": sc_var
            }

        def build_text_only(label):
            f = tk.Frame(form_frame, bg=COLOR_WHITE, bd=0, highlightbackground="#ddd",
                         highlightthickness=1)
            f.pack(fill="x", pady=5)
            tk.Label(f, text=label, font=("微软雅黑", 11, "bold"), bg=COLOR_WHITE).pack(anchor="w", padx=12, pady=(10, 2))
            ct_frame = tk.Frame(f, bg=COLOR_WHITE)
            ct_frame.pack(fill="x", padx=12, pady=(0, 8))
            content_txt = tk.Text(ct_frame, height=6, font=("微软雅黑", 10), wrap="word", bd=1, relief="solid")
            ct_scroll = tk.Scrollbar(ct_frame, orient="vertical", command=content_txt.yview)
            content_txt.configure(yscrollcommand=ct_scroll.set)
            content_txt.pack(side="left", fill="both", expand=True)
            ExamSystem._setup_text_auto_scroll(content_txt, ct_scroll, side="right", fill="y")
            sc_frame = tk.Frame(f, bg=COLOR_WHITE)
            sc_frame.pack(fill="x", padx=12, pady=(0, 12))
            tk.Label(sc_frame, text="分值", font=("微软雅黑", 10), bg=COLOR_WHITE).pack(side="left")
            sc_var = tk.StringVar(value="20")
            tk.Entry(sc_frame, textvariable=sc_var, font=("微软雅黑", 10), width=6, bd=1,
                     relief="solid").pack(side="left", padx=5)
            return {"content": content_txt, "score": sc_var}, f

        def build_network_device():
            f = tk.Frame(form_frame, bg=COLOR_WHITE, bd=0, highlightbackground="#ddd",
                         highlightthickness=1)
            f.pack(fill="x", pady=5)
            tk.Label(f, text="题目描述（网络拓扑 + 调试要求）", font=("微软雅黑", 11, "bold"), bg=COLOR_WHITE).pack(
                anchor="w", padx=12, pady=(10, 2))
            ct_frame = tk.Frame(f, bg=COLOR_WHITE)
            ct_frame.pack(fill="x", padx=12, pady=(0, 8))
            content_txt = tk.Text(ct_frame, height=5, font=("微软雅黑", 10), wrap="word", bd=1, relief="solid")
            ct_scroll = tk.Scrollbar(ct_frame, orient="vertical", command=content_txt.yview)
            content_txt.configure(yscrollcommand=ct_scroll.set)
            content_txt.pack(side="left", fill="both", expand=True)
            ExamSystem._setup_text_auto_scroll(content_txt, ct_scroll, side="right", fill="y")
            # 文本框字段
            tf_frame = tk.Frame(f, bg=COLOR_WHITE)
            tf_frame.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(tf_frame, text="考生输入字段", font=("微软雅黑", 10, "bold"), bg=COLOR_WHITE).pack(anchor="w")
            text_fields = []
            tf_container = tk.Frame(tf_frame, bg=COLOR_WHITE)
            tf_container.pack(fill="x", pady=(4, 0))

            def add_text_field():
                idx = len(text_fields)
                row = tk.Frame(tf_container, bg=COLOR_WHITE)
                row.pack(fill="x", pady=1)
                tk.Label(row, text=f"字段{idx + 1}标签：", font=("微软雅黑", 9), bg=COLOR_WHITE).pack(side="left")
                tv = tk.StringVar(value=f"(1) 字段{idx + 1}")
                tk.Entry(row, textvariable=tv, font=("微软雅黑", 9), bd=1, relief="solid").pack(side="left",
                                                                                               fill="x",
                                                                                               expand=True, padx=4)
                del_btn = tk.Button(row, text="×", font=("微软雅黑", 8), fg="red", bg=COLOR_WHITE, bd=0,
                                    cursor="hand2",
                                    command=lambda r=row, ti=idx: self._remove_net_field(
                                        text_fields, r, ti))
                del_btn.pack(side="left", padx=2)
                text_fields.append({"var": tv, "row": row})

            tk.Button(tf_container, text="+ 添加字段", font=("微软雅黑", 9), bg="#e3f2fd", bd=1,
                      relief="solid", cursor="hand2", command=add_text_field).pack(anchor="w", pady=2)
            add_text_field()
            # 分值
            sc_frame = tk.Frame(f, bg=COLOR_WHITE)
            sc_frame.pack(fill="x", padx=12, pady=(0, 12))
            tk.Label(sc_frame, text="分值", font=("微软雅黑", 10), bg=COLOR_WHITE).pack(side="left")
            sc_var = tk.StringVar(value="15")
            tk.Entry(sc_frame, textvariable=sc_var, font=("微软雅黑", 10), width=6, bd=1,
                     relief="solid").pack(side="left", padx=5)

            # 答案 Word 文档
            ans_doc_frame = tk.Frame(f, bg=COLOR_WHITE)
            ans_doc_frame.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(ans_doc_frame, text="答案文档", font=("微软雅黑", 10), bg=COLOR_WHITE).pack(side="left")
            answer_doc_var = tk.StringVar(value="")
            ans_doc_entry = tk.Entry(ans_doc_frame, textvariable=answer_doc_var, font=("微软雅黑", 10),
                                     width=40, bd=1, relief="solid", state="readonly",
                                     readonlybackground=COLOR_WHITE)
            ans_doc_entry.pack(side="left", padx=(5, 5))
            tk.Button(ans_doc_frame, text="浏览...", font=("微软雅黑", 9),
                     bg="#e3f2fd", fg=COLOR_ACCENT, bd=0, padx=8, pady=2,
                     cursor="hand2", command=lambda: answer_doc_var.set(
                         filedialog.askopenfilename(title="选择答案 Word 文档",
                             filetypes=[("Word 文档", "*.docx *.doc"), ("所有文件", "*.*")],
                             initialdir=os.path.dirname(__file__))
                     ) or None).pack(side="left")

            # 参考答案（文本输入）
            ref_frame = tk.Frame(f, bg=COLOR_WHITE)
            ref_frame.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(ref_frame, text="参考答案", font=("微软雅黑", 10, "bold"), bg=COLOR_WHITE).pack(anchor="w")
            ref_answer_txt = tk.Text(ref_frame, height=5, font=("微软雅黑", 10), wrap="word", bd=1, relief="solid")
            ref_answer_txt.pack(fill="x", pady=(2, 0))

            widgets = {"content": content_txt, "text_fields": text_fields, "score": sc_var,
                       "answer_doc": answer_doc_var, "ref_answer": ref_answer_txt}
            type_widgets["network_device"] = widgets
            return widgets, f

        def build_folder_design(label, default_score="20", default_folder=""):
            """构建带文件夹选择的表单（网页/图形题）"""
            f = tk.Frame(form_frame, bg=COLOR_WHITE, bd=0, highlightbackground="#ddd",
                         highlightthickness=1)
            f.pack(fill="x", pady=5)
            tk.Label(f, text=label, font=("微软雅黑", 11, "bold"), bg=COLOR_WHITE).pack(anchor="w", padx=12, pady=(10, 2))
            ct_frame = tk.Frame(f, bg=COLOR_WHITE)
            ct_frame.pack(fill="x", padx=12, pady=(0, 8))
            content_txt = tk.Text(ct_frame, height=6, font=("微软雅黑", 10), wrap="word", bd=1, relief="solid")
            ct_scroll = tk.Scrollbar(ct_frame, orient="vertical", command=content_txt.yview)
            content_txt.configure(yscrollcommand=ct_scroll.set)
            content_txt.pack(side="left", fill="both", expand=True)
            ExamSystem._setup_text_auto_scroll(content_txt, ct_scroll, side="right", fill="y")
            # 文件夹路径
            folder_frame = tk.Frame(f, bg=COLOR_WHITE)
            folder_frame.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(folder_frame, text="题目文件夹", font=("微软雅黑", 10), bg=COLOR_WHITE).pack(side="left")
            folder_var = tk.StringVar(value=default_folder)
            folder_entry = tk.Entry(folder_frame, textvariable=folder_var, font=("微软雅黑", 10),
                                   width=40, bd=1, relief="solid", state="readonly",
                                   readonlybackground=COLOR_WHITE)
            folder_entry.pack(side="left", padx=(5, 5))
            tk.Button(folder_frame, text="浏览...", font=("微软雅黑", 9),
                     bg="#e3f2fd", fg=COLOR_ACCENT, bd=0, padx=8, pady=2,
                     cursor="hand2", command=lambda v=folder_var: v.set(
                         filedialog.askdirectory(title="选择题目文件夹", initialdir=v.get() or os.path.dirname(__file__))
                     ) or None).pack(side="left")
            # 分值
            sc_frame = tk.Frame(f, bg=COLOR_WHITE)
            sc_frame.pack(fill="x", padx=12, pady=(0, 12))
            tk.Label(sc_frame, text="分值", font=("微软雅黑", 10), bg=COLOR_WHITE).pack(side="left")
            sc_var = tk.StringVar(value=default_score)
            tk.Entry(sc_frame, textvariable=sc_var, font=("微软雅黑", 10), width=6, bd=1,
                     relief="solid").pack(side="left", padx=5)
            return {"content": content_txt, "score": sc_var, "folder_path": folder_var}, f

        def build_form(*_):
            clear_form()
            qtype = label_to_key.get(type_var.get(), "single_choice")
            if qtype == "single_choice":
                build_single_choice()
            elif qtype == "fill_blank":
                build_fill_blank()
            elif qtype == "programming":
                w, f = build_text_only("题目描述（程序功能）")
                type_widgets["programming"] = w
                # 添加测试案例区域
                tc_container = tk.Frame(f, bg=COLOR_WHITE)
                tc_container.pack(fill="x", padx=12, pady=(0, 12))
                tk.Label(tc_container, text="测试案例", font=("微软雅黑", 11, "bold"), bg=COLOR_WHITE).pack(anchor="w")
                tc_hint = tk.Label(tc_container, text="每个案例需提供 .in（输入）和 .out（期望输出）",
                                   font=("微软雅黑", 8), fg="#999", bg=COLOR_WHITE)
                tc_hint.pack(anchor="w", pady=(2, 6))
                tc_list_frame = tk.Frame(tc_container, bg=COLOR_WHITE)
                tc_list_frame.pack(fill="x")
                test_cases = []

                def add_test_case_row():
                    idx = len(test_cases)
                    row = tk.Frame(tc_list_frame, bg=COLOR_WHITE)
                    row.pack(fill="x", pady=2)
                    tk.Label(row, text=f"#{idx + 1}", font=("Consolas", 9, "bold"),
                             fg=COLOR_ACCENT, bg=COLOR_WHITE, width=4, anchor="w").pack(side="left")
                    in_entry = tk.Text(row, height=2, font=("Consolas", 9), width=30, bd=1, relief="solid")
                    in_entry.pack(side="left", padx=(0, 5))
                    out_entry = tk.Text(row, height=2, font=("Consolas", 9), width=30, bd=1, relief="solid")
                    out_entry.pack(side="left", padx=(0, 5))
                    del_btn = tk.Button(row, text="×", font=("微软雅黑", 9, "bold"),
                                        fg="red", bg=COLOR_WHITE, bd=0, cursor="hand2",
                                        command=lambda r=row, tcs=test_cases, idx=idx: _remove_tc(r, tcs, idx))
                    del_btn.pack(side="left")
                    test_cases.append({"in": in_entry, "out": out_entry})

                def _remove_tc(row, tcs, idx):
                    row.destroy()
                    tcs[idx] = None  # 标记删除，避免索引错乱
                    # 重新编号剩余行
                    for i, tc in enumerate(tcs):
                        if tc is not None:
                            tc["_new_idx"] = i + 1

                add_btn = tk.Button(tc_container, text="+ 添加案例", font=("微软雅黑", 9),
                                    bg="#e3f2fd", fg=COLOR_ACCENT, bd=0, padx=12, pady=4,
                                    cursor="hand2", command=add_test_case_row)
                add_btn.pack(anchor="w", pady=(6, 0))
                # 默认添加3个案例
                for _ in range(3):
                    add_test_case_row()
                w["test_cases"] = test_cases
                # 添加参考答案输入框
                ref_frame = tk.Frame(f, bg=COLOR_WHITE)
                ref_frame.pack(fill="x", padx=12, pady=(8, 12))
                tk.Label(ref_frame, text="参考答案（可选）", font=("微软雅黑", 11, "bold"), bg=COLOR_WHITE).pack(anchor="w")
                ref_answer = tk.Text(ref_frame, height=8, font=("Consolas", 10), wrap="word", bd=1, relief="solid")
                ref_answer.pack(fill="x", pady=(4, 0))
                w["ref_answer"] = ref_answer
            elif qtype == "web_design":
                type_widgets["web_design"], _ = build_folder_design("题目描述（网页制作要求）", "30", "DW")
            elif qtype == "network_device":
                build_network_device()
            elif qtype == "graphic_design":
                type_widgets["graphic_design"], _ = build_folder_design("题目描述（图形图像处理要求）", "30", "PS")
            form_frame.update_idletasks()
            form_canvas.configure(scrollregion=form_canvas.bbox("all"))

        type_cb.bind("<<ComboboxSelected>>", build_form)

        def do_submit():
            qtype = label_to_key.get(type_var.get(), "single_choice")
            w = type_widgets.get(qtype)
            if not w:
                messagebox.showwarning("提示", "请先完成表单填写")
                return
            try:
                q = {}
                if qtype == "single_choice":
                    q["content"] = w["content"].get("1.0", "end-1c").strip()
                    q["options"] = [o.get().strip() for o in w["options"]]
                    if not q["content"] or any(not opt for opt in q["options"]):
                        raise ValueError("题干和所有选项不能为空")
                    q["answer"] = int(w["answer"].get())
                    q["score"] = int(w["score"].get())
                elif qtype == "programming":
                    q["content"] = w["content"].get("1.0", "end-1c").strip()
                    if not q["content"]:
                        raise ValueError("题干不能为空")
                    q["score"] = int(w["score"].get())
                    # 参考答案
                    q["reference_answer"] = w.get("ref_answer", {}).get("1.0", "end-1c").strip() if isinstance(w.get("ref_answer"), tk.Text) else ""
                    # 收集测试案例
                    test_cases = w.get("test_cases", [])
                    if test_cases:
                        q["_test_cases"] = []
                        for tc in test_cases:
                            if tc is None:
                                continue
                            in_text = tc["in"].get("1.0", "end-1c").strip()
                            out_text = tc["out"].get("1.0", "end-1c").strip()
                            if in_text or out_text:
                                q["_test_cases"].append({"in": in_text, "out": out_text})
                elif qtype in ("web_design", "graphic_design"):
                    q["content"] = w["content"].get("1.0", "end-1c").strip()
                    if not q["content"]:
                        raise ValueError("题干不能为空")
                    q["score"] = int(w["score"].get())
                    folder_path = w.get("folder_path")
                    if folder_path and folder_path.get():
                        q["folder_path"] = folder_path.get().strip()
                        _copy_material_folder_into_system(q, qtype)
                elif qtype == "fill_blank":
                    q["content"] = w["content"].get("1.0", "end-1c").strip()
                    if not q["content"]:
                        raise ValueError("题干不能为空")
                    code = w["code"].get("1.0", "end-1c").strip()
                    if code:
                        q["code"] = code
                    # 收集共享选项
                    shared_opts = [o.get().strip() for o in w["option_entries"] if o.get().strip()]
                    q["shared_options"] = shared_opts
                    # 收集空位答案
                    letter_to_idx = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4,
                                     "F": 5, "G": 6, "H": 7, "I": 8, "J": 9}
                    blanks = []
                    for b in w["blanks"]:
                        ans_letter = b["answer_var"].get().strip()
                        ans_idx = letter_to_idx.get(ans_letter, 0)
                        blanks.append({
                            "label": b["label_var"].get().strip(),
                            "answer": ans_idx
                        })
                    if not blanks:
                        raise ValueError("至少需要添加一个空位")
                    q["blanks"] = blanks
                    q["score"] = int(w["score"].get())
                elif qtype == "network_device":
                    q["content"] = w["content"].get("1.0", "end-1c").strip()
                    if not q["content"]:
                        raise ValueError("题目描述不能为空")
                    q["text_fields"] = [{"label": tf["var"].get().strip()} for tf in
                                        w["text_fields"]]
                    q["score"] = int(w["score"].get())
                    # 参考答案（从表单读取）
                    ref_widget = w.get("ref_answer")
                    q["reference_answer"] = ref_widget.get("1.0", "end-1c").strip() if isinstance(ref_widget, tk.Text) else ""
                    # 答案 Word 文档路径
                    answer_doc = w.get("answer_doc")
                    if answer_doc and answer_doc.get().strip():
                        q["answer_doc"] = _to_rel_path(answer_doc.get().strip())

                # 保存图片
                if img_path_var.get():
                    os.makedirs(IMAGE_DIR, exist_ok=True)
                    ext = os.path.splitext(img_path_var.get())[1]
                    img_name = f"{qtype}_{len(self.question_bank.get(qtype,[]))+1}_{datetime.now().strftime('%H%M%S')}{ext}"
                    dest = os.path.join(IMAGE_DIR, img_name)
                    shutil.copy2(img_path_var.get(), dest)
                    q["image"] = os.path.join("question_images", img_name)
                elif qtype in ("web_design", "graphic_design") and q.get("folder_path"):
                    # 自动从题目文件夹中读取名为"样图"的图片
                    folder = _resolve_path(q["folder_path"])
                    for test_ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif",
                                     ".PNG", ".JPG", ".JPEG", ".BMP", ".GIF"):
                        sample_path = os.path.join(folder, f"样图{test_ext}")
                        if os.path.exists(sample_path):
                            os.makedirs(IMAGE_DIR, exist_ok=True)
                            ext = os.path.splitext(sample_path)[1]
                            img_name = f"{qtype}_{len(self.question_bank.get(qtype,[]))+1}_{datetime.now().strftime('%H%M%S')}{ext}"
                            dest = os.path.join(IMAGE_DIR, img_name)
                            shutil.copy2(sample_path, dest)
                            q["image"] = os.path.join("question_images", img_name)
                            break

                q["source"] = source_var.get().strip()

                # 去重：检查 content 是否已存在
                pool = self.question_bank.get(qtype, [])
                new_content = q.get("content", "").strip()
                if new_content and any(q_existing.get("content", "").strip() == new_content for q_existing in pool):
                    messagebox.showwarning("重复题目", "该题目的题干内容与题库中已有题目重复，已跳过添加。")
                    return

                q["id"] = len(pool) + 1000
                self.question_bank.setdefault(qtype, []).append(q)
                save_question_bank(self.question_bank)

                # 保存编程题测试案例到文件
                if qtype == "programming" and q.get("_test_cases"):
                    tc_dir = os.path.join(IMAGE_DIR, "test_cases", f"prog_{q['id']}")
                    os.makedirs(tc_dir, exist_ok=True)
                    for i, tc in enumerate(q["_test_cases"], 1):
                        with open(os.path.join(tc_dir, f"{i}.in"), "w", encoding="utf-8") as f:
                            f.write(tc["in"])
                        with open(os.path.join(tc_dir, f"{i}.out"), "w", encoding="utf-8") as f:
                            f.write(tc["out"])
                    # 从题目数据中移除临时字段（不存入JSON）
                    del q["_test_cases"]
                    save_question_bank(self.question_bank)

                messagebox.showinfo("添加成功",
                                    f"已添加 1 道{self.TYPE_NAMES[qtype]}！\n"
                                    f"题型: {self.TYPE_NAMES[qtype]}\n当前题库总数: {sum(len(v) for v in self.question_bank.values())}")
                self.show_admin_panel()
                dlg.destroy()
            except ValueError as e:
                messagebox.showwarning("输入错误", f"请检查输入：{e}")
            except Exception as e:
                messagebox.showerror("保存失败", f"发生错误：{e}")

        # 底部按钮
        btn_frame = tk.Frame(dlg, bg=COLOR_BG)
        btn_frame.pack(fill="x", padx=20, pady=(5, 15))
        tk.Button(btn_frame, text="添加题目", font=("微软雅黑", 12, "bold"),
                  bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=25, pady=8,
                  cursor="hand2", command=do_submit).pack(side="right", padx=5)
        tk.Button(btn_frame, text="取消", font=("微软雅黑", 11),
                  bg="#ccc", fg="#333", bd=0, padx=20, pady=8,
                  cursor="hand2", command=dlg.destroy).pack(side="right", padx=5)

        # 显示当前题型说明
        info = tk.Label(dlg, text=f"当前题型：{type_var.get()}", font=("微软雅黑", 10),
                        fg="#666", bg=COLOR_BG)
        info.pack(anchor="w", padx=20, pady=(0, 0))
        type_var.trace_add("write",
                           lambda *_: info.configure(text=f"当前题型：{type_var.get()}"))

        build_form()
        dlg.focus_set()

    @staticmethod
    def _remove_net_field(text_fields, row, index):
        if len(text_fields) <= 1:
            return
        row.destroy()
        text_fields.pop(index)

    # ==================== 考生须知 ====================
    def show_notice(self):
        self._clear_window()
        self.root.deiconify()
        self.root.state('zoomed')
        sw, sh = self._screen_size()
        cx, cy = sw // 2, sh // 2

        canvas = tk.Canvas(self.root, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._create_gradient_bg(canvas, sw, sh, "#1a6fb5", "#0d4f8a")

        canvas.create_text(cx, 50, text="考 生 须 知",
                          fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 26, "bold"))

        notice_card = tk.Frame(canvas, bg=COLOR_WHITE, bd=0,
                              highlightbackground="#2980b9", highlightthickness=2)
        canvas.create_window(cx, cy, window=notice_card, width=1000, height=520)

        notice_text = f"""一、考试时长：60分钟

二、考试模块：C语言、网页制作、网络设备安装与调试、图形图像处理

三、本次试题：
    单选题：{self.question_config.get('single_choice', 0)} 道
    选择填空题：{self.question_config.get('fill_blank', 0)} 道
    编程题：{self.question_config.get('programming', 0)} 道
    网页制作题：{self.question_config.get('web_design', 0)} 道
    网络设备安装与调试题：{self.question_config.get('network_device', 0)} 道
    图形图像处理题：{self.question_config.get('graphic_design', 0)} 道
    共 {len(self.questions)} 道题

四、考试形式：登录考试系统答题，考试结果以考试系统提交为准

五、注意事项：
    (1) 考试过程中，至少每间隔十分钟保存一次作答文件
    (2) 考生不得自行关闭或重启系统，不得删除或移动系统文件
    (3) 禁止更改考试文件的命名及存储路径，否则作答无效
    (4) 严禁将任何考试资料带离考场，不得提前离场
    (5) 考试过程全程录屏监控
    (6) 考场时钟仅作参考，以考试系统计时为准"""

        text_widget = tk.Text(notice_card, font=("微软雅黑", 12), bg=COLOR_WHITE,
                             fg=COLOR_TEXT_DARK, wrap="word", bd=0, padx=30, pady=20)
        text_widget.pack(fill="both", expand=True)
        text_widget.insert("1.0", notice_text)
        text_widget.config(state="disabled")

        # 确认区域 - 无倒计时，直接可用
        bottom_frame = tk.Frame(canvas, bg="#0d4f8a")
        canvas.create_window(cx, sh - 60, window=bottom_frame)

        self.notice_check_var = tk.BooleanVar(value=False)
        self.notice_check = tk.Checkbutton(bottom_frame,
                                          text="我已认真阅读并知悉以上全部考生须知内容",
                                          variable=self.notice_check_var,
                                          fg=COLOR_TEXT_LIGHT, bg="#0d4f8a",
                                          font=("微软雅黑", 13),
                                          selectcolor="#0d4f8a",
                                          activebackground="#0d4f8a",
                                          activeforeground=COLOR_TEXT_LIGHT)
        self.notice_check.pack(side="left", padx=(0, 15))

        self.start_btn = tk.Button(bottom_frame, text="开始考试", font=("微软雅黑", 14, "bold"),
                                  bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=25, pady=6,
                                  cursor="hand2", state="disabled",
                                  command=self.start_exam)
        self.start_btn.pack(side="left")

        self.notice_check_var.trace_add("write", self._on_notice_check)

    def _on_notice_check(self, *args):
        self.start_btn.config(state="normal" if self.notice_check_var.get() else "disabled")

    def start_exam(self):
        if not self.notice_check_var.get():
            messagebox.showwarning("提示", "请先勾选确认已阅读考生须知！")
            return
        self.question_bank = load_question_bank()
        # 标记锁定题状态（过滤锁定题目）
        self._merge_unlocked_questions()
        self._build_exam_questions()
        if not self.questions:
            messagebox.showwarning("提示", "题库为空或配置数量为0，无法开始考试！")
            return
        # 重置作答状态，避免上次考试答案影响
        self.answers = {}
        self.submitted = set()
        self.marked = set()
        self.current_question = 0
        self._locked_question = None
        self._float_window = None
        self.exam_started = True
        self.timer_running = True
        self.time_remaining = self.exam_duration
        self.show_exam_interface()
        self._reset_prog_dir()
        self._setup_its_data()
        self._start_timer()

    # ==================== 考试/练习主界面（合并） ====================
    def show_exam_interface(self, mode="exam"):
        if mode == "practice":
            self._show_practice_interface_impl()
            return
        self._clear_window()
        self.root.deiconify()
        self.root.state('zoomed')

        top_bar = tk.Frame(self.root, bg=COLOR_SIDEBAR, height=50)
        top_bar.pack(fill="x")
        top_bar.pack_propagate(False)

        tk.Label(top_bar, text="山东省2027年春季高考技能测试网络技术类专业考试系统（考生练习用）",
                fg=COLOR_TEXT_LIGHT, bg=COLOR_SIDEBAR, font=("微软雅黑", 13, "bold")).pack(side="left", padx=15, pady=10)

        self.timer_var = tk.StringVar()
        self._update_timer_display()
        self.timer_label = tk.Label(top_bar, textvariable=self.timer_var, fg="#ffcc00",
                bg=COLOR_SIDEBAR, font=("Consolas", 16, "bold"))
        self.timer_label.pack(side="right", padx=15, pady=10)

        tk.Button(top_bar, text="交  卷", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_DANGER, fg=COLOR_WHITE, bd=0, padx=20, pady=4,
                 cursor="hand2", activebackground="#c62828",
                 command=self.submit_all).pack(side="right", padx=5, pady=8)

        main_frame = tk.Frame(self.root, bg=COLOR_BG_MAIN)
        main_frame.pack(fill="both", expand=True)

        self._create_sidebar(main_frame)
        self._create_question_area(main_frame)

        bottom_bar = tk.Frame(self.root, bg=COLOR_SIDEBAR, height=30)
        bottom_bar.pack(fill="x")
        bottom_bar.pack_propagate(False)
        self.status_var = tk.StringVar(value=f"考生：{self.exam_id}  |  快捷键：F4 上一题  F5 下一题")
        tk.Label(bottom_bar, textvariable=self.status_var, fg="#7fb3d8",
                bg=COLOR_SIDEBAR, font=("微软雅黑", 9)).pack(side="left", padx=15, pady=5)

        self.root.bind("<F4>", lambda e: self.prev_question())
        self.root.bind("<F5>", lambda e: self.next_question())
        self._display_question(0)

    def _create_sidebar(self, parent, mode="exam"):
        if mode == "practice":
            self._create_practice_sidebar_impl(parent)
            return
        sidebar = tk.Frame(parent, bg=COLOR_WHITE, width=200, bd=0,
                          highlightbackground="#b0c4de", highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="答题卡", font=("微软雅黑", 14, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(pady=(15, 5))

        legend_frame = tk.Frame(sidebar, bg=COLOR_WHITE)
        legend_frame.pack(fill="x", padx=10, pady=5)
        for text, color in [("■ 已答", COLOR_ANSWERED), ("■ 当前", COLOR_ANSWERING),
                            ("□ 未答", COLOR_UNANSWERED), ("★ 标记", COLOR_MARKED)]:
            tk.Label(legend_frame, text=text, fg=color, bg=COLOR_WHITE,
                    font=("微软雅黑", 10)).pack(anchor="w", pady=1)

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", padx=10, pady=5)

        self.question_buttons = []
        self.sidebar_collapsed = {}
        self.sidebar_group_frames = {}

        # 题型分组定义
        type_order = ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]
        group_titles = {
            "single_choice": "一、C语言单选题",
            "fill_blank": "二、C语言选择填空题",
            "programming": "三、C语言编程题",
            "web_design": "四、网页制作操作题",
            "network_device": "五、网络设备安装与调试",
            "graphic_design": "六、图形图像处理操作题"
        }
        # 将题目按题型分组
        grouped = {}
        for i, q in enumerate(self.questions):
            qtype = q["type"]
            if qtype not in grouped:
                grouped[qtype] = []
            grouped[qtype].append((i, q))

        # 带滚动条的答题卡
        scroll_canvas = tk.Canvas(sidebar, bg=COLOR_WHITE, highlightthickness=0, width=180)
        scrollbar = tk.Scrollbar(sidebar, orient="vertical", command=scroll_canvas.yview)
        groups_frame = tk.Frame(scroll_canvas, bg=COLOR_WHITE)
        groups_frame.bind("<Configure>", lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.create_window((0, 0), window=groups_frame, anchor="nw", width=175)
        scroll_canvas.configure(yscrollcommand=scrollbar.set)
        scroll_canvas.pack(fill="both", expand=True, padx=(10, 0), pady=5)
        scrollbar.pack(side="right", fill="y", pady=5)
        ExamSystem._setup_auto_scroll(scroll_canvas, scrollbar, side="right", fill="y", pady=5)

        for qtype in type_order:
            if qtype not in grouped:
                continue
            items = grouped[qtype]
            title = group_titles.get(qtype, qtype)
            self.sidebar_collapsed[qtype] = False

            # 分组标题行
            header = tk.Frame(groups_frame, bg="#f0f0f0", cursor="hand2")
            header.pack(fill="x", pady=(6, 0))
            toggle_callback = lambda e, t=qtype: self._toggle_sidebar_group(t)
            header.bind("<Button-1>", toggle_callback)

            title_lbl = tk.Label(header, text=title, font=("微软雅黑", 9, "bold"),
                    fg=COLOR_TEXT_DARK, bg="#f0f0f0")
            title_lbl.pack(side="left", padx=8, pady=4)
            title_lbl.bind("<Button-1>", toggle_callback)

            arrow_var = tk.StringVar(value="▼")
            arrow_label = tk.Label(header, textvariable=arrow_var, font=("微软雅黑", 9),
                                   fg=COLOR_TEXT_DARK, bg="#f0f0f0", width=2)
            arrow_label.pack(side="right", padx=5, pady=4)
            arrow_label.bind("<Button-1>", toggle_callback)

            # 按钮区域
            btn_grid = tk.Frame(groups_frame, bg=COLOR_WHITE)
            btn_grid.pack(fill="x", padx=5, pady=3)
            for i in range(4):
                btn_grid.columnconfigure(i, weight=1)

            for j, (orig_idx, q) in enumerate(items):
                row, col = j // 4, j % 4
                btn = tk.Button(btn_grid, text=str(orig_idx + 1), font=("微软雅黑", 8),
                               bg=COLOR_WHITE, fg=COLOR_UNANSWERED, bd=1,
                               relief="solid", cursor="hand2", width=4, height=1,
                               command=lambda idx=orig_idx: self._display_question(idx))
                btn.grid(row=row, column=col, padx=1, pady=1, sticky="ew")
                self.question_buttons.append(btn)

            self.sidebar_group_frames[qtype] = {
                "header": header,
                "btn_grid": btn_grid,
                "arrow_var": arrow_var,
                "arrow_label": arrow_label
            }

        self.mark_btn = tk.Button(sidebar, text="标记本题", font=("微软雅黑", 10),
                                 bg="#e1bee7", fg=COLOR_MARKED, bd=0, cursor="hand2",
                                 command=self.toggle_mark)
        self.mark_btn.pack(fill="x", padx=15, pady=(5, 15), ipady=5)

    def _toggle_sidebar_group(self, qtype):
        if qtype not in self.sidebar_group_frames:
            return
        info = self.sidebar_group_frames[qtype]
        self.sidebar_collapsed[qtype] = not self.sidebar_collapsed[qtype]
        if self.sidebar_collapsed[qtype]:
            info["btn_grid"].pack_forget()
            info["arrow_var"].set("▶")
        else:
            info["btn_grid"].pack(fill="x", padx=5, pady=3, after=info["header"])
            info["arrow_var"].set("▼")

    def _create_question_area(self, parent):
        self.question_frame = tk.Frame(parent, bg=COLOR_BG_MAIN)
        self.question_frame.pack(side="left", fill="both", expand=True)

    def _display_question(self, index, mode="exam"):
        if mode == "practice":
            self._display_practice_question_impl(index)
            return
        if not self.questions:
            return

        # 答题锁定：如果当前有题目处于答题状态，禁止切换
        locked_qid = self._locked_question
        if locked_qid is not None:
            # 检查是否尝试切换到锁定的题目本身
            if index < len(self.questions) and self.questions[index]["_qid"] == locked_qid:
                pass  # 允许回看锁定的题目，继续正常渲染
            else:
                # 如果悬浮窗还在，把它弹到最前面提醒用户
                if self._float_window is not None:
                    try:
                        self._float_window.deiconify()
                        self._float_window.lift()
                        self._float_window.focus_force()
                    except Exception:
                        pass
                return

        # 强制处理所有待处理事件，确保当前题目的答案已被保存
        self.root.update_idletasks()

        self.current_question = index
        q = self.questions[index]

        for widget in self.question_frame.winfo_children():
            widget.destroy()

        type_names = {"single_choice": "C语言 · 单选题",
                     "fill_blank": "C语言 · 选择填空题",
                     "programming": "C语言 · 编程题",
                     "web_design": "网页制作 · 操作题",
                     "network_device": "网络设备安装与调试",
                     "graphic_design": "图形图像处理 · 操作题"}
        type_label = type_names.get(q["type"], q.get("title", ""))

        info_frame = tk.Frame(self.question_frame, bg=COLOR_WHITE, bd=0,
                             highlightbackground="#b0c4de", highlightthickness=1)
        info_frame.pack(fill="x", padx=10, pady=(10, 5))
        tk.Label(info_frame, text=f"第 {index+1} 题 / 共 {len(self.questions)} 题",
                fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, font=("微软雅黑", 11, "bold")).pack(side="left", padx=15, pady=8)
        tk.Label(info_frame, text=f"({type_label})  |  分值：{q.get('score', 0)}分",
                fg=COLOR_ACCENT, bg=COLOR_WHITE, font=("微软雅黑", 11)).pack(side="left", padx=5, pady=8)

        # 带滚动条的内容区域
        content_outer = tk.Frame(self.question_frame, bg=COLOR_WHITE, bd=0,
                                highlightbackground="#b0c4de", highlightthickness=1)
        content_outer.pack(fill="both", expand=True, padx=10, pady=5)

        content_canvas = tk.Canvas(content_outer, bg=COLOR_WHITE, highlightthickness=0)
        content_scrollbar = tk.Scrollbar(content_outer, orient="vertical", command=content_canvas.yview)
        content_frame = tk.Frame(content_canvas, bg=COLOR_WHITE)

        canvas_window = content_canvas.create_window((0, 0), window=content_frame, anchor="nw")
        def _on_exam_content_configure(event):
            content_canvas.itemconfig(canvas_window, width=event.width - 20)
        content_outer.bind("<Configure>", _on_exam_content_configure)
        # 立即强制布局计算，在渲染内容前设置Canvas内窗口宽度
        # 防止 <Configure> 事件尚未触发导致内容区域宽度为0而显示空白
        content_outer.update_idletasks()
        _init_w = content_outer.winfo_width()
        if _init_w > 1:
            content_canvas.itemconfig(canvas_window, width=_init_w - 20)
        content_frame.bind("<Configure>", lambda e: content_canvas.configure(scrollregion=content_canvas.bbox("all")))
        content_canvas.configure(yscrollcommand=content_scrollbar.set)
        content_canvas.pack(side="left", fill="both", expand=True, padx=(5, 0), pady=5)
        content_scrollbar.pack(side="right", fill="y", pady=5)
        ExamSystem._setup_auto_scroll(content_canvas, content_scrollbar, side="right", fill="y", pady=5)

        if q["type"] == "single_choice":
            self._render_single_choice(content_frame, q)
        elif q["type"] == "fill_blank":
            self._render_fill_blank(content_frame, q)
        elif q["type"] == "programming":
            self._render_programming(content_frame, q)
        elif q["type"] == "web_design":
            self._render_web_design(content_frame, q)
        elif q["type"] == "network_device":
            self._render_network_device(content_frame, q)
        elif q["type"] == "graphic_design":
            self._render_graphic_design(content_frame, q)
        else:
            # 兜底：类型未知时显示错误提示，防止空白页
            print(f"[_display_question] 警告：未知题目类型 q['type']={q['type']!r}, q._type={q.get('_type', 'N/A')!r}", flush=True)
            tk.Label(content_frame, text=f"⚠ 题目类型错误：{q.get('type', '未定义')}", font=("微软雅黑", 14, "bold"),
                    fg=COLOR_DANGER, bg=COLOR_WHITE).pack(padx=20, pady=30)
            tk.Label(content_frame, text="请联系老师确认题目数据是否完整", font=("微软雅黑", 11),
                    fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(padx=20, pady=(0, 20))
            for key in ['type', '_type', 'content', 'id']:
                tk.Label(content_frame, text=f"  {key}: {str(q.get(key, '缺失'))[:100]}",
                        font=("Consolas", 9), fg="#888", bg=COLOR_WHITE, anchor="w").pack(fill="x", padx=30, pady=1)

        # 强制更新Canvas布局，防止内容显示空白
        content_outer.update_idletasks()
        content_frame.update_idletasks()

        def _fix_exam_canvas_after_render():
            """渲染后修复Canvas布局，确保内容正确显示"""
            try:
                cw = content_canvas.winfo_width()
                if cw > 1:
                    content_canvas.itemconfig(canvas_window, width=cw - 20)
                content_canvas.configure(scrollregion=content_canvas.bbox("all"))
            except tk.TclError:
                pass

        _fix_exam_canvas_after_render()
        content_canvas.after(50, _fix_exam_canvas_after_render)
        content_canvas.after(200, _fix_exam_canvas_after_render)

        nav_frame = tk.Frame(self.question_frame, bg=COLOR_BG_MAIN)
        nav_frame.pack(fill="x", padx=10, pady=(5, 10))
        tk.Button(nav_frame, text="◀ 上一题 (F4)", font=("微软雅黑", 11),
                 bg=COLOR_BTN, fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                 cursor="hand2", command=self.prev_question).pack(side="left")
        tk.Button(nav_frame, text="下一题 (F5) ▶", font=("微软雅黑", 11),
                 bg=COLOR_BTN, fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                 cursor="hand2", command=self.next_question).pack(side="right")

        self._update_sidebar_buttons()
        # 局域网模式：上报状态
        self._send_lan_status()

    # ==================== Text 控件高度自适应 ====================

    def _auto_height_text(self, ct, content):
        """让 Text 控件根据 wrap 后的实际显示行数自动调整高度，避免内容溢出"""
        # 固定字符宽度，让 wrap='word' 在~50字符处自动换行
        ct.config(width=50)
        ct.insert("1.0", content)
        ct.config(state="disabled")
        self.root.update_idle()
        try:
            display_lines = int(ct.count("1.0", "end-1c", "displaylines")[0])
            # 限制最大高度，防止 Canvas 内 displaylines 返回异常值导致大片空白
            ct.config(height=max(min(display_lines, 25), 1))
        except Exception:
            pass

    # ==================== 题干图片显示 ====================
    def _show_question_image(self, parent, q):
        img_rel = q.get("image", "")
        if not img_rel:
            return
        # 相对路径自动拼 EXAM_DIR 为绝对路径
        img_path = img_rel if os.path.isabs(img_rel) else os.path.join(EXAM_DIR, img_rel)
        if not os.path.exists(img_path):
            return
        try:
            pil_img = Image.open(img_path)
            orig_w, orig_h = pil_img.size
            # 限制最大宽度780，按比例缩放
            w, h = orig_w, orig_h
            max_w = 780
            if w > max_w:
                ratio = max_w / w
                w, h = int(w * ratio), int(h * ratio)
            pil_img = pil_img.resize((w, h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(pil_img)
            lbl = tk.Label(parent, image=photo, bg=COLOR_WHITE, bd=1, relief="solid",
                           cursor="hand2")
            lbl.image = photo
            lbl._pil_ref = pil_img
            lbl._img_path = img_path
            lbl.pack(padx=20, pady=(15, 0))
            # 点击查看原图（新窗口显示全尺寸图片）
            lbl.bind("<Button-1>", lambda e, p=img_path: self._show_image_fullscreen(p))
        except Exception:
            pass

    def _make_image_widget(self, parent, q):
        """创建题干图片控件（不pack，返回Label或None）"""
        img_rel = q.get("image", "")
        if not img_rel:
            return None
        img_path = img_rel if os.path.isabs(img_rel) else os.path.join(EXAM_DIR, img_rel)
        if not os.path.exists(img_path):
            return None
        try:
            pil_img = Image.open(img_path)
            orig_w, orig_h = pil_img.size
            w, h = orig_w, orig_h
            max_w = 780
            ratio = 1.0
            if w > max_w:
                ratio = max_w / w
            if ratio < 1.0:
                w, h = int(w * ratio), int(h * ratio)
            pil_img = pil_img.resize((w, h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(pil_img)
            lbl = tk.Label(parent, image=photo, bg=COLOR_WHITE, bd=1, relief="solid",
                           cursor="hand2")
            lbl.image = photo
            lbl._pil_ref = pil_img
            lbl._img_path = img_path
            lbl.bind("<Button-1>", lambda e, p=img_path: self._show_image_fullscreen(p))
            return lbl
        except Exception:
            return None

    def _show_image_fullscreen(self, img_path):
        """在新窗口中显示图片，支持点击切换原始/自适应尺寸，超出窗口时显示滚动条"""
        try:
            pil_img = Image.open(img_path)
        except Exception:
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("查看图片")
        dlg.configure(bg="#1a1a1a")
        dlg.transient(self.root)
        dlg.grab_set()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()

        # 外层层：底部提示条
        bottom_bar = tk.Frame(dlg, bg="#1a1a1a", height=28)
        bottom_bar.pack(side="bottom", fill="x")
        tk.Label(bottom_bar, text="点击图片切换原始尺寸/自适应  ｜  鼠标滚轮滚动查看大图  ｜  关闭窗口退出",
                font=("微软雅黑", 9), fg="#888888", bg="#1a1a1a").pack(pady=4)

        # Canvas + 滚动条
        canvas = tk.Canvas(dlg, bg="#1a1a1a", highlightthickness=0)
        hbar = tk.Scrollbar(dlg, orient="horizontal", command=canvas.xview)
        vbar = tk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")

        # 在 Canvas 上显示图片的 Label
        img_item = tk.Label(canvas, bg="#1a1a1a", cursor="hand2")
        window_item = canvas.create_window((0, 0), window=img_item, anchor="nw")

        # 状态
        orig_w, orig_h = pil_img.size
        max_w = int(sw * 0.85)
        max_h = int(sh * 0.85)
        is_zoomed = [False]  # 用列表包装以便在嵌套函数中修改

        def apply_scale(zoomed: bool):
            """应用缩放：zoomed=True 显示原始尺寸，False 显示自适应"""
            if zoomed:
                dw, dh = orig_w, orig_h
            else:
                ratio = min(max_w / orig_w, max_h / orig_h, 1.0)
                dw, dh = int(orig_w * ratio), int(orig_h * ratio)
            photo = ImageTk.PhotoImage(pil_img.resize((dw, dh), Image.Resampling.LANCZOS))
            img_item.configure(image=photo)
            img_item.image = photo
            img_item._dw = dw
            img_item._dh = dh
            is_zoomed[0] = zoomed

            # 更新 Canvas 滚动区域
            canvas.configure(scrollregion=(0, 0, dw, dh))
            # 窗口大小：不超过屏幕 90%，也不小于 400x300
            win_w = min(dw + 40, int(sw * 0.9))
            win_h = min(dh + 28 + 40, int(sh * 0.9))
            win_w = max(win_w, 400)
            win_h = max(win_h, 300)
            dlg.geometry(f"{win_w}x{win_h}")
            # 居中
            dlg.update_idletasks()
            dx = (sw - win_w) // 2
            dy = (sh - win_h) // 2
            dlg.geometry(f"{win_w}x{win_h}+{dx}+{dy}")

        def on_img_click(event=None):
            apply_scale(not is_zoomed[0])

        img_item.bind("<Button-1>", on_img_click)

        # 鼠标滚轮滚动（跨平台）
        def on_mouse_wheel(event):
            # Windows
            if event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
                canvas.yview_scroll(1, "units")
            elif event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
                canvas.yview_scroll(-1, "units")
        dlg.bind("<MouseWheel>", on_mouse_wheel)   # Windows
        dlg.bind("<Button-4>", on_mouse_wheel)     # Linux 上滚
        dlg.bind("<Button-5>", on_mouse_wheel)     # Linux 下滚
        # 让 Canvas 获得焦点以接收滚轮事件
        canvas.focus_set()

        # Shift+滚轮 = 横向滚动
        def on_shift_wheel(event):
            if event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
                canvas.xview_scroll(1, "units")
            elif event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
                canvas.xview_scroll(-1, "units")
        dlg.bind("<Shift-MouseWheel>", on_shift_wheel)
        # 用键盘方向键也能滚动
        dlg.bind("<Up>",    lambda e: canvas.yview_scroll(-1, "units"))
        dlg.bind("<Down>",  lambda e: canvas.yview_scroll( 1, "units"))
        dlg.bind("<Left>",  lambda e: canvas.xview_scroll(-1, "units"))
        dlg.bind("<Right>", lambda e: canvas.xview_scroll( 1, "units"))

        # 初始显示自适应尺寸
        apply_scale(False)

    # ==================== 单选题渲染 ====================
    def _render_single_choice(self, parent, q, mode="exam", revealed=False):
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 题干
        _lbl = tk.Label(parent, text=q["content"],
                       font=("微软雅黑", 13), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        # 如果题目包含代码块，添加复制代码按钮
        content = q["content"]
        if self._has_code_block(content):
            btn_row = tk.Frame(parent, bg=COLOR_WHITE)
            btn_row.pack(fill="x", padx=20, pady=(5, 0))
            code_text = self._extract_code(content)
            tk.Button(btn_row, text="📋 复制代码", font=("微软雅黑", 10),
                     bg="#e3f2fd", fg=COLOR_ACCENT, bd=1, relief="solid",
                     cursor="hand2", padx=10, pady=3,
                     command=lambda c=code_text: self._copy_code(c)).pack(side="left")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)

        qid = q["_qid"]
        self.choice_var = tk.IntVar(value=-1)
        if qid in self.answers:
            self.choice_var.set(self.answers[qid])

        opts = tk.Frame(parent, bg=COLOR_WHITE)
        opts.pack(fill="both", expand=True, padx=30, pady=15)
        for i, opt in enumerate(q["options"]):
            tk.Radiobutton(opts, text=opt, variable=self.choice_var, value=i,
                          font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                          activebackground=COLOR_WHITE, cursor="hand2",
                          anchor="w", padx=10, pady=8,
                          command=lambda idx=i, qid=qid: self._save_answer(qid, idx)).pack(fill="x", pady=3)

    def _save_answer(self, qid, value):
        self.answers[qid] = value
        self._update_sidebar_buttons()
        # 局域网模式：上报状态
        self._send_lan_status()
        self._sync_answered_count()

    def _sync_answered_count(self):
        """同步已答题数到局域网客户端（心跳中实时上报）。"""
        if self.lan_mode and self.network_client:
            self.network_client._answered_counter = sum(
                1 for q in self.questions if self._is_answered(q)
            )

    # ==================== 填空题渲染（多空） ====================
    def _render_fill_blank(self, parent, q, mode="exam", revealed=False):
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 填空题题干 + 代码显示
        display_text = q.get("content", "")
        if q.get("code"):
            display_text += "\n\n" + q["code"]
        # 题干 - 使用 Label + wraplength 精确控制每行字符数
        _lbl = tk.Label(parent, text=display_text,
                       font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        # 复制代码按钮（检查单独的 code 字段或内容中的代码块）
        has_code = q.get("code") or self._has_code_block(q.get("content", ""))
        if has_code:
            code_text = q.get("code") or self._extract_code(q.get("content", ""))
            btn_row = tk.Frame(parent, bg=COLOR_WHITE)
            btn_row.pack(fill="x", padx=20, pady=(0, 5))
            tk.Button(btn_row, text="📋 复制代码", font=("微软雅黑", 10),
                     bg="#e3f2fd", fg=COLOR_ACCENT, bd=1, relief="solid",
                     cursor="hand2", padx=10, pady=3,
                     command=lambda c=code_text: self._copy_code(c)).pack(side="left")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)

        # 左右分栏容器
        shared_opts = q.get("shared_options", [])
        num_blanks = len(q.get("blanks", []))
        # 备选项字母：按实际备选项数量生成（A/B/C...），最少2个
        all_letters = [chr(ord('A') + i) for i in range(26)]
        letters = all_letters[:max(len(shared_opts), 2)]
        qid = q["_qid"]
        if qid not in self.answers:
            self.answers[qid] = {}
        self.blank_vars = {}

        split_frame = tk.Frame(parent, bg=COLOR_WHITE)
        split_frame.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        # 左侧：备选项
        # 左侧：备选项（固定宽度、固定高度，不扩展）
        left_frame = tk.Frame(split_frame, bg=COLOR_WHITE, width=400, height=350)
        left_frame.pack(side="left", fill="y", expand=False, padx=(0, 5))
        left_frame.pack_propagate(False)

        opts_frame = tk.Frame(left_frame, bg="#f5f5f5", bd=1, relief="solid")
        opts_frame.pack(fill="both", expand=True)
        opts_header = tk.Frame(opts_frame, bg="#f5f5f5")
        opts_header.pack(fill="x", padx=12, pady=(6, 4))
        tk.Label(opts_header, text="【备选项】", font=("微软雅黑", 11, "bold"),
                fg=COLOR_TEXT_DARK, bg="#f5f5f5").pack(side="left")
        opts_text_lines = []
        for i, letter in enumerate(letters):
            opt_text = shared_opts[i] if i < len(shared_opts) else ""
            if len(opt_text) > 30:
                opt_text = opt_text[:30] + "…"
            opts_text_lines.append(f"{letter}. {opt_text}")
        opts_copy_text = "\n".join(opts_text_lines)
        tk.Button(opts_header, text="📋 复制备选项", font=("微软雅黑", 9),
                 bg="#e3f2fd", fg=COLOR_ACCENT, bd=1, relief="solid",
                 cursor="hand2", padx=8, pady=2,
                 command=lambda t=opts_copy_text: self._copy_code(t)).pack(side="right")

        for i, letter in enumerate(letters):
            opt_text = shared_opts[i] if i < len(shared_opts) else ""
            if len(opt_text) > 30:
                opt_text = opt_text[:30] + "…"
            tk.Label(opts_frame, text=f"  {letter}. {opt_text}", font=("微软雅黑", 10),
                    fg=COLOR_TEXT_DARK, bg="#f5f5f5", anchor="w").pack(fill="x", padx=20, pady=0)

        # 右侧分隔线
        ttk.Separator(split_frame, orient="vertical").pack(side="left", fill="y", padx=5)

        # 右侧：答题区域
        right_frame = tk.Frame(split_frame, bg=COLOR_WHITE)
        right_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))

        tk.Label(right_frame, text="【答题区域】共有 {} 个空，请分别选择正确答案：".format(num_blanks),
                font=("微软雅黑", 12, "bold"), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(anchor="w", pady=(0, 10))

        for blank in q.get("blanks", []):
            bf = tk.Frame(right_frame, bg=COLOR_WHITE)
            bf.pack(fill="x", pady=6)

            tk.Label(bf, text=f"{blank['label']}：", font=("微软雅黑", 12, "bold"),
                    fg=COLOR_ACCENT, bg=COLOR_WHITE, width=6, anchor="e").pack(side="left", padx=(0, 10))

            var = tk.StringVar()
            lbl = blank["label"]
            if lbl in self.answers.get(qid, {}):
                var.set(self.answers[qid][lbl])

            combo = ttk.Combobox(bf, textvariable=var, values=letters,
                                state="readonly", font=("微软雅黑", 11), width=8)
            combo.pack(side="left")
            combo.bind("<<ComboboxSelected>>",
                      lambda e, qid=qid, lbl=lbl, v=var: self._save_fill_blank(qid, lbl, v))
            self.blank_vars[lbl] = var

            if lbl in self.answers.get(qid, {}):
                val_label = tk.Label(bf, text=f"  ✓已选", font=("微软雅黑", 9),
                                    fg=COLOR_SUCCESS, bg=COLOR_WHITE)
                val_label.pack(side="left", padx=5)

    def _save_fill_blank(self, qid, label, var):
        if qid not in self.answers:
            self.answers[qid] = {}
        self.answers[qid][label] = var.get()
        self._update_sidebar_buttons()
        # 局域网模式：上报状态
        self._send_lan_status()
        self._sync_answered_count()

    def _save_practice_selection(self, qid, answer):
        """刷题模式：仅保存单选题选项，不判题（判题由「参考答案」按钮手动触发）"""
        self.answers[qid] = answer
        self._update_sidebar_buttons()

    def _save_and_show_answer(self, qid, answer, correct, options, current_index):
        """保存答案并立即显示结果，如果回答正确则自动跳转到下一题"""
        # 保存答案
        self.answers[qid] = answer

        # 更新统计数据
        if answer == correct:
            self.practice_stats["correct"] = self.practice_stats.get("correct", 0) + 1
            self.wrong_questions.discard(qid)
        else:
            self.practice_stats["wrong"] = self.practice_stats.get("wrong", 0) + 1
            self.wrong_questions.add(qid)

        # 标记为已显示答案
        self.show_revealed.add(qid)

        # 更新统计显示
        self._update_practice_stats_display()

        # ★ 关键修复：用 after(0) 延迟渲染到事件队列末尾
        # 这样 Radiobutton 的点击事件完整处理完后才重建 UI，避免在事件回调中 destroy 导致布局错乱
        def _do_refresh():
            # 刷新当前题目显示（显示答案）
            self._display_practice_question(current_index)

            # 如果回答正确，延迟1秒后跳转到下一题
            if answer == correct and current_index < len(self.questions) - 1:
                def _delayed_jump():
                    if self.current_question == current_index:
                        self._display_practice_question(current_index + 1)
                self.root.after(1000, _delayed_jump)

        self.root.after(0, _do_refresh)

    # ==================== 编程题渲染 ====================
    def _render_programming(self, parent, q, mode="exam", revealed=False):
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 题干 - 使用 Label + wraplength 精确控制每行字符数
        from tkinter import Label
        _lbl = Label(parent, text=q["content"],
                      font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                      wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)

        btn_frame = tk.Frame(parent, bg=COLOR_WHITE)
        btn_frame.pack(fill="x", padx=20, pady=15)

        prog_folder = self._prog_folder_map.get(q["_qid"], "C")
        tk.Button(btn_frame, text="📂 答题（打开考试文件夹）", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2", command=lambda q=q: self._start_answer(q, os.path.join(r"C:\ITSData", prog_folder))).pack(side="left", padx=(0, 15))
        tk.Button(btn_frame, text="✅ 提交本题", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2", command=lambda: self._submit_programming(q["_qid"])).pack(side="left")

        tip_frame = tk.Frame(parent, bg="#fff3e0", bd=0)
        tip_frame.pack(fill="x", padx=20, pady=(0, 15))
        for tip in ["1. 点击答题按钮，系统自动打开考试文件夹",
                    "2. 进入 prog.c 文件作答",
                    "3. 作答完成后保存文件并关闭相关软件",
                    "4. 点击提交本题按钮",
                    "5. 严禁更改已有代码，仅限编程区域内编写"]:
            tk.Label(tip_frame, text=tip, font=("微软雅黑", 10), fg="#e65100",
                    bg="#fff3e0", anchor="w").pack(fill="x", padx=15, pady=2)

    # ==================== 网页制作题渲染 ====================
    def _render_web_design(self, parent, q, mode="exam", revealed=False):
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 题干
        _lbl = tk.Label(parent, text=q["content"],
                       font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)

        btn_frame = tk.Frame(parent, bg=COLOR_WHITE)
        btn_frame.pack(fill="x", padx=20, pady=15)

        dw_folder = self._dw_folder_map.get(q["_qid"], "DW")
        tk.Button(btn_frame, text="📂 答题（打开考试文件夹）", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2", command=lambda q=q, df=dw_folder: self._start_answer(q, os.path.join(r"C:\ITSData", df))).pack(side="left", padx=(0, 15))
        tk.Button(btn_frame, text="✅ 提交本题", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2", command=lambda: self._submit_web_design(q["_qid"])).pack(side="left")

        tip_frame = tk.Frame(parent, bg="#fff3e0", bd=0)
        tip_frame.pack(fill="x", padx=20, pady=(0, 15))
        for tip in ["1. 点击答题按钮，系统自动打开考试文件夹",
                    "2. 以 website 为站点文件夹，index.html 为作答文件",
                    "3. 在 website 内创建 image 和 css 文件夹",
                    "4. 完成网页制作并保存后关闭相关软件",
                    "5. 点击提交本题按钮",
                    "6. 考试结果以 website 文件夹内内容为准"]:
            tk.Label(tip_frame, text=tip, font=("微软雅黑", 10), fg="#e65100",
                    bg="#fff3e0", anchor="w").pack(fill="x", padx=15, pady=2)

    # ==================== 网络设备安装与调试渲染 ====================
    def _render_network_device(self, parent, q, mode="exam", revealed=False):
        """网络设备安装与调试题：两列布局——左图右答题区"""
        # === 两列主容器 ===
        main_frame = tk.Frame(parent, bg=COLOR_WHITE)
        main_frame.pack(fill="both", expand=True, padx=15, pady=10)

        # === 左列：图片 ===
        left_col = tk.Frame(main_frame, bg=COLOR_WHITE)
        left_col.pack(side="left", fill="y", padx=(0, 15))

        img_widget = self._make_image_widget(left_col, q)
        if img_widget:
            img_widget.pack(pady=(5, 10))

        # === 右列：题目内容 + 答题区 ===
        right_col = tk.Frame(main_frame, bg=COLOR_WHITE)
        right_col.pack(side="left", fill="both", expand=True)

        # 题目来源
        if q.get("source"):
            tk.Label(right_col, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", pady=(0, 5))
        # 题干
        _lbl = tk.Label(right_col, text=q.get("content", ""),
                       font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=550, justify="left", anchor="w")
        _lbl.pack(fill="x", pady=(0, 10))
        ttk.Separator(right_col, orient="horizontal").pack(fill="x")

        qid = q["_qid"]
        if qid not in self.answers:
            self.answers[qid] = {}

        text_fields = q.get("text_fields", [])
        fields_frame = tk.Frame(right_col, bg=COLOR_WHITE)
        fields_frame.pack(fill="both", expand=True, pady=(10, 0))

        tk.Label(fields_frame, text="【答题区域】请在下方各文本框中粘贴命令输出结果：",
                font=("微软雅黑", 12, "bold"), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(anchor="w", pady=(0, 10))

        for tf in text_fields:
            tf_frame = tk.Frame(fields_frame, bg=COLOR_WHITE)
            tf_frame.pack(fill="x", pady=(0, 8))

            label_text = tf.get("label", "")
            tk.Label(tf_frame, text=f"{label_text}：", font=("微软雅黑", 11, "bold"),
                    fg=COLOR_ACCENT, bg=COLOR_WHITE, anchor="w").pack(fill="x", pady=(0, 3))

            # 带边框和滚动条的文本框
            text_frame = tk.Frame(tf_frame, bg="#b0c4de", bd=1)
            text_frame.pack(fill="x")

            text_widget = tk.Text(text_frame, font=("Consolas", 11), bg="#fafafa", fg=COLOR_TEXT_DARK,
                                 wrap="word", bd=0, padx=8, pady=5, height=7)
            text_scrollbar = tk.Scrollbar(text_frame, orient="vertical", command=text_widget.yview)
            text_widget.configure(yscrollcommand=text_scrollbar.set)
            text_widget.pack(side="left", fill="both", expand=True)
            text_scrollbar.pack(side="right", fill="y")

            # 恢复之前保存的内容
            saved = self.answers[qid].get(label_text, "")
            if saved:
                text_widget.insert("1.0", saved)
            # 重置 modified 标志，防止恢复内容时触发保存事件
            text_widget.edit_modified(False)

            # 绑定内容变更自动保存
            def make_callback(widget, lbl, qid):
                def on_modified(event=None):
                    # 只在真正修改时保存（排除恢复内容时的触发）
                    if widget.edit_modified():
                        content = widget.get("1.0", "end-1c")
                        if qid not in self.answers:
                            self.answers[qid] = {}
                        self.answers[qid][lbl] = content
                        self._update_sidebar_buttons()
                        self._sync_answered_count()
                    widget.edit_modified(False)
                return on_modified

            text_widget.bind("<<Modified>>", make_callback(text_widget, label_text, qid))

    # ==================== 图形图像处理渲染 ====================
    def _render_graphic_design(self, parent, q, mode="exam", revealed=False):
        """图形图像处理操作题：参考 web_design 实现"""
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 题干
        _lbl = tk.Label(parent, text=q.get("content", ""),
                       font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)

        btn_frame = tk.Frame(parent, bg=COLOR_WHITE)
        btn_frame.pack(fill="x", padx=20, pady=15)

        ps_folder = self._ps_folder_map.get(q["_qid"], "PS")
        tk.Button(btn_frame, text="📂 答题（打开考试文件夹）", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2", command=lambda q=q, pf=ps_folder: self._start_answer(q, os.path.join(r"C:\ITSData", pf))).pack(side="left", padx=(0, 15))
        tk.Button(btn_frame, text="✅ 提交本题", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2", command=lambda: self._submit_graphic_design(q["_qid"])).pack(side="left")

        tip_frame = tk.Frame(parent, bg="#fff3e0", bd=0)
        tip_frame.pack(fill="x", padx=20, pady=(0, 15))
        for tip in ["1. 点击答题按钮，系统自动打开考试文件夹",
                    "2. 在 PS 文件夹内完成图形图像处理操作",
                    "3. 作答完成后保存文件并关闭相关软件",
                    "4. 点击提交本题按钮",
                    "5. 考试结果以 PS 文件夹内内容为准"]:
            tk.Label(tip_frame, text=tip, font=("微软雅黑", 10), fg="#e65100",
                    bg="#fff3e0", anchor="w").pack(fill="x", padx=15, pady=2)

    def _submit_graphic_design(self, qid):
        """图形图像处理提交：检查 ITSData/PS_n 下是否有文件"""
        ps_folder = self._ps_folder_map.get(qid, "PS")
        ps_path = os.path.join(r"C:\ITSData", ps_folder)
        if os.path.exists(ps_path) and os.listdir(ps_path):
            self.submitted.add(qid)
            self.answers[qid] = "已提交"
            try:
                self._update_sidebar_buttons()
            except Exception:
                pass  # 刷题模式下 sidebar 结构不同，忽略更新失败
            self._unlock_question()
            messagebox.showinfo("提交成功", "图形图像处理题已提交！以最后一次提交为准。")
        else:
            messagebox.showwarning("提示", f"{ps_folder} 文件夹为空，请先完成作答！")

    # ==================== 工具方法 ====================
    def _apply_folder_config(self):
        """从 exam_config.json 读取文件夹路径，覆盖模块级默认值"""
        try:
            global PROG_DIR, WEBSITE_DIR, GRAPHIC_DIR
            cfg = self.question_config
            if cfg.get("prog_dir"):
                PROG_DIR = cfg["prog_dir"]
            if cfg.get("website_dir"):
                WEBSITE_DIR = cfg["website_dir"]
            if cfg.get("graphic_dir"):
                GRAPHIC_DIR = cfg["graphic_dir"]
        except Exception as e:
            import traceback
            messagebox.showerror("文件夹配置错误",
                f"应用文件夹配置时发生异常：\n{str(e)}\n\n详细信息：\n{traceback.format_exc()}")

    def _browse_folder(self, key, var):
        """打开文件夹选择对话框"""
        path = filedialog.askdirectory(title="选择考试文件夹", initialdir=var.get())
        if path:
            var.set(path)

    def _reset_prog_dir(self):
        """打开编程题文件夹前，用模板重置 prog.c"""
        template = os.path.join(EXAM_DIR, "template_prog.c")
        if not os.path.exists(PROG_DIR):
            os.makedirs(PROG_DIR)
        if os.path.exists(template):
            shutil.copy2(template, os.path.join(PROG_DIR, "prog.c"))
        else:
            # 无模板时生成一个空的 prog.c
            with open(os.path.join(PROG_DIR, "prog.c"), "w", encoding="gbk") as f:
                f.write('#include <stdio.h>\nmain() {\n    /*答题区域   春考网络一点通 */\n    \n    \n    \n    /*答题区域   网络技术潘老师 */\n    \n}\n')

    def _init_prog_folder_map(self):
        """填充编程题/DW/PS 文件夹映射表（不创建任何磁盘文件夹）

        供刷题模式启动时调用，避免一次性预创建所有 ITSData 子文件夹。
        实际文件夹在用户点击「打开考试文件夹」时按需创建。
        """
        self._prog_folder_map = {}
        self._dw_folder_map = {}
        self._ps_folder_map = {}

        # 编程题 → C / C1 / C2 ...
        prog_questions = [q for q in self.questions
                          if q.get("type") == "programming" or q.get("_type") == "programming"]
        prog_count = len(prog_questions)
        for pi, pq in enumerate(prog_questions):
            folder_name = "C" if prog_count == 1 else f"C{pi + 1}"
            self._prog_folder_map[pq["_qid"]] = folder_name

        # 网页制作题 → DW / DW1 / DW2 ...
        dw_questions = [q for q in self.questions
                         if q.get("type") == "web_design" or q.get("_type") == "web_design"]
        dw_count = len(dw_questions)
        for pi, pq in enumerate(dw_questions):
            folder_name = "DW" if dw_count == 1 else f"DW{pi + 1}"
            self._dw_folder_map[pq["_qid"]] = folder_name

        # 图形图像题 → PS / PS1 / PS2 ...
        ps_questions = [q for q in self.questions
                         if q.get("type") == "graphic_design" or q.get("_type") == "graphic_design"]
        ps_count = len(ps_questions)
        for pi, pq in enumerate(ps_questions):
            folder_name = "PS" if ps_count == 1 else f"PS{pi + 1}"
            self._ps_folder_map[pq["_qid"]] = folder_name

    def _prepare_its_data_for_exam(self):
        """准备 C:\\ITSData：同一场考试保留现场，新考试才清理旧环境。"""
        ITS_DIR = r"C:\ITSData"
        os.makedirs(ITS_DIR, exist_ok=True)
        session_id = getattr(self, "_current_its_session_id", "")
        if not session_id:
            session_id = f"local-{self.exam_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            self._current_its_session_id = session_id

        marker_path = os.path.join(ITS_DIR, ".exam_session.json")
        old_session = ""
        if os.path.exists(marker_path):
            try:
                with open(marker_path, "r", encoding="utf-8") as f:
                    old_session = json.load(f).get("session_id", "")
            except Exception:
                old_session = ""

        preserve_existing = old_session == session_id
        if not preserve_existing:
            for item in os.listdir(ITS_DIR):
                item_path = os.path.join(ITS_DIR, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as e:
                    print(f"[ITSData] 新考试清理旧文件失败 {item_path}: {e}")

        try:
            with open(marker_path, "w", encoding="utf-8") as f:
                json.dump({
                    "session_id": session_id,
                    "exam_id": self.exam_id,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, f, ensure_ascii=False)
        except Exception:
            pass
        return preserve_existing

    def _setup_its_data(self):
        """考试开始时创建 C:\\ITSData 文件夹结构

        根据考试题目配置生成对应的子文件夹：
        - programming → C:\\ITSData\\C\\ (含 prog.c)
        - web_design  → C:\\ITSData\\DW\\ (含题目文件夹内容)
        - graphic_design → C:\\ITSData\\PS\\ (含题目文件夹内容)
        """
        import shutil

        ITS_DIR = r"C:\ITSData"
        config = getattr(self, "question_config", {}) or {}
        preserve_existing = self._prepare_its_data_for_exam()

        # 判断哪些题型需要创建文件夹
        has_prog = config.get("programming", 0) > 0 or any(
            q.get("type") == "programming" or q.get("_type") == "programming"
            for q in self.questions)
        has_web = config.get("web_design", 0) > 0 or any(
            q.get("type") == "web_design" or q.get("_type") == "web_design"
            for q in self.questions)
        has_graphic = config.get("graphic_design", 0) > 0 or any(
            q.get("type") == "graphic_design" or q.get("_type") == "graphic_design"
            for q in self.questions)

        # 编程题 → C / C1 / C2 / ... 文件夹，复制 prog.c
        # 存储 qid → 文件夹名称 的映射，供答题/提交按钮使用
        self._prog_folder_map = {}
        if has_prog:
            prog_questions = [q for q in self.questions
                              if q.get("type") == "programming" or q.get("_type") == "programming"]
            prog_count = len(prog_questions)
            for pi, pq in enumerate(prog_questions):
                folder_name = "C" if prog_count == 1 else f"C{pi + 1}"
                target_dir = os.path.join(ITS_DIR, folder_name)
                os.makedirs(target_dir, exist_ok=True)
                self._prog_folder_map[pq["_qid"]] = folder_name
                prog_c_target = os.path.join(target_dir, "prog.c")
                if preserve_existing and os.path.exists(prog_c_target):
                    continue
                # 局域网模式：优先使用教师端发来的模板
                lan_folder = getattr(self, "_lan_received_folders", {}).get(pq.get("_teacher_qid", pq["_qid"]))
                if lan_folder and os.path.isfile(os.path.join(lan_folder, "prog.c")):
                    shutil.copy2(os.path.join(lan_folder, "prog.c"), prog_c_target)
                elif os.path.exists(r"C:\Users\Administrator\Desktop\C语言提交文件\prog.c"):
                    shutil.copy2(r"C:\Users\Administrator\Desktop\C语言提交文件\prog.c", prog_c_target)
                else:
                    template = os.path.join(EXAM_DIR, "template_prog.c")
                    if os.path.exists(template):
                        shutil.copy2(template, prog_c_target)
                    else:
                        with open(prog_c_target, "w", encoding="gbk") as f:
                            f.write('#include <stdio.h>\nmain() {\n    /*答题区域   春考网络一点通 */\n    \n    \n    \n    /*答题区域   网络技术潘老师 */\n    \n}\n')

        # 网页制作 → DW / DW1 / DW2 ... 文件夹
        self._dw_folder_map = {}
        if has_web:
            website_questions = [q for q in self.questions
                                 if q.get("type") == "web_design" or q.get("_type") == "web_design"]
            dw_count = len(website_questions)
            for pi, wq in enumerate(website_questions):
                folder_name = "DW" if dw_count == 1 else f"DW{pi + 1}"
                self._dw_folder_map[wq["_qid"]] = folder_name
                target_dir = os.path.join(ITS_DIR, folder_name)
                os.makedirs(target_dir, exist_ok=True)
                if preserve_existing and os.listdir(target_dir):
                    continue
                # 局域网模式：优先使用教师端发来的文件夹
                lan_folder = getattr(self, "_lan_received_folders", {}).get(wq.get("_teacher_qid", wq["_qid"]))
                if lan_folder and os.path.isdir(lan_folder):
                    self._copy_folder_contents(lan_folder, target_dir)
                else:
                    source_folder = _resolve_path(wq.get("folder_path", "")) or WEBSITE_DIR
                    self._copy_folder_contents(source_folder, target_dir)

        # 图形图像 → PS / PS1 / PS2 ... 文件夹
        self._ps_folder_map = {}
        if has_graphic:
            graphic_questions = [q for q in self.questions
                                 if q.get("type") == "graphic_design" or q.get("_type") == "graphic_design"]
            ps_count = len(graphic_questions)
            for pi, gq in enumerate(graphic_questions):
                folder_name = "PS" if ps_count == 1 else f"PS{pi + 1}"
                self._ps_folder_map[gq["_qid"]] = folder_name
                target_dir = os.path.join(ITS_DIR, folder_name)
                os.makedirs(target_dir, exist_ok=True)
                if preserve_existing and os.listdir(target_dir):
                    continue
                # 局域网模式：优先使用教师端发来的文件夹
                lan_folder = getattr(self, "_lan_received_folders", {}).get(gq.get("_teacher_qid", gq["_qid"]))
                if lan_folder and os.path.isdir(lan_folder):
                    self._copy_folder_contents(lan_folder, target_dir)
                else:
                    source_folder = _resolve_path(gq.get("folder_path", "")) or GRAPHIC_DIR
                    self._copy_folder_contents(source_folder, target_dir)

    def _archive_and_clear_its_data(self):
        """交卷时：将 C:\\ITSData 内容归档到考试系统目录，然后清空原文件夹"""
        import shutil
        ITS_DIR = r"C:\ITSData"
        if not os.path.isdir(ITS_DIR):
            return

        # 目标归档路径：exam_files/ITSData_<准考证号>/
        archive_dir = os.path.join(EXAM_FILES_DIR, f"ITSData_{self.exam_id}")
        # 如果已存在（重复交卷），先删除旧的
        if os.path.exists(archive_dir):
            shutil.rmtree(archive_dir)

        try:
            shutil.copytree(ITS_DIR, archive_dir)
        except Exception as e:
            print(f"[ITSData] 归档失败: {e}")
            return

        # 清空原 ITSData 文件夹内容
        for item in os.listdir(ITS_DIR):
            item_path = os.path.join(ITS_DIR, item)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
            except Exception as e:
                print(f"[ITSData] 清空失败 {item_path}: {e}")

    def _copy_folder_contents(self, source_folder, target_dir):
        """复制源文件夹的所有内容到目标文件夹"""
        import shutil
        if not source_folder or not os.path.isdir(source_folder):
            return
        for item in os.listdir(source_folder):
            src_item = os.path.join(source_folder, item)
            dst_item = os.path.join(target_dir, item)
            try:
                if os.path.isfile(src_item):
                    shutil.copy2(src_item, dst_item)
                elif os.path.isdir(src_item):
                    if os.path.exists(dst_item):
                        shutil.rmtree(dst_item)
                    shutil.copytree(src_item, dst_item)
            except Exception as e:
                print(f"[ITSData] 复制失败 {src_item} → {dst_item}: {e}")

    # ==================== 答题锁定 & 悬浮窗 ====================
    def _ensure_its_data_folder_for_question(self, q, folder_path):
        """按需为单个题目创建 ITSData 文件夹（刷题模式用，考试模式已有 _setup_its_data 预创建）

        在刷题模式下，_setup_its_data 不再被调用（防止一次性创建大量文件夹）。
        本方法在用户点击"打开考试文件夹"时按需创建对应文件夹并填充内容。
        """
        import shutil
        qtype = q.get("type", "")
        ITS_DIR = r"C:\ITSData"
        qid = q.get("_qid")

        try:
            if qtype == "programming":
                # 创建 C 或 C1/C2... 文件夹，并放入 prog.c
                os.makedirs(folder_path, exist_ok=True)
                prog_c_path = os.path.join(folder_path, "prog.c")
                
                # ★ 优先使用教师端下发的文件夹
                lan_folder = getattr(self, "_lan_received_folders", {}).get(q.get("_teacher_qid", qid))
                if lan_folder and os.path.isfile(os.path.join(lan_folder, "prog.c")):
                    shutil.copy2(os.path.join(lan_folder, "prog.c"), prog_c_path)
                elif not os.path.exists(prog_c_path):
                    template = os.path.join(EXAM_DIR, "template_prog.c")
                    if os.path.exists(template):
                        shutil.copy2(template, prog_c_path)
                    else:
                            with open(prog_c_path, "w", encoding="gbk") as f:
                                f.write(
                                    '#include <stdio.h>\nmain() {\n    /*答题区域   春考网络一点通 */\n    \n    \n    \n    /*答题区域   网络技术潘老师 */\n    \n}\n'
                                )

            elif qtype == "web_design":
                # 创建 DW 文件夹，并复制题目素材
                # ★ 优先使用教师端下发的文件夹
                lan_folder = getattr(self, "_lan_received_folders", {}).get(q.get("_teacher_qid", qid))
                if lan_folder and os.path.isdir(lan_folder):
                    if not os.path.exists(folder_path):
                        os.makedirs(folder_path, exist_ok=True)
                    self._copy_folder_contents(lan_folder, folder_path)
                else:
                    if not os.path.exists(folder_path):
                        os.makedirs(folder_path, exist_ok=True)
                        source_folder = _resolve_path(q.get("folder_path", "")) or WEBSITE_DIR
                        self._copy_folder_contents(source_folder, folder_path)
                    else:
                        # 文件夹已存在，检查是否有内容，若无则重新复制
                        if not os.listdir(folder_path):
                            source_folder = _resolve_path(q.get("folder_path", "")) or WEBSITE_DIR
                            self._copy_folder_contents(source_folder, folder_path)

            elif qtype == "graphic_design":
                # 创建 PS 文件夹，并复制题目素材
                # ★ 优先使用教师端下发的文件夹
                lan_folder = getattr(self, "_lan_received_folders", {}).get(q.get("_teacher_qid", qid))
                if lan_folder and os.path.isdir(lan_folder):
                    if not os.path.exists(folder_path):
                        os.makedirs(folder_path, exist_ok=True)
                    self._copy_folder_contents(lan_folder, folder_path)
                else:
                    if not os.path.exists(folder_path):
                        os.makedirs(folder_path, exist_ok=True)
                        source_folder = _resolve_path(q.get("folder_path", "")) or GRAPHIC_DIR
                        self._copy_folder_contents(source_folder, folder_path)
                    else:
                        if not os.listdir(folder_path):
                            source_folder = _resolve_path(q.get("folder_path", "")) or GRAPHIC_DIR
                            self._copy_folder_contents(source_folder, folder_path)
        except Exception as e:
            print(f"[ITSData] 按需创建文件夹失败: {e}")

    def _start_answer(self, q, folder_path):
        """点击答题按钮：按需创建文件夹 + 打开文件夹 + 锁定题目 + 弹出悬浮窗"""
        qid = q["_qid"]

        # ★ 先锁定题目，再处理文件夹——无论文件夹是否存在，都要锁住防止切换
        self._locked_question = qid
        try:
            self._update_sidebar_buttons()
        except Exception:
            pass  # 刷题模式下 sidebar 结构不同，忽略更新失败

        # ★ 刷题模式下 _setup_its_data 不预创建文件夹，这里按需创建
        self._ensure_its_data_folder_for_question(q, folder_path)

        # 打开考试文件夹
        if os.path.exists(folder_path):
            try:
                os.startfile(folder_path)
            except Exception as e:
                messagebox.showwarning("提示", f"无法打开文件夹：{folder_path}\n错误：{e}")
        else:
            messagebox.showwarning("提示", f"考试文件夹不存在：{folder_path}\n请检查 C:\\\\ITSData 目录权限")

        # 关闭旧悬浮窗
        if self._float_window is not None:
            try:
                self._float_window.destroy()
            except Exception:
                pass
            self._float_window = None

        self._create_answer_float_window(q)

    def _create_answer_float_window(self, q):
        """创建显示题干的悬浮窗，始终置顶，无边框，最小化只显示按钮栏，初始位置在左下角"""
        win = tk.Toplevel(self.root)
        self._float_window = win
        qid = q["_qid"]

        # 隐藏系统标题栏
        win.overrideredirect(True)
        win.configure(bg=COLOR_BG)
        win.attributes('-topmost', True)

        idx = next((i for i, qq in enumerate(self.questions) if qq["_qid"] == qid), -1)
        type_names = {"programming": "C语言·编程题", "web_design": "网页制作·操作题",
                     "graphic_design": "图形图像处理·操作题"}
        qtype_label = type_names.get(q.get("type", ""), q.get("title", ""))

        # 窗口关闭时也解锁
        def on_close():
            if self._locked_question == qid:
                self._locked_question = None
                try:
                    self._update_sidebar_buttons()
                except Exception:
                    pass  # 刷题模式下 sidebar 结构不同
            self._float_window = None
            try:
                win.destroy()
            except Exception:
                pass

        # 初始位置：左下角（距左边10px，距底部120px）
        # 用 root 的屏幕尺寸，比新 Toplevel 更可靠
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w, win_h = 480, 420
        bottom_y = sh - win_h - 120
        geo_normal = f"{win_w}x{win_h}+10+{bottom_y}"
        win.geometry(geo_normal)
        win.minsize(360, 60)

        # ========== 自定义标题栏（可拖拽 + 关闭按钮） ==========
        title_bar = tk.Frame(win, bg=COLOR_ACCENT, height=36)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        tk.Label(title_bar, text=f"📋 题干 - {qtype_label}",
                font=("微软雅黑", 11, "bold"), fg=COLOR_WHITE, bg=COLOR_ACCENT,
                anchor="w").pack(side="left", fill="both", expand=True, padx=12)

        # 关闭按钮 ×
        close_btn = tk.Label(title_bar, text=" ✕ ", font=("微软雅黑", 12, "bold"),
                            fg=COLOR_WHITE, bg=COLOR_ACCENT, cursor="hand2")
        close_btn.pack(side="right", padx=(0, 8))
        close_btn.bind("<Enter>", lambda e: close_btn.config(bg="#e74c3c"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(bg=COLOR_ACCENT))
        close_btn.bind("<Button-1>", lambda e: on_close())

        # 标题栏拖拽移动窗口
        def start_drag(e):
            win._drag_x = e.x_root - win.winfo_x()
            win._drag_y = e.y_root - win.winfo_y()

        def do_drag(e):
            try:
                win.geometry(f"+{e.x_root - win._drag_x}+{e.y_root - win._drag_y}")
            except Exception:
                pass

        title_bar.bind("<Button-1>", start_drag)
        title_bar.bind("<B1-Motion>", do_drag)

        # ========== 状态变量 ==========
        is_minimized = [False]
        geo_normal_saved = [geo_normal]   # 保存正常状态的 geometry

        # ========== 按钮栏（始终可见） ==========
        btn_frame = tk.Frame(win, bg=COLOR_BG)
        btn_frame.pack(fill="x", padx=10, pady=(8, 5))

        # ========== 题干内容区 ==========
        content_outer = tk.Frame(win, bg=COLOR_WHITE, bd=1, relief="solid")
        content_outer.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        ct = tk.Text(content_outer, font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                    wrap="word", bd=0, padx=15, pady=15)
        ct_scroll = tk.Scrollbar(content_outer, orient="vertical", command=ct.yview)
        ct.configure(yscrollcommand=ct_scroll.set)
        ct.pack(side="left", fill="both", expand=True)
        ct_scroll.pack(side="right", fill="y")

        content = q.get("content", "")
        ct.insert("1.0", content)
        ct.config(state="disabled")

        # ========== 按钮回调函数 ==========
        def show_question():
            """显示题目：将主窗口置前"""
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._display_question(idx)

        def do_minimize():
            """最小化：隐藏内容区，窗口缩小并贴到左下角"""
            if not is_minimized[0]:
                is_minimized[0] = True
                geo_normal_saved[0] = win.geometry()
                content_outer.pack_forget()
                # 强制贴到左下角（x=10, y=屏幕高度-76-10）
                sh = self.root.winfo_screenheight()
                win.geometry(f"{win_w}x76+10+{sh - 76 - 120}")
                min_btn.config(text="⬆ 最大化")

        def do_restore():
            """还原：恢复完整窗口并贴到左下角"""
            if is_minimized[0]:
                is_minimized[0] = False
                content_outer.pack(fill="both", expand=True, padx=10, pady=(0, 10))
                # 还原时也贴到左下角
                sh = self.root.winfo_screenheight()
                win.geometry(f"{win_w}x{win_h}+10+{sh - win_h - 120}")
                min_btn.config(text="🔽 最小化")

        def toggle_minimize():
            """切换最小化/还原"""
            if is_minimized[0]:
                do_restore()
            else:
                do_minimize()

        def submit_current():
            """提交本题并解锁"""
            qtype = q.get("type", "")
            if qtype == "programming":
                self._submit_programming(qid)
            elif qtype == "web_design":
                self._submit_web_design(qid)
            elif qtype == "graphic_design":
                self._submit_graphic_design(qid)

        # ========== 三个按钮 ==========
        tk.Button(btn_frame, text="📄 显示题目", font=("微软雅黑", 10, "bold"),
                 bg="#5b9bd5", fg=COLOR_WHITE, bd=0, padx=16, pady=6,
                 cursor="hand2", command=show_question).pack(side="left", padx=(0, 8))

        min_btn = tk.Button(btn_frame, text="🔽 最小化", font=("微软雅黑", 10, "bold"),
                 bg="#9e9e9e", fg=COLOR_WHITE, bd=0, padx=16, pady=6,
                 cursor="hand2", command=toggle_minimize)
        min_btn.pack(side="left", padx=(0, 8))

        tk.Button(btn_frame, text="✅ 提交本题", font=("微软雅黑", 10, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                 cursor="hand2", command=submit_current).pack(side="right")

    def _unlock_question(self):
        """解锁当前锁定的题目，关闭悬浮窗"""
        if self._locked_question is not None:
            self._locked_question = None
            try:
                self._update_sidebar_buttons()
            except Exception:
                pass  # 刷题模式下 sidebar 结构不同，忽略更新失败
        if self._float_window is not None:
            try:
                self._float_window.destroy()
            except Exception:
                pass
            self._float_window = None

    def _open_folder(self, path):
        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showwarning("提示", f"考试文件夹不存在：{path}")

    def _save_itsdata_to_folder(self, source_dir, label="文件"):
        """将 ITSData 子文件夹保存到用户指定位置（首次设置后自动记住）

        行为：
        1. 若已有保存路径，先询问是否沿用
        2. 用户选择「否」则重新选择路径并记住
        3. 保存前检查目标文件夹是否重名，重名则自动加 (1)/(2) 后缀
        """
        import shutil
        from tkinter import filedialog

        if not os.path.exists(source_dir) or not os.listdir(source_dir):
            messagebox.showwarning("提示", f"{label}文件夹为空，请先完成作答！")
            return

        folder_name = os.path.basename(source_dir)

        # 读取已保存的存储路径
        saved_path = None
        if os.path.exists(SAVE_PATH_FILE):
            try:
                with open(SAVE_PATH_FILE, "r", encoding="utf-8") as f:
                    saved_path = f.read().strip()
                if not saved_path or not os.path.exists(saved_path):
                    saved_path = None
            except Exception:
                saved_path = None

        # ★ 新增：询问是否使用已保存的路径
        if saved_path:
            use_saved = messagebox.askyesno(
                "选择存储路径",
                f"是否保存到之前设置的路径？\n\n"
                f"路径：{saved_path}\n\n"
                f"（点击「否」可选择其他路径）"
            )
            if not use_saved:
                saved_path = None

        # 如果没有路径（首次或用户选择否），弹出选择对话框
        if not saved_path:
            dest = filedialog.askdirectory(title=f"选择{label}存储位置")
            if not dest:
                return  # 用户取消
            # 记住存储路径
            try:
                with open(SAVE_PATH_FILE, "w", encoding="utf-8") as f:
                    f.write(dest)
            except Exception as e:
                messagebox.showwarning(
                    "提示",
                    f"保存路径设置失败：{e}\n本次仍可正常保存，但下次需要重新选择。"
                )
            saved_path = dest

        # ★ 新增：检查重名，自动加数字后缀
        base_name = folder_name
        target_path = os.path.join(saved_path, base_name)
        if os.path.exists(target_path):
            counter = 1
            while True:
                new_name = f"{folder_name}({counter})"
                candidate = os.path.join(saved_path, new_name)
                if not os.path.exists(candidate):
                    base_name = new_name
                    target_path = candidate
                    break
                counter += 1

        # 执行保存
        try:
            shutil.copytree(source_dir, target_path)
            messagebox.showinfo("保存成功", f"{label}已保存到：\n{target_path}")
        except Exception as e:
            messagebox.showerror("保存失败", f"保存过程中出错：{e}")

    def _submit_programming(self, qid):
        prog_folder = self._prog_folder_map.get(qid, "C")
        if os.path.exists(os.path.join(r"C:\ITSData", prog_folder, "prog.c")):
            self.submitted.add(qid)
            self.answers[qid] = "已提交"
            try:
                self._update_sidebar_buttons()
            except Exception:
                pass  # 刷题模式下 sidebar 结构不同，忽略更新失败
            self._unlock_question()
            messagebox.showinfo("提交成功", "编程题已提交！以最后一次提交为准。")
        else:
            messagebox.showwarning("提示", "未找到 prog.c 文件，请先完成作答！")

    def _submit_web_design(self, qid):
        dw_folder = self._dw_folder_map.get(qid, "DW")
        its_dw_dir = os.path.join(r"C:\ITSData", dw_folder)
        website_dir = os.path.join(its_dw_dir, "website")
        if os.path.isdir(website_dir) and os.path.exists(os.path.join(website_dir, "index.html")):
            self.submitted.add(qid)
            self.answers[qid] = "已提交"
            try:
                self._update_sidebar_buttons()
            except Exception:
                pass  # 刷题模式下 sidebar 结构不同，忽略更新失败
            self._unlock_question()
            messagebox.showinfo("提交成功", "网页制作题已提交！以 website 文件夹内容为准。")
        else:
            messagebox.showwarning("提示",
                "未找到 website/index.html，请先完成作答！\n"
                "请确认已在 website 文件夹中创建 index.html 以及 image、css 子文件夹。")

    def _has_reference_answer_content(self, answer_text):
        if isinstance(answer_text, dict):
            return any(str(value).strip() for value in answer_text.values())
        if isinstance(answer_text, (list, tuple)):
            return any(str(value).strip() for value in answer_text)
        return bool(str(answer_text or "").strip())

    def _add_reference_answer_button(self, parent, answer_text, padx=(8, 0)):
        if not self._has_reference_answer_content(answer_text):
            return None
        return tk.Button(parent, text="查看参考答案", font=("微软雅黑", 10, "bold"),
                         bg="#FF6F00", fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                         cursor="hand2",
                         command=lambda ra=answer_text: self._show_reference_answer_popup(ra))

    def _get_reference_folder_path(self, item):
        if not isinstance(item, dict):
            return ""
        for key in ("reference_folder", "reference_answer_folder", "answer_folder"):
            folder = item.get(key, "")
            if not folder:
                continue
            resolved = _resolve_path(str(folder))
            if resolved and os.path.isdir(resolved):
                return resolved
        return ""

    def _add_reference_folder_button(self, parent, item):
        folder = self._get_reference_folder_path(item)
        if not folder:
            return None
        return tk.Button(parent, text="查看参考答案", font=("微软雅黑", 10, "bold"),
                         bg="#FF6F00", fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                         cursor="hand2", command=lambda path=folder: os.startfile(path))

    def _show_reference_answer_popup(self, answer_text):
        """弹出窗口显示参考答案内容"""
        popup = tk.Toplevel(self.root)
        popup.title("参考答案")
        popup.geometry("600x400")
        popup.resizable(True, True)
        popup.minsize(400, 280)
        popup.configure(bg=COLOR_BG)
        popup.transient(self.root)
        popup.grab_set()
        sw, sh = self._screen_size()
        popup.geometry(f"+{sw//2-300}+{sh//2-200}")

        tk.Label(popup, text="参考答案", font=("微软雅黑", 14, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 10))

        # 如果答案是 dict（网络设备题），格式化为可读文本
        if isinstance(answer_text, dict):
            display_text = "\n\n".join(f"【{label}】\n{answer}" for label, answer in answer_text.items() if answer.strip())
            if not display_text:
                display_text = "（暂无参考答案）"
        else:
            display_text = answer_text

        text_frame = tk.Frame(popup, bg=COLOR_WHITE, bd=1, relief="solid")
        text_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        ref_text = tk.Text(text_frame, font=("Consolas", 10), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                          wrap="word", bd=0, padx=12, pady=10, state="disabled")
        ref_scroll = tk.Scrollbar(text_frame, orient="vertical", command=ref_text.yview)
        ref_text.configure(yscrollcommand=ref_scroll.set)
        ref_text.pack(side="left", fill="both", expand=True)
        ref_scroll.pack(side="right", fill="y")
        ref_text.config(state="normal")
        ref_text.insert("1.0", display_text)
        ref_text.config(state="disabled")

        tk.Button(popup, text="关闭", font=("微软雅黑", 11),
                 bg="#ccc", fg="#333", bd=0, padx=20, pady=6,
                 cursor="hand2", command=popup.destroy).pack(pady=(0, 15))

    @staticmethod
    def _match_flexible_answer(user_text, ref_pattern):
        """灵活答案匹配引擎，支持多种匹配模式。

        匹配模式（按优先级）：
        1. 精确匹配（大小写不敏感）: "3000" → 字符串完全相同
        2. 正则匹配: "/^3[0-9]{3}$/" → 正则表达式匹配
        3. 数字范围匹配: "3000~3999" → 数字在闭区间内
        4. 多值匹配: "3000|3005|3100" → 匹配任一值
        5. 关键词包含: "*permit*ip*" → 答案包含所有关键词
        6. 模糊匹配: 去除空格换行后比较（兜底）

        返回: (matched: bool, match_type: str, comment: str)
        """
        import re as _re
        if not ref_pattern or not ref_pattern.strip():
            return False, "无参考答案", ""

        ref = ref_pattern.strip()
        user = (user_text or "").strip()

        # 去除多余空格/换行，统一比较
        user_normalized = " ".join(user.split())
        ref_normalized = " ".join(ref.split())

        # ---- 1. 精确匹配（大小写不敏感） ----
        if user_normalized.lower() == ref_normalized.lower():
            return True, "精确匹配", ""

        # ---- 2. 正则匹配：/pattern/ ----
        if ref.startswith("/") and ref.endswith("/") and len(ref) > 2:
            try:
                if _re.search(ref[1:-1], user, _re.IGNORECASE | _re.DOTALL):
                    return True, "正则匹配", f"模式: {ref}"
            except _re.error:
                pass  # 正则语法错误，继续尝试其他模式

        # ---- 3. 数字范围匹配：3000~3999 ----
        range_m = _re.match(r'^(-?\d+)\s*~\s*(-?\d+)$', ref)
        if range_m:
            try:
                lo, hi = int(range_m.group(1)), int(range_m.group(2))
                user_num = int(user_normalized)
                if lo <= user_num <= hi:
                    return True, "范围匹配", f"范围: {lo}~{hi}"
            except (ValueError, TypeError):
                pass  # 用户输入不是数字，回退到其他模式

        # ---- 4. 多值匹配：val1|val2|val3 ----
        if "|" in ref and not ref.startswith("/"):
            options = [o.strip() for o in ref.split("|") if o.strip()]
            for opt in options:
                if user_normalized.lower() == opt.lower():
                    return True, "多值匹配", f"匹配: {opt}"

        # ---- 5. 关键词包含：*keyword1*keyword2*... ----
        if "*" in ref:
            keywords = [k.strip() for k in ref.split("*") if k.strip()]
            if keywords:
                user_lower = user.lower()
                matched_kw = [kw for kw in keywords if kw.lower() in user_lower]
                if len(matched_kw) == len(keywords):
                    return True, "关键词匹配", f"关键词: {' & '.join(keywords)}"

        # ---- 6. 模糊匹配（兜底）：比较前N个字符 ----
        short_len = min(len(user_normalized), len(ref_normalized), 200)
        if short_len > 0 and user_normalized[:short_len].lower() == ref_normalized[:short_len].lower():
            return True, "部分匹配", f"前{short_len}个字符一致"

        return False, "不匹配", ""


    def _judge_program(self, qid, orig_id, source_path, num_cases=10):
        """编程题判题方法
        参数：
            qid: 运行时题目序号
            orig_id: 题库中原题目的 id（用于定位测试案例）
            source_path: 源代码文件路径（prog.c）
            num_cases: 测试案例数量
        返回：(passed_count, total_count, details_list)
        """
        import subprocess
        import tempfile
        details = []
        passed = 0

        src_dir = os.path.dirname(source_path)
        exe_path = os.path.join(src_dir, "prog.exe")
        test_dir = os.path.join(IMAGE_DIR, "test_cases", f"prog_{orig_id}")

        # 0. 检查测试案例目录是否存在
        if not os.path.isdir(test_dir):
            return (0, 0, [{"case": 0, "status": "NO_CASES",
                            "note": f"该题目尚未配置测试案例。\n请在 {test_dir} 下放置 .in/.out 文件。"}])

        # 1. 编译前检查 GCC 是否存在
        gcc_path = _detect_gcc_path()
        if not gcc_path or not os.path.exists(gcc_path):
            return (0, 0, [{"case": 0, "status": "CE",
                                "note": "未找到 GCC 编译器。请确认 Dev-C++ 或 MinGW 已安装。"}])

        # 将 MinGW64\bin 加入 PATH，确保 libiconv-2.dll 等依赖可被找到
        MINGW_BIN = os.path.dirname(gcc_path)
        env = os.environ.copy()
        env["PATH"] = MINGW_BIN + os.pathsep + env.get("PATH", "")

        total = num_cases

        try:
            compile_cmd = f'"{gcc_path}" "{source_path}" -o "{exe_path}" -Wall -std=c99'
            proc = subprocess.run(
                compile_cmd, shell=True, capture_output=True, text=True, timeout=10,
                cwd=src_dir, env=env
            )
            if proc.returncode != 0:
                return (0, total, [{
                    "case": 0,
                    "status": "CE",
                    "note": f"编译错误:\n{proc.stderr[:500]}"
                }])
        except subprocess.TimeoutExpired:
            return (0, total, [{"case": 0, "status": "CE", "note": "编译超时（10秒）"}])
        except FileNotFoundError:
            return (0, total, [{"case": 0, "status": "CE",
                                "note": f"编译器执行失败: {gcc_path}\n请确认该路径下的 gcc.exe 可正常执行。"}])
        except Exception as e:
            return (0, total, [{"case": 0, "status": "CE", "note": f"编译异常: {str(e)}"}])

        # 2. 逐个测试案例运行
        for i in range(1, num_cases + 1):
            in_file = os.path.join(test_dir, f"{i}.in")
            out_file = os.path.join(test_dir, f"{i}.out")

            if not os.path.exists(in_file) or not os.path.exists(out_file):
                details.append({"case": i, "status": "SKIP", "note": f"测试案例 {i} 缺失 .in 或 .out 文件"})
                total -= 1
                continue

            try:
                with open(in_file, "r", encoding="utf-8") as f_in:
                    stdin_data = f_in.read()

                # 用 text=False 读取原始字节，再智能解码（兼容 GBK/UTF-8）
                proc = subprocess.run(
                    [exe_path], input=stdin_data.encode("utf-8"),
                    capture_output=True, text=False,
                    timeout=2, cwd=src_dir, env=env
                )

                raw_stdout = proc.stdout
                raw_stderr = proc.stderr
                # 先尝试 UTF-8，如果出现大量替换字符则回退到 GBK（Windows 控制台默认编码）
                def _decode_output(raw):
                    try:
                        decoded = raw.decode("utf-8")
                        if "\ufffd" in decoded and len(decoded) > 2:
                            decoded = raw.decode("gbk", errors="replace")
                        return decoded.rstrip()
                    except Exception:
                        return raw.decode("gbk", errors="replace").rstrip()

                stdout_output = _decode_output(raw_stdout)
                stderr_output = _decode_output(raw_stderr)
                with open(out_file, "r", encoding="utf-8") as f_out:
                    expected_output = f_out.read().rstrip()

                # 规范化输出：合并连续空行、统一换行符
                import re as _re
                _normalize = lambda s: _re.sub(r'\n{2,}', '\n', s.replace('\r\n', '\n').replace('\r', '\n')).rstrip()
                _norm_stdout = _normalize(stdout_output)
                _norm_expected = _normalize(expected_output)
                _display_stdout = _norm_stdout if _norm_stdout else "（程序无输出）"
                _display_expected = _norm_expected if _norm_expected else "（期望为空）"
                if _norm_stdout == _norm_expected:
                    details.append({"case": i, "status": "AC", "note": "通过"})
                    passed += 1
                elif proc.returncode != 0:
                    in_short = stdin_data.strip()
                    error_text = _normalize(stderr_output) if stderr_output else "（无错误输出）"
                    details.append({
                        "case": i, "status": "RE",
                        "note": (
                            f"输入:\n{in_short}\n\n"
                            f"期望:\n{_display_expected}\n\n"
                            f"实际:\n{_display_stdout}\n\n"
                            f"程序退出码: {proc.returncode}\n"
                            f"错误输出:\n{error_text}"
                        )
                    })
                else:
                    in_short = stdin_data.strip()
                    details.append({
                        "case": i, "status": "WA",
                        "note": f"输入:\n{in_short}\n\n期望:\n{_display_expected}\n\n实际:\n{_display_stdout}"
                    })

            except subprocess.TimeoutExpired:
                details.append({"case": i, "status": "TLE", "note": "运行超时（2秒）"})
            except Exception as e:
                details.append({"case": i, "status": "RE", "note": f"运行异常: {str(e)[:200]}"})

        return (passed, total, details)

    def _copy_code(self, code):
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        messagebox.showinfo("提示", "代码已复制，可粘贴到 Dev-C++ 中调试。")

    def _has_code_block(self, content):
        """检测题目内容中是否包含代码块"""
        if not content:
            return False
        if "```" in content:
            return True
        patterns = [
            r'#include\s*<', r'int\s+main\s*\(', r'void\s+main\s*\(',
            r'public\s+class\s+\w+', r'public\s+static\s+void\s+main',
            r'#define\s+\w+', r'printf\s*\(', r'scanf\s*\(',
            r'System\.out\.print', r'cout\s*<<', r'cin\s*>>',
        ]
        for p in patterns:
            if re.search(p, content):
                return True
        return False

    def _extract_code(self, content):
        """从题目内容中提取代码部分"""
        match = re.search(r'```[\w]*\n?(.*?)```', content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return content.strip()

    def toggle_mark(self):
        qid = self.questions[self.current_question]["_qid"]
        if qid in self.marked:
            self.marked.remove(qid)
        else:
            self.marked.add(qid)
        self._update_sidebar_buttons()


    def _is_answered(self, q):
        """判断某题是否真正作答（排除仅初始化的空dict/空字符串）"""
        qid = q["_qid"]
        qtype = q.get("type") or q.get("_type", "")
        if qid in self.submitted:
            return True
        if qid not in self.answers:
            return False
        ans = self.answers[qid]
        if qtype == "fill_blank":
            if isinstance(ans, dict):
                return any(v for v in ans.values() if v)
            return False
        elif qtype == "network_device":
            if isinstance(ans, dict):
                return any(str(v).strip() for v in ans.values() if v is not None)
            return False
        elif qtype == "single_choice":
            return isinstance(ans, int) and ans >= 0
        elif qtype in ("programming", "web_design", "graphic_design"):
            # 操作题型只在学生点击“提交本题”后计为已答；
            # 仅创建/复制模板文件夹不算作答，避免局域网考试首题误显示已答。
            return ans == "已提交"
        else:
            # 兜底：ans 为非空值才认为已答
            if isinstance(ans, dict):
                return any(v for v in ans.values() if v)
            return bool(ans)

    def _check_operational_answered(self, q):
        """检查操作题型（编程/DW/PS）是否真正作答：检查 C:\\ITSData 下对应文件是否有实际内容"""
        qid = q["_qid"]
        qtype = q["type"]
        ITS_DIR = r"C:\ITSData"
        try:
            if qtype == "programming":
                # 检查 C:\ITSData\C\prog.c 是否有实际代码（非常量模板）
                folder = getattr(self, "_prog_folder_map", {}).get(qid, "C")
                prog_c = os.path.join(ITS_DIR, folder, "prog.c")
                if not os.path.isfile(prog_c):
                    return False
                with open(prog_c, "r", encoding="gbk", errors="replace") as f:
                    content = f.read()
                # 去除模板常量部分，检查是否有新增代码
                stripped = content.strip()
                if len(stripped) < 50:  # 小于50字符认为基本是空模板
                    return False
                return True
            elif qtype == "web_design":
                folder = getattr(self, "_dw_folder_map", {}).get(qid, "DW")
                dw_dir = os.path.join(ITS_DIR, folder)
                if not os.path.isdir(dw_dir):
                    return False
                # 检查文件夹是否有非模板文件，或有修改
                files = os.listdir(dw_dir)
                return len(files) > 0
            elif qtype == "graphic_design":
                folder = getattr(self, "_ps_folder_map", {}).get(qid, "PS")
                ps_dir = os.path.join(ITS_DIR, folder)
                if not os.path.isdir(ps_dir):
                    return False
                files = os.listdir(ps_dir)
                return len(files) > 0
        except Exception:
            pass
        # 兜底：检查 self.answers
        ans = self.answers.get(qid)
        if isinstance(ans, dict):
            return any(v for v in ans.values() if v)
        return bool(ans)

    def _grade_exam(self):
        """批改考试：只批改单选题和填空题，返回总分和详细结果"""
        total_score = 0
        results = []
        
        for q in self.questions:
            qid = q["_qid"]
            qtype = q["type"]
            score = q.get("score", 0)
            
            # 单选题/填空题/编程题自动批改，网络设备题/DW/PS操作题需人工批改
            if qtype not in ["single_choice", "fill_blank", "programming"]:
                results.append({
                    "qid": qid,
                    "type": qtype,
                    "score": 0,
                    "correct": False,
                    "user_answer": self.answers.get(qid, ""),
                    "reference_answer": q.get("reference_answer", ""),
                    "reference_folder": q.get("reference_folder", q.get("reference_answer_folder", q.get("answer_folder", ""))),
                    "answer_doc": q.get("answer_doc", ""),
                    "status": "需人工批改"
                })
                continue
            
            user_answer = self.answers.get(qid)
            # 单选题从"answer"字段获取正确答案索引，填空题也从"answer"字段获取
            if qtype == "single_choice":
                ref_answer = q.get("answer", -1)  # 从"answer"字段获取正确答案索引
            else:
                ref_answer = q.get("answer", q.get("reference_answer", ""))  # 填空题优先从"answer"字段获取
            
            if qtype == "single_choice":
                # 单选题：比较答案索引
                correct = user_answer == ref_answer
                if correct:
                    total_score += score
                else:
                    self.wrong_questions.add(qid)
                results.append({
                    "qid": qid,
                    "type": qtype,
                    "score": score if correct else 0,
                    "correct": correct,
                    "user_answer": user_answer,
                    "reference_answer": ref_answer,
                    "status": "正确" if correct else "错误"
                })
                
            elif qtype == "fill_blank":
                # 填空题：比较字典值
                if isinstance(user_answer, dict) and isinstance(ref_answer, dict):
                    # 规范化用户答案的key：去掉【】符号，提取纯数字
                    # 用户答案key格式: '【1】' -> '1'
                    normalized_user = {}
                    for k, v in user_answer.items():
                        # 去掉【】等符号，只保留数字
                        clean_key = k.strip().replace('【', '').replace('】', '').replace('[', '').replace(']', '').strip()
                        normalized_user[clean_key] = v
                    
                    correct_count = 0
                    total_blanks = len(ref_answer)
                    for key, ref_val in ref_answer.items():
                        # key是'1','2'等纯数字字符串
                        user_val = normalized_user.get(key, "").strip()
                        if user_val.upper() == ref_val.strip().upper():  # 大小写不敏感比较
                            correct_count += 1
                    
                    if total_blanks > 0:
                        blank_score = (correct_count / total_blanks) * score
                        total_score += blank_score
                        is_all_correct = (correct_count == total_blanks)
                        if not is_all_correct:
                            self.wrong_questions.add(qid)
                        results.append({
                            "qid": qid,
                    "type": qtype,
                    "score": round(blank_score, 1),
                    "correct": is_all_correct,
                    "user_answer": user_answer,
                    "reference_answer": ref_answer,
                    "answer": ref_answer,  # 添加answer字段，供显示使用
                    "status": f"正确 {correct_count}/{total_blanks}"
                        })
                    else:
                        results.append({
                            "qid": qid,
                    "type": qtype,
                    "score": 0,
                    "correct": False,
                    "user_answer": user_answer,
                    "reference_answer": ref_answer,
                    "answer": ref_answer,  # 确保所有分支都有answer字段
                    "status": "无参考答案"
                        })
                else:
                    results.append({
                        "qid": qid,
                "type": qtype,
                "score": 0,
                "correct": False,
                "user_answer": user_answer,
                "reference_answer": ref_answer,
                "answer": ref_answer,  # 确保所有分支都有answer字段
                    "status": "格式错误"
                    })

            elif qtype == "network_device":
                # 网络设备题：改为人工批改，仅显示参考答案（不做自动匹配）
                ref_answer = q.get("reference_answer", {})
                results.append({
                    "qid": qid,
                    "type": qtype,
                    "score": 0,
                    "correct": False,
                    "user_answer": self.answers.get(qid, ""),
                    "reference_answer": ref_answer,
                    "reference_folder": q.get("reference_folder", q.get("reference_answer_folder", q.get("answer_folder", ""))),
                    "answer_doc": q.get("answer_doc", ""),
                    "status": "需人工批改"
                })

            elif qtype == "programming":
                # 编程题：调用 _judge_program 自动编译运行判题
                orig_id = q.get("id") or q.get("_orig_id")
                # 实际参考答案（题库中的 reference_answer 字段，是字符串）
                real_ref_answer = q.get("reference_answer", "")
                if orig_id:
                    prog_path = os.path.join(PROG_DIR, "prog.c")
                    if os.path.exists(prog_path):
                        passed, total_cases, details = self._judge_program(qid, orig_id, prog_path)
                        if total_cases > 0:
                            prog_score = (passed / total_cases) * score
                            total_score += prog_score
                            results.append({
                                "qid": qid,
                                "type": qtype,
                                "score": round(prog_score, 1),
                                "correct": passed == total_cases,
                                "user_answer": f"通过 {passed}/{total_cases} 个测试案例",
                                "reference_answer": real_ref_answer,
                                "judge_details": details,
                                "status": f"通过 {passed}/{total_cases} 个测试案例"
                            })
                        else:
                            results.append({
                                "qid": qid,
                                "type": qtype,
                                "score": 0,
                                "correct": False,
                                "user_answer": details[0]["note"] if details else "",
                                "reference_answer": real_ref_answer,
                                "judge_details": details,
                                "status": "无法判题"
                            })
                    else:
                        results.append({
                            "qid": qid,
                            "type": qtype,
                            "score": 0,
                            "correct": False,
                            "user_answer": "源代码文件不存在",
                            "reference_answer": real_ref_answer,
                            "status": "未提交"
                        })
                else:
                    results.append({
                        "qid": qid,
                        "type": qtype,
                        "score": 0,
                        "correct": False,
                        "user_answer": self.answers.get(qid, ""),
                        "reference_answer": real_ref_answer,
                        "status": "无题目ID"
                    })

        return round(total_score, 1), results

    def _show_exam_result(self, score, results):
        """显示考试批改结果"""
        dialog = tk.Toplevel(self.root)
        dialog.title("考试批改结果")
        dialog.geometry("950x700")
        dialog.resizable(True, True)
        dialog.minsize(700, 450)
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # 标题
        tk.Label(dialog, text="考试批改结果", font=("微软雅黑", 18, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 5))
        tk.Label(dialog, text=f"客观题总分：{score}分", font=("微软雅黑", 14, "bold"),
                fg=COLOR_SUCCESS, bg=COLOR_BG).pack(pady=(0, 10))
        
        # 说明
        tk.Label(dialog, text="注：单选题/填空题/编程题自动批改，网络设备题/DW/PS操作题需人工批改",
                font=("微软雅黑", 10), fg="#666", bg=COLOR_BG).pack(pady=(0, 15))
        
        # 可滚动画布
        canvas = tk.Canvas(dialog, bg=COLOR_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        content_frame = tk.Frame(canvas, bg=COLOR_BG)
        
        content_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=content_frame, anchor="nw", width=800)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True, padx=(15, 0), pady=5)
        scrollbar.pack(side="right", fill="y", padx=(0, 5), pady=5)
        
        # 结果表格
        for result in results:
            card = tk.Frame(content_frame, bg=COLOR_WHITE, bd=1, relief="solid")
            card.pack(fill="x", padx=10, pady=5)
            
            # 标题行
            title_frame = tk.Frame(card, bg=COLOR_WHITE)
            title_frame.pack(fill="x", padx=15, pady=(10, 5))
            
            tk.Label(title_frame, text=f"第 {result['qid']} 题", 
                    font=("微软雅黑", 12, "bold"), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(side="left")
            
            type_label = "单选题" if result["type"] == "single_choice" else "填空题" if result["type"] == "fill_blank" else result["type"]
            tk.Label(title_frame, text=f"（{type_label}）", 
                    font=("微软雅黑", 11), fg="#666", bg=COLOR_WHITE).pack(side="left", padx=(5, 0))
            
            # 状态和分数
            status_color = COLOR_SUCCESS if result.get("correct", False) else COLOR_DANGER if result["status"] not in ("不批改", "需人工批改") else "#666"
            if result["status"] == "不批改":
                status_text = "不批改。"
            elif result["status"] == "需人工批改":
                status_text = "需人工批改"
            else:
                status_text = f"{result['status']} | {result['score']}分"
            tk.Label(title_frame, text=status_text,
                    font=("微软雅黑", 11, "bold"), fg=status_color, bg=COLOR_WHITE).pack(side="right")
            
            # 内容区域
            content_frame_inner = tk.Frame(card, bg=COLOR_WHITE)
            content_frame_inner.pack(fill="x", padx=15, pady=(0, 10))
            
            # 显示题目内容
            for q in self.questions:
                if q["_qid"] == result["qid"]:
                    content_text = q.get("content", "")[:200] + ("..." if len(q.get("content", "")) > 200 else "")
                    tk.Label(content_frame_inner, text=content_text,
                            font=("微软雅黑", 10), fg="#333", bg=COLOR_WHITE, wraplength=475, justify="left").pack(anchor="w", pady=(0, 5))
                    break
            
            # 显示答案对比
            if result["type"] in ["single_choice", "fill_blank"]:
                answer_frame = tk.Frame(content_frame_inner, bg="#f5f5f5", bd=1, relief="solid")
                answer_frame.pack(fill="x", pady=(5, 0))
                
                # 用户答案
                user_frame = tk.Frame(answer_frame, bg="#f5f5f5")
                user_frame.pack(fill="x", padx=10, pady=5)
                tk.Label(user_frame, text="你的答案：", font=("微软雅黑", 10, "bold"), 
                        fg="#333", bg="#f5f5f5").pack(side="left")
                
                # 单选题将数字索引转换为ABCD字母
                if result["type"] == "single_choice":
                    user_answer_val = result["user_answer"]
                    if user_answer_val is not None and isinstance(user_answer_val, (int, float)):
                        user_answer_text = chr(65 + int(user_answer_val))  # 0->A, 1->B, 2->C, 3->D
                    else:
                        user_answer_text = str(user_answer_val) if user_answer_val is not None else "未作答"
                else:
                    # 填空题：格式化用户答案字典为可读形式
                    ua = result["user_answer"]
                    if isinstance(ua, dict) and ua:
                        # key可能是'【1】'或'1'，统一提取数字部分
                        def _clean_key(k):
                            return k.strip().replace('【', '').replace('】', '').replace('[', '').replace(']', '').strip()
                        items = sorted(ua.items(), key=lambda x: (int(_clean_key(x[0])) if _clean_key(x[0]).isdigit() else _clean_key(x[0])))
                        user_answer_text = "  ".join(f"{_clean_key(k)}:{v}" for k, v in items)
                    else:
                        user_answer_text = str(ua) if ua is not None else "未作答"
                    
                tk.Label(user_frame, text=user_answer_text, font=("微软雅黑", 10), 
                        fg=COLOR_DANGER if not result.get("correct", False) and result["status"] != "不批改" else "#333", 
                        bg="#f5f5f5").pack(side="left", padx=(5, 0))
                
                # 参考答案
                ref_frame = tk.Frame(answer_frame, bg="#f5f5f5")
                ref_frame.pack(fill="x", padx=10, pady=(0, 5))
                tk.Label(ref_frame, text="参考答案：", font=("微软雅黑", 10, "bold"), 
                        fg="#333", bg="#f5f5f5").pack(side="left")
                
                # 单选题将数字索引转换为ABCD字母
                if result["type"] == "single_choice":
                    ref_answer_val = result["reference_answer"]
                    if isinstance(ref_answer_val, (int, float)):
                        ref_answer_text = chr(65 + int(ref_answer_val))  # 0->A, 1->B, 2->C, 3->D
                    else:
                        ref_answer_text = str(ref_answer_val)
                else:
                    # 填空题：使用answer字段（字典格式如{'1':'C','2':'B'}），而非空的reference_answer
                    ref_answer_val = result.get("answer") or result.get("reference_answer", {})
                    if isinstance(ref_answer_val, dict):
                        # 格式化为 "1:C 2:B 3:A" 的可读形式
                        items = [f"{k}:{v}" for k, v in sorted(ref_answer_val.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0])]
                        ref_answer_text = "  ".join(items) if items else "无参考答案"
                    else:
                        ref_answer_text = str(ref_answer_val) if ref_answer_val else "无参考答案"
                    
                tk.Label(ref_frame, text=ref_answer_text, font=("微软雅黑", 10), 
                        fg=COLOR_SUCCESS, bg="#f5f5f5").pack(side="left", padx=(5, 0))
            
            elif result["type"] == "network_device":
                # 网络设备题只提供 Word 答案文档，不再显示题库中的文字参考答案
                answer_doc = _resolve_path(result.get("answer_doc", ""))
                if answer_doc and os.path.exists(answer_doc):
                    doc_btn_frame = tk.Frame(content_frame_inner, bg=COLOR_WHITE)
                    doc_btn_frame.pack(fill="x", pady=(5, 0))
                    tk.Button(doc_btn_frame, text="📄 打开答案文档 (Word)", font=("微软雅黑", 10, "bold"),
                             bg="#1565C0", fg=COLOR_WHITE, bd=0, padx=12, pady=5,
                             cursor="hand2",
                             command=lambda path=answer_doc: os.startfile(path)).pack(anchor="w")

            elif result["type"] == "programming":
                # 显示编程题参考答案
                ref_frame = tk.Frame(content_frame_inner, bg="#e8f5e8", bd=1, relief="solid")
                ref_frame.pack(fill="x", pady=(5, 0))

                tk.Label(ref_frame, text="参考答案：", font=("微软雅黑", 10, "bold"),
                        fg=COLOR_SUCCESS, bg="#e8f5e8").pack(anchor="w", padx=10, pady=5)

                ref_answer = result.get("reference_answer", "")
                if isinstance(ref_answer, str) and ref_answer:
                    tk.Label(ref_frame, text=str(ref_answer),
                            font=("微软雅黑", 10), fg="#333", bg="#e8f5e8",
                            wraplength=475, justify="left").pack(anchor="w", padx=20, pady=(0, 5))
                else:
                    tk.Label(ref_frame, text="（无参考答案）",
                            font=("微软雅黑", 10), fg="#999", bg="#e8f5e8").pack(anchor="w", padx=20, pady=(0, 5))

                # 显示判题详情
                judge_details = result.get("judge_details", [])
                if judge_details:
                    detail_frame = tk.Frame(content_frame_inner, bg="#f5f5f5", bd=1, relief="solid")
                    detail_frame.pack(fill="x", pady=(3, 0))
                    tk.Label(detail_frame, text="判题详情：", font=("微软雅黑", 10, "bold"),
                            fg="#333", bg="#f5f5f5").pack(anchor="w", padx=10, pady=(5, 2))
                    for d in judge_details[:5]:
                        status_text = d.get("status", "")
                        note = d.get("note", "")[:150]
                        color = COLOR_SUCCESS if status_text == "AC" else COLOR_DANGER if status_text in ("WA", "CE", "RE", "TLE") else "#666"
                        tk.Label(detail_frame, text=f"  案例{d.get('case', '?')}: [{status_text}] {note}",
                                font=("Consolas", 8), fg=color, bg="#f5f5f5",
                                wraplength=475, justify="left").pack(anchor="w", padx=15, pady=1)

            else:
                # web_design / graphic_design 等其他题型：显示参考答案（如果有）
                ref_btn = self._add_reference_folder_button(content_frame_inner, result)
                if ref_btn:
                    ref_btn.pack(anchor="w", pady=(5, 0))
        
        # 底部按钮
        btn_frame = tk.Frame(dialog, bg=COLOR_BG)
        btn_frame.pack(fill="x", pady=(10, 15))
        
        tk.Button(btn_frame, text="关闭", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=30, pady=8,
                 cursor="hand2", command=dialog.destroy).pack()
    def _update_sidebar_buttons(self, mode="exam"):
        if mode == "practice":
            self._update_practice_sidebar_impl()
            return
        locked_qid = self._locked_question
        for i, q in enumerate(self.questions):
            btn = self.question_buttons[i]
            qid = q["_qid"]
            # 有锁定题目且本题不是锁定的题目 → 禁用/灰色
            if locked_qid is not None and qid != locked_qid:
                btn.config(bg="#cccccc", fg="#888888", font=("微软雅黑", 8),
                          highlightbackground="#cccccc", highlightthickness=1,
                          relief="solid", state="disabled")
                continue
            btn.config(state="normal")
            # 优先级：标记 > 当前题 > 已答 > 未答
            if qid in self.marked:
                btn.config(bg=COLOR_MARKED, fg=COLOR_WHITE, font=("微软雅黑", 8, "bold"),
                          highlightbackground=COLOR_MARKED, highlightthickness=1,
                          relief="solid")
            elif i == self.current_question:
                btn.config(bg=COLOR_ANSWERING, fg=COLOR_WHITE, font=("微软雅黑", 8, "bold"),
                          highlightbackground=COLOR_ANSWERING, highlightthickness=1,
                          relief="solid")
            elif self._is_answered(q):
                btn.config(bg=COLOR_ANSWERED, fg=COLOR_WHITE, font=("微软雅黑", 8),
                          highlightbackground=COLOR_ANSWERED, highlightthickness=1,
                          relief="solid")
            else:
                btn.config(bg=COLOR_UNANSWERED, fg=COLOR_WHITE, font=("微软雅黑", 8),
                          highlightbackground=COLOR_UNANSWERED, highlightthickness=1,
                          relief="solid")

        qid = self.questions[self.current_question]["_qid"]
        if qid in self.marked:
            self.mark_btn.config(text="取消标记", bg="#ce93d8")
        else:
            self.mark_btn.config(text="标记本题", bg="#e1bee7")

    def prev_question(self, mode="exam"):
        if self._locked_question is not None:
            return  # 答题锁定中，禁止切换
        if self.current_question > 0:
            self._display_question(self.current_question - 1, mode=mode)

    def next_question(self, mode="exam"):
        if self._locked_question is not None:
            return  # 答题锁定中，禁止切换
        if self.current_question < len(self.questions) - 1:
            self._display_question(self.current_question + 1, mode=mode)

    # ==================== 计时器 ====================
    def _start_timer(self):
        self._time_warned_5 = False
        self._time_warned_1 = False
        self._warning_banner = None

        def tick():
            with self._timer_lock:
                if not self.timer_running:
                    return
                if self.time_remaining > 0:
                    self.time_remaining -= 1
                    running = True
                else:
                    self.timer_running = False
                    running = False
            if not running:
                self.root.after(0, self.auto_submit)
                return
            self._update_timer_display()
            if self.time_remaining <= 300 and not self._time_warned_5:
                self._time_warned_5 = True
                self.root.after(0, lambda: self._show_time_popup("考试剩余5分钟", "请抓紧时间作答！", "#ff9800"))
            if self.time_remaining <= 60 and not self._time_warned_1:
                self._time_warned_1 = True
                self.root.after(0, lambda: self._show_time_popup("考试剩余1分钟", "系统即将自动交卷！", "#e53935"))
            self.root.after(1000, tick)
        tick()

    def _update_timer_display(self):
        m, s = self.time_remaining // 60, self.time_remaining % 60
        self.timer_var.set(f"⏱ {m:02d}:{s:02d}")
        # 剩余5分钟内背景色闪烁
        with self._timer_lock:
            if self.time_remaining <= 300 and hasattr(self, 'timer_label'):
                flash_color = "#e53935" if (self.time_remaining % 2) == 0 else COLOR_SIDEBAR
                self.timer_label.config(bg=flash_color)

    def _show_time_warning(self, message, is_critical=False):
        """在考试界面顶部显示时间警告横幅，3秒后自动消失"""
        if hasattr(self, '_warning_banner') and self._warning_banner and self._warning_banner.winfo_exists():
            self._warning_banner.destroy()

        bg_color = "#ff4444" if is_critical else "#ff9800"
        banner = tk.Frame(self.root, bg=bg_color, height=40)
        banner.pack(fill="x", before=self.root.winfo_children()[0])
        banner.pack_propagate(False)

        tk.Label(banner, text=message, fg=COLOR_WHITE, bg=bg_color,
                font=("微软雅黑", 14, "bold")).pack(pady=8)

        self._warning_banner = banner
        self._warning_dismiss_id = self.root.after(3500, lambda: self._dismiss_warning(banner))

    def _dismiss_warning(self, banner):
        if hasattr(self, '_warning_dismiss_id') and self._warning_dismiss_id:
            self.root.after_cancel(self._warning_dismiss_id)
            self._warning_dismiss_id = None
        if banner and banner.winfo_exists():
            banner.destroy()
            self._warning_banner = None

    def _show_time_popup(self, title, message, color):
        """弹出置顶提醒窗口，覆盖在所有应用之上"""
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.configure(bg=color)
        popup.attributes('-topmost', True)
        popup.resizable(False, False)

        # 内容区域
        frm = tk.Frame(popup, bg=color, padx=30, pady=20)
        frm.pack()

        tk.Label(frm, text=title, fg=COLOR_WHITE, bg=color,
                font=("微软雅黑", 18, "bold")).pack()
        tk.Label(frm, text=message, fg=COLOR_WHITE, bg=color,
                font=("微软雅黑", 13)).pack(pady=(8, 16))

        def _close():
            if hasattr(popup, '_auto_close_id') and popup._auto_close_id:
                popup.after_cancel(popup._auto_close_id)
                popup._auto_close_id = None
            popup.destroy()

        tk.Button(frm, text="我知道了", font=("微软雅黑", 12, "bold"),
                  bg=COLOR_WHITE, fg=color, padx=30, pady=6, bd=0,
                  cursor="hand2", command=_close).pack()

        # 居中并强制置顶
        popup.update_idletasks()
        w = popup.winfo_width()
        h = popup.winfo_height()
        sw = popup.winfo_screenwidth()
        sh = popup.winfo_screenheight()
        popup.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        popup.focus_force()
        popup.bell()
        # 10 秒后自动关闭
        popup._auto_close_id = self.root.after(10000, _close)

    # ==================== 窗口控制 ====================
    def _do_minimize(self):
        self._was_fullscreen = bool(self.root.attributes('-fullscreen'))
        self.root.attributes('-fullscreen', False)
        self._iconified = True
        self.root.after(100, lambda: self.root.iconify())

    def _do_maximize_restore(self):
        is_full = self.root.attributes('-fullscreen')
        if is_full:
            self.root.attributes('-fullscreen', False)
            self._btn_max.config(text=" □ ")
        else:
            self.root.state('zoomed')
            self._btn_max.config(text=" ❐ ")

    def _do_close(self):
        msg = "确定要退出考试系统吗？"
        if self.exam_started:
            msg += "\n\n⚠ 当前考试尚未交卷，退出后答题进度将丢失！"
        if messagebox.askyesno("退出确认", msg):
            self.root.destroy()

    # ==================== 交卷 ====================
    def _restore_window(self):
        """从最小化状态恢复窗口"""
        try:
            if not self.root.winfo_exists():
                return
            self.root.deiconify()
            # 恢复最小化前的最大化/全屏显示状态
            if getattr(self, '_was_fullscreen', False):
                self.root.attributes('-fullscreen', True)
            self.root.lift()
            self.root.focus_force()
            # 短暂置顶，确保窗口出现在最前面
            self.root.attributes('-topmost', True)
            self.root.after(150, lambda: self.root.attributes('-topmost', False))
        except Exception:
            pass

    def _on_root_close(self):
        """点击窗口 X 按钮关闭"""
        # 考试进行中时弹出确认框，防止误操作
        if getattr(self, 'exam_started', False) and getattr(self, 'exam_end_time', None):
            remaining = self.exam_end_time - datetime.now()
            remaining_sec = max(0, int(remaining.total_seconds()))
            m, s = divmod(remaining_sec, 60)
            if not messagebox.askyesno(
                "确认退出",
                f"考试正在进行中（剩余 {m} 分 {s} 秒）。\n\n"
                "关闭窗口不会清空作答文件，重新打开后可继续处理。\n\n"
                "确定要退出吗？"
            ):
                return
            try:
                os.makedirs(EXAM_CACHE_DIR, exist_ok=True)
                cache_path = os.path.join(EXAM_CACHE_DIR, f"{self.exam_id}_recovery.json")
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "exam_id": self.exam_id,
                        "answers": self.answers,
                        "submitted": list(self.submitted),
                        "session_id": getattr(self, "_current_its_session_id", ""),
                        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"保存关闭恢复缓存失败: {e}")
        self._closing = True
        self.root.destroy()
        self.root.quit()

    def submit_all(self):
        # 局域网模式：直接发送 SUBMIT，不弹确认框
        if self.lan_mode:
            self._do_lan_submit()
            return

        unanswered = []
        for q in self.questions:
            qid = q["_qid"]
            if not self._is_answered(q):
                unanswered.append(str(q["_qid"]))
        msg = "确定要交卷吗？\n"
        if unanswered:
            msg += f"\n未作答题目编号: {', '.join(unanswered)}\n"
        msg += "\n交卷后无法继续答题。"
        if messagebox.askyesno("确认交卷", msg):
            self._do_submit()

    def _do_lan_submit(self):
        """局域网模式交卷：打包答案发送 SUBMIT，文件题附 base64 文件数据（含断线恢复自动上传）"""
        import json as _json_lan
        cache_file = os.path.join(EXAM_CACHE_DIR, f"lan_pending_{self.exam_id}.json")

        try:
            if not self.network_client:
                self._lan_pending_answers = dict(self.answers)
                os.makedirs(EXAM_CACHE_DIR, exist_ok=True)
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        _json_lan.dump(self._lan_pending_answers, f, ensure_ascii=False)
                except Exception:
                    pass
                logger.info("无网络连接，答卷已本地缓存")
                self._fallback_local_cache({"exam_id": self.exam_id, "answers": dict(self.answers), "submitted": list(self.submitted)})
                return
            if not self.network_client.is_connected:
                self._lan_pending_answers = dict(self.answers)
                self._lan_disconnected = True
                os.makedirs(EXAM_CACHE_DIR, exist_ok=True)
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        _json_lan.dump(self._lan_pending_answers, f, ensure_ascii=False)
                except Exception:
                    pass
                logger.info("网络断开，答卷已本地缓存，等待重连后自动上传")
                self._fallback_local_cache({"exam_id": self.exam_id, "answers": dict(self.answers), "submitted": list(self.submitted)})
                return
            if self._lan_pending_answers:
                logger.info("重连成功，上传断线期间缓存的答卷")
                self._lan_pending_answers = {}
                # 清除磁盘缓存
                if os.path.exists(cache_file):
                    try:
                        os.remove(cache_file)
                    except Exception:
                        pass
            FILE_TYPES = ("programming", "web_design", "graphic_design")
            temp_submit_dir = os.path.join(
                EXAM_CACHE_DIR,
                "submit_packages",
                datetime.now().strftime("%Y%m%d%H%M%S%f")
            )
            file_packages = []
            file_upload_total = 0

            # 构建答案 JSON
            answers_data = {}
            for q in self.questions:
                qid = q["_qid"]
                if qid not in self.answers:
                    continue

                q_type = q.get("_type") or q.get("type", "")
                if q_type in FILE_TYPES and qid in self.submitted:
                    package = self._build_file_submission_package(q, temp_submit_dir)
                    if package:
                        if package.get("too_large"):
                            answers_data[str(qid)] = {
                                "_type": "local_file_too_large",
                                "q_type": q_type,
                                "size": package.get("size", 0),
                                "limit": package.get("limit", MAX_STUDENT_UPLOAD_SIZE),
                                "source_dir": package.get("source_dir", ""),
                                "note": "交卷文件超过1GB限制，未通过局域网上传，请教师从学生机本地拷贝。",
                            }
                        elif file_upload_total + package.get("source_size", package["size"]) > MAX_STUDENT_UPLOAD_SIZE:
                            answers_data[str(qid)] = {
                                "_type": "local_file_too_large",
                                "q_type": q_type,
                                "size": package.get("source_size", package["size"]),
                                "limit": MAX_STUDENT_UPLOAD_SIZE,
                                "source_dir": package.get("source_dir", ""),
                                "note": "本次交卷文件总量超过1GB限制，未通过局域网上传，请教师从学生机本地拷贝。",
                            }
                        else:
                            file_upload_total += package.get("source_size", package["size"])
                            file_packages.append(package)
                            answers_data[str(qid)] = {
                                "_type": "file_stream",
                                "q_type": q_type,
                                "upload_id": package["upload_id"],
                                "size": package["size"],
                                "sha256": package["sha256"],
                            }
                    else:
                        answers_data[str(qid)] = "已提交"
                else:
                    answers_data[str(qid)] = self.answers[qid]
            submit_payload = {
                "exam_id": self.exam_id,
                "answers": answers_data,
                "submitted": list(self.submitted),
            }

            # 生成答题记录文档，随提交数据上传到教师端
            try:
                doc_text = self._generate_answer_document()
                submit_payload["answer_document"] = doc_text
            except Exception as e:
                print(f"[答题记录] 生成失败: {e}")

            # 尝试发送给教师端
            sent_ok = False
            if self.network_client and self.network_client.is_connected:
                try:
                    if self._upload_file_submission_packages(file_packages):
                        sent_ok = self.network_client.send("SUBMIT", submit_payload)
                except Exception as e:
                    print(f"[交卷] 发送失败: {e}", flush=True)
                    sent_ok = False

            if sent_ok:
                # 等待教师端 ACK，5 秒超时后降级为本地缓存
                self.timer_running = False

                def _ack_timeout():
                    """SUBMIT_ACK 超时，降级为本地缓存交卷"""
                    if self._submit_ack_timeout_id is not None:
                        self._submit_ack_timeout_id = None
                        self._fallback_local_cache(submit_payload)

                self._submit_ack_timeout_id = self.root.after(5000, _ack_timeout)
            else:
                # 网络异常，缓存到本地
                self._fallback_local_cache(submit_payload)

            try:
                if os.path.isdir(temp_submit_dir):
                    shutil.rmtree(temp_submit_dir, ignore_errors=True)
            except Exception:
                pass

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[交卷] _do_lan_submit 异常: {e}", flush=True)
            try:
                if "temp_submit_dir" in locals() and os.path.isdir(temp_submit_dir):
                    shutil.rmtree(temp_submit_dir, ignore_errors=True)
            except Exception:
                pass
            # 即使出错也降级为本地缓存，确保学生能看到交卷结果
            self._fallback_local_cache({
                "exam_id": self.exam_id,
                "answers": {str(q["_qid"]): self.answers.get(q["_qid"], "") for q in self.questions},
                "submitted": list(self.submitted),
            })

    def _get_file_submission_source_dir(self, q):
        """找到文件类题目当前作答文件夹。"""
        q_type = q.get("_type") or q.get("type", "")
        qid = q.get("_qid", "")
        prog_map = getattr(self, '_prog_folder_map', {})
        dw_map = getattr(self, '_dw_folder_map', {})
        ps_map = getattr(self, '_ps_folder_map', {})
        folder_map = {
            "programming": os.path.join(r"C:\ITSData", prog_map.get(qid, "C")),
            "web_design": os.path.join(r"C:\ITSData", dw_map.get(qid, "DW")),
            "graphic_design": os.path.join(r"C:\ITSData", ps_map.get(qid, "PS")),
        }
        source_dir = folder_map.get(q_type)
        return source_dir if source_dir and os.path.isdir(source_dir) else ""

    def _build_file_submission_package(self, q, temp_dir):
        """把单个文件题作答目录打成临时 zip，供分块上传。"""
        q_type = q.get("_type") or q.get("type", "")
        qid = q.get("_qid", "")
        source_dir = self._get_file_submission_source_dir(q)
        if not source_dir:
            return None

        all_files = []
        source_size = 0
        for root_dir, _dirs, files in os.walk(source_dir):
            for fname in files:
                file_path = os.path.join(root_dir, fname)
                if os.path.isfile(file_path):
                    all_files.append(file_path)
                    source_size += os.path.getsize(file_path)
        if not all_files:
            return None
        if source_size > MAX_STUDENT_UPLOAD_SIZE:
            return {
                "too_large": True,
                "qid": qid,
                "q_type": q_type,
                "source_dir": source_dir,
                "size": source_size,
                "limit": MAX_STUDENT_UPLOAD_SIZE,
            }

        os.makedirs(temp_dir, exist_ok=True)
        upload_id = (
            f"{self.exam_id}_{qid}_{q_type}_"
            f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{random.randint(1000, 9999)}"
        )
        safe_upload = "".join(c if c.isalnum() or c in "-_." else "_" for c in upload_id)
        zip_path = os.path.join(temp_dir, f"{safe_upload}.zip")
        file_count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in all_files:
                arcname = os.path.relpath(file_path, source_dir)
                zf.write(file_path, arcname)
                file_count += 1
        if file_count <= 0:
            try:
                os.remove(zip_path)
            except OSError:
                pass
            return None
        zip_size = os.path.getsize(zip_path)
        if zip_size > MAX_STUDENT_UPLOAD_SIZE:
            try:
                os.remove(zip_path)
            except OSError:
                pass
            return {
                "too_large": True,
                "qid": qid,
                "q_type": q_type,
                "source_dir": source_dir,
                "size": zip_size,
                "limit": MAX_STUDENT_UPLOAD_SIZE,
            }

        digest = hashlib.sha256()
        with open(zip_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return {
            "upload_id": upload_id,
            "qid": qid,
            "q_type": q_type,
            "source_dir": source_dir,
            "source_size": source_size,
            "path": zip_path,
            "size": zip_size,
            "sha256": digest.hexdigest(),
            "file_count": file_count,
        }

    def _upload_file_submission_packages(self, packages, chunk_size=512 * 1024):
        """把交卷文件包分块上传到教师端。"""
        if not packages:
            return True
        total_size = sum(p.get("size", 0) for p in packages)
        sent_total = 0
        last_bucket = -1
        total_mb = total_size / 1024 / 1024
        self._show_lan_transfer_status(f"正在上传交卷文件：0%（约 {total_mb:.1f}MB）")

        for package in packages:
            start_data = {
                "upload_id": package["upload_id"],
                "name": self.user_name,
                "exam_id": self.exam_id,
                "qid": package["qid"],
                "q_type": package["q_type"],
                "size": package["size"],
                "sha256": package["sha256"],
                "file_count": package["file_count"],
                "chunk_size": chunk_size,
            }
            if not self.network_client.send("SUBMIT_FILE_START", start_data):
                return False

            seq = 0
            with open(package["path"], "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    data = {
                        "upload_id": package["upload_id"],
                        "seq": seq,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    }
                    if not self.network_client.send("SUBMIT_FILE_CHUNK", data):
                        return False
                    sent_total += len(chunk)
                    seq += 1
                    if total_size > 0:
                        percent = min(100, int(sent_total * 100 / total_size))
                        bucket = percent // 5
                        if bucket != last_bucket:
                            last_bucket = bucket
                            self._show_lan_transfer_status(f"正在上传交卷文件：{percent}%")

            end_data = {
                "upload_id": package["upload_id"],
                "chunks": seq,
                "size": package["size"],
                "sha256": package["sha256"],
            }
            if not self.network_client.send("SUBMIT_FILE_END", end_data):
                return False

        self._show_lan_transfer_status("交卷文件上传完成，正在提交答卷...")
        return True

    def _collect_file_submission(self, q):
        """将文件类题目的答题文件夹打包为 zip → base64 字符串。

        Args:
            q: 题目字典

        Returns:
            str or None: base64 编码的 zip 数据，文件夹为空时返回 None
        """
        source_dir = self._get_file_submission_source_dir(q)
        if not source_dir:
            return None

        # 收集所有文件
        all_files = []
        for root_dir, dirs, files in os.walk(source_dir):
            for f in files:
                all_files.append(os.path.join(root_dir, f))

        if not all_files:
            return None

        # 打包为内存 zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in all_files:
                arcname = os.path.relpath(file_path, source_dir)
                # 跳过过大文件（单个 > 10MB）
                if os.path.getsize(file_path) > 10 * 1024 * 1024:
                    continue
                zf.write(file_path, arcname)

        if len(zf.namelist()) == 0:
            return None

        buf.seek(0)
        return base64.b64encode(buf.read()).decode("ascii")

    def _fallback_local_cache(self, submit_payload):
        """降级：将答卷缓存到本地文件并显示提示界面"""
        os.makedirs(EXAM_CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(EXAM_CACHE_DIR, f"{self.exam_id}_answers.json")
        archive_dir = os.path.join(EXAM_FILES_DIR, f"ITSData_{self.exam_id}")

        try:
            self._archive_and_clear_its_data()
        except Exception as e:
            print(f"[ITSData] 本地缓存归档失败: {e}")

        cache_payload = dict(submit_payload or {})
        cache_payload["local_archive_dir"] = archive_dir
        answers = dict(cache_payload.get("answers") or {})
        for answer_key, answer in list(answers.items()):
            if isinstance(answer, dict) and answer.get("_type") == "file_stream":
                local_answer = dict(answer)
                local_answer["_type"] = "local_file_archive"
                local_answer["archive_dir"] = archive_dir
                local_answer["note"] = "网络异常，本题文件已保存到本机归档目录"
                answers[answer_key] = local_answer
        cache_payload["answers"] = answers

        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # 局域网断线时，也保存一份答题记录文档到本地
        try:
            os.makedirs(ANSWER_RECORDS_DIR, exist_ok=True)
            doc_filename = f"{self.exam_id}_答题记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            doc_path = os.path.join(ANSWER_RECORDS_DIR, doc_filename)
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(cache_payload.get("answer_document", ""))
        except Exception:
            pass

        self.timer_running = False
        # 清理答题锁定和悬浮窗
        self._unlock_question()
        self._clear_window()
        sw, sh = self._screen_size()
        cx = sw // 2

        canvas = tk.Canvas(self.root, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._create_gradient_bg(canvas, sw, sh, "#1a6fb5", "#0d4f8a")

        canvas.create_text(cx, int(sh * 0.35), text="网络异常，答卷已本地保存",
                          fill="#ffcc00", font=("微软雅黑", 28, "bold"))
        canvas.create_text(cx, int(sh * 0.45), text=f"缓存路径：{cache_path}",
                          fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 13))

        tk.Button(canvas, text="退出系统", font=("微软雅黑", 14, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=30, pady=8,
                 cursor="hand2", command=self.root.quit).place(x=cx, y=int(sh * 0.55), anchor="center")

        # 60 秒倒计时自动退出
        self._start_countdown(canvas, cx, int(sh * 0.65))

    def _generate_answer_document(self, score=None, results=None):
        """生成考试答题记录文档文本。

        Args:
            score: 客观题得分（可选）
            results: 批改结果列表（可选）

        Returns:
            str: 答题记录文档全文
        """
        TYPE_TITLES = {
            "single_choice": "一、单选题",
            "fill_blank": "二、选择填空题",
            "programming": "三、编程题",
            "web_design": "四、网页制作题",
            "network_device": "五、网络设备题",
            "graphic_design": "六、图形图像题",
        }
        TYPE_ORDER = ["single_choice", "fill_blank", "programming",
                      "web_design", "network_device", "graphic_design"]

        # 按题型分组
        groups = {t: [] for t in TYPE_ORDER}
        for q in self.questions:
            qt = q.get("type", "")
            if qt in groups:
                groups[qt].append(q)

        # 构建批改结果索引
        result_map = {}
        if results:
            for r in results:
                result_map[r["qid"]] = r

        lines = []
        lines.append("=" * 50)
        lines.append("              考试答题记录")
        lines.append("=" * 50)
        lines.append(f"准考证号：{self.exam_id}")
        lines.append(f"姓    名：{self.user_name}")
        lines.append(f"交卷时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        total_q = len(self.questions)
        answered = sum(1 for q in self.questions if self._is_answered(q))
        lines.append(f"总题数：{total_q}  已作答：{answered}")
        if score is not None:
            lines.append(f"客观题得分：{score} 分")
        lines.append("=" * 50)
        lines.append("")

        for qtype in TYPE_ORDER:
            qs = groups[qtype]
            if not qs:
                continue

            lines.append(TYPE_TITLES[qtype])
            lines.append("-" * 50)

            for i, q in enumerate(qs, 1):
                qid = q["_qid"]
                content = q.get("content", "")
                # 题干取前80字预览
                content_brief = content.replace("\n", " ")[:80]
                if len(content) > 80:
                    content_brief += "…"
                lines.append(f"{i}. [{content_brief}]")
                lines.append(f"   分值：{q.get('score', 0)}分")

                ans = self.answers.get(qid)

                if qtype == "single_choice":
                    # 单选题：ans 是选项索引
                    options = q.get("options", [])
                    if ans is not None and isinstance(ans, int) and 0 <= ans < len(options):
                        lines.append(f"   你的选择：{options[ans]}")
                    else:
                        lines.append("   你的选择：未作答")
                    # 正确答案
                    ref = q.get("answer", -1)
                    if isinstance(ref, int) and 0 <= ref < len(options):
                        lines.append(f"   正确答案：{options[ref]}")
                    r = result_map.get(qid)
                    if r:
                        lines.append(f"   结果：{r.get('status', '')}")

                elif qtype == "fill_blank":
                    # 选择填空题：ans 是字典 {"【1】": "A", ...}
                    shared_opts = q.get("shared_options", [])
                    blanks = q.get("blanks", [])
                    if isinstance(ans, dict) and ans:
                        for blank in blanks:
                            lbl = blank.get("label", "")
                            user_letter = ans.get(lbl, "")
                            if user_letter:
                                # 尝试找到选项内容
                                letter_idx = ord(user_letter.upper()) - ord('A')
                                opt_text = shared_opts[letter_idx] if 0 <= letter_idx < len(shared_opts) else ""
                                lines.append(f"   {lbl} 你的选择：{user_letter} - {opt_text}")
                            else:
                                lines.append(f"   {lbl} 未作答")
                    else:
                        lines.append("   未作答")
                    # 正确答案
                    ref = q.get("answer", q.get("reference_answer", ""))
                    if isinstance(ref, dict):
                        for blank in blanks:
                            lbl = blank.get("label", "")
                            clean_key = lbl.strip().replace("【", "").replace("】", "").replace("[", "").replace("]", "").strip()
                            ref_val = ref.get(clean_key, ref.get(lbl, ""))
                            if ref_val:
                                lines.append(f"   {lbl} 正确答案：{ref_val}")
                    r = result_map.get(qid)
                    if r:
                        lines.append(f"   结果：{r.get('status', '')}")

                elif qtype == "network_device":
                    # 网络设备题：每个字段生成一段
                    text_fields = q.get("text_fields", [])
                    if isinstance(ans, dict) and ans:
                        for tf in text_fields:
                            lbl = tf.get("label", "")
                            content_val = ans.get(lbl, "")
                            lines.append(f"   {lbl}：")
                            if content_val:
                                # 每行内容缩进显示
                                for content_line in content_val.split("\n"):
                                    lines.append(f"     {content_line}")
                            else:
                                lines.append("     （未填写）")
                            lines.append("")  # 空行分隔
                    else:
                        lines.append("   未作答")

                else:
                    # programming / web_design / graphic_design：文件提交类
                    if qid in self.submitted:
                        lines.append("   已提交文件")
                    else:
                        lines.append("   未提交")

                lines.append("")  # 题目间空行

            lines.append("")

        lines.append("=" * 50)
        lines.append("              答题记录结束")
        lines.append("=" * 50)
        return "\n".join(lines)

    def _save_answer_files(self, timestamp_str):
        """交卷后保存作答文件到答题记录文件夹。

        注意：_do_submit 开头会调用 _archive_and_clear_its_data() 清空 C:\\ITSData，
        所以这里优先从归档目录读取，找不到再回退到 C:\\ITSData。

        - 编程题：复制 prog.c 文件，文件名加时间戳
        - 网页设计题(DW)：复制整个文件夹
        - 图形图像题(PS)：复制整个文件夹
        """
        import shutil
        os.makedirs(ANSWER_RECORDS_DIR, exist_ok=True)

        # 归档目录：exam_files/ITSData_{准考证号}/（交卷时已将 C:\ITSData 完整复制到此）
        archive_dir = os.path.join(EXAM_FILES_DIR, f"ITSData_{self.exam_id}")
        its_dir = r"C:\ITSData"

        def _find_source(subfolder):
            """优先从归档目录找，找不到回退到 C:\\ITSData"""
            p = os.path.join(archive_dir, subfolder)
            if os.path.exists(p):
                return p
            p = os.path.join(its_dir, subfolder)
            if os.path.exists(p):
                return p
            return None

        for q in self.questions:
            qid = q["_qid"]
            qtype = q.get("type", q.get("_type", ""))

            if qtype == "programming":
                prog_folder = self._prog_folder_map.get(qid, "C")
                src_dir = _find_source(prog_folder)
                if src_dir:
                    src_path = os.path.join(src_dir, "prog.c")
                    if os.path.isfile(src_path):
                        dst_name = f"{self.exam_id}_编程题_{timestamp_str}.c"
                        dst_path = os.path.join(ANSWER_RECORDS_DIR, dst_name)
                        try:
                            shutil.copy2(src_path, dst_path)
                        except Exception as e:
                            print(f"[答题文件] 编程题复制失败: {e}")

            elif qtype == "web_design":
                dw_folder = self._dw_folder_map.get(qid, "DW")
                src_dir = _find_source(dw_folder)
                if src_dir and os.path.isdir(src_dir):
                    dst_name = f"{self.exam_id}_DW_{timestamp_str}"
                    dst_path = os.path.join(ANSWER_RECORDS_DIR, dst_name)
                    if os.path.exists(dst_path):
                        dst_path = dst_path + "_1"
                    try:
                        shutil.copytree(src_dir, dst_path)
                    except Exception as e:
                        print(f"[答题文件] DW文件夹复制失败: {e}")

            elif qtype == "graphic_design":
                ps_folder = self._ps_folder_map.get(qid, "PS")
                src_dir = _find_source(ps_folder)
                if src_dir and os.path.isdir(src_dir):
                    dst_name = f"{self.exam_id}_PS_{timestamp_str}"
                    dst_path = os.path.join(ANSWER_RECORDS_DIR, dst_name)
                    if os.path.exists(dst_path):
                        dst_path = dst_path + "_1"
                    try:
                        shutil.copytree(src_dir, dst_path)
                    except Exception as e:
                        print(f"[答题文件] PS文件夹复制失败: {e}")

    def auto_submit(self):
        if self.lan_mode:
            self._do_lan_submit()
            return
        messagebox.showinfo("考试结束", "考试时间到，系统将自动交卷！")
        self._do_submit()

    def _do_submit(self):
        self.timer_running = False
        self._archive_and_clear_its_data()
        # 清理答题锁定和悬浮窗
        self._unlock_question()
        self._clear_window()
        sw, sh = self._screen_size()
        cx = sw // 2

        canvas = tk.Canvas(self.root, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._create_gradient_bg(canvas, sw, sh, "#1a6fb5", "#0d4f8a")

        # 标题
        canvas.create_text(cx, int(sh * 0.16), text="交 卷 成 功",
                          fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 36, "bold"))

        # 绿色对勾
        check_r = 50
        canvas.create_oval(cx - check_r, int(sh * 0.27) - check_r,
                          cx + check_r, int(sh * 0.27) + check_r,
                          fill=COLOR_SUCCESS, outline="")
        canvas.create_text(cx, int(sh * 0.27), text="✓",
                          fill=COLOR_WHITE, font=("微软雅黑", 50, "bold"))

        answered = sum(1 for q in self.questions if self._is_answered(q))
        submitted = len(self.submitted)

        lines = [
            f"准考证号：{self.exam_id}",
            f"交卷时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"总题数：{len(self.questions)}  已作答：{answered}  已提交：{submitted}",
        ]

        # 批改结果
        score, results = self._grade_exam()

        # 生成答题记录文档 + 保存作答文件
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        try:
            doc_text = self._generate_answer_document(score, results)
            os.makedirs(ANSWER_RECORDS_DIR, exist_ok=True)
            doc_filename = f"{self.exam_id}_答题记录_{timestamp_str}.txt"
            doc_path = os.path.join(ANSWER_RECORDS_DIR, doc_filename)
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(doc_text)
        except Exception as e:
            print(f"[答题记录] 保存失败: {e}")

        # 保存作答文件到答题记录文件夹
        try:
            self._save_answer_files(timestamp_str)
        except Exception as e:
            print(f"[答题文件] 保存失败: {e}")

        if score is not None:
            lines.append(f"客观题得分：{score}分")
            lines.append("考试结果已保存，感谢您的参与！")
        else:
            lines.append("考试结果已保存，感谢您的参与！")

        # 信息文字：白色文字，统一左对齐，整体居中
        left_x = cx - 230
        y_start = int(sh * 0.40)
        line_height = 32

        for i, line in enumerate(lines):
            canvas.create_text(left_x, y_start + i * line_height, text=line,
                              fill=COLOR_TEXT_LIGHT, font=("微软雅黑", 13),
                              anchor="w")

        # 查看结果按钮
        btn_y = y_start + len(lines) * line_height + 60
        if score is not None:
            tk.Button(canvas, text="查看批改结果", font=("微软雅黑", 14, "bold"),
                     bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=30, pady=8,
                     cursor="hand2", command=lambda: self._show_exam_result(score, results)).place(x=cx - 180, y=btn_y, anchor="center")
        tk.Button(canvas, text="返回登录", font=("微软雅黑", 14, "bold"),
                 bg="#e0e0e0", fg=COLOR_TEXT_DARK, bd=0, padx=30, pady=8,
                 cursor="hand2", command=self.show_login).place(x=cx, y=btn_y, anchor="center")
        tk.Button(canvas, text="退出系统", font=("微软雅黑", 14, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=30, pady=8,
                 cursor="hand2", command=self.root.quit).place(x=cx + 180, y=btn_y, anchor="center")

        

    # ==================== 题库刷题模式 ====================
    def start_practice(self):
        self.question_bank = load_question_bank()
        self._merge_unlocked_questions()  # 过滤锁定题
        self._build_all_questions_for_practice()
        if not self.questions:
            messagebox.showwarning("提示", "题库为空，请先上传题目或使用默认题库！")
            return
        self.mode = "practice"
        self.current_question = 0
        self.answers = {}
        self.practice_answers = {}
        self.show_revealed = set()
        self._locked_question = None
        self._float_window = None
        self.practice_stats = {"correct": 0, "wrong": 0, "total": len(self.questions)}
        # ★ 刷题模式：只填充映射表，不预创建文件夹（点击"打开考试文件夹"时按需创建）
        self._init_prog_folder_map()
        self._show_practice_interface_impl()

    def _build_all_questions_for_practice(self, filter_params=None):
        """将题库题目按类型顺序展开为刷题列表，可选筛选条件"""
        bank = self.question_bank
        qid = 1
        questions = []
        allowed_types = filter_params.get("types", None) if filter_params else None
        keyword = (filter_params.get("keyword", "").strip().lower()) if filter_params else ""
        src_filter = (filter_params.get("source", "").strip().lower()) if filter_params else ""
        for qtype in ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]:
            if allowed_types and qtype not in allowed_types:
                continue
            for idx, q in enumerate(bank.get(qtype, [])):
                # 练习模式下不过滤锁定题目，而是标记后显示
                ct = q.get("content", "")
                src = q.get("source", "").lower()
                if keyword and keyword not in ct.lower():
                    continue
                if src_filter and src_filter not in src:
                    continue
                qc = dict(q)
                qc["_qid"] = qid
                qc["type"] = qtype
                qc["_bank_idx"] = idx
                qc["title"] = {"single_choice": "C语言单选题",
                              "fill_blank": "C语言选择填空题",
                              "programming": "C语言编程题",
                              "web_design": "网页制作操作题",
                              "network_device": "网络设备安装与调试",
                              "graphic_design": "图形图像处理操作题"}[qtype]
                questions.append(qc)
                qid += 1
        self.questions = questions

    def _build_exam_questions(self):
        """根据配置从题库中选题（优先使用精准选题），按题型顺序排列"""
        import random
        bank = self.question_bank
        selected_qids = getattr(self, 'selected_qids', {})
        questions = []
        qid = 1

        # 按题型固定顺序：单选 → 填空 → 编程 → 网页 → 网络设备 → 图形图像
        type_order = ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]
        for qtype in type_order:
            count = self.question_config.get(qtype, 0)
            if count <= 0:
                continue
            pool = [q for q in bank.get(qtype, []) if not q.get("_locked")]
            if not pool:
                continue

            sids = selected_qids.get(qtype, [])

            if sids:
                # 精准选题：按用户选定的ID顺序出题
                id_to_q = {}
                for i, q in enumerate(pool):
                    qid_val = q.get("id", i + 1000)
                    id_to_q[qid_val] = q

                found = []
                for sid in sids:
                    if sid in id_to_q:
                        qc = dict(id_to_q[sid])
                        qc["_qid"] = qid
                        qc["type"] = qtype
                        qc["title"] = {"single_choice": "C语言单选题",
                                      "fill_blank": "C语言选择填空题",
                                      "programming": "C语言编程题",
                                      "web_design": "网页制作操作题",
                                      "network_device": "网络设备安装与调试",
                                      "graphic_design": "图形图像处理操作题"}[qtype]
                        questions.append(qc)
                        qid += 1
                        found.append(qc)
                        if len(found) >= count:
                            break

                # 精准选题数量不足时，随机补足
                if len(found) < count:
                    used_ids = {q.get("id", 0) for q in found}
                    remainder = [q for q in pool if q.get("id", 0) not in used_ids]
                    need = count - len(found)
                    if remainder and need > 0:
                        if len(remainder) <= need:
                            extras = remainder
                        else:
                            extras = random.sample(remainder, need)
                        for q in extras:
                            qc = dict(q)
                            qc["_qid"] = qid
                            qc["type"] = qtype
                            qc["title"] = {"single_choice": "C语言单选题",
                                          "fill_blank": "C语言选择填空题",
                                          "programming": "C语言编程题",
                                          "web_design": "网页制作操作题",
                                          "network_device": "网络设备安装与调试",
                                          "graphic_design": "图形图像处理操作题"}[qtype]
                            questions.append(qc)
                            qid += 1
            else:
                # 未设置精准选题：随机抽取指定数量题目
                if len(pool) <= count:
                    selected = pool
                else:
                    selected = random.sample(pool, count)

                for q in selected:
                    qc = dict(q)
                    qc["_qid"] = qid
                    qc["type"] = qtype
                    qc["title"] = {"single_choice": "C语言单选题",
                                  "fill_blank": "C语言选择填空题",
                                  "programming": "C语言编程题",
                                  "web_design": "网页制作操作题",
                                  "network_device": "网络设备安装与调试",
                                  "graphic_design": "图形图像处理操作题"}[qtype]
                    questions.append(qc)
                    qid += 1

        self.questions = questions

    def _show_practice_interface_impl(self):
        # ★ 重置 Canvas 复用状态，防止退出后再次进入时引用已销毁的旧 Canvas
        self._pf_canvas = None
        self._pf_canvas_window = None
        self._pf_frame = None
        self._pf_outer = None
        self._pf_scroll = None
        self._pf_info = None
        self._pf_nav = None

        self._clear_window()
        self.root.deiconify()
        self.root.state('zoomed')

        top_bar = tk.Frame(self.root, bg="#2e7d32", height=50)
        top_bar.pack(fill="x")
        top_bar.pack_propagate(False)

        tk.Label(top_bar, text="题库刷题模式 — 顺序刷题，点击'查看答案'核对正确率",
                fg=COLOR_WHITE, bg="#2e7d32", font=("微软雅黑", 13, "bold")).pack(side="left", padx=15, pady=10)

        self.practice_stat_var = tk.StringVar()
        self._update_practice_stats_display()
        tk.Label(top_bar, textvariable=self.practice_stat_var, fg="#c8e6c9",
                bg="#2e7d32", font=("微软雅黑", 11)).pack(side="right", padx=15, pady=10)

        # === 刷题筛选栏 ===
        self.filter_bar = tk.Frame(self.root, bg="#e8f5e9", height=36)
        self.filter_bar.pack(fill="x")
        self.filter_bar.pack_propagate(False)

        filter_inner = tk.Frame(self.filter_bar, bg="#e8f5e9")
        filter_inner.pack(fill="x", padx=15, pady=4)

        # 题型勾选
        tk.Label(filter_inner, text="题型：", font=("微软雅黑", 9), bg="#e8f5e9").pack(side="left")
        self.filter_type_vars = {}
        for key, lbl in [("single_choice", "单选"), ("fill_blank", "填空"),
                         ("programming", "编程"), ("web_design", "网页"),
                         ("network_device", "网络"), ("graphic_design", "图像")]:
            var = tk.BooleanVar(value=True)
            self.filter_type_vars[key] = var
            cb = tk.Checkbutton(filter_inner, text=lbl, variable=var,
                               font=("微软雅黑", 9), bg="#e8f5e9",
                               activebackground="#e8f5e9", selectcolor="#c8e6c9",
                               command=self._on_practice_filter_change)
            cb.pack(side="left", padx=2)

        tk.Label(filter_inner, text="", bg="#e8f5e9").pack(side="left", padx=8)  # spacer

        # 关键词
        tk.Label(filter_inner, text="关键词：", font=("微软雅黑", 9), bg="#e8f5e9").pack(side="left")
        self.filter_kw_var = tk.StringVar()
        kw_e = tk.Entry(filter_inner, textvariable=self.filter_kw_var, font=("微软雅黑", 9),
                       width=12, bd=1, relief="solid")
        kw_e.pack(side="left", padx=2)
        kw_e.bind("<KeyRelease>", self._on_practice_filter_change)

        # 来源
        tk.Label(filter_inner, text="来源：", font=("微软雅黑", 9), bg="#e8f5e9").pack(side="left", padx=(8, 0))
        self.filter_src_var = tk.StringVar()
        src_e = tk.Entry(filter_inner, textvariable=self.filter_src_var, font=("微软雅黑", 9),
                        width=10, bd=1, relief="solid")
        src_e.pack(side="left", padx=2)
        src_e.bind("<KeyRelease>", self._on_practice_filter_change)

        # 应用按钮
        tk.Button(filter_inner, text="筛选", font=("微软雅黑", 9, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=10, pady=1,
                 cursor="hand2", command=self._apply_practice_filter).pack(side="left", padx=(10, 0))

        # 筛选结果数量
        self.filter_count_var = tk.StringVar()
        tk.Label(filter_inner, textvariable=self.filter_count_var,
                font=("微软雅黑", 9), fg=COLOR_ACCENT, bg="#e8f5e9").pack(side="left", padx=(8, 0))

        # 初始显示全部题目数
        self.filter_count_var.set(f"共 {len(self.questions)} 题")

        main_frame = tk.Frame(self.root, bg=COLOR_BG_MAIN)
        self.practice_main_frame = main_frame
        main_frame.pack(fill="both", expand=True)

        # 防抖定时器
        self._filter_timer = None

        # 左侧题库列表
        self._create_practice_sidebar(main_frame)
        # 右侧题目区域
        self.question_frame = tk.Frame(main_frame, bg=COLOR_BG_MAIN)
        self.question_frame.pack(side="left", fill="both", expand=True)

        bottom = tk.Frame(self.root, bg="#2e7d32", height=30)
        bottom.pack(fill="x")
        bottom.pack_propagate(False)
        tk.Label(bottom, text="F4 上一题  F5 下一题  |  点击'查看答案'后选项高亮显示正确答案",
                fg="#a5d6a7", bg="#2e7d32", font=("微软雅黑", 9)).pack(side="left", padx=15, pady=5)

        self.root.bind("<F4>", lambda e: self._practice_prev())
        self.root.bind("<F5>", lambda e: self._practice_next())
        self._display_practice_question_impl(0)

    def _on_practice_filter_change(self, event=None):
        """筛选条件变化时防抖更新预览数"""
        if self._filter_timer:
            self.root.after_cancel(self._filter_timer)
        self._filter_timer = self.root.after(300, self._preview_filter_count)

    def _preview_filter_count(self):
        """预览当前筛选条件匹配的题目数"""
        selected = [k for k, v in self.filter_type_vars.items() if v.get()]
        kw = self.filter_kw_var.get().strip().lower()
        src_f = self.filter_src_var.get().strip().lower()
        cnt = 0
        for qt in ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]:
            if selected and qt not in selected:
                continue
            for q in self.question_bank.get(qt, []):
                ct = q.get("content", "")
                src = q.get("source", "")
                if kw and kw not in ct.lower():
                    continue
                if src_f and src_f not in src.lower():
                    continue
                cnt += 1
        self.filter_count_var.set(f"共 {cnt} 题")

    def _apply_practice_filter(self):
        """应用筛选条件，重建题目列表并刷新界面"""
        selected = [k for k, v in self.filter_type_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一种题型！")
            return
        filter_params = {
            "types": set(selected),
            "keyword": self.filter_kw_var.get().strip(),
            "source": self.filter_src_var.get().strip(),
        }
        self._build_all_questions_for_practice(filter_params)
        if not self.questions:
            messagebox.showwarning("提示", "没有符合条件的题目，请调整筛选条件！")
            return
        # 重置状态
        self.current_question = 0
        self.answers = {}
        self.practice_answers = {}
        self.show_revealed = set()
        self.practice_stats = {"correct": 0, "wrong": 0, "total": len(self.questions)}
        self._update_practice_stats_display()
        self.filter_count_var.set(f"共 {len(self.questions)} 题")
        # ★ 修复：筛选后重置 Canvas 复用状态，防止引用已销毁的旧 Canvas 导致白屏
        self._pf_canvas = None
        self._pf_canvas_window = None
        self._pf_frame = None
        self._pf_outer = None
        self._pf_scroll = None
        self._pf_info = None
        self._pf_nav = None
        # 重建左侧题库列表
        main_frame = self.practice_main_frame
        for w in main_frame.winfo_children():
            w.destroy()
        self._create_practice_sidebar(main_frame)
        self.question_frame = tk.Frame(main_frame, bg=COLOR_BG_MAIN)
        self.question_frame.pack(side="left", fill="both", expand=True)
        self._display_practice_question(0)
        self._update_practice_sidebar()

    def _create_practice_sidebar_impl(self, parent):
        sidebar = tk.Frame(parent, bg=COLOR_WHITE, width=260, bd=0,
                          highlightbackground="#a5d6a7", highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="题库列表", font=("微软雅黑", 14, "bold"),
                fg="#2e7d32", bg=COLOR_WHITE).pack(pady=(15, 5))

        # 统计
        stats = tk.Frame(sidebar, bg=COLOR_WHITE)
        stats.pack(fill="x", padx=10, pady=5)
        for t, c in [("正确 ✓", "#4CAF50"), ("错误 ✗", "#f44336"), ("未做 ·", "#9e9e9e")]:
            tk.Label(stats, text=t, fg=c, bg=COLOR_WHITE, font=("微软雅黑", 10)).pack(anchor="w", pady=1)

        self.practice_sidebar_buttons = []
        self.practice_collapsed = {}
        self.practice_group_frames = {}

        # 题型分组定义
        type_order = ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]
        group_titles = {
            "single_choice": "一、C语言单选题",
            "fill_blank": "二、C语言选择填空题",
            "programming": "三、C语言编程题",
            "web_design": "四、网页制作操作题",
            "network_device": "五、网络设备安装与调试",
            "graphic_design": "六、图形图像处理操作题"
        }
        grouped = {}
        for i, q in enumerate(self.questions):
            qtype = q["type"]
            if qtype not in grouped:
                grouped[qtype] = []
            grouped[qtype].append((i, q))

        # 带滚动条的题目列表
        self.practice_scroll_canvas = tk.Canvas(sidebar, bg=COLOR_WHITE, highlightthickness=0, width=225)
        scrollbar = tk.Scrollbar(
            sidebar,
            orient="vertical",
            command=self.practice_scroll_canvas.yview,
            width=22,
            bg="#8bc34a",
            activebackground="#689f38",
            troughcolor="#e8f5e9",
            bd=0,
            relief="flat",
        )
        self.practice_btn_frame = tk.Frame(self.practice_scroll_canvas, bg=COLOR_WHITE)
        self.practice_btn_frame.bind("<Configure>", lambda e: self.practice_scroll_canvas.configure(scrollregion=self.practice_scroll_canvas.bbox("all")))
        self.practice_scroll_canvas.create_window((0, 0), window=self.practice_btn_frame, anchor="nw", width=220)
        self.practice_scroll_canvas.configure(yscrollcommand=scrollbar.set)
        self.practice_scroll_canvas.pack(fill="both", expand=True, padx=(10, 0), pady=5)
        scrollbar.pack(side="right", fill="y", pady=5)
        ExamSystem._setup_auto_scroll(self.practice_scroll_canvas, scrollbar, side="right", fill="y", pady=5)

        for qtype in type_order:
            if qtype not in grouped:
                continue
            items = grouped[qtype]
            title = group_titles.get(qtype, qtype)
            self.practice_collapsed[qtype] = False

            # 分组标题行
            header = tk.Frame(self.practice_btn_frame, bg="#e8f5e9", cursor="hand2")
            header.pack(fill="x", pady=(6, 0))
            toggle_cb = lambda e, t=qtype: self._toggle_practice_group(t)
            header.bind("<Button-1>", toggle_cb)

            title_lbl = tk.Label(header, text=f"{title} ({len(items)})", font=("微软雅黑", 9, "bold"),
                    fg="#2e7d32", bg="#e8f5e9")
            title_lbl.pack(side="left", padx=8, pady=4)
            title_lbl.bind("<Button-1>", toggle_cb)

            arrow_var = tk.StringVar(value="▼")
            arrow_label = tk.Label(header, textvariable=arrow_var, font=("微软雅黑", 9),
                                   fg="#2e7d32", bg="#e8f5e9", width=2)
            arrow_label.pack(side="right", padx=5, pady=4)
            arrow_label.bind("<Button-1>", toggle_cb)

            # 按钮区域
            btn_grid = tk.Frame(self.practice_btn_frame, bg=COLOR_WHITE)
            btn_grid.pack(fill="x", padx=5, pady=3)
            for i in range(4):
                btn_grid.columnconfigure(i, weight=1)

            for j, (orig_idx, q) in enumerate(items):
                row_idx, col = j // 4, j % 4
                cell = tk.Frame(btn_grid, bg=COLOR_WHITE)
                cell.grid(row=row_idx, column=col, padx=0, pady=1, sticky="ew")

                # 检查题目是否锁定
                is_locked = q.get("_locked")
                btn_text = f"🔒{orig_idx + 1}" if is_locked else str(orig_idx + 1)
                btn_bg = "#e0e0e0" if is_locked else COLOR_WHITE
                btn_fg = "#9e9e9e" if is_locked else COLOR_UNANSWERED
                
                btn = tk.Button(cell, text=btn_text, font=("微软雅黑", 8),
                               bg=btn_bg, fg=btn_fg, bd=1, relief="solid",
                               cursor="hand2", width=3, height=1,
                               command=lambda idx=orig_idx: self._display_practice_question(idx))
                btn.pack(side="left")

                self.practice_sidebar_buttons.append((btn, None, qtype, orig_idx))

            self.practice_group_frames[qtype] = {
                "header": header,
                "btn_grid": btn_grid,
                "arrow_var": arrow_var,
                "arrow_label": arrow_label
            }

        self.root.after(
            100,
            lambda: ExamSystem._bind_area_mousewheel_to_canvas(
                self.practice_scroll_canvas,
                self.practice_btn_frame,
            )
        )

        tk.Button(sidebar, text="返回登录", font=("微软雅黑", 10),
                 bg="#e8f5e9", fg="#2e7d32", bd=1, relief="solid",
                 cursor="hand2", command=self.show_login).pack(fill="x", padx=15, pady=(5, 15), ipady=5)

    def _create_practice_sidebar(self, parent):
        """转发包装：调用合并后的 _create_sidebar(mode="practice")"""
        self._create_sidebar(parent, mode="practice")
    def _toggle_practice_group(self, qtype):
        if qtype not in self.practice_group_frames:
            return
        info = self.practice_group_frames[qtype]
        self.practice_collapsed[qtype] = not self.practice_collapsed[qtype]
        if self.practice_collapsed[qtype]:
            info["btn_grid"].pack_forget()
            info["arrow_var"].set("▶")
        else:
            info["btn_grid"].pack(fill="x", padx=5, pady=3, after=info["header"])
            info["arrow_var"].set("▼")

    def _display_practice_question_impl(self, index):
        """渲染刷题模式当前题目（Canvas 只创建一次，防止反复销毁重建导致空白）"""
        if not self.questions or index >= len(self.questions):
            return

        # 答题锁定：如果当前有题目处于答题状态，禁止切换
        if self._locked_question is not None:
            q = self.questions[index] if index < len(self.questions) else {}
            if q.get("_qid") != self._locked_question:
                if self._float_window is not None:
                    try:
                        self._float_window.deiconify()
                        self._float_window.lift()
                        self._float_window.focus_force()
                    except Exception:
                        pass
                return

        self.current_question = index
        q = self.questions[index]
        qid = q["_qid"]

        # 检查题目是否锁定
        if q.get("_locked"):
            self._show_locked_message()
            return

        revealed = qid in self.show_revealed

        type_names = {"single_choice": "C语言 · 单选题",
                     "fill_blank": "C语言 · 选择填空题",
                     "programming": "C语言 · 编程题",
                     "web_design": "网页制作 · 操作题",
                     "network_device": "网络设备安装与调试",
                     "graphic_design": "图形图像处理 · 操作题"}

        # ======== 首次渲染：创建所有 UI 组件（只执行一次）======
        if self._pf_canvas is None:
            for widget in self.question_frame.winfo_children():
                widget.destroy()

            # 题号/题型信息栏
            info_frame = tk.Frame(self.question_frame, bg=COLOR_WHITE, bd=0,
                                 highlightbackground="#a5d6a7", highlightthickness=1)
            info_frame.pack(fill="x", padx=10, pady=(10, 5))
            self._pf_info = info_frame

            # 带滚动条的内容区域（Canvas 只创建这一次）
            content_outer = tk.Frame(self.question_frame, bg=COLOR_WHITE, bd=0,
                                    highlightbackground="#a5d6a7", highlightthickness=1)
            content_outer.pack(fill="both", expand=True, padx=10, pady=5)

            content_canvas = tk.Canvas(content_outer, bg=COLOR_WHITE, highlightthickness=0)
            content_scrollbar = tk.Scrollbar(content_outer, orient="vertical", command=content_canvas.yview)
            content_frame = tk.Frame(content_canvas, bg=COLOR_WHITE)

            canvas_window = content_canvas.create_window((0, 0), window=content_frame, anchor="nw")

            # ★ 初始设为大宽度，避免 Configure 事件触发前宽度为 0
            content_canvas.itemconfig(canvas_window, width=10000)

            def _on_content_configure(event, cc=content_canvas, cw=canvas_window):
                try:
                    cc.itemconfig(cw, width=event.width - 20)
                    cc.configure(scrollregion=cc.bbox("all"))
                except tk.TclError:
                    pass

            content_frame.bind("<Configure>", lambda e, cc=content_canvas: (
                cc.configure(scrollregion=cc.bbox("all")) if cc.winfo_exists() else None
            ))
            content_outer.bind("<Configure>", _on_content_configure)

            content_canvas.configure(yscrollcommand=content_scrollbar.set)
            content_canvas.pack(side="left", fill="both", expand=True, padx=(5, 0), pady=5)
            content_scrollbar.pack(side="right", fill="y", pady=5)
            ExamSystem._setup_auto_scroll(content_canvas, content_scrollbar)

            # 存储引用，供后续复用
            self._pf_outer = content_outer
            self._pf_canvas = content_canvas
            self._pf_canvas_window = canvas_window
            self._pf_frame = content_frame
            self._pf_scroll = content_scrollbar

            # 导航栏（也只创建一次，后续只更新）
            nav_frame = tk.Frame(self.question_frame, bg=COLOR_BG_MAIN)
            nav_frame.pack(fill="x", padx=10, pady=(5, 10))
            self._pf_nav = nav_frame

        # ======== 更新信息栏（每题都更新）======
        for widget in self._pf_info.winfo_children():
            widget.destroy()
        status = ""
        if qid in self.show_revealed:
            if q["type"] in ("single_choice", "fill_blank"):
                correct = self._check_practice_answer(q)
                status = "  ✅ 正确" if correct else "  ❌ 错误"
        tk.Label(self._pf_info, text=f"第 {index+1} 题 / 共 {len(self.questions)} 题{status}",
                fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, font=("微软雅黑", 11, "bold")).pack(side="left", padx=15, pady=8)
        tk.Label(self._pf_info, text=f"({type_names.get(q['type'], q.get('title', ''))})  |  分值：{q.get('score', 0)}分",
                fg="#2e7d32", bg=COLOR_WHITE, font=("微软雅黑", 11)).pack(side="left", padx=5, pady=8)

        # ======== 清空内容区并渲染新题目 =======
        for widget in self._pf_frame.winfo_children():
            widget.destroy()

        # ★ 触发 Canvas Configure 事件，确保宽度与容器一致
        try:
            if self._pf_canvas.winfo_exists():
                outer_w = self._pf_outer.winfo_width()
                if outer_w > 10:
                    self._pf_canvas.itemconfig(self._pf_canvas_window, width=outer_w - 20)
        except tk.TclError:
            pass

        if q["type"] == "single_choice":
            self._render_practice_single_choice_impl(self._pf_frame, q, revealed)
        elif q["type"] == "fill_blank":
            self._render_practice_fill_blank_impl(self._pf_frame, q, revealed)
        elif q["type"] == "programming":
            self._render_practice_programming_impl(self._pf_frame, q, revealed)
        elif q["type"] == "web_design":
            self._render_practice_web_design_impl(self._pf_frame, q, revealed)
        elif q["type"] == "network_device":
            self._render_practice_network_device_impl(self._pf_frame, q, revealed)
        elif q["type"] == "graphic_design":
            self._render_practice_graphic_design_impl(self._pf_frame, q, revealed)
        else:
            # 兜底：类型未知时显示错误提示，防止空白页
            print(f"[_display_practice_question] 警告：未知题目类型 q['type']={q['type']!r}, q._type={q.get('_type', 'N/A')!r}", flush=True)
            tk.Label(self._pf_frame, text=f"⚠ 题目类型错误：{q.get('type', '未定义')}", font=("微软雅黑", 14, "bold"),
                    fg=COLOR_DANGER, bg=COLOR_WHITE).pack(padx=20, pady=30)
            tk.Label(self._pf_frame, text="请联系老师确认题目数据是否完整", font=("微软雅黑", 11),
                    fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(padx=20, pady=(0, 20))
            for key in ['type', '_type', 'content', 'id']:
                tk.Label(self._pf_frame, text=f"  {key}: {str(q.get(key, '缺失'))[:100]}",
                        font=("Consolas", 9), fg="#888", bg=COLOR_WHITE, anchor="w").pack(fill="x", padx=30, pady=1)

        # ======== 更新导航栏 =======
        for widget in self._pf_nav.winfo_children():
            widget.destroy()
        tk.Button(self._pf_nav, text="◀ 上一题 (F4)", font=("微软雅黑", 11),
                 bg=COLOR_BTN, fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                 cursor="hand2", command=self._practice_prev).pack(side="left")
        tk.Button(self._pf_nav, text="下一题 (F5) ▶", font=("微软雅黑", 11),
                 bg=COLOR_BTN, fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                 cursor="hand2", command=self._practice_next).pack(side="right")

        if q["type"] in ("single_choice", "fill_blank") and not revealed:
            tk.Button(self._pf_nav, text="参考答案", font=("微软雅黑", 11, "bold"),
                     bg=COLOR_WARNING, fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                     cursor="hand2", command=lambda idx=index: self._reveal_answer(idx)).pack(side="left", padx=20)

        # ======== 修复 Canvas 滚动区域（只更新滚动范围，不改变宽度） =======
        def _fix_scroll():
            try:
                if self._pf_canvas.winfo_exists():
                    self._pf_canvas.configure(scrollregion=self._pf_canvas.bbox("all"))
            except tk.TclError:
                pass

        self._pf_frame.update_idletasks()
        self._pf_canvas.after(0, _fix_scroll)
        self._pf_canvas.after(50, _fix_scroll)
        self._pf_canvas.after(200, _fix_scroll)

        self._update_practice_sidebar_impl()
    def _show_locked_message(self):
        """显示题目未解锁提示"""
        messagebox.showinfo("题目未解锁", 
            "题目未解锁，请在登录页面扫描二维码加作者获取解锁码（免费获取）。")

    def _display_practice_question(self, index):
        """转发包装：调用合并后的 _display_question(index, mode="practice")"""
        self._display_question(index, mode="practice")
    def _render_practice_single_choice_impl(self, parent, q, revealed):
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 题干
        _lbl = tk.Label(parent, text=q.get("content", ""),
                       font=("微软雅黑", 13), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        # 如果题目包含代码块，添加复制代码按钮
        content = q.get("content", "")
        if self._has_code_block(content):
            btn_row = tk.Frame(parent, bg=COLOR_WHITE)
            btn_row.pack(fill="x", padx=20, pady=(5, 0))
            code_text = self._extract_code(content)
            tk.Button(btn_row, text="📋 复制代码", font=("微软雅黑", 10),
                     bg="#e3f2fd", fg=COLOR_ACCENT, bd=1, relief="solid",
                     cursor="hand2", padx=10, pady=3,
                     command=lambda c=code_text: self._copy_code(c)).pack(side="left")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)

        qid = q["_qid"]
        # 确保 correct 是有效整数
        correct = q.get("answer", -1)
        if isinstance(correct, str):
            if correct.isalpha() and len(correct) == 1:
                correct = ord(correct.upper()) - ord('A')
            else:
                try:
                    correct = int(correct)
                except (ValueError, TypeError):
                    correct = -1
        if not isinstance(correct, int) or correct < 0:
            correct = -1

        options = q.get("options", [])
        if not options:
            tk.Label(parent, text="本题暂无选项数据，请联系管理员",
                    font=("微软雅黑", 12), fg=COLOR_DANGER, bg=COLOR_WHITE).pack(fill="x", padx=20, pady=10)
            return

        # 获取用户已选择的答案
        selected_answer = -1
        if qid in self.answers:
            ans = self.answers[qid]
            if isinstance(ans, int):
                selected_answer = ans

        opts_frame = tk.Frame(parent, bg=COLOR_WHITE)
        opts_frame.pack(fill="both", expand=True, padx=30, pady=15)

        # 每道题创建独立的 IntVar，避免上一题的选项残留在下一题
        self.choice_var = tk.IntVar(value=selected_answer if selected_answer >= 0 else -1)

        # 始终显示所有选项
        for i, opt in enumerate(options):
            letter = chr(ord('A') + i)
            frame = tk.Frame(opts_frame, bg=COLOR_WHITE)
            frame.pack(fill="x", pady=3)

            is_selected = (selected_answer == i)
            is_correct = (i == correct)
            
            if revealed and is_selected:
                # 已选择：显示结果标记
                if is_correct:
                    # 选对了
                    frame.config(bg="#e8f5e9")
                    tk.Label(frame, text=f"{letter}. {opt}  ✓ 正确答案",
                            font=("微软雅黑", 12, "bold"),
                            fg=COLOR_SUCCESS, bg="#e8f5e9",
                            anchor="w", padx=8, pady=6).pack(fill="x")
                else:
                    # 选错了 - 显示错误标记
                    frame.config(bg="#ffebee")
                    tk.Label(frame, text=f"{letter}. {opt}  ✗ 你的答案（错误）",
                            font=("微软雅黑", 12, "bold"),
                            fg=COLOR_DANGER, bg="#ffebee",
                            anchor="w", padx=8, pady=6).pack(fill="x")
            elif revealed and is_correct and not is_selected:
                # 未选但显示了正确答案
                frame.config(bg="#e8f5e9")
                tk.Label(frame, text=f"{letter}. {opt}  ← 正确答案",
                        font=("微软雅黑", 12, "bold"),
                        fg=COLOR_SUCCESS, bg="#e8f5e9",
                        anchor="w", padx=8, pady=6).pack(fill="x")
            else:
                # 正常未作答状态：显示可点击的单选按钮
                rb_text = f"{letter}. {opt}" if opt else f"{letter}. （空）"
                # 刷题模式：选中选项只保存答案，不自动判题；判题由导航栏「查看答案」按钮手动触发
                rb = tk.Radiobutton(frame, text=rb_text, variable=self.choice_var, value=i,
                                   font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                                   activebackground=COLOR_WHITE, cursor="hand2",
                                   anchor="w", padx=8, pady=6,
                                   command=lambda idx=i: self._save_practice_selection(qid, idx),
                                   state="disabled" if revealed else "normal")
                rb.pack(fill="x")

    def _render_practice_single_choice(self, parent, q, revealed):
        """转发包装"""
        self._render_practice_single_choice_impl(parent, q, revealed)
    def _render_practice_fill_blank_impl(self, parent, q, revealed):
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 填空题题干 + 代码显示
        display_text = q.get("content", "")
        if q.get("code"):
            display_text += "\n\n" + q["code"]
        # 题干 - 使用 Label + wraplength 精确控制每行字符数
        _lbl = tk.Label(parent, text=display_text,
                       font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        # 复制代码按钮（检查单独的 code 字段或内容中的代码块）
        has_code = q.get("code") or self._has_code_block(q.get("content", ""))
        if has_code:
            code_text = q.get("code") or self._extract_code(q.get("content", ""))
            btn_row = tk.Frame(parent, bg=COLOR_WHITE)
            btn_row.pack(fill="x", padx=20, pady=(0, 5))
            tk.Button(btn_row, text="📋 复制代码", font=("微软雅黑", 10),
                     bg="#e3f2fd", fg=COLOR_ACCENT, bd=1, relief="solid",
                     cursor="hand2", padx=10, pady=3,
                     command=lambda c=code_text: self._copy_code(c)).pack(side="left")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)

        # 左右分栏容器
        shared_opts = q.get("shared_options", [])
        num_blanks = len(q.get("blanks", []))
        # 备选项字母：按实际备选项数量生成（A/B/C...），最少2个
        all_letters = [chr(ord('A') + i) for i in range(26)]
        letters = all_letters[:max(len(shared_opts), 2)]
        qid = q["_qid"]
        if qid not in self.answers:
            self.answers[qid] = {}

        split_frame = tk.Frame(parent, bg=COLOR_WHITE)
        split_frame.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        # 左侧：备选项
        # 左侧：备选项（固定宽度、固定高度，不扩展）
        left_frame = tk.Frame(split_frame, bg=COLOR_WHITE, width=400, height=350)
        left_frame.pack(side="left", fill="y", expand=False, padx=(0, 5))
        left_frame.pack_propagate(False)

        opts_frame = tk.Frame(left_frame, bg="#f5f5f5", bd=1, relief="solid")
        opts_frame.pack(fill="both", expand=True)
        opts_header = tk.Frame(opts_frame, bg="#f5f5f5")
        opts_header.pack(fill="x", padx=12, pady=(6, 4))
        tk.Label(opts_header, text="【备选项】", font=("微软雅黑", 11, "bold"),
                fg=COLOR_TEXT_DARK, bg="#f5f5f5").pack(side="left")
        opts_text_lines = []
        for i, letter in enumerate(letters):
            opt_text = shared_opts[i] if i < len(shared_opts) else ""
            if len(opt_text) > 30:
                opt_text = opt_text[:30] + "…"
            opts_text_lines.append(f"{letter}. {opt_text}")
        opts_copy_text = "\n".join(opts_text_lines)
        tk.Button(opts_header, text="📋 复制备选项", font=("微软雅黑", 9),
                 bg="#e3f2fd", fg=COLOR_ACCENT, bd=1, relief="solid",
                 cursor="hand2", padx=8, pady=2,
                 command=lambda t=opts_copy_text: self._copy_code(t)).pack(side="right")

        for i, letter in enumerate(letters):
            opt_text = shared_opts[i] if i < len(shared_opts) else ""
            if len(opt_text) > 30:
                opt_text = opt_text[:30] + "…"
            tk.Label(opts_frame, text=f"  {letter}. {opt_text}", font=("微软雅黑", 10),
                    fg=COLOR_TEXT_DARK, bg="#f5f5f5", anchor="w").pack(fill="x", padx=20, pady=0)

        # 右侧分隔线
        ttk.Separator(split_frame, orient="vertical").pack(side="left", fill="y", padx=5)

        # 右侧：答题区域
        right_frame = tk.Frame(split_frame, bg=COLOR_WHITE)
        right_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))

        tk.Label(right_frame, text="【答题区域】共有 {} 个空：".format(num_blanks),
                font=("微软雅黑", 12, "bold"), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(anchor="w", pady=(0, 10))

        for blank in q.get("blanks", []):
            bf = tk.Frame(right_frame, bg=COLOR_WHITE)
            bf.pack(fill="x", pady=6)

            tk.Label(bf, text=f"{blank['label']}：", font=("微软雅黑", 12, "bold"),
                    fg=COLOR_ACCENT, bg=COLOR_WHITE, width=6, anchor="e").pack(side="left", padx=(0, 10))

            correct_idx = blank.get("answer", -1)
            lbl = blank["label"]

            if revealed:
                correct_text = shared_opts[correct_idx] if 0 <= correct_idx < len(shared_opts) else "无"
                user_letter = self.answers.get(qid, {}).get(lbl, "")
                letter_idx = {l: i for i, l in enumerate(letters)}
                user_idx = letter_idx.get(user_letter, -1)
                if user_idx == correct_idx:
                    tk.Label(bf, text=f"✓ {user_letter}.{correct_text} （正确）", font=("微软雅黑", 11),
                            fg=COLOR_SUCCESS, bg=COLOR_WHITE).pack(side="left")
                else:
                    user_text = shared_opts[user_idx] if 0 <= user_idx < len(shared_opts) else ""
                    tk.Label(bf, text=f"✗ 你的：{user_letter}.{user_text}", font=("微软雅黑", 11),
                            fg=COLOR_DANGER, bg=COLOR_WHITE).pack(side="left")
                    tk.Label(bf, text=f"  正确答案：{letters[correct_idx]}.{correct_text}",
                            font=("微软雅黑", 11, "bold"),
                            fg=COLOR_SUCCESS, bg=COLOR_WHITE).pack(side="left", padx=5)
            else:
                var = tk.StringVar()
                if lbl in self.answers.get(qid, {}):
                    var.set(self.answers[qid][lbl])
                combo = ttk.Combobox(bf, textvariable=var, values=letters,
                                    state="readonly", font=("微软雅黑", 11), width=8)
                combo.pack(side="left")
                combo.bind("<<ComboboxSelected>>",
                          lambda e, qid=qid, lbl=lbl, v=var: self._save_fill_blank(qid, lbl, v))

    def _render_practice_fill_blank(self, parent, q, revealed):
        """转发包装"""
        self._render_practice_fill_blank_impl(parent, q, revealed)
    def _render_practice_programming_impl(self, parent, q, revealed):
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        qid = q["_qid"]

        # 题干 - 自动换行（每行最多50字符）+ 自适应高度
        # 题干 - 使用 Label + wraplength 精确控制每行字符数
        from tkinter import Label
        _lbl = Label(parent, text=q["content"],
                      font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                      wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)

        # 提示：编程题在 ITSData 文件夹中编辑
        tip_frame = tk.Frame(parent, bg="#e3f2fd", bd=0)
        tip_frame.pack(fill="x", padx=20, pady=(10, 5))
        for tip in ["请点击「打开考试文件夹」编辑 prog.c 文件"]:
            tk.Label(tip_frame, text=tip, font=("微软雅黑", 10), fg="#1565C0",
                    bg="#e3f2fd", anchor="w").pack(fill="x", padx=15, pady=3)

        # 按钮栏
        btn_frame = tk.Frame(parent, bg=COLOR_WHITE)
        btn_frame.pack(fill="x", padx=20, pady=(8, 5))

        prog_folder = self._prog_folder_map.get(qid, "C")
        its_prog_dir = os.path.join(r"C:\ITSData", prog_folder)

        tk.Button(btn_frame, text="📂 打开考试文件夹", font=("微软雅黑", 10),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                 cursor="hand2",
                 command=lambda q=q, d=its_prog_dir: (self._ensure_its_data_folder_for_question(q, d), self._start_answer(q, d) or True)[-1]).pack(side="left", padx=(0, 8))

        tk.Button(btn_frame, text="💾 存储为", font=("微软雅黑", 10),
                 bg="#E65100", fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                 cursor="hand2",
                 command=lambda: self._save_itsdata_to_folder(its_prog_dir, "编程题")).pack(side="left", padx=(0, 8))

        submit_btn = tk.Button(btn_frame, text="提交判题", font=("微软雅黑", 10, "bold"),
                               bg="#7B1FA2", fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                               cursor="hand2",
                               command=lambda: self._submit_judge(qid, parent))
        submit_btn.pack(side="left")

        # 保存说明
        save_tip = tk.Label(parent, text="⚠ 注意：每次答题都会重置题目文件夹，完成后请点击「存储为」另存到其他位置，以免丢失！",
                           font=("微软雅黑", 9), fg="#E65100", bg=COLOR_WHITE, anchor="w")
        save_tip.pack(fill="x", padx=20, pady=(0, 5))

        # 判题规则说明
        judge_note = tk.Label(parent, text="📋 判题规则：通过输入输出样例进行判题，并非实际考试评分标准",
                             font=("微软雅黑", 9), fg="#666666", bg=COLOR_WHITE, anchor="w")
        judge_note.pack(fill="x", padx=20, pady=(0, 10))

        # 查看参考答案按钮（仅在有参考答案时显示）
        ref_answer = q.get("reference_answer", "")
        ref_has_content = False
        if isinstance(ref_answer, dict):
            ref_has_content = any(v.strip() for v in ref_answer.values())
        else:
            ref_has_content = bool(ref_answer.strip())
        if ref_has_content:
            tk.Button(btn_frame, text="查看参考答案", font=("微软雅黑", 10),
                     bg="#FF6F00", fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                     cursor="hand2",
                     command=lambda ra=ref_answer: self._show_reference_answer_popup(ra)).pack(side="left", padx=(8, 0))

        # 判题结果面板容器
        result_container = tk.Frame(parent, bg=COLOR_WHITE)
        result_container.pack(fill="x", padx=20, pady=(5, 10))

        # 如果有之前的判题结果，恢复显示
        if qid in self._judge_result_widgets:
            self._rebuild_judge_result(qid, result_container)
        elif qid in self.show_revealed and qid in self.wrong_questions:
            self._rebuild_judge_result(qid, result_container)

    def _render_practice_programming(self, parent, q, revealed):
        """转发包装"""
        self._render_practice_programming_impl(parent, q, revealed)
    def _render_practice_web_design_impl(self, parent, q, revealed):
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 题干
        _lbl = tk.Label(parent, text=q["content"],
                       font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)
        btn_frame = tk.Frame(parent, bg=COLOR_WHITE)
        btn_frame.pack(fill="x", padx=20, pady=15)

        dw_folder = self._dw_folder_map.get(q["_qid"], "DW")
        its_dw_dir = os.path.join(r"C:\ITSData", dw_folder)
        tk.Button(btn_frame, text="📂 打开考试文件夹", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2",
                 command=lambda q=q, d=its_dw_dir: (self._ensure_its_data_folder_for_question(q, d), self._start_answer(q, d) or True)[-1]).pack(side="left", padx=(0, 8))

        tk.Button(btn_frame, text="💾 存储为", font=("微软雅黑", 12, "bold"),
                 bg="#E65100", fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2",
                 command=lambda: self._save_itsdata_to_folder(its_dw_dir, "网页制作题")).pack(side="left")

        ref_btn = self._add_reference_folder_button(btn_frame, q)
        if ref_btn:
            ref_btn.pack(side="left", padx=(8, 0))

        tip_frame = tk.Frame(parent, bg="#fff3e0", bd=0)
        tip_frame.pack(fill="x", padx=20, pady=(10, 5))
        for tip in ["网页制作题需在开发环境中实际操作练习",
                    f"点击按钮打开 ITSData\\{dw_folder} 文件夹进行网页制作",
                    "刷题模式下无自动评分，自行核对效果"]:
            tk.Label(tip_frame, text=tip, font=("微软雅黑", 10), fg="#2e7d32",
                    bg="#fff3e0", anchor="w").pack(fill="x", padx=15, pady=2)

        save_tip = tk.Label(parent, text="⚠ 注意：每次答题都会重置题目文件夹，完成后请点击「存储为」另存到其他位置，以免丢失！",
                           font=("微软雅黑", 9), fg="#E65100", bg=COLOR_WHITE, anchor="w")
        save_tip.pack(fill="x", padx=20, pady=(0, 10))

    def _render_practice_web_design(self, parent, q, revealed):
        """转发包装"""
        self._render_practice_web_design_impl(parent, q, revealed)
    def _render_practice_network_device_impl(self, parent, q, revealed):
        """网络设备安装与调试刷题模式：两列布局——左图右答题区"""
        # === 两列主容器 ===
        main_frame = tk.Frame(parent, bg=COLOR_WHITE)
        main_frame.pack(fill="both", expand=True, padx=15, pady=10)

        # === 左列：图片 ===
        left_col = tk.Frame(main_frame, bg=COLOR_WHITE)
        left_col.pack(side="left", fill="y", padx=(0, 15))

        img_widget = self._make_image_widget(left_col, q)
        if img_widget:
            img_widget.pack(pady=(5, 10))

        # === 右列：题目内容 + 答题区 ===
        right_col = tk.Frame(main_frame, bg=COLOR_WHITE)
        right_col.pack(side="left", fill="both", expand=True)

        # 题目来源
        if q.get("source"):
            tk.Label(right_col, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", pady=(0, 5))
        # 题干
        _lbl = tk.Label(right_col, text=q.get("content", ""),
                       font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=550, justify="left", anchor="w")
        _lbl.pack(fill="x", pady=(0, 10))
        ttk.Separator(right_col, orient="horizontal").pack(fill="x")

        qid = q["_qid"]
        if qid not in self.answers:
            self.answers[qid] = {}

        text_fields = q.get("text_fields", [])
        fields_frame = tk.Frame(right_col, bg=COLOR_WHITE)
        fields_frame.pack(fill="both", expand=True, pady=(10, 0))

        tk.Label(fields_frame, text="【答题区域】请在下方各文本框中粘贴命令输出结果：",
                font=("微软雅黑", 12, "bold"), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(anchor="w", pady=(0, 10))

        for tf in text_fields:
            tf_frame = tk.Frame(fields_frame, bg=COLOR_WHITE)
            tf_frame.pack(fill="x", pady=(0, 8))

            label_text = tf.get("label", "")
            tk.Label(tf_frame, text=f"{label_text}：", font=("微软雅黑", 11, "bold"),
                    fg=COLOR_ACCENT, bg=COLOR_WHITE, anchor="w").pack(fill="x", pady=(0, 3))

            text_frame = tk.Frame(tf_frame, bg="#b0c4de", bd=1)
            text_frame.pack(fill="x")

            text_widget = tk.Text(text_frame, font=("Consolas", 11), bg="#fafafa", fg=COLOR_TEXT_DARK,
                                 wrap="word", bd=0, padx=8, pady=5, height=7)
            text_scrollbar = tk.Scrollbar(text_frame, orient="vertical", command=text_widget.yview)
            text_widget.configure(yscrollcommand=text_scrollbar.set)
            text_widget.pack(side="left", fill="both", expand=True)
            text_scrollbar.pack(side="right", fill="y")

            saved = self.answers[qid].get(label_text, "")
            if saved:
                text_widget.insert("1.0", saved)

            if revealed:
                text_widget.config(state="disabled")
            else:
                def make_callback(widget, lbl, qid):
                    def on_modified(event=None):
                        content = widget.get("1.0", "end-1c")
                        if qid not in self.answers:
                            self.answers[qid] = {}
                        self.answers[qid][lbl] = content
                        self._update_practice_sidebar_impl()
                        widget.edit_modified(False)
                    return on_modified
                text_widget.bind("<<Modified>>", make_callback(text_widget, label_text, qid))

        # 查看答案按钮
        answer_doc_raw = q.get("answer_doc", "")
        answer_doc = _resolve_path(answer_doc_raw) if answer_doc_raw else ""
        has_answer_doc = bool(answer_doc and answer_doc.strip())

        if has_answer_doc:
            btn_frame = tk.Frame(right_col, bg=COLOR_WHITE)
            btn_frame.pack(fill="x", pady=(10, 0))
            tk.Button(btn_frame, text="📄 查看答案（Word文档）", font=("微软雅黑", 10, "bold"),
                     bg="#1565C0", fg=COLOR_WHITE, bd=0, padx=15, pady=6,
                     cursor="hand2",
                     command=lambda path=answer_doc: (os.startfile(path) if os.path.exists(path) else messagebox.showwarning("提示", f"答案文档不存在：\n{path}"))).pack(side="left", padx=(0, 10))

    def _render_practice_network_device(self, parent, q, revealed):
        """转发包装"""
        self._render_practice_network_device_impl(parent, q, revealed)
    def _render_practice_graphic_design_impl(self, parent, q, revealed):
        """图形图像处理刷题模式"""
        self._show_question_image(parent, q)
        # 题目来源
        if q.get("source"):
            tk.Label(parent, text=f"题目来源：{q['source']}",
                    font=("微软雅黑", 10, "italic"), fg="#888888", bg=COLOR_WHITE,
                    anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        # 题干
        _lbl = tk.Label(parent, text=q.get("content", ""),
                       font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK,
                       wraplength=850, justify="left", anchor="w")
        _lbl.pack(fill="x", padx=20, pady=15)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=20)
        btn_frame = tk.Frame(parent, bg=COLOR_WHITE)
        btn_frame.pack(fill="x", padx=20, pady=15)

        ps_folder = self._ps_folder_map.get(q["_qid"], "PS")
        its_ps_dir = os.path.join(r"C:\ITSData", ps_folder)
        tk.Button(btn_frame, text="📂 打开考试文件夹", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2",
                 command=lambda q=q, d=its_ps_dir: (self._ensure_its_data_folder_for_question(q, d), self._start_answer(q, d) or True)[-1]).pack(side="left", padx=(0, 8))

        tk.Button(btn_frame, text="💾 存储为", font=("微软雅黑", 12, "bold"),
                 bg="#E65100", fg=COLOR_WHITE, bd=0, padx=20, pady=8,
                 cursor="hand2",
                 command=lambda: self._save_itsdata_to_folder(its_ps_dir, "图形图像处理题")).pack(side="left")

        ref_btn = self._add_reference_folder_button(btn_frame, q)
        if ref_btn:
            ref_btn.pack(side="left", padx=(8, 0))

        tip_frame = tk.Frame(parent, bg="#fff3e0", bd=0)
        tip_frame.pack(fill="x", padx=20, pady=(10, 5))
        for tip in ["图形图像处理题需在开发环境中实际操作练习",
                    f"点击按钮打开 ITSData\\{ps_folder} 文件夹进行图像处理",
                    "刷题模式下无自动评分，自行核对效果"]:
            tk.Label(tip_frame, text=tip, font=("微软雅黑", 10), fg="#2e7d32",
                    bg="#fff3e0", anchor="w").pack(fill="x", padx=15, pady=2)

        save_tip = tk.Label(parent, text="⚠ 注意：每次答题都会重置题目文件夹，完成后请点击「存储为」另存到其他位置，以免丢失！",
                           font=("微软雅黑", 9), fg="#E65100", bg=COLOR_WHITE, anchor="w")
        save_tip.pack(fill="x", padx=20, pady=(0, 10))

    def _render_practice_graphic_design(self, parent, q, revealed):
        """转发包装"""
        self._render_practice_graphic_design_impl(parent, q, revealed)
    def _save_practice_code(self, qid, code_editor):
        """保存编辑器中的代码到内存和 ITSData\\C_n\\prog.c 文件"""
        code = code_editor.get("1.0", "end-1c")
        self.practice_code_content[qid] = code
        # 写入 ITSData 对应文件夹
        prog_folder = self._prog_folder_map.get(qid, "C")
        its_prog_dir = os.path.join(r"C:\ITSData", prog_folder)
        if not os.path.exists(its_prog_dir):
            os.makedirs(its_prog_dir)
        prog_c_path = os.path.join(its_prog_dir, "prog.c")
        with open(prog_c_path, "w", encoding="utf-8") as f:
            f.write(code)
        # 显示保存提示
        saved_label = tk.Label(code_editor.master.master,
                               text="代码已保存", font=("微软雅黑", 9),
                               fg=COLOR_SUCCESS, bg=COLOR_WHITE)
        saved_label.place(relx=1.0, rely=0.0, anchor="ne", x=-10, y=5)
        code_editor.master.master.after(2000, saved_label.destroy)

    def _submit_judge(self, qid, parent):
        """提交判题：读取 ITSData\\C_n\\prog.c → 编译运行 → 显示判题结果"""
        if not hasattr(self, "_judging_qids"):
            self._judging_qids = set()
        if qid in self._judging_qids:
            messagebox.showinfo("提示", "该题正在判题中，请稍等。")
            return

        prog_folder = self._prog_folder_map.get(qid, "C")
        its_prog_dir = os.path.join(r"C:\ITSData", prog_folder)
        prog_c_path = os.path.join(its_prog_dir, "prog.c")

        if not os.path.exists(prog_c_path):
            messagebox.showwarning("提示", f"未找到 {prog_c_path}，请先打开考试文件夹编辑 prog.c 文件！")
            return

        # 获取题目的原始 id（用于定位测试案例目录）
        q = next((q for q in self.questions if q["_qid"] == qid), {})
        orig_id = q.get("id", qid)

        self._judging_qids.add(qid)
        self._judge_result_widgets[qid] = {
            "passed": 0, "total": 0,
            "details": [{"case": 0, "status": "RUNNING", "note": "正在后台编译并运行测试，请稍等..."}],
            "score_pct": 0, "all_passed": False
        }
        self._display_practice_question(self.current_question)
        self._update_practice_sidebar_impl()

        def judge_worker():
            try:
                passed, total, details = self._judge_program(qid, orig_id, prog_c_path, num_cases=10)
            except Exception as exc:
                passed, total = 0, 0
                details = [{
                    "case": 0,
                    "status": "RE",
                    "note": f"判题过程异常：{str(exc)[:500]}"
                }]

            def finish_judge():
                self._judging_qids.discard(qid)

                # 计算得分
                score_pct = (passed / total * 100) if total > 0 else 0
                all_passed = (passed == total and total > 0)

                # 更新错题标记
                if all_passed:
                    self.wrong_questions.discard(qid)
                else:
                    self.wrong_questions.add(qid)
                self.show_revealed.add(qid)

                # 保存判题结果
                self._judge_result_widgets[qid] = {
                    "passed": passed, "total": total, "details": details,
                    "score_pct": score_pct, "all_passed": all_passed
                }

                # 重建当前页面以显示判题结果
                try:
                    self._display_practice_question(self.current_question)
                    self._update_practice_sidebar_impl()
                except tk.TclError:
                    pass

            try:
                self.root.after(0, finish_judge)
            except tk.TclError:
                pass

        threading.Thread(target=judge_worker, daemon=True).start()

    def _rebuild_judge_result(self, qid, container):
        """在 container 中重建判题结果面板"""
        data = self._judge_result_widgets.get(qid)
        if not data:
            return

        for widget in container.winfo_children():
            widget.destroy()

        passed = data["passed"]
        total = data["total"]
        score_pct = data["score_pct"]
        all_passed = data["all_passed"]
        details = data["details"]

        # 总体结果
        if total == 0 and details and details[0].get("status") == "RUNNING":
            status_color = COLOR_WARNING
            status_text = "正在判题"
            score_label = "请稍等，后台正在编译并运行测试"
        elif total == 0 and details and details[0].get("status") == "NO_CASES":
            status_color = COLOR_WARNING
            status_text = "未配置测试案例"
            score_label = "请放置 .in/.out 文件后重试"
        else:
            status_color = COLOR_SUCCESS if all_passed else COLOR_DANGER
            status_text = "全部通过" if all_passed else "存在错题"
            score_label = f"通过 {passed}/{total}，得分 {score_pct:.0f}%"

        header_frame = tk.Frame(container, bg=COLOR_WHITE)
        header_frame.pack(fill="x", pady=(5, 8))

        tk.Label(header_frame, text=f"判题结果：{status_text}",
                font=("微软雅黑", 13, "bold"), fg=status_color, bg=COLOR_WHITE).pack(side="left")
        tk.Label(header_frame, text=score_label,
                font=("微软雅黑", 12), fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(side="left", padx=15)

        # 进度条
        bar_frame = tk.Frame(container, bg="#e0e0e0", height=16, bd=0)
        bar_frame.pack(fill="x", pady=(0, 8))
        bar_frame.pack_propagate(False)
        bar_width = 600
        if total > 0:
            green_w = int(bar_width * passed / total)
            if green_w > 0:
                tk.Frame(bar_frame, bg=COLOR_SUCCESS, height=16, width=green_w).place(x=0, y=0)
            if passed < total:
                red_w = bar_width - green_w
                tk.Frame(bar_frame, bg=COLOR_DANGER, height=16, width=red_w).place(x=green_w, y=0)

        # 逐案例详情
        detail_frame = tk.Frame(container, bg=COLOR_WHITE)
        detail_frame.pack(fill="x")

        # 表头
        cols_frame = tk.Frame(detail_frame, bg="#f5f5f5")
        cols_frame.pack(fill="x")
        for col_text, col_w in [("案例", 6), ("状态", 8), ("说明", 80)]:
            tk.Label(cols_frame, text=col_text, font=("微软雅黑", 9, "bold"),
                    fg=COLOR_TEXT_DARK, bg="#f5f5f5", width=col_w,
                    anchor="w", padx=5).pack(side="left")

        status_colors = {"AC": COLOR_SUCCESS, "WA": COLOR_DANGER, "TLE": COLOR_WARNING,
                         "CE": COLOR_DANGER, "RE": COLOR_DANGER, "SKIP": "#9e9e9e",
                         "NO_CASES": COLOR_WARNING, "RUNNING": COLOR_WARNING}
        status_cn = {"AC": "通过", "WA": "答案错误", "TLE": "运行超时",
                     "CE": "编译错误", "RE": "运行异常", "SKIP": "跳过",
                     "NO_CASES": "无案例", "RUNNING": "判题中"}

        for d in details:
            if d["status"] == "SKIP":
                continue
            row = tk.Frame(detail_frame, bg=COLOR_WHITE)
            row.pack(fill="x", pady=1)
            sc = status_colors.get(d["status"], COLOR_TEXT_DARK)
            tk.Label(row, text=f"#{d['case']}", font=("Consolas", 9),
                    fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, width=6, anchor="w", padx=5).pack(side="left")
            tk.Label(row, text=status_cn.get(d["status"], d["status"]), font=("微软雅黑", 9, "bold"),
                    fg=sc, bg=COLOR_WHITE, width=8, anchor="w", padx=5).pack(side="left")
            tk.Label(row, text=d.get("note", ""), font=("微软雅黑", 9),
                     fg="#666666", bg=COLOR_WHITE, anchor="w", justify="left",
                    wraplength=760).pack(side="left", padx=5, fill="x", expand=True)

    def _reveal_answer(self, index):
        q = self.questions[index]
        qid = q["_qid"]

        # ★ 单选题未选选项时提示，必须先选择再查看答案
        if q["type"] == "single_choice":
            user = self.answers.get(qid, -1)
            if user == -1:
                messagebox.showwarning("提示", "请先选择一个选项，再点击「参考答案」")
                return
            correct = q.get("answer", -1)
            is_correct = (user == correct)

            # 更新统计数据（不重建UI，彻底避免白屏）
            if is_correct:
                self.practice_stats["correct"] = self.practice_stats.get("correct", 0) + 1
                self.wrong_questions.discard(qid)
            else:
                self.practice_stats["wrong"] = self.practice_stats.get("wrong", 0) + 1
                self.wrong_questions.add(qid)

            self.show_revealed.add(qid)
            self._update_practice_stats_display()

            # 弹窗显示参考答案，完全不重建UI
            self._show_sc_answer_popup(q, qid, user, correct, is_correct, index)
            return
        elif q["type"] == "fill_blank":
            is_correct = self._check_practice_answer(q)
        elif q["type"] == "programming":
            # 编程题通过判题结果判定
            judge_data = self._judge_result_widgets.get(qid, {})
            passed = judge_data.get("passed", 0)
            total = judge_data.get("total", 0)
            is_correct = (passed == total and total > 0) if judge_data else False
            if not judge_data:
                self.show_revealed.add(qid)
                self.root.after(0, lambda idx=index: self._display_practice_question(idx))
                return
        elif q["type"] == "network_device":
            # 网络设备题：自动评分已禁用，显示答案供手动对比
            self.show_revealed.add(qid)
            self.root.after(0, lambda idx=index: self._display_practice_question(idx))
            return
        elif q["type"] == "graphic_design":
            # 图形题无需答案核查，直接标记已查看
            self.show_revealed.add(qid)
            self.root.after(0, lambda idx=index: self._display_practice_question(idx))
            return
        else:
            self.show_revealed.add(qid)
            self.root.after(0, lambda idx=index: self._display_practice_question(idx))
            return

        if is_correct:
            self.practice_stats["correct"] = self.practice_stats.get("correct", 0) + 1
        else:
            self.practice_stats["wrong"] = self.practice_stats.get("wrong", 0) + 1

        # 错题标记
        if not is_correct:
            self.wrong_questions.add(qid)
        else:
            self.wrong_questions.discard(qid)

        self.show_revealed.add(qid)
        self._update_practice_stats_display()
        # ★ 用 after(0) 延迟渲染，避免按钮点击回调中直接 destroy+重建导致布局异常
        self.root.after(0, lambda idx=index: self._display_practice_question(idx))

    def _show_sc_answer_popup(self, q, qid, user, correct, is_correct, index):
        """单选题：弹窗显示参考答案（完全不重建UI，从根源避免白屏）"""
        popup = tk.Toplevel(self.root)
        popup.title("参考答案")
        popup.geometry("500x450")
        popup.configure(bg=COLOR_WHITE)
        popup.transient(self.root)
        popup.grab_set()

        # 居中显示
        popup.update_idletasks()
        x = (popup.winfo_screenwidth() - popup.winfo_width()) // 2
        y = (popup.winfo_screenheight() - popup.winfo_height()) // 2
        popup.geometry(f"+{x}+{y}")

        # 标题
        tk.Label(popup, text="参考答案", font=("微软雅黑", 16, "bold"),
                bg=COLOR_WHITE, fg=COLOR_TEXT_DARK).pack(pady=(20, 10))

        # 题目编号
        tk.Label(popup, text=f"第 {index+1} 题",
                font=("微软雅黑", 12),
                bg=COLOR_WHITE, fg=COLOR_TEXT_DARK).pack(pady=5)

        # 用户答案
        user_letter = chr(ord('A') + user) if 0 <= user < 26 else "未选择"
        tk.Label(popup, text=f"你的答案：{user_letter}",
                font=("微软雅黑", 12), bg=COLOR_WHITE, fg=COLOR_TEXT_DARK).pack(pady=5)

        # 正确答案
        correct_letter = chr(ord('A') + int(correct)) if isinstance(correct, (int, float)) else str(correct)
        tk.Label(popup, text=f"正确答案：{correct_letter}",
                font=("微软雅黑", 12, "bold"),
                bg=COLOR_WHITE,
                fg=COLOR_SUCCESS if is_correct else COLOR_DANGER).pack(pady=5)

        # 结果（大号图标）
        result_frame = tk.Frame(popup, bg=COLOR_WHITE)
        result_frame.pack(pady=20)

        if is_correct:
            tk.Label(result_frame, text="✓", font=("微软雅黑", 48, "bold"),
                    fg=COLOR_SUCCESS, bg=COLOR_WHITE).pack()
            tk.Label(result_frame, text="回答正确！", font=("微软雅黑", 14, "bold"),
                        fg=COLOR_SUCCESS, bg=COLOR_WHITE).pack()
        else:
            tk.Label(result_frame, text="✗", font=("微软雅黑", 48, "bold"),
                        fg=COLOR_DANGER, bg=COLOR_WHITE).pack()
            tk.Label(result_frame, text="回答错误", font=("微软雅黑", 14, "bold"),
                        fg=COLOR_DANGER, bg=COLOR_WHITE).pack()

        # 关闭按钮
        tk.Button(popup, text="关闭", font=("微软雅黑", 11),
                 bg="#1976d2", fg=COLOR_WHITE, bd=0, padx=30, pady=8,
                 cursor="hand2", command=popup.destroy).pack(pady=(0, 20))

    def _check_practice_answer(self, q):
        qid = q["_qid"]
        if q["type"] == "single_choice":
            return self.answers.get(qid, -1) == q.get("answer", -1)
        elif q["type"] == "fill_blank":
            user = self.answers.get(qid, {})
            letter_to_idx = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4,
                             "F": 5, "G": 6, "H": 7, "I": 8, "J": 9}
            all_correct = True
            for blank in q.get("blanks", []):
                lbl = blank["label"]
                correct_idx = blank.get("answer", -1)
                user_letter = user.get(lbl, "")
                user_idx = letter_to_idx.get(user_letter, -1)
                if user_idx != correct_idx:
                    all_correct = False
            return all_correct
        elif q["type"] == "programming":
            judge_data = self._judge_result_widgets.get(qid, {})
            passed = judge_data.get("passed", 0)
            total = judge_data.get("total", 0)
            return total > 0 and passed == total
        elif q["type"] == "network_device":
            judge_data = self._judge_result_widgets.get(qid, {})
            return judge_data.get("all_correct", False)
        return True

    def _update_practice_stats_display(self):
        self.practice_stat_var.set(
            f"正确：{self.practice_stats.get('correct', 0)}  |  "
            f"错误：{self.practice_stats.get('wrong', 0)}  |  "
            f"共 {len(self.questions)} 题")

    def _update_practice_sidebar_impl(self):
        for entry in self.practice_sidebar_buttons:
            btn, del_btn, qtype, orig_idx = entry
            if orig_idx >= len(self.questions):
                break
            q = self.questions[orig_idx]
            qid = q["_qid"]
            if orig_idx == self.current_question:
                btn.config(bg="#1976d2", fg=COLOR_WHITE, font=("微软雅黑", 8, "bold"),
                          highlightbackground="#1976d2", highlightthickness=1, relief="solid")
            elif qid in self.wrong_questions:
                btn.config(bg="#ffebee", fg=COLOR_DANGER, font=("微软雅黑", 8),
                          highlightbackground="#ffebee", highlightthickness=1, relief="solid")
            elif qid in self.show_revealed:
                is_correct = self._check_practice_answer(q)
                if is_correct:
                    btn.config(bg="#e8f5e9", fg=COLOR_SUCCESS, font=("微软雅黑", 8),
                              highlightbackground="#e8f5e9", highlightthickness=1, relief="solid")
                else:
                    btn.config(bg="#ffebee", fg=COLOR_DANGER, font=("微软雅黑", 8),
                              highlightbackground="#ffebee", highlightthickness=1, relief="solid")
            elif self._is_answered(q):
                btn.config(bg="#fff3e0", fg=COLOR_WARNING, font=("微软雅黑", 8),
                          highlightbackground="#fff3e0", highlightthickness=1, relief="solid")
            else:
                btn.config(bg=COLOR_WHITE, fg=COLOR_UNANSWERED, font=("微软雅黑", 8),
                          highlightbackground="#e0e0e0", highlightthickness=1, relief="solid")

    def _update_practice_sidebar(self):
        """转发包装：调用合并后的 _update_sidebar_buttons(mode="practice")"""
        self._update_sidebar_buttons(mode="practice")
    def _practice_prev(self):
        if self._locked_question is not None:
            return  # 答题锁定中，禁止切换
        if self.current_question > 0:
            self._display_practice_question_impl(self.current_question - 1)

    def _practice_next(self):
        if self._locked_question is not None:
            return  # 答题锁定中，禁止切换
        if self.current_question < len(self.questions) - 1:
            self._display_practice_question_impl(self.current_question + 1)

    # ==================== 账号管理 ====================
    def show_account_management(self):
        """账号管理对话框：查看、添加、删除考生账号。"""
        accounts = load_accounts()

        dialog = tk.Toplevel(self.root)
        dialog.title("账号管理")
        dialog.geometry("700x650")
        dialog.resizable(True, True)
        dialog.minsize(550, 400)
        dialog.configure(bg=COLOR_WHITE)
        dialog.transient(self.root)

        sw, sh = self._screen_size()
        dialog.geometry(f"+{sw//2-350}+{sh//2-300}")

        # 标题
        tk.Label(dialog, text="考生账号管理", font=("微软雅黑", 18, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_WHITE).pack(pady=(15, 10))

        # 账号列表（Treeview）
        list_frame = tk.Frame(dialog, bg=COLOR_WHITE)
        list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        columns = ("exam_id", "name", "id_number", "is_admin")
        tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=12)

        tree.heading("exam_id", text="准考证号")
        tree.heading("name", text="姓名")
        tree.heading("id_number", text="身份证号(密码)")
        tree.heading("is_admin", text="管理员")

        tree.column("exam_id", width=150, anchor="center")
        tree.column("name", width=120, anchor="center")
        tree.column("id_number", width=200, anchor="center")
        tree.column("is_admin", width=80, anchor="center")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def refresh_tree():
            for item in tree.get_children():
                tree.delete(item)
            nonlocal accounts
            accounts = load_accounts()
            for exam_id, info in accounts.items():
                tree.insert("", "end", values=(
                    exam_id,
                    info.get("name", ""),
                    info.get("id_number", info.get("password", "")),
                    "是" if info.get("is_admin") else "否"
                ))

        refresh_tree()

        # 双击行切换管理员身份
        def on_double_click(event):
            selected = tree.selection()
            if not selected:
                return
            values = tree.item(selected[0], "values")
            exam_id = values[0]
            if exam_id == "admin":
                messagebox.showinfo("提示", "内置 admin 账号身份不可修改")
                return
            accounts = load_accounts()
            if exam_id not in accounts:
                return
            current = accounts[exam_id].get("is_admin", False)
            new_val = not current
            accounts[exam_id]["is_admin"] = new_val
            save_accounts(accounts)
            refresh_tree()
            status_text = "管理员" if new_val else "普通考生"
            messagebox.showinfo("成功", f"账号 {exam_id} 已切换为「{status_text}」")

        tree.bind("<Double-1>", on_double_click)

        # 添加区
        add_frame = tk.LabelFrame(dialog, text="添加账号", font=("微软雅黑", 12, "bold"),
                                   fg=COLOR_TEXT_DARK, bg=COLOR_WHITE, padx=10, pady=10)
        add_frame.pack(fill="x", padx=20, pady=(5, 10))

        row1 = tk.Frame(add_frame, bg=COLOR_WHITE)
        row1.pack(fill="x", pady=3)
        tk.Label(row1, text="准考证号：", font=("微软雅黑", 11), bg=COLOR_WHITE, width=10, anchor="e").pack(side="left")
        entry_exam_id = tk.Entry(row1, font=("微软雅黑", 11), width=18)
        entry_exam_id.pack(side="left", padx=(0, 20))

        tk.Label(row1, text="姓名：", font=("微软雅黑", 11), bg=COLOR_WHITE, width=6, anchor="e").pack(side="left")
        entry_name = tk.Entry(row1, font=("微软雅黑", 11), width=12)
        entry_name.pack(side="left")

        row2 = tk.Frame(add_frame, bg=COLOR_WHITE)
        row2.pack(fill="x", pady=3)
        tk.Label(row2, text="身份证号：", font=("微软雅黑", 11), bg=COLOR_WHITE, width=10, anchor="e").pack(side="left")
        entry_id_number = tk.Entry(row2, font=("微软雅黑", 11), width=22)
        entry_id_number.pack(side="left", padx=(0, 20))

        is_admin_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row2, text="管理员", variable=is_admin_var,
                      font=("微软雅黑", 11), bg=COLOR_WHITE).pack(side="left")

        def do_add():
            exam_id = entry_exam_id.get().strip()
            name = entry_name.get().strip()
            id_number = entry_id_number.get().strip()
            is_admin = is_admin_var.get()

            if not exam_id or not name or not id_number:
                messagebox.showwarning("提示", "准考证号、姓名、身份证号不能为空！")
                return

            if exam_id in accounts:
                messagebox.showwarning("提示", f"准考证号 {exam_id} 已存在！")
                return

            accounts[exam_id] = {
                "password": id_number,
                "name": name,
                "id_number": id_number,
                "is_admin": is_admin,
            }
            save_accounts(accounts)
            refresh_tree()
            entry_exam_id.delete(0, "end")
            entry_name.delete(0, "end")
            entry_id_number.delete(0, "end")
            entry_exam_id.focus()
            messagebox.showinfo("成功", f"已添加考生 {name}({exam_id})")

        tk.Button(add_frame, text="添加", font=("微软雅黑", 12, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=25, pady=5,
                 cursor="hand2", command=do_add).pack(pady=(8, 0))

        # 操作按钮区
        btn_frame = tk.Frame(dialog, bg=COLOR_WHITE)
        btn_frame.pack(pady=(5, 15))

        def do_delete():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("提示", "请先选择要删除的账号！")
                return
            values = tree.item(selected[0], "values")
            exam_id = values[0]
            if exam_id == "admin":
                messagebox.showwarning("提示", "超级管理员账号不可删除！")
                return
            if not messagebox.askyesno("确认", f"确定删除账号 {exam_id}（{values[1]}）吗？"):
                return
            accounts.pop(exam_id, None)
            save_accounts(accounts)
            refresh_tree()

        tk.Button(btn_frame, text="删除选中", font=("微软雅黑", 12),
                 bg="#e74c3c", fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                 cursor="hand2", command=do_delete).pack(side="left", padx=10)

        tk.Button(btn_frame, text="批量导入", font=("微软雅黑", 12),
                 bg="#00897B", fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                 cursor="hand2", command=lambda: self._batch_import_accounts(dialog, refresh_tree)).pack(side="left", padx=10)

        tk.Button(btn_frame, text="关闭", font=("微软雅黑", 12),
                 bg="#e0e0e0", fg=COLOR_TEXT_DARK, bd=0, padx=20, pady=6,
                 cursor="hand2", command=dialog.destroy).pack(side="left", padx=10)

    # ==================== 批量导入账号 ====================
    def _batch_import_accounts(self, parent_dialog, refresh_tree):
        """从 CSV 文件批量导入考生账号。"""
        filepath = filedialog.askopenfilename(
            parent=parent_dialog,
            title="选择 CSV 文件",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")]
        )
        if not filepath:
            return

        # 自动检测编码
        content = None
        encoding = None
        for enc in ["utf-8", "gbk", "gb2312"]:
            try:
                with open(filepath, "r", encoding=enc) as f:
                    content = f.read()
                encoding = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if content is None:
            messagebox.showerror("错误", "无法识别文件编码，请使用 UTF-8 或 GBK 编码保存文件")
            return

        # 使用 csv 模块解析
        import csv, io
        reader = csv.reader(io.StringIO(content))
        success = 0
        skipped = 0
        failed = 0
        accounts = load_accounts()

        try:
            header = next(reader)
        except StopIteration:
            messagebox.showerror("错误", "CSV 文件为空")
            return

        # 映射列：找 姓名/准考证号/身份证号 列
        col_map = {}
        for i, col_name in enumerate(header):
            col_name = col_name.strip()
            if "准考证号" in col_name or "考号" in col_name:
                col_map["exam_id"] = i
            elif "姓名" in col_name or "名字" in col_name:
                col_map["name"] = i
            elif "身份证" in col_name or "身份证号" in col_name:
                col_map["id_number"] = i

        if "exam_id" not in col_map or "name" not in col_map:
            messagebox.showerror("错误", "CSV文件第一行必须包含「准考证号」「姓名」列标题")
            return
        if "id_number" not in col_map:
            col_map["id_number"] = None  # 身份证号可选

        seen_ids = set()
        for row_idx, row in enumerate(reader, start=2):
            # 跳过空行
            if not row or all(cell.strip() == "" for cell in row):
                continue

            try:
                exam_id = row[col_map["exam_id"]].strip() if len(row) > col_map["exam_id"] else ""
                name = row[col_map["name"]].strip() if len(row) > col_map["name"] else ""
                id_number = ""
                if col_map["id_number"] is not None and len(row) > col_map["id_number"]:
                    id_number = row[col_map["id_number"]].strip()

                if not exam_id:
                    failed += 1
                    continue

                if not name:
                    name = exam_id  # 姓名缺失用准考证号代替

                if not id_number:
                    id_number = exam_id  # 身份证号缺失用准考证号代替

                if exam_id in seen_ids:
                    skipped += 1
                    continue
                seen_ids.add(exam_id)

                # 合并到 accounts
                if exam_id in accounts and accounts[exam_id].get("is_admin"):
                    # 管理员账号不覆盖
                    skipped += 1
                    continue

                # 如果已存在，跳过（保留原有信息）
                if exam_id in accounts:
                    skipped += 1
                    continue

                accounts[exam_id] = {
                    "password": id_number,
                    "name": name,
                    "id_number": id_number,
                    "is_admin": False,
                }
                success += 1
            except Exception:
                failed += 1

        save_accounts(accounts)
        refresh_tree()
        msg = f"导入完成：成功 {success} 人"
        if skipped > 0:
            msg += f"，跳过（重复）{skipped} 人"
        if failed > 0:
            msg += f"，失败 {failed} 人"
        messagebox.showinfo("导入结果", msg)

    # ==================== 启动 ====================
    def _start_teacher_panel(self):
        """启动教师端面板（打包后通过 --mode teacher 启动）"""
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包模式：通过 --mode 参数启动同一 exe
            cmd = [sys.executable, "--mode", "teacher"]
        else:
            # 源码运行模式：直接运行 teacher_panel.py
            cmd = [sys.executable, os.path.join(EXAM_DIR, "teacher_panel.py")]
        try:
            subprocess.Popen(cmd,
                           creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)
        except Exception as e:
            messagebox.showerror("启动失败", f"无法启动教师端：{e}")


    # ==================== 题目管理 ====================
    def show_question_manager(self):
        bank = load_question_bank()
        dlg = tk.Toplevel(self.root)
        dlg.title("题目管理")
        dlg.geometry("1000x650")
        dlg.resizable(True, True)
        dlg.minsize(700, 450)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-500}+{sh//2-325}")

        tk.Label(dlg, text="题目管理 - 题库所有题目", font=("微软雅黑", 16, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 5))

        # Treeview
        tv_frame = tk.Frame(dlg, bg=COLOR_BG)
        tv_frame.pack(fill="both", expand=True, padx=15, pady=(5, 10))
        cols = ("qtype", "source", "content", "locked", "action")
        tree = ttk.Treeview(tv_frame, columns=cols, show="headings", height=18)
        tree.heading("qtype", text="题型")
        tree.heading("source", text="来源")
        tree.heading("content", text="内容摘要")
        tree.heading("locked", text="锁定")
        tree.heading("action", text="操作")
        tree.column("qtype", width=80, anchor="center")
        tree.column("source", width=80, anchor="center")
        tree.column("content", width=420, anchor="w")
        tree.column("locked", width=50, anchor="center")
        tree.column("action", width=60, anchor="center")
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        qmap = {}  # tree item iid -> (qtype, idx_in_pool)
        type_cn = {"single_choice": "单选题", "fill_blank": "填空题",
                   "programming": "编程题", "web_design": "网页设计",
                   "network_device": "网络设备", "graphic_design": "图形图像"}
        for qt in ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]:
            pool = bank.get(qt, [])
            for i, q in enumerate(pool):
                ct = q.get("content", "")[:60].replace("\n", " ")
                src = q.get("source", "")
                locked_mark = "🔒" if q.get("locked") else ""
                iid = tree.insert("", "end", values=(type_cn.get(qt, qt), src, ct, locked_mark, "编辑"))
                qmap[iid] = (qt, i)

        # 双击编辑
        def on_double(e):
            sel = tree.selection()
            if sel:
                _edit_question(sel[0])

        tree.bind("<Double-1>", on_double)

        def _edit_question(iid):
            qt, idx = qmap[iid]
            pool = bank.get(qt, [])
            if idx >= len(pool):
                return
            q = pool[idx]
            self._show_edit_dialog(qt, idx, q, dlg, bank)

        # 编辑按钮列点击
        def on_tree_click(e):
            region = tree.identify_region(e.x, e.y)
            if region == "cell":
                col = tree.identify_column(e.x)
                if col == "#5":
                    iid = tree.identify_row(e.y)
                    if iid in qmap:
                        _edit_question(iid)

        tree.bind("<Button-1>", on_tree_click)

        # 刷新按钮
        btn_f = tk.Frame(dlg, bg=COLOR_BG)
        btn_f.pack(pady=(0, 15))
        tk.Button(btn_f, text="刷新", font=("微软雅黑", 11),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=5,
                 cursor="hand2", command=lambda: (dlg.destroy(), self.show_question_manager())).pack(side="left", padx=5)
        tk.Button(btn_f, text="批量锁定/解锁", font=("微软雅黑", 11, "bold"),
                 bg="#FF8F00", fg=COLOR_WHITE, bd=0, padx=20, pady=5,
                 cursor="hand2", command=lambda: self._show_batch_lock_dialog(bank, dlg, tree, qmap)).pack(side="left", padx=5)
        tk.Button(btn_f, text="关闭", font=("微软雅黑", 11),
                 bg="#ccc", fg="#333", bd=0, padx=20, pady=5,
                 cursor="hand2", command=dlg.destroy).pack(side="left", padx=5)

    def _show_batch_lock_dialog(self, bank, parent_dlg, parent_tree=None, parent_qmap=None):
        """批量锁定/解锁题目对话框"""
        dlg = tk.Toplevel(parent_dlg)
        dlg.title("批量锁定/解锁题目")
        dlg.geometry("900x650")
        dlg.resizable(True, True)
        dlg.minsize(700, 500)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(parent_dlg)
        dlg.grab_set()

        dlg.update_idletasks()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{(sw - 900) // 2}+{(sh - 650) // 2}")

        # 顶部：题型筛选
        filter_frame = tk.Frame(dlg, bg=COLOR_BG)
        filter_frame.pack(fill="x", padx=15, pady=(15, 5))

        tk.Label(filter_frame, text="题型筛选：", font=("微软雅黑", 11, "bold"),
                bg=COLOR_BG, fg=COLOR_TEXT_DARK).pack(side="left", padx=(0, 10))

        type_var = tk.StringVar(value="all")
        type_cn = {"all": "全部", "single_choice": "单选题", "fill_blank": "填空题",
                   "programming": "编程题", "web_design": "网页设计",
                   "network_device": "网络设备", "graphic_design": "图形图像"}

        for key, val in type_cn.items():
            tk.Radiobutton(filter_frame, text=val, variable=type_var, value=key,
                          bg=COLOR_BG, fg=COLOR_TEXT_DARK, font=("微软雅黑", 10),
                          command=lambda: self._refresh_batch_list(scrollable_frame, bank, type_var.get(), checks)).pack(side="left", padx=5)

        # 全选/取消按钮
        btn_frame = tk.Frame(dlg, bg=COLOR_BG)
        btn_frame.pack(fill="x", padx=15, pady=(5, 10))

        tk.Button(btn_frame, text="全选", font=("微软雅黑", 10),
                 bg="#2196F3", fg=COLOR_WHITE, bd=0, padx=15, pady=5,
                 cursor="hand2", command=lambda: self._toggle_all_checks(checks, True)).pack(side="left", padx=5)
        tk.Button(btn_frame, text="取消全选", font=("微软雅黑", 10),
                 bg="#9E9E9E", fg=COLOR_WHITE, bd=0, padx=15, pady=5,
                 cursor="hand2", command=lambda: self._toggle_all_checks(checks, False)).pack(side="left", padx=5)

        # 题目列表（带复选框）
        list_frame = tk.Frame(dlg, bg=COLOR_BG)
        list_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        # 使用 Canvas + Frame 实现带复选框的列表
        canvas = tk.Canvas(list_frame, bg=COLOR_WHITE, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLOR_WHITE)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 存储复选框状态
        checks = {}  # (qtype, idx) -> BooleanVar

        # 初始加载
        self._refresh_batch_list(scrollable_frame, bank, "all", checks)

        # 底部：批量操作按钮
        bottom_frame = tk.Frame(dlg, bg=COLOR_BG)
        bottom_frame.pack(fill="x", padx=15, pady=(0, 15))

        def do_batch_lock(lock_flag):
            """执行批量锁定/解锁"""
            selected = [(qt, idx) for (qt, idx), var in checks.items() if var.get()]
            if not selected:
                messagebox.showwarning("提示", "请先选择要操作的题目！")
                return

            action = "锁定" if lock_flag else "解锁"
            if not messagebox.askyesno("确认", f"确定要{action}选中的 {len(selected)} 道题吗？"):
                return

            # 执行批量操作
            for qt, idx in selected:
                pool = bank.get(qt, [])
                if idx < len(pool):
                    pool[idx]["locked"] = lock_flag

            # 保存
            save_question_bank(bank)

            messagebox.showinfo("成功", f"已{action} {len(selected)} 道题！")

            # 刷新批量列表
            self._refresh_batch_list(scrollable_frame, bank, type_var.get(), checks)

            # 同步刷新父窗口题目管理列表的锁定状态
            if parent_tree is not None and parent_qmap is not None:
                type_cn = {"single_choice": "单选题", "fill_blank": "填空题",
                           "programming": "编程题", "web_design": "网页设计",
                           "network_device": "网络设备", "graphic_design": "图形图像"}
                for iid, (qt, idx) in parent_qmap.items():
                    pool = bank.get(qt, [])
                    if idx < len(pool):
                        q = pool[idx]
                        locked_mark = "🔒" if q.get("locked") else ""
                        vals = list(parent_tree.item(iid, "values"))
                        vals[3] = locked_mark
                        parent_tree.item(iid, values=vals)

        tk.Button(bottom_frame, text="批量锁定选中", font=("微软雅黑", 12, "bold"),
                 bg="#F44336", fg=COLOR_WHITE, bd=0, padx=25, pady=8,
                 cursor="hand2",
                 command=lambda: do_batch_lock(True)).pack(side="left", padx=10)

        tk.Button(bottom_frame, text="批量解锁选中", font=("微软雅黑", 12, "bold"),
                 bg="#4CAF50", fg=COLOR_WHITE, bd=0, padx=25, pady=8,
                 cursor="hand2",
                 command=lambda: do_batch_lock(False)).pack(side="left", padx=10)

        tk.Button(bottom_frame, text="关闭", font=("微软雅黑", 12),
                 bg="#9E9E9E", fg=COLOR_WHITE, bd=0, padx=25, pady=8,
                 cursor="hand2", command=dlg.destroy).pack(side="right", padx=10)

    def _refresh_batch_list(self, parent, bank, qtype, checks):
        """刷新批量操作题目列表"""
        # 清空现有控件
        for widget in parent.winfo_children():
            widget.destroy()

        checks.clear()

        type_cn = {"single_choice": "单选题", "fill_blank": "填空题",
                   "programming": "编程题", "web_design": "网页设计",
                   "network_device": "网络设备", "graphic_design": "图形图像"}

        types_to_show = [qtype] if qtype != "all" else ["single_choice", "fill_blank",
                                                            "programming", "web_design",
                                                            "network_device", "graphic_design"]

        row = 0
        for qt in types_to_show:
            pool = bank.get(qt, [])
            for i, q in enumerate(pool):
                if not isinstance(q, dict):
                    continue

                # 创建行框架
                row_frame = tk.Frame(parent, bg=COLOR_WHITE if row % 2 == 0 else "#f5f5f5",
                                    relief="solid", bd=0)
                row_frame.pack(fill="x", padx=5, pady=1)

                # 复选框
                var = tk.BooleanVar(value=False)
                check = tk.Checkbutton(row_frame, variable=var, bg=row_frame["bg"])
                check.pack(side="left", padx=(10, 5), pady=5)
                checks[(qt, i)] = var

                # 题型标签
                type_label = tk.Label(row_frame, text=type_cn.get(qt, qt),
                                      font=("微软雅黑", 9), width=8, anchor="w",
                                      bg=row_frame["bg"], fg="#666")
                type_label.pack(side="left", padx=(0, 10))

                # 锁定状态
                locked_text = "🔒 已锁定" if q.get("locked") else "○ 未锁定"
                locked_label = tk.Label(row_frame, text=locked_text,
                                       font=("微软雅黑", 9), width=10, anchor="w",
                                       bg=row_frame["bg"],
                                       fg="#D32F2F" if q.get("locked") else "#9E9E9E")
                locked_label.pack(side="left", padx=(0, 10))

                # 题目内容摘要
                content = q.get("content", "")[:80].replace("\n", " ")
                content_label = tk.Label(row_frame, text=content,
                                        font=("微软雅黑", 10), anchor="w",
                                        bg=row_frame["bg"], fg=COLOR_TEXT_DARK)
                content_label.pack(side="left", fill="x", expand=True, padx=(0, 10))

                row += 1

        if row == 0:
            tk.Label(parent, text="暂无题目", font=("微软雅黑", 12),
                    bg=COLOR_WHITE, fg="#999").pack(pady=50)

    def _toggle_all_checks(self, checks, value):
        """全选/取消全选"""
        for var in checks.values():
            var.set(value)

    def _show_edit_dialog(self, qt, idx, q, parent_dlg, bank):
        """弹出编辑对话框，预填现有数据"""
        ed = tk.Toplevel(parent_dlg)
        ed.title(f"编辑题目 - {qt}")
        ed.geometry("750x600")
        ed.resizable(True, True)
        ed.minsize(550, 400)
        ed.configure(bg=COLOR_BG)
        ed.transient(parent_dlg)
        ed.grab_set()

        canvas = tk.Canvas(ed, bg=COLOR_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(ed, orient="vertical", command=canvas.yview)
        ef = tk.Frame(canvas, bg=COLOR_BG)
        ef.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=ef, anchor="nw", width=660)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(15, 0), pady=10)
        scrollbar.pack(side="right", fill="y", padx=(0, 15), pady=10)

        # 内容
        tk.Label(ef, text="题干", font=("微软雅黑", 11, "bold"), bg=COLOR_BG).pack(anchor="w", pady=(5, 2))
        ct_txt = tk.Text(ef, height=5, font=("微软雅黑", 10), wrap="word", bd=1, relief="solid")
        ct_txt.pack(fill="x", pady=(0, 8))
        ct_txt.insert("1.0", q.get("content", ""))

        # 代码（填空题）
        code_txt = None
        if qt == "fill_blank":
            tk.Label(ef, text="参考代码", font=("微软雅黑", 11, "bold"), bg=COLOR_BG).pack(anchor="w", pady=(5, 2))
            code_txt = tk.Text(ef, height=4, font=("Consolas", 9), wrap="none", bd=1, relief="solid")
            code_txt.pack(fill="x", pady=(0, 8))
            code_txt.insert("1.0", q.get("code", ""))

        # 选项（单选题）
        opt_entries = []
        if qt == "single_choice":
            tk.Label(ef, text="选项", font=("微软雅黑", 11, "bold"), bg=COLOR_BG).pack(anchor="w", pady=(5, 2))
            for i, label in enumerate(["A", "B", "C", "D"]):
                rf = tk.Frame(ef, bg=COLOR_BG)
                rf.pack(fill="x", pady=1)
                tk.Label(rf, text=f"{label}.", font=("微软雅黑", 10), bg=COLOR_BG, width=3).pack(side="left")
                ev = tk.Entry(rf, font=("微软雅黑", 10), bd=1, relief="solid")
                ev.pack(side="left", fill="x", expand=True)
                opts = q.get("options", [])
                if i < len(opts):
                    ev.insert(0, opts[i])
                opt_entries.append(ev)

        # 答案
        ans_var = tk.StringVar()
        tk.Label(ef, text="答案", font=("微软雅黑", 11, "bold"), bg=COLOR_BG).pack(anchor="w", pady=(10, 2))
        if qt == "single_choice":
            af = tk.Frame(ef, bg=COLOR_BG)
            af.pack(fill="x", pady=(0, 8))
            ans_var.set(str(q.get("answer", 0)))
            for i, label in enumerate(["A", "B", "C", "D"]):
                tk.Radiobutton(af, text=label, variable=ans_var, value=str(i),
                              bg=COLOR_BG, font=("微软雅黑", 10)).pack(side="left", padx=4)
        elif qt == "fill_blank":
            af = tk.Frame(ef, bg=COLOR_BG)
            af.pack(fill="x", pady=(0, 8))
            blanks = q.get("blanks", [])
            bl_vars = []
            for j, bl in enumerate(blanks):
                tk.Label(af, text=f"空{j+1}({bl.get('label','')})：", font=("微软雅黑", 9), bg=COLOR_BG).pack(side="left")
                bv = tk.StringVar(value=chr(65 + bl.get("answer", 0)))
                ttk.Combobox(af, textvariable=bv, values=list("ABCDEFGHIJ"),
                            state="readonly", font=("微软雅黑", 9), width=3).pack(side="left", padx=(0, 8))
                bl_vars.append(bv)
            ans_var._bl_vars = bl_vars
        else:
            ans_entry = tk.Entry(ef, font=("微软雅黑", 10), bd=1, relief="solid")
            ans_entry.pack(fill="x", pady=(0, 8))
            ans_entry.insert(0, str(q.get("answer", q.get("reference_answer", ""))))
            ans_var._entry = ans_entry

        # 来源
        tk.Label(ef, text="来源", font=("微软雅黑", 11, "bold"), bg=COLOR_BG).pack(anchor="w", pady=(5, 2))
        src_var = tk.StringVar(value=q.get("source", ""))
        tk.Entry(ef, textvariable=src_var, font=("微软雅黑", 10), bd=1, relief="solid").pack(fill="x", pady=(0, 8))

        # 知识点
        tk.Label(ef, text="知识点", font=("微软雅黑", 11, "bold"), bg=COLOR_BG).pack(anchor="w", pady=(5, 2))
        kp_var = tk.StringVar(value=q.get("knowledge_points", ""))
        tk.Entry(ef, textvariable=kp_var, font=("微软雅黑", 10), bd=1, relief="solid").pack(fill="x", pady=(0, 8))

        # 锁定开关
        lock_var = tk.BooleanVar(value=q.get("locked", False))
        tk.Checkbutton(ef, text="🔒 锁定（需激活码解锁）", variable=lock_var,
                      font=("微软雅黑", 10, "bold"), fg="#C62828", bg=COLOR_BG,
                      activebackground=COLOR_BG).pack(anchor="w", pady=(5, 8))

        def do_save():
            try:
                old_content = q.get("content", "").strip()
                new_content = ct_txt.get("1.0", "end-1c").strip()
                if not new_content:
                    messagebox.showwarning("提示", "题干不能为空")
                    return
                q["content"] = new_content
                q["source"] = src_var.get().strip()
                q["knowledge_points"] = kp_var.get().strip()
                q["locked"] = lock_var.get()
                if qt == "single_choice":
                    opts = [e.get().strip() for e in opt_entries]
                    q["options"] = opts
                    q["answer"] = int(ans_var.get())
                elif qt == "fill_blank":
                    if code_txt:
                        q["code"] = code_txt.get("1.0", "end-1c").strip()
                    bl_vars = getattr(ans_var, "_bl_vars", [])
                    for j, bl in enumerate(q.get("blanks", [])):
                        if j < len(bl_vars):
                            bl["answer"] = ord(bl_vars[j].get()) - 65
                elif hasattr(ans_var, "_entry"):
                    q["answer"] = ans_var._entry.get().strip()
                save_question_bank(bank)
                messagebox.showinfo("成功", "修改已保存")
                ed.destroy()
            except Exception as e:
                messagebox.showerror("保存失败", str(e))

        bf = tk.Frame(ef, bg=COLOR_BG)
        bf.pack(pady=(10, 5))
        tk.Button(bf, text="保存修改", font=("微软雅黑", 11, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                 cursor="hand2", command=do_save).pack(side="left", padx=5)
        tk.Button(bf, text="取消", font=("微软雅黑", 11),
                 bg="#ccc", fg="#333", bd=0, padx=20, pady=6,
                 cursor="hand2", command=ed.destroy).pack(side="left", padx=5)

    # ==================== 搜索筛选 ====================
    def show_search_dialog(self):
        bank = load_question_bank()
        dlg = tk.Toplevel(self.root)
        dlg.title("搜索筛选题目")
        dlg.geometry("1000x650")
        dlg.resizable(True, True)
        dlg.minsize(700, 450)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-500}+{sh//2-325}")

        # 搜索栏
        sf = tk.Frame(dlg, bg=COLOR_BG)
        sf.pack(fill="x", padx=15, pady=(15, 5))
        tk.Label(sf, text="关键词：", font=("微软雅黑", 11), bg=COLOR_BG).pack(side="left")
        kw_var = tk.StringVar()
        kw_entry = tk.Entry(sf, textvariable=kw_var, font=("微软雅黑", 11), width=20, bd=1, relief="solid")
        kw_entry.pack(side="left", padx=5)
        tk.Label(sf, text="题型：", font=("微软雅黑", 11), bg=COLOR_BG).pack(side="left", padx=(15, 5))
        tp_var = tk.StringVar(value="全部")
        tps = ["全部", "单选", "填空", "编程", "网页设计", "网络设备", "图形图像"]
        tpcb = ttk.Combobox(sf, textvariable=tp_var, values=tps, state="readonly",
                           font=("微软雅黑", 11), width=10)
        tpcb.pack(side="left", padx=5)
        tk.Label(sf, text="来源：", font=("微软雅黑", 11), bg=COLOR_BG).pack(side="left", padx=(15, 5))
        src_var = tk.StringVar()
        tk.Entry(sf, textvariable=src_var, font=("微软雅黑", 11), width=12, bd=1, relief="solid").pack(side="left", padx=5)

        # Treeview
        tv_frame = tk.Frame(dlg, bg=COLOR_BG)
        tv_frame.pack(fill="both", expand=True, padx=15, pady=(5, 10))
        cols = ("no", "qtype", "source", "content")
        tree = ttk.Treeview(tv_frame, columns=cols, show="headings", height=16)
        tree.heading("no", text="题号")
        tree.heading("qtype", text="题型")
        tree.heading("source", text="来源")
        tree.heading("content", text="内容摘要")
        tree.column("no", width=50, anchor="center")
        tree.column("qtype", width=90, anchor="center")
        tree.column("source", width=90, anchor="center")
        tree.column("content", width=450, anchor="w")
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        qmap = {}
        type_cn = {"single_choice": "单选题", "fill_blank": "填空题",
                   "programming": "编程题", "web_design": "网页设计",
                   "network_device": "网络设备", "graphic_design": "图形图像"}
        tp_filter = {"单选": "single_choice", "填空": "fill_blank", "编程": "programming",
                    "网页设计": "web_design", "网络设备": "network_device", "图形图像": "graphic_design"}

        def do_search():
            for item in tree.get_children():
                tree.delete(item)
            qmap.clear()
            kw = kw_var.get().strip().lower()
            tp = tp_var.get()
            src_f = src_var.get().strip().lower()
            no = 0
            for qt in ["single_choice", "fill_blank", "programming", "web_design", "network_device", "graphic_design"]:
                if tp != "全部" and tp_filter.get(tp) != qt:
                    continue
                pool = bank.get(qt, [])
                for i, q in enumerate(pool):
                    ct = q.get("content", "")
                    src = q.get("source", "").lower()
                    if kw and kw not in ct.lower():
                        continue
                    if src_f and src_f not in src:
                        continue
                    no += 1
                    preview = ct[:60].replace("\n", " ")
                    iid = tree.insert("", "end", values=(no, type_cn.get(qt, qt),
                                       q.get("source", ""), preview))
                    qmap[iid] = (qt, i)

        # 搜索触发
        kw_entry.bind("<KeyRelease>", lambda e: do_search())
        tpcb.bind("<<ComboboxSelected>>", lambda e: do_search())

        def on_double(e):
            sel = tree.selection()
            if sel and sel[0] in qmap:
                qt, idx = qmap[sel[0]]
                pool = bank.get(qt, [])
                if idx < len(pool):
                    self._show_edit_dialog(qt, idx, pool[idx], dlg)

        tree.bind("<Double-1>", on_double)

        # 按钮
        bf = tk.Frame(dlg, bg=COLOR_BG)
        bf.pack(pady=(0, 15))
        tk.Button(bf, text="搜索", font=("微软雅黑", 11),
                 bg=COLOR_ACCENT, fg=COLOR_WHITE, bd=0, padx=20, pady=5,
                 cursor="hand2", command=do_search).pack(side="left", padx=5)
        tk.Button(bf, text="关闭", font=("微软雅黑", 11),
                 bg="#ccc", fg="#333", bd=0, padx=20, pady=5,
                 cursor="hand2", command=dlg.destroy).pack(side="left", padx=5)

        do_search()

    # ==================== Excel批量导入 ====================
    def import_excel(self):
        filepath = filedialog.askopenfilename(
            title="选择 Excel 文件", filetypes=[("Excel文件", "*.xlsx"), ("所有文件", "*.*")])
        if not filepath:
            return
        try:
            import openpyxl
            wb = openpyxl.load_workbook(filepath)
            ws = wb.active
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            if not rows:
                messagebox.showwarning("提示", "Excel 文件无数据行")
                return
            total = 0
            succ = 0
            skip = 0
            fail = 0
            tp_map = {"单选题": "single_choice", "填空": "fill_blank", "填空题": "fill_blank",
                     "编程": "programming", "编程题": "programming",
                     "网页设计": "web_design", "网页": "web_design",
                     "网络设备": "network_device", "网络": "network_device",
                     "图形图像": "graphic_design", "图形": "graphic_design",
                     "单选": "single_choice"}
            for row in rows:
                total += 1
                try:
                    if not row or len(row) < 2:
                        fail += 1
                        continue
                    tp_raw = str(row[0]).strip() if row[0] else ""
                    content = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                    qt = tp_map.get(tp_raw, "")
                    if not qt or not content:
                        fail += 1
                        continue
                    code = str(row[2]).strip() if len(row) > 2 and row[2] else ""
                    opts_raw = str(row[3]).strip() if len(row) > 3 and row[3] else ""
                    ans = str(row[4]).strip() if len(row) > 4 and row[4] else ""
                    diff = str(row[5]).strip() if len(row) > 5 and row[5] else "中"
                    src = str(row[6]).strip() if len(row) > 6 and row[6] else ""
                    kp = str(row[7]).strip() if len(row) > 7 and row[7] else ""

                    # 去重检查
                    pool = self.question_bank.get(qt, [])
                    dup = any(q.get("content", "").strip() == content for q in pool)
                    if dup:
                        skip += 1
                        continue

                    q = {"content": content, "difficulty": diff or "中", "source": src}
                    if kp:
                        q["knowledge_points"] = kp

                    if qt == "single_choice":
                        opts = [o.strip() for o in opts_raw.split("##")] if opts_raw else []
                        if len(opts) < 4:
                            fail += 1
                            continue
                        q["options"] = opts[:4]
                        try:
                            q["answer"] = int(ans) if ans else 0
                        except ValueError:
                            q["answer"] = ord(ans.upper()) - 65 if ans else 0
                        q["score"] = 2
                    elif qt == "fill_blank":
                        if code:
                            q["code"] = code
                        shared = [o.strip() for o in opts_raw.split("##")] if opts_raw else []
                        q["shared_options"] = shared
                        blanks = []
                        for bi in range(5):
                            blanks.append({"label": f"【{bi+1}】", "answer": 0})
                        q["blanks"] = blanks
                        q["score"] = 10
                    elif qt in ("programming", "web_design", "network_device", "graphic_design"):
                        if qt == "programming":
                            q["score"] = 20
                        elif qt == "web_design":
                            q["score"] = 30
                        elif qt == "network_device":
                            q["score"] = 15
                            q["text_fields"] = [{"label": "(1) 输出结果"}]
                        else:
                            q["score"] = 30
                        q["reference_answer"] = ans if qt == "programming" else ({} if qt == "network_device" else "")

                    q["id"] = len(pool) + 1000 + succ
                    self.question_bank.setdefault(qt, []).append(q)
                    succ += 1
                except Exception:
                    fail += 1

            save_question_bank(self.question_bank)
            msg = f"导入完成\n总行数: {total}\n成功: {succ}\n跳过重复: {skip}\n失败: {fail}"
            messagebox.showinfo("导入结果", msg)
            self.show_admin_panel()
        except ImportError:
            messagebox.showerror("错误", "缺少 openpyxl 库，请安装：pip install openpyxl")
        except Exception as e:
            messagebox.showerror("导入失败", f"文件解析错误：{str(e)}")

    # ==================== 启动 ====================
    def run(self):
        self.root.mainloop()


    # ==================== 打字练习入口 ====================
    def open_typing_practice(self):
        """打开打字练习（WordSprite 英文打字游戏）— 子进程方式启动，避免 Tcl 线程冲突"""
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包模式：通过 --mode 参数启动同一 exe
            cmd = [sys.executable, "--mode", "typing"]
        else:
            # 源码运行模式：直接运行 typing_launcher.py
            cmd = [sys.executable, os.path.join(EXAM_DIR, "typing_launcher.py")]
        try:
            subprocess.Popen(cmd,
                           creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)
        except Exception as e:
            messagebox.showerror("启动失败", f"无法启动打字练习：\n{e}")


# ==================== 打字练习窗口类 ====================
class TypingPracticeWindow:
    """打字练习窗口 - 仿金山打字通"""

    # 英文练习素材
    ENGLISH_TEXTS = [
        "The quick brown fox jumps over the lazy dog.",
        "Practice makes perfect. Keep typing and you will improve your speed and accuracy over time.",
        "Success is not final, failure is not fatal: it is the courage to continue that counts.",
        "In the middle of difficulty lies opportunity. The best way to predict the future is to create it.",
        "Programming is the art of telling another human what one wants the computer to do.",
        "Life is what happens when you are busy making other plans. Stay focused and keep learning.",
        "Technology is best when it brings people together. The advance of technology is based on making it fit in.",
        "The only way to do great work is to love what you do. If you have not found it yet, keep looking.",
        "It does not matter how slowly you go as long as you do not stop. Persistence is the key to success.",
        "Debugging is twice as hard as writing the code in the first place. Therefore, write code as cleverly as possible.",
        "Simplicity is the soul of efficiency. Any fool can write code that a computer can understand.",
        "First, solve the problem. Then, write the code. Always code as if the guy who ends up maintaining your code is a violent psychopath who knows where you live.",
        "The computer was born to solve problems that did not exist before. Software is a great combination of artistry and engineering.",
        "Every great developer you know got there by solving problems they were unqualified to solve until they actually did it.",
        "Walking on water and developing software from a specification are easy if both are frozen.",
        "Measuring programming progress by lines of code is like measuring aircraft building progress by weight.",
        "Python is an interpreted, high-level, general-purpose programming language. Its design philosophy emphasizes code readability.",
    ]

    # 中文练习素材
    CHINESE_TEXTS = [
        "春眠不觉晓，处处闻啼鸟。夜来风雨声，花落知多少。",
        "床前明月光，疑是地上霜。举头望明月，低头思故乡。",
        "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。",
        "锄禾日当午，汗滴禾下土。谁知盘中餐，粒粒皆辛苦。",
        "松下问童子，言师采药去。只在此山中，云深不知处。",
        "千山鸟飞绝，万径人踪灭。孤舟蓑笠翁，独钓寒江雪。",
        "人生自古谁无死，留取丹心照汗青。",
        "学而时习之，不亦说乎？有朋自远方来，不亦乐乎？",
        "三人行，必有我师焉。择其善者而从之，其不善者而改之。",
        "温故而知新，可以为师矣。学而不思则罔，思而不学则殆。",
        "己所不欲，勿施于人。见贤思齐焉，见不贤而内自省也。",
        "不积跬步，无以至千里；不积小流，无以成江海。",
        "故天将降大任于是人也，必先苦其心志，劳其筋骨，饿其体肤。",
        "路漫漫其修远兮，吾将上下而求索。",
        "天生我材必有用，千金散尽还复来。长风破浪会有时，直挂云帆济沧海。",
        "山重水复疑无路，柳暗花明又一村。纸上得来终觉浅，绝知此事要躬行。",
        "众里寻他千百度，蓦然回首，那人却在灯火阑珊处。",
        "海内存知己，天涯若比邻。无为在歧路，儿女共沾巾。",
    ]

    def __init__(self, parent):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("打字练习")
        self.window.state('zoomed')
        self.window.configure(bg=COLOR_BG_MAIN)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---- 状态变量 ----
        self.mode = "english"  # "english" or "chinese"
        self.current_text = ""
        self.current_index = 0       # 当前待打字符在原文中的索引
        self.total_keystrokes = 0    # 总击键数
        self.correct_keystrokes = 0  # 正确击键数
        self.wrong_chars = set()     # 已打错位置的索引集合（退格可清除，用于UI对照）
        self.wrong_char_counter = {}  # 记录具体错字及次数 {字符: 次数}
        self._resetting = False       # 正在重置状态时阻止自动开始
        self.is_running = False
        self.is_paused = False
        self.is_countdown = False    # 是否使用倒计时模式
        self.time_elapsed = 0        # 已用时间(秒)
        self.time_limit = 0          # 时间限制(秒), 0=无限制
        self.accuracy_requirement = 0  # 正确率要求(0-100), 0=无要求
        self.countdown_remaining = 0
        self.timer_id = None
        self.caps_lock = False

        # ---- 中文模式专用 ----
        self.chinese_entry_var = tk.StringVar()
        self.imported_file = os.path.join(EXAM_DIR, "imported_texts.json")
        self.imported_texts = {"english": [], "chinese": []}  # [{"title":..., "content":...}, ...]
        self._load_imported_texts()

        self._build_ui()
        self._load_new_text()
        self.stats_file = os.path.join(EXAM_DIR, "typo_stats.json")
        self.window.after(200, lambda: self._switch_input_method(self.mode))

    def _load_imported_texts(self):
        """从文件加载已导入文章"""
        if os.path.exists(self.imported_file):
            try:
                with open(self.imported_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "english" in data and "chinese" in data:
                    # 兼容旧格式：纯字符串列表转新格式
                    for lang in ("english", "chinese"):
                        converted = []
                        for item in data[lang]:
                            if isinstance(item, str):
                                # 旧格式：纯字符串，取第一行作为标题
                                first_line = item.strip().split('\n')[0][:30]
                                converted.append({"title": first_line, "content": item})
                            elif isinstance(item, dict):
                                converted.append(item)
                        self.imported_texts[lang] = converted
            except Exception:
                pass

    def _save_imported_texts(self):
        """保存已导入文章到文件"""
        try:
            with open(self.imported_file, "w", encoding="utf-8") as f:
                json.dump(self.imported_texts, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _switch_input_method(self, mode):
        """切换系统输入法：中文→微软拼音，英文→美式键盘"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            if mode == "chinese":
                user32.LoadKeyboardLayoutW("00000804", 1)  # 简体中文-微软拼音
            else:
                user32.LoadKeyboardLayoutW("00000804", 1)  # 微软拼音（英文模式）
        except Exception:
            pass  # 静默失败

    def _save_error_record(self):
        """保存本次练习的错误记录（仅英文模式）"""
        if self.mode != "english":
            return
        try:
            records = []
            if os.path.exists(self.stats_file):
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    records = json.load(f)
            records.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "wrong_chars": dict(self.wrong_char_counter)
            })
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 静默失败，不影响练习

    # ==================== UI构建 ====================
    def _build_ui(self):
        sw = self.window.winfo_screenwidth()
        sh = self.window.winfo_screenheight()

        # ---- 顶部状态栏 ----
        top_bar = tk.Frame(self.window, bg=COLOR_BG_TOP, height=56)
        top_bar.pack(fill="x")
        top_bar.pack_propagate(False)

        inner_top = tk.Frame(top_bar, bg=COLOR_BG_TOP)
        inner_top.pack(expand=True)

        # 计时显示
        self.lbl_timer = tk.Label(inner_top, text="00:00", font=("Consolas", 18, "bold"),
                                  fg=COLOR_TEXT_LIGHT, bg=COLOR_BG_TOP, width=8)
        self.lbl_timer.pack(side="left", padx=(10, 30))

        # 速度
        speed_frame = tk.Frame(inner_top, bg=COLOR_BG_TOP)
        speed_frame.pack(side="left", padx=20)
        tk.Label(speed_frame, text="速度", font=("微软雅黑", 10),
                fg="#b0d4f1", bg=COLOR_BG_TOP).pack()
        self.lbl_speed = tk.Label(speed_frame, text="0 字/分", font=("微软雅黑", 14, "bold"),
                                   fg=COLOR_TEXT_LIGHT, bg=COLOR_BG_TOP)
        self.lbl_speed.pack()

        # 正确率
        acc_frame = tk.Frame(inner_top, bg=COLOR_BG_TOP)
        acc_frame.pack(side="left", padx=20)
        tk.Label(acc_frame, text="正确率", font=("微软雅黑", 10),
                fg="#b0d4f1", bg=COLOR_BG_TOP).pack()
        self.lbl_accuracy = tk.Label(acc_frame, text="100%", font=("微软雅黑", 14, "bold"),
                                      fg=COLOR_SUCCESS, bg=COLOR_BG_TOP)
        self.lbl_accuracy.pack()

        # 模式标签
        self.lbl_mode = tk.Label(inner_top, text="英文模式", font=("微软雅黑", 12, "bold"),
                                  fg="#FFD54F", bg=COLOR_BG_TOP, width=10)
        self.lbl_mode.pack(side="right", padx=30)

        # 限时 / 正确率要求
        req_frame = tk.Frame(inner_top, bg=COLOR_BG_TOP)
        req_frame.pack(side="right", padx=20)
        self.lbl_requirement = tk.Label(req_frame, text="", font=("微软雅黑", 9),
                                         fg="#ffcc80", bg=COLOR_BG_TOP)
        self.lbl_requirement.pack()

        # ---- 控制按钮栏 ----
        ctrl_bar = tk.Frame(self.window, bg="#e3f2fd", height=44)
        ctrl_bar.pack(fill="x")
        ctrl_bar.pack_propagate(False)

        btn_style = {"font": ("微软雅黑", 10, "bold"), "bd": 0, "padx": 16, "pady": 5,
                     "cursor": "hand2"}

        self.btn_start = tk.Button(ctrl_bar, text="开始练习", bg=COLOR_SUCCESS, fg=COLOR_WHITE,
                                    command=self._start_practice, **btn_style)
        self.btn_start.pack(side="left", padx=(15, 5), pady=5)

        self.btn_pause = tk.Button(ctrl_bar, text="暂停", bg=COLOR_WARNING, fg=COLOR_WHITE,
                                    command=self._toggle_pause, **btn_style)
        self.btn_pause.pack(side="left", padx=5, pady=5)
        self.btn_pause.configure(state="disabled")

        self.btn_reset = tk.Button(ctrl_bar, text="重置", bg="#78909C", fg=COLOR_WHITE,
                                    command=self._reset_practice, **btn_style)
        self.btn_reset.pack(side="left", padx=5, pady=5)

        self.btn_new_text = tk.Button(ctrl_bar, text="换一篇", bg=COLOR_ACCENT, fg=COLOR_WHITE,
                                       command=self._load_new_text, **btn_style)
        self.btn_new_text.pack(side="left", padx=5, pady=5)

        tk.Button(ctrl_bar, text="选文章", bg="#00695C", fg=COLOR_WHITE,
                  command=self._select_text, **btn_style).pack(side="left", padx=5, pady=5)

        tk.Button(ctrl_bar, text="导入文章", bg="#E65100", fg=COLOR_WHITE,
                  command=self._import_text, **btn_style).pack(side="left", padx=5, pady=5)

        tk.Button(ctrl_bar, text="英文/中文", bg="#7B1FA2", fg=COLOR_WHITE,
                  command=self._toggle_mode, **btn_style).pack(side="left", padx=5, pady=5)

        tk.Button(ctrl_bar, text="设置", bg="#546E7A", fg=COLOR_WHITE,
                  command=self._show_settings, **btn_style).pack(side="right", padx=(5, 5), pady=5)
        tk.Button(ctrl_bar, text="错误统计", bg="#00838F", fg=COLOR_WHITE,
                  command=self._show_error_stats, **btn_style).pack(side="right", padx=5, pady=5)

        # ---- 主内容区 ----
        main_area = tk.Frame(self.window, bg=COLOR_BG_MAIN)
        main_area.pack(fill="both", expand=True, padx=30, pady=(15, 5))

        # 练习显示区：每行原文Label + 输入Entry成对，白底简约
        self.LINES_PER_PAGE = 5
        self.line_width = 45

        self.page_label = tk.Label(main_area, text="", font=("微软雅黑", 10),
                                    fg=COLOR_TEXT_DARK, bg=COLOR_BG_MAIN, anchor="e")
        self.page_label.pack(fill="x", pady=(0, 6))

        self.practice_frame = tk.Frame(main_area, bg=COLOR_BG_MAIN)
        self.practice_frame.pack(fill="x", expand=False)

        # 每行一对 (Label + Entry) 的动态列表
        self.line_labels = []
        self.line_entries = []
        self.line_vars = []

        # 中文输入框（仅中文模式显示，英文模式下隐藏）
        self.entry_input = tk.Entry(main_area, font=("微软雅黑", 18),
                                     bd=1, relief="solid", bg="#F5F5F5",
                                     textvariable=self.chinese_entry_var)
        # 初始化时根据模式决定是否显示
        if self.mode == "chinese":
            self.entry_input.pack(fill="x", ipady=4, padx=4, pady=(6, 0))

        # 错误提示标签
        self.lbl_error = tk.Label(main_area, text="", font=("微软雅黑", 10, "bold"),
                                   fg=COLOR_DANGER, bg=COLOR_BG_MAIN, anchor="w")
        self.lbl_error.pack(fill="x", pady=(5, 0))

        # 中文模式：监听Entry变化
        self.chinese_entry_var.trace_add("write", self._on_chinese_input_change)
        self._last_chinese_content = ""

    # ==================== 文本加载与显示 ====================
    def _load_new_text(self):
        """加载新的练习文本"""
        # 换文前保存当前练习的记录
        if self.is_running and self.wrong_chars:
            self._save_error_record()
        import random
        if self.mode == "english":
            pool = self.ENGLISH_TEXTS + [t["content"] for t in self.imported_texts["english"]]
        else:
            pool = self.CHINESE_TEXTS + [t["content"] for t in self.imported_texts["chinese"]]
        self.current_text = random.choice(pool)
        self.lines = []
        self._reset_state()
        # 强制重建界面（清除旧文章的 Entry 控件和分页缓存）
        self._last_page_line = -1
        self.line_labels.clear()
        self.line_entries.clear()
        self.line_vars.clear()
        for widget in self.practice_frame.winfo_children():
            widget.destroy()
        self._update_display()
        self._update_stats()

    def _import_text(self):
        """在弹窗中输入文章内容进行导入，含标题设置"""
        dlg = tk.Toplevel(self.window)
        dlg.title("导入文章")
        dlg.geometry("580x520")
        dlg.resizable(True, True)
        dlg.minsize(400, 380)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.window)

        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-290}+{sh//2-260}")

        # 标题输入
        title_frame = tk.Frame(dlg, bg=COLOR_BG)
        title_frame.pack(fill="x", padx=20, pady=(12, 5))
        tk.Label(title_frame, text="标题", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(side="left")
        title_entry = tk.Entry(title_frame, font=("微软雅黑", 11), bd=1, relief="solid")
        title_entry.pack(side="left", fill="x", expand=True, padx=(10, 0))

        # 内容标签
        tk.Label(dlg, text="文章内容", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG, anchor="w").pack(fill="x", padx=20, pady=(8, 2))

        # 输入区
        text_frame = tk.Frame(dlg, bg=COLOR_WHITE, bd=1, relief="solid",
                              height=320, width=540)
        text_frame.pack(padx=20, pady=(0, 6))
        text_frame.pack_propagate(False)

        text_area = tk.Text(text_frame, font=("微软雅黑", 11), wrap="word",
                           bd=0, padx=10, pady=8, undo=True)
        text_area.pack(fill="both", expand=True)

        # 底部区域
        bottom_frame = tk.Frame(dlg, bg=COLOR_BG)
        bottom_frame.pack(fill="x", padx=20, pady=(0, 10))

        lbl_count = tk.Label(bottom_frame, text="0 字符", font=("微软雅黑", 9),
                            fg="#999", bg=COLOR_BG)
        lbl_count.pack(side="left")

        def update_count(*args):
            content = text_area.get("1.0", "end-1c")
            lbl_count.configure(text=f"{len(content)} 字符")

        text_area.bind("<KeyRelease>", update_count)

        # 按钮区
        btn_frame = tk.Frame(bottom_frame, bg=COLOR_BG)
        btn_frame.pack(side="right")

        def do_import():
            content = text_area.get("1.0", "end-1c").strip()
            if not content:
                messagebox.showwarning("内容为空", "请输入或粘贴文章内容。")
                return

            title = title_entry.get().strip()
            if not title:
                # 默认取内容第一行作为标题
                title = content.split('\n')[0].strip()[:30]

            cjk_count = sum(1 for ch in content if '\u4e00' <= ch <= '\u9fff')
            ratio = cjk_count / len(content) if content else 0
            detected_mode = "chinese" if ratio > 0.5 else "english"

            dlg.destroy()

            self.imported_texts[detected_mode].append({"title": title, "content": content})
            self._save_imported_texts()

            if self.mode != detected_mode:
                self.mode = detected_mode
                self._switch_input_method(self.mode)

            self.current_text = content
            self.lines = []
            self._reset_state()
            self._update_display()
            self._update_stats()

        tk.Button(btn_frame, text="导  入", bg=COLOR_ACCENT, fg=COLOR_WHITE,
                  font=("微软雅黑", 11), width=12, command=do_import).pack(side="right", padx=(10, 0))
        tk.Button(btn_frame, text="取  消", bg="#9E9E9E", fg=COLOR_WHITE,
                  font=("微软雅黑", 11), width=12, command=dlg.destroy).pack(side="right")

    def _select_text(self):
        """选择特定文章进行练习，支持删除"""
        texts = self._get_all_texts()
        if not texts:
            messagebox.showinfo("提示", "当前没有任何可用的文章，请先导入文章。")
            return

        dlg = tk.Toplevel(self.window)
        dlg.title("选择文章")
        dlg.geometry("650x460")
        dlg.resizable(True, True)
        dlg.minsize(450, 300)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.window)

        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-325}+{sh//2-230}")

        tk.Label(dlg, text="选择练习文章", font=("微软雅黑", 15, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(15, 10))

        # 列表框架
        list_frame = tk.Frame(dlg, bg=COLOR_WHITE, bd=1, relief="solid")
        list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")

        listbox = tk.Listbox(list_frame, font=("Microsoft YaHei", 10), yscrollcommand=scrollbar.set,
                            selectmode="single", activestyle="none", bg=COLOR_WHITE,
                            fg=COLOR_TEXT_DARK, selectbackground=COLOR_ACCENT, selectforeground=COLOR_WHITE,
                            bd=0, highlightthickness=0)
        scrollbar.config(command=listbox.yview)

        # 填充列表
        idx_map = {}
        for i, item in enumerate(texts):
            display = f"[{item['source']}]  {item['title']}"
            listbox.insert(tk.END, display)
            idx_map[i] = item

        listbox.pack(fill="both", expand=True, padx=(5, 0), pady=5)

        # 当前文章高亮
        for i, item in enumerate(texts):
            if item['content'] == self.current_text:
                listbox.selection_set(i)
                listbox.activate(i)
                listbox.see(i)
                break

        # 底部按钮区
        btn_frame = tk.Frame(dlg, bg=COLOR_BG)
        btn_frame.pack(fill="x", padx=20, pady=(0, 15))

        def do_select():
            sel = listbox.curselection()
            if not sel:
                return
            item = idx_map[sel[0]]
            dlg.destroy()
            self.current_text = item['content']
            self.lines = []
            self._reset_state()
            # 强制重建界面，清除旧 Entry 内容和分页缓存
            self._last_page_line = -1
            self.line_labels.clear()
            self.line_entries.clear()
            self.line_vars.clear()
            for widget in self.practice_frame.winfo_children():
                widget.destroy()
            self._update_display()
            self._update_stats()

        def do_delete():
            sel = listbox.curselection()
            if not sel:
                messagebox.showwarning("提示", "请先选择要删除的文章。")
                return
            item = idx_map[sel[0]]
            if item['source_type'] != 'imported':
                messagebox.showinfo("提示", "内置文章不可删除。")
                return
            ok = messagebox.askyesno("确认删除", f"确定删除文章「{item['title']}」吗？")
            if not ok:
                return
            # 从数据中移除
            lang = self.mode
            self.imported_texts[lang] = [
                t for t in self.imported_texts[lang]
                if t["content"] != item['content']
            ]
            self._save_imported_texts()
            # 刷新列表
            listbox.delete(sel[0])
            del idx_map[sel[0]]
            # 重建 idx_map
            new_map = {}
            new_idx = 0
            for old_i, old_item in sorted(idx_map.items()):
                new_map[new_idx] = old_item
                new_idx += 1
            idx_map.clear()
            idx_map.update(new_map)

        tk.Button(btn_frame, text="确定", bg=COLOR_ACCENT, fg=COLOR_WHITE,
                  font=("微软雅黑", 11), width=8, command=do_select).pack(side="right", padx=(8, 0))
        tk.Button(btn_frame, text="删除", bg="#E57373", fg=COLOR_WHITE,
                  font=("微软雅黑", 11), width=8, command=do_delete).pack(side="right", padx=(8, 0))
        tk.Button(btn_frame, text="取消", bg="#9E9E9E", fg=COLOR_WHITE,
                  font=("微软雅黑", 11), width=8, command=dlg.destroy).pack(side="right")

        listbox.bind("<Double-Button-1>", lambda e: do_select())

    def _get_all_texts(self):
        """获取当前模式下所有可用文章（内置+导入）"""
        result = []
        builtin = self.ENGLISH_TEXTS if self.mode == "english" else self.CHINESE_TEXTS
        imported = self.imported_texts.get(self.mode, [])

        for i, text in enumerate(builtin):
            preview = text.replace('\n', ' ').strip()[:35]
            result.append({'source': f'内置 #{i+1}', 'title': preview, 'preview': preview,
                          'content': text, 'source_type': 'builtin'})
        for i, item in enumerate(imported):
            title = item.get("title", "") if isinstance(item, dict) else item[:30]
            content = item["content"] if isinstance(item, dict) else item
            result.append({'source': f'导入 #{i+1}', 'title': title,
                          'preview': content.replace('\n', ' ')[:35],
                          'content': content, 'source_type': 'imported'})

        return result

    def _reset_state(self):
        """重置打字状态"""
        self.current_index = 0
        # 跳过开头的换行符（文章可能以换行开头）
        if hasattr(self, 'current_text') and self.current_text:
            while self.current_index < len(self.current_text) and self.current_text[self.current_index] == '\n':
                self.current_index += 1
        self.total_keystrokes = 0
        self.correct_keystrokes = 0
        self.wrong_chars = set()
        self.wrong_char_counter = {}
        self.time_elapsed = 0
        self.is_running = False
        self.is_paused = False
        self._stop_timer()

        if self.is_countdown and self.time_limit > 0:
            self.countdown_remaining = self.time_limit
        else:
            self.countdown_remaining = 0

        self._last_chinese_content = ""

        self._clear_input_mirror()

        # 初始化每行用户输入保存列表
        n = len(self.lines) if hasattr(self, 'lines') and self.lines else 0
        self.line_input_strings = [''] * n

        # 更新UI状态
        self.btn_start.configure(text="开始练习", state="normal")
        self.btn_pause.configure(state="disabled")
        self.lbl_error.configure(text="")

        if self.mode == "chinese":
            self._resetting = True
            self.chinese_entry_var.set("")
            self._resetting = False
            self.entry_input.configure(state="normal")
            self.entry_input.delete(0, "end")
            self.entry_input.configure(state="normal" if not self.is_running or not self.is_paused else "normal")
        else:
            self.entry_input.delete(0, "end")
            # 英文模式也清空entry
            self.entry_input.configure(state="normal")
            self.entry_input.delete(0, "end")

    def _split_lines(self):
        """按固定行宽拆分文本为多行，位置连续无空隙
        
        正确处理 \r\n 换行符和空白行。
        空白行（\n\n）会被保留为一个占位行，但标记为空白，
        在输入过程中会自动跳过。
        """
        self.line_strings = []
        self.lines = []
        self.line_input_strings = []  # 保存每行用户输入
        
        # 统一换行符：把 \r\n 和 \r 都转换成 \n
        text = self.current_text.replace('\r\n', '\n').replace('\r', '\n')
        self.current_text = text  # 写回，确保后续处理一致
        
        raw_lines = text.split('\n')
        pos = 0
        for raw in raw_lines:
            if not raw:
                # 空白行：生成一个占位行，line_start == line_end
                # 在 _on_line_input 里会自动跳过
                self.line_strings.append("")
                self.lines.append((pos, pos))  # pos 指向 \n 的位置
                pos += 1  # \n 字符
                continue
            start = 0
            while start < len(raw):
                end = min(start + self.line_width, len(raw))
                # 尽量在空格后断行（英文），但把空格包含在当前行
                if end < len(raw) and raw[end] != ' ':
                    # 在当前行内找最后一个空格，把空格一起包含进去
                    space_idx = raw.rfind(' ', start, end)
                    if space_idx > start:
                        end = space_idx + 1  # +1 把空格包含进来，下一行从空格后开始
                line_text = raw[start:end]
                self.line_strings.append(line_text)
                self.lines.append((pos + start, pos + end))
                start = end
            pos += len(raw) + 1  # +1 for \n character

        # 根据最终行数初始化用户输入缓存，确保与 _build_line_pairs / _on_line_key 索引一致
        self.line_input_strings = [''] * len(self.lines)

    def _build_line_pairs(self, page_line):
        """动态创建当前页的 Label+Entry 对：只显示有实际内容的行，行数自适应"""
        for widget in self.practice_frame.winfo_children():
            widget.destroy()
        self.line_labels.clear()
        self.line_entries.clear()
        self.line_vars.clear()
        self._visible_lines = []  # 存储每行实际对应的 lines 索引

        tag_configs = {
            "correct": {"foreground": "#2E7D32", "font": ("Consolas", 16, "bold")},
            "wrong":   {"foreground": "#C62828", "font": ("Consolas", 16, "bold"), "underline": True},
            "current": {"foreground": "#0D47A1", "font": ("Consolas", 16, "bold"), "background": "#FFF9C4"},
            "untyped": {"foreground": "#555555", "font": ("Consolas", 16)},
        }

        text = self.current_text
        total_lines = len(self.lines)

        # 从 page_line 开始收集有效的（非空）行，最多 LINES_PER_PAGE 个
        for off in range(total_lines):
            li = page_line + off
            if li >= total_lines:
                break
            st, ed = self.lines[li]
            if st >= ed:
                continue  # 跳过空白行
            self._visible_lines.append(li)
            if len(self._visible_lines) >= self.LINES_PER_PAGE:
                break

        for li in self._visible_lines:
            st, ed = self.lines[li]
            row = tk.Frame(self.practice_frame, bg="#ffffff", bd=1, relief="solid",
                          highlightbackground="#e0e0e0", highlightthickness=1)
            row.pack(fill="x", pady=(0, 10), padx=0)

            var = tk.StringVar()
            if hasattr(self, "line_input_strings") and li < len(self.line_input_strings):
                var.set(self.line_input_strings[li])
            entry = tk.Entry(row, textvariable=var, font=("Consolas", 16),
                            bd=0, bg="#f8f9fa", fg="#333333",
                            insertbackground="#2196F3", relief="flat",
                            takefocus=1)
            entry._line_index = li
            entry.bind("<Key>", lambda e, v=var, ls=st, le=ed, idx=li:
                       self._on_line_key(e, v, ls, le, idx))
            entry.bind("<FocusIn>", lambda e, idx=li: self._on_line_focus_in(idx))

            pt = tk.Text(row, font=("Consolas", 16), bg="#ffffff", fg="#333333",
                         wrap="word", bd=0, padx=10, pady=6, cursor="arrow",
                         state="disabled", height=2)
            pt.pack(fill="x")
            for tag_name, tag_kwargs in tag_configs.items():
                pt.tag_configure(tag_name, **tag_kwargs)
            self.line_labels.append(pt)
            entry.pack(fill="x", ipady=6, padx=10, pady=(0, 6))
            self.line_entries.append(entry)
            self.line_vars.append(var)

    def _update_display(self):
        """增量更新：翻页时重建控件，否则只更新文本和状态"""
        text = self.current_text
        if not text:
            for widget in self.practice_frame.winfo_children():
                widget.destroy()
            tk.Label(self.practice_frame, text="（无练习文本）", font=("微软雅黑", 14),
                    fg="#999", bg=COLOR_BG_MAIN).pack(pady=20)
            return

        if not hasattr(self, 'lines') or not self.lines:
            self._split_lines()

        cur_line = 0
        idx = self.current_index
        while idx < len(self.current_text) and self.current_text[idx] == '\n':
            idx += 1
        if idx >= len(self.current_text):
            cur_line = len(self.lines) - 1
        else:
            for li, (st, ed) in enumerate(self.lines):
                if st <= idx < ed:
                    cur_line = li
                    break
            else:
                cur_line = len(self.lines) - 1

        # 跳过空白行（st == ed 的行）
        while cur_line < len(self.lines) and self.lines[cur_line][0] == self.lines[cur_line][1]:
            cur_line += 1
        if cur_line >= len(self.lines):
            cur_line = len(self.lines) - 1

        # 滑动窗口：page_line 是起始查找位置
        page_line = max(0, cur_line - 1)
        if page_line >= len(self.lines):
            page_line = max(0, len(self.lines) - 1)

        # 判断是否需要重建：首次或 visible_lines 变化
        need_rebuild = (not self.line_labels or
                        getattr(self, '_last_page_line', -1) != page_line)
        if need_rebuild:
            self._build_line_pairs(page_line)
            self._last_page_line = page_line

        self.page_label.configure(text=f"当前行 {cur_line + 1}/{len(self.lines)}")

        # 用 _visible_lines 做增量更新（不再假设连续行号）
        for off, li in enumerate(self._visible_lines):
            if off >= len(self.line_labels):
                break
            pt = self.line_labels[off]
            entry = self.line_entries[off]
            var = self.line_vars[off]

            st, ed = self.lines[li]
            is_current = (li == cur_line)

            # 更新原文 Text（逐字上色）
            pt.configure(state="normal")
            pt.delete("1.0", "end")
            for i in range(st, ed):
                ch = text[i]
                if i < self.current_index:
                    tag = "wrong" if i in self.wrong_chars else "correct"
                elif i == self.current_index and is_current:
                    tag = "current"
                else:
                    tag = "untyped"
                pt.insert("end", ch, tag)
            pt.configure(state="disabled")

            # 更新输入 Entry 状态
            if is_current:
                entry.configure(state="normal")
            else:
                entry.configure(state="disabled")

    def _focus_current_line(self):
        """聚焦到当前行对应的 Entry"""
        if not hasattr(self, 'lines') or not self.lines:
            return
        if not hasattr(self, '_visible_lines') or not self._visible_lines:
            return
        # 计算 cur_line
        idx = self._skip_newlines_forward(self.current_index)
        if idx >= len(self.current_text):
            cur_line = len(self.lines) - 1
        else:
            cur_line = None
            for li, (st, ed) in enumerate(self.lines):
                if st <= idx < ed:
                    cur_line = li
                    break
            if cur_line is None:
                cur_line = len(self.lines) - 1
        # 在 visible_lines 中找 cur_line 的位置
        for off, li in enumerate(self._visible_lines):
            if li == cur_line:
                if off < len(self.line_entries):
                    entry = self.line_entries[off]
                    if entry.winfo_exists() and entry.cget("state") == "normal":
                        entry.focus_set()
                        # 移动光标到末尾
                        entry.icursor("end")
                        self.window.after_idle(
                            lambda e=entry: (e.focus_set(), e.icursor("end")) if (e.winfo_exists() and e.cget("state") == "normal") else None
                        )
                break

    def _on_line_focus_in(self, line_index):
        """某行 Entry 获得焦点时的回调（暂无特殊处理）"""
        pass

    def _update_stats(self):
        """更新统计数据显示"""
        total_chars = len(self.current_text)
        typed_chars = self.current_index

        # 时间显示
        if self.is_countdown and self.countdown_remaining > 0:
            mins, secs = divmod(int(self.countdown_remaining), 60)
            self.lbl_timer.configure(text=f"{mins:02d}:{secs:02d}",
                                      fg="#FF8A65")
        else:
            mins, secs = divmod(int(self.time_elapsed), 60)
            self.lbl_timer.configure(text=f"{mins:02d}:{secs:02d}",
                                      fg=COLOR_TEXT_LIGHT)

        # 正确率
        if self.total_keystrokes > 0:
            acc = self.correct_keystrokes / self.total_keystrokes * 100
        else:
            acc = 100.0
        self.lbl_accuracy.configure(text=f"{acc:.1f}%")
        if acc >= 95:
            self.lbl_accuracy.configure(fg=COLOR_SUCCESS)
        elif acc >= 80:
            self.lbl_accuracy.configure(fg=COLOR_WARNING)
        else:
            self.lbl_accuracy.configure(fg=COLOR_DANGER)

        # 速度（字/分钟）
        if self.time_elapsed > 0:
            speed = self.correct_keystrokes / (self.time_elapsed / 60.0)
        else:
            speed = 0
        self.lbl_speed.configure(text=f"{int(speed)} 字/分")

    # ==================== 计时器 ====================
    def _start_timer(self):
        self._stop_timer()
        self._tick()

    def _stop_timer(self):
        if self.timer_id:
            self.window.after_cancel(self.timer_id)
            self.timer_id = None

    def _tick(self):
        if not self.is_running or self.is_paused:
            return

        self.time_elapsed += 1

        if self.is_countdown and self.time_limit > 0:
            self.countdown_remaining -= 1
            if self.countdown_remaining <= 0:
                self.countdown_remaining = 0
                self._on_time_up()
                return

        self._update_stats()
        self.timer_id = self.window.after(1000, self._tick)

    def _on_time_up(self):
        """时间到"""
        self.is_running = False
        self._stop_timer()
        self._save_error_record()
        self._check_completion(force=True)

    # ==================== 键盘事件处理 ====================
    def _on_key_press(self, event):
        """键盘按下事件"""
        # 自动开始：有输入且未运行则自动开始练习
        if not self.is_running:
            self._start_practice()
            return "break"  # 消耗本次击键，避免start后立即处理

        if self.is_paused:
            return
        if self.mode == "chinese":
            # 中文模式不由键盘事件直接处理
            return

        keysym = event.keysym
        char = event.char

        # 忽略修饰键
        if keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R",
                       "Alt_L", "Alt_R", "Super_L", "Super_R", "Menu",
                       "Caps_Lock", "Num_Lock", "Scroll_Lock"):
            return

        # 处理退格键
        if keysym == "BackSpace":
            self._handle_backspace()
            return

        # 处理回车：跳过（不处理）
        if keysym == "Return":
            return

        # 处理Tab
        if keysym == "Tab":
            return "break"

        # 检查输入
        if not char or char == '\x1b':  # 忽略空字符和ESC
            return

        self._process_keystroke(char, keysym)

    def _process_keystroke(self, char, keysym):
        """处理击键"""
        if self.current_index >= len(self.current_text):
            return

        expected_char = self.current_text[self.current_index]
        self.total_keystrokes += 1

        if char == expected_char:
            # 正确
            self.correct_keystrokes += 1
            self.current_index += 1
            self.lbl_error.configure(text="")
            self._append_to_input_mirror(char, True)
        else:
            # 错误：也推进索引，保持对照区与原文一一对应
            self.wrong_chars.add(self.current_index)
            self.wrong_char_counter[expected_char] = self.wrong_char_counter.get(expected_char, 0) + 1
            self.current_index += 1

            self.lbl_error.configure(
                text=f"打错了！期望 '{expected_char}'，实际 '{char}'")
            self._append_to_input_mirror(char, False)
            # 错误音效
            try:
                self.window.bell()
            except Exception:
                pass

        self._update_stats()

        # 检查是否完成
        if self.current_index >= len(self.current_text):
            self._on_text_complete()

    def _handle_backspace(self):
        """处理退格"""
        if self.current_index > 0:
            # 回退统计数据
            was_wrong = (self.current_index - 1) in self.wrong_chars
            if self.total_keystrokes > 0:
                self.total_keystrokes -= 1
            if not was_wrong and self.correct_keystrokes > 0:
                self.correct_keystrokes -= 1
            self.wrong_chars.discard(self.current_index - 1)
            self.current_index -= 1
            self.lbl_error.configure(text="")
            self._backspace_input_mirror()
            self._update_stats()

    def _on_text_complete(self):
        """完成当前文本"""
        self.is_running = False
        self._stop_timer()
        self._save_error_record()
        self._check_completion(force=False)

    def _check_completion(self, force=False):
        """检查完成情况并显示结果"""
        self._update_stats()

        total = len(self.current_text)
        typed = self.current_index

        if self.total_keystrokes > 0:
            acc = self.correct_keystrokes / self.total_keystrokes * 100
        else:
            acc = 100.0

        # 判断是否通过
        passed = True
        fail_reasons = []

        # 正确率检查
        if self.accuracy_requirement > 0:
            if acc < self.accuracy_requirement:
                passed = False
                fail_reasons.append(
                    f"正确率 {acc:.1f}% 低于要求 {self.accuracy_requirement}%")

        # 未完成检查（仅在force模式下）
        if force and typed < total:
            fail_reasons.append(f"未完成全部文本（完成 {typed}/{total} 字符）")
            if self.is_countdown:
                fail_reasons.insert(0, "时间到！")

        # 构建结果消息
        if not force and typed >= total:
            # 自然完成
            result_lines = [
                "练习完成！",
                "",
                f"用时：{self._format_time(self.time_elapsed)}",
                f"总击键数：{self.total_keystrokes}",
                f"正确击键：{self.correct_keystrokes}",
                f"正确率：{acc:.1f}%",
                f"速度：{self.lbl_speed.cget('text')}",
            ]
            if not passed:
                result_lines.append("")
                result_lines.append("不合格：")
                result_lines.extend(f"  - {r}" for r in fail_reasons)
            else:
                result_lines.append("")
                result_lines.append("合格！")
        elif force:
            result_lines = [
                "练习结束",
                "",
                f"用时：{self._format_time(self.time_elapsed)}",
                f"总击键数：{self.total_keystrokes}",
                f"正确击键：{self.correct_keystrokes}",
                f"正确率：{acc:.1f}%",
                f"速度：{self.lbl_speed.cget('text')}",
            ]
            if fail_reasons:
                result_lines.append("")
                result_lines.append("不合格：")
                result_lines.extend(f"  - {r}" for r in fail_reasons)
            else:
                result_lines.append("")
                result_lines.append("合格！")
        else:
            return

        self.btn_start.configure(text="重新开始", state="normal")
        self.btn_pause.configure(state="disabled")

        messagebox.showinfo("练习结果", "\n".join(result_lines), parent=self.window)

    @staticmethod
    def _format_time(seconds):
        mins, secs = divmod(int(seconds), 60)
        return f"{mins} 分 {secs} 秒"

    # ==================== 中文模式处理 ====================
    def _on_chinese_input_change(self, *args):
        """中文模式：监听输入变化"""
        if self.mode != "chinese":
            return
        # 重置状态时抑制回调，防止自动开始
        if self._resetting:
            return
        # 自动开始：有输入且未运行则自动开始练习（保留首次输入）
        if not self.is_running:
            saved_value = self.chinese_entry_var.get()
            self._start_practice()
            if saved_value:
                self.chinese_entry_var.set(saved_value)
            return
        if self.is_paused:
            return

        current_value = self.chinese_entry_var.get()

        # 如果内容变短（删除操作），回退
        if len(current_value) < len(self._last_chinese_content):
            delta = len(self._last_chinese_content) - len(current_value)
            for _ in range(min(delta, self.current_index)):
                if self.current_index > 0:
                    was_wrong = (self.current_index - 1) in self.wrong_chars
                    if self.total_keystrokes > 0:
                        self.total_keystrokes -= 1
                    if not was_wrong and self.correct_keystrokes > 0:
                        self.correct_keystrokes -= 1
                    self.wrong_chars.discard(self.current_index - 1)
                    self.current_index -= 1
            self._last_chinese_content = current_value
        else:
            # 内容增加，逐字比较
            new_chars = current_value[len(self._last_chinese_content):]
            for ch in new_chars:
                self.total_keystrokes += 1
                if self.current_index < len(self.current_text):
                    expected = self.current_text[self.current_index]
                    if ch == expected:
                        self.correct_keystrokes += 1
                        self.current_index += 1
                        self.lbl_error.configure(text="")
                    else:
                        self.wrong_chars.add(self.current_index)
                        self.wrong_char_counter[expected] = self.wrong_char_counter.get(expected, 0) + 1
                        self.lbl_error.configure(
                            text=f"打错了！期望 ' {expected} '，实际 ' {ch} '")
                        try:
                            self.window.bell()
                        except Exception:
                            pass
                        # 中文错误也推进（标记为错）
                        self.current_index += 1
            self._last_chinese_content = current_value

        self._update_display()
        self._update_stats()

        if self.current_index >= len(self.current_text):
            self._on_text_complete()

    # ==================== 输入对照区辅助方法（已统一到 _update_display） ====================
    def _clear_input_mirror(self):
        self._update_display()

    def _skip_newlines_forward(self, idx):
        """向前跳过所有 \\n（\\n 不需要输入，自动跳过）"""
        while idx < len(self.current_text) and self.current_text[idx] == '\n':
            idx += 1
        return idx

    def _skip_newlines_backward(self, idx):
        """向后跳过所有 \\n"""
        while idx > 0 and self.current_text[idx] == '\n':
            idx -= 1
        return idx


    def _on_line_key(self, event, var, line_start, line_end, line_index):
        """Entry 按键处理：普通键接受输入，退格键特殊处理"""
        if self.is_paused:
            return "break"
        if not self.is_running:
            self._auto_start_practice()
        if self.mode == "chinese":
            return "break"

        # 计算 cur_line，跳过空白行
        idx = self._skip_newlines_forward(self.current_index)
        cur_line = None
        for li, (st, ed) in enumerate(self.lines):
            if st <= idx < ed:
                cur_line = li
                break
        if cur_line is None:
            for li, (st, ed) in enumerate(self.lines):
                if st < ed and st > idx:
                    cur_line = li
                    self.current_index = st
                    idx = st
                    break
            if cur_line is None:
                cur_line = len(self.lines) - 1
        while cur_line < len(self.lines) and self.lines[cur_line][0] == self.lines[cur_line][1]:
            self.current_index = self.lines[cur_line][1] + 1
            self.current_index = self._skip_newlines_forward(self.current_index)
            idx = self.current_index
            cur_line = None
            for li, (st, ed) in enumerate(self.lines):
                if st <= idx < ed:
                    cur_line = li
                    break
            if cur_line is None:
                break
        if cur_line is None:
            cur_line = len(self.lines) - 1

        if line_index != cur_line:
            return "break"

        # 退格键
        if event.keysym == "BackSpace":
            # 优先使用内部追踪值，避免 var.get() 已反映原生退格导致多删字符
            if hasattr(self, "line_input_strings") and cur_line < len(self.line_input_strings):
                cur_val = self.line_input_strings[cur_line]
            else:
                cur_val = var.get()
            if cur_val:
                new_val = cur_val[:-1]
                var.set(new_val)
                if self.current_index > line_start:
                    self.current_index -= 1
                    self.current_index = self._skip_newlines_backward(self.current_index)
                if hasattr(self, "line_input_strings") and cur_line < len(self.line_input_strings):
                    self.line_input_strings[cur_line] = new_val
                self._update_display()
                self._update_stats()
                self._focus_current_line()
            return "break"

        # 忽略功能键
        if not event.char or event.keysym in ("Return", "Tab", "Escape", "Left", "Right", "Up", "Down"):
            return "break"

        # 普通字符键：接受输入
        expected_char = self.current_text[idx] if idx < len(self.current_text) else ""
        typed_char = event.char
        if typed_char == expected_char:
            self.correct_keystrokes += 1
        else:
            self.wrong_chars.add(idx)
            wrong_ch = self.wrong_char_counter
            wrong_ch[expected_char] = wrong_ch.get(expected_char, 0) + 1
        self.current_index = idx + 1
        self.current_index = self._skip_newlines_forward(self.current_index)
        self.total_keystrokes += 1
        # 手动更新 Entry 内容（使用内部追踪值，避免 var.get() 在不同 Tk 版本下已含输入导致重复）
        current = (self.line_input_strings[cur_line] if hasattr(self, "line_input_strings") and cur_line < len(self.line_input_strings) else var.get())
        new_val = current + typed_char
        var.set(new_val)
        if hasattr(self, "line_input_strings") and cur_line < len(self.line_input_strings):
            self.line_input_strings[cur_line] = new_val
        self._update_display()
        self._update_stats()
        self._focus_current_line()
        if self.current_index >= len(self.current_text):
            self._on_text_complete()
        return "break"
    def _append_to_input_mirror(self, char, is_correct):
        self._update_display()

    def _backspace_input_mirror(self):
        self._update_display()

    def _rebuild_input_mirror(self):
        self._update_display()

    def _auto_start_practice(self):
        """输入即开始：用户键入第一个字符时自动启动练习"""
        self.is_running = True
        self.btn_start.configure(text="练习中...", state="disabled")
        self.btn_pause.configure(text="暂停", state="normal")
        if self.is_countdown and self.time_limit > 0:
            self.countdown_remaining = self.time_limit
        self.entry_input.configure(state="readonly")
        self._start_timer()

    # ==================== 控制按钮 ====================
    def _start_practice(self):
        """开始/重新开始练习"""
        if self.is_running and not self.is_paused:
            # 已经在运行，重新开始（不换文章，只重置当前文）
            self._reset_state()
            self._update_stats()
            self._start_auto()
            return

        if self.is_paused:
            # 恢复
            self.is_paused = False
            self.btn_pause.configure(text="暂停")
            self.btn_start.configure(state="disabled")
            self._start_timer()
            if self.mode == "chinese":
                self.entry_input.configure(state="normal")
                self.entry_input.focus_set()
            else:
                self.entry_input.delete(0, "end")
                self.entry_input.configure(state="readonly")
                self._focus_current_line()
            return

        # 全新开始（不换文章，保留已加载的文本）
        self._reset_state()
        self.is_running = True
        self.is_paused = False
        self.btn_start.configure(text="练习中...", state="disabled")
        self.btn_pause.configure(text="暂停", state="normal")

        if self.is_countdown and self.time_limit > 0:
            self.countdown_remaining = self.time_limit

        if self.mode == "chinese":
            self.entry_input.configure(state="normal")
            self.entry_input.focus_set()
            self._last_chinese_content = ""
            self.chinese_entry_var.set("")
        else:
            self.entry_input.delete(0, "end")
            self.entry_input.configure(state="readonly")
            self.window.focus_set()

        self._update_display()
        self._focus_current_line()
        self._start_timer()

    def _toggle_pause(self):
        """暂停/继续"""
        if not self.is_running:
            return

        if self.is_paused:
            self.is_paused = False
            self.btn_pause.configure(text="暂停")
            self.btn_start.configure(state="disabled")
            self._start_timer()
            if self.mode == "chinese":
                self.entry_input.configure(state="normal")
                self.entry_input.focus_set()
            else:
                self.window.focus_set()
        else:
            self.is_paused = True
            self._stop_timer()
            self.btn_pause.configure(text="继续")
            self.btn_start.configure(state="normal")

    def _reset_practice(self):
        """重置当前练习（恢复当前文章到起点，不换文章）"""
        self._stop_timer()
        self._reset_state()
        # 强制重建界面（清除旧 Entry 内容）
        self._last_page_line = -1
        self.line_labels.clear()
        self.line_entries.clear()
        self.line_vars.clear()
        for widget in self.practice_frame.winfo_children():
            widget.destroy()
        self._update_display()
        self._update_stats()
        self.btn_start.configure(text="开始练习", state="normal")
        self.btn_pause.configure(state="disabled")

    def _toggle_mode(self):
        """切换中英文模式"""
        self._stop_timer()
        if self.mode == "english":
            self.mode = "chinese"
            self.lbl_mode.configure(text="中文模式", fg="#FF8A65")
            # 显示中文输入框
            self.entry_input.pack(fill="x", ipady=4, padx=4, pady=(6, 0))
        else:
            self.mode = "english"
            self.lbl_mode.configure(text="英文模式", fg="#FFD54F")
            # 隐藏中文输入框
            self.entry_input.pack_forget()

        self._reset_state()
        self._load_new_text()
        self.btn_start.configure(text="开始练习", state="normal")
        self.btn_pause.configure(state="disabled")
        self.window.after(100, lambda: self._switch_input_method(self.mode))

    # ==================== 错误统计 ====================
    def _show_error_stats(self):
        """显示具体错字统计，按频率从高到低排序"""
        if not os.path.exists(self.stats_file):
            messagebox.showinfo("错误统计", "暂无练习记录，赶快开始打字吧！", parent=self.window)
            return

        dlg = tk.Toplevel(self.window)
        dlg.title("打字错字统计")
        dlg.geometry("520x480")
        dlg.resizable(True, True)
        dlg.minsize(380, 320)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.window)

        # 顶部筛选栏
        filter_bar = tk.Frame(dlg, bg=COLOR_BG)
        filter_bar.pack(fill="x", pady=(10, 5))

        tk.Label(filter_bar, text="时间范围：", font=("微软雅黑", 11),
                 fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(side="left", padx=(15, 5))

        range_var = tk.StringVar(value="不限")
        combo = ttk.Combobox(filter_bar, textvariable=range_var, state="readonly",
                             values=["1天内", "3天内", "7天内", "14天内", "不限"],
                             font=("微软雅黑", 10), width=10)
        combo.pack(side="left", padx=5)

        # 表格容器（Canvas + Scrollbar）
        list_frame = tk.Frame(dlg, bg=COLOR_BG)
        list_frame.pack(fill="both", expand=True, padx=15, pady=10)

        # 列标题
        hdr_frame = tk.Frame(list_frame, bg="#00838F")
        hdr_frame.pack(fill="x")
        tk.Label(hdr_frame, text="排名", width=6, font=("微软雅黑", 10, "bold"),
                 fg="white", bg="#00838F").pack(side="left", padx=2, pady=4)
        tk.Label(hdr_frame, text="错字", width=16, font=("微软雅黑", 10, "bold"),
                 fg="white", bg="#00838F").pack(side="left", padx=2, pady=4)
        tk.Label(hdr_frame, text="次数", width=10, font=("微软雅黑", 10, "bold"),
                 fg="white", bg="#00838F").pack(side="left", padx=2, pady=4)

        # 可滚动内容区
        canvas = tk.Canvas(list_frame, bg=COLOR_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLOR_BG)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 总计标签
        summary_label = tk.Label(dlg, text="", font=("微软雅黑", 10),
                                 fg=COLOR_TEXT_DARK, bg=COLOR_BG, anchor="e")
        summary_label.pack(fill="x", padx=15, pady=(0, 10))

        def refresh_table():
            range_str = range_var.get()
            for widget in scrollable_frame.winfo_children():
                widget.destroy()

            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    records = json.load(f)
            except Exception:
                records = []

            if not records:
                tk.Label(scrollable_frame, text="暂无记录", font=("微软雅黑", 11),
                         fg="#999", bg=COLOR_BG).pack(pady=30)
                summary_label.configure(text="")
                return

            # 计算时间截止
            cutoff = None
            if range_str == "1天内":
                cutoff = datetime.now().date()
            elif range_str == "3天内":
                cutoff = datetime.now().date() - timedelta(days=2)
            elif range_str == "7天内":
                cutoff = datetime.now().date() - timedelta(days=6)
            elif range_str == "14天内":
                cutoff = datetime.now().date() - timedelta(days=13)

            # 汇总错字
            char_count = {}
            for r in records:
                if cutoff and datetime.strptime(r["date"], "%Y-%m-%d").date() < cutoff:
                    continue
                wc = r.get("wrong_chars", {})
                for c, n in wc.items():
                    char_count[c] = char_count.get(c, 0) + n

            if not char_count:
                tk.Label(scrollable_frame, text=f"{range_str}内暂无错字记录", font=("微软雅黑", 11),
                         fg="#999", bg=COLOR_BG).pack(pady=30)
                summary_label.configure(text="")
                return

            # 按次数降序排列
            sorted_chars = sorted(char_count.items(), key=lambda x: x[1], reverse=True)
            total_char_errors = sum(n for _, n in sorted_chars)

            for i, (char, count) in enumerate(sorted_chars, 1):
                row_bg = COLOR_BG if i % 2 == 0 else "#F5F5F5"
                row = tk.Frame(scrollable_frame, bg=row_bg)
                row.pack(fill="x")
                disp_char = " (空格)" if char == " " else char
                tk.Label(row, text=str(i), width=6, font=("Consolas", 10),
                         fg=COLOR_TEXT_DARK, bg=row_bg).pack(side="left", padx=2, pady=1)
                tk.Label(row, text=disp_char, width=16, font=("微软雅黑", 11, "bold"),
                         fg="#E53935", bg=row_bg, anchor="w").pack(side="left", padx=2, pady=1)
                tk.Label(row, text=str(count), width=10, font=("Consolas", 11),
                         fg=COLOR_TEXT_DARK, bg=row_bg).pack(side="left", padx=2, pady=1)

            summary_label.configure(
                text=f"共 {len(sorted_chars)} 个错字，总计 {total_char_errors} 次  |  {range_str}")

        combo.bind("<<ComboboxSelected>>", lambda e: refresh_table())
        refresh_table()

    # ==================== 设置面板 ====================
    def _show_settings(self):
        """显示设置对话框"""
        dlg = tk.Toplevel(self.window)
        dlg.title("打字练习设置")
        dlg.geometry("440x380")
        dlg.resizable(True, True)
        dlg.minsize(380, 300)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.window)
        dlg.grab_set()

        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{sw//2-220}+{sh//2-190}")

        tk.Label(dlg, text="打字练习设置", font=("微软雅黑", 16, "bold"),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(pady=(20, 15))

        # 时间限制
        time_frame = tk.Frame(dlg, bg=COLOR_BG)
        time_frame.pack(fill="x", padx=40, pady=8)
        tk.Label(time_frame, text="时间限制（秒，0=不限时）：", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        time_var = tk.StringVar(value=str(self.time_limit))
        tk.Entry(time_frame, textvariable=time_var, font=("微软雅黑", 11),
                width=10, justify="center", bd=1, relief="solid").pack(anchor="w", pady=(5, 0))

        # 正确率要求
        acc_frame = tk.Frame(dlg, bg=COLOR_BG)
        acc_frame.pack(fill="x", padx=40, pady=8)
        tk.Label(acc_frame, text="正确率要求（%，0=不要求）：", font=("微软雅黑", 11),
                fg=COLOR_TEXT_DARK, bg=COLOR_BG).pack(anchor="w")
        acc_var = tk.StringVar(value=str(self.accuracy_requirement))
        tk.Entry(acc_frame, textvariable=acc_var, font=("微软雅黑", 11),
                width=10, justify="center", bd=1, relief="solid").pack(anchor="w", pady=(5, 0))

        # 倒计时模式
        cd_frame = tk.Frame(dlg, bg=COLOR_BG)
        cd_frame.pack(fill="x", padx=40, pady=8)
        cd_var = tk.BooleanVar(value=self.is_countdown)
        tk.Checkbutton(cd_frame, text="使用倒计时模式（时间到自动结束）",
                       variable=cd_var, font=("微软雅黑", 11),
                       bg=COLOR_BG, fg=COLOR_TEXT_DARK,
                       activebackground=COLOR_BG).pack(anchor="w")

        def save_settings():
            try:
                tl = int(time_var.get())
                ar = int(acc_var.get())
                if tl < 0:
                    tl = 0
                if ar < 0:
                    ar = 0
                if ar > 100:
                    ar = 100
                self.time_limit = tl
                self.accuracy_requirement = ar
                self.is_countdown = cd_var.get()

                # 更新需求标签
                req_parts = []
                if self.time_limit > 0:
                    req_parts.append(f"限时 {self.time_limit}秒")
                if self.accuracy_requirement > 0:
                    req_parts.append(f"正确率 >= {self.accuracy_requirement}%")
                self.lbl_requirement.configure(
                    text=" | ".join(req_parts) if req_parts else "")

                dlg.destroy()
            except ValueError:
                messagebox.showwarning("输入错误", "请输入有效的数字", parent=dlg)

        btn_frame = tk.Frame(dlg, bg=COLOR_BG)
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="保存", font=("微软雅黑", 11, "bold"),
                 bg=COLOR_SUCCESS, fg=COLOR_WHITE, bd=0, padx=20, pady=6,
                 cursor="hand2", command=save_settings).pack(side="left", padx=10)

        tk.Button(btn_frame, text="取消", font=("微软雅黑", 11),
                 bg="#ccc", fg="#333", bd=0, padx=20, pady=6,
                 cursor="hand2", command=dlg.destroy).pack(side="left", padx=10)

    # ==================== 清理 ====================
    def _on_close(self):
        """关闭窗口：停止定时器，保留 C:/ITSData 作答现场。"""
        self._stop_timer()
        self.window.destroy()


if __name__ == "__main__":
    try:
        ExamSystem().run()
    except Exception as _e:
        from datetime import datetime
        import traceback
        log_path = os.path.join(EXAM_DIR, "startup_error.log")
        with open(log_path, "a", encoding="utf-8") as _f:
            _f.write(f"\n\n=== {datetime.now()} ===\n")
            traceback.print_exc(file=_f)
        raise
