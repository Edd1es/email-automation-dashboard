"""Google Sheets and Gmail access.

Authorize once with `npm run py -- -m app.cli google-auth`, which writes GOOGLE_TOKEN_PATH.
Tests inject fakes through the GOOGLE_SERVICE_FACTORY config value.
"""
import base64
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path

from flask import current_app

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
KNOWN_COLUMNS = {
    "email": "email", "email_address": "email", "e_mail": "email",
    "first_name": "first_name", "firstname": "first_name", "first": "first_name",
    "last_name": "last_name", "lastname": "last_name", "last": "last_name",
    "company": "company", "organization": "company", "organisation": "company",
    "title": "title", "job_title": "title", "role": "title",
}
AUTOMATED_RE = re.compile(r"no-?reply|mailer-daemon|postmaster|notifications?@", re.I)
BOUNCE_SENDER_RE = re.compile(r"mailer-daemon|postmaster", re.I)


class GoogleNotConfigured(Exception):
    pass


def is_configured() -> bool:
    if current_app.config.get("GOOGLE_SERVICE_FACTORY"):
        return True
    return Path(current_app.config["GOOGLE_TOKEN_PATH"]).exists()


def _service(name: str, version: str):
    factory = current_app.config.get("GOOGLE_SERVICE_FACTORY")
    if factory:
        return factory(name, version)
    token = Path(current_app.config["GOOGLE_TOKEN_PATH"])
    if not token.exists():
        raise GoogleNotConfigured(
            "Google is not connected. Run: npm run py -- -m app.cli google-auth"
        )
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json())
    return build(name, version, credentials=creds, cache_discovery=False)


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def parse_sheet_rows(values: list[list[str]]) -> tuple[list[dict], list[str]]:
    """Turn raw sheet values into recipient dicts. Returns (recipients, problems)."""
    if not values:
        return [], ["The sheet range is empty."]
    headers = [_normalize_header(cell) for cell in values[0]]
    mapped = [KNOWN_COLUMNS.get(header) for header in headers]
    if "email" not in mapped:
        return [], ["The first row needs an 'email' column."]
    recipients, problems = [], []
    for number, row in enumerate(values[1:], start=2):
        if not any(str(cell).strip() for cell in row):
            continue
        record = {"first_name": "", "last_name": "", "company": "", "title": "", "attributes": {}}
        for index, header in enumerate(headers):
            if not header:
                continue
            value = str(row[index]).strip() if index < len(row) else ""
            if mapped[index]:
                record[mapped[index]] = value
            elif value:
                record["attributes"][header] = value
        if not EMAIL_RE.match(record.get("email", "")):
            problems.append(f"Row {number}: '{record.get('email', '')}' is not an email address.")
            continue
        recipients.append(record)
    return recipients, problems


def read_sheet(sheet_id: str, sheet_range: str) -> list[list[str]]:
    result = (
        _service("sheets", "v4").spreadsheets().values()
        .get(spreadsheetId=sheet_id, range=sheet_range).execute()
    )
    return result.get("values", [])


def sender_address() -> str:
    return _service("gmail", "v1").users().getProfile(userId="me").execute()["emailAddress"]


def recipients_from_gmail(query: str, limit: int = 200) -> list[dict]:
    """Collect correspondents from messages matching a Gmail search."""
    gmail = _service("gmail", "v1")
    me = sender_address().lower()
    found: dict[str, dict] = {}
    listing = gmail.users().messages().list(userId="me", q=query, maxResults=limit).execute()
    for item in listing.get("messages", []):
        message = gmail.users().messages().get(
            userId="me", id=item["id"], format="metadata",
            metadataHeaders=["From", "To", "Cc"],
        ).execute()
        headers = message.get("payload", {}).get("headers", [])
        values = [h["value"] for h in headers if h["name"].lower() in ("from", "to", "cc")]
        for name, address in getaddresses(values):
            address = address.lower()
            if address == me or not EMAIL_RE.match(address) or AUTOMATED_RE.search(address):
                continue
            parts = name.split()
            found.setdefault(address, {
                "email": address,
                "first_name": parts[0] if parts else "",
                "last_name": " ".join(parts[1:]),
                "company": "", "title": "", "attributes": {},
            })
    return list(found.values())


def send_email(message: EmailMessage) -> dict:
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return _service("gmail", "v1").users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()


def thread_events(thread_id: str, own_message_id: str, me: str) -> list[dict]:
    """Find replies and bounce notices in the thread of a sent message."""
    thread = _service("gmail", "v1").users().threads().get(
        userId="me", id=thread_id, format="metadata", metadataHeaders=["From"]
    ).execute()
    events = []
    for message in thread.get("messages", []):
        if message["id"] == own_message_id:
            continue
        headers = message.get("payload", {}).get("headers", [])
        sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "")
        address = parseaddr(sender)[1].lower()
        if not address or address == me.lower():
            continue
        events.append({
            "type": "bounced" if BOUNCE_SENDER_RE.search(address) else "replied",
            "gmail_id": message["id"],
            "internal_date_ms": int(message.get("internalDate", 0)),
        })
    return events
