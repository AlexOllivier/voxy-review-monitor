# Voxy Cloud - Weekly GitHub Actions Setup

Voxy runs from GitHub once per week, reads the shared Google Sheet, checks review pages, sends an email only when a product has new bad reviews or a score alert, and creates a dashboard artifact.

Your PC does not need to stay on.

## Files in GitHub

These files must be present at the root of the GitHub repository:

- `voxy_review_monitor.py`
- `voxy_weekly_alert_runner.py`
- `voxy_newsletter_runner.py`
- `requirements.txt`
- `README.md`
- `QUICK_START.md`
- `.github/workflows/voxy-daily.yml`

The workflow file must stay in exactly this path:

`.github/workflows/voxy-daily.yml`

## Weekly email behavior

The workflow is scheduled once every Monday at `07:00 UTC`.

In Paris, this is usually:

- `09:00` during summer time
- `08:00` during winter time

GitHub can sometimes start scheduled jobs late. Voxy sends the alert email whenever that Monday job actually starts, so a GitHub delay will not silently block the email.

Email is selective: Voxy sends an email only to recipients whose products have new bad reviews or a score alert. Products with good scores, no bad reviews, or only technical extraction issues are not included in the email.

Alert emails are sent as an HTML newsletter with one visual card per product in alert.

## Google Sheet format

The Google Sheet file can be named `voxy`; the important part is that `GOOGLE_SHEET_URL` points to the correct file and that the product tab is named `Produits`.

Recommended column order in `Produits`:

`Active`, `Country`, `City`, `Owner`, `Product name`, `URL to monitor`, `Alert emails`, `Star threshold`, `Platform`, `Notes`

Supported aliases:

- `Country` or `Pays`
- `City` or `Ville`
- `Owner`, `Product owner`, or `Responsable`

## Dashboard behavior

The Google Sheet `Dashboard` tab is updated for all active products and includes `Country`, `City`, and `Owner`.

Dashboard cells are centered, vertically centered, and wrapped so the table stays readable.

The dashboard action column uses concrete examples from the bad review text when available, instead of generic actions.

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

If `GOOGLE_SERVICE_ACCOUNT_JSON` is not configured, the workflow still sends alert emails and uploads the Excel dashboard artifact.

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
