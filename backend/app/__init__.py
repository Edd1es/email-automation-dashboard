"""Flask application factory."""
import base64
import hmac
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Blueprint, Flask, Response, abort, current_app, jsonify, redirect, request
from itsdangerous import BadSignature
from werkzeug.exceptions import HTTPException

from . import campaign_service, google_service, groq_service, reports
from .db import close_db, get_db, init_db, utcnow

BACKEND_DIR = Path(__file__).resolve().parent.parent
TRANSPARENT_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

api = Blueprint("api", __name__, url_prefix="/api")
tracking = Blueprint("tracking", __name__, url_prefix="/t")


def _error(message: str, status: int):
    return jsonify({"error": message}), status


def _load_campaign(campaign_id: int) -> dict:
    campaign = reports.campaign(get_db(), campaign_id)
    if campaign is None:
        abort(404, description="Campaign not found")
    return campaign


@api.before_request
def require_internal_token():
    """Only the Node gateway, which authenticates users, may call the API."""
    expected = current_app.config["INTERNAL_API_TOKEN"]
    supplied = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        return _error("Missing or invalid internal token", 401)
    return None


@api.get("/status")
def status():
    return jsonify({
        "groq": {"configured": groq_service.is_configured(),
                 "model": current_app.config["GROQ_MODEL"]},
        "google": {"configured": google_service.is_configured()},
        "max_send_per_run": current_app.config["MAX_SEND_PER_RUN"],
    })


@api.get("/overview")
def overview():
    return jsonify(reports.overview(get_db()))


@api.get("/trend")
def trend():
    days = max(1, min(request.args.get("days", 30, type=int), 365))
    campaign_id = request.args.get("campaign_id", type=int)
    return jsonify(reports.trend(get_db(), days, campaign_id))


@api.get("/campaigns")
def campaigns():
    return jsonify(reports.list_campaigns(get_db()))


@api.post("/campaigns")
def create_campaign():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    goal = str(body.get("goal") or "").strip()
    if not name or not goal:
        return _error("Enter a campaign name and what the email should achieve.", 400)
    if len(name) > 120 or len(goal) > 2000:
        return _error("Keep the name under 120 characters and the goal under 2,000.", 400)
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO campaigns (name, goal, subject_hint, sheet_id, sheet_range, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            name, goal, str(body.get("subject_hint") or "").strip()[:150],
            str(body.get("sheet_id") or "").strip() or None,
            str(body.get("sheet_range") or "").strip() or None,
            utcnow(),
        ),
    )
    db.commit()
    return jsonify(reports.campaign(db, cursor.lastrowid)), 201


@api.get("/campaigns/<int:campaign_id>")
def campaign_detail(campaign_id):
    campaign = _load_campaign(campaign_id)
    campaign["insight"] = campaign_service.latest_insight(get_db(), campaign_id)
    return jsonify(campaign)


@api.get("/campaigns/<int:campaign_id>/messages")
def campaign_messages(campaign_id):
    _load_campaign(campaign_id)
    limit = max(1, min(request.args.get("limit", 50, type=int), 200))
    offset = max(0, request.args.get("offset", 0, type=int))
    return jsonify(reports.list_messages(get_db(), campaign_id, limit, offset))


@api.post("/campaigns/<int:campaign_id>/import")
def import_recipients(campaign_id):
    campaign = _load_campaign(campaign_id)
    if campaign["is_sample"]:
        return _error("Sample campaigns can't be changed. Create a new campaign.", 409)
    body = request.get_json(silent=True) or {}
    source = body.get("source", "sheet")
    db = get_db()
    if source == "sheet":
        sheet_id = body.get("sheet_id") or campaign["sheet_id"]
        sheet_range = body.get("sheet_range") or campaign["sheet_range"] or "A:Z"
        if not sheet_id:
            return _error("Add a Google Sheet ID to import recipients.", 400)
        values = google_service.read_sheet(sheet_id, sheet_range)
        recipients, problems = google_service.parse_sheet_rows(values)
    elif source == "gmail":
        query = str(body.get("query") or "").strip()
        if not query:
            return _error("Enter a Gmail search to import recipients.", 400)
        recipients, problems = google_service.recipients_from_gmail(query), []
    else:
        return _error("Choose 'sheet' or 'gmail' as the import source.", 400)
    added = campaign_service.add_recipients(db, campaign_id, recipients, source)
    return jsonify({"found": len(recipients), "added": added, "problems": problems[:20]})


@api.post("/campaigns/<int:campaign_id>/generate")
def generate(campaign_id):
    campaign = _load_campaign(campaign_id)
    if campaign["is_sample"]:
        return _error("Sample campaigns can't be changed. Create a new campaign.", 409)
    return jsonify(campaign_service.generate_messages(get_db(), campaign))


@api.post("/campaigns/<int:campaign_id>/send")
def send(campaign_id):
    campaign = _load_campaign(campaign_id)
    if campaign["is_sample"]:
        return _error("Sample campaigns use made-up addresses and can't be sent.", 409)
    return jsonify(campaign_service.send_campaign(get_db(), campaign))


