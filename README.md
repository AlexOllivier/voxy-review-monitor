# Voxy Cloud - GitHub Actions Setup

Voxy Cloud runs from GitHub once per day, reads the shared Google Sheet, checks review pages with Python + Playwright, sends email alerts, and generates a dashboard workbook.

Your PC does not need to be on.

## 1. Create the GitHub repository

Create a new private repository named:

`voxy-review-monitor`

## 2. Upload the files

Upload the contents of the `Voxy GitHub Actions` folder to the root of the repository.

The repository should contain:

- `voxy_review_monitor.py`
- `requirements.txt`
- `.github/workflows/voxy-daily.yml`
- `README.md`

## 3. Configure the Google Sheet

The Google Sheet must keep the `Produits` sheet with these columns:

- `Active`
- `Product name`
- `URL to monitor`
- `Alert emails`
- `Star threshold`
- `Platform`
- `Notes`

Share the sheet so Voxy can download it:

- simplest: `Anyone with the link can view`
- colleagues who add URLs: `Editor`

## 4. Add GitHub secrets

In GitHub:

`Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

Add these secrets:

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
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service account JSON if you want Voxy to update dashboard tabs inside the shared Google Sheet |

## 5. Test manually

In GitHub:

`Actions` -> `Voxy Daily Review Analysis` -> `Run workflow`

The first run can be launched in baseline mode by setting:

`baseline = true`

That saves existing reviews without sending alerts.

## 6. Daily schedule

The workflow is scheduled at `07:00` and `08:00` UTC.

Voxy only runs when the current time in `Europe/Paris` is `09:00`.

This handles winter/summer time more safely:

- winter: 09:00 Paris = 08:00 UTC
- summer: 09:00 Paris = 07:00 UTC

## 7. Dashboard

After each run, Voxy updates the `Dashboard` sheet and one tab per product directly inside the shared Google Sheet when `GOOGLE_SERVICE_ACCOUNT_JSON` is configured.

GitHub also stores a downloadable backup:

`voxy_dashboard_report.xlsx`

as a workflow artifact.

Download it from the workflow run page.

## Important limit

GitHub Actions + Playwright is stronger than Google Apps Script because it runs a browser. Still, some platforms may block automation or change their page structure. Voxy will be more robust than Apps Script, but not guaranteed on every platform.
