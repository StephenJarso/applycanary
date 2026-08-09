# SMTP & Email Notification Setup Guide

ApplyCanary automatically sends daily email digests summarizing newly found jobs, queue status, and immediate alerts for exceptional job matches (score >= 90).

When SMTP is unconfigured, email alerts are safely logged to standard output without interrupting job scraping or scoring.

---

## 1. Required Configuration (`.env`)

Add the following environment variables to your `.env` file:

```ini
# SMTP Host & Port
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587

# Authentication Credentials
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password

# Email Recipient & Alert Ceiling
DIGEST_TO=your_email@gmail.com
ALERT_MIN_SCORE=90
```

### Configuration Parameters

| Parameter | Default | Description |
|---|---|---|
| `SMTP_HOST` | *(empty)* | Hostname of the SMTP server (e.g. `smtp.gmail.com`). |
| `SMTP_PORT` | `587` | Server port (`587` for STARTTLS, `465` for SSL/TLS). |
| `SMTP_USER` | *(empty)* | SMTP login username / sender email address. |
| `SMTP_PASSWORD` | *(empty)* | SMTP password or provider App Password. |
| `DIGEST_TO` | *(empty)* | Recipient email address where digests and alerts are sent. |
| `ALERT_MIN_SCORE` | `90` | Jobs scoring at or above this threshold trigger instant email alerts. |

---

## 2. Common Provider Setup

### Gmail (Recommended for Personal Use)

Gmail requires an **App Password** instead of your account password:

1. Enable **2-Step Verification** in your [Google Account Security Settings](https://myaccount.google.com/security).
2. Go to [Google App Passwords](https://myaccount.google.com/apppasswords).
3. Generate a new App Password (select App: *Mail*, Device: *Other/ApplyCanary*).
4. Copy the generated 16-character password into `.env`:

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_gmail_address@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx
DIGEST_TO=your_gmail_address@gmail.com
```

---

### SendGrid

```ini
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=SG.your_sendgrid_api_key
DIGEST_TO=your_email@domain.com
```

---

### Mailgun

```ini
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_USER=postmaster@your-domain.com
SMTP_PASSWORD=your_mailgun_smtp_password
DIGEST_TO=your_email@domain.com
```

---

### Outlook / Office 365

```ini
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=your_email@outlook.com
SMTP_PASSWORD=your_password
DIGEST_TO=your_email@outlook.com
```

---

## 3. Verifying Email Delivery

Test your SMTP settings directly using the Python runtime:

```bash
.venv/bin/python -c "
import asyncio
from app.notify.email import send

async def test():
    ok = await send(
        'ApplyCanary SMTP Test',
        '<h3>SMTP Configured Successfully</h3><p>Your email notification pipeline is active.</p>',
        'SMTP Configured Successfully'
    )
    print('Delivery Result:', 'SUCCESS' if ok else 'FAILED')

asyncio.run(test())
"
```

---

## 4. Troubleshooting

- **`SMTPAuthenticationError`**: Verify that your `SMTP_USER` and `SMTP_PASSWORD` are correct. For Gmail or Outlook, verify 2FA and App Password permissions.
- **`ConnectionRefusedError` / Timeout**: Ensure host firewalls or VPS provider rules allow outbound traffic on port `587` or `465`.
- **Port 587 vs 465**: Port `587` automatically uses STARTTLS; port `465` uses implicit SSL/TLS. Both are supported automatically by ApplyCanary.
