from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from foyelle_connectors import sdk
from foyelle_connectors.sdk.htmltext import html_to_text, strip_tags
from foyelle_connectors.sdk.log import JsonFormatter


def test_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    assert sdk.read_credential("email-password") is None
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    (tmp_path / "email-password").write_text("s3cret\n")
    assert sdk.read_credential("email-password") == "s3cret"
    with pytest.raises(sdk.ConfigError, match="caldav-password"):
        sdk.require_credential("caldav-password")


def test_server_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOYELLE_CONNECTOR_PORT", raising=False)
    monkeypatch.delenv("FOYELLE_CONNECTOR_HOST", raising=False)
    server = sdk.create_server("web", "test")
    assert (server.settings.host, server.settings.port, server.settings.streamable_http_path) == ("127.0.0.1", 7304, "/mcp")
    assert server.settings.transport_security.enable_dns_rebinding_protection
    monkeypatch.setenv("FOYELLE_CONNECTOR_PORT", "9000")
    monkeypatch.setenv("FOYELLE_CONNECTOR_HOST", "127.0.0.2")
    server = sdk.create_server("web", "test")
    assert (server.settings.host, server.settings.port) == ("127.0.0.2", 9000)
    monkeypatch.setenv("FOYELLE_CONNECTOR_PORT", "x")
    with pytest.raises(sdk.ConfigError):
        sdk.create_server("web", "test")


def test_annotations() -> None:
    read = sdk.annotations(read_only=True, destructive=True)
    assert (read.readOnlyHint, read.destructiveHint, read.openWorldHint, read.idempotentHint) == (True, False, False, True)
    send = sdk.annotations(read_only=False, open_world=True)
    assert (send.readOnlyHint, send.destructiveHint, send.openWorldHint) == (False, False, True)
    assert sdk.ui_meta("ui://x/y") == {"ui": {"resourceUri": "ui://x/y"}}


def test_register_view_rejects_other_schemes() -> None:
    server = sdk.create_server("shell", "t", port=0)
    with pytest.raises(ValueError):
        sdk.register_view(server, "x", "https://example.com/x", "<html>")


def test_html_to_text() -> None:
    page = html_to_text(
        "<html><head><title>T</title><style>.a{}</style></head><body><nav>skip</nav>"
        "<ul><li>one<li>two</ul><pre>  keep\n  spacing</pre><p>a &amp; b<br>c</p></body></html>",
        base_url="https://example.com/dir/",
    )
    assert page.title == "T"
    assert page.text == "- one\n- two\n\n  keep\n  spacing\n\na & b\nc"
    assert strip_tags("<b>x</b>  y") == "x y"


def test_json_logging() -> None:
    record = logging.LogRecord("foyelle.t", logging.INFO, "f", 1, "built", (), None)
    record.event = "built"
    record.proposal = "p1"
    line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "built" and line["proposal"] == "p1" and line["level"] == "info"
