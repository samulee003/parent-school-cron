"""企業微信客服 API 封裝

封裝 access_token 管理、sync_msg、send_msg、send_welcome_msg 等接口
"""

import logging
import os
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger("wecom_cs_api")


class WeComCSAPI:
    """企業微信客服 API 客戶端"""

    BASE_URL = "https://qyapi.weixin.qq.com"

    def __init__(self, corp_id: str, secret: str):
        """
        Args:
            corp_id: 企業 ID
            secret: 應用 Secret（微信客服應用）
        """
        self.corp_id = corp_id
        self.secret = secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self.session = requests.Session()

    # ============== Access Token ==============

    def get_access_token(self) -> str:
        """獲取有效 access_token（帶緩存）"""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        url = f"{self.BASE_URL}/cgi-bin/gettoken"
        params = {"corpid": self.corp_id, "corpsecret": self.secret}

        try:
            resp = self.session.get(url, params=params, timeout=30)
            data = resp.json()

            if data.get("errcode") != 0:
                raise RuntimeError(f"獲取 access_token 失敗: {data}")

            self._access_token = data["access_token"]
            self._token_expires_at = now + data.get("expires_in", 7200)
            logger.info("Access token 刷新成功")
            return self._access_token

        except Exception as e:
            logger.exception(f"獲取 access_token 異常: {e}")
            raise

    def _api_post(self, path: str, payload: dict) -> dict:
        """POST 請求企業微信 API"""
        token = self.get_access_token()
        url = f"{self.BASE_URL}{path}"
        params = {"access_token": token}

        resp = self.session.post(url, params=params, json=payload, timeout=30)
        data = resp.json()

        if data.get("errcode") != 0:
            logger.warning(f"API 錯誤: {data}")
        return data

    def _api_get(self, path: str, params: dict = None) -> dict:
        """GET 請求企業微信 API"""
        token = self.get_access_token()
        url = f"{self.BASE_URL}{path}"
        query = {"access_token": token}
        if params:
            query.update(params)

        resp = self.session.get(url, params=query, timeout=30)
        return resp.json()

    # ============== 消息同步 ==============

    def sync_msg(self, token: str, open_kfid: str = "", limit: int = 100) -> dict:
        """
        同步消息（獲取客服會話消息列表）

        Args:
            token: 回調事件中的 Token（用於分頁）
            open_kfid: 客服帳號 ID（可選，篩選特定客服）
            limit: 最大返回條數

        Returns:
            {
                "errcode": 0,
                "next_cursor": "...",
                "has_more": 1,
                "msg_list": [
                    {
                        "msgid": "...",
                        "open_kfid": "...",
                        "external_userid": "...",
                        "send_time": 123456789,
                        "origin": 3,  # 3=客戶發送, 4=系統發送
                        "msgtype": "text",
                        "text": {"content": "..."}
                    }
                ]
            }
        """
        payload = {"cursor": token, "token": token, "limit": limit}
        if open_kfid:
            payload["open_kfid"] = open_kfid

        return self._api_post("/cgi-bin/kf/sync_msg", payload)

    # ============== 發送消息 ==============

    def send_msg(self, open_kfid: str, external_userid: str, msgtype: str, content: dict) -> dict:
        """
        發送客服消息（主動發送，受 48h/5條 限制）

        Args:
            open_kfid: 客服帳號 ID
            external_userid: 外部聯繫人 ID（家長的微信 ID）
            msgtype: 消息類型 (text, image, voice, video, file, news, mpnews, msgmenu, location, link, business_card, miniprogram)
            content: 對應消息類型的內容字典
        """
        payload = {
            "touser": external_userid,
            "open_kfid": open_kfid,
            "msgtype": msgtype,
            msgtype: content,
        }
        return self._api_post("/cgi-bin/kf/send_msg", payload)

    def send_text_msg(self, open_kfid: str, external_userid: str, content: str) -> dict:
        """發送文本消息"""
        return self.send_msg(open_kfid, external_userid, "text", {"content": content})

    def send_menu_msg(self, open_kfid: str, external_userid: str, headline: str, options: List[dict]) -> dict:
        """
        發送菜單消息（按鈕選項）

        Args:
            headline: 標題
            options: [{"id": "1", "type": "click", "click": {"content": "選項文字"}}]
        """
        return self.send_msg(
            open_kfid,
            external_userid,
            "msgmenu",
            {
                "head_content": headline,
                "list": options,
            },
        )

    def send_news_msg(self, open_kfid: str, external_userid: str, title: str, description: str, url: str, pic_url: str = "") -> dict:
        """發送圖文鏈接消息"""
        articles = [
            {
                "title": title,
                "description": description,
                "url": url,
            }
        ]
        if pic_url:
            articles[0]["picurl"] = pic_url

        return self.send_msg(open_kfid, external_userid, "news", {"articles": articles})

    # ============== 歡迎語 ==============

    def send_welcome_msg(self, welcome_code: str, msgtype: str, content: dict) -> dict:
        """
        發送歡迎語（用戶首次進入客服會話時）

        Args:
            welcome_code: 回調事件中的 WelcomeCode（一次性有效）
        """
        payload = {
            "welcome_code": welcome_code,
            "msgtype": msgtype,
            msgtype: content,
        }
        return self._api_post("/cgi-bin/kf/send_msg_on_event", payload)

    def send_welcome_text(self, welcome_code: str, content: str) -> dict:
        """發送文本歡迎語"""
        return self.send_welcome_msg(welcome_code, "text", {"content": content})

    # ============== 客服帳號管理 ==============

    def list_kf_account(self) -> List[dict]:
        """獲取客服帳號列表"""
        data = self._api_post("/cgi-bin/kf/account/list", {})
        return data.get("account_list", [])

    def get_kf_account(self, open_kfid: str) -> dict:
        """獲取客服帳號詳情"""
        return self._api_post("/cgi-bin/kf/account/get", {"open_kfid": open_kfid})

    # ============== 客戶信息 ==============

    def get_customer_info(self, external_userid: str, open_kfid: str = "") -> dict:
        """獲取客戶基本信息"""
        payload = {"external_userid": external_userid}
        if open_kfid:
            payload["open_kfid"] = open_kfid
        return self._api_post("/cgi-bin/kf/customer/batchget", payload)

    # ============== 會話狀態 ==============

    def get_service_state(self, open_kfid: str, external_userid: str) -> dict:
        """獲取會話狀態"""
        return self._api_post(
            "/cgi-bin/kf/service_state/get",
            {"open_kfid": open_kfid, "external_userid": external_userid},
        )

    def transfer_service(self, open_kfid: str, external_userid: str, receiver: str) -> dict:
        """轉接會話給接待人員"""
        return self._api_post(
            "/cgi-bin/kf/service_state/trans",
            {
                "open_kfid": open_kfid,
                "external_userid": external_userid,
                "receiver": receiver,
            },
        )
