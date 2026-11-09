"""The campaign workflow end to end, with Google and Groq faked."""
import re

import pytest
from itsdangerous import URLSafeSerializer

from app.seed import clear_samples, seed
from .conftest import FakeGroq, HEADERS, get, make_campaign, post

SHEET = [
    ["Email", "First Name", "Company", "Job Title", "Favorite Tool"],
    ["ada@example.com", "Ada", "Analytical", "CTO", "Looker"],
    ["grace@example.com", "Grace", "Navy", "Admiral"],
    ["not-an-email", "Bad"],
    [],
]


def prepared(client, fakes, sheet=SHEET):
    fakes.sheets.values_data = sheet
    campaign_id = make_campaign(client)
    post(client, f"/api/campaigns/{campaign_id}/import")
    post(client, f"/api/campaigns/{campaign_id}/generate")
    return campaign_id


def html_part(message):
    return message.get_body(preferencelist=("html",)).get_content()


def test_api_requires_internal_token(client):
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers={"X-Internal-Token": "wrong"}).status_code == 401
    assert client.get("/api/overview", headers=HEADERS).status_code == 200


def test_weak_secrets_stop_startup(tmp_path):
    from app import create_app
    with pytest.raises(RuntimeError):
        create_app({"SECRET_KEY": "short", "INTERNAL_API_TOKEN": "x" * 40,
                    "DATABASE_PATH": str(tmp_path / "x.db")})


def test_campaign_requires_name_and_goal(client):
    assert post(client, "/api/campaigns", {"name": "x"}).status_code == 400


def test_import_from_sheet_reports_bad_rows_and_is_idempotent(client, fakes):
    fakes.sheets.values_data = SHEET
    campaign_id = make_campaign(client)
    result = post(client, f"/api/campaigns/{campaign_id}/import").get_json()
    assert result["found"] == 2 and result["added"] == 2
    assert "Row 4" in result["problems"][0]
    assert post(client, f"/api/campaigns/{campaign_id}/import").get_json()["added"] == 0


def test_import_requires_email_column(client, fakes):
    fakes.sheets.values_data = [["Name"], ["Ada"]]
    campaign_id = make_campaign(client)
    result = post(client, f"/api/campaigns/{campaign_id}/import").get_json()
    assert result["added"] == 0 and "email" in result["problems"][0]


def test_import_from_gmail_skips_self_and_automated(client, fakes):
    fakes.gmail.search_results = [{
        "id": "m1",
        "payload": {"headers": [
            {"name": "From", "value": "Lin Wu <lin@example.com>"},
            {"name": "To", "value": "me@sender.test, no-reply@example.com"},
        ]},
    }]
    campaign_id = make_campaign(client)
    result = post(client, f"/api/campaigns/{campaign_id}/import",
                  {"source": "gmail", "query": "label:leads"}).get_json()
    assert result["added"] == 1
    item = get(client, f"/api/campaigns/{campaign_id}/messages").get_json()["items"][0]
    assert item["email"] == "lin@example.com" and item["first_name"] == "Lin"


def test_template_generation_is_labelled(client, fakes):
    campaign_id = prepared(client, fakes)
    items = get(client, f"/api/campaigns/{campaign_id}/messages").get_json()["items"]
    assert items[0]["generated_by"] == "template"
    assert items[0]["status"] == "ready"
    assert "Hi Ada" in items[0]["body"] and "Analytical" in items[0]["body"]


def test_groq_personalization_sends_recipient_fields(app, client, fakes):
    groq = FakeGroq(reply={"subject": "Hi Ada", "body": "A note for Analytical."})
    app.config.update(GROQ_API_KEY="k", GROQ_CLIENT_FACTORY=lambda _key: groq)
    campaign_id = prepared(client, fakes)
    items = get(client, f"/api/campaigns/{campaign_id}/messages").get_json()["items"]
    assert items[0]["generated_by"] == app.config["GROQ_MODEL"]
    prompt = groq.calls[0]["messages"][1]["content"]
    assert "Analytical" in prompt and "Looker" in prompt


@pytest.mark.parametrize("groq", [
    FakeGroq(reply="not json"),
    FakeGroq(reply={"subject": "", "body": "x"}),
    FakeGroq(reply={"subject": "Hi {{name}}", "body": "x"}),
    FakeGroq(error=RuntimeError("secret detail")),
])
def test_bad_groq_output_fails_the_row_not_the_batch(app, client, fakes, groq):
    app.config.update(GROQ_API_KEY="k", GROQ_CLIENT_FACTORY=lambda _key: groq)
    fakes.sheets.values_data = SHEET
    campaign_id = make_campaign(client)
    post(client, f"/api/campaigns/{campaign_id}/import")
    result = post(client, f"/api/campaigns/{campaign_id}/generate").get_json()
    assert result == {"generated": 0, "failed": 2}
    item = get(client, f"/api/campaigns/{campaign_id}/messages").get_json()["items"][0]
    assert item["status"] == "generation_failed"
    assert "secret detail" not in item["error"]
    # A retry picks the failed rows back up.
    app.config.update(GROQ_API_KEY="")
    assert post(client, f"/api/campaigns/{campaign_id}/generate").get_json()["generated"] == 2


