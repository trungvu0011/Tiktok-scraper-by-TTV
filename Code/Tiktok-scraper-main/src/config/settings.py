from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os
from typing import Optional, Dict


class Settings(BaseModel):
    """
    Minimal settings container.

    This repository intentionally does NOT include platform-circumvention logic.
    Use this as a configuration layer for your own compliant data collection flows.
    """

    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    )
    timeout_s: int = Field(default=20, ge=1, le=120)
    retries: int = Field(default=3, ge=0, le=10)
    output_dir: str = Field(default="output")

    # SQLite file backing the automation layer (jobs, schedules, snapshots,
    # alerts, notifications). Relative paths resolve against the package root.
    db_path: str = Field(default="scraper.db")

    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None

    def model_post_init(self, __context) -> None:
        load_dotenv()
        self.http_proxy = os.getenv("HTTP_PROXY") or self.http_proxy
        self.https_proxy = os.getenv("HTTPS_PROXY") or self.https_proxy

    @property
    def proxies(self) -> Optional[Dict[str, str]]:
        proxies: Dict[str, str] = {}
        if self.http_proxy:
            proxies["http"] = self.http_proxy
        if self.https_proxy:
            proxies["https"] = self.https_proxy
        return proxies or None
