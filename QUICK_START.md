# Voxy Weekly Email - Quick Start

## 1. Files in GitHub

The repository must contain:

- `voxy_review_monitor.py`
- `voxy_weekly_alert_runner.py`
- `voxy_newsletter_runner.py`
- `requirements.txt`
- `README.md`
- `QUICK_START.md`
- `.github/workflows/voxy-daily.yml`

Keep `.github/workflows/voxy-daily.yml` in that exact folder path.

## 2. Check the Google Sheet

The Google Sheet file can be named `voxy`, but the tab must be named `Produits`.

Use this recommended column order:

`Active`, `Country`, `City`, `Owner`, `Product name`, `URL to monitor`, `Alert emails`, `Star threshold`, `Platform`, `Notes`

Minimum required columns:

- `Active`
- `Product name`
- `URL to monitor`
- `Alert emails`

Recommended columns for the dashboard:

- `Country`
- `City`
- `Owner`
- `Star threshold`
- `Platform`
- `Notes`

The `Alert emails` column controls who receives bad-review alerts.

## 3. Add GitHub secrets

In GitHub:

`Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

Add:

- `GOOGLE_SHEET_URL`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_USE_TLS`
- `ALERT_SUBJECT_PREFIX`

Recommended SMTP values:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
ALERT_SUBJECT_PREFIX=[Voxy - Review alert]
```

For Gmail or Google Workspace, `SMTP_PASSWORD` is usually an app password, not the normal mailbox password.

Optional dashboard update:

- `GOOGLE_SERVICE_ACCOUNT_JSON`

If this optional secret is missing, Voxy can still send alert emails and upload the dashboard as a GitHub artifact.

## 4. Test without sending

In GitHub:

1. Open `Actions`.
2. Click `Voxy Weekly Review Analysis`.
3. Click `Run workflow`.
4. Set `baseline` to `false`.
5. Set `dry_run` to `true`.
6. Click `Run workflow`.

This prints the alert email content in the logs if any product has new bad reviews or a score alert, but does not send it.

## 5. Send the real alert email

Run the workflow again with:

- `baseline`: `false`
- `dry_run`: `false`

This sends an HTML newsletter email only for products with new bad reviews or a score alert. Products with good scores or no bad reviews are not included.

## 6. Automatic weekly run

The workflow is scheduled for every Monday at `07:00 UTC`.

In Paris, this is usually `09:00` in summer and `08:00` in winter.

GitHub can sometimes start scheduled jobs late. Voxy sends the alert email whenever that Monday job actually starts, so a delay on GitHub's side will not block the email.

The Google Sheet dashboard is still updated for all active products. It includes `Country`, `City`, and `Owner`, centers the table values, wraps long text, and uses concrete examples from bad reviews when Voxy finds them.
