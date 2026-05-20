import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

from classifier import CourseClassifier
from scraper import Course, CourseScraper


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
