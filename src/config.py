"""配置管理模組"""

import os
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """應用配置"""

    # API 設置
    api_base_url: str = "https://portal.dsedj.gov.mo"
    api_endpoint: str = "/webdsejspace/addon/msglisttplan/MsgList_parentacademy_main_page.jsp"
    api_prgvar: str = "ParentAcademy922605258376053016695"
    api_refid: str = "711905"

    # 企業微信 Webhook
    wechat_webhook_url: str = ""

    # 排程設置
    push_day: str = "mon"      # mon/tue/wed/thu/fri/sat/sun
    push_hour: int = 9
    push_minute: int = 0

    # 抓取設置
    fetch_status: str = "報名中"
    request_timeout: int = 30
    request_retry: int = 3
    request_delay: float = 1.0

    # 數據存儲
    data_dir: str = "./data"
    subscribers_file: str = "./data/subscribers.json"
    courses_cache_file: str = "./data/courses_cache.json"

    # 日誌
    log_level: str = "INFO"
    log_file: str = "./data/agent.log"

    def __post_init__(self):
        """驗證配置並創建數據目錄"""
        valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        if self.push_day not in valid_days:
            raise ValueError(f"push_day 必須是 {valid_days} 之一，當前: {self.push_day}")

        if not (0 <= self.push_hour <= 23):
            raise ValueError(f"push_hour 必須在 0-23 之間，當前: {self.push_hour}")

        if not (0 <= self.push_minute <= 59):
            raise ValueError(f"push_minute 必須在 0-59 之間，當前: {self.push_minute}")

        # 創建數據目錄
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, prefix: str = "WXAGENT_") -> "Config":
        """從環境變量加載配置"""
        kwargs = {}
        env_mappings = {
            f"{prefix}WEBHOOK_URL": "wechat_webhook_url",
            f"{prefix}PUSH_DAY": "push_day",
            f"{prefix}PUSH_HOUR": "push_hour",
            f"{prefix}PUSH_MINUTE": "push_minute",
            f"{prefix}FETCH_STATUS": "fetch_status",
            f"{prefix}LOG_LEVEL": "log_level",
            f"{prefix}DATA_DIR": "data_dir",
        }

        for env_key, attr_name in env_mappings.items():
            value = os.environ.get(env_key)
            if value is not None:
                # 嘗試類型轉換
                if attr_name in ("push_hour", "push_minute", "request_timeout", "request_retry"):
                    value = int(value)
                elif attr_name == "request_delay":
                    value = float(value)
                kwargs[attr_name] = value
                logger.info(f"從環境變量加載: {attr_name}")

        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """從 YAML 或 JSON 文件加載配置"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            if path.suffix in (".yaml", ".yml"):
                if not HAS_YAML:
                    raise ImportError("加載 YAML 配置需要安裝 PyYAML: pip install pyyaml")
                data = yaml.safe_load(f)
            elif path.suffix == ".json":
                data = json.load(f)
            else:
                raise ValueError(f"不支持的配置格式: {path.suffix}")

        return cls(**data)

    def to_dict(self) -> dict:
        """轉為字典"""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """轉為 JSON 字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save_to_file(self, path: str):
        """保存到文件"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            if path.suffix in (".yaml", ".yml"):
                if not HAS_YAML:
                    raise ImportError("保存 YAML 需要 PyYAML")
                yaml.dump(self.to_dict(), f, allow_unicode=True, sort_keys=False)
            else:
                f.write(self.to_json())

    @property
    def api_url(self) -> str:
        """完整的 API URL"""
        return f"{self.api_base_url}{self.api_endpoint}"
