"""課程分類器模組 - 按年齡層分類和篩選課程"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from scraper import Course, AGE_GROUP_LABELS

logger = logging.getLogger(__name__)


class CourseClassifier:
    """課程分類器"""

    def by_age_group(self, courses: List[Course]) -> Dict[str, List[Course]]:
        """
        按年齡層分組課程

        Args:
            courses: 課程列表

        Returns:
            {年齡層: [課程列表]}
        """
        result: Dict[str, List[Course]] = {age: [] for age in AGE_GROUP_LABELS.keys()}
        result["未知"] = []

        for course in courses:
            if course.age_group and course.age_group in result:
                result[course.age_group].append(course)
            else:
                result["未知"].append(course)

        # 過濾空分組
        return {k: v for k, v in result.items() if v}

    def filter_by_week(self, courses: List[Course], week_offset: int = 0) -> List[Course]:
        """
        篩選指定週的課程

        Args:
            courses: 課程列表
            week_offset: 0=本週, 1=下週, -1=上週

        Returns:
            符合條件的課程列表
        """
        now = datetime.now()
        target_week = now.isocalendar()[1] + week_offset
        target_year = now.year

        # 處理跨年情況
        if target_week > 52:
            target_week = target_week - 52
            target_year += 1
        elif target_week < 1:
            target_week = 52 + target_week
            target_year -= 1

        filtered = []
        for course in courses:
            if course.date_parsed:
                cw = course.date_parsed.isocalendar()
                if cw[0] == target_year and cw[1] == target_week:
                    filtered.append(course)

        return filtered

    def filter_upcoming(self, courses: List[Course], days: int = 7) -> List[Course]:
        """
        篩選未來 N 天內的課程

        Args:
            courses: 課程列表
            days: 天數

        Returns:
            未來 N 天內的課程
        """
        now = datetime.now()
        future = now + timedelta(days=days)

        filtered = []
        for course in courses:
            if course.date_parsed:
                if now <= course.date_parsed <= future:
                    filtered.append(course)
            elif not course.date_parsed:
                # 無日期的課程單獨標記
                pass

        return filtered

    def filter_by_age_group(self, courses: List[Course], age_group: str) -> List[Course]:
        """篩選指定年齡層的課程"""
        return [c for c in courses if c.age_group == age_group]

    def filter_by_status(self, courses: List[Course], status: str) -> List[Course]:
        """篩選指定狀態的課程"""
        return [c for c in courses if c.status == status]

    def get_weekly_digest(
        self, courses_by_age: Dict[str, List[Course]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        生成每週課程摘要

        Args:
            courses_by_age: {年齡層: [課程列表]}

        Returns:
            {
                "年齡層": {
                    "label": "顯示名稱",
                    "this_week": [...],
                    "next_week": [...],
                    "upcoming_7d": [...],
                    "no_date": [...],
                    "total": N
                }
            }
        """
        digest = {}

        for age_group, courses in courses_by_age.items():
            this_week = self.filter_by_week(courses, week_offset=0)
            next_week = self.filter_by_week(courses, week_offset=1)
            upcoming_7d = self.filter_upcoming(courses, days=7)
            no_date = [c for c in courses if not c.date_parsed]

            label = AGE_GROUP_LABELS.get(age_group, age_group)

            digest[age_group] = {
                "label": label,
                "this_week": this_week,
                "next_week": next_week,
                "upcoming_7d": upcoming_7d,
                "no_date": no_date,
                "total": len(courses),
            }

        return digest

    def summarize(self, courses: List[Course]) -> Dict[str, Any]:
        """生成課程統計摘要"""
        by_age = self.by_age_group(courses)
        by_topic: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        date_count = 0
        no_date_count = 0

        for c in courses:
            by_topic[c.topic] = by_topic.get(c.topic, 0) + 1
            by_status[c.status] = by_status.get(c.status, 0) + 1
            if c.date_parsed:
                date_count += 1
            else:
                no_date_count += 1

        return {
            "total": len(courses),
            "by_age_group": {k: len(v) for k, v in by_age.items()},
            "by_topic": by_topic,
            "by_status": by_status,
            "with_date": date_count,
            "without_date": no_date_count,
        }
