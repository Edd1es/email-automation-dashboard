"""Aggregations checked against a small dataset with hand-counted answers."""
from datetime import datetime, timedelta, timezone

import pytest

from app import reports


@pytest.fixture
def dataset(db):
    db.execute(
        "INSERT INTO campaigns (id, name, goal, status, created_at) VALUES (1, 'A', 'g', 'sent', '2026-01-01T00:00:00Z')"
    )
    db.execute(
        "INSERT INTO campaigns (id, name, goal, status, created_at) VALUES (2, 'Empty', 'g', 'draft', '2026-01-02T00:00:00Z')"
    )
    today = datetime.now(timezone.utc)
    stamp = today.strftime("%Y-%m-%dT%H:%M:%SZ")
    statuses = ["sent"] * 5 + ["failed", "ready"]
    for index, status in enumerate(statuses, start=1):
        db.execute(
            "INSERT INTO recipients (id, email, created_at) VALUES (?, ?, ?)",
            (index, f"r{index}@example.com", stamp),
        )
        db.execute(
            """INSERT INTO messages (id, campaign_id, recipient_id, status, tracking_token, sent_at, created_at)
               VALUES (?, 1, ?, ?, ?, ?, ?)""",
            (index, index, status, f"tok{index}", stamp if status == "sent" else None, stamp),
        )
    events = [
        (1, "opened"), (1, "opened"), (1, "opened"),  # repeats count once
        (1, "clicked"), (1, "replied"),
        (2, "opened"),
        (3, "opened"), (3, "replied"),
        (4, "bounced"), (4, "opened"),                # engagement on a bounce is ignored
        (5, "unsubscribed"),
    ]
    for message_id, kind in events:
        db.execute(
            "INSERT INTO engagement_events (message_id, event_type, occurred_at) VALUES (?, ?, ?)",
            (message_id, kind, stamp),
        )
    db.commit()
    return db


def test_campaign_metrics_match_hand_counts(dataset):
    row = reports.campaign(dataset, 1)
    assert (row["total"], row["sent"], row["failed"], row["queued"]) == (7, 5, 1, 1)
    assert (row["bounced"], row["delivered"]) == (1, 4)
    assert (row["opened"], row["clicked"], row["replied"], row["unsubscribed"]) == (3, 1, 2, 1)
    assert row["delivery_rate"] == 0.8
    assert row["opened_rate"] == 0.75
    assert row["clicked_rate"] == 0.25
    assert row["replied_rate"] == 0.5


def test_campaign_with_no_messages_has_null_rates_not_a_crash(dataset):
    row = reports.campaign(dataset, 2)
    assert row["sent"] == 0
    assert row["delivery_rate"] is None
    assert row["replied_rate"] is None


def test_missing_campaign_returns_none(dataset):
    assert reports.campaign(dataset, 999) is None


def test_overview_covers_all_campaigns(dataset):
    row = reports.overview(dataset)
    assert row["campaigns"] == 2
    assert row["sent"] == 5
    assert row["delivered"] == 4


def test_list_includes_empty_campaigns(dataset):
    assert {row["name"] for row in reports.list_campaigns(dataset)} == {"A", "Empty"}


def test_trend_zero_fills_and_counts_distinct_messages(dataset):
    rows = reports.trend(dataset, 7)
    assert len(rows) == 7
    assert rows[0]["sent"] == 0
    assert rows[-1]["day"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Messages 1, 2, 3, 4 opened today; message 1's repeat opens collapse.
    assert rows[-1]["sent"] == 5
    assert rows[-1]["opened"] == 4
    assert rows[-1]["replied"] == 2


def test_trend_filters_by_campaign(dataset):
    assert sum(row["sent"] for row in reports.trend(dataset, 7, 2)) == 0
    assert sum(row["sent"] for row in reports.trend(dataset, 7, 1)) == 5


def test_trend_is_consistent_across_a_day_boundary(dataset):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    dataset.execute(
        "INSERT INTO engagement_events (message_id, event_type, occurred_at) VALUES (1, 'clicked', ?)",
        (yesterday,),
    )
    dataset.commit()
    rows = reports.trend(dataset, 7)
    assert rows[-2]["clicked"] == 1
    assert rows[-1]["clicked"] == 1
