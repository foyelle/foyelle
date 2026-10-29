"""Mail and calendar providers Foyelle knows how to connect with an app password.

Each preset is what a person would otherwise have to look up: the servers, how the user name is
written, and where to make an app password. "other" takes the servers from the person instead.
Folder names are found on the server (IMAP special-use flags), so they are not listed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Server:
    host: str
    port: int
    security: str  # "ssl" (TLS from the start) | "starttls"


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    imap: Server | None
    smtp: Server | None
    # CalDAV start URL ({address} is filled in); None when the provider has no CalDAV.
    caldav: str | None
    # User names to try in order ({address}, {local} = the part before @); the first that works is kept.
    usernames: tuple[str, ...] = ("{address}",)
    # The server keeps a copy of sent mail by itself (saving one too would make two).
    saves_sent: bool = False
    # Where to make an app password, and how, in plain words.
    app_password_url: str | None = None
    app_password_steps: tuple[str, ...] = field(default_factory=tuple)
    # Something the person should know before starting (shown on the card).
    note: str | None = None


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            id="gmail",
            name="Gmail",
            imap=Server("imap.gmail.com", 993, "ssl"),
            smtp=Server("smtp.gmail.com", 465, "ssl"),
            # Google's CalDAV accepts only OAuth (its own sign-in), never an app password, so Gmail
            # connects mail only for now.
            caldav=None,
            saves_sent=True,
            note="Google Calendar needs Google's own sign-in, which Foyelle doesn't have yet. Mail works with an app password.",
            app_password_url="https://myaccount.google.com/apppasswords",
            app_password_steps=(
                "Sign in to your Google Account. 2-Step Verification must be on.",
                "Open App passwords, type Foyelle as the name, and choose Create.",
                "Copy the 16-letter password it shows. It works only for Foyelle, and you can remove it any time.",
            ),
        ),
        Provider(
            id="icloud",
            name="iCloud",
            imap=Server("imap.mail.me.com", 993, "ssl"),
            smtp=Server("smtp.mail.me.com", 587, "starttls"),
            caldav="https://caldav.icloud.com/",
            usernames=("{address}", "{local}"),
            app_password_url="https://account.apple.com",
            app_password_steps=(
                "Sign in to your Apple Account.",
                "Open Sign-In and Security, then App-Specific Passwords, and add one called Foyelle.",
                "Copy the password it shows. Apple shows it only once.",
            ),
        ),
        Provider(
            id="fastmail",
            name="Fastmail",
            imap=Server("imap.fastmail.com", 993, "ssl"),
            smtp=Server("smtp.fastmail.com", 465, "ssl"),
            caldav="https://caldav.fastmail.com/dav/",
            app_password_url="https://app.fastmail.com/settings/security",
            app_password_steps=(
                "Sign in to Fastmail and open Settings, then Privacy & Security.",
                "Make a new app password called Foyelle, with access to Mail, Contacts & Calendars.",
                "Copy the password it shows.",
            ),
        ),
        Provider(
            id="other",
            name="Other",
            imap=None,
            smtp=None,
            caldav=None,
            app_password_steps=(
                "Your mail provider's help pages list the IMAP and SMTP servers.",
                "If it offers app passwords, make one for Foyelle; otherwise use your mail password.",
            ),
        ),
    )
}


def describe(provider: Provider) -> dict:
    """What the Accounts card shows for a provider (no secrets here)."""
    return {
        "id": provider.id,
        "name": provider.name,
        "mail": provider.imap is not None,
        "calendar": provider.caldav is not None,
        "app_password_url": provider.app_password_url,
        "app_password_steps": list(provider.app_password_steps),
        "note": provider.note,
    }


def usernames(provider: Provider, address: str) -> list[str]:
    local = address.partition("@")[0]
    out: list[str] = []
    for template in provider.usernames:
        name = template.format(address=address, local=local)
        if name and name not in out:
            out.append(name)
    return out
