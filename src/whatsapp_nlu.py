"""Local NLU helpers for WhatsApp parent messages."""

import re
import unicodedata
from typing import Any, Dict, List, Optional

from scraper import AGE_GROUP_LABELS


AGE_KEYWORDS = {
    "0-2歲": ("0-2", "0至2", "0到2", "嬰兒", "嬰幼", "寶寶", "bb"),
    "3-6歲": ("3-6", "3至6", "3到6", "幼兒", "幼稚園", "幼兒園"),
    "7-12歲": ("7-12", "7至12", "7到12", "小學", "小學生", "兒童"),
    "13-18歲": ("13-18", "13至18", "13到18", "中學", "中學生", "青少年"),
}

CHINESE_NUMERAL_VALUES = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

NEXT_PAGE_KEYWORDS = {
    "更多", "下一頁", "下頁", "還有嗎", "還有沒有", "還有",
    "有其他嗎", "還有別的嗎", "再來", "繼續", "more", "next",
}
ALL_COURSE_KEYWORDS = {"全部課程", "全部", "all"}
RESET_KEYWORDS = {"重設", "重新設定", "reset"}
COURSE_INTENT_KEYWORDS = (
    "課程", "课程", "course", "最新", "推薦", "推荐", "推介",
    "幫我揀", "帮我揀", "幫我選", "帮我选", "報名", "报名",
    "搵", "找", "搜尋", "搜索", "查詢", "查询", "查找", "search",
)
COURSE_DOMAIN_KEYWORDS = (
    "課程", "课程", "course", "家長學堂", "家长学堂", "活動", "活动",
    "講座", "讲座", "工作坊", "報名", "报名", "親子", "亲子",
    "家長", "家长", "小朋友", "孩子", "子女", "嬰幼", "婴幼",
    "幼兒", "幼儿", "小學", "小学", "青少年", "中學", "中学",
)
OFF_TOPIC_KEYWORDS = (
    "餐廳", "餐厅", "外賣", "外卖", "酒店", "機票", "机票", "航班",
    "天氣", "天气", "股票", "投資", "投资", "幣", "币", "匯率", "汇率",
    "作文", "翻譯", "翻译", "新聞", "新闻", "電影", "电影", "音樂", "音乐",
    "遊戲", "游戏", "食譜", "食谱", "功課", "功课", "數學題", "数学题",
    "醫生", "医生", "診所", "诊所", "藥", "药", "感冒", "python",
    "javascript", "寫code", "写code", "寫程式", "写程序",
)
BARE_AGE_CONTEXT_KEYWORDS = (
    "小朋友", "孩子", "子女", "仔女", "兒子", "儿子", "女兒", "女儿",
    "我個仔", "我个仔", "我個女", "我个女", "大仔", "細仔", "细仔",
    "age", "幾歲", "几歲", "幾多歲", "幾多岁", "歲數", "岁数",
)
PARENT_CONTEXT_KEYWORDS = (
    "小朋友", "孩子", "子女", "仔女", "兒子", "儿子", "女兒", "女儿",
    "我個仔", "我个仔", "我個女", "我个女", "家長", "家长", "父母",
    "媽媽", "妈妈", "爸爸", "幼兒", "幼儿", "小學生", "小学生",
    "中學生", "中学生", "青少年", "bb", "寶寶", "宝宝",
)
SOFT_PARENT_PAIN_OFF_TOPIC_KEYWORDS = (
    "功課", "功课", "遊戲", "游戏",
)
OFF_TOPIC_TASK_KEYWORDS = (
    "答案", "解答", "題目", "题目", "數學題", "数学题", "幫我做",
    "帮我做", "代做", "翻譯", "翻译", "作文",
)
PAIN_POINT_RULES = [
    {
        "tag": "情緒壓力",
        "topic": "身心健康",
        "keywords": (
            "情緒", "情绪", "焦慮", "焦虑", "壓力", "压力", "發脾氣", "发脾气",
            "易怒", "暴躁", "抑鬱", "抑郁", "心情", "哭", "青春期",
        ),
    },
    {
        "tag": "親子溝通",
        "topic": "家庭關係",
        "keywords": (
            "溝通", "沟通", "頂嘴", "顶嘴", "衝突", "冲突", "吵架",
            "反叛", "叛逆", "不聽話", "不听话", "管教", "親子關係", "亲子关系",
        ),
    },
    {
        "tag": "學習動機",
        "topic": "學習與成就感",
        "keywords": (
            "學習", "学习", "功課", "功课", "成績", "成绩", "考試", "考试",
            "專注", "专注", "拖延", "讀書", "读书", "動機", "动机",
        ),
    },
    {
        "tag": "環境適應",
        "topic": "環境適應",
        "keywords": (
            "適應", "适应", "入學", "入学", "轉校", "转校", "升小",
            "升中", "分離", "分离", "新環境", "新环境",
        ),
    },
    {
        "tag": "社交人際",
        "topic": "社會人際關係",
        "keywords": (
            "交朋友", "無朋友", "沒有朋友", "冇朋友", "朋友少",
            "同學", "同学", "社交", "人際", "人际", "欺凌",
            "孤立", "群體", "群体", "相處", "相处",
        ),
    },
    {
        "tag": "生活照顧",
        "topic": "生活照顧",
        "keywords": (
            "睡眠", "瞓覺", "睡覺", "吃飯", "食飯", "飲食", "自理",
            "如廁", "戒片", "生活習慣", "生活习惯",
        ),
    },
    {
        "tag": "科技使用",
        "topic": "科技素養",
        "keywords": (
            "手機", "手机", "打機", "遊戲", "游戏", "上網", "上网",
            "短片", "平板", "網絡", "网络", "沉迷",
        ),
    },
]


