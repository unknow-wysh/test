"""
春考网络技术技能模拟系统 — 局域网考试网络通信层
TCP Socket + JSON 协议，纯 Python 标准库实现。

消息格式:
    {
        "type": "消息类型",
        "data": { ... },
        "timestamp": "2026-05-30 12:00:00"
    }

支持的消息类型:
    LOGIN / LOGIN_RESP           学生登录与响应
    EXAM_START / EXAM_START_ACK  开始考试与确认
    TIME_EXTEND / TIME_EXTEND_ACK 延长时间与确认
    SUBMIT / SUBMIT_ACK          提交答案与确认
    FORCE_SUBMIT                 强制收卷
    QUESTION_BANK_SYNC           题目下发（教师将题目推送到学生端）
    STUDENT_STATUS               学生在线状态
    BROADCAST_MSG                广播消息
    BROADCAST_MSG                广播消息（教师发送自定义消息）
    HEARTBEAT                    心跳探测
    COUNTDOWN                    考试倒计时通知
"""

import socket
import json
import threading
import time
import logging
import os
from typing import Optional, Callable, Dict, Any

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
try:
    from logger import get_logger
    logger = get_logger("network")
except Exception:
    logger = logging.getLogger("Network")

# ============================================================
# 常量定义
# ============================================================
DEFAULT_PORT = 8888  # 默认监听端口
BUFFER_SIZE = 65536  # 接收缓冲区大小（字节），增大以支持文件夹数据传输
ENCODING = "utf-8"  # 统一编码
HEARTBEAT_INTERVAL = 5  # 心跳间隔（秒）
MAX_RECONNECT_COUNT = 5  # 最大重连次数
RECONNECT_INTERVAL = 3  # 重连间隔（秒）
SOCKET_TIMEOUT = 0.5  # Socket 接收超时（秒），用于优雅退出
SEND_TIMEOUT = 120  # 大文件分块发送超时（秒）
DEBUG_NETWORK = os.environ.get("EXAM_DEBUG_NETWORK", "").strip().lower() in {
    "1", "true", "yes", "on"
}


