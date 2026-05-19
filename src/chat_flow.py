"""AI 對話流程 — 引導家長完成設定"""

import logging
from typing import List, Tuple, Optional

from scraper import AGE_GROUP_LABELS

logger = logging.getLogger(__name__)

# 年齡選項
AGE_OPTIONS = list(AGE_GROUP_LABELS.items())  # [("0-2歲", "嬰幼兒期"), ...]


class ChatFlow:
    """AI 對話狀態機"""

    STATE_WELCOME = "welcome"      # 新用戶歡迎
    STATE_SELECT_AGE = "select_age"  # 選擇年齡層
    STATE_CONFIRM = "confirm"      # 確認選擇
    STATE_ACTIVE = "active"        # 設定完成，正常接收推送
    STATE_IDLE = "idle"            # 未設定/暫停

    def __init__(self):
        self.age_options = AGE_OPTIONS

    def get_welcome_message(self) -> str:
        """歡迎消息"""
        return (
            "👋 歡迎使用家長學堂課程助手！\n\n"
            "我會每週自動推送適合您孩子的課程活動，"
            "讓您不錯過任何精彩的親子活動。\n\n"
            "📋 首先，請告訴我您孩子的年齡層，"
            "這樣我才能為您推薦最適合的課程。\n\n"
            f"{self._format_age_menu()}\n"
            "💡 可以選擇多個，輸入對應數字即可。\n"
            "例如：輸入「1」或「1,3」"
        )

    def get_age_select_message(self) -> str:
        """年齡選擇提示"""
        return (
            "請選擇您孩子的年齡層：\n\n"
            f"{self._format_age_menu()}\n\n"
            "輸入對應數字（可多選，用逗號分隔）\n"
            "例如：「1」或「1,3」\n"
            "選完後輸入「完成」繼續"
        )

    def get_help_message(self) -> str:
        """幫助消息"""
        return (
            "📖 使用說明\n\n"
            "🔹 回覆「修改」— 重新設定年齡層\n"
            "🔹 回覆「停止」— 暫停接收推送\n"
            "🔹 回覆「開始」— 恢復接收推送\n"
            "🔹 回覆「狀態」— 查看當前設定\n"
            "🔹 回覆「幫助」— 查看此說明\n\n"
            "每週一早上 9 點，我會自動推送當週適合您的課程。"
        )

    def _format_age_menu(self) -> str:
        """格式化年齡選擇菜單"""
        lines = []
        for i, (age, label) in enumerate(self.age_options, 1):
            emoji = {"嬰幼兒期": "👶", "幼兒期": "🧒", "兒童期": "🧑", "青少年期": "🧑‍🎓"}
            lines.append(f"{i}. {emoji.get(label, '📌')} {label}（{age}）")
        return "\n".join(lines)

    def _parse_age_selection(self, text: str) -> Tuple[List[str], str]:
        """
        解析用戶的年齡選擇

        Returns:
            (selected_ages, 響應消息)
        """
        text = text.strip().replace("，", ",").replace(" ", "")
        selected = []
        errors = []

        # 支持「1,2」或「1 2」或「1」
        parts = text.split(",") if "," in text else text.split()

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # 嘗試解析為數字
            try:
                idx = int(part)
                if 1 <= idx <= len(self.age_options):
                    age = self.age_options[idx - 1][0]
                    if age not in selected:
                        selected.append(age)
                else:
                    errors.append(part)
            except ValueError:
                # 嘗試直接匹配年齡文字
                matched = False
                for age, label in self.age_options:
                    if part in age or part in label:
                        if age not in selected:
                            selected.append(age)
                        matched = True
                        break
                if not matched:
                    errors.append(part)

        if errors:
            error_msg = f"⚠️ 無法識別的選項: {', '.join(errors)}"
        else:
            error_msg = ""

        return selected, error_msg

    def handle_message(
        self,
        user_id: str,
        message: str,
        current_state: str,
        current_ages: List[str],
    ) -> Tuple[str, str, List[str], bool]:
        """
        處理用戶消息，返回 (新狀態, 回覆消息, 年齡選擇, 是否更新)

        Args:
            user_id: 微信用戶ID
            message: 用戶消息
            current_state: 當前狀態
            current_ages: 當前已選年齡

        Returns:
            (new_state, reply, selected_ages, should_update)
        """
        msg = message.strip()
        msg_lower = msg.lower()

        # 全局命令（任何狀態都響應）
        if msg_lower in ("幫助", "help", "?", "？"):
            return current_state, self.get_help_message(), current_ages, False

        if msg_lower in ("停止", "取消", "stop", "unsubscribe"):
            return self.STATE_IDLE, (
                "⏸️ 已暫停接收課程推送。\n\n"
                "隨時回覆「開始」恢復推送。"
            ), current_ages, True

        if msg_lower in ("開始", "start", "subscribe"):
            if not current_ages:
                return self.STATE_SELECT_AGE, self.get_age_select_message(), current_ages, True
            return self.STATE_ACTIVE, (
                "✅ 已恢復接收推送！\n\n"
                f"當前設定: {self._format_ages(current_ages)}\n\n"
                "每週一早上 9 點為您推送課程。"
            ), current_ages, True

        if msg_lower in ("修改", "修改年齡", "change", "edit"):
            return self.STATE_SELECT_AGE, self.get_age_select_message(), [], True

        if msg_lower in ("狀態", "status", "info"):
            if current_ages:
                return current_state, (
                    f"📋 當前設定:\n\n"
                    f"👶 孩子年齡層: {self._format_ages(current_ages)}\n"
                    f"📬 推送狀態: {'✅ 已啟用' if current_state == self.STATE_ACTIVE else '⏸️ 已暫停'}\n\n"
                    "回覆「修改」可更改設定"
                ), current_ages, False
            else:
                return current_state, (
                    "❓ 您還沒有設定孩子年齡層。\n\n"
                    f"{self.get_age_select_message()}"
                ), current_ages, False

        # 狀態機處理
        if current_state == self.STATE_WELCOME:
            return self._handle_welcome(msg, current_ages)

        elif current_state == self.STATE_SELECT_AGE:
            return self._handle_select_age(msg, current_ages)

        elif current_state == self.STATE_CONFIRM:
            return self._handle_confirm(msg, current_ages)

        elif current_state == self.STATE_ACTIVE:
            # 活躍狀態下收到非命令消息，給予友好提示
            return current_state, (
                f"收到您的消息！我已經記住了您的設定：{self._format_ages(current_ages)}\n\n"
                "每週一早上 9 點會自動推送課程給您。\n\n"
                "如需修改設定，請回覆「修改」\n"
                "查看說明請回覆「幫助」"
            ), current_ages, False

        elif current_state == self.STATE_IDLE:
            return self.STATE_SELECT_AGE, self.get_age_select_message(), [], True

        return current_state, "抱歉，我不明白您的意思。請回覆「幫助」查看說明。", current_ages, False

    def _handle_welcome(self, msg: str, current_ages: List[str]) -> Tuple[str, str, List[str], bool]:
        """處理歡迎狀態的消息"""
        # 嘗試解析年齡選擇
        selected, error = self._parse_age_selection(msg)
        if selected:
            combined = list(set(current_ages + selected))
            return self.STATE_CONFIRM, self._format_confirm(combined), combined, True

        # 如果無法解析，提示重新選擇
        return self.STATE_SELECT_AGE, (
            f"{error}\n\n" if error else ""
        ) + self.get_age_select_message(), current_ages, False

    def _handle_select_age(
        self, msg: str, current_ages: List[str]
    ) -> Tuple[str, str, List[str], bool]:
        """處理年齡選擇狀態"""
        msg = msg.strip()

        # 檢查是否完成
        if msg in ("完成", "好了", "done", "ok", "確認"):
            if not current_ages:
                return self.STATE_SELECT_AGE, (
                    "⚠️ 您還沒有選擇任何年齡層。\n\n"
                    f"{self._format_age_menu()}\n\n"
                    "請輸入對應數字。"
                ), current_ages, False
            return self.STATE_CONFIRM, self._format_confirm(current_ages), current_ages, False

        # 嘗試解析選擇
        selected, error = self._parse_age_selection(msg)
        if selected:
            combined = list(set(current_ages + selected))
            labels = [f"{AGE_GROUP_LABELS[a]}（{a}）" for a in combined]

            reply = (
                f"✅ 已選擇: {', '.join(labels)}\n\n"
                "還要選擇其他年齡層嗎？\n"
                "輸入「完成」確認，或繼續選擇。"
            )
            return self.STATE_SELECT_AGE, reply, combined, True

        # 無法識別
        return self.STATE_SELECT_AGE, (
            f"⚠️ 無法識別「{msg}」\n\n"
            f"{self._format_age_menu()}\n\n"
            "請輸入數字（如「1」或「1,3」），選完後輸入「完成」。"
        ), current_ages, False

    def _handle_confirm(
        self, msg: str, current_ages: List[str]
    ) -> Tuple[str, str, List[str], bool]:
        """處理確認狀態"""
        msg_lower = msg.strip().lower()

        if msg_lower in ("確認", "1", "yes", "y", "好", "ok", "是的"):
            return self.STATE_ACTIVE, (
                "🎉 設定完成！\n\n"
                f"我將為您推送以下年齡層的課程：\n"
                f"{self._format_ages(current_ages)}\n\n"
                "📬 每週一早上 9 點自動推送\n"
                "📝 有新課程時會即時通知\n\n"
                "如需修改設定，隨時回覆「修改」\n"
                "查看說明請回覆「幫助」"
            ), current_ages, True

        if msg_lower in ("重新選", "2", "no", "n", "否", "修改"):
            return self.STATE_SELECT_AGE, self.get_age_select_message(), [], True

        # 不明確的輸入，再次提示
        return self.STATE_CONFIRM, (
            f"{self._format_confirm(current_ages)}\n\n"
            "請回覆「確認」或「1」完成設定，"
            "或「重新選」重新選擇。"
        ), current_ages, False

    def _format_ages(self, ages: List[str]) -> str:
        """格式化年齡層列表"""
        parts = []
        for age in ages:
            label = AGE_GROUP_LABELS.get(age, age)
            emoji = {"嬰幼兒期": "👶", "幼兒期": "🧒", "兒童期": "🧑", "青少年期": "🧑‍🎓"}
            parts.append(f"  {emoji.get(label, '📌')} {label}（{age}）")
        return "\n".join(parts)

    def _format_confirm(self, ages: List[str]) -> str:
        """格式化確認消息"""
        return (
            "📋 請確認您的選擇：\n\n"
            f"{self._format_ages(ages)}\n\n"
            "回覆「確認」完成設定\n"
            "回覆「重新選」重新選擇"
        )