def test_send_builds_tracked_email_and_never_resends(client, fakes):
    campaign_id = prepared(client, fakes)
    result = post(client, f"/api/campaigns/{campaign_id}/send").get_json()
    assert result["sent"] == 2 and result["remaining"] == 0
    message = fakes.gmail.sent[0]
    assert message["To"] == "ada@example.com"
    assert message["List-Unsubscribe"].startswith("<https://mail.test/t/u/")
    body = html_part(message)
    assert "https://mail.test/t/o/" in body and "Unsubscribe" in body
    assert "Unsubscribe: https://mail.test/t/u/" in message.get_body(("plain",)).get_content()
    assert post(client, f"/api/campaigns/{campaign_id}/send").get_json()["sent"] == 0
    assert len(fakes.gmail.sent) == 2
    assert get(client, f"/api/campaigns/{campaign_id}").get_json()["status"] == "sent"


def test_send_respects_run_limit(app, client, fakes):
    app.config["MAX_SEND_PER_RUN"] = 1
    campaign_id = prepared(client, fakes)
    assert post(client, f"/api/campaigns/{campaign_id}/send").get_json() == {
        "sent": 1, "failed": 0, "skipped": 0, "remaining": 1,
    }
    assert get(client, f"/api/campaigns/{campaign_id}").get_json()["status"] == "sending"


def test_failed_send_is_recorded_and_batch_continues(client, fakes):
    fakes.gmail.fail_for = {"ada@example.com"}
    campaign_id = prepared(client, fakes)
    result = post(client, f"/api/campaigns/{campaign_id}/send").get_json()
    assert (result["sent"], result["failed"]) == (1, 1)
    metrics = get(client, f"/api/campaigns/{campaign_id}").get_json()
    assert metrics["failed"] == 1 and metrics["sent"] == 1


def test_model_html_is_escaped_and_links_are_tracked(app, client, fakes):
    groq = FakeGroq(reply={
        "subject": "Hello",
        "body": 'See <script>alert(1)</script> at https://example.org/a?x=1&y=2 today',
    })
    app.config.update(GROQ_API_KEY="k", GROQ_CLIENT_FACTORY=lambda _key: groq)
    campaign_id = prepared(client, fakes, SHEET[:2])
    post(client, f"/api/campaigns/{campaign_id}/send")
    body = html_part(fakes.gmail.sent[0])
    assert "<script>" not in body and "&lt;script&gt;" in body
    signed = re.search(r'href="https://mail\.test/t/c/([^"]+)"', body).group(1)
    response = client.get(f"/t/c/{signed}")
    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.org/a?x=1&y=2"
    assert get(client, f"/api/campaigns/{campaign_id}").get_json()["clicked"] == 1


def test_click_rejects_forged_and_non_http_links(app, client):
    assert client.get("/t/c/forged-value").status_code == 404
    bad_key = URLSafeSerializer("other" * 10, salt="click-link").dumps({"t": "x", "u": "https://evil.test"})
    assert client.get(f"/t/c/{bad_key}").status_code == 404
    with app.app_context():
        from app.campaign_service import sign_click
        signed = sign_click("x", "javascript:alert(1)")
    assert client.get(f"/t/c/{signed}").status_code == 404


def test_open_pixel_counts_unique_and_hides_bad_tokens(client, fakes, db):
    campaign_id = prepared(client, fakes, SHEET[:2])
    post(client, f"/api/campaigns/{campaign_id}/send")
    token = db.execute("SELECT tracking_token FROM messages").fetchone()[0]
    for _ in range(3):
        response = client.get(f"/t/o/{token}.gif")
        assert response.mimetype == "image/gif"
    assert client.get("/t/o/unknown.gif").status_code == 200
    metrics = get(client, f"/api/campaigns/{campaign_id}").get_json()
    assert metrics["opened"] == 1


