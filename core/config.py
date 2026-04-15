"""
Helix Configuration
Loads from ~/.helix/config.json. All sensitive values come from secrets.py, not here.
"""

import json
import uuid
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator


CONFIG_PATH = Path.home() / ".helix" / "config.json"
WORKSPACE_PATH = Path.home() / ".helix" / "workspace"

# ─── Sub-models ───────────────────────────────────────────────────────────────

class ModelEntry(BaseModel):
    id: str
    alias: str
    tier: str  # fast | balanced | powerful
    description: str = ""


class ModelsConfig(BaseModel):
    roster: list[ModelEntry] = Field(default_factory=lambda: [
        ModelEntry(id="claude-haiku-4-5-20251022", alias="haiku", tier="fast",      description="Heartbeats, compaction, simple tasks"),
        ModelEntry(id="claude-sonnet-4-6",          alias="sonnet", tier="balanced", description="General work, default"),
        ModelEntry(id="claude-opus-4-6",            alias="opus",   tier="powerful", description="Complex reasoning, heavy tasks"),
    ])
    default: str = "sonnet"
    heartbeat: str = "haiku"
    compaction: str = "haiku"

    def resolve(self, alias_or_id: str) -> str:
        """Return model ID for an alias or pass through a full model ID."""
        for m in self.roster:
            if m.alias == alias_or_id or m.id == alias_or_id:
                return m.id
        raise ValueError(f"Unknown model alias or ID: {alias_or_id!r}")

    @property
    def default_id(self) -> str:
        return self.resolve(self.default)

    @property
    def heartbeat_id(self) -> str:
        return self.resolve(self.heartbeat)

    @property
    def compaction_id(self) -> str:
        return self.resolve(self.compaction)


class DiscordConfig(BaseModel):
    enabled: bool = False
    allowed_users: list[str] = Field(default_factory=list)   # Discord snowflake IDs
    guild_channels: list[str] = Field(default_factory=list)  # Channel IDs to listen in
    mention_only: bool = False  # Only respond when mentioned in guild channels


class TelegramConfig(BaseModel):
    enabled: bool = False
    allowed_users: list[int] = Field(default_factory=list)   # Telegram user IDs


class RateLimitConfig(BaseModel):
    per_minute: int = 20
    per_hour: int = 200


class SecurityConfig(BaseModel):
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    web_fetch_domain_allowlist: list[str] = Field(default_factory=list)  # empty = all allowed
    inject_detection_enabled: bool = True
    audit_log_max_mb: int = 100



class SessionConfig(BaseModel):
    routing: str = "per-channel-peer"
    daily_reset_hour: int = 4   # Hour in configured timezone
    idle_reset_minutes: int = 0    # 0 = no idle reset
    compaction_token_budget: int = 20000
    compaction_keep_pct: float = 0.6


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    admin_port: int = 18791
    jwt_expiry_hours: int = 24


class CronJob(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    schedule: str        # standard 5-field cron expression e.g. "0 8 * * *"
    prompt: str          # message to send to the agent when this fires
    enabled: bool = True
    last_run: Optional[str] = None   # ISO timestamp
    model: Optional[str] = None      # alias/id override; None = use heartbeat model


class HelixConfig(BaseModel):
    agent_id: str = "helix"
    timezone: str = "UTC"
    workspace_path: str = str(WORKSPACE_PATH)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    crons: list[CronJob] = Field(default_factory=list)
    log_level: str = "INFO"
    heartbeat_interval_minutes: int = 30

    @model_validator(mode="after")
    def validate_workspace(self) -> "HelixConfig":
        p = Path(self.workspace_path)
        p.mkdir(parents=True, exist_ok=True)
        return self


# ─── Load / Save ──────────────────────────────────────────────────────────────

_config_cache: Optional[HelixConfig] = None


def load_config(path: Path = CONFIG_PATH, force_reload: bool = False) -> HelixConfig:
    global _config_cache
    if _config_cache is not None and not force_reload:
        return _config_cache

    if path.exists():
        raw = json.loads(path.read_text())
        _config_cache = HelixConfig.model_validate(raw)
    else:
        _config_cache = HelixConfig()
        save_config(_config_cache, path)

    return _config_cache


def save_config(cfg: HelixConfig, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg.model_dump(), indent=2))
    path.chmod(0o600)


def get_config() -> HelixConfig:
    return load_config()
