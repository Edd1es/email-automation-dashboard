"""Campaign workflow: import, generate, send, sync engagement, produce insight."""
import html
import json
import re
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage

from flask import current_app
from itsdangerous import URLSafeSerializer

from . import google_service, groq_service, reports
from .db import new_token, utcnow

# Matches URLs in already-escaped text, stopping at escaped quotes and brackets.
ESCAPED_URL_RE = re.compile(r"https?://(?:(?!&quot;|&#x27;|&lt;|&gt;)[^\s)\]])+")


def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="click-link")


def sign_click(token: str, url: str) -> str:
    return _signer().dumps({"t": token, "u": url})


def read_click(signed: str) -> dict:
    """Raises itsdangerous.BadSignature, which stops this being an open redirect."""
    return _signer().loads(signed)


def add_recipients(db: sqlite3.Connection, campaign_id: int, recipients: list[dict], source: str) -> int:
    """Upsert recipients and create one pending message each. Returns new messages."""
    now, added = utcnow(), 0
    for person in recipients:
        db.execute(
            """
            INSERT INTO recipients
                (email, first_name, last_name, company, title, attributes, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (email) DO UPDATE SET
                first_name = COALESCE(NULLIF(excluded.first_name, ''), first_name),
                last_name  = COALESCE(NULLIF(excluded.last_name, ''), last_name),
                company    = COALESCE(NULLIF(excluded.company, ''), company),
                title      = COALESCE(NULLIF(excluded.title, ''), title),
                attributes = excluded.attributes
            """,
            (
                person["email"], person["first_name"], person["last_name"], person["company"],
                person["title"], json.dumps(person["attributes"]), source, now,
            ),
        )
        recipient_id = db.execute(
            "SELECT id FROM recipients WHERE email = ?", (person["email"],)
        ).fetchone()[0]
        added += db.execute(
            """
            INSERT OR IGNORE INTO messages (campaign_id, recipient_id, tracking_token, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (campaign_id, recipient_id, new_token(), now),
        ).rowcount
    db.commit()
    return added


def generate_messages(db: sqlite3.Connection, campaign: dict) -> dict:
    """Write messages for pending and previously failed rows. One failure never stops the rest."""
    rows = db.execute(
        """
        SELECT m.id AS message_id, r.* FROM messages m
        JOIN recipients r ON r.id = m.recipient_id
        WHERE m.campaign_id = ? AND m.status IN ('pending', 'generation_failed')
        ORDER BY m.id
        """,
        (campaign["id"],),
    ).fetchall()
    generated = failed = 0
    for row in rows:
        try:
            content, generated_by = groq_service.personalize(campaign, dict(row))
        except groq_service.GenerationError as exc:
            db.execute(
                "UPDATE messages SET status = 'generation_failed', error = ? WHERE id = ?",
                (str(exc), row["message_id"]),
            )
            failed += 1
        else:
            db.execute(
                """
                UPDATE messages
                SET subject = ?, body = ?, generated_by = ?, status = 'ready', error = NULL
                WHERE id = ?
                """,
                (content["subject"], content["body"], generated_by, row["message_id"]),
            )
            generated += 1
        db.commit()
    return {"generated": generated, "failed": failed}


def build_email(message: dict, sender: str) -> EmailMessage:
    """Build the MIME email: tracked links and a pixel in HTML, plus an unsubscribe link."""
    base = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    token = message["tracking_token"]
    unsubscribe = f"{base}/t/u/{token}"
    address = current_app.config.get("SENDER_POSTAL_ADDRESS", "")

    email = EmailMessage()
    email["To"] = message["email"]
    email["From"] = sender
    email["Subject"] = message["subject"]
    email["List-Unsubscribe"] = f"<{unsubscribe}>"
    email["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    footer = f"Unsubscribe: {unsubscribe}"
    if address:
        footer = f"{address}\n{footer}"
    email.set_content(f"{message['body']}\n\n--\n{footer}\n")

    def link(match: re.Match) -> str:
        # The body is already escaped here, so undo that for the real URL.
        url = html.unescape(match.group(0))
        target = f"{base}/t/c/{sign_click(token, url)}"
        return f'<a href="{html.escape(target)}">{match.group(0)}</a>'

    escaped = html.escape(message["body"], quote=True)
    paragraphs = "".join(
        f"<p>{ESCAPED_URL_RE.sub(link, part).replace(chr(10), '<br>')}</p>"
        for part in escaped.split("\n\n")
    )
    address_html = f"{html.escape(address)}<br>" if address else ""
    email.add_alternative(
        "<!doctype html><html><body>"
        f"{paragraphs}"
        f'<p style="font-size:12px;color:#666">{address_html}'
        f'<a href="{html.escape(unsubscribe)}">Unsubscribe</a></p>'
        f'<img src="{html.escape(base)}/t/o/{token}.gif" width="1" height="1" alt="">'
        "</body></html>",
        subtype="html",
    )
    return email


def send_campaign(db: sqlite3.Connection, campaign: dict) -> dict:
    """Send ready messages, up to MAX_SEND_PER_RUN.

    Each row is claimed ('ready' to 'sending') before the Gmail call, so two
    overlapping runs cannot double-send. A crash mid-call leaves the row in
    'sending', and it is never retried automatically.
    """
    limit = current_app.config["MAX_SEND_PER_RUN"]
    sender = google_service.sender_address()
    skipped = db.execute(
        """
        UPDATE messages SET status = 'skipped', error = 'Recipient unsubscribed'
        WHERE campaign_id = ? AND status = 'ready' AND recipient_id IN
            (SELECT id FROM recipients WHERE unsubscribed_at IS NOT NULL)
        """,
        (campaign["id"],),
    ).rowcount
    rows = db.execute(
        """
        SELECT m.id, m.subject, m.body, m.tracking_token, r.email
        FROM messages m JOIN recipients r ON r.id = m.recipient_id
        WHERE m.campaign_id = ? AND m.status = 'ready'
        ORDER BY m.id LIMIT ?
        """,
        (campaign["id"], limit),
    ).fetchall()
    db.execute(
        """
        UPDATE campaigns SET status = 'sending', started_at = COALESCE(started_at, ?)
        WHERE id = ?
        """,
        (utcnow(), campaign["id"]),
    )
    db.commit()

    sent = failed = 0
    for row in rows:
        claimed = db.execute(
            "UPDATE messages SET status = 'sending' WHERE id = ? AND status = 'ready'",
            (row["id"],),
        ).rowcount
        db.commit()
        if not claimed:
            continue
        try:
            result = google_service.send_email(build_email(dict(row), sender))
        except Exception as exc:  # one bad address must not stop the batch
            current_app.logger.warning("Send failed for message %s: %s", row["id"], type(exc).__name__)
            db.execute(
                "UPDATE messages SET status = 'failed', error = ? WHERE id = ?",
                (f"Gmail rejected this message: {type(exc).__name__}", row["id"]),
            )
            failed += 1
        else:
            db.execute(
                """
                UPDATE messages
                SET status = 'sent', sent_at = ?, gmail_message_id = ?, gmail_thread_id = ?, error = NULL
                WHERE id = ?
                """,
                (utcnow(), result["id"], result.get("threadId"), row["id"]),
            )
            sent += 1
        db.commit()

    remaining = db.execute(
        "SELECT COUNT(*) FROM messages WHERE campaign_id = ? AND status = 'ready'",
        (campaign["id"],),
    ).fetchone()[0]
    if not remaining:
        db.execute("UPDATE campaigns SET status = 'sent' WHERE id = ?", (campaign["id"],))
        db.commit()
    return {"sent": sent, "failed": failed, "skipped": skipped, "remaining": remaining}


def record_event(db: sqlite3.Connection, message_id: int, event_type: str,
                 occurred_at: str | None = None, detail: str | None = None,
                 dedupe_key: str | None = None) -> bool:
    inserted = db.execute(
        """
        INSERT OR IGNORE INTO engagement_events
            (message_id, event_type, occurred_at, detail, dedupe_key)
        VALUES (?, ?, ?, ?, ?)
        """,
        (message_id, event_type, occurred_at or utcnow(), detail, dedupe_key),
    ).rowcount
    db.commit()
    return bool(inserted)


def sync_engagement(db: sqlite3.Connection, campaign_id: int) -> dict:
    """Read each sent message's Gmail thread for replies and bounces."""
    me = google_service.sender_address()
    rows = db.execute(
        """
        SELECT id, gmail_message_id, gmail_thread_id FROM messages
        WHERE campaign_id = ? AND status = 'sent' AND gmail_thread_id IS NOT NULL
        """,
        (campaign_id,),
    ).fetchall()
    counts = {"replied": 0, "bounced": 0, "checked": len(rows)}
    for row in rows:
        for event in google_service.thread_events(row["gmail_thread_id"], row["gmail_message_id"], me):
            occurred = None
            if event["internal_date_ms"]:
                occurred = datetime.fromtimestamp(
                    event["internal_date_ms"] / 1000, timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            if record_event(db, row["id"], event["type"], occurred,
                            dedupe_key=f"gmail:{event['gmail_id']}"):
                counts[event["type"]] += 1
    return counts


def create_insight(db: sqlite3.Connection, campaign_id: int) -> dict:
    metrics = reports.campaign(db, campaign_id)
    observations, generated_by = groq_service.generate_insights(metrics, reports.overview(db))
    now = utcnow()
    db.execute(
        "INSERT INTO insights (campaign_id, content, generated_by, created_at) VALUES (?, ?, ?, ?)",
        (campaign_id, json.dumps(observations), generated_by, now),
    )
    db.commit()
    return {"observations": observations, "generated_by": generated_by, "created_at": now}


def latest_insight(db: sqlite3.Connection, campaign_id: int) -> dict | None:
    row = db.execute(
        """
        SELECT content, generated_by, created_at FROM insights
        WHERE campaign_id = ? ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (campaign_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "observations": json.loads(row["content"]),
        "generated_by": row["generated_by"],
        "created_at": row["created_at"],
    }
