# Voxy Cloud - Weekly GitHub Actions Setup

Voxy runs from GitHub once per week, reads the shared Google Sheet, checks review pages, sends one weekly email summary to the addresses listed in `Alert emails`, and creates a dashboard artifact.

Your PC does not need to stay on.

## Files to upload

Upload these files to the root of the GitHub repository:

- `voxy_review_monitor.py`
- `requirements.txt`
- `README.md`
- `QUICK_START.md`
- `.github/workflows/voxy-daily.yml`

The workflow file must stay in exactly this path:

`.github/workflows/voxy-daily.yml`

## Weekly email behavior

The workflow is scheduled every Monday.

It checks at `07:00` and `08:00` UTC, but the script only runs when Paris time is `09:00`.

This handles summer and winter time:

- summer: Monday 09:00 Paris = 07:00 UTC
- winter: Monday 09:00 Paris = 08:00 UTC

Each normal weekly run sends one summary email per recipient, even when there is no new alert. Products with issues are still marked in the email.

## Required GitHub secrets

In the GitHub repository, open:

`Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

Add:

| Secret | Value |
|---|---|
| `GOOGLE_SHEET_URL` | Your Google Sheet link |
| `SMTP_HOST` | Example: `smtp.gmail.com` |
| `SMTP_PORT` | Example: `587` |
| `SMTP_USER` | Sender email account |
| `SMTP_PASSWORD` | SMTP password or app password |
| `SMTP_FROM` | Sender email address |
| `SMTP_USE_TLS` | `true` |
| `ALERT_SUBJECT_PREFIX` | `[Voxy - Review alert]` |

Optional:

| Secret | Value |
|---|---|
| `TRANSLATE_REVIEWS_TO_ENGLISH` | `true` or `false` |
| `DEEPL_API_KEY` | DeepL key if translation is enabled |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service account JSON if Voxy should update dashboard tabs inside the shared Google Sheet |

If `GOOGLE_SERVICE_ACCOUNT_JSON` is not configured, the workflow still sends the weekly email and uploads the Excel dashboard artifact.

## Manual test

In GitHub:

`Actions` -> `Voxy Weekly Review Analysis` -> `Run workflow`

First test without sending:

- `baseline`: `false`
- `dry_run`: `true`

Then run for real:

- `baseline`: `false`
- `dry_run`: `false`

Use `baseline=true` only if you want to save existing reviews without sending any email.
