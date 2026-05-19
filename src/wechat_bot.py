"""企業微信推送模組"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

import requests

from scraper import Course, AGE_GROUP_LABELS

logger = logging.getLogger(__name__)

# 企業微信 Markdown 長度限制
MAX_MARKDOWN_LENGTH = 4096


class WeChatBotError(Exception):
    """微信推送異常基類"""
    pass


class WebhookURLError(WeChatBotError):
    """Webhook URL 錯誤"""
    pass


class MessageTooLongError(WeChatBotError):
    """消息過長"""
    pass


class SendMessageError(WeChatBotError):
    """發送消息失敗"""
    pass


class WeChatAPIError(WeChatBotError):
    """企業微信 API 錯誤"""
    pass


@dataclass
class WeChatResponse:
    """企業微信 API 響應"""
    errcode: int = 0
    errmsg: str = "ok"


class WeChatBot:
    """企業微信機器人"""

    def __init__(self, webhook_url: str):
        if not webhook_url:
            raise WebhookURLError("Webhook URL 不能為空")

        self.webhook_url = webhook_url
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })

    def send_text_card(
        self,
        title: str,
        description: str,
        url: str = "",
        btntxt: str = "查看詳情",
    ) -> bool:
        """
        發送文本卡片消息

        Args:
            title: 標題
            description: 描述（支持HTML標籤）
            url: 點擊跳轉URL
            btntxt: 按鈕文字

        Returns:
            是否成功
        """
        data = {
            "msgtype": "textcard",
            "textcard": {
                "title": title,
                "description": description,
                "url": url,
                "btntxt": btntxt,
            },
        }
        return self._send(data)

    def send_markdown(self, content: str) -> bool:
        """
        發送 Markdown 消息

        Args:
            content: Markdown 內容

        Returns:
            是否成功
        """
        if len(content) > MAX_MARKDOWN_LENGTH:
            logger.warning(f"Markdown 內容過長 ({len(content)} > {MAX_MARKDOWN_LENGTH})，將被截斷")
            content = content[:MAX_MARKDOWN_LENGTH - 20] + "\n\n...(內容已截斷)"

        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        }
        return self._send(data)

    def send_weekly_courses(
        self,
        age_group: str,
        courses: List[Course],
        week_label: str = "本週",
    ) -> bool:
        """
        發送週課程推送消息

        Args:
            age_group: 年齡層
            courses: 課程列表
            week_label: 週標籤

        Returns:
            是否成功
        """
        content = self.format_course_message(age_group, courses, week_label)
        return self.send_markdown(content)

    def broadcast_to_groups(
        self,
        courses_by_age: Dict[str, List[Course]],
        age_subscribers: Dict[str, List[str]],  # {年齡層: [用戶ID列表]}
    ) -> Dict[str, bool]:
        """
        按年齡組別廣播課程推送

        Args:
            courses_by_age: {年齡層: [課程列表]}
            age_subscribers: {年齡層: [用戶ID列表]}

        Returns:
            {年齡層: 是否成功}
        """
        results = {}
        for age_group, courses in courses_by_age.items():
            if not courses:
                logger.info(f"{age_group} 無課程，跳過推送")
                continue

            success = self.send_weekly_courses(age_group, courses)
            results[age_group] = success

            if success:
                logger.info(f"{age_group} 課程推送成功 ({len(courses)} 個活動)")
            else:
                logger.warning(f"{age_group} 課程推送失敗")

            time.sleep(1)  # 避免觸發頻率限制

        return results

    def format_course_message(
        self,
        age_group: str,
        courses: List[Course],
        week_label: str = "本週",
    ) -> str:
        """
        格式化課程推送消息（Markdown）

        Args:
            age_group: 年齡層
            courses: 課程列表
            week_label: 週標籤

        Returns:
            Markdown 格式消息
        """
        label = AGE_GROUP_LABELS.get(age_group, age_group)

        lines = [
            f"# 📚 家長學堂 — {week_label}適合您的課程",
            "",
            f"## 👶 {label}（{age_group}）",
            f"共 **{len(courses)}** 個活動：",
            "",
        ]

        for i, course in enumerate(courses, 1):
            lines.append(f"**{i}. {course.name}**")

            if course.date and course.date != "詳見活動內容":
                lines.append(f"📅 {course.date}")
            else:
                lines.append(f"📅 詳見活動內容")

            tags = []
            if course.topic:
                tags.append(course.topic)
            if course.target:
                tags.append(course.target)
            if tags:
                lines.append(f"🏷️ {' | '.join(tags)}")

            status_emoji = {"報名中": "🟢", "待報名": "🟡", "已完成報名": "🔴"}
            emoji = status_emoji.get(course.status, "⚪")
            lines.append(f"{emoji} {course.status}")

            if course.detail_url:
                lines.append(f"[查看詳情]({course.detail_url})")

            lines.append("")
            lines.append("---")
            lines.append("")

        lines.append("💡 回覆「修改年齡」可調整訂閱設置")

        return "\n".join(lines)

    def format_course_card(
        self,
        age_group: str,
        courses: List[Course],
        week_label: str = "本週",
    ) -> str:
        """
        格式化課程卡片（HTML格式）

        Args:
            age_group: 年齡層
            courses: 課程列表
            week_label: 週標籤

        Returns:
            HTML 格式描述
        """
        label = AGE_GROUP_LABELS.get(age_group, age_group)

        lines = [
            f"<div class=\"gray\">{week_label}課程推薦</div>",
            f"<div class=\"highlight\">{label}（{age_group}）</div>",
            f"<div class=\"normal\">共 {len(courses)} 個活動</div>",
            "",
        ]

        for course in courses[:5]:  # 卡片最多顯示5個
            lines.append(f"<div class=\"highlight\">{course.name}</div>")
            if course.date and course.date != "詳見活動內容":
                lines.append(f"<div class=\"gray\">{course.date}</div>")
            lines.append(f"<div class=\"normal\">{course.topic} | {course.target}</div>")
            lines.append("")

        if len(courses) > 5:
            lines.append(f"<div class=\"gray\">...還有 {len(courses) - 5} 個活動</div>")

        return "\n".join(lines)

    def _send(self, data: dict, max_retries: int = 3) -> bool:
        """發送消息到企業微信"""
        for attempt in range(max_retries):
            try:
                response = self.session.post(
                    self.webhook_url,
                    data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    timeout=30,
                )
                response.raise_for_status()

                result = response.json()
                errcode = result.get("errcode", -1)

                if errcode == 0:
                    logger.debug(f"消息發送成功")
                    return True
                elif errcode == 40014:  # access_token 過期
                    raise WeChatAPIError(f"Token 過期: {result}")
                elif errcode == 45009:  # 頻率限制
                    retry_after = result.get("retry_after", 5)
                    logger.warning(f"觸發頻率限制，等待 {retry_after} 秒")
                    time.sleep(retry_after)
                else:
                    raise WeChatAPIError(f"API 錯誤: {result}")

            except WeChatAPIError:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"發送失敗，{wait} 秒後重試...")
                    time.sleep(wait)
                else:
                    logger.error(f"消息發送最終失敗")
                    raise SendMessageError(f"無法發送消息: {data}")
            except Exception as e:
                logger.error(f"發送異常: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                else:
                    raise SendMessageError(f"發送異常: {e}")

        return False
