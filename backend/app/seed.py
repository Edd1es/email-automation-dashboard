"""Load clearly labelled sample data so the dashboard can be explored offline.

All addresses use the reserved example.com domain, and sample campaigns are
marked is_sample = 1, which the API refuses to send, import into, or sync.
"""
import random
from datetime import datetime, timedelta, timezone

from .db import connect, init_db, new_token

FIRST = ["Ava", "Noah", "Mia", "Liam", "Zoe", "Ethan", "Ivy", "Owen", "Nina", "Leo",
         "Maya", "Eli", "Ruth", "Omar", "Tess", "Kai"]
LAST = ["Reyes", "Chen", "Patel", "Novak", "Okafor", "Lind", "Marsh", "Sato", "Costa", "Hale"]
COMPANIES = ["Northwind Labs", "Bluepeak Systems", "Harbor Analytics", "Fernway Health",
             "Cobalt Freight", "Quillon Software", "Tandem Retail", "Mesa Robotics"]
TITLES = ["Head of Growth", "VP Marketing", "Founder", "RevOps Lead", "Director of Sales"]

# name, goal, days ago started, size, bounce, open, click, reply
CAMPAIGNS = [
    ("Spring product launch", "Invite operators to try the new reporting feature.", 27, 240, .018, .46, .11, .052),
    ("Webinar invitation", "Invite marketing leaders to a live deliverability session.", 20, 180, .011, .54, .19, .034),
    ("Lapsed trial win-back", "Ask former trial users what stopped them.", 13, 150, .064, .33, .05, .081),
    ("Customer referral ask", "Ask happy customers for one introduction.", 5, 96, .010, .61, .14, .125),
]


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(path: str, rng_seed: int = 42) -> int:
    init_db(path)
    db = connect(path)
    rng = random.Random(rng_seed)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        if db.execute("SELECT COUNT(*) FROM campaigns WHERE is_sample = 1").fetchone()[0]:
            return 0
        pool = []
        for index in range(600):
            first, last = rng.choice(FIRST), rng.choice(LAST)
            email = f"{first}.{last}.{index}@example.com".lower()
            cursor = db.execute(
                """
                INSERT INTO recipients
                    (email, first_name, last_name, company, title, attributes, source, created_at)
                VALUES (?, ?, ?, ?, ?, '{}', 'sample', ?)
                """,
                (email, first, last, rng.choice(COMPANIES), rng.choice(TITLES),
                 _stamp(now - timedelta(days=40))),
            )
            pool.append((cursor.lastrowid, first, rng.choice(COMPANIES)))

        for name, goal, days_ago, size, bounce, opened, clicked, replied in CAMPAIGNS:
            started = now - timedelta(days=days_ago)
            campaign_id = db.execute(
                """
                INSERT INTO campaigns (name, goal, subject_hint, status, is_sample, created_at, started_at)
                VALUES (?, ?, ?, 'sent', 1, ?, ?)
                """,
                (name, goal, name, _stamp(started - timedelta(days=2)), _stamp(started)),
            ).lastrowid
            for recipient_id, first, company in rng.sample(pool, size):
                sent_at = started + timedelta(minutes=rng.randint(0, 2880))
                message_id = db.execute(
                    """
                    INSERT INTO messages
                        (campaign_id, recipient_id, subject, body, status, generated_by,
                         tracking_token, sent_at, created_at)
                    VALUES (?, ?, ?, ?, 'sent', 'template', ?, ?, ?)
                    """,
                    (campaign_id, recipient_id, f"{name} for {company}",
                     f"Hi {first},\n\n{goal}\n\nThanks", new_token(),
                     _stamp(sent_at), _stamp(started)),
                ).lastrowid

                def event(kind, delay_hours):
                    moment = min(sent_at + timedelta(hours=delay_hours), now)
                    db.execute(
                        "INSERT INTO engagement_events (message_id, event_type, occurred_at) VALUES (?, ?, ?)",
                        (message_id, kind, _stamp(moment)),
                    )

                if rng.random() < bounce:
                    event("bounced", 0.1)
                    continue
                if rng.random() < opened:
                    first_open = rng.expovariate(1 / 18)
                    event("opened", first_open)
                    if rng.random() < .3:
                        event("opened", first_open + rng.expovariate(1 / 30))
                    # Conditional odds, so the campaign-level rates above hold.
                    if rng.random() < clicked / opened:
                        event("clicked", first_open + rng.random())
                    if rng.random() < replied / opened:
                        event("replied", first_open + rng.expovariate(1 / 12))
                    if rng.random() < .006:
                        event("unsubscribed", first_open + .2)
        db.commit()
        return len(CAMPAIGNS)
    finally:
        db.close()


def clear_samples(path: str) -> None:
    init_db(path)
    db = connect(path)
    try:
        db.execute("DELETE FROM campaigns WHERE is_sample = 1")
        db.execute("DELETE FROM recipients WHERE source = 'sample'")
        db.commit()
    finally:
        db.close()



