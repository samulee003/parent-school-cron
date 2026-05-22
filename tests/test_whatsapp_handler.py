import hashlib
import hmac
import asyncio
import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
logging.disable(logging.CRITICAL)

from fastapi import HTTPException

import api_server
from scraper import Course
from whatsapp_handler import WhatsAppHandler, is_valid_meta_signature
from whatsapp_memory import WhatsAppMemoryStore


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


class FakeAcademyCrawler:
    def __init__(self):
        self.open_courses = [
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
            )
        ]
        self.teen_courses = [
            Course(
                id="c2",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/c2",
            )
        ]

    def fetch_all_open_courses(self, max_retries=3, delay=1.0):
        return list(self.open_courses)

    def fetch_courses(self, age_group="", status="", max_retries=3, delay=1.0):
        if age_group == "13-18歲":
            return list(self.teen_courses)
        return list(self.open_courses)


class WhatsAppHandlerTests(unittest.TestCase):
    def setUp(self):
        self._old_memory_db = os.environ.get("WHATSAPP_MEMORY_DB")
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["WHATSAPP_MEMORY_DB"] = os.path.join(self._tmpdir.name, "memory.db")

    def tearDown(self):
        self._tmpdir.cleanup()
        if self._old_memory_db is None:
            os.environ.pop("WHATSAPP_MEMORY_DB", None)
        else:
            os.environ["WHATSAPP_MEMORY_DB"] = self._old_memory_db

    def make_handler(self):
        handler = WhatsAppHandler()
        handler._get_bot = lambda: FakeBot()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True
        return handler, sent

    def admin_request(self, secret="admin-secret", cookie=""):
        headers = {}
        cookies = {}
        if secret:
            headers["authorization"] = f"Bearer {secret}"
        if cookie:
            cookies["parent_school_admin"] = cookie
        return FakeRequest(b"{}", headers, cookies)

    def test_courses_keyword_without_profile_asks_parent_interview_question(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "課程")

        self.assertEqual(sent[0][0], "85360000000")
        self.assertIn("小朋友幾多歲", sent[0][1])
        self.assertIn("情緒", sent[0][1])
        self.assertIn("學習", sent[0][1])
        self.assertIn("親子溝通", sent[0][1])
        self.assertIn("升學壓力", sent[0][1])
        self.assertNotIn("嬰幼繪本氹氹轉", sent[0][1])

    def test_search_activity_without_profile_asks_parent_interview_question(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "搜尋活動")

        self.assertIn("小朋友幾多歲", sent[0][1])
        self.assertIn("情緒", sent[0][1])
        self.assertNotIn("未能轉成課程條件", sent[0][1])
        self.assertEqual(handler._memory.list_agent_flags(), [])

    def test_complete_onboarding_answer_stores_memory_and_adds_soft_consent_prompt(self):
        courses = [
            Course(
                id="c-health",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/health",
                summary="覺察青少年壓力與焦慮，學習親子衝突後真誠對話。",
                registration_url="https://example.test/register/health",
            )
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "課程")
        handler._handle_text_message("85360000000", "13歲，最近情緒壓力大")

        profile = handler._memory.get_profile("85360000000")
        conversation = handler._memory.get_conversation("85360000000")

        self.assertEqual(profile["age_groups"], ["13-18歲"])
        self.assertIn("情緒壓力", profile["pain_points"])
        self.assertIn("健康情緒與青少年同行", sent[1][1])
        self.assertIn("https://example.test/register/health", sent[1][1])
        self.assertIn("同意推送", sent[1][1])
        self.assertIn("青少年", conversation["tags"])
        self.assertIn("情緒壓力", conversation["tags"])
        self.assertIn("onboarding:", conversation["notes"])

    def test_onboarding_age_only_asks_for_concern_without_calling_deepseek(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", "13歲")

        self.assertFalse(post.called)
        self.assertIn("最想處理", sent[0][1])
        self.assertIn("情緒", sent[0][1])
        self.assertNotIn("青少年親子溝通工作坊", sent[0][1])

    def test_onboarding_pain_only_asks_for_child_age_without_calling_deepseek(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", "最近情緒壓力大")

        self.assertFalse(post.called)
        self.assertIn("小朋友幾多歲", sent[0][1])
        self.assertNotIn("青少年親子溝通工作坊", sent[0][1])

    def test_recommendation_does_not_repeat_consent_prompt_after_consent_set(self):
        handler, sent = self.make_handler()
        handler._memory.update_conversation("85360000000", consent_status="allowed")

        handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertNotIn("同意推送", sent[0][1])

    def test_onboarding_meta_preserves_operator_notes_verbatim(self):
        handler, _ = self.make_handler()
        operator_notes = "人工備註第一行\n\nonboarding: 這句是人工寫的，不是系統欄位\n最後一行"
        handler._memory.update_conversation("85360000000", notes=operator_notes)

        handler._handle_text_message("85360000000", "13歲，最近情緒壓力大")

        notes = handler._memory.get_conversation("85360000000")["notes"]
        self.assertIn(operator_notes, notes)
        self.assertIn("[[ai:onboarding]] onboarding:", notes)

    def test_all_courses_returns_compact_course_objects_with_links(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "全部課程")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertIn("青少年親子溝通工作坊", sent[0][1])
        self.assertIn("https://example.test/course/c1", sent[0][1])
        self.assertNotIn("回覆 *詳情1* 看報名連結", sent[0][1])

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

        handler._handle_text_message("85360000000", "0-2歲，想親子活動")

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

    def test_next_page_accepts_haiyouma_question(self):
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
        handler._handle_text_message("85360000000", "還有嗎？")

        self.assertIn("第 2/3 頁", sent[1][1])
        self.assertIn("課程4", sent[1][1])
        self.assertNotIn("課程1", sent[1][1])

    def test_persisted_last_query_supports_more_after_restart(self):
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
        sent = []
        first = WhatsAppHandler()
        first._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        first._send_text = lambda to, text: sent.append((to, text)) or True
        first._handle_text_message("85360000000", "全部課程")

        second = WhatsAppHandler()
        second._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        second._send_text = lambda to, text: sent.append((to, text)) or True
        second._handle_text_message("85360000000", "還有嗎？")

        self.assertIn("第 2/3 頁", sent[1][1])
        self.assertIn("課程4", sent[1][1])
        self.assertNotIn("課程1", sent[1][1])

    def test_profile_is_persisted_across_handler_restart(self):
        sent = []
        first = WhatsAppHandler()
        first._get_bot = lambda: FakeBot()
        first._send_text = lambda to, text: sent.append((to, text)) or True
        first._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        second = WhatsAppHandler()
        second._get_bot = lambda: FakeBot()
        second._send_text = lambda to, text: sent.append((to, text)) or True
        second._handle_text_message("85360000000", "幫我揀")

        self.assertIn("嬰幼繪本氹氹轉", sent[1][1])
        self.assertNotIn("青少年親子溝通工作坊", sent[1][1])

    def test_negative_target_can_refine_existing_memory(self):
        courses = [
            Course(
                id="c1",
                name="嬰幼親子活動",
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
                name="嬰幼家長講座",
                date="2026/06/21 星期日 10:00-11:00",
                date_parsed=None,
                age_group="0-2歲",
                topic="家庭關係",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/c2",
            ),
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")
        handler._handle_text_message("85360000000", "不要親子，要家長課")

        self.assertIn("嬰幼家長講座", sent[1][1])
        self.assertNotIn("嬰幼親子活動", sent[1][1])

    def test_multiple_child_ages_are_remembered_together(self):
        courses = [
            Course(
                id="c1",
                name="幼兒親子活動",
                date="2026/06/20 星期六 15:00-16:00",
                date_parsed=None,
                age_group="3-6歲",
                age_groups=["3-6歲"],
                topic="家庭關係",
                target="親子",
                status="報名中",
                detail_url="https://example.test/course/c1",
            ),
            Course(
                id="c2",
                name="青少年親子工作坊",
                date="2026/06/21 星期日 10:00-11:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="家庭關係",
                target="親子",
                status="報名中",
                detail_url="https://example.test/course/c2",
            ),
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "我有一個4歲一個13歲，想親子活動")

        self.assertIn("幼兒親子活動", sent[0][1])
        self.assertIn("青少年親子工作坊", sent[0][1])

    def test_profile_command_accepts_question_mark(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")
        handler._handle_text_message("85360000000", "我的偏好？")

        self.assertIn("我目前記得的偏好", sent[1][1])
        self.assertIn("嬰幼兒期", sent[1][1])

    def test_age_only_refinement_preserves_existing_target(self):
        courses = [
            Course(
                id="c1",
                name="青少年親子工作坊",
                date="2026/06/21 星期日 10:00-11:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="家庭關係",
                target="親子",
                status="報名中",
                detail_url="https://example.test/course/c1",
            ),
            Course(
                id="c2",
                name="青少年家長講座",
                date="2026/06/22 星期一 19:00-20:30",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/c2",
            ),
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "不要親子，要家長課")
        handler._handle_text_message("85360000000", "只要青少年")

        self.assertIn("青少年家長講座", sent[1][1])
        self.assertNotIn("青少年親子工作坊", sent[1][1])

    def test_deepseek_500_falls_back_to_rule_based_recommendation(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 500
                post.return_value.text = "server error"

                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertIn("為什麼推薦", sent[0][1])

    def test_deepseek_exception_falls_back_to_rule_based_recommendation(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post", side_effect=RuntimeError("boom")):
                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertIn("為什麼推薦", sent[0][1])

    def test_deepseek_response_is_cached_for_identical_query(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": "LLM 快取測試回覆"}}]
                }

                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")
                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertEqual(post.call_count, 1)
        self.assertTrue(sent[0][1].startswith("LLM 快取測試回覆"))
        self.assertTrue(sent[1][1].startswith("LLM 快取測試回覆"))
        self.assertIn("同意推送", sent[0][1])
        self.assertEqual(handler._memory.get_llm_usage_count("85360000000"), 1)

    def test_deepseek_daily_limit_falls_back_without_api_call(self):
        handler, sent = self.make_handler()

        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_DAILY_LIMIT_PER_USER": "1"},
            clear=False,
        ):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": "LLM 第一次回覆"}}]
                }

                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")
                handler._handle_text_message("85360000000", "小朋友1歲，想家庭關係課程")

        self.assertEqual(post.call_count, 1)
        self.assertTrue(sent[0][1].startswith("LLM 第一次回覆"))
        self.assertIn("同意推送", sent[0][1])
        self.assertIn("嬰幼繪本氹氹轉", sent[1][1])
        self.assertIn("為什麼推薦", sent[1][1])
        self.assertEqual(handler._memory.get_llm_usage_count("85360000000"), 1)

    def test_deepseek_daily_limit_zero_disables_llm(self):
        handler, sent = self.make_handler()

        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_DAILY_LIMIT_PER_USER": "0"},
            clear=False,
        ):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertFalse(post.called)
        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertIn("為什麼推薦", sent[0][1])
        self.assertEqual(handler._memory.get_llm_usage_count("85360000000"), 0)

    def test_deepseek_global_daily_limit_falls_back_for_other_users(self):
        handler, sent = self.make_handler()

        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_DAILY_LIMIT_PER_USER": "12",
                "DEEPSEEK_DAILY_LIMIT_GLOBAL": "1",
            },
            clear=False,
        ):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": "LLM 全域第一次回覆"}}]
                }

                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")
                handler._handle_text_message("85360000001", "小朋友1歲，想家庭關係課程")

        self.assertEqual(post.call_count, 1)
        self.assertTrue(sent[0][1].startswith("LLM 全域第一次回覆"))
        self.assertIn("同意推送", sent[0][1])
        self.assertIn("嬰幼繪本氹氹轉", sent[1][1])
        self.assertIn("為什麼推薦", sent[1][1])
        self.assertEqual(handler._memory.get_llm_usage_count("__global__"), 1)

    def test_off_topic_recommendation_does_not_call_deepseek_after_profile_exists(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "小朋友13歲")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", "推薦餐廳")

        self.assertFalse(post.called)
        self.assertIn("只協助查詢和推薦", sent[1][1])
        self.assertIn("澳門家長學堂課程", sent[1][1])

    def test_off_topic_message_with_child_age_does_not_call_deepseek_or_update_profile(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", "我小朋友13歲，想推薦餐廳")

        self.assertFalse(post.called)
        self.assertIn("只協助查詢和推薦", sent[0][1])
        self.assertEqual(handler._load_profile("85360000000"), {})

    def test_unrelated_latest_question_does_not_call_deepseek(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "小朋友13歲")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", "最新電影有什麼推薦")

        self.assertFalse(post.called)
        self.assertIn("澳門家長學堂課程", sent[1][1])

    def test_long_unrelated_message_is_stopped_locally(self):
        handler, sent = self.make_handler()
        long_message = (
            "你的 ChatGPT 只是工具嗎？科學證實我們正跟 AI 建立依附關係。"
            "這段文字很長，想請你評論人工智能和心理學文章，重點是情感依附和人機互動。"
        )

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", long_message)

        self.assertFalse(post.called)
        self.assertIn("只協助查詢和推薦", sent[0][1])
        self.assertEqual(handler._load_profile("85360000000"), {})

    def test_transcript_records_parent_and_ai_messages(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "課程")

        self.assertEqual(len(sent), 1)
        messages = handler._memory.get_messages("85360000000")
        self.assertEqual([m["direction"] for m in messages], ["inbound", "outbound"])
        self.assertEqual([m["source"] for m in messages], ["parent", "ai"])
        self.assertEqual(messages[0]["body"], "課程")
        self.assertIn("小朋友幾多歲", messages[1]["body"])
        conversations = handler._memory.list_conversations()
        self.assertEqual(conversations[0]["phone"], "85360000000")
        self.assertEqual(conversations[0]["status"], "ai")

    def test_human_takeover_suppresses_ai_auto_reply(self):
        handler, sent = self.make_handler()
        handler._memory.set_conversation_status("85360000000", "human")

        handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertEqual(sent, [])
        messages = handler._memory.get_messages("85360000000")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["direction"], "inbound")
        self.assertEqual(messages[0]["source"], "parent")

    def test_parent_can_allow_proactive_push_from_whatsapp(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "我同意收課程提醒")

        conversation = handler._memory.get_conversation("85360000000")
        self.assertEqual(conversation["consent_status"], "allowed")
        self.assertIn("主動課程提醒", sent[0][1])
        self.assertIn("暫停推送", sent[0][1])

    def test_parent_can_pause_proactive_push_from_whatsapp(self):
        handler, sent = self.make_handler()
        handler._memory.update_conversation("85360000000", consent_status="allowed")

        handler._handle_text_message("85360000000", "暫停推送")

        conversation = handler._memory.get_conversation("85360000000")
        self.assertEqual(conversation["consent_status"], "paused")
        self.assertIn("暫停", sent[0][1])

    def test_parent_denial_does_not_enable_proactive_push_from_whatsapp(self):
        handler, sent = self.make_handler()

        for index, message in enumerate(["不同意推送", "暫時不同意推送"]):
            phone = f"8536000000{index}"
            handler._handle_text_message(phone, message)

            conversation = handler._memory.get_conversation(phone)
            self.assertEqual(conversation["consent_status"], "paused")
            self.assertIn("暫停", sent[index][1])

    def test_unknown_message_creates_uncertain_flag(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "唔該你睇下")

        self.assertIn("未能轉成課程條件", sent[0][1])
        flags = handler._memory.list_agent_flags()
        self.assertEqual(flags[0]["flag_type"], "uncertain")
        self.assertEqual(flags[0]["phone"], "85360000000")

    def test_no_match_creates_queue_flag(self):
        courses = [
            Course(
                id="c1",
                name="嬰幼親子活動",
                date="2026/06/20 星期六 15:00-16:00",
                date_parsed=None,
                age_group="0-2歲",
                age_groups=["0-2歲"],
                topic="家庭關係",
                target="親子",
                status="報名中",
                detail_url="https://example.test/course/c1",
            )
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "孩子13歲，最近情緒壓力大")

        self.assertIn("暫時沒有", sent[0][1])
        self.assertNotIn("同意推送", sent[0][1])
        flags = handler._memory.list_agent_flags()
        self.assertEqual(flags[0]["flag_type"], "no_match")

    def test_duplicate_whatsapp_message_id_is_processed_once(self):
        handler, sent = self.make_handler()
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.test-1",
                                        "type": "text",
                                        "from": "85360000000",
                                        "text": {"body": "小朋友1歲，想親子活動"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

        handler.handle_webhook(payload)
        handler.handle_webhook(payload)

        self.assertEqual(len(sent), 1)
        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])

    def test_claim_webhook_messages_prevents_duplicate_background_work(self):
        handler, _ = self.make_handler()
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.test-claim-1",
                                        "type": "text",
                                        "from": "85360000000",
                                        "text": {"body": "小朋友1歲，想親子活動"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

        self.assertTrue(handler.claim_webhook_messages(payload))
        self.assertFalse(handler.claim_webhook_messages(payload))

    def test_voice_note_webhook_records_transcript_and_guides_parent_when_transcription_unavailable(self):
        handler, sent = self.make_handler()
        handler._last_transcription_error = {"error_code": "insufficient_quota"}
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.voice-1",
                                        "type": "audio",
                                        "from": "85360000000",
                                        "audio": {
                                            "id": "media-audio-1",
                                            "mime_type": "audio/ogg; codecs=opus",
                                            "voice": True,
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

        handler.handle_webhook(payload)

        self.assertEqual(len(sent), 1)
        self.assertIn("語音訊息", sent[0][1])
        self.assertIn("語音輸入成文字", sent[0][1])
        messages = handler._memory.get_messages("85360000000")
        self.assertEqual([m["direction"] for m in messages], ["inbound", "outbound"])
        self.assertEqual(messages[0]["body"], "[語音訊息]")
        self.assertEqual(messages[0]["meta"]["message_type"], "audio")
        self.assertTrue(messages[0]["meta"]["voice"])
        flags = handler._memory.list_agent_flags(phone="85360000000")
        self.assertEqual(flags[0]["flag_type"], "handoff_needed")
        self.assertEqual(flags[0]["meta"]["media_id"], "media-audio-1")
        self.assertEqual(
            flags[0]["meta"]["transcription_error"]["error_code"],
            "insufficient_quota",
        )
        self.assertIn("quota 不足", flags[0]["summary"])

    def test_voice_note_webhook_transcribes_and_uses_course_recommendation_flow(self):
        handler, sent = self.make_handler()
        handler._transcribe_audio_message = (
            lambda media_id, mime_type="": "小朋友13歲，想家長課"
        )
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.voice-2",
                                        "type": "audio",
                                        "from": "85360000000",
                                        "audio": {
                                            "id": "media-audio-2",
                                            "mime_type": "audio/ogg; codecs=opus",
                                            "voice": True,
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

        handler.handle_webhook(payload)

        self.assertEqual(len(sent), 1)
        self.assertIn("青少年親子溝通工作坊", sent[0][1])
        self.assertNotIn("暫時未能直接聽錄音", sent[0][1])
        messages = handler._memory.get_messages("85360000000")
        self.assertEqual([m["direction"] for m in messages], ["inbound", "inbound", "outbound"])
        self.assertEqual(messages[0]["body"], "[語音訊息]")
        self.assertEqual(messages[1]["body"], "小朋友13歲，想家長課")
        self.assertEqual(messages[1]["meta"]["message_type"], "audio_transcription")
        self.assertEqual(messages[1]["meta"]["media_id"], "media-audio-2")
        self.assertEqual(handler._memory.list_agent_flags(phone="85360000000"), [])

    def test_stepfun_transcription_parses_sse_done_event(self):
        old_provider = os.environ.get("AUDIO_TRANSCRIPTION_PROVIDER")
        old_key = os.environ.get("STEPFUN_API_KEY")
        old_base_url = os.environ.get("STEPFUN_BASE_URL")
        old_model = os.environ.get("STEPFUN_ASR_MODEL")
        os.environ["AUDIO_TRANSCRIPTION_PROVIDER"] = "stepfun"
        os.environ["STEPFUN_API_KEY"] = "stepfun-test-key"
        os.environ["STEPFUN_BASE_URL"] = "https://stepfun.example/v1"
        os.environ["STEPFUN_ASR_MODEL"] = "stepaudio-2.5-asr"

        class FakeStepFunResponse:
            status_code = 200

            def iter_lines(self, decode_unicode=True):
                yield 'data: {"type":"transcript.text.delta","delta":"小朋友13歲"}'
                yield 'data: {"type":"transcript.text.done","text":"小朋友13歲，想家長課"}'

        handler, _sent = self.make_handler()
        try:
            with patch("whatsapp_handler.requests.post", return_value=FakeStepFunResponse()) as post:
                transcript = handler._transcribe_audio_bytes(b"voice-bytes", "audio/ogg; codecs=opus")
        finally:
            for key, value in [
                ("AUDIO_TRANSCRIPTION_PROVIDER", old_provider),
                ("STEPFUN_API_KEY", old_key),
                ("STEPFUN_BASE_URL", old_base_url),
                ("STEPFUN_ASR_MODEL", old_model),
            ]:
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(transcript, "小朋友13歲，想家長課")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://stepfun.example/v1/audio/asr/sse")
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["headers"]["Accept"], "text/event-stream")
        self.assertEqual(kwargs["json"]["audio"]["input"]["format"]["type"], "ogg")
        self.assertEqual(kwargs["json"]["audio"]["input"]["transcription"]["model"], "stepaudio-2.5-asr")

    def test_detail_request_returns_link_for_visible_course(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "全部課程")
        handler._handle_text_message("85360000000", "詳情1")

        self.assertIn("嬰幼繪本氹氹轉", sent[1][1])
        self.assertIn("https://example.test/course/c1", sent[1][1])

    def test_filter_by_target_keeps_list_focused(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "13歲，想家長課")

        self.assertIn("青少年親子溝通工作坊", sent[0][1])
        self.assertNotIn("嬰幼繪本氹氹轉", sent[0][1])

    def test_agentic_recommendation_infers_child_age_from_sentence(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertIn("為什麼推薦", sent[0][1])
        self.assertIn("https://example.test/course/c1", sent[0][1])
        self.assertNotIn("青少年親子溝通工作坊", sent[0][1])

    def test_parent_pain_point_is_remembered_and_mapped_to_topic(self):
        courses = [
            Course(
                id="c-health",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/health",
            ),
            Course(
                id="c-social",
                name="青少年社交工作坊",
                date="2026/06/01 星期一 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="社會人際關係",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/social",
            ),
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "孩子13歲，最近情緒壓力大")

        self.assertIn("健康情緒與青少年同行", sent[0][1])
        self.assertNotIn("青少年社交工作坊", sent[0][1])
        profile = handler._load_profile("85360000000")
        self.assertEqual(profile["topic"], "身心健康")
        self.assertIn("情緒壓力", profile["pain_points"])
        self.assertIn("情緒壓力", handler._memory.get_conversation("85360000000")["tags"])

    def test_parent_pain_point_can_match_course_outline_not_only_title_or_topic(self):
        courses = [
            Course(
                id="c-outline",
                name="青少年同行工作坊",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="家庭關係",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/outline",
                summary="覺察面對青少年壓力、焦慮與憤怒，學習在親子衝突後真誠對話。",
            ),
            Course(
                id="c-unrelated",
                name="青少年生活講座",
                date="2026/06/01 星期一 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="生活照顧",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/life",
                summary="整理生活作息與日常照顧安排。",
            ),
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "孩子13歲，最近情緒壓力大")

        self.assertIn("青少年同行工作坊", sent[0][1])
        self.assertIn("大綱回應", sent[0][1])
        self.assertNotIn("青少年生活講座", sent[0][1])

    def test_pain_point_without_age_turns_into_short_interview(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "孩子最近做功課很拖拉")

        self.assertIn("小朋友幾多歲", sent[0][1])
        self.assertNotIn("只協助查詢和推薦", sent[0][1])
        profile = handler._load_profile("85360000000")
        self.assertEqual(profile["topic"], "學習與成就感")
        self.assertIn("學習動機", profile["pain_points"])

    def test_age_query_uses_age_specific_source_not_only_open_list(self):
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeAcademyCrawler()})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "青少年，最近情緒壓力大")

        self.assertIn("健康情緒與青少年同行", sent[0][1])
        self.assertIn("https://example.test/course/c2", sent[0][1])
        self.assertNotIn("嬰幼繪本氹氹轉", sent[0][1])

    def test_deepseek_is_used_for_agentic_recommendation_when_configured(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": "我會先推介 1. 嬰幼繪本氹氹轉。🔗 https://example.test/course/c1"
                            }
                        }
                    ]
                }

                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("我會先推介", sent[0][1])
        self.assertIn("https://example.test/course/c1", sent[0][1])
        self.assertNotIn("詳情1", sent[0][1])
        self.assertTrue(post.called)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        user_payload = json.loads(payload["messages"][1]["content"])
        self.assertIn("detail_url", user_payload["候選課程"][0])

    def test_deepseek_reply_with_unapproved_url_falls_back_to_rule_based(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": "推薦不存在的課程，報名連結：https://evil.example/register"
                            }
                        }
                    ]
                }

                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertNotIn("evil.example", sent[0][1])
        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertIn("https://example.test/course/c1", sent[0][1])

    def test_deepseek_reply_repairs_dsedj_registered_symbol_link(self):
        broken_link = (
            "https://portal.dsedj.gov.mo/webdsejspace/addon/allmain/msgfunc/"
            "Msg_funclink_parentacademy_page.jsp?msg_id=713092®status=報名中&langsel=C"
        )
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"crawler": FakeCrawler([
            Course(
                id="713092",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="0-2歲",
                topic="家庭關係",
                target="親子",
                status="報名中",
                detail_url=broken_link,
            )
        ])})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": f"推薦這個課程，報名連結：{broken_link}"
                            }
                        }
                    ]
                }

                handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("?regstatus=", sent[0][1])
        self.assertIn("&msg_id=713092", sent[0][1])
        self.assertIn("%E5%A0%B1%E5%90%8D%E4%B8%AD", sent[0][1])
        self.assertNotIn("®status", sent[0][1])

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
                asyncio.run(api_server.whatsapp_webhook(request, api_server.BackgroundTasks()))
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            if old_secret is None:
                os.environ.pop("WHATSAPP_APP_SECRET", None)
            else:
                os.environ["WHATSAPP_APP_SECRET"] = old_secret

    def test_whatsapp_webhook_requires_meta_secret_configuration(self):
        body = b'{"object":"whatsapp_business_account","entry":[]}'
        request = FakeRequest(body, {})
        old_secret = os.environ.get("WHATSAPP_APP_SECRET")
        old_allow_unsigned = os.environ.get("WHATSAPP_ALLOW_UNSIGNED_WEBHOOK")
        os.environ.pop("WHATSAPP_APP_SECRET", None)
        os.environ.pop("WHATSAPP_ALLOW_UNSIGNED_WEBHOOK", None)
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.whatsapp_webhook(request, api_server.BackgroundTasks()))
            self.assertEqual(ctx.exception.status_code, 500)
        finally:
            if old_secret is None:
                os.environ.pop("WHATSAPP_APP_SECRET", None)
            else:
                os.environ["WHATSAPP_APP_SECRET"] = old_secret
            if old_allow_unsigned is None:
                os.environ.pop("WHATSAPP_ALLOW_UNSIGNED_WEBHOOK", None)
            else:
                os.environ["WHATSAPP_ALLOW_UNSIGNED_WEBHOOK"] = old_allow_unsigned

    def test_whatsapp_webhook_can_temporarily_allow_unsigned_when_explicitly_enabled(self):
        body = b'{"object":"whatsapp_business_account","entry":[]}'
        request = FakeRequest(body, {})
        old_secret = os.environ.get("WHATSAPP_APP_SECRET")
        old_allow_unsigned = os.environ.get("WHATSAPP_ALLOW_UNSIGNED_WEBHOOK")
        os.environ.pop("WHATSAPP_APP_SECRET", None)
        os.environ["WHATSAPP_ALLOW_UNSIGNED_WEBHOOK"] = "true"
        old_get_wa_handler = api_server.get_wa_handler
        api_server.get_wa_handler = lambda: None
        try:
            response = asyncio.run(api_server.whatsapp_webhook(request, api_server.BackgroundTasks()))
            self.assertEqual(response.body, b"ok")
        finally:
            api_server.get_wa_handler = old_get_wa_handler
            if old_secret is None:
                os.environ.pop("WHATSAPP_APP_SECRET", None)
            else:
                os.environ["WHATSAPP_APP_SECRET"] = old_secret
            if old_allow_unsigned is None:
                os.environ.pop("WHATSAPP_ALLOW_UNSIGNED_WEBHOOK", None)
            else:
                os.environ["WHATSAPP_ALLOW_UNSIGNED_WEBHOOK"] = old_allow_unsigned

    def test_whatsapp_webhook_schedules_processing_in_background(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.test-background-1",
                                        "type": "text",
                                        "from": "85360000000",
                                        "text": {"body": "小朋友1歲，想親子活動"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        body = json.dumps(payload).encode("utf-8")
        secret = "app-secret"
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        request = FakeRequest(body, {"x-hub-signature-256": f"sha256={digest}"})

        class FakeWhatsAppHandler:
            def __init__(self):
                self.claimed = False
                self.handled = False
                self.preclaimed = False

            def claim_webhook_messages(self, data):
                self.claimed = data == payload
                return True

            def handle_webhook(self, data, messages_preclaimed=False):
                self.handled = data == payload
                self.preclaimed = messages_preclaimed

        fake_handler = FakeWhatsAppHandler()
        old_get_wa_handler = api_server.get_wa_handler
        old_secret = os.environ.get("WHATSAPP_APP_SECRET")
        os.environ["WHATSAPP_APP_SECRET"] = secret
        api_server.get_wa_handler = lambda: fake_handler
        background_tasks = api_server.BackgroundTasks()
        try:
            response = asyncio.run(api_server.whatsapp_webhook(request, background_tasks))
            self.assertEqual(response.body, b"ok")
            self.assertTrue(fake_handler.claimed)
            self.assertFalse(fake_handler.handled)
            self.assertEqual(len(background_tasks.tasks), 1)

            asyncio.run(background_tasks())
            self.assertTrue(fake_handler.handled)
            self.assertTrue(fake_handler.preclaimed)
        finally:
            api_server.get_wa_handler = old_get_wa_handler
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

    def test_whatsapp_admin_conversation_endpoints(self):
        handler, sent = self.make_handler()
        handler._handle_text_message("85360000000", "課程")

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            listing = asyncio.run(api_server.api_whatsapp_conversations(request=request))
            self.assertEqual(listing["total"], 1)
            self.assertEqual(listing["conversations"][0]["phone"], "85360000000")

            detail = asyncio.run(api_server.api_whatsapp_conversation(
                "85360000000",
                request=request,
            ))
            self.assertEqual(len(detail["messages"]), 2)
            self.assertEqual(detail["conversation"]["status"], "ai")

            updated = asyncio.run(api_server.api_whatsapp_update_conversation(
                "85360000000",
                api_server.ConversationUpdateRequest(
                    tags=["高關注", "情緒壓力"],
                    notes="需要留意青少年情緒",
                    consent_status="allowed",
                    proactive_notes="可以推送青少年情緒課程",
                ),
                request=request,
            ))
            self.assertIn("高關注", updated["conversation"]["tags"])
            self.assertEqual(updated["conversation"]["notes"], "需要留意青少年情緒")
            self.assertEqual(updated["conversation"]["consent_status"], "allowed")
            self.assertEqual(updated["conversation"]["proactive_notes"], "可以推送青少年情緒課程")

            takeover = asyncio.run(api_server.api_whatsapp_takeover(
                "85360000000",
                request=request,
            ))
            self.assertEqual(takeover["conversation"]["status"], "human")

            asyncio.run(api_server.api_whatsapp_admin_message(
                "85360000000",
                api_server.AdminMessageRequest(body="我人工接手看看。"),
                request=request,
            ))
            self.assertIn("我人工接手看看。", sent[-1][1])
            messages = handler._memory.get_messages("85360000000")
            self.assertEqual(messages[-1]["source"], "admin")

            resumed = asyncio.run(api_server.api_whatsapp_resume_ai(
                "85360000000",
                request=request,
            ))
            self.assertEqual(resumed["conversation"]["status"], "ai")
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_whatsapp_admin_profile_update_and_agent_state(self):
        handler, _ = self.make_handler()
        handler._handle_text_message("85360000000", "13歲")

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            before = asyncio.run(api_server.api_whatsapp_conversation(
                "85360000000",
                request=request,
            ))
            self.assertFalse(before["agent_state"]["profile_ready"])
            self.assertIn("concern", before["agent_state"]["missing_fields"])
            self.assertIn("13-18歲", before["profile_options"]["age_groups"])
            self.assertIn("情緒壓力", before["profile_options"]["pain_points"])

            updated = asyncio.run(api_server.api_whatsapp_update_profile(
                "85360000000",
                api_server.ProfileUpdateRequest(
                    age_groups=["13-18歲"],
                    pain_points=["情緒壓力"],
                    target="家長",
                    topic="身心健康",
                    pain_summary="青春期壓力和焦慮",
                ),
                request=request,
            ))

            self.assertTrue(updated["agent_state"]["profile_ready"])
            self.assertEqual(updated["agent_state"]["missing_fields"], [])
            self.assertEqual(updated["profile"]["age_groups"], ["13-18歲"])
            self.assertEqual(updated["profile"]["topic_source"], "admin")
            self.assertIn("情緒壓力", updated["profile"]["pain_points"])
            self.assertIn("青少年", updated["conversation"]["tags"])

            detail = asyncio.run(api_server.api_whatsapp_conversation(
                "85360000000",
                request=request,
            ))
            self.assertTrue(detail["agent_state"]["profile_ready"])
            self.assertEqual(detail["agent_state"]["recommended_action"], "可推薦課程")
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_whatsapp_admin_profile_update_rejects_invalid_values_and_requires_auth(self):
        handler, _ = self.make_handler()

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            with self.assertRaises(HTTPException) as auth_ctx:
                asyncio.run(api_server.api_whatsapp_update_profile(
                    "85360000000",
                    api_server.ProfileUpdateRequest(age_groups=["13-18歲"]),
                    request=self.admin_request(secret="wrong"),
                ))
            self.assertEqual(auth_ctx.exception.status_code, 401)

            with self.assertRaises(HTTPException) as invalid_ctx:
                asyncio.run(api_server.api_whatsapp_update_profile(
                    "85360000000",
                    api_server.ProfileUpdateRequest(
                        age_groups=["99歲"],
                        pain_points=["情緒壓力"],
                        target="家長",
                        topic="身心健康",
                    ),
                    request=self.admin_request(),
                ))
            self.assertEqual(invalid_ctx.exception.status_code, 400)
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_whatsapp_admin_profile_clear_removes_fields(self):
        handler, _ = self.make_handler()

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            asyncio.run(api_server.api_whatsapp_update_profile(
                "85360000000",
                api_server.ProfileUpdateRequest(
                    age_groups=["13-18歲"],
                    pain_points=["情緒壓力"],
                    target="家長",
                    topic="身心健康",
                    pain_summary="青春期壓力",
                ),
                request=request,
            ))
            cleared = asyncio.run(api_server.api_whatsapp_update_profile(
                "85360000000",
                api_server.ProfileUpdateRequest(
                    age_groups=[],
                    pain_points=[],
                    target="",
                    topic="",
                    pain_summary="",
                ),
                request=request,
            ))

            self.assertEqual(cleared["profile"], {})
            self.assertFalse(cleared["agent_state"]["profile_ready"])
            self.assertEqual(cleared["agent_state"]["missing_fields"], ["age_group", "concern"])
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_admin_profile_update_affects_next_recommendation(self):
        courses = [
            Course(
                id="c-health",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/health",
                summary="覺察青少年壓力與焦慮，學習親子衝突後真誠對話。",
            )
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            asyncio.run(api_server.api_whatsapp_update_profile(
                "85360000000",
                api_server.ProfileUpdateRequest(
                    age_groups=["13-18歲"],
                    pain_points=["情緒壓力"],
                    target="家長",
                    topic="身心健康",
                ),
                request=self.admin_request(),
            ))
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
                handler._handle_text_message("85360000000", "幫我揀")

            self.assertIn("健康情緒與青少年同行", sent[-1][1])
            self.assertIn("為什麼推薦", sent[-1][1])
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_whatsapp_admin_conversation_list_filters_and_detail_agent_context(self):
        handler, _ = self.make_handler()
        handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")
        handler._handle_text_message("85360000001", "課程")
        handler._memory.set_conversation_status("85360000000", "human")
        handler._memory.update_conversation("85360000000", consent_status="allowed")
        handler._memory.add_agent_flag("85360000000", "handoff_needed", "需要人工跟進")
        handler._memory.save_proactive_draft(
            "85360000000",
            "健康情緒與青少年同行草稿",
            matches=[{"course": {"id": "c-health", "name": "健康情緒與青少年同行"}}],
            profile={"pain_points": ["情緒壓力"]},
        )

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            human = asyncio.run(api_server.api_whatsapp_conversations(
                request=request,
                status="human",
            ))
            self.assertEqual([c["phone"] for c in human["conversations"]], ["85360000000"])

            flagged = asyncio.run(api_server.api_whatsapp_conversations(
                request=request,
                filter="flagged",
            ))
            self.assertEqual([c["phone"] for c in flagged["conversations"]], ["85360000000"])

            consented = asyncio.run(api_server.api_whatsapp_conversations(
                request=request,
                consent_status="allowed",
            ))
            self.assertEqual([c["phone"] for c in consented["conversations"]], ["85360000000"])

            searched = asyncio.run(api_server.api_whatsapp_conversations(
                request=request,
                search="85360000000",
            ))
            self.assertEqual([c["phone"] for c in searched["conversations"]], ["85360000000"])

            detail = asyncio.run(api_server.api_whatsapp_conversation(
                "85360000000",
                request=request,
            ))
            self.assertEqual(detail["agent_state"]["open_flags_count"], 1)
            self.assertEqual(detail["agent_state"]["draft_count"], 1)
            self.assertEqual(detail["flags"][0]["flag_type"], "handoff_needed")
            self.assertEqual(detail["drafts"][0]["draft_text"], "健康情緒與青少年同行草稿")
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_whatsapp_agent_tasks_queue_prioritizes_next_actions(self):
        handler, _ = self.make_handler()
        handler._handle_text_message("85360000000", "課程")
        handler._handle_text_message("85360000001", "小朋友13歲，最近情緒壓力大")
        handler._memory.update_conversation("85360000001", consent_status="allowed")
        handler._memory.set_conversation_status("85360000001", "human")
        handler._memory.add_agent_flag("85360000001", "handoff_needed", "需要人工判斷")
        handler._memory.save_proactive_draft(
            "85360000001",
            "健康情緒與青少年同行草稿",
            matches=[{"course": {"id": "c-health", "name": "健康情緒與青少年同行"}}],
            profile={"pain_points": ["情緒壓力"]},
        )

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.api_whatsapp_agent_tasks(
                    request=self.admin_request(secret="", cookie="wrong-cookie"),
                ))
            self.assertEqual(ctx.exception.status_code, 401)

            tasks = asyncio.run(api_server.api_whatsapp_agent_tasks(
                request=self.admin_request(),
            ))
            task_types = [task["type"] for task in tasks["tasks"]]

            self.assertGreaterEqual(tasks["total"], 5)
            self.assertEqual(task_types[0], "review_flag")
            self.assertIn("human_takeover", task_types)
            self.assertIn("approve_draft", task_types)
            self.assertIn("ask_age", task_types)
            self.assertIn("ask_concern", task_types)
            self.assertEqual(tasks["briefing"]["by_type"]["review_flag"], 1)
            self.assertEqual(tasks["briefing"]["parents"], 2)

            searched = asyncio.run(api_server.api_whatsapp_agent_tasks(
                request=self.admin_request(),
                search="85360000001",
            ))
            self.assertTrue(all(task["phone"] == "85360000001" for task in searched["tasks"]))
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_admin_qa_feedback_creates_anonymized_learning_sample_and_task(self):
        handler, sent = self.make_handler()
        handler._handle_text_message("85360000000", "我的電話是 +853 6123 4567，搜尋活動")
        ai_message = handler._memory.get_messages("85360000000")[-1]

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            with self.assertRaises(HTTPException) as auth_ctx:
                asyncio.run(api_server.api_whatsapp_add_qa_feedback(
                    "85360000000",
                    api_server.QaFeedbackRequest(
                        message_id=ai_message["id"],
                        rating="bad",
                        issue_type="missed_course",
                        summary="朋友說想搜尋活動，但 AI 沒有給可用的課程方向",
                        expected_behavior="應先訪談年齡和痛點，再推薦課程",
                    ),
                    request=self.admin_request(secret="", cookie="wrong-cookie"),
                ))
            self.assertEqual(auth_ctx.exception.status_code, 401)

            created = asyncio.run(api_server.api_whatsapp_add_qa_feedback(
                "85360000000",
                api_server.QaFeedbackRequest(
                    message_id=ai_message["id"],
                    rating="bad",
                    issue_type="missed_course",
                    summary="朋友說想搜尋活動，但 AI 沒有給可用的課程方向",
                    expected_behavior="應先訪談年齡和痛點，再推薦課程",
                ),
                request=request,
            ))

            self.assertTrue(created["success"])
            feedback = created["feedback"]
            self.assertEqual(feedback["issue_type"], "missed_course")
            self.assertEqual(feedback["rating"], "bad")
            sample = feedback["anonymized_sample"]
            self.assertEqual(sample["source"], "admin_qa_feedback")
            self.assertIn("搜尋活動", sample["parent_message"])
            self.assertNotIn("6123", json.dumps(sample, ensure_ascii=False))
            self.assertIn("[phone]", sample["parent_message"])

            detail = asyncio.run(api_server.api_whatsapp_conversation(
                "85360000000",
                request=request,
            ))
            self.assertEqual(detail["agent_state"]["qa_feedback_count"], 1)
            self.assertEqual(detail["qa_feedback"][0]["issue_type"], "missed_course")

            tasks = asyncio.run(api_server.api_whatsapp_agent_tasks(request=request))
            task_types = [task["type"] for task in tasks["tasks"]]
            self.assertEqual(task_types[0], "review_qa_feedback")

            closed = asyncio.run(api_server.api_whatsapp_update_qa_feedback_status(
                feedback["id"],
                api_server.QaFeedbackStatusRequest(status="converted"),
                request=request,
            ))
            self.assertEqual(closed["feedback"]["status"], "converted")
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_whatsapp_admin_dashboard_contains_agent_inbox_controls(self):
        old_admin = os.environ.get("ADMIN_SECRET")
        os.environ["ADMIN_SECRET"] = "admin-secret"
        try:
            response = asyncio.run(api_server.admin_dashboard(
                request=self.admin_request(secret=""),
            ))
            login_html = response.body.decode()
            self.assertIn("WhatsApp 家長學堂接手台", login_html)

            valid_cookie = api_server.make_admin_session_token("admin-secret")
            response = asyncio.run(api_server.admin_dashboard(
                request=self.admin_request(secret="", cookie=valid_cookie),
            ))
            html = response.body.decode()
            self.assertIn("filterStatus", html)
            self.assertIn("結構化 Profile", html)
            self.assertIn("profileAgeGroups", html)
            self.assertIn("saveProfile", html)
            self.assertIn("conversationCount", html)
            self.assertIn("filterSegments", html)
            self.assertIn("chatAvatar", html)
            self.assertIn("profileSummary", html)
            self.assertIn("loadAgentTasks", html)
            self.assertIn("agentTasks", html)
            self.assertIn("朋友測試 QA", html)
            self.assertIn("submitQaFeedback", html)
            self.assertIn("loadQaFeedback", html)
            self.assertIn("人工接手", html)
            self.assertIn("不確定隊列", html)
            self.assertIn("主動匹配草稿", html)
        finally:
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_whatsapp_admin_rejects_wrong_cookie_and_accepts_valid_cookie(self):
        handler, _ = self.make_handler()
        handler._handle_text_message("85360000000", "課程")

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.api_whatsapp_conversations(
                    request=self.admin_request(secret="", cookie="wrong-cookie"),
                ))
            self.assertEqual(ctx.exception.status_code, 401)

            valid_cookie = api_server.make_admin_session_token("admin-secret")
            listing = asyncio.run(api_server.api_whatsapp_conversations(
                request=self.admin_request(secret="", cookie=valid_cookie),
            ))
            self.assertEqual(listing["total"], 1)
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_admin_flags_and_proactive_matches(self):
        courses = [
            Course(
                id="c-health",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/health",
            )
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        handler._send_text = lambda to, text: True
        handler._handle_text_message("85360000000", "孩子13歲，最近情緒壓力大")
        flag_id = handler._memory.add_agent_flag("85360000000", "handoff_needed", "想人工跟進")

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            flags = asyncio.run(api_server.api_whatsapp_flags(request=request))
            self.assertGreaterEqual(flags["total"], 1)

            resolved = asyncio.run(api_server.api_whatsapp_resolve_flag(
                flag_id,
                request=request,
            ))
            self.assertTrue(resolved["success"])

            matches = asyncio.run(api_server.api_whatsapp_proactive_matches(request=request))
            self.assertEqual(matches["total"], 1)
            self.assertEqual(matches["matches"][0]["matches"][0]["course"]["name"], "健康情緒與青少年同行")
            self.assertIn("孩子年齡吻合", matches["matches"][0]["matches"][0]["reasons"])
            self.assertIn("健康情緒與青少年同行", matches["matches"][0]["draft_text"])
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_proactive_matches_can_filter_to_consented_parents(self):
        courses = [
            Course(
                id="c-health",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/health",
                summary="覺察青少年壓力與焦慮，學習親子衝突後真誠對話。",
                registration_url="https://example.test/register/health",
            )
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        handler._send_text = lambda to, text: True
        handler._handle_text_message("85360000000", "孩子13歲，最近情緒壓力大")
        handler._handle_text_message("85360000001", "孩子13歲，最近情緒壓力大")
        handler._memory.update_conversation("85360000000", consent_status="allowed")

        matches = handler.get_proactive_matches(allowed_only=True)

        self.assertEqual([m["phone"] for m in matches], ["85360000000"])
        self.assertIn("健康情緒與青少年同行", matches[0]["draft_text"])
        self.assertIn("https://example.test/register/health", matches[0]["draft_text"])

    def test_memory_store_persists_proactive_draft_queue(self):
        store = WhatsAppMemoryStore()
        match_snapshot = [{
            "score": 5,
            "reasons": ["孩子年齡吻合", "痛點吻合"],
            "course": {
                "id": "c-health",
                "name": "健康情緒與青少年同行",
                "reply_url": "https://example.test/register/health",
            },
        }]

        draft = store.save_proactive_draft(
            "85360000000",
            "AI 原始草稿",
            matches=match_snapshot,
            profile={"pain_points": ["情緒壓力"]},
        )
        duplicate = store.save_proactive_draft(
            "85360000000",
            "AI 原始草稿",
            matches=match_snapshot,
            profile={"pain_points": ["情緒壓力"]},
        )
        self.assertEqual(duplicate["id"], draft["id"])

        edited = store.update_proactive_draft_body(draft["id"], "人工修改後草稿")
        reopened = WhatsAppMemoryStore(str(store.db_path))
        persisted = reopened.get_proactive_draft(edited["id"])

        self.assertEqual(persisted["status"], "draft")
        self.assertEqual(persisted["draft_text"], "人工修改後草稿")
        self.assertEqual(persisted["original_text"], "AI 原始草稿")
        self.assertEqual(persisted["profile"]["pain_points"], ["情緒壓力"])
        self.assertEqual(persisted["matches"][0]["course"]["id"], "c-health")

        skipped = reopened.mark_proactive_draft(persisted["id"], "skipped")
        self.assertEqual(skipped["status"], "skipped")
        self.assertTrue(skipped["skipped_at"])

    def test_proactive_draft_claim_is_single_use(self):
        store = WhatsAppMemoryStore()
        draft = store.save_proactive_draft(
            "85360000000",
            "AI 原始草稿",
            matches=[{"course": {"id": "c-health"}}],
            profile={"pain_points": ["情緒壓力"]},
        )

        claimed = store.claim_proactive_draft_for_send(
            draft["id"],
            "第一次送出的內容",
        )
        second_claim = store.claim_proactive_draft_for_send(
            draft["id"],
            "第二次送出的內容",
        )
        sent = store.mark_proactive_draft(
            draft["id"],
            "sent",
            sent_message_type="text",
            sent_text="第一次送出的內容",
            only_status="sending",
        )
        skipped = store.mark_proactive_draft(
            draft["id"],
            "skipped",
            only_status="draft",
        )

        self.assertEqual(claimed["status"], "sending")
        self.assertEqual(claimed["draft_text"], "第一次送出的內容")
        self.assertEqual(second_claim, {})
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(sent["sent_text"], "第一次送出的內容")
        self.assertEqual(skipped, {})
        self.assertEqual(store.get_proactive_draft(draft["id"])["status"], "sent")

    def test_proactive_match_generation_persists_idempotent_drafts(self):
        courses = [
            Course(
                id="c-health",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/health",
                summary="覺察青少年壓力與焦慮，學習親子衝突後真誠對話。",
                registration_url="https://example.test/register/health",
            )
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        handler._send_text = lambda to, text: True
        handler._handle_text_message("85360000000", "孩子13歲，最近情緒壓力大")

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            first = asyncio.run(api_server.api_whatsapp_generate_proactive_drafts(
                api_server.ProactiveDraftGenerateRequest(),
                request=request,
            ))
            second = asyncio.run(api_server.api_whatsapp_generate_proactive_drafts(
                api_server.ProactiveDraftGenerateRequest(),
                request=request,
            ))
            queue = asyncio.run(api_server.api_whatsapp_proactive_drafts(
                request=request,
            ))

            self.assertEqual(first["total"], 1)
            self.assertEqual(second["total"], 1)
            self.assertEqual(first["drafts"][0]["id"], second["drafts"][0]["id"])
            self.assertEqual(queue["total"], 1)
            self.assertIn("健康情緒與青少年同行", queue["drafts"][0]["draft_text"])
            self.assertEqual(queue["drafts"][0]["status"], "draft")
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_proactive_draft_list_filters_search_and_consent(self):
        handler, _sent = self.make_handler()
        handler._memory.update_conversation(
            "85360000000",
            tags=["情緒壓力"],
            consent_status="allowed",
            proactive_notes="偏好青少年情緒課程",
        )
        handler._memory.update_conversation(
            "85361111111",
            tags=["親子溝通"],
            consent_status="unknown",
        )
        first = handler._memory.save_proactive_draft(
            "85360000000",
            "健康情緒與青少年同行草稿",
            matches=[{"course": {"id": "c-health", "name": "健康情緒與青少年同行"}}],
            profile={"pain_points": ["情緒壓力"]},
        )
        second = handler._memory.save_proactive_draft(
            "85361111111",
            "親子溝通工作坊草稿",
            matches=[{"course": {"id": "c-talk", "name": "親子溝通工作坊"}}],
            profile={"pain_points": ["親子溝通"]},
        )
        handler._memory.mark_proactive_draft(second["id"], "skipped")

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            allowed = asyncio.run(api_server.api_whatsapp_proactive_drafts(
                request=request,
                status="draft",
                search="健康",
                consent_status="allowed",
            ))
            history = asyncio.run(api_server.api_whatsapp_proactive_drafts(
                request=request,
                status="all",
                search="親子溝通",
            ))

            self.assertEqual([d["id"] for d in allowed["drafts"]], [first["id"]])
            self.assertEqual([d["id"] for d in history["drafts"]], [second["id"]])
            self.assertEqual(history["drafts"][0]["status"], "skipped")
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_privacy_prune_previews_and_deletes_old_operational_history(self):
        store = WhatsAppMemoryStore()
        old_time = (datetime.now() - timedelta(days=120)).isoformat()
        store.record_message("85360000000", "inbound", "parent", "舊訊息")
        store.save_llm_cached_response("cache-key", "舊 LLM 回覆")
        store.claim_message("wamid.old", "85360000000")
        flag_id = store.add_agent_flag("85360000000", "handoff_needed", "舊 flag")
        store.resolve_agent_flag(flag_id)
        draft = store.save_proactive_draft(
            "85360000000",
            "舊推送紀錄",
            matches=[{"course": {"id": "c-health"}}],
            profile={"pain_points": ["情緒壓力"]},
        )
        store.mark_proactive_draft(draft["id"], "skipped")
        with sqlite3.connect(str(store.db_path)) as conn:
            for table in (
                "whatsapp_messages",
                "llm_response_cache",
                "processed_whatsapp_messages",
                "whatsapp_agent_flags",
                "whatsapp_proactive_drafts",
            ):
                column = "updated_at" if table == "whatsapp_proactive_drafts" else "created_at"
                conn.execute(f"UPDATE {table} SET {column} = ?", (old_time,))
            conn.commit()

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = None
        api_server.wa_memory = store
        try:
            preview = asyncio.run(api_server.api_whatsapp_privacy_prune(
                api_server.PrivacyPruneRequest(older_than_days=90, dry_run=True),
                request=self.admin_request(),
            ))
            self.assertTrue(preview["dry_run"])
            self.assertEqual(preview["counts"]["messages"], 1)
            self.assertEqual(preview["counts"]["llm_cache"], 1)
            self.assertEqual(preview["counts"]["processed_message_ids"], 1)
            self.assertEqual(preview["counts"]["resolved_flags"], 1)
            self.assertEqual(preview["counts"]["closed_proactive_drafts"], 1)
            self.assertEqual(len(store.get_messages("85360000000")), 1)

            result = asyncio.run(api_server.api_whatsapp_privacy_prune(
                api_server.PrivacyPruneRequest(older_than_days=90, dry_run=False),
                request=self.admin_request(),
            ))
            self.assertFalse(result["dry_run"])
            self.assertEqual(store.get_messages("85360000000"), [])
            self.assertEqual(store.get_llm_cached_response("cache-key"), "")
            self.assertEqual(store.list_agent_flags(unresolved_only=False), [])
            self.assertEqual(store.list_proactive_drafts(status="all"), [])
            self.assertTrue(store.get_conversation("85360000000"))
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_send_proactive_draft_requires_consent_and_records_history(self):
        handler, sent = self.make_handler()
        draft = handler._memory.save_proactive_draft(
            "85360000000",
            "AI 原始草稿",
            matches=[{"course": {"id": "c-health", "name": "健康情緒與青少年同行"}}],
            profile={"pain_points": ["情緒壓力"]},
        )
        handler._memory.record_message("85360000000", "inbound", "parent", "孩子13歲")

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            request = self.admin_request()
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.api_whatsapp_send_proactive_draft(
                    draft["id"],
                    api_server.ProactiveSendRequest(body="人工最後發送內容"),
                    request=request,
                ))
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(sent, [])
            self.assertEqual(
                handler._memory.get_proactive_draft(draft["id"])["status"],
                "draft",
            )

            handler._memory.update_conversation("85360000000", consent_status="allowed")
            result = asyncio.run(api_server.api_whatsapp_send_proactive_draft(
                draft["id"],
                api_server.ProactiveSendRequest(body="人工最後發送內容"),
                request=request,
            ))

            self.assertTrue(result["success"])
            self.assertEqual(result["message_type"], "text")
            self.assertIn("人工最後發送內容", sent[-1][1])
            updated = handler._memory.get_proactive_draft(draft["id"])
            self.assertEqual(updated["status"], "sent")
            self.assertEqual(updated["original_text"], "AI 原始草稿")
            self.assertEqual(updated["draft_text"], "人工最後發送內容")
            self.assertEqual(updated["sent_text"], "人工最後發送內容")
            self.assertEqual(updated["sent_message_type"], "text")
            self.assertTrue(updated["sent_at"])
            self.assertEqual(
                handler._memory.get_messages("85360000000")[-1]["source"],
                "admin",
            )

            with self.assertRaises(HTTPException) as skip_ctx:
                asyncio.run(api_server.api_whatsapp_skip_proactive_draft(
                    draft["id"],
                    request=request,
                ))
            self.assertEqual(skip_ctx.exception.status_code, 409)

            with self.assertRaises(HTTPException) as edit_ctx:
                asyncio.run(api_server.api_whatsapp_update_proactive_draft(
                    draft["id"],
                    api_server.ProactiveDraftUpdateRequest(body="不應覆寫已發送紀錄"),
                    request=request,
                ))
            self.assertEqual(edit_ctx.exception.status_code, 404)
            self.assertEqual(
                handler._memory.get_proactive_draft(draft["id"])["status"],
                "sent",
            )
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_proactive_send_requires_consent_then_sends_draft(self):
        handler, sent = self.make_handler()
        handler._handle_text_message("85360000000", "孩子13歲，最近情緒壓力大")

        old_admin = os.environ.get("ADMIN_SECRET")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.api_whatsapp_send_proactive_match(
                    "85360000000",
                    api_server.ProactiveSendRequest(body="這是主動推送草稿"),
                    request=self.admin_request(),
                ))
            self.assertEqual(ctx.exception.status_code, 409)

            handler._memory.update_conversation("85360000000", consent_status="allowed")
            result = asyncio.run(api_server.api_whatsapp_send_proactive_match(
                "85360000000",
                api_server.ProactiveSendRequest(body="這是主動推送草稿"),
                request=self.admin_request(),
            ))

            self.assertTrue(result["success"])
            self.assertIn("這是主動推送草稿", sent[-1][1])
            messages = handler._memory.get_messages("85360000000")
            self.assertEqual(messages[-1]["source"], "admin")
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin

    def test_template_message_payload_uses_cloud_api_template(self):
        old_phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
        old_access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
        os.environ["WHATSAPP_PHONE_NUMBER_ID"] = "phone-id"
        os.environ["WHATSAPP_ACCESS_TOKEN"] = "access-token"
        try:
            handler = WhatsAppHandler()
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200

                sent = handler.send_template_message(
                    to="85360000000",
                    template_name="parent_course_reminder",
                    language_code="zh_HK",
                    body_parameters=["健康情緒與青少年同行", "https://example.test/register"],
                    transcript_body="template transcript",
                )

            self.assertTrue(sent)
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["type"], "template")
            self.assertEqual(payload["template"]["name"], "parent_course_reminder")
            self.assertEqual(payload["template"]["language"]["code"], "zh_HK")
            params = payload["template"]["components"][0]["parameters"]
            self.assertEqual([p["text"] for p in params], [
                "健康情緒與青少年同行",
                "https://example.test/register",
            ])
            messages = handler._memory.get_messages("85360000000")
            self.assertEqual(messages[-1]["body"], "template transcript")
            self.assertEqual(messages[-1]["meta"]["message_type"], "template")
        finally:
            if old_phone_id is None:
                os.environ.pop("WHATSAPP_PHONE_NUMBER_ID", None)
            else:
                os.environ["WHATSAPP_PHONE_NUMBER_ID"] = old_phone_id
            if old_access_token is None:
                os.environ.pop("WHATSAPP_ACCESS_TOKEN", None)
            else:
                os.environ["WHATSAPP_ACCESS_TOKEN"] = old_access_token

    def test_proactive_send_requires_configured_template_when_window_expired(self):
        handler, sent = self.make_handler()
        handler._memory.update_conversation("85360000000", consent_status="allowed")
        handler._memory.record_message("85360000000", "inbound", "parent", "孩子13歲")
        old_time = (datetime.now() - timedelta(hours=25)).isoformat()
        with sqlite3.connect(str(handler._memory.db_path)) as conn:
            conn.execute(
                "UPDATE whatsapp_messages SET created_at = ? WHERE phone = ? AND direction = 'inbound'",
                (old_time, "85360000000"),
            )
            conn.commit()

        old_admin = os.environ.get("ADMIN_SECRET")
        old_template = os.environ.get("WHATSAPP_PROACTIVE_TEMPLATE_NAME")
        old_language = os.environ.get("WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE")
        old_handler = api_server.wa_handler
        old_memory = api_server.wa_memory
        os.environ["ADMIN_SECRET"] = "admin-secret"
        os.environ.pop("WHATSAPP_PROACTIVE_TEMPLATE_NAME", None)
        api_server.wa_handler = handler
        api_server.wa_memory = handler._memory
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(api_server.api_whatsapp_send_proactive_match(
                    "85360000000",
                    api_server.ProactiveSendRequest(body="主動推送草稿"),
                    request=self.admin_request(),
                ))
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(sent, [])

            os.environ["WHATSAPP_PROACTIVE_TEMPLATE_NAME"] = "parent_course_reminder"
            os.environ["WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE"] = "zh_HK"
            handler.send_template_message = (
                lambda to, template_name, language_code, body_parameters, transcript_body:
                sent.append((to, template_name, language_code, body_parameters, transcript_body)) or True
            )

            result = asyncio.run(api_server.api_whatsapp_send_proactive_match(
                "85360000000",
                api_server.ProactiveSendRequest(body="主動推送草稿"),
                request=self.admin_request(),
            ))

            self.assertTrue(result["success"])
            self.assertEqual(sent[-1][1], "parent_course_reminder")
            self.assertEqual(sent[-1][2], "zh_HK")
            self.assertEqual(sent[-1][3], ["主動推送草稿"])
        finally:
            api_server.wa_handler = old_handler
            api_server.wa_memory = old_memory
            if old_admin is None:
                os.environ.pop("ADMIN_SECRET", None)
            else:
                os.environ["ADMIN_SECRET"] = old_admin
            if old_template is None:
                os.environ.pop("WHATSAPP_PROACTIVE_TEMPLATE_NAME", None)
            else:
                os.environ["WHATSAPP_PROACTIVE_TEMPLATE_NAME"] = old_template
            if old_language is None:
                os.environ.pop("WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE", None)
            else:
                os.environ["WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE"] = old_language

    def test_root_reports_whatsapp_first_version(self):
        result = asyncio.run(api_server.root())

        self.assertIn("家長學堂 WhatsApp 課程小助手", result)
        self.assertIn("https://wa.me/8614714949607", result)
        self.assertIn("whatsapp://send?phone=8614714949607", result)
        self.assertIn("parent-school-bot.zeabur.app/whatsapp", result)
        self.assertIn("MicroMessenger", result)

    def test_whatsapp_share_page_handles_wechat_handoff(self):
        result = asyncio.run(api_server.whatsapp_share_page())

        self.assertIn("WeChat 內建瀏覽器", result)
        self.assertIn("用瀏覽器打開", result)
        self.assertIn("intent://send?phone=8614714949607", result)

    def test_root_head_allows_website_validators(self):
        result = asyncio.run(api_server.root_head())

        self.assertEqual(result, "")


class FakeRequest:
    def __init__(self, body: bytes, headers: dict, cookies: dict | None = None):
        self._body = body
        self.headers = headers
        self.cookies = cookies or {}

    async def body(self):
        return self._body

    async def json(self):
        return json.loads(self._body.decode("utf-8"))


class FakePushBot:
    def run_push(self):
        return {"success": True, "courses": 0, "users": 0, "error": ""}


if __name__ == "__main__":
    unittest.main()