def make_message(msg_type: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构造标准 JSON 消息字典。

    Args:
        msg_type: 消息类型，如 LOGIN、HEARTBEAT 等
        data: 消息数据体，可选

    Returns:
        包含 type、data、timestamp 的标准消息字典
    """
    return {
        "type": msg_type,
        "data": data or {},
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ============================================================
# 服务器端
# ============================================================
class Server:
    """考试服务器，监听端口接受多个学生客户端连接。

    职责:
        - 维护在线学生字典 {socket: {"name": ..., "ip": ..., "login_time": ...}}
        - 处理学生登录 / 心跳 / 提交等消息
        - 支持广播消息、向指定学生发送消息
        - 记录连接历史（含断开的学生），方便查询交卷情况
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        """初始化服务器。

        Args:
            host: 绑定 IP 地址，"0.0.0.0" 表示监听所有网卡
            port: 监听端口
        """
        self.host = host
        self.port = port
        self._server_socket: Optional[socket.socket] = None
        self._running = False

        # 当前在线学生: key=socket, value=学生信息字典
        self.students: Dict[socket.socket, Dict[str, Any]] = {}
        self._students_lock = threading.Lock()

        # 学生连接历史（含已断开，用于查询是否交卷等）: key=学生姓名
        self.student_history: Dict[str, Dict[str, Any]] = {}
        self._history_lock = threading.Lock()

        # 消息处理器注册表: 消息类型 -> 处理函数(socket, data)
        self._handlers: Dict[str, Callable] = {}

        # 注册内置处理器
        self._register_default_handlers()

    # ---------- 消息处理器注册 ----------
    def _register_default_handlers(self):
        """注册内置的消息处理器（子类可覆盖）。"""
        self._handlers["LOGIN"] = self._handle_login
        self._handlers["HEARTBEAT"] = self._handle_heartbeat
        self._handlers["SUBMIT"] = self._handle_submit

    def register_handler(self, msg_type: str, handler: Callable):
        """注册自定义消息处理器。

        Args:
            msg_type: 消息类型
            handler: 处理函数，签名为 handler(socket, data) -> None
        """
        self._handlers[msg_type] = handler

    def add_student(self, client_sock: socket.socket, info: Dict[str, Any]):
        """线程安全地添加在线学生记录。"""
        with self._students_lock:
            self.students[client_sock] = info

    def remove_student(self, client_sock: socket.socket):
        """线程安全地移除在线学生记录。"""
        with self._students_lock:
            self.students.pop(client_sock, None)

    def add_history(self, name: str, info: Dict[str, Any]):
        """线程安全地添加/更新学生历史记录。"""
        with self._history_lock:
            self.student_history[name] = info

    def update_history(self, name: str, updates: Dict[str, Any]):
        """线程安全地更新学生历史记录的指定字段。"""
        with self._history_lock:
            if name in self.student_history:
                self.student_history[name].update(updates)

    def get_history(self, name: str) -> Optional[Dict[str, Any]]:
        """线程安全地获取学生历史记录。"""
        with self._history_lock:
            return self.student_history.get(name)

    # ---------- 生命周期 ----------
    def start(self):
        """启动服务器，开始监听客户端连接。"""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 允许端口复用，解决重启时 TIME_WAIT 问题
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(50)  # 最多 50 个排队连接
        self._server_socket.settimeout(SOCKET_TIMEOUT)
        self._running = True

        logger.info(f"服务器启动成功，监听 {self.host}:{self.port}")

        # 主循环：接受新连接
        while self._running:
            try:
                client_sock, addr = self._server_socket.accept()
                logger.info(f"新连接: {addr}")
                # 为每个客户端创建一个独立线程处理消息
                thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, addr),
                    daemon=True,
                )
                thread.start()
            except socket.timeout:
                # 超时仅用于优雅退出，继续循环
                continue
            except OSError:
                # 服务器关闭时 accept 会抛出异常
                break

        logger.info("服务器已停止")

    def stop(self):
        """停止服务器，断开所有客户端连接。"""
        self._running = False
        # 断开所有学生
        with self._students_lock:
            for sock in list(self.students.keys()):
                try:
                    sock.close()
                except OSError:
                    pass
            self.students.clear()
        # 关闭服务器 Socket
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None

    # ---------- 客户端处理 ----------
    def _handle_client(self, client_sock: socket.socket, addr: tuple):
        """处理单个客户端连接的完整生命周期。

        Args:
            client_sock: 客户端 Socket
            addr: 客户端地址 (ip, port)
        """
        client_sock.settimeout(SOCKET_TIMEOUT)
        # 禁用 Nagle 算法，立即发送小包，减少传输延迟
        try:
            client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass
        # 使用列表缓冲区避免大消息时 O(n²) 字符串拼接
        buffer_parts = []
        buffer_size = 0

        while self._running:
            try:
                data = client_sock.recv(BUFFER_SIZE)
            except socket.timeout:
                continue
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                break

            if not data:
                break

            # UTF-8 解码并追加到缓冲区
            try:
                decoded = data.decode('utf-8')
            except UnicodeDecodeError:
                decoded = data.decode('utf-8', errors='ignore')

            buffer_parts.append(decoded)
            buffer_size += len(decoded)

            # 大消息通常直到最后才有换行符；未收到完整行时不反复拼接整包
            if "\n" not in decoded:
                continue

            # 收到完整消息后再合并缓冲区并按换行符分割
            buffer = "".join(buffer_parts)
            buffer_parts = []
            buffer_size = 0
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                self._dispatch(client_sock, line)

            # 剩余不完整数据放回缓冲区
            if buffer:
                buffer_parts.append(buffer)
                buffer_size = len(buffer)

        # 客户端断开，清理
        self._remove_student(client_sock)

    def _dispatch(self, client_sock: socket.socket, raw: str):
        """解析 JSON 消息并分发到对应处理器。

        Args:
            client_sock: 发送方 Socket
            raw: 原始 JSON 字符串
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"收到非法 JSON: {raw[:100]}")
            return

        msg_type = msg.get("type", "")
        data = msg.get("data", {})

        if msg_type == "LOGIN" and DEBUG_NETWORK:
            safe_data = dict(data)
            if "id_number" in safe_data:
                safe_data["id_number"] = "***"
            logger.debug(f"收到 LOGIN 消息: {safe_data}, handlers={list(self._handlers.keys())}")

        handler = self._handlers.get(msg_type)
        if handler:
            try:
                handler(client_sock, data)
            except Exception as e:
                logger.error(f"处理 {msg_type} 消息时出错: {e}", exc_info=True)
        else:
            logger.warning(f"未注册的消息类型: {msg_type}")

    # ---------- 内置处理器 ----------
    def _handle_login(self, client_sock: socket.socket, data: Dict[str, Any]):
        """处理学生登录。

        学生登录后：
            - 加入在线学生字典
            - 加入历史记录
            - 返回 LOGIN_RESP 确认

        Args:
            client_sock: 学生 Socket
            data: {"name": 学生姓名, "exam_id": 准考证号}
        """
        name = data.get("name", "未知")
        exam_id = data.get("exam_id", "")

        # 加入在线字典
        with self._students_lock:
            self.students[client_sock] = {
                "name": name,
                "exam_id": exam_id,
                "ip": client_sock.getpeername()[0],
                "login_time": time.time(),
            }

        # 加入历史
        with self._history_lock:
            self.student_history[name] = {
                "name": name,
                "exam_id": exam_id,
                "ip": client_sock.getpeername()[0],
                "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "online": True,
                "submitted": False,
                "answers": {},
                "answers_count": 0,
            }

        # 回复登录成功
        resp = make_message("LOGIN_RESP", {"status": "OK", "name": name})
        self._send_to_socket(client_sock, resp)
        logger.info(f"学生登录: {name} ({exam_id}) - {self.student_count} 人在线")

    def _handle_heartbeat(self, client_sock: socket.socket, data: Dict[str, Any]):
        """处理心跳包，仅回复确认。

        Args:
            client_sock: 学生 Socket
            data: 心跳数据，可含 {"answered": N} 表示已答题数
        """
        # 更新答题进度（心跳中携带）
        answered = data.get("answered")
        if answered is not None:
            with self._students_lock:
                info = self.students.get(client_sock)
            if info:
                name = info.get("name")
                if name:
                    with self._history_lock:
                        if name in self.student_history:
                            self.student_history[name]["answers_count"] = answered

        resp = make_message("HEARTBEAT", {"status": "OK"})
        self._send_to_socket(client_sock, resp)

    def _handle_submit(self, client_sock: socket.socket, data: Dict[str, Any]):
        """处理学生提交答案。

        Args:
            client_sock: 学生 Socket
            data: {"name": 学生姓名, "answers": {...}}
        """
        name = data.get("name", "未知")
        answers = data.get("answers", {})

        # 记录交卷信息到历史
        with self._history_lock:
            if name in self.student_history:
                self.student_history[name]["submitted"] = True
                self.student_history[name]["submit_time"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                self.student_history[name]["answers"] = answers

        # 回复确认
        resp = make_message("SUBMIT_ACK", {"status": "OK", "name": name})
        self._send_to_socket(client_sock, resp)
        logger.info(f"学生 {name} 已交卷，共 {len(answers)} 道题")

    # ---------- 辅助方法 ----------
    def _remove_student(self, client_sock: socket.socket):
        """移除断开连接的学生。

        Args:
            client_sock: 已断开的 Socket
        """
        with self._students_lock:
            info = self.students.pop(client_sock, None)
        if info:
            name = info.get("name", "未知")
            # 更新历史为离线
            with self._history_lock:
                if name in self.student_history:
                    self.student_history[name]["online"] = False
                    self.student_history[name]["disconnect_time"] = time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
            logger.info(f"学生离线: {name} - {self.student_count} 人在线")
        try:
            client_sock.close()
        except OSError:
            pass

    def _send_to_socket(self, sock: socket.socket, msg: Dict[str, Any]):
        """向单个 Socket 发送 JSON 消息（加换行符作为分隔）。

        Args:
            sock: 目标 Socket
            msg: 消息字典
        """
        try:
            # separators 去掉多余空格，减少 JSON 体积
            raw = json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + "\n"
            sock.sendall(raw.encode(ENCODING))
        except (OSError, ConnectionResetError, BrokenPipeError):
            self._remove_student(sock)

    def _find_student_socket(self, name: str) -> Optional[socket.socket]:
        """根据学生姓名查找对应的 socket（线程安全）。

        Returns:
            学生的 socket，找不到返回 None
        """
        with self._students_lock:
            for sock, info in self.students.items():
                if info.get("name") == name:
                    return sock
        return None

    def send_pre_serialized(self, name: str, raw_bytes: bytes,
                            remove_on_error: bool = True):
        """向指定学生发送预序列化的二进制数据（已编码的 JSON 行）。

        与 try_send 不同，此方法跳过 JSON 序列化步骤，直接发送 raw_bytes。
        适用于需要向多个学生发送完全相同的消息。

        Args:
            name: 学生姓名
            raw_bytes: 预序列化的字节数据（UTF-8 编码的 JSON + "\n"）
            remove_on_error: 发送失败时是否移除该学生

        Returns:
            (True, None) 表示成功，(False, error_msg) 表示失败
        """
        target_sock = None
        with self._students_lock:
            for sock, info in self.students.items():
                if info.get("name") == name:
                    target_sock = sock
                    break

        if target_sock is not None:
            try:
                old_timeout = target_sock.gettimeout()
                try:
                    target_sock.settimeout(SEND_TIMEOUT)
                except OSError:
                    old_timeout = None
                target_sock.sendall(raw_bytes)
                if old_timeout is not None:
                    try:
                        target_sock.settimeout(old_timeout)
                    except OSError:
                        pass
                return True, None
            except (OSError, ConnectionResetError, BrokenPipeError, socket.timeout) as e:
                try:
                    if 'old_timeout' in locals() and old_timeout is not None:
                        target_sock.settimeout(old_timeout)
                except OSError:
                    pass
                if remove_on_error:
                    self.remove_student(target_sock)
                return False, str(e)

        return False, "学生不在线"

    # ---------- 公共 API ----------
    def broadcast(self, msg_type: str, data: Optional[Dict[str, Any]] = None):
        """向所有在线学生广播消息。

        Args:
            msg_type: 消息类型
            data: 消息数据体
        """
        msg = make_message(msg_type, data)
        with self._students_lock:
            # 快照避免迭代时修改
            socks = list(self.students.keys())
        for sock in socks:
            self._send_to_socket(sock, msg)

    def send_to(self, name: str, msg_type: str, data: Optional[Dict[str, Any]] = None):
        """向指定姓名的学生发送消息。

        Args:
            name: 学生姓名
            msg_type: 消息类型
            data: 消息数据体

        Returns:
            True 表示发送成功，False 表示该学生不在线
        """
        msg = make_message(msg_type, data)
        with self._students_lock:
            for sock, info in self.students.items():
                if info.get("name") == name:
                    self._send_to_socket(sock, msg)
                    return True
        return False

    def try_send(self, name: str, msg_type: str,
                 data: Optional[Dict[str, Any]] = None):
        """尝试向指定学生发送消息，返回 (成功, 错误信息)。

        Returns:
            (True, None) 表示发送成功，(False, error_message) 表示失败。
        """
        msg = make_message(msg_type, data)

        # 提前序列化：1) 避免在持锁期间失败 2) 及早发现非 JSON 数据
        try:
            raw = json.dumps(msg, ensure_ascii=False) + "\n"
            raw_bytes = raw.encode(ENCODING)
        except Exception as e:
            return False, f"消息序列化失败: {e}"

        sock_to_remove = None
        err_msg = ""

        with self._students_lock:
            for sock, info in self.students.items():
                if info.get("name") == name:
                    try:
                        sock.sendall(raw_bytes)
                        return True, None
                    except (OSError, ConnectionResetError,
                            BrokenPipeError) as e:
                        sock_to_remove = sock
                        err_msg = str(e)
                    # 无论发送成功还是失败，找到学生就退出循环
                    break
            else:
                # for-else: 没有找到匹配的学生
                return False, "学生不在线"

        # 在锁外执行移除操作，避免死锁
        if sock_to_remove is not None:
            self._remove_student(sock_to_remove)
            return False, err_msg

        # 理论上不会走到这里，但兜底
        return False, "学生不在线"

    def force_submit(self, name: str):
        """强制指定学生交卷。

        Args:
            name: 学生姓名

        Returns:
            True 表示消息已发送，False 表示该学生不在线
        """
        return self.send_to(name, "FORCE_SUBMIT", {"name": name})

    def get_student_list(self) -> list:
        """获取所有在线学生姓名列表。"""
        with self._students_lock:
            return [info.get("name", "未知") for info in self.students.values()]

    @property
    def student_count(self) -> int:
        """当前在线学生数。"""
        with self._students_lock:
            return len(self.students)

    def get_student_status(self) -> list:
        """获取所有学生的在线状态列表（含已断开）。

        Returns:
            [{"name": ..., "exam_id": ..., "online": ..., "submitted": ...}, ...]
        """
        with self._history_lock:
            return list(self.student_history.values())


# ============================================================
# 客户端
# ============================================================
class Client:
    """考试客户端，连接到教师端服务器。

    职责:
        - 连接教师端 IP:端口
        - 每 5 秒发送心跳包维持连接
        - 断线自动重连（最多 5 次，间隔 3 秒）
        - 发送消息到服务器，接收服务器消息
    """

    def __init__(self, student_name: str = "", exam_id: str = "", id_number: str = ""):
        """初始化客户端。

        Args:
            student_name: 学生姓名
            exam_id: 准考证号
            id_number: （可选）身份证号，用于教师端身份校验
        """
        self.student_name = student_name
        self.exam_id = exam_id
        self.id_number = id_number or ""
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._connected = False
        self._reconnect_count = 0
        self._lock = threading.Lock()
        self._answered_counter = 0  # 供 exam_system 实时更新已答题数

        # 消息处理器注册表: 消息类型 -> 处理函数(data)
        self._handlers: Dict[str, Callable] = {}

        # 后台线程
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._reconnect_thread: Optional[threading.Thread] = None
        self._server_host = ""
        self._server_port = DEFAULT_PORT

        # 注册默认处理器
        self._register_default_handlers()

    # ---------- 消息处理器注册 ----------
    def _register_default_handlers(self):
        """注册内置处理器（子类可覆盖）。"""
        self._handlers["LOGIN_RESP"] = self._handle_login_resp
        self._handlers["FORCE_SUBMIT"] = self._handle_force_submit

    def register_handler(self, msg_type: str, handler: Callable):
        """注册自定义消息处理器。

        Args:
            msg_type: 消息类型
            handler: 处理函数，签名为 handler(data) -> None
        """
        self._handlers[msg_type] = handler

    def _handle_login_resp(self, data: Dict[str, Any]):
        """处理登录响应（默认仅记录日志）。"""
        logger.info(f"登录响应: {data}")

    def _handle_force_submit(self, data: Dict[str, Any]):
        """处理强制交卷。

        Args:
            data: {"name": 学生姓名}
        """
        logger.warning(f"收到强制交卷指令: {data.get('name')}")
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        """是否已连接到教师机。"""
        with self._lock:
            return self._connected

    # ---------- 连接管理 ----------
    def connect(self, server_host: str, server_port: int = DEFAULT_PORT):
        """连接到服务器并启动后台线程。

        Args:
            server_host: 服务器 IP 地址
            server_port: 服务器端口
        """
        self._running = True
        self._server_host = server_host
        self._server_port = server_port

        # 尝试建立 TCP 连接
        if not self._try_connect(server_host, server_port):
            logger.error("无法连接到服务器，客户端启动失败")
            self._running = False
            return

        # 连接成功后发送登录消息
        self._send_login()

        # 启动接收线程
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

        # 启动心跳线程
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()

    def _try_connect(self, host: str, port: int) -> bool:
        """尝试建立 TCP 连接。

        Args:
            host: 服务器 IP
            port: 服务器端口

        Returns:
            True 表示连接成功
        """
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(3)  # 连接超时 3 秒
            self._sock.connect((host, port))
            self._sock.settimeout(SOCKET_TIMEOUT)
            # 禁用 Nagle 算法，减少接收延迟
            try:
                self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (OSError, AttributeError):
                pass
            with self._lock:
                self._connected = True
            logger.info(f"已连接到服务器 {host}:{port}")
            return True
        except (OSError, socket.timeout) as e:
            logger.warning(f"连接失败 {host}:{port}: {e}")
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            return False

    def _reconnect_loop(self, host: str, port: int):
        """断线重连循环（后台运行）。

        Args:
            host: 服务器 IP
            port: 服务器端口
        """
        while self._running and not self._connected:
            if self._reconnect_count >= MAX_RECONNECT_COUNT:
                logger.error(f"重连次数已达上限 ({MAX_RECONNECT_COUNT})，停止重连")
                self._running = False
                return

            self._reconnect_count += 1
            logger.info(
                f"尝试重连 ({self._reconnect_count}/{MAX_RECONNECT_COUNT})..."
            )
            time.sleep(RECONNECT_INTERVAL)

            if self._try_connect(host, port):
                self._reconnect_count = 0
                self._send_login()
                if not self._recv_thread or not self._recv_thread.is_alive():
                    self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
                    self._recv_thread.start()
                return

    def _mark_disconnected(self, reason: str = ""):
        """标记断线并启动后台重连。"""
        already_disconnected = False
        with self._lock:
            already_disconnected = not self._connected
            self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

        if not already_disconnected:
            handler = self._handlers.get("DISCONNECTED")
            if handler:
                try:
                    handler({"reason": reason})
                except TypeError:
                    handler()
                except Exception as e:
                    logger.warning(f"断线回调异常: {e}")

        if not self._running:
            return
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            args=(self._server_host, self._server_port),
            daemon=True,
        )
        self._reconnect_thread.start()

    def disconnect(self):
        """断开与服务器的连接。"""
        self._running = False
        with self._lock:
            self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        logger.info("已断开连接")

    # ---------- 消息收发 ----------
    def send(self, msg_type: str, data: Optional[Dict[str, Any]] = None):
        """向服务器发送消息。

        Args:
            msg_type: 消息类型
            data: 消息数据体

        Returns:
            True 表示发送成功
        """
        if not self._connected or not self._sock:
            logger.warning("未连接，无法发送消息")
            return False

        # 自动填充学生信息
        full_data = data or {}
        if "name" not in full_data and self.student_name:
            full_data["name"] = self.student_name
        if "exam_id" not in full_data and self.exam_id:
            full_data["exam_id"] = self.exam_id

        msg = make_message(msg_type, full_data)
        try:
            raw = json.dumps(msg, ensure_ascii=False) + "\n"
            old_timeout = self._sock.gettimeout()
            try:
                self._sock.settimeout(SEND_TIMEOUT)
            except OSError:
                old_timeout = None
            self._sock.sendall(raw.encode(ENCODING))
            if old_timeout is not None:
                try:
                    self._sock.settimeout(old_timeout)
                except OSError:
                    pass
            return True
        except (OSError, ConnectionResetError, BrokenPipeError, socket.timeout) as e:
            try:
                if 'old_timeout' in locals() and old_timeout is not None and self._sock:
                    self._sock.settimeout(old_timeout)
            except OSError:
                pass
            logger.warning(f"发送失败: {e}")
            self._mark_disconnected(str(e))
            return False

    def _send_login(self):
        """发送登录消息给服务器。"""
        if DEBUG_NETWORK:
            logger.debug(f"发送 LOGIN: name={self.student_name}, exam_id={self.exam_id}")
        self.send("LOGIN", {
            "name": self.student_name,
            "exam_id": self.exam_id,
            "id_number": self.id_number,
        })

    def _recv_loop(self):
        """接收消息循环（后台线程）。"""
        # 使用列表缓冲区避免大消息时 O(n²) 字符串拼接
        buffer_parts = []

        while self._running:
            try:
                # 检查是否已连接
                if not self._connected:
                    time.sleep(0.5)
                    continue

                # 防御：_sock 可能在断线时被置 None
                sock = self._sock
                if sock is None:
                    time.sleep(0.5)
                    continue

                try:
                    data = sock.recv(BUFFER_SIZE)
                except socket.timeout:
                    continue
                except (ConnectionResetError, ConnectionAbortedError, OSError, AttributeError) as e:
                    logger.warning(f"接收异常: {e}")
                    self._mark_disconnected(str(e))
                    continue

                if not data:
                    # 服务器断开
                    logger.warning("服务器断开连接")
                    self._mark_disconnected("服务器断开连接")
                    continue

                # UTF-8 解码
                try:
                    decoded = data.decode('utf-8')
                except UnicodeDecodeError:
                    decoded = data.decode('utf-8', errors='ignore')

                buffer_parts.append(decoded)
                if "\n" not in decoded:
                    continue

                # 收到完整消息后再合并缓冲区并按换行符分割
                buffer = "".join(buffer_parts)
                buffer_parts = []
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if DEBUG_NETWORK:
                        logger.debug(f"收到消息 len={len(line)} preview={line[:80]}")
                    self._dispatch(line)

                # 剩余不完整数据放回缓冲区
                if buffer:
                    buffer_parts.append(buffer)
            except Exception as e:
                # 防止任何未预料到的异常导致接收线程静默死亡
                import traceback
                traceback.print_exc()
                logger.error(f"_recv_loop 未预料异常: {e}\n{traceback.format_exc()}")
                if DEBUG_NETWORK:
                    logger.debug(f"_recv_loop 异常: {e}，2秒后重试")
                time.sleep(2)  # 避免异常时疯狂循环占CPU

    def _dispatch(self, raw: str):
        """解析 JSON 消息并分发到对应处理器。

        Args:
            raw: 原始 JSON 字符串
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"收到非法 JSON: {raw[:100]}")
            return

        msg_type = msg.get("type", "")
        data = msg.get("data", {})

        handler = self._handlers.get(msg_type)
        if handler:
            handler(data)
        else:
            logger.debug(f"未处理的消息类型: {msg_type}")

    # ---------- 心跳 ----------
    def _heartbeat_loop(self):
        """心跳发送循环（后台线程），每 HEARTBEAT_INTERVAL 秒发送一次。"""
        while self._running:
            time.sleep(HEARTBEAT_INTERVAL)
            if self._connected:
                self.send("HEARTBEAT", {"answered": self._answered_counter})


# ============================================================
# 自测入口
# ============================================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  服务端: python network.py server")
        print("  客户端: python network.py client <学生姓名> [准考证号]")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "server":
        # 启动一个简单的测试服务器
        server = Server(port=8888)
        try:
            server.start()
        except KeyboardInterrupt:
            print("\n正在关闭服务器...")
            server.stop()

    elif mode == "client":
        name = sys.argv[2] if len(sys.argv) > 2 else "测试学生"
        exam_id = sys.argv[3] if len(sys.argv) > 3 else "000000"
        client = Client(student_name=name, exam_id=exam_id)

        # 注册考试开始处理器（演示用）
        def on_exam_start(data):
            print(f">>> 收到考试开始指令: {data}")

        client.register_handler("EXAM_START", on_exam_start)

        # 连接服务器（假设本地测试）
        server_ip = input("服务器 IP (默认 127.0.0.1): ").strip() or "127.0.0.1"
        client.connect(server_ip, 8888)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在断开...")
            client.disconnect()
