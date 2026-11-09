"""Aggregation queries behind delivery and performance reporting.

Definitions, used everywhere:
  sent       messages Gmail accepted
  delivered  sent messages with no bounce event
  opened / clicked / replied   distinct delivered messages with that event
  delivery_rate = delivered / sent; all other rates are out of delivered
"""
import sqlite3

# One row per message, with each engagement type collapsed to a 0/1 flag, so a
# recipient who opens five times counts once. A bounced message cannot count
# as engaged.
_PER_MESSAGE = """
    per_message AS (
        SELECT m.id, m.campaign_id, m.status,
               COALESCE(MAX(e.event_type = 'bounced'), 0)      AS bounced,
               COALESCE(MAX(e.event_type = 'opened'), 0)       AS opened,
               COALESCE(MAX(e.event_type = 'clicked'), 0)      AS clicked,
               COALESCE(MAX(e.event_type = 'replied'), 0)      AS replied,
               COALESCE(MAX(e.event_type = 'unsubscribed'), 0) AS unsubscribed
        FROM messages m
        LEFT JOIN engagement_events e ON e.message_id = m.id
        GROUP BY m.id
    )
"""

_TOTALS = """
    COUNT(pm.id)                                                       AS total,
    COALESCE(SUM(pm.status = 'sent'), 0)                               AS sent,
    COALESCE(SUM(pm.status IN ('failed', 'generation_failed')), 0)     AS failed,
    COALESCE(SUM(pm.status IN ('pending', 'ready', 'sending')), 0)     AS queued,
    COALESCE(SUM(pm.status = 'sent' AND pm.bounced), 0)                AS bounced,
    COALESCE(SUM(pm.status = 'sent' AND NOT pm.bounced AND pm.opened), 0)  AS opened,
    COALESCE(SUM(pm.status = 'sent' AND NOT pm.bounced AND pm.clicked), 0) AS clicked,
    COALESCE(SUM(pm.status = 'sent' AND NOT pm.bounced AND pm.replied), 0) AS replied,
    COALESCE(SUM(pm.status = 'sent' AND pm.unsubscribed), 0)           AS unsubscribed
"""


def _with_rates(row: sqlite3.Row) -> dict:
    data = dict(row)
    sent, bounced = data["sent"], data["bounced"]
    delivered = sent - bounced
    data["delivered"] = delivered

    def rate(part, whole):
        return round(part / whole, 4) if whole else None

    data["delivery_rate"] = rate(delivered, sent)
    for name in ("opened", "clicked", "replied", "unsubscribed"):
        data[f"{name}_rate"] = rate(data[name], delivered)
    return data


def overview(db: sqlite3.Connection) -> dict:
    row = db.execute(
        f"""
        WITH {_PER_MESSAGE}
        SELECT {_TOTALS},
               (SELECT COUNT(*) FROM campaigns) AS campaigns,
               (SELECT COUNT(*) FROM campaigns WHERE is_sample = 1) AS sample_campaigns,
               (SELECT MAX(occurred_at) FROM engagement_events) AS last_event_at
        FROM per_message pm
        """
    ).fetchone()
    return _with_rates(row)


def list_campaigns(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        f"""
        WITH {_PER_MESSAGE}
        SELECT c.id, c.name, c.goal, c.subject_hint, c.status, c.is_sample,
               c.sheet_id, c.sheet_range, c.created_at, c.started_at, {_TOTALS}
        FROM campaigns c
        LEFT JOIN per_message pm ON pm.campaign_id = c.id
        GROUP BY c.id
        ORDER BY COALESCE(c.started_at, c.created_at) DESC, c.id DESC
        """
    ).fetchall()
    return [_with_rates(row) for row in rows]


def campaign(db: sqlite3.Connection, campaign_id: int) -> dict | None:
    row = db.execute(
        f"""
        WITH {_PER_MESSAGE}
        SELECT c.id, c.name, c.goal, c.subject_hint, c.status, c.is_sample,
               c.sheet_id, c.sheet_range, c.created_at, c.started_at, {_TOTALS}
        FROM campaigns c
        LEFT JOIN per_message pm ON pm.campaign_id = c.id
        WHERE c.id = ?
        GROUP BY c.id
        """,
        (campaign_id,),
    ).fetchone()
    return _with_rates(row) if row else None


def trend(db: sqlite3.Connection, days: int, campaign_id: int | None = None) -> list[dict]:
    """Daily counts over the last `days` UTC days, with zero-filled gaps.

    Each series counts distinct messages per day, by the day the thing happened.
    """
    rows = db.execute(
        """
        WITH RECURSIVE calendar (day) AS (
            SELECT date('now', printf('-%d days', :days - 1))
            UNION ALL
            SELECT date(day, '+1 day') FROM calendar WHERE day < date('now')
        ),
        sent AS (
            SELECT substr(sent_at, 1, 10) AS day, COUNT(*) AS n
            FROM messages
            WHERE status = 'sent' AND (:campaign IS NULL OR campaign_id = :campaign)
            GROUP BY day
        ),
        events AS (
            SELECT substr(e.occurred_at, 1, 10) AS day, e.event_type,
                   COUNT(DISTINCT e.message_id) AS n
            FROM engagement_events e
            JOIN messages m ON m.id = e.message_id
            WHERE :campaign IS NULL OR m.campaign_id = :campaign
            GROUP BY day, e.event_type
        )
        SELECT c.day,
               COALESCE(s.n, 0) AS sent,
               COALESCE(SUM(CASE WHEN e.event_type = 'opened'  THEN e.n END), 0) AS opened,
               COALESCE(SUM(CASE WHEN e.event_type = 'clicked' THEN e.n END), 0) AS clicked,
               COALESCE(SUM(CASE WHEN e.event_type = 'replied' THEN e.n END), 0) AS replied,
               COALESCE(SUM(CASE WHEN e.event_type = 'bounced' THEN e.n END), 0) AS bounced
        FROM calendar c
        LEFT JOIN sent s ON s.day = c.day
        LEFT JOIN events e ON e.day = c.day
        GROUP BY c.day
        ORDER BY c.day
        """,
        {"days": days, "campaign": campaign_id},
    ).fetchall()
    return [dict(row) for row in rows]


def list_messages(db: sqlite3.Connection, campaign_id: int, limit: int, offset: int) -> dict:
    rows = db.execute(
        """
        SELECT m.id, m.subject, m.body, m.status, m.generated_by, m.error, m.sent_at,
               r.email, r.first_name, r.last_name, r.company, r.title,
               COALESCE(MAX(e.event_type = 'bounced'), 0) AS bounced,
               COALESCE(MAX(e.event_type = 'opened'), 0)  AS opened,
               COALESCE(MAX(e.event_type = 'clicked'), 0) AS clicked,
               COALESCE(MAX(e.event_type = 'replied'), 0) AS replied
        FROM messages m
        JOIN recipients r ON r.id = m.recipient_id
        LEFT JOIN engagement_events e ON e.message_id = m.id
        WHERE m.campaign_id = ?
        GROUP BY m.id
        ORDER BY m.id
        LIMIT ? OFFSET ?
        """,
        (campaign_id, limit, offset),
    ).fetchall()
    total = db.execute(
        "SELECT COUNT(*) FROM messages WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()[0]
    return {"items": [dict(row) for row in rows], "total": total}