def test_unsubscribe_needs_post_and_blocks_later_sends(client, fakes, db):
    campaign_id = prepared(client, fakes, SHEET[:2])
    post(client, f"/api/campaigns/{campaign_id}/send")
    token = db.execute("SELECT tracking_token FROM messages").fetchone()[0]
    client.get(f"/t/u/{token}")
    assert db.execute("SELECT unsubscribed_at FROM recipients").fetchone()[0] is None
    assert client.post(f"/t/u/{token}").status_code == 200
    assert db.execute("SELECT unsubscribed_at FROM recipients").fetchone()[0] is not None

    second = prepared(client, fakes, SHEET[:2])
    result = post(client, f"/api/campaigns/{second}/send").get_json()
    assert result["sent"] == 0 and result["skipped"] == 1
    assert len(fakes.gmail.sent) == 1


def test_sync_finds_replies_and_bounces_once(client, fakes):
    campaign_id = prepared(client, fakes)
    post(client, f"/api/campaigns/{campaign_id}/send")
    own = {"id": "g1", "payload": {"headers": [{"name": "From", "value": "me@sender.test"}]}}
    fakes.gmail.threads_data = {
        "th1": {"messages": [own, {
            "id": "r1", "internalDate": "1767225600000",
            "payload": {"headers": [{"name": "From", "value": "Ada <ada@example.com>"}]},
        }]},
        "th2": {"messages": [{
            "id": "b1", "internalDate": "1767225600000",
            "payload": {"headers": [{"name": "From", "value": "Mail Delivery <mailer-daemon@googlemail.com>"}]},
        }]},
    }
    first = post(client, f"/api/campaigns/{campaign_id}/sync").get_json()
    assert first == {"replied": 1, "bounced": 1, "checked": 2}
    again = post(client, f"/api/campaigns/{campaign_id}/sync").get_json()
    assert again == {"replied": 0, "bounced": 0, "checked": 2}
    metrics = get(client, f"/api/campaigns/{campaign_id}").get_json()
    assert metrics["delivery_rate"] == 0.5 and metrics["replied"] == 1


def test_groq_insight_receives_only_aggregates(app, client, fakes):
    campaign_id = prepared(client, fakes)
    post(client, f"/api/campaigns/{campaign_id}/send")
    groq = FakeGroq(reply={"observations": ["Ask a question."]})
    app.config.update(GROQ_API_KEY="k", GROQ_CLIENT_FACTORY=lambda _key: groq)
    response = post(client, f"/api/campaigns/{campaign_id}/insights")
    assert response.status_code == 201
    assert response.get_json()["observations"] == ["Ask a question."]
    prompt = groq.calls[0]["messages"][1]["content"]
    assert "ada@example.com" not in prompt and "Ada" not in prompt
    assert get(client, f"/api/campaigns/{campaign_id}").get_json()["insight"]["generated_by"] == app.config["GROQ_MODEL"]


def test_groq_insight_failure_returns_502_without_details(app, client, fakes):
    campaign_id = prepared(client, fakes)
    groq = FakeGroq(error=RuntimeError("secret detail"))
    app.config.update(GROQ_API_KEY="k", GROQ_CLIENT_FACTORY=lambda _key: groq)
    response = post(client, f"/api/campaigns/{campaign_id}/insights")
    assert response.status_code == 502
    assert "secret detail" not in response.get_json()["error"]


def test_rule_insight_when_groq_is_off(client, fakes):
    campaign_id = make_campaign(client)
    result = post(client, f"/api/campaigns/{campaign_id}/insights").get_json()
    assert result["generated_by"] == "rules"
    assert "Nothing has been sent" in result["observations"][0]


def test_google_missing_gives_directions(app, client, tmp_path):
    app.config.update(GOOGLE_SERVICE_FACTORY=None, GOOGLE_TOKEN_PATH=str(tmp_path / "none.json"))
    campaign_id = make_campaign(client)
    response = post(client, f"/api/campaigns/{campaign_id}/import")
    assert response.status_code == 503
    assert "google-auth" in response.get_json()["error"]


def test_sample_campaigns_cannot_be_sent(app, client, fakes):
    seed(app.config["DATABASE_PATH"])
    campaign = get(client, "/api/campaigns").get_json()[0]
    assert campaign["is_sample"] == 1
    for action in ("send", "import", "generate", "sync"):
        assert post(client, f"/api/campaigns/{campaign['id']}/{action}").status_code == 409
    assert fakes.gmail.sent == []


def test_seed_is_idempotent_and_clearable(app, client):
    path = app.config["DATABASE_PATH"]
    assert seed(path) == 4
    assert seed(path) == 0
    overview = get(client, "/api/overview").get_json()
    assert overview["sent"] == 666 and 0.9 < overview["delivery_rate"] < 1
    clear_samples(path)
    assert get(client, "/api/overview").get_json()["sent"] == 0
