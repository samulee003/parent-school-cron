import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

from classifier import CourseClassifier
from scraper import Course, CourseScraper, normalize_course_detail_url


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class ScraperClassifierTests(unittest.TestCase):
    def test_fetch_page_sends_zero_based_pageis(self):
        scraper = CourseScraper()
        captured = {}

        def fake_post(url, data, timeout):
            captured["url"] = url
            captured["data"] = data
            captured["timeout"] = timeout
            return FakeResponse("<html><body><p>(3/8)</p></body></html>")

        scraper.session.post = fake_post

        courses, total_pages = scraper._fetch_page(page=3, status="")

        self.assertEqual(courses, [])
        self.assertEqual(total_pages, 8)
        self.assertEqual(captured["data"]["pageis"], "2")

    def test_course_detail_url_is_safe_for_whatsapp(self):
        scraper = CourseScraper()

        def fake_post(url, data, timeout):
            return FakeResponse(
                """
                <html><body>
                  <p>(1/1)</p>
                  <table id="result">
                    <tr><th>ID</th><th>名稱</th><th>日期</th><th>標籤</th><th>狀態</th></tr>
                    <tr>
                      <td title="713092"></td>
                      <td>
                        <a class="act_title" onclick="changeIframeURL('/webdsejspace/addon/allmain/msgfunc/Msg_funclink_parentacademy_page.jsp?msg_id=713092&regstatus=報名中&langsel=C')">健康情緒與青少年同行</a>
                      </td>
                      <td>2026/05/31 星期日 10:30-12:00</td>
                      <td>
                        <span class="badge">13-18歲</span>
                        <span class="badge">身心健康</span>
                        <span class="badge">家長</span>
                      </td>
                      <td data-status="報名中"></td>
                    </tr>
                  </table>
                </body></html>
                """
            )

        scraper.session.post = fake_post

        courses, _ = scraper._fetch_page(page=1, status="報名中")

        self.assertEqual(len(courses), 1)
        self.assertIn("?regstatus=", courses[0].detail_url)
        self.assertIn("&msg_id=713092", courses[0].detail_url)
        self.assertIn("%E5%A0%B1%E5%90%8D%E4%B8%AD", courses[0].detail_url)
        self.assertNotIn("&regstatus", courses[0].detail_url)
        self.assertNotIn("®", courses[0].detail_url)

    def test_normalize_repairs_registered_symbol_link(self):
        broken = (
            "https://portal.dsedj.gov.mo/webdsejspace/addon/allmain/msgfunc/"
            "Msg_funclink_parentacademy_page.jsp?msg_id=713092®status=報名中&langsel=C"
        )

        fixed = normalize_course_detail_url(broken)

        self.assertIn("?regstatus=", fixed)
        self.assertIn("&msg_id=713092", fixed)
        self.assertNotIn("®status", fixed)

    def test_fetch_courses_can_enrich_detail_outline_and_registration_link(self):
        scraper = CourseScraper()

        def fake_post(url, data, timeout):
            return FakeResponse(
                """
                <html><body>
                  <p>(1/1)</p>
                  <table id="result">
                    <tr><th>ID</th><th>名稱</th><th>日期</th><th>標籤</th><th>狀態</th></tr>
                    <tr>
                      <td title="713092"></td>
                      <td>
                        <a class="act_title" onclick="changeIframeURL('/webdsejspace/addon/allmain/msgfunc/Msg_funclink_parentacademy_page.jsp?msg_id=713092&regstatus=報名中&langsel=C')">健康情緒與青少年同行</a>
                      </td>
                      <td>2026/05/31 星期日 10:30-12:00</td>
                      <td>
                        <span class="badge">13-18歲</span>
                        <span class="badge">身心健康</span>
                        <span class="badge">家長</span>
                      </td>
                      <td data-status="報名中"></td>
                    </tr>
                  </table>
                </body></html>
                """
            )

        def fake_get(url, params, timeout):
            self.assertIn("Msg_funcmain_parentacademy_page.jsp", url)
            self.assertEqual(params["msg_id"], "713092")
            return FakeResponse(
                """
                <div class="main">
                  <h1>家長學堂——健康情緒與青少年同行</h1>
                  <div>活動日期：2026/5/31 星期日 10:30-12:00</div>
                  <ol>
                    <li>覺察面對青少年疏離、叛逆時的無力感與憤怒。</li>
                    <li>學習在親子衝突後進行真誠對話。</li>
                  </ol>
                  <p>報名連結：<a href="https://portal.dsedj.gov.mo/actregspace/gensystem/actreg/actreg/ActReg_view_page_2.jsp?search_id=36975&langsel=C">報名</a></p>
                </div>
                """
            )

        scraper.session.post = fake_post
        scraper.session.get = fake_get

        courses = scraper.fetch_courses(
            status="",
            include_details=True,
            delay=0,
            detail_delay=0,
        )

        self.assertEqual(len(courses), 1)
        self.assertIn("親子衝突", courses[0].summary)
        self.assertIn("actregspace", courses[0].registration_url)

    def test_detail_parser_reads_direct_body_text_without_metadata(self):
        scraper = CourseScraper()

        detail = scraper._parse_course_detail_html(
            """
            <div class="main">
              <h1>公共圖書館—嬰幼繪本</h1>
              <div class="act_course_tag"><span>報名中</span><span>0-2歲</span></div>
              <div>活動日期：2026/06/20 星期六 15:00-16:00</div>
              描繪孩子成長過程中的里程碑，適合親子共讀。
              <p>報名連結：<a href="https://activity.mo.gov.mo/activity-h5/activity-detail?activityId=1">報名</a></p>
            </div>
            """
        )

        self.assertEqual(detail["summary"], "描繪孩子成長過程中的里程碑，適合親子共讀。")
        self.assertNotIn("活動日期", detail["summary"])
        self.assertIn("activity.mo.gov.mo", detail["registration_url"])

    def test_classifier_groups_multi_age_course_into_every_matching_age(self):
        course = Course(
            id="c1",
            name="多階段親子課",
            date="2026/06/20 星期六 15:00-16:00",
            date_parsed=None,
            age_group="3-6歲",
            topic="家庭關係",
            target="親子",
            status="報名中",
            detail_url="https://example.test/course/c1",
            age_groups=["3-6歲", "7-12歲", "13-18歲"],
        )

        by_age = CourseClassifier().by_age_group([course])

        self.assertIn(course, by_age["3-6歲"])
        self.assertIn(course, by_age["7-12歲"])
        self.assertIn(course, by_age["13-18歲"])

    def test_filter_by_age_group_uses_multi_age_membership(self):
        course = Course(
            id="c1",
            name="青少年也適用的課",
            date="2026/06/20 星期六 15:00-16:00",
            date_parsed=None,
            age_group="3-6歲",
            topic="家庭關係",
            target="親子",
            status="報名中",
            detail_url="https://example.test/course/c1",
            age_groups=["3-6歲", "13-18歲"],
        )

        result = CourseClassifier().filter_by_age_group([course], "13-18歲")

        self.assertEqual(result, [course])


if __name__ == "__main__":
    unittest.main()
