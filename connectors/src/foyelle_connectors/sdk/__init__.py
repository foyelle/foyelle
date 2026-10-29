"""Shared connector SDK: server factory, config, credentials, views, logging."""

from .config import ConfigError, env, env_int, env_path, env_required, read_credential, require_credential
from .log import get_logger, log_event, setup_logging
from .server import MCP_PATH, PORTS, annotations, create_server, run, ui_meta
from .ui import RESOURCE_MIME_TYPE, register_html_page, register_page, register_view

__all__ = [
    "MCP_PATH",
    "PORTS",
    "RESOURCE_MIME_TYPE",
    "ConfigError",
    "annotations",
    "create_server",
    "env",
    "env_int",
    "env_path",
    "env_required",
    "get_logger",
    "log_event",
    "read_credential",
    "register_html_page",
    "register_page",
    "register_view",
    "require_credential",
    "run",
    "setup_logging",
    "ui_meta",
]
