"""企業微信客服輪詢模組

定時調用 sync_msg 拉取消息，無需回調 URL，繞過 ICP 備案要求。
"""

import logging
import os
import threading
import time
from typing import Optional

from wecom_cs_api import WeComCSAPI
from wecom_cs_handler import CSMessageHandler

logger = logging.getLogger("wecom_poller")


class WeComPoller:
    """企業微信客服消息輪詢器"""

    def __init__(self, handler: CSMessageHandler, poll_interval: int = 5):
        """
        Args:
            handler: 消息處理器（含 API 實例）
            poll_interval: 輪詢間隔（秒），建議 3-10 秒
        """
        self.handler = handler
        self.api = handler.api
        self.poll_interval = poll_interval

        # 分頁游標：open_kfid -> cursor
        self._cursors: dict = {}
        # 是否正在運行
        self._running = False
        # 後台線程
        self._thread: Optional[threading.Thread] = None
        # 已知的客服帳號列表
        self._open_kfids: list = []

    # ============== 生命週期 ==============

    def start(self):
        """啟動輪詢（非阻塞，後台線程）"""
        if self._running:
            logger.warning("輪詢器已在運行")
            return

        if not self.api:
            logger.error("API 未初始化，無法啟動輪詢")
            return

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="wecom-poller")
        self._thread.start()
        logger.info(f"輪詢器啟動，間隔 {self.poll_interval}s")

    def stop(self):
        """停止輪詢"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("輪詢器已停止")

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    # ============== 客服帳號發現 ==============

    def _discover_kf_accounts(self) -> list:
        """獲取所有客服帳號 open_kfid"""
        try:
            accounts = self.api.list_kf_account()
            kfids = [a.get("open_kfid") for a in accounts if a.get("open_kfid")]
            if kfids:
                logger.info(f"發現 {len(kfids)} 個客服帳號: {kfids}")
            else:
                logger.warning("未找到任何客服帳號，請確認已在企業微信後台創建客服帳號")
            return kfids
        except Exception as e:
            logger.exception(f"獲取客服帳號失敗: {e}")
            return []

    # ============== 輪詢主循環 ==============

    def _poll_loop(self):
        """輪詢主循環"""
        logger.info("輪詢循環啟動")

        # 首次啟動時等待一下，讓服務完全就緒
        time.sleep(3)

        consecutive_errors = 0
        max_errors = 10

        while self._running:
            try:
                # 定期刷新客服帳號列表
                if not self._open_kfids:
                    self._open_kfids = self._discover_kf_accounts()

                if not self._open_kfids:
                    # 沒有帳號，等待後重試
                    logger.warning("無可用客服帳號，30s 後重試")
                    time.sleep(30)
                    continue

                # 對每個客服帳號輪詢
                for kfid in self._open_kfids:
                    if not self._running:
                        break
                    self._poll_one(kfid)

                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                logger.exception(f"輪詢異常 ({consecutive_errors}/{max_errors}): {e}")

                if consecutive_errors >= max_errors:
                    logger.error("連續錯誤過多，停止輪詢")
                    self._running = False
                    break

            # 等待下一輪
            time.sleep(self.poll_interval)

        logger.info("輪詢循環結束")

    def _poll_one(self, open_kfid: str):
        """
        輪詢單個客服帳號的消息

        sync_msg 的 cursor 機制：
        - 首次調用 cursor=""
        - 返回 next_cursor 用於下次調用
        - has_more=1 表示還有更多消息
        """
        cursor = self._cursors.get(open_kfid, "")

        try:
            result = self.api.sync_msg(
                token=cursor,
                open_kfid=open_kfid,
                limit=50,
            )

            errcode = result.get("errcode", -1)

            # 91110 = cursor 過期，需要重新從頭拉取
            if errcode == 91110:
                logger.warning(f"cursor 過期 (open_kfid={open_kfid})，重置為空")
                self._cursors[open_kfid] = ""
                return

            if errcode != 0:
                logger.warning(f"sync_msg 錯誤 (open_kfid={open_kfid}): {result}")
                return

            msg_list = result.get("msg_list", [])
            next_cursor = result.get("next_cursor", "")
            has_more = result.get("has_more", 0)

            if msg_list:
                logger.info(f"拉取到 {len(msg_list)} 條消息 (open_kfid={open_kfid})")
                for msg in msg_list:
                    try:
                        self.handler._handle_single_msg(msg)
                    except Exception as e:
                        logger.exception(f"處理消息失敗: {e}")

            # 更新 cursor
            if next_cursor:
                self._cursors[open_kfid] = next_cursor

            # has_more=1 表示還有消息，立即再拉取
            if has_more and self._running:
                self._poll_one(open_kfid)

        except Exception as e:
            logger.exception(f"輪詢異常 (open_kfid={open_kfid}): {e}")

    # ============== 狀態 ==============

    def get_status(self) -> dict:
        """獲取輪詢器狀態"""
        return {
            "running": self.is_running,
            "poll_interval": self.poll_interval,
            "kf_accounts": self._open_kfids,
            "cursors": dict(self._cursors),
        }
