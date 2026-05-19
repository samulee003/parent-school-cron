"""排程器模組 - 定時任務管理"""

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event
from typing import Callable, Dict, Any, Optional

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    BackgroundScheduler = None
    CronTrigger = None

logger = logging.getLogger(__name__)


class SchedulerError(Exception):
    """排程器異常"""
    pass


class SchedulerNotRunningError(SchedulerError):
    """排程器未運行"""
    pass


class DuplicateJobError(SchedulerError):
    """重複任務"""
    pass


@dataclass
class PushResult:
    """推送結果"""
    success: bool = False
    timestamp: str = ""
    total_courses: int = 0
    pushed_groups: int = 0
    failed_groups: int = 0
    details: Dict[str, bool] = field(default_factory=dict)
    error: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


class CourseScheduler:
    """課程推送排程器"""

    JOB_WEEKLY_PUSH = "weekly_push"
    JOB_DAILY_CHECK = "daily_check"

    def __init__(self, config: "Config"):
        self.config = config
        self._scheduler = None
        self._shutdown_event = Event()
        self._is_running = False
        self._push_job_func: Optional[Callable] = None

        if not HAS_APSCHEDULER:
            raise ImportError(
                "使用排程功能需要安裝 APScheduler:\n"
                "  pip install apscheduler"
            )

        self._scheduler = BackgroundScheduler(
            timezone=config.get("timezone", "Asia/Macau"),
            job_defaults={
                "misfire_grace_time": 3600,  # 1小時容錯
                "coalesce": True,             # 合併錯過的任務
                "max_instances": 1,           # 同時只執行一個實例
            },
        )

        # 註冊事件監聽
        self._scheduler.add_listener(
            self._on_job_executed,
            EVENT_JOB_EXECUTED,
        )
        self._scheduler.add_listener(
            self._on_job_error,
            EVENT_JOB_ERROR,
        )

    def register_push_job(self, job_func: Callable[[], PushResult]):
        """
        註冊推送任務函數

        Args:
            job_func: 推送任務函數，返回 PushResult
        """
        self._push_job_func = job_func

    def schedule_weekly_push(
        self,
        day_of_week: str = "mon",
        hour: int = 9,
        minute: int = 0,
    ):
        """
        設置每週推送任務

        Args:
            day_of_week: 星期 (mon/tue/wed/thu/fri/sat/sun)
            hour: 小時 (0-23)
            minute: 分鐘 (0-59)
        """
        if not self._scheduler:
            raise SchedulerNotRunningError("排程器未初始化")

        # 移除已存在的任務
        self._remove_job(self.JOB_WEEKLY_PUSH)

        # 星期映射
        day_map = {
            "mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
            "fri": "fri", "sat": "sat", "sun": "sun",
        }
        cron_day = day_map.get(day_of_week.lower(), "mon")

        self._scheduler.add_job(
            func=self._run_weekly_push,
            trigger=CronTrigger(
                day_of_week=cron_day,
                hour=hour,
                minute=minute,
            ),
            id=self.JOB_WEEKLY_PUSH,
            name="每週課程推送",
            replace_existing=True,
        )

        logger.info(f"已設置每週推送: {cron_day} {hour:02d}:{minute:02d}")

    def schedule_daily_check(
        self,
        hour: int = 8,
        minute: int = 0,
    ):
        """
        設置每日檢查任務

        Args:
            hour: 小時
            minute: 分鐘
        """
        if not self._scheduler:
            raise SchedulerNotRunningError("排程器未初始化")

        self._remove_job(self.JOB_DAILY_CHECK)

        self._scheduler.add_job(
            func=self._run_daily_check,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=self.JOB_DAILY_CHECK,
            name="每日課程檢查",
            replace_existing=True,
        )

        logger.info(f"已設置每日檢查: {hour:02d}:{minute:02d}")

    def run_now(self) -> Optional[PushResult]:
        """立即執行一次推送任務"""
        logger.info("手動觸發推送任務")
        return self._run_weekly_push()

    def start(self):
        """啟動排程器"""
        if not self._scheduler:
            raise SchedulerNotRunningError("排程器未初始化")

        # 註冊信號處理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self._scheduler.start()
        self._is_running = True
        logger.info("排程器已啟動")

    def shutdown(self, wait: bool = True):
        """關閉排程器"""
        if self._scheduler:
            self._scheduler.shutdown(wait=wait)
            self._is_running = False
            self._shutdown_event.set()
            logger.info("排程器已關閉")

    def keep_running(self):
        """保持主線程運行"""
        logger.info("Agent 運行中... (按 Ctrl+C 停止)")
        try:
            while not self._shutdown_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("收到鍵盤中斷")
        finally:
            self.shutdown()

    def get_jobs(self) -> list:
        """獲取所有任務"""
        if self._scheduler:
            return self._scheduler.get_jobs()
        return []

    def _run_weekly_push(self) -> Optional[PushResult]:
        """執行每週推送"""
        logger.info("開始執行每週推送任務")

        if not self._push_job_func:
            logger.error("未註冊推送任務函數")
            return PushResult(success=False, error="未註冊推送任務函數")

        try:
            result = self._push_job_func()
            if result.success:
                logger.info(f"每週推送完成: {result.pushed_groups} 組成功, {result.failed_groups} 組失敗")
            else:
                logger.warning(f"每週推送失敗: {result.error}")
            return result

        except Exception as e:
            logger.exception(f"每週推送任務異常: {e}")
            return PushResult(success=False, error=str(e))

    def _run_daily_check(self):
        """執行每日檢查"""
        logger.info("開始每日課程檢查")
        # TODO: 實現每日檢查邏輯（如檢查新課程、報名截止提醒等）
        logger.info("每日課程檢查完成")

    def _remove_job(self, job_id: str):
        """移除指定任務"""
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # 任務不存在，忽略

    def _on_job_executed(self, event):
        """任務執行成功回調"""
        logger.debug(f"任務執行成功: {event.job_id}")

    def _on_job_error(self, event):
        """任務執行失敗回調"""
        logger.error(f"任務執行失敗: {event.job_id}, 異常: {event.exception}")

    def _signal_handler(self, signum, frame):
        """信號處理"""
        sig_name = signal.Signals(signum).name
        logger.info(f"收到信號: {sig_name}")
        self._shutdown_event.set()