def normalize_parent_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_compact(text: str) -> str:
    normalized = normalize_parent_text(text).lower().replace("岁", "歲")
    return re.sub(r"[\s\?？!！。,.、，；;:：]+", "", normalized)


def parse_chinese_number(value: str) -> Optional[int]:
    """Parse small Chinese numerals used for child ages, e.g. 八, 十三, 十八."""
    text = str(value or "").strip()
    if not text:
        return None
    if all(ch in CHINESE_NUMERAL_VALUES for ch in text):
        number = 0
        for ch in text:
            number = number * 10 + CHINESE_NUMERAL_VALUES[ch]
        return number
    if text == "十":
        return 10
    if "十" not in text:
        return None
    left, right = text.split("十", 1)
    if left == "":
        tens = 1
    elif left in CHINESE_NUMERAL_VALUES:
        tens = CHINESE_NUMERAL_VALUES[left]
    else:
        return None
    if right == "":
        ones = 0
    elif right in CHINESE_NUMERAL_VALUES:
        ones = CHINESE_NUMERAL_VALUES[right]
    else:
        return None
    return tens * 10 + ones


def age_to_group(age: float) -> str:
    if 0 <= age < 3:
        return "0-2歲"
    if 3 <= age < 7:
        return "3-6歲"
    if 7 <= age < 13:
        return "7-12歲"
    if 13 <= age <= 18:
        return "13-18歲"
    return ""


def _ordered_groups(groups: List[str]) -> List[str]:
    seen = set(groups)
    return [age for age in AGE_GROUP_LABELS if age in seen]


def _should_parse_bare_numbers(normalized: str) -> bool:
    if re.search(r"\d{4}\s*[/-]\s*\d{1,2}\s*[/-]\s*\d{1,2}", normalized):
        return False
    if re.fullmatch(r"(?:page|detail)\s*\d{1,3}", normalized):
        return False
    if re.fullmatch(r"詳情\s*\d{1,3}", normalized):
        return False
    if re.fullmatch(r"第\s*\d{1,3}\s*(?:頁|页)", normalized):
        return False
    if re.fullmatch(r"\d{1,2}", normalized):
        return True
    if re.fullmatch(r"\d{1,2}(?:\s*(?:and|和|同|,|，|、|&|\+)\s*\d{1,2})+", normalized):
        return True
    return any(keyword in normalized for keyword in BARE_AGE_CONTEXT_KEYWORDS)


def detect_child_age_groups(text: str) -> List[str]:
    """Detect child age groups from age values and school-stage hints."""
    normalized = normalize_parent_text(text).lower().replace("岁", "歲")
    groups: List[str] = []

    for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)\s*歲", normalized):
        group = age_to_group(float(match.group(1)))
        if group and group not in groups:
            groups.append(group)

    for match in re.finditer(r"([零〇一二兩两三四五六七八九十]{1,3})\s*歲", normalized):
        age = parse_chinese_number(match.group(1))
        if age is None:
            continue
        group = age_to_group(float(age))
        if group and group not in groups:
            groups.append(group)

    if _should_parse_bare_numbers(normalized):
        for match in re.finditer(r"(?<!\d)(\d{1,2})(?!\d)", normalized):
            group = age_to_group(float(match.group(1)))
            if group and group not in groups:
                groups.append(group)

    for age in AGE_GROUP_LABELS:
        if age.lower() in normalized and age not in groups:
            groups.append(age)

    for age, keywords in AGE_KEYWORDS.items():
        if any(keyword.lower() in normalized for keyword in keywords) and age not in groups:
            groups.append(age)

    return _ordered_groups(groups)


def detect_child_age_group(text: str) -> Optional[str]:
    groups = detect_child_age_groups(text)
    return groups[0] if groups else None


def detect_local_intent(text: str) -> str:
    normalized = _normalize_compact(text)
    if not normalized:
        return ""
    if normalized in NEXT_PAGE_KEYWORDS:
        return "next_page"
    if normalized in ALL_COURSE_KEYWORDS:
        return "all_courses"
    if normalized in RESET_KEYWORDS:
        return "reset"
    if any(keyword in normalized for keyword in COURSE_INTENT_KEYWORDS):
        return "courses"
    return ""


def is_hard_off_topic(text: str) -> bool:
    normalized = normalize_parent_text(text).lower()
    if not normalized:
        return False
    off_topic_hits = [
        keyword for keyword in OFF_TOPIC_KEYWORDS
        if keyword in normalized
    ]
    if not off_topic_hits:
        return False
    if any(keyword not in SOFT_PARENT_PAIN_OFF_TOPIC_KEYWORDS for keyword in off_topic_hits):
        return True
    if any(keyword in normalized for keyword in OFF_TOPIC_TASK_KEYWORDS):
        return True
    has_parent_context = any(keyword in normalized for keyword in PARENT_CONTEXT_KEYWORDS)
    return not (has_parent_context and detect_pain_points(normalized))


def detect_pain_points(text: str) -> List[Dict[str, str]]:
    text_lower = normalize_parent_text(text).lower()
    matches: List[Dict[str, str]] = []
    for rule in PAIN_POINT_RULES:
        keywords = (str(rule["tag"]),) + tuple(str(keyword) for keyword in rule["keywords"])
        if any(keyword.lower() in text_lower for keyword in keywords):
            matches.append({
                "tag": str(rule["tag"]),
                "topic": str(rule["topic"]),
            })
    return matches


def extract_local_profile_patch(text: str) -> Dict[str, Any]:
    patch: Dict[str, Any] = {}
    age_groups = detect_child_age_groups(text)
    pain_points = detect_pain_points(text)
    if age_groups:
        patch["age_groups"] = age_groups
    if pain_points:
        patch["pain_points"] = [point["tag"] for point in pain_points]
    return patch
