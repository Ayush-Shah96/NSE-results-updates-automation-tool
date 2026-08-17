# Quarterly Results Alert Center

A Python + SQLite automation for user-configured quarterly-results alerts across NSE and BSE, with real WhatsApp notifications.

## What the user does

1. Open the dashboard.
2. Create an alert for an NSE symbol or BSE scrip code.
3. Choose whether the alert should send a text summary, a report image, or both.
4. Leave the application running.
5. The background monitor checks the configured exchange feed on the configured interval.
6. When a new result appears after the alert was created, the system processes it and sends the notification to the WhatsApp recipients in `.env`.

## Notification flow

```text
User creates company alert
          |
          v
Background monitor
          |
          +---- NSE Integrated Filing - Financials
          |
          +---- BSE company-result monitor
          |
          v
New filing detected
          |
          v
Financial extraction (structured source where available)
          |
          v
Previous quarter + previous year comparison
          |
          v
QoQ / YoY calculation
          |
          +-------------------+
          |                   |
          v                   v
 Short WhatsApp summary   Report PNG
          |                   |
          +---------+---------+
                    v
               WhatsApp
```

## UI

The UI is designed as an alert center rather than a demo/report generator. The main screen focuses on:

- Create a new company alert.
- Active / paused alert management.
- Monitoring status and scan interval.
- Recent detection and delivery events.
- Live filing explorer.
- Generated report preview.
- WhatsApp delivery history.

There is no demo workflow in the UI.

## WhatsApp behavior

For alerts, the default delivery is:

1. A short result summary text message.
2. The generated quarterly-results image immediately after it.

The text summary includes the company, quarter, consolidation, Revenue, EBITDA, PAT, EPS, QoQ/YoY changes and source URL when available.

The app stores a provider message ID for each WhatsApp send in SQLite.

For outbound WhatsApp messages, use the official WhatsApp Cloud API credentials in `.env`. Depending on Meta account state and conversation rules, a production setup may require approved templates for business-initiated messaging.

## NSE

NSE Integrated Filing - Financials is the primary live financial-results source. The extractor follows the filing Details/iXBRL/XBRL link and prefers structured financial data. NSE's public filing interface exposes company/symbol, quarter-end filters and XBRL-related functionality.

## BSE

The BSE adapter watches configured company result pages and sends filing-level alerts. When BSE exposes a structured source that the extractor can consume, the same financial report pipeline can be used. This build deliberately does not invent numeric values when a structured attachment is unavailable.

## Environment

```env
APP_ENV=development
DATABASE_PATH=data/results_live.db
MONITOR_INTERVAL_SECONDS=60
MONITOR_MODE=live
ALERT_POLL_ENABLED=true
LIVE_LOOKBACK_DAYS=90
LIVE_HISTORY_LOOKBACK_DAYS=450
LIVE_PREFERRED_CONSOLIDATION=Consolidated

WHATSAPP_ENABLED=true
WHATSAPP_SEND_MODE=both
WHATSAPP_GRAPH_VERSION=v25.0
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_RECIPIENTS=
```

`WHATSAPP_SEND_MODE=both` makes manual processing send a short text summary followed by the image. Alert-specific settings can independently enable/disable text and image.

## Windows setup

```powershell
py -3.11 -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

## API

- `GET /api/alerts`
- `POST /api/alerts`
- `PATCH /api/alerts/{id}`
- `DELETE /api/alerts/{id}`
- `GET /api/monitor/status`
- `POST /api/monitor/scan`
- `GET /api/live/scan`
- `POST /api/live/process`
- `GET /api/filings`
- `GET /api/deliveries`
- `GET /api/reports/{filing_id}`

## Testing

```powershell
pytest -q
```

The suite covers calculations, live parsing, the live-only UI, alert persistence and the WhatsApp summary formatter.

## Security

Keep `.env` private. Never commit the WhatsApp access token to source control.


## WhatsApp recipient directory and multiple alerts

The Alert Center now supports a persistent recipient directory in SQLite. From the Alerts screen you can:

- Add a WhatsApp recipient manually with a name and international-format phone number.
- Upload a CSV containing `name,phone_number`. The importer also accepts `phone`, `mobile`, `number`, or `whatsapp` as the phone column.
- Download `whatsapp_recipients_template.csv` from the UI.
- Select multiple recipients for each alert.
- Keep multiple NSE/BSE alerts active at the same time, with different recipient groups and notification choices.

Each alert is stored separately and is linked to one or more recipients through the `alert_recipients` table. When a filing matches multiple alerts, the delivery plan is merged per recipient so each person receives the text summary and/or report image required by their matched alerts.

Relevant API endpoints:

```text
GET    /api/recipients
POST   /api/recipients
POST   /api/recipients/import
GET    /api/recipients/template
DELETE /api/recipients/{recipient_id}

GET    /api/alerts
POST   /api/alerts
PATCH  /api/alerts/{alert_id}
DELETE /api/alerts/{alert_id}
```

Phone numbers are normalized to digits-only international format before storage. Keep the Meta/WhatsApp access token in `.env`; never place it in the recipient CSV.
