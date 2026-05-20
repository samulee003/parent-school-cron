import hashlib
import hmac
import asyncio
import json
import logging
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
logging.disable(logging.CRITICAL)

from fastapi import HTTPException

import api_server
from scraper import Course
from whatsapp_handler import WhatsAppHandler, is_valid_meta_signature


class FakeCrawler:
    def __init__(self, courses=None):
        self.courses = courses or [
            Course(
                id="c1",
                name="嬰幼繪本氹氹轉",
                date="2026/06/20 星期六 15:00-16:00",
                date_parsed=None,
                age_group="0-2歲",
                topic="家庭關係",
                target="親子",
                status="報名中",
                detail_url="https://example.test/course/c1",
            ),
            Course(
                id="c2",
                name="青少年親子溝通工作坊",
                date="2026/07/01 星期三 19:00-20:30",
                date_parsed=None,
                age_group="13-18歲",
                topic="社會人際關係",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/c2",
            ),
        ]

    def fetch_all_open_courses(self, max_retries=3, delay=1.0):
        return list(self.courses)


class FakeBot:
    def __init__(self):
        self.crawler = FakeCrawler()


class FakeZeaburBotShape:
    def __init__(self):
        self.scraper = FakeCrawler()


class WhatsAppHandlerTests(unittest.TestCase):
    def make_handler(self):
        handler = WhatsAppHandler()
        handler._get_bot = lambda: FakeBot()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True
        return handler, sent

    def test_courses_keyword_without_profile_asks_for_context(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "課程")

        self.assertEqual(sent[0][0], "85360000000")
        self.assertIn("我先幫你縮窄", sent[0][1])
        self.assertIn("小朋友1歲", sent[0][1])
        self.assertNotIn("嬰幼繪本氹氹轉", sent[0][1])

    def test_all_courses_returns_compact_course_objects_without_links(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "全部課程")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertIn("青少年親子溝通工作坊", sent[0][1])
        self.assertNotIn("https://example.test/course/c1", sent[0][1])
        self.assertIn("回覆 *詳情1* 看報名連結", sent[0][1])

    def test_courses_keyword_supports_zeabur_bot_scraper_shape(self):
        handler = WhatsAppHandler()
        handler._get_bot = lambda: FakeZeaburBotShape()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "全部課程")

        self.assertEqual(sent[0][0], "85360000000")
        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])

    def test_age_keyword_filters_courses(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "0-2歲")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertNotIn("青少年親子溝通工作坊", sent[0][1])

    def test_courses_keyword_prompts_next_page_when_more_courses_exist(self):
        courses = [
            Course(
                id=f"c{i}",
                name=f"課程{i}",
                date="2026/06/20 星期六 15:00-16:00",
                date_parsed=None,
                age_group="0-2歲",
                topic="家庭關係",
                target="親子",
                status="報名中",
                detail_url=f"https://example.test/course/c{i}",
            )
            for i in range(1, 8)
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "全部課程")

        self.assertIn("第 1/3 頁", sent[0][1])
        self.assertIn("課程1", sent[0][1])
        self.assertIn("課程3", sent[0][1])
        self.assertNotIn("課程4", sent[0][1])
        self.assertIn("輸入 *更多* 或 *下一頁*", sent[0][1])

    def test_next_page_returns_remaining_courses_for_last_query(self):
        courses = [
            Course(
                id=f"c{i}",
                name=f"課程{i}",
                date="2026/06/20 星期六 15:00-16:00",
                date_parsed=None,
                age_group="0-2歲",
                topic="家庭關係",
                target="親子",
                status="報名中",
                detail_url=f"https://example.test/course/c{i}",
            )
            for i in range(1, 8)
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "全部課程")
        handler._handle_text_message("85360000000", "更多")

        self.assertIn("第 2/3 頁", sent[1][1])
        self.assertIn("課程4", sent[1][1])
        self.assertIn("課程6", sent[1][1])
        self.assertNotIn("課程7", sent[1][1])

    def test_detail_request_returns_link_for_visible_course(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "全部課程")
        handler._handle_text_message("85360000000", "詳情1")

        self.assertIn("嬰幼繪本氹氹轉", sent[1][1])
        self.assertIn("https://example.test/course/c1", sent[1][1])

    def test_filter_by_target_keeps_list_focused(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "家長")

        self.assertIn("青少年親子溝通工作坊", sent[0][1])
        self.assertNotIn("嬰幼繪本氹氹轉", sent[0][1])

    def test_agentic_recommendation_infers_child_age_from_sentence(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertIn("為什麼推薦", sent[0][1])
        self.assertNotIn("青少年親子溝通工作坊", sent[0][1])

    def test_deepseek_is_used_for_agentic_recommendation_when_configured(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": "我會先推介 1. 嬰幼繪本氹氹轉。回覆 詳情1 看連結。"
                            }
                        }
                    ]
                }

                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("我會先推介", sent[0][1])
        self.assertIn("詳情1", sent[0][1])
        self.assertTrue(post.called)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_meta_signature_verification(self):
        body = b'{"object":"whatsapp_business_account"}'
        secret = "app-secret"
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

        self.assertTrue(is_valid_meta_signature(body, f"sha256={digest}", secret))
        self.assertFalse(is_valid_meta_signature(body, "sha256=bad", secret))
        self.assertFalse(is_valid_meta_signature(body, f"sha1={digest}", secret))

    def test_whatsapp_webhook_rejects_bad_meta_signature_when_secret_configured(self):
        body = b'{"object":"whatsapp_business_account","entry":[]}'
        request = FakeRequest(body, {"x-hub-signature-256": "sha256=bad"})
        old_secret = os.environ.get("WHATSAPP_APP_SECRET")
        os.environ["WHATSAPP_APP_SECRET"] = "app-secret"
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.whatsapp_webhook(request))
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            if old_secret is None:
                os.environ.pop("WHATSAPP_APP_SECRET", None)
            else:
                os.environ["WHATSAPP_APP_SECRET"] = old_secret

    def test_admin_endpoints_require_configured_secret(self):
        old_cron = os.environ.get("CRON_SECRET")
        old_admin = os.environ.get("ADMIN_SECRET")
        os.environ.pop("CRON_SECRET", None)
        os.environ.pop("ADMIN_SECRET", None)
        old_get_bot = api_server.get_bot
        api_server.get_bot = lambda: FakePushBot()
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.api_cron(secret="anything"))
            self.assertEqual(ctx.exception.status_code, 500)
        finally:
            api_server.get_bot = old_get_bot
            if old_cron is not None:
                os.environ["CRON_SECRET"] = old_cron
            if old_admin is not None:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_users_endpoint_rejects_wrong_secret(self):
        old_cron = os.environ.get("CRON_SECRET")
        old_admin = os.environ.get("ADMIN_SECRET")
        os.environ["CRON_SECRET"] = "cron-secret"
        os.environ.pop("ADMIN_SECRET", None)
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.api_users(secret="wrong"))
            self.assertEqual(ctx.exception.status_code, 401)
        finally:
            if old_cron is None:
                os.environ.pop("CRON_SECRET", None)
            else:
                os.environ["CRON_SECRET"] = old_cron
            if old_admin is not None:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_root_reports_whatsapp_first_version(self):
        result = asyncio.run(api_server.root())

        self.assertEqual(result["version"], "3.0.0")
        self.assertEqual(result["primary_channel"], "whatsapp")


class FakeRequest:
    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    async def body(self):
        return self._body

    async def json(self):
        return json.loads(self._body.decode("utf-8"))


class FakePushBot:
    def run_push(self):
        return {"success": True, "courses": 0, "users": 0, "error": ""}


if __name__ == "__main__":
    unittest.main()
