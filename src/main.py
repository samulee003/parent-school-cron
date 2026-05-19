"""主入口 — 家長學堂課程推送 Agent v2"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

# 將 src 加入路徑
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config import Config
from bot_server import ParentAcademyBot

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

logger = logging.getLogger("parent_academy_agent")


def setup_logging(config: Config):
    """設置日誌"""
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    # 文件
    try:
        from logging.handlers import TimedRotatingFileHandler
        file_h = TimedRotatingFileHandler(
            config.log_file, when="midnight", interval=1,
            backupCount=7, encoding="utf-8",
        )
        file_h.setLevel(log_level)
        file_h.setFormatter(formatter)
        root_logger.addHandler(file_h)
    except Exception as e:
        logger.warning(f"文件日誌失敗: {e}")


def print_banner():
    """打印啟動畫面"""
    print("""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║        📚 家長學堂課程推送 Agent v2.0                      ║
║                                                           ║
║   家長掃碼加好友 → AI對話設定 → 自動每週推送              ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
    """)


async def main_async(config: Config):
    """異步主入口"""
    print_banner()
    setup_logging(config)

    logger.info("=" * 60)
    logger.info("Agent 啟動")
    logger.info("=" * 60)

    # 創建機器人
    bot = ParentAcademyBot(config)

    # 設置信號處理
    shutdown_event = asyncio.Event()

    def signal_handler(sig, frame):
        logger.info(f"收到信號 {sig}")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 設置定時任務
    scheduler = None
    if HAS_SCHEDULER:
        scheduler = AsyncIOScheduler(timezone="Asia/Macau")

        # 每週推送
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        cron_day = day_map.get(config.push_day.lower(), 0)

        scheduler.add_job(
            bot.run_scheduled_push,
            CronTrigger(day_of_week=cron_day, hour=config.push_hour, minute=config.push_minute),
            id="weekly_push",
            name="每週課程推送",
        )
        logger.info(f"定時推送: 每週 {config.push_day} {config.push_hour:02d}:{config.push_minute:02d}")

        # 每日健康檢查
        scheduler.add_job(
            lambda: logger.info(f"健康檢查 | 用戶數: {bot.store.get_stats()}"),
            CronTrigger(hour=8, minute=0),
            id="health_check",
            name="每日健康檢查",
        )

        scheduler.start()
    else:
        logger.warning("未安裝 APScheduler，無定時推送")

    # 啟動機器人
    bot_task = asyncio.create_task(bot.start())

    # 等待關機信號
    try:
        logger.info("Agent 運行中... (按 Ctrl+C 停止)")
        await shutdown_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("正在關閉...")
        if scheduler:
            scheduler.shutdown()
        await bot.stop()
        logger.info("Agent 已關閉")


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="家長學堂課程推送 Agent v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 正常啟動
  python main.py --push-day mon     # 每週一推送
  python main.py --push-hour 9      # 早上9點
        """,
    )

    parser.add_argument("--config", "-c", help="配置文件路徑 (YAML/JSON)")
    parser.add_argument("--push-day", default="mon", help="推送星期")
    parser.add_argument("--push-hour", type=int, default=9, help="推送小時")
    parser.add_argument("--push-minute", type=int, default=0, help="推送分鐘")
    parser.add_argument("--log-level", default="INFO", help="日誌級別")
    parser.add_argument("--data-dir", default="./data", help="數據目錄")
    parser.add_argument("--version", "-v", action="store_true", help="版本")

    args = parser.parse_args()

    if args.version:
        print("家長學堂課程推送 Agent v2.0")
        sys.exit(0)

    # 加載配置
    config = Config()

    if args.config:
        config = Config.from_file(args.config)

    # 環境變量
    env = Config.from_env()
    for k, v in env.to_dict().items():
        if v != getattr(Config(), k):
            setattr(config, k, v)

    # 命令行覆蓋
    config.push_day = args.push_day
    config.push_hour = args.push_hour
    config.push_minute = args.push_minute
    config.log_level = args.log_level
    config.data_dir = args.data_dir

    # 運行
    asyncio.run(main_async(config))


if __name__ == "__main__":
    main()
