# Voxy Cloud - Step-by-step Launch

## Step 1 - Keep your Google Sheet

Your Google Sheet is already the central file.

Your colleagues only edit the `Produits` sheet.

Required columns:

- `Active`
- `Product name`
- `URL to monitor`
- `Alert emails`
- `Star threshold`
- `Platform`
- `Notes`

## Step 2 - Make the Google Sheet readable

In Google Sheets:

1. Click `Share`.
2. Under general access, choose `Anyone with the link`.
3. Set the role to `Viewer`.
4. Copy the Google Sheet link.

Your colleagues can still have `Editor` access individually.

## Step 3 - Create a GitHub account

Go to:

https://github.com

Create an account or log in.

## Step 4 - Create a repository

Click `New repository`.

Use this name:

`voxy-review-monitor`

Recommended:

- Visibility: `Private`
- Do not worry if you leave README unchecked.

Click `Create repository`.

## Step 5 - Upload the Voxy files

Upload all files from the local folder:

`Voxy GitHub Actions`

The GitHub repository must contain:

- `voxy_review_monitor.py`
- `requirements.txt`
- `README.md`
- `.github/workflows/voxy-daily.yml`

Important: the `.github/workflows/voxy-daily.yml` file must be inside exactly this folder path:

`.github/workflows/`

## Step 6 - Add GitHub secrets

In the GitHub repository:

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
- `GOOGLE_SERVICE_ACCOUNT_JSON`

Recommended values:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
ALERT_SUBJECT_PREFIX=[Voxy - Review alert]
```

For Gmail or Google Workspace, `SMTP_PASSWORD` is usually an app password, not your normal password.

## Step 6A - Create the Google permission for dashboard updates

To let GitHub update the Dashboard tabs inside your Google Sheet:

1. Go to Google Cloud Console.
2. Create a project named `Voxy`.
3. Enable the `Google Sheets API`.
4. Create a `Service account`.
5. Create a JSON key for that service account.
6. Copy the full JSON content.
7. In GitHub secrets, create `GOOGLE_SERVICE_ACCOUNT_JSON` and paste the full JSON.
8. In the JSON, find the service account email, usually ending with:
   `iam.gserviceaccount.com`
9. Share your Google Sheet with that service account email as `Editor`.

Without this secret, Voxy can read the public Google Sheet but cannot write the dashboard back into it.

## Step 7 - Run the first baseline

In GitHub:

1. Go to `Actions`.
2. Click `Voxy Daily Review Analysis`.
3. Click `Run workflow`.
4. Set `baseline` to `true`.
5. Click `Run workflow`.

This saves existing reviews without sending alerts.

## Step 8 - Run a dry test

Run the workflow again with:

- `baseline`: `false`
- `dry_run`: `true`

This tests Voxy without sending emails.

## Step 9 - Run for real

Run the workflow again with:

- `baseline`: `false`
- `dry_run`: `false`

Voxy can now send emails if it finds alerts.

## Step 10 - Automatic daily run

The workflow is already scheduled.

It checks at 07:00 UTC and 08:00 UTC, but Voxy only runs when Paris time is 09:00.

This handles summer and winter time.

## Step 11 - Get the dashboard

After each run, Voxy updates the `Dashboard` tab and one tab per product in the shared Google Sheet.

GitHub also keeps a backup:

1. Open the workflow run.
2. Scroll to `Artifacts`.
3. Download `voxy-dashboard-report`.

Inside is:

`voxy_dashboard_report.xlsx`

## Step 12 - Stop the old PC version

If you installed the Windows task before, remove it:

```powershell
Unregister-ScheduledTask -TaskName "Voxy Daily Review Analysis" -Confirm:$false
```

After GitHub works, your PC does not need to stay on.
