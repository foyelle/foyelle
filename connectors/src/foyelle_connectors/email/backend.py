"""IMAP and SMTP access with the stdlib. Injectable: the service takes any ``MailBackend``,
and ``ImapSmtpBackend`` takes factories for its IMAP and SMTP connections."""

from __future__ import annotations

import imaplib
import re
import smtplib
import ssl
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from typing import Any, Protocol

from ..sdk import net
from ..sdk.google import GoogleError, xoauth2


class MailError(RuntimeError):
    """A mail operation failed; the message is safe to show (no secrets)."""


class MailCheckError(MailError):
    """Connecting an account failed at one step: "imap-connect", "imap-login", "smtp-connect", "smtp-login"."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


# Connections through the connector's egress proxy (see sdk.net); TLS stays end to end.
class _IMAP4(imaplib.IMAP4):
    def _create_socket(self, timeout):  # type: ignore[no-untyped-def]
        return net.connect(self.host, self.port, timeout)


class _IMAP4_SSL(imaplib.IMAP4_SSL):
    def _create_socket(self, timeout):  # type: ignore[no-untyped-def]
        return self.ssl_context.wrap_socket(net.connect(self.host, self.port, timeout), server_hostname=self.host)


class _SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):  # type: ignore[no-untyped-def]
        return net.connect(host, port, timeout)


class _SMTP_SSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):  # type: ignore[no-untyped-def]
        return self.context.wrap_socket(net.connect(host, port, timeout), server_hostname=self._host)


_SPECIAL_USE_RE = re.compile(rb'^\(([^)]*)\) (?:"[^"]*"|NIL) (.+)$')


@dataclass
class SearchCriteria:
    text: str | None = None
    from_: str | None = None
    to: str | None = None
    subject: str | None = None
    since: date | None = None
    before: date | None = None
    unseen: bool = False


@dataclass
class RawMessage:
    uid: str
    flags: list[str] = field(default_factory=list)
    size: int = 0
    data: bytes = b""  # headers only (search) or the full message (fetch)


class MailBackend(Protocol):
    def search(self, folder: str, criteria: SearchCriteria, limit: int) -> list[RawMessage]: ...
    def fetch(self, folder: str, uid: str, max_bytes: int) -> RawMessage: ...
    def append(self, folder: str, message: bytes, flags: str) -> str | None: ...
    def delete_draft(self, folder: str, uid: str) -> None: ...
    def send(self, message: EmailMessage) -> None: ...


@dataclass
class MailConfig:
    address: str
    imap_host: str
    imap_port: int = 993
    imap_security: str = "ssl"  # "ssl" | "starttls"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"
    username: str = ""
    smtp_username: str | None = None  # when sending needs a different user name than reading
    auth: str = "password"  # "password" | "xoauth2" (the secret is then a Google access token)
    display_name: str | None = None
    drafts_folder: str = "Drafts"
    sent_folder: str | None = "Sent"
    timeout: float = 30.0


HEADER_FIELDS = "FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES"
_UID_RE = re.compile(rb"UID (\d+)")
_FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")
_SIZE_RE = re.compile(rb"RFC822\.SIZE (\d+)")
_APPENDUID_RE = re.compile(rb"APPENDUID \d+ (\d+)")


def quote(value: str) -> str:
    """IMAP quoted string (RFC 3501). CR and LF are refused."""
    if "\r" in value or "\n" in value:
        raise MailError("search terms must not contain line breaks")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _imap_date(d: date) -> str:
    return f"{d.day}-{('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')[d.month - 1]}-{d.year}"


def search_args(criteria: SearchCriteria) -> tuple[list[str], bytes | None]:
    """IMAP SEARCH keys and an optional UTF-8 literal.

    ASCII terms are sent as quoted strings. imaplib can send one literal per
    command, so at most one term may contain non-ASCII text.
    """
    keys: list[str] = []
    literal_key: str | None = None
    literal: bytes | None = None
    for key, value in (("TEXT", criteria.text), ("FROM", criteria.from_), ("TO", criteria.to), ("SUBJECT", criteria.subject)):
        if not value:
            continue
        if value.isascii():
            keys += [key, quote(value)]
        elif literal_key is None:
            literal_key, literal = key, value.encode("utf-8")
        else:
            raise MailError("only one search term may contain non-ASCII characters")
    if criteria.since:
        keys += ["SINCE", _imap_date(criteria.since)]
    if criteria.before:
        keys += ["BEFORE", _imap_date(criteria.before)]
    if criteria.unseen:
        keys.append("UNSEEN")
    if literal_key is not None:
        # imaplib sends the literal right after the last argument.
        return ["CHARSET", "UTF-8", *keys, literal_key], literal
    return (keys or ["ALL"]), None


def parse_fetch(data: list[Any]) -> list[RawMessage]:
    """Parse an imaplib FETCH response into messages."""
    out = []
    for item in data:
        if not isinstance(item, tuple):
            continue
        meta, payload = item[0], item[1]
        uid = _UID_RE.search(meta)
        if not uid:
            continue
        flags = _FLAGS_RE.search(meta)
        size = _SIZE_RE.search(meta)
        out.append(
            RawMessage(
                uid=uid.group(1).decode(),
                flags=flags.group(1).decode().split() if flags else [],
                size=int(size.group(1)) if size else len(payload),
                data=payload,
            )
        )
    return out


def mailbox(name: str) -> str:
    return quote(name)


def _check(typ: str, data: Any, what: str) -> Any:
    if typ != "OK":
        detail = data[0].decode(errors="replace") if data and isinstance(data[0], bytes) else ""
        raise MailError(f"IMAP {what} failed: {detail}".strip())
    return data


class ImapSmtpBackend:
    def __init__(
        self,
        config: MailConfig,
        password: Callable[[], str],
        *,
        imap_factory: Callable[[], imaplib.IMAP4] | None = None,
        smtp_factory: Callable[[], smtplib.SMTP] | None = None,
    ) -> None:
        self.config = config
        self._password = password
        self._imap_factory = imap_factory or self._connect_imap
        self._smtp_factory = smtp_factory or self._connect_smtp

    def _connect_imap(self) -> imaplib.IMAP4:
        c = self.config
        ctx = ssl.create_default_context()
        if c.imap_security == "ssl":
            return _IMAP4_SSL(c.imap_host, c.imap_port, ssl_context=ctx, timeout=c.timeout)
        conn = _IMAP4(c.imap_host, c.imap_port, timeout=c.timeout)
        conn.starttls(ssl_context=ctx)
        return conn

    def _connect_smtp(self) -> smtplib.SMTP:
        c = self.config
        ctx = ssl.create_default_context()
        if c.smtp_security == "ssl":
            return _SMTP_SSL(c.smtp_host, c.smtp_port, context=ctx, timeout=c.timeout)
        conn = _SMTP(c.smtp_host, c.smtp_port, timeout=c.timeout)
        conn.ehlo()
        conn.starttls(context=ctx)
        conn.ehlo()
        return conn

    @contextmanager
    def _imap(self, folder: str, readonly: bool = True) -> Iterator[imaplib.IMAP4]:
        try:
            conn = self._imap_factory()
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailError(f"cannot connect to the IMAP server: {exc}") from None
        try:
            try:
                self._imap_login(conn)
            except imaplib.IMAP4.error:
                raise MailError("IMAP login failed") from None
            _check(*conn.select(mailbox(folder), readonly=readonly), what=f"select {folder}")
            yield conn
        except imaplib.IMAP4.error as exc:
            raise MailError(f"IMAP error: {exc}") from None
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - best effort
                pass

    def search(self, folder: str, criteria: SearchCriteria, limit: int) -> list[RawMessage]:
        keys, literal = search_args(criteria)
        with self._imap(folder) as conn:
            if literal is not None:
                conn.literal = literal  # type: ignore[attr-defined]
            data = _check(*conn.uid("SEARCH", *keys), what="search")
            uids = sorted((int(u) for u in (data[0] or b"").split()), reverse=True)[:limit]
            if not uids:
                return []
            fetched = _check(
                *conn.uid("FETCH", ",".join(map(str, uids)), f"(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])"),
                what="fetch",
            )
        messages = parse_fetch(fetched)
        messages.sort(key=lambda m: int(m.uid), reverse=True)
        return messages

    def fetch(self, folder: str, uid: str, max_bytes: int) -> RawMessage:
        if not uid.isdigit():
            raise MailError("message id must be a numeric IMAP UID")
        with self._imap(folder) as conn:
            meta = parse_fetch(_check(*conn.uid("FETCH", uid, "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER])"), what="fetch"))
            if not meta:
                raise MailError(f"no message with id {uid} in {folder}")
            if meta[0].size > max_bytes:
                meta[0].flags.append("foyelle:too-large")
                return meta[0]
            full = parse_fetch(_check(*conn.uid("FETCH", uid, "(UID FLAGS RFC822.SIZE BODY.PEEK[])"), what="fetch"))
        if not full:
            raise MailError(f"no message with id {uid} in {folder}")
        return full[0]

    def append(self, folder: str, message: bytes, flags: str) -> str | None:
        with self._imap(folder, readonly=True) as conn:
            typ, data = conn.append(mailbox(folder), flags, imaplib.Time2Internaldate(time.time()), message)
            _check(typ, data, what=f"append to {folder}")
            match = _APPENDUID_RE.search(data[0] or b"") if data and isinstance(data[0], bytes) else None
            if match:
                return match.group(1).decode()
            # No UIDPLUS: find it by Message-ID.
            msgid = re.search(rb"^Message-ID:\s*(\S+)", message, re.IGNORECASE | re.MULTILINE)
            if msgid:
                conn.select(mailbox(folder), readonly=True)
                found = _check(*conn.uid("SEARCH", "HEADER", "Message-ID", quote(msgid.group(1).decode())), what="search")
                uids = (found[0] or b"").split()
                if uids:
                    return uids[-1].decode()
        return None

    def delete_draft(self, folder: str, uid: str) -> None:
        if not uid.isdigit():
            raise MailError("draft id must be a numeric IMAP UID")
        with self._imap(folder, readonly=False) as conn:
            meta = parse_fetch(_check(*conn.uid("FETCH", uid, "(UID FLAGS)"), what="fetch"))
            if not meta:
                raise MailError(f"no draft with id {uid}")
            if "\\Draft" not in meta[0].flags:
                raise MailError(f"message {uid} is not a draft; only drafts can be deleted")
            _check(*conn.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)"), what="store")
            caps = getattr(conn, "capabilities", ())
            if "UIDPLUS" in caps:
                _check(*conn.uid("EXPUNGE", uid), what="expunge")
            else:
                _check(*conn.expunge(), what="expunge")

    def send(self, message: EmailMessage) -> None:
        try:
            conn = self._smtp_factory()
        except OSError as exc:
            raise MailError(f"cannot connect to the SMTP server: {exc}") from None
        try:
            try:
                self._smtp_login(conn)
            except smtplib.SMTPException:
                raise MailError("SMTP login failed") from None
            try:
                refused = conn.send_message(message)
            except smtplib.SMTPException as exc:
                raise MailError(f"SMTP refused the message: {exc}") from None
            if refused:
                raise MailError(f"some recipients were refused: {', '.join(refused)}")
        finally:
            try:
                conn.quit()
            except Exception:  # noqa: BLE001
                pass

    def _secret(self) -> str:
        try:
            return self._password()
        except GoogleError as exc:
            raise MailError(str(exc)) from None

    def _imap_login(self, conn: imaplib.IMAP4) -> None:
        user = self.config.username or self.config.address
        if self.config.auth == "xoauth2":
            token = self._secret()
            conn.authenticate("XOAUTH2", lambda _: xoauth2(user, token).encode())
        else:
            conn.login(user, self._password())

    def _smtp_login(self, conn: smtplib.SMTP) -> None:
        if self.config.auth == "xoauth2":
            token = self._secret()
            conn.ehlo_or_helo_if_needed()
            conn.auth("XOAUTH2", lambda challenge=None: xoauth2(self._smtp_user(), token), initial_response_ok=True)
        else:
            conn.login(self._smtp_user(), self._password())

    def _smtp_user(self) -> str:
        c = self.config
        return c.smtp_username or c.username or c.address

    def check(self) -> dict[str, str | None]:
        """Log in to both servers without changing anything, and find the Drafts and Sent folders
        (IMAP special-use flags, RFC 6154). Raises MailCheckError naming the step that failed."""
        try:
            conn = self._imap_factory()
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailCheckError("imap-connect", f"cannot reach {self.config.imap_host}: {exc}") from None
        folders: dict[str, str | None] = {"drafts": None, "sent": None}
        try:
            try:
                self._imap_login(conn)
            except imaplib.IMAP4.error:
                raise MailCheckError("imap-login", "the mail server did not accept the user name and password") from None
            try:
                typ, data = conn.list()
            except imaplib.IMAP4.error:
                typ, data = "NO", []
            if typ == "OK":
                folders = special_use(data)
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
        try:
            smtp = self._smtp_factory()
        except OSError as exc:
            raise MailCheckError("smtp-connect", f"cannot reach {self.config.smtp_host}: {exc}") from None
        try:
            try:
                self._smtp_login(smtp)
            except smtplib.SMTPException:
                raise MailCheckError("smtp-login", "the sending server did not accept the user name and password") from None
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001
                pass
        return folders


def special_use(lines: list[Any]) -> dict[str, str | None]:
    """The Drafts and Sent folders from IMAP LIST lines with special-use flags."""
    found: dict[str, str | None] = {"drafts": None, "sent": None}
    for line in lines or []:
        if not isinstance(line, bytes):
            continue
        m = _SPECIAL_USE_RE.match(line)
        if not m:
            continue
        flags = m.group(1).lower().split()
        name = m.group(2).strip()
        if name.startswith(b'"') and name.endswith(b'"'):
            name = name[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
        text = name.decode("utf-8", "replace")
        if b"\\drafts" in flags and not found["drafts"]:
            found["drafts"] = text
        if b"\\sent" in flags and not found["sent"]:
            found["sent"] = text
    return found
