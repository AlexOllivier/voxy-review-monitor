import html
import os
import re
import smtplib
import sys
from email.message import EmailMessage

import voxy_review_monitor as base
import voxy_weekly_alert_runner as runner


ORIGINAL_UPDATE_GOOGLE_SHEET_DASHBOARD = runner.update_google_sheet_dashboard


def line_to_html(line: str) -> str:
    escaped = html.escape(line)
    if ": " not in line:
        return f"<tr><td style='padding:8px 0;color:#475467;font-size:14px;line-height:1.45;'>{escaped}</td></tr>"
    label, value = escaped.split(": ", 1)
    label_cell = (
        "color:#667085;font-size:12px;font-weight:500;text-transform:uppercase;"
        "letter-spacing:0;padding:10px 14px 10px 0;border-top:1px solid #edf0f3;"
        "width:170px;vertical-align:top;"
    )
    value_cell = (
        "color:#101828;font-size:14px;font-weight:400;line-height:1.45;"
        "padding:10px 0;border-top:1px solid #edf0f3;text-align:right;vertical-align:top;"
    )
    if label in {"Action", "Link"}:
        value_style = "color:#101828;font-size:14px;font-weight:400;line-height:1.45;text-align:left;word-break:break-word;"
        block_style = "padding:12px 0 4px;border-top:1px solid #edf0f3;text-align:left;"
        if label == "Action":
            block_style = (
                "padding:12px;border:1px solid #f4d35e;background:#fff8db;"
                "border-radius:10px;text-align:left;"
            )
        if label == "Link":
            value = f"<a href='{value}' style='color:#006fd6;text-decoration:none;word-break:break-word;'>{value}</a>"
        return (
            f"<tr><td colspan='2' style='{block_style}'>"
            f"<div style='color:#667085;font-size:12px;font-weight:500;text-transform:uppercase;margin-bottom:6px;'>{label}</div>"
            f"<div style='{value_style}'>{value}</div>"
            f"</td></tr>"
        )
    if label == "Link":
        value = f"<a href='{value}'>{value}</a>"
    return f"<tr><td style='{label_cell}'>{label}</td><td style='{value_cell}'>{value}</td></tr>"


def newsletter_html(plain_body: str) -> str:
    lines = plain_body.splitlines()
    title = html.escape(lines[0] if lines else "Products Reviews Update")
    product_count = "0"
    for line in lines:
        if line.startswith("Products in alert:"):
            product_count = html.escape(line.split(":", 1)[1].strip())
            break

    cards = []
    current = []
    for line in lines[1:]:
        if re.match(r"^\d+\. ", line):
            if current:
                cards.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        cards.append(current)

    card_html = []
    for card in cards:
        heading = html.escape(card[0])
        details = "\n".join(line_to_html(line) for line in card[1:] if line.strip())
        card_html.append(
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            "style='background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;margin-top:18px;'>"
            "<tr><td style='padding:22px;'>"
            f"<h2 style='margin:0 0 16px;color:#101828;font-size:20px;font-weight:600;text-align:center;line-height:1.3;'>{heading}</h2>"
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0'>"
            f"{details}"
            "</table>"
            "</td></tr>"
            "</table>"
        )
    if not card_html:
        card_html.append(
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            "style='background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;margin-top:18px;'>"
            "<tr><td style='padding:28px;text-align:center;'>"
            "<div style='color:#101828;font-size:20px;font-weight:600;margin-bottom:8px;'>No products need attention</div>"
            "<div style='color:#667085;font-size:14px;line-height:1.5;'>No new bad reviews or score alerts were detected in this run.</div>"
            "</td></tr>"
            "</table>"
        )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
</head>
<body style="margin:0;padding:0;background:#f3f5f8;color:#14213d;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f5f8;">
    <tr>
      <td align="center" style="padding:28px 18px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:760px;">
          <tr>
            <td align="center" style="background:#ffffff;border:1px solid #dde3ea;border-radius:16px;padding:28px 24px;text-align:center;">
              <div style="color:#22316f;font-size:30px;font-weight:700;line-height:1;letter-spacing:0;text-align:center;">VoxCity</div>
              <div style="display:inline-block;background:#e7fff7;color:#007f63;border:1px solid #b8f3de;border-radius:999px;padding:8px 16px;font-size:14px;font-weight:500;margin-top:12px;text-align:center;">{product_count} product(s) remaining</div>
              <h1 style="margin:20px 0 10px;color:#101828;font-size:30px;font-weight:600;line-height:1.25;text-align:center;letter-spacing:0;">{title}</h1>
              <div style="color:#667085;font-size:14px;line-height:1.5;text-align:center;white-space:nowrap;">Weekly product review monitoring focused on platform scores, low reviews, and concrete actions.</div>
            </td>
          </tr>
          <tr>
            <td>
    {''.join(card_html)}
              <div style="text-align:center;color:#667085;font-size:12px;margin-top:22px;">Voxy review monitoring - dashboard updated automatically when Google Sheet access is configured.</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_newsletter_email(recipients, subject, body):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM") or user
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in {"true", "1", "yes", "oui"}

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    message.add_alternative(newsletter_html(body), subtype="html")

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(message)


def try_send_newsletter_email(recipients, subject, body):
    try:
        send_newsletter_email(recipients, subject, body)
        return True
    except Exception as exc:
        print(f"Email delivery failed for {', '.join(recipients)}: {exc}")
        return False


def centered_update_google_sheet_dashboard(sheet_url, summaries):
    ORIGINAL_UPDATE_GOOGLE_SHEET_DASHBOARD(sheet_url, summaries)
    client = base.google_client_from_env()
    if client is None:
        return
    spreadsheet = client.open_by_url(sheet_url)
    dashboard = spreadsheet.worksheet("Dashboard")
    dashboard.format("A:M", {
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
    })
    dashboard.format("A1:B5", {
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "textFormat": {
            "bold": True,
            "foregroundColor": {"red": 0.05, "green": 0.08, "blue": 0.16},
        },
        "backgroundColor": {"red": 0.92, "green": 0.98, "blue": 0.96},
    })
    dashboard.format("A7:M7", {
        "horizontalAlignment": "CENTER",
        "textFormat": {
            "bold": True,
            "foregroundColor": {"red": 0, "green": 0, "blue": 0},
        },
        "backgroundColor": {"red": 0.91, "green": 0.94, "blue": 0.98},
    })
    dashboard.format("F:J", {
        "horizontalAlignment": "CENTER",
        "textFormat": {
            "bold": True,
        },
    })
    print("Dashboard cells centered.")


base.try_send_email = try_send_newsletter_email
runner.update_google_sheet_dashboard = centered_update_google_sheet_dashboard


if __name__ == "__main__":
    sys.exit(runner.main())
