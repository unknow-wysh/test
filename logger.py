"""
春考网络技术技能模拟系统 - 日志模块
日志文件: exam_files/logs/exam_YYYYMMDD.log
格式: [时间] [级别] [模块] 消息
同时输出到控制台和文件
"""

import os
import sys
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler


class LoggerManager:
    """日志管理器，封装 logging 配置"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if LoggerManager._initialized:
            return
        LoggerManager._initialized = True
        self._loggers = {}
        self._log_dir = None
        self._setup()

    def _setup(self):
        """初始化日志目录（写入用户可写目录，避免 C:\\Program Files 权限问题）"""
        import tempfile as _tf
        _candidates = []
        _la = os.environ.get("LOCALAPPDATA")
        if _la:
            _candidates.append(os.path.join(_la, "CKWLYDT", "logs"))
        _candidates.append(os.path.join(os.path.expanduser("~"), "CKWLYDT", "logs"))
        _candidates.append(os.path.join(_tf.gettempdir(), "CKWLYDT", "logs"))
        self._log_dir = None
        for _c in _candidates:
            try:
                os.makedirs(_c, exist_ok=True)
                self._log_dir = _c
                break
            except Exception:
                continue
        if self._log_dir is None:
            self._log_dir = os.path.join(os.path.expanduser("~"), "CKWLYDT", "logs")

    def get_logger(self, module_name):
        """获取指定模块的 logger"""
        if module_name in self._loggers:
            return self._loggers[module_name]

        logger = logging.getLogger(module_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        # 避免重复添加 handler
        if logger.handlers:
            self._loggers[module_name] = logger
            return logger

        # 日志格式: [时间] [级别] [模块] 消息
        fmt = logging.Formatter(
            "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 文件 handler（轮转日志，每 5MB 一个，最多保留 5 个旧文件）
        log_file = os.path.join(
            self._log_dir,
            f"exam_{datetime.now().strftime('%Y%m%d')}.log",
        )
        fh = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        # 控制台 handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        self._loggers[module_name] = logger
        return logger


# 全局单例
_logger_manager = LoggerManager()


def get_logger(module_name="exam_system"):
    """便捷函数：获取 logger"""
    return _logger_manager.get_logger(module_name)
