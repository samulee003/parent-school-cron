"""課程爬蟲模組 - 從家長學堂 API 抓取課程數據"""

import re
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 年齡層標籤映射
AGE_GROUP_LABELS = {
    "0-2歲": "嬰幼兒期",
    "3-6歲": "幼兒期",
    "7-12歲": "兒童期",
    "13-18歲": "青少年期",
}

# 主題列表
TOPICS = [
    "能力發展", "家庭關係", "身心健康", "生活照顧",
    "環境適應", "學習與成就感", "社會人際關係", "群體歸屬感", "科技素養"
]

# 對象列表
TARGETS = ["家長", "親子"]


def normalize_course_detail_url(url: str) -> str:
    """Return a WhatsApp-safe DSEDJ course URL.

    DSEDJ detail links contain a query parameter named ``regstatus``. In some
    renderers and LLM replies, ``&regstatus`` is treated like the HTML entity
    ``&reg`` and becomes ``®status``. Put ``regstatus`` first and URL-encode
    the Chinese value so the link stays clickable in WhatsApp.
    """
    if not url:
        return ""

    cleaned = str(url).strip().replace("&amp;", "&")
    cleaned = re.sub(
        r"(?:\u00ae\ufe0f?|%C2%AE(?:%EF%B8%8F)?)status",
        "&regstatus",
        cleaned,
        flags=re.IGNORECASE,
    )

    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return cleaned

    if not parts.query:
        return cleaned

    params = parse_qsl(parts.query, keep_blank_values=True)
    if not params:
        return cleaned

    priority = {"regstatus": 0, "msg_id": 1, "langsel": 2}
    params.sort(key=lambda item: (priority.get(item[0], 99), item[0]))
    query = urlencode(params, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


@dataclass
class Course:
    """課程數據模型"""
    id: str
    name: str
    date: str
    date_parsed: Optional[datetime]
    age_group: str
    topic: str
    target: str
    status: str
    detail_url: str
    week_number: int = 0
    age_groups: List[str] = field(default_factory=list)


class CourseScraper:
    """課程爬蟲"""

    def __init__(self, base_url: str = "https://portal.dsedj.gov.mo"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": f"{base_url}/webdsejspace/site/parent_academy/course.jsp",
            "X-Requested-With": "XMLHttpRequest",
        })

    def fetch_courses(
        self,
        age_group: str = "",
        topic: str = "",
        target: str = "",
        status: str = "報名中",
        keyword: str = "",
        max_retries: int = 3,
        delay: float = 1.0,
    ) -> List[Course]:
        """
        抓取課程列表

        Args:
            age_group: 年齡層過濾 (0-2歲/3-6歲/7-12歲/13-18歲)
            topic: 主題過濾
            target: 對象過濾 (家長/親子)
            status: 報名狀態 (報名中/待報名/已完成報名)
            keyword: 關鍵字搜索
            max_retries: 最大重試次數
            delay: 請求間隔秒數

        Returns:
            課程列表
        """
        all_courses = []
        page = 1
        total_pages = 1

        while page <= total_pages:
            retries = 0
            while retries < max_retries:
                try:
                    courses, total_pages = self._fetch_page(
                        page=page,
                        age_group=age_group,
                        topic=topic,
                        target=target,
                        status=status,
                        keyword=keyword,
                    )
                    all_courses.extend(courses)
                    logger.info(f"第 {page}/{total_pages} 頁抓取完成，獲得 {len(courses)} 條課程")
                    break
                except Exception as e:
                    retries += 1
                    wait_time = delay * (2 ** retries)  # 指數退避
                    logger.warning(f"第 {page} 頁抓取失敗 (重試 {retries}/{max_retries}): {e}")
                    if retries < max_retries:
                        time.sleep(wait_time)
                    else:
                        logger.error(f"第 {page} 頁抓取最終失敗")
                        raise RuntimeError(f"無法抓取第 {page} 頁數據: {e}")

            page += 1
            if page <= total_pages:
                time.sleep(delay)

        logger.info(f"共抓取 {len(all_courses)} 條課程")
        return all_courses

    def _fetch_page(
        self,
        page: int = 1,
        age_group: str = "",
        topic: str = "",
        target: str = "",
        status: str = "報名中",
        keyword: str = "",
    ) -> tuple[List[Course], int]:
        """抓取單頁數據"""
        params = {
            "prgvar": "ParentAcademy922605258376053016695",
            "refid": "711905",
            "remark": age_group,
            "remark1": topic,
            "remark2": target,
            "regstatus": status,
            "search_fixdata": "",
            "search_data": keyword,
            "pageis": str(max(page - 1, 0)),
            "search_order": (
                "CASE WHEN (CURRENT_DATE BETWEEN a.remark4 AND a.remark5) THEN 1 "
                "WHEN (CURRENT_DATE < a.remark4) THEN 2 "
                "WHEN (CURRENT_DATE > a.remark5) THEN 3 ELSE 4 END asc, "
                "CASE a.remark when '0-2歲' then 1 when '3-6歲' then 2 "
                "when '7-12歲' then 3 when '13-18歲' then 4 else 5 end asc, "
                "a.start_date desc"
            ),
        }

        url = f"{self.base_url}/webdsejspace/addon/msglisttplan/MsgList_parentacademy_main_page.jsp"

        response = self.session.post(url, data=params, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # 解析分頁信息
        total_pages = self._parse_pagination(soup)

        # 解析課程列表
        courses = self._parse_courses(soup)

        return courses, total_pages

    def _parse_pagination(self, soup: BeautifulSoup) -> int:
        """解析分頁信息，返回總頁數"""
        pagination_text = soup.get_text()
        match = re.search(r'\((\d+)/(\d+)\)', pagination_text)
        if match:
            return int(match.group(2))
        return 1

    def _parse_courses(self, soup: BeautifulSoup) -> List[Course]:
        """解析課程列表"""
        courses = []
        table = soup.find("table", id="result")
        if not table:
            logger.warning("未找到課程表格")
            return courses

        rows = table.find_all("tr")[1:]  # 跳過表頭
        for row in rows:
            course = self._parse_course_row(row)
            if course:
                courses.append(course)

        return courses

    def _parse_course_row(self, row) -> Optional[Course]:
        """解析單個課程行"""
        cols = row.find_all("td")
        if len(cols) < 5:
            return None

        try:
            # 課程ID
            course_id = cols[0].get("title", "").strip()

            # 課程名稱和詳情URL
            name_link = cols[1].find("a", class_="act_title")
            name = name_link.get_text(strip=True) if name_link else ""

            detail_url = ""
            if name_link and name_link.get("onclick"):
                match = re.search(r"changeIframeURL\('([^']+)'", name_link["onclick"])
                if match:
                    detail_url = normalize_course_detail_url(
                        urljoin(self.base_url, match.group(1))
                    )

            # 日期（桌面版列）
            date_str = ""
            if len(cols) > 2:
                date_str = cols[2].get_text(strip=True)

            # 如果桌面版為空，嘗試移動版
            if not date_str:
                mobile_date = cols[1].find("div", class_=lambda x: x and "d-lg-none" in x)
                if mobile_date:
                    date_str = mobile_date.get_text(strip=True)

            # 解析標籤（年齡、主題、對象）
            tags = []
            if len(cols) > 3:
                tag_spans = cols[3].find_all("span", class_="badge")
                tags = [t.get_text(strip=True) for t in tag_spans]

            # 如果沒有在列中找到標籤，嘗試在課程名稱單元格中找
            if not tags:
                tag_div = cols[1].find("div", class_="act_course_tag")
                if tag_div:
                    tag_spans = tag_div.find_all("span", class_="badge")
                    tags = [t.get_text(strip=True) for t in tag_spans]

            # 分類標籤
            age_group = [t for t in tags if t in AGE_GROUP_LABELS.keys()]
            topic_list = [t for t in tags if t in TOPICS]
            target_list = [t for t in tags if t in TARGETS]

            # 報名狀態
            status = ""
            if len(cols) > 4:
                status = cols[4].get("data-status", cols[4].get_text(strip=True))

            # 解析日期
            date_parsed = self._parse_date(date_str)
            week_number = 0
            if date_parsed:
                week_number = date_parsed.isocalendar()[1]

            return Course(
                id=course_id,
                name=name,
                date=date_str,
                date_parsed=date_parsed,
                age_group=age_group[0] if age_group else "",
                age_groups=age_group,
                topic=topic_list[0] if topic_list else "",
                target=target_list[0] if target_list else "",
                status=status,
                detail_url=detail_url,
                week_number=week_number,
            )

        except Exception as e:
            logger.error(f"解析課程行失敗: {e}")
            return None

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """解析日期字符串"""
        if not date_str or date_str == "詳見活動內容":
            return None

        # 格式: YYYY/MM/DD 星期X HH:MM-HH:MM
        match = re.match(r'(\d{4}/\d{2}/\d{2})', date_str)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y/%m/%d")
            except ValueError:
                pass

        return None

    def fetch_all_open_courses(self, max_retries: int = 3, delay: float = 1.0) -> List[Course]:
        """抓取所有報名中的課程"""
        return self.fetch_courses(status="報名中", max_retries=max_retries, delay=delay)

    def fetch_all_age_groups(self, max_retries: int = 3, delay: float = 1.0) -> Dict[str, List[Course]]:
        """按年齡層分別抓取所有課程"""
        result = {}
        for age in AGE_GROUP_LABELS.keys():
            logger.info(f"抓取 {age} 課程...")
            courses = self.fetch_courses(age_group=age, max_retries=max_retries, delay=delay)
            result[age] = courses
        return result
