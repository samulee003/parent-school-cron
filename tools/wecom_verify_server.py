"""企業微信回調驗證一次性伺服器

用法：
1. 修改下方 TOKEN、ENCODING_AES_KEY、CORP_ID
2. python wecom_verify_server.py
3. 另開終端跑: cloudflared tunnel --url http://localhost:8888
4. 把 cloudflared 給的 URL + /wecom-callback 填入企業微信後台
5. 驗證通過後，去加可信 IP: 43.167.10.6
6. Ctrl+C 關掉這個腳本和 cloudflared
"""

import hashlib
import os
import struct
import base64
import xml.etree.ElementTree as ET
from flask import Flask, request, abort

app = Flask(__name__)

TOKEN = os.environ.get("WECOM_TOKEN", "")
ENCODING_AES_KEY = os.environ.get("WECOM_ENCODING_AES_KEY", "")
CORP_ID = os.environ.get("WECOM_CORP_ID", "")


class WeComCallback:
    """極簡企業微信回調驗證（只處理 echostr 解密）"""

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id
        # AES Key = EncodingAESKey + "=" 再 base64 解碼
        self.aes_key = base64.b64decode(encoding_aes_key + "=")

    def verify_signature(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> bool:
        sort_list = sorted([self.token, timestamp, nonce, echostr])
        sha1 = hashlib.sha1("".join(sort_list).encode()).hexdigest()
        return sha1 == msg_signature

    def decrypt(self, encrypted: str) -> str:
        from Crypto.Cipher import AES as CryptoAES
        cipher = CryptoAES.new(self.aes_key, CryptoAES.MODE_CBC, self.aes_key[:16])
        plain = cipher.decrypt(base64.b64decode(encrypted))
        # 去掉 PKCS7 padding
        pad = plain[-1]
        plain = plain[:-pad]
        # 去掉 16 字節隨機串 + 4 字節消息長度 + 消息 + CorpID
        msg_len = struct.unpack("!I", plain[16:20])[0]
        msg = plain[20:20 + msg_len].decode("utf-8")
        return msg


@app.route("/wecom-callback", methods=["GET"])
def verify():
    """GET 請求 — 企業微信驗證 URL"""
    msg_signature = request.args.get("msg_signature", "")
    timestamp = request.args.get("timestamp", "")
    nonce = request.args.get("nonce", "")
    echostr = request.args.get("echostr", "")

    if not echostr:
        return "waiting for WeCom verification..."

    print(f"收到驗證請求: signature={msg_signature}, ts={timestamp}, nonce={nonce}")

    try:
        callback = WeComCallback(TOKEN, ENCODING_AES_KEY, CORP_ID)

        if not callback.verify_signature(msg_signature, timestamp, nonce, echostr):
            print("簽名驗證失敗！")
            abort(403)

        plaintext = callback.decrypt(echostr)
        print(f"✅ 驗證成功！解密結果: {plaintext}")
        return plaintext

    except Exception as e:
        print(f"❌ 驗證失敗: {e}")
        import traceback
        traceback.print_exc()
        abort(500)


@app.route("/wecom-callback", methods=["POST"])
def receive():
    """POST 請求 — 接收消息（驗證通過後不需要真的處理）"""
    return "success"


if __name__ == "__main__":
    print("=" * 50)
    print("企業微信回調驗證伺服器")
    print(f"監聯端口: http://localhost:8888/wecom-callback")
    print("")
    print("請先設定 WECOM_TOKEN、WECOM_ENCODING_AES_KEY、WECOM_CORP_ID")
    print("然後用 cloudflared tunnel --url http://localhost:8888")
    print("=" * 50)
    if not TOKEN or not ENCODING_AES_KEY or not CORP_ID:
        raise SystemExit("缺少 WeCom 環境變數，已停止。")
    app.run(host="0.0.0.0", port=8888)
