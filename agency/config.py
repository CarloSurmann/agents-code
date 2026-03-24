"""
Config — YAML deployment config parser.

Loads per-client YAML config files with environment variable substitution.
Supports ${ENV_VAR} syntax for secrets.

Design: Giovanni's architecture (2026-03-24 session).
"""

import os
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)


def _substitute_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} patterns with actual environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            logger.warning(f"Environment variable not set: {var_name}")
            return match.group(0)  # Keep original if not found
        return env_value

    return re.sub(r"\$\{(\w+)\}", replacer, value)


def _process_config_values(obj):
    """Recursively substitute env vars in all string values."""
    if isinstance(obj, str):
        return _substitute_env_vars(obj)
    elif isinstance(obj, dict):
        return {k: _process_config_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_process_config_values(item) for item in obj]
    return obj


@dataclass
class FollowUpStep:
    number: int
    day_offset: int
    template: str


@dataclass
class EmailConfig:
    provider: str = "gmail"  # "gmail" | "msgraph" | "imap"
    user_email: str = ""
    credentials_path: str = ""
    # Gmail specific
    token_path: str = ""
    # Microsoft Graph specific
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""


@dataclass
class HITLConfig:
    channel: str = "console"  # "console" | "slack" | "teams" | "telegram"
    autonomy_level: str = "supervised"  # "supervised" | "semi_auto" | "autonomous"
    value_threshold: float = 5000.0
    always_approve_breakup: bool = True
    # Slack specific
    slack_bot_token: str = ""
    slack_app_token: str = ""        # For Socket Mode (xapp-...)
    slack_channel_id: str = ""
    slack_signing_secret: str = ""   # For HTTP mode
    # Telegram specific
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class CRMConfig:
    provider: str = "none"  # "none" | "hubspot" | "pipedrive"
    api_key: str = ""
    enrich_deal_value: bool = False
    log_follow_ups: bool = False


@dataclass
class StorageConfig:
    db_path: str = "./data/tracker.db"
    sheets_id: str = ""


@dataclass
class VoiceConfig:
    tone: str = "professional, warm"
    language: str = "English"
    company_context: str = ""
    user_name: str = ""
    company_name: str = ""


@dataclass
class AgentConfig:
    """Complete agent deployment configuration."""
    client_name: str = ""
    region: str = ""
    model: str = "claude-sonnet-4-20250514"
    email: EmailConfig = field(default_factory=EmailConfig)
    hitl: HITLConfig = field(default_factory=HITLConfig)
    crm: CRMConfig = field(default_factory=CRMConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    follow_up_schedule: list[FollowUpStep] = field(default_factory=lambda: [
        FollowUpStep(number=1, day_offset=3, template="gentle_check_in"),
        FollowUpStep(number=2, day_offset=7, template="add_value"),
        FollowUpStep(number=3, day_offset=14, template="create_urgency"),
        FollowUpStep(number=4, day_offset=21, template="graceful_close"),
    ])
    check_interval_hours: int = 2
    max_follow_ups: int = 4


def load_config(config_path: str) -> AgentConfig:
    """
    Load and parse a deployment YAML config file.

    Args:
        config_path: Path to the YAML config file

    Returns:
        AgentConfig with all settings populated
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw = _process_config_values(raw)

    config = AgentConfig()

    # Client info
    client = raw.get("client", {})
    config.client_name = client.get("name", "")
    config.region = client.get("region", "")
    config.model = raw.get("model", config.model)

    # Email
    email_raw = raw.get("email", {})
    config.email = EmailConfig(
        provider=email_raw.get("provider", "gmail"),
        user_email=email_raw.get("user_email", ""),
        credentials_path=email_raw.get("credentials_path", ""),
        token_path=email_raw.get("token_path", ""),
        tenant_id=email_raw.get("tenant_id", ""),
        client_id=email_raw.get("client_id", ""),
        client_secret=email_raw.get("client_secret", ""),
    )

    # HITL
    hitl_raw = raw.get("hitl", {})
    config.hitl = HITLConfig(
        channel=hitl_raw.get("channel", "console"),
        autonomy_level=hitl_raw.get("autonomy_level", "supervised"),
        value_threshold=float(hitl_raw.get("value_threshold", 5000)),
        always_approve_breakup=hitl_raw.get("always_approve_breakup", True),
        slack_bot_token=hitl_raw.get("slack_bot_token", ""),
        slack_app_token=hitl_raw.get("slack_app_token", ""),
        slack_channel_id=hitl_raw.get("slack_channel_id", ""),
        slack_signing_secret=hitl_raw.get("slack_signing_secret", ""),
        telegram_bot_token=hitl_raw.get("telegram_bot_token", ""),
        telegram_chat_id=hitl_raw.get("telegram_chat_id", ""),
    )

    # CRM
    crm_raw = raw.get("crm", {})
    config.crm = CRMConfig(
        provider=crm_raw.get("provider", "none"),
        api_key=crm_raw.get("api_key", ""),
        enrich_deal_value=crm_raw.get("enrich_deal_value", False),
        log_follow_ups=crm_raw.get("log_follow_ups", False),
    )

    # Storage
    storage_raw = raw.get("storage", {})
    config.storage = StorageConfig(
        db_path=storage_raw.get("db_path", "./data/tracker.db"),
        sheets_id=storage_raw.get("sheets_id", ""),
    )

    # Voice
    voice_raw = raw.get("voice", {})
    config.voice = VoiceConfig(
        tone=voice_raw.get("tone", "professional, warm"),
        language=voice_raw.get("language", "English"),
        company_context=voice_raw.get("company_context", ""),
        user_name=voice_raw.get("user_name", ""),
        company_name=voice_raw.get("company_name", ""),
    )

    # Follow-up schedule
    schedule_raw = raw.get("follow_up", {}).get("schedule", [])
    if schedule_raw:
        config.follow_up_schedule = [
            FollowUpStep(
                number=s.get("number", i + 1),
                day_offset=s.get("day_offset", (i + 1) * 7),
                template=s.get("template", "generic"),
            )
            for i, s in enumerate(schedule_raw)
        ]

    config.check_interval_hours = raw.get("follow_up", {}).get("check_interval_hours", 2)
    config.max_follow_ups = raw.get("follow_up", {}).get("max_follow_ups", 4)

    logger.info(f"Loaded config for client: {config.client_name} ({config.email.provider} / {config.hitl.channel})")
    return config
