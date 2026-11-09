# Email Automation Intelligence Dashboard

A full-stack outbound email system. It pulls recipients from Google Sheets and Gmail, writes a personalized message for each one with Groq, sends through Gmail, and reports delivery and engagement on a live React dashboard.

```
React dashboard ──> Node.js gateway ──> Flask API ──> SQLite
                    sign-in, CSRF,       workflow,
                    rate limits,         SQL reports
                    live stream             │
                                            ├──> Groq (writing, insight)
                                            └──> Google Sheets and Gmail
```

| Layer | What it does |
| --- | --- |
| `frontend/` | React and Vite dashboard: live funnel, daily trend chart, campaign workflow, message review |
| `gateway/` | Express API gateway: JWT session in an HttpOnly cookie, scrypt password hash, CSRF header check, rate limits, Helmet CSP, server-sent live metrics |
| `backend/` | Flask API: SQL schema and aggregation queries, Groq, Google, open and click tracking, unsubscribe |

## Quick start

You need Node 22 or newer and Python 3.11 or newer. The commands work in PowerShell, Command Prompt, macOS and Linux.

```bash
npm run setup   # installs everything, creates .env, adds sample data, prints your password
npm run dev
```

Open http://localhost:5173 and sign in as `admin` with the password that setup printed.

Sample campaigns are labelled in the interface and can't send email. To remove them:

```bash
npm run py -- -m app.cli clear-samples
```

To run the tests for all three layers:

```bash
npm test
```

## Connect Groq

Add a key from https://console.groq.com to `.env`:

```bash
GROQ_API_KEY=your_key
GROQ_MODEL=openai/gpt-oss-120b
```

Without a key, messages use a plain template and insights use fixed rules. Everything is labelled with what produced it, so template text is never presented as model output.

Personalization sends each recipient's sheet fields to Groq. Insight requests send only campaign totals.

## Connect Google

1. In Google Cloud, enable the Gmail API and the Google Sheets API.
2. Create an OAuth client of type **Desktop app**, and save its JSON as `backend/credentials.json`.
3. Authorize your account:

```bash
npm run py -- -m app.cli google-auth
```

This saves `backend/token.json`, which is ignored by git. It requests permission to send mail, read mail, and read spreadsheets.

Your sheet's first row needs an `email` column. `first_name`, `last_name`, `company` and `title` are recognized, and any other columns are passed to the writing prompt.

## Run a campaign

1. **Import recipients** from a Google Sheet or a Gmail search.
2. **Write messages**, then read them in the message list.
3. **Send**, after confirming. Each run sends at most `MAX_SEND_PER_RUN` emails.
4. **Check Gmail** to pick up replies and bounces from each thread.

Set `PUBLIC_BASE_URL` to an address recipients can reach, or open tracking, click tracking and unsubscribe links won't work. Only email people you have a right to contact. For commercial email in the US, also set `SENDER_POSTAL_ADDRESS`, which CAN-SPAM requires.

## How the numbers are defined

| Metric | Definition |
| --- | --- |
| Sent | Messages Gmail accepted |
| Delivered | Sent messages with no bounce event |
| Delivery rate | Delivered / sent |
| Open, click and reply rates | Distinct delivered messages with that event / delivered |

Repeat opens count once. Gmail doesn't confirm delivery, so "delivered" means no bounce notice has appeared in the thread. Open rates run high because mail privacy tools load tracking pixels automatically.

The queries are in `backend/app/reports.py` and the schema is in `backend/app/schema.sql`.

## Security

- The browser only talks to the gateway. Flask rejects any API call without the shared internal token.
- Sessions are signed JWTs in `HttpOnly`, `SameSite=Strict` cookies. Set `COOKIE_SECURE=true` when serving over HTTPS.
- Click links are signed, so the redirect can't be used to send people to other sites.
- Generated text is escaped before it goes into HTML email.
- Unsubscribing takes a POST, so mail scanners that fetch links can't unsubscribe anyone. Unsubscribed recipients are skipped on every later send.
- A message is claimed before its Gmail call, so overlapping runs can't send it twice. A message interrupted mid-send stays in `sending` and is never retried automatically.

## Docker

```bash
npm run setup
docker compose up --build
```

Then open http://localhost:4000. Only the gateway is published.

## Limits

- There is one admin account, and SQLite suits one server. A larger deployment would move to Postgres.
- Sends run inside the web request, which suits small batches. Larger volumes would need a job queue.
- Tracking timestamps and daily reports use UTC.
