"""企業微信客服消息加解密

實現 WeCom 官方推薦的 AES-256-CBC + PKCS7(32) + SHA1 簽名方案
"""

import base64
import hashlib
import json
import logging
import os
import struct
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

logger = logging.getLogger("wecom_crypto")

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logger.warning("pycryptodome 未安裝，AES 功能不可用")


class WeComCrypto:
    """企業微信消息加解密工具"""

    # 企業微信使用 32-byte block size（非標準 16-byte）
    BLOCK_SIZE = 32

    def __init__(self, encoding_aes_key: str, token: str, corp_id: str):
        """
        Args:
            encoding_aes_key: 後台生成的 43 字符 EncodingAESKey
            token: 回調配置中的 Token
            corp_id: 企業 ID
        """
        if not HAS_CRYPTO:
            raise RuntimeError("需要安裝 pycryptodome: pip install pycryptodome")

        self.token = token
        self.corp_id = corp_id

        # EncodingAESKey 解碼 → 32-byte AES key
        key_bytes = base64.b64decode(encoding_aes_key + "=")
        if len(key_bytes) != 32:
            raise ValueError(f"AES key 長度必須為 32 bytes，當前: {len(key_bytes)}")
        self.aes_key = key_bytes
        self.iv = key_bytes[:16]

    # ============== 簽名 ==============

    def verify_signature(self, signature: str, timestamp: str, nonce: str, msg_encrypt: str) -> bool:
        """驗證回調簽名"""
        expected = self._sign(timestamp, nonce, msg_encrypt)
        return expected == signature

    def generate_signature(self, timestamp: str, nonce: str, msg_encrypt: str) -> str:
        """生成回覆簽名"""
        return self._sign(timestamp, nonce, msg_encrypt)

    def _sign(self, timestamp: str, nonce: str, msg_encrypt: str) -> str:
        """SHA1 簽名"""
        raw = "".join(sorted([self.token, timestamp, nonce, msg_encrypt]))
        return hashlib.sha1(raw.encode()).hexdigest()

    # ============== 加密 ==============

    def encrypt(self, msg: str) -> str:
        """
        加密明文消息

        格式: random(16B) + msg_len(4B, big-endian) + msg + corp_id
        """
        # 16-byte 隨機前綴
        random_bytes = os.urandom(16)

        # 消息長度 (4 bytes, network byte order)
        msg_bytes = msg.encode("utf-8")
        msg_len_bytes = struct.pack(">I", len(msg_bytes))

        # corp_id
        corp_id_bytes = self.corp_id.encode("utf-8")

        # 拼接
        plaintext = random_bytes + msg_len_bytes + msg_bytes + corp_id_bytes

        # PKCS7 padding to 32-byte block
        padded = self._pkcs7_pad(plaintext)

        # AES-256-CBC 加密
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        encrypted = cipher.encrypt(padded)

        return base64.b64encode(encrypted).decode("utf-8")

    # ============== 解密 ==============

    def decrypt(self, msg_encrypt: str) -> Tuple[str, str]:
        """
        解密消息

        Returns:
            (明文消息, corp_id)
        """
        encrypted = base64.b64decode(msg_encrypt)

        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        decrypted = cipher.decrypt(encrypted)

        # PKCS7 unpad
        plaintext = self._pkcs7_unpad(decrypted)

        # 解析格式: random(16) + msg_len(4) + msg + appid
        if len(plaintext) < 20:
            raise ValueError(f"解密後數據過短: {len(plaintext)} bytes")

        msg_len = struct.unpack(">I", plaintext[16:20])[0]
        msg = plaintext[20:20 + msg_len].decode("utf-8")
        corp_id = plaintext[20 + msg_len:].decode("utf-8")

        return msg, corp_id

    # ============== PKCS7 (block size = 32) ==============

    def _pkcs7_pad(self, data: bytes) -> bytes:
        """PKCS7 padding，block size = 32"""
        pad_len = self.BLOCK_SIZE - (len(data) % self.BLOCK_SIZE)
        padding = bytes([pad_len] * pad_len)
        return data + padding

    def _pkcs7_unpad(self, data: bytes) -> bytes:
        """PKCS7 unpad，block size = 32"""
        if not data:
            return data
        pad_len = data[-1]
        if pad_len > self.BLOCK_SIZE or pad_len == 0:
            raise ValueError(f"Invalid PKCS7 padding: {pad_len}")
        return data[:-pad_len]

    # ============== XML 封裝 / 解封 ==============

    def encrypt_msg(self, msg: str, timestamp: str, nonce: str) -> str:
        """
        加密並封裝成 XML

        返回企業微信要求的 XML 格式
        """
        encrypt = self.encrypt(msg)
        signature = self.generate_signature(timestamp, nonce, encrypt)

        xml_template = (
            "<xml>"
            "<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            "<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            "<TimeStamp>{timestamp}</TimeStamp>"
            "<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )
        return xml_template.format(
            encrypt=encrypt,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
        )

    def decrypt_msg(self, msg_signature: str, timestamp: str, nonce: str, post_data: str) -> str:
        """
        從 POST 的 XML 中解密消息

        Args:
            msg_signature: URL 參數中的 msg_signature
            timestamp: URL 參數中的 timestamp
            nonce: URL 參數中的 nonce
            post_data: POST body (XML string)

        Returns:
            解密後的明文（XML 字符串）
        """
        # 解析 XML
        root = ET.fromstring(post_data)
        encrypt_node = root.find("Encrypt")
        if encrypt_node is None:
            raise ValueError("XML 中缺少 Encrypt 節點")

        msg_encrypt = encrypt_node.text

        # 驗證簽名
        if not self.verify_signature(msg_signature, timestamp, nonce, msg_encrypt):
            raise ValueError("簽名驗證失敗")

        # 解密
        plaintext, corp_id = self.decrypt(msg_encrypt)

        if corp_id != self.corp_id:
            raise ValueError(f"corp_id 不匹配: {corp_id} != {self.corp_id}")

        return plaintext

    def decrypt_event_msg(self, msg_signature: str, timestamp: str, nonce: str, post_data: str) -> dict:
        """
        解密並解析事件消息為字典

        返回解析後的 XML 字典，如:
        {
            "ToUserName": "corp_id",
            "CreateTime": "123456789",
            "MsgType": "event",
            "Event": "kf_msg_or_event",
            "Token": "...",
        }
        """
        plaintext = self.decrypt_msg(msg_signature, timestamp, nonce, post_data)

        root = ET.fromstring(plaintext)
        result = {}
        for child in root:
            result[child.tag] = child.text or ""

        return result


# ============== 便捷函數 ==============

def parse_xml_dict(xml_str: str) -> dict:
    """將 XML 字符串解析為字典"""
    root = ET.fromstring(xml_str)
    return {child.tag: child.text or "" for child in root}


def build_text_xml(to_user: str, from_user: str, content: str) -> str:
    """構建被動回覆的文本消息 XML"""
    import time
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )
