"""Email tool logic on top of a ``MailBackend``: header summaries, readable bodies, drafts."""

from __future__ import annotations

from datetime import date
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parsedate_to_datetime
from typing import Any

from ..sdk.htmltext import html_to_text
from .backend import MailBackend, MailConfig, MailError, RawMessage, SearchCriteria

MAX_TEXT_CHARS = 100_000
_parser = BytesParser(policy=policy.default)


def _header(msg: Message, name: str) -> str:
    value = msg.get(name)
    return str(value) if value is not None else ""


def _iso_date(value: str) -> str | None:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        return None


def summarize(raw: RawMessage, folder: str) -> dict[str, Any]:
    msg = _parser.parsebytes(raw.data, headersonly=True)
    return {
        "id": raw.uid,
        "folder": folder,
        "from": _header(msg, "From"),
        "to": _header(msg, "To"),
        "cc": _header(msg, "Cc"),
        "subject": _header(msg, "Subject"),
        "date": _iso_date(_header(msg, "Date")) or _header(msg, "Date"),
        "message_id": _header(msg, "Message-ID"),
        "unread": "\\Seen" not in raw.flags,
        "flags": raw.flags,
        "size": raw.size,
    }


def body_text(msg: EmailMessage) -> tuple[str, str]:
    """Readable text of a message and where it came from ("plain" or "html")."""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return "", "none"
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        content = part.get_payload(decode=True) or b""
        content = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
    if part.get_content_subtype() == "html":
        return html_to_text(content).text, "html"
    return content.strip(), "plain"


def attachments(msg: EmailMessage) -> list[dict[str, Any]]:
    out = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        out.append(
            {
                "filename": part.get_filename() or "",
                "content_type": part.get_content_type(),
                "size": len(payload) if isinstance(payload, bytes) else None,
            }
        )
    return out


def _addresses(values: list[str] | None, field: str) -> list[str]:
    out = []
    for value in values or []:
        if "\r" in value or "\n" in value:
            raise MailError(f"{field}: line breaks are not allowed")
        for name, addr in getaddresses([value]):
            if not addr or "@" not in addr or addr.count("@") != 1:
                raise MailError(f"{field}: {value!r} is not an email address")
            out.append(formataddr((name, addr)))
    return out


class EmailService:
    def __init__(self, backend: MailBackend, config: MailConfig, *, max_fetch: int = 20 * 1024 * 1024) -> None:
        self.backend = backend
        self.config = config
        self.max_fetch = max_fetch

    def search(
        self,
        query: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        subject: str | None = None,
        since: date | None = None,
        before: date | None = None,
        unread_only: bool = False,
        folder: str = "INBOX",
        limit: int = 25,
    ) -> dict[str, Any]:
        criteria = SearchCriteria(query, from_, to, subject, since, before, unread_only)
        raws = self.backend.search(folder, criteria, max(1, min(limit, 100)))
        return {"folder": folder, "messages": [summarize(r, folder) for r in raws]}

    def read(self, message_id: str, folder: str = "INBOX") -> dict[str, Any]:
        raw = self.backend.fetch(folder, message_id, self.max_fetch)
        out = summarize(raw, folder)
        if "foyelle:too-large" in raw.flags:
            out["flags"] = [f for f in raw.flags if f != "foyelle:too-large"]
            return out | {"text": "", "truncated": True, "attachments": [],
                          "note": f"message is {raw.size} bytes, over the {self.max_fetch}-byte limit; headers only"}
        msg = _parser.parsebytes(raw.data)
        assert isinstance(msg, EmailMessage)
        text, source = body_text(msg)
        truncated = len(text) > MAX_TEXT_CHARS
        return out | {
            "text": text[:MAX_TEXT_CHARS],
            "text_source": source,
            "truncated": truncated,
            "attachments": attachments(msg),
            "in_reply_to": _header(msg, "In-Reply-To"),
            "references": _header(msg, "References"),
        }

    def compose(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> EmailMessage:
        msg = EmailMessage(policy=policy.SMTP)
        domain = self.config.address.rpartition("@")[2] or None
        sender = Address(self.config.display_name or "", addr_spec=self.config.address)
        msg["From"] = sender
        recipients = _addresses(to, "to")
        if not recipients:
            raise MailError("at least one recipient is required")
        msg["To"] = ", ".join(recipients)
        if cc_list := _addresses(cc, "cc"):
            msg["Cc"] = ", ".join(cc_list)
        if bcc_list := _addresses(bcc, "bcc"):
            msg["Bcc"] = ", ".join(bcc_list)
        if "\n" in subject or "\r" in subject:
            raise MailError("subject must be one line")
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=domain)
        msg["Date"] = formatdate(usegmt=True)
        for name, value in (("In-Reply-To", in_reply_to), ("References", references)):
            if value:
                if "\n" in value or "\r" in value:
                    raise MailError(f"{name} must be one line")
                msg[name] = value
        msg.set_content(body)
        return msg

    def draft(self, **fields: Any) -> dict[str, Any]:
        msg = self.compose(**fields)
        uid = self.backend.append(self.config.drafts_folder, msg.as_bytes(), "(\\Draft \\Seen)")
        return {
            "draft_id": uid,
            "folder": self.config.drafts_folder,
            "message_id": msg["Message-ID"],
            "to": fields.get("to"),
            "cc": fields.get("cc") or [],
            "bcc": fields.get("bcc") or [],
            "subject": fields.get("subject"),
            "body": fields.get("body"),
            "in_reply_to": fields.get("in_reply_to"),
            "references": fields.get("references"),
        }

    def send(self, draft_id: str | None = None, **fields: Any) -> dict[str, Any]:
        msg = self.compose(**fields)
        self.backend.send(msg)
        result: dict[str, Any] = {"sent": True, "message_id": msg["Message-ID"], "to": fields.get("to")}
        del msg["Bcc"]
        if self.config.sent_folder:
            try:
                self.backend.append(self.config.sent_folder, msg.as_bytes(), "(\\Seen)")
                result["saved_to"] = self.config.sent_folder
            except MailError as exc:
                result["warning"] = f"sent, but not saved to {self.config.sent_folder}: {exc}"
        if draft_id:
            try:
                self.backend.delete_draft(self.config.drafts_folder, draft_id)
                result["draft_deleted"] = draft_id
            except MailError as exc:
                result["warning"] = f"sent, but the draft was not deleted: {exc}"
        return result

    def delete_draft(self, draft_id: str) -> dict[str, Any]:
        self.backend.delete_draft(self.config.drafts_folder, draft_id)
        return {"deleted": draft_id, "folder": self.config.drafts_folder}