@api.post("/campaigns/<int:campaign_id>/sync")
def sync(campaign_id):
    campaign = _load_campaign(campaign_id)
    if campaign["is_sample"]:
        return _error("Sample campaigns have no Gmail threads to sync.", 409)
    return jsonify(campaign_service.sync_engagement(get_db(), campaign_id))


@api.post("/campaigns/<int:campaign_id>/insights")
def insights(campaign_id):
    _load_campaign(campaign_id)
    return jsonify(campaign_service.create_insight(get_db(), campaign_id)), 201


def _message_by_token(token: str):
    return get_db().execute(
        "SELECT id, recipient_id, status FROM messages WHERE tracking_token = ?", (token,)
    ).fetchone()


@tracking.get("/o/<token>.gif")
def track_open(token):
    # Always answer with the pixel, so a bad token reveals nothing.
    message = _message_by_token(token)
    if message and message["status"] == "sent":
        campaign_service.record_event(get_db(), message["id"], "opened")
    response = Response(TRANSPARENT_GIF, mimetype="image/gif")
    response.headers["Cache-Control"] = "no-store"
    return response


@tracking.get("/c/<signed>")
def track_click(signed):
    try:
        data = campaign_service.read_click(signed)
    except BadSignature:
        abort(404, description="This link is not valid.")
    url = data.get("u", "")
    if not url.startswith(("http://", "https://")):
        abort(404, description="This link is not valid.")
    message = _message_by_token(data.get("t", ""))
    if message and message["status"] == "sent":
        campaign_service.record_event(get_db(), message["id"], "clicked", detail=url)
    return redirect(url, code=302)


def _simple_page(text: str, form: bool = False) -> Response:
    button = (
        '<form method="post"><button type="submit">Unsubscribe</button></form>' if form else ""
    )
    return Response(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>Email preferences</title>'
        '<body style="font:16px system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem">'
        f"<p>{text}</p>{button}</body>",
        mimetype="text/html",
    )


@tracking.route("/u/<token>", methods=["GET", "POST"])
def unsubscribe(token):
    message = _message_by_token(token)
    if not message:
        abort(404, description="This link is not valid.")
    # GET only asks, because mail scanners fetch links and must not unsubscribe anyone.
    if request.method == "GET":
        return _simple_page("Stop receiving these emails?", form=True)
    db = get_db()
    db.execute(
        "UPDATE recipients SET unsubscribed_at = COALESCE(unsubscribed_at, ?) WHERE id = ?",
        (utcnow(), message["recipient_id"]),
    )
    campaign_service.record_event(
        db, message["id"], "unsubscribed", dedupe_key=f"unsubscribe:{message['id']}"
    )
    return _simple_page("You're unsubscribed. You won't receive more of these emails.")


def create_app(overrides: dict | None = None) -> Flask:
    load_dotenv(BACKEND_DIR.parent / ".env")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", ""),
        INTERNAL_API_TOKEN=os.environ.get("INTERNAL_API_TOKEN", ""),
        DATABASE_PATH=os.environ.get("DATABASE_PATH", str(BACKEND_DIR / "data" / "app.db")),
        GROQ_API_KEY=os.environ.get("GROQ_API_KEY", ""),
        GROQ_MODEL=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        GOOGLE_TOKEN_PATH=os.environ.get("GOOGLE_TOKEN_PATH", str(BACKEND_DIR / "token.json")),
        GOOGLE_CLIENT_SECRETS_PATH=os.environ.get(
            "GOOGLE_CLIENT_SECRETS_PATH", str(BACKEND_DIR / "credentials.json")
        ),
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", "http://localhost:4000"),
        SENDER_POSTAL_ADDRESS=os.environ.get("SENDER_POSTAL_ADDRESS", ""),
        MAX_SEND_PER_RUN=int(os.environ.get("MAX_SEND_PER_RUN", "50")),
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    app.config.update(overrides or {})

    for key in ("SECRET_KEY", "INTERNAL_API_TOKEN"):
        if len(app.config[key]) < 32:
            env = "FLASK_SECRET_KEY" if key == "SECRET_KEY" else key
            raise RuntimeError(f"{env} must be at least 32 characters. Run: npm run setup")

    init_db(app.config["DATABASE_PATH"])
    app.teardown_appcontext(close_db)
    app.register_blueprint(api)
    app.register_blueprint(tracking)

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.errorhandler(google_service.GoogleNotConfigured)
    def google_missing(exc):
        return _error(str(exc), 503)

    @app.errorhandler(groq_service.GenerationError)
    def groq_failed(exc):
        return _error(str(exc), 502)

    @app.errorhandler(404)
    def not_found(exc):
        return _error(exc.description, 404)

    @app.errorhandler(Exception)
    def unexpected(exc):
        if isinstance(exc, HTTPException):
            return _error(exc.description, exc.code)
        # Google API failures land here too; details go to the log, not the client.
        app.logger.exception("Unhandled error")
        return _error("The request failed on the server. Check the backend log.", 500)

    return app
