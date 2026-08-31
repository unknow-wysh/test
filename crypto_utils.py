"""
加密工具模块 - 用于加密和解密考试系统的敏感数据文件
使用 AES-256-CBC 加密，密钥从固定字符串派生
"""

import os
import json
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class CryptoManager:
    """加密管理器 - 使用 Fernet (AES-128-CBC + HMAC) 加密数据"""
    
    # 加密密钥（实际应用中应该更安全地处理，这里为了简便直接内置）
    # 可以通过环境变量或配置文件获取
    _KEY_SEED = "NetworkExamSystem2026!@#$%^&*()_+SecureKey"
    _SALT = b"NetworkExamSalt2026"  # 固定盐值
    
    def __init__(self):
        """初始化加密管理器，派生加密密钥"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self._SALT,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self._KEY_SEED.encode()))
        self.fernet = Fernet(key)
    
    def encrypt_data(self, data: str) -> str:
        """加密字符串数据
        
        Args:
            data: 要加密的字符串
            
        Returns:
            加密后的 Base64 字符串
        """
        encrypted = self.fernet.encrypt(data.encode('utf-8'))
        return base64.urlsafe_b64encode(encrypted).decode('ascii')
    
    def decrypt_data(self, encrypted_data: str) -> str:
        """解密字符串数据
        
        Args:
            encrypted_data: 加密的 Base64 字符串
            
        Returns:
            解密后的字符串
        """
        decoded = base64.urlsafe_b64decode(encrypted_data.encode('ascii'))
        decrypted = self.fernet.decrypt(decoded)
        return decrypted.decode('utf-8')
    
    def encrypt_file(self, input_path: str, output_path: str = None) -> str:
        """加密文件
        
        Args:
            input_path: 输入文件路径
            output_path: 输出文件路径（如果为 None，则覆盖原文件）
            
        Returns:
            输出文件路径
        """
        if output_path is None:
            output_path = input_path
        
        with open(input_path, 'r', encoding='utf-8-sig') as f:
            data = f.read()
        
        encrypted = self.encrypt_data(data)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(encrypted)
        
        return output_path
    
    def decrypt_file(self, input_path: str, output_path: str = None) -> str:
        """解密文件
        
        Args:
            input_path: 输入文件路径
            output_path: 输出文件路径（如果为 None，则覆盖原文件）
            
        Returns:
            输出文件路径
        """
        if output_path is None:
            output_path = input_path
        
        with open(input_path, 'r', encoding='utf-8') as f:
            encrypted_data = f.read()
        
        decrypted = self.decrypt_data(encrypted_data)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(decrypted)
        
        return output_path
    
    def encrypt_json_file(self, input_path: str, output_path: str = None) -> str:
        """加密 JSON 文件（保持 JSON 格式，但内容加密）
        
        Args:
            input_path: 输入文件路径
            output_path: 输出文件路径（如果为 None，则覆盖原文件）
            
        Returns:
            输出文件路径
        """
        if output_path is None:
            output_path = input_path
        
        with open(input_path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        
        # 将 JSON 转为字符串，然后加密
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        encrypted = self.encrypt_data(json_str)
        
        # 保存为 JSON 格式，包含加密标记
        encrypted_obj = {
            "_encrypted": True,
            "_version": "1.0",
            "data": encrypted
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(encrypted_obj, f, ensure_ascii=False, indent=2)
        
        return output_path
    
    def decrypt_json_file(self, input_path: str) -> dict:
        """解密 JSON 文件
        
        Args:
            input_path: 输入文件路径
            
        Returns:
            解密后的 JSON 对象
        """
        with open(input_path, 'r', encoding='utf-8-sig') as f:
            encrypted_obj = json.load(f)
        
        # 检查是否是加密文件
        if not encrypted_obj.get("_encrypted"):
            # 不是加密文件，直接返回
            return encrypted_obj
        
        # 解密数据
        encrypted_data = encrypted_obj["data"]
        decrypted_str = self.decrypt_data(encrypted_data)
        
        # 解析 JSON
        return json.loads(decrypted_str)


# 全局加密管理器实例
_crypto_manager = None

def get_crypto_manager() -> CryptoManager:
    """获取全局加密管理器实例"""
    global _crypto_manager
    if _crypto_manager is None:
        _crypto_manager = CryptoManager()
    return _crypto_manager


def load_encrypted_json(file_path: str) -> dict:
    """加载加密的 JSON 文件
    
    Args:
        file_path: 文件路径
        
    Returns:
        解密后的 JSON 对象
    """
    if not os.path.exists(file_path):
        return {}
    
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        
        # 检查是否是加密文件
        if isinstance(data, dict) and data.get("_encrypted"):
            cm = get_crypto_manager()
            return cm.decrypt_json_file(file_path)
        
        # 不是加密文件，直接返回
        return data
    except Exception as e:
        print(f"加载文件失败: {file_path}, 错误: {e}")
        return {}


def save_encrypted_json(file_path: str, data: dict) -> bool:
    """保存加密的 JSON 文件
    
    Args:
        file_path: 文件路径
        data: 要保存的数据
        
    Returns:
        是否成功
    """
    try:
        cm = get_crypto_manager()
        
        # 将 JSON 转为字符串，然后加密
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        encrypted = cm.encrypt_data(json_str)
        
        # 保存为 JSON 格式，包含加密标记
        encrypted_obj = {
            "_encrypted": True,
            "_version": "1.0",
            "data": encrypted
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(encrypted_obj, f, ensure_ascii=False, indent=2)
        
        return True
    except Exception as e:
        print(f"保存文件失败: {file_path}, 错误: {e}")
        return False


if __name__ == "__main__":
    # 测试加密功能
    cm = CryptoManager()
    
    # 测试字符串加密
    test_str = '{"name": "测试", "score": 100}'
    encrypted = cm.encrypt_data(test_str)
    decrypted = cm.decrypt_data(encrypted)
    print(f"原始: {test_str}")
    print(f"加密: {encrypted[:50]}...")
    print(f"解密: {decrypted}")
    print(f"匹配: {test_str == decrypted}")
    
    # 测试文件加密
    test_file = "test_encrypt.json"
    with open(test_file, 'w', encoding='utf-8') as f:
        json.dump({"test": "数据"}, f, ensure_ascii=False, indent=2)
    
    cm.encrypt_json_file(test_file, test_file + ".enc")
    print(f"\n加密文件已保存: {test_file}.enc")
    
    # 解密
    decrypted_data = cm.decrypt_json_file(test_file + ".enc")
    print(f"解密数据: {decrypted_data}")
    
    # 清理
    os.remove(test_file)
    os.remove(test_file + ".enc")
    print("\n测试完成")
