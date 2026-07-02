# Voxy Weekly Email - Quick Start

## 1. Upload the files

Upload everything in this folder to the root of your GitHub repository:

- `voxy_review_monitor.py`
- `requirements.txt`
- `README.md`
- `QUICK_START.md`
- `.github/workflows/voxy-daily.yml`

Keep `.github/workflows/voxy-daily.yml` in that exact folder path.

## 2. Check the Google Sheet

The shared Google Sheet must have a `Produits` tab with these columns:

- `Active`
- `Product name`
- `Country`
- `Owner`
- `Priority`
- `Paused`
- `URL to monitor`
- `Alert emails`
- `Star threshold`
- `Score alert threshold`
- `Alert type`
- `Platform`
- `Platform account`
- `Notes`

The `Alert emails` column controls who receives the weekly summary.

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

If this optional secret is missing, Voxy still sends the weekly email and uploads the dashboard as a GitHub artifact.

## 4. Test without sending

In GitHub:

1. Open `Actions`.
2. Click `Voxy Weekly Review Analysis`.
3. Click `Run workflow`.
4. Set `baseline` to `false`.
5. Set `dry_run` to `true`.
6. Click `Run workflow`.

This prints the weekly email content in the logs but does not send it.

## 5. Send the real weekly email

Run the workflow again with:

- `baseline`: `false`
- `dry_run`: `false`

This sends one weekly summary email to each recipient in the `Alert emails` column.

## 6. Automatic weekly run

The workflow is already scheduled for every Monday at `07:00 UTC`.

In Paris, this is usually `09:00` in summer and `08:00` in winter.

GitHub can sometimes start scheduled jobs late. Voxy now sends the weekly email whenever that Monday job actually starts, so a delay on GitHub's side will not block the email.
