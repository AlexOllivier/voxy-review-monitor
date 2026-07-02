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
        return f"<div class='line'>{escaped}</div>"
    label, value = escaped.split(": ", 1)
    class_name = "row action" if label == "Action" else "row"
    return f"<div class='{class_name}'><span>{label}</span><strong>{value}</strong></div>"


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
        card_html.append(f"<section class='card'><h2>{heading}</h2>{details}</section>")

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ margin:0; padding:0; background:#f3f5f8; color:#14213d; font-family:Arial, Helvetica, sans-serif; }}
    .wrap {{ max-width:760px; margin:0 auto; padding:28px 18px; }}
    .hero {{ background:#ffffff; border:1px solid #dde3ea; border-radius:16px; padding:24px; text-align:left; }}
    .brand-row {{ display:flex; align-items:center; justify-content:space-between; gap:18px; }}
    .brand {{ display:flex; align-items:center; gap:14px; }}
    .mascot {{ width:68px; height:68px; border-radius:22px; background:#dffcf1; position:relative; flex:0 0 auto; }}
    .mascot:before {{ content:''; position:absolute; width:34px; height:40px; left:17px; top:16px; background:#39d6b2; border-radius:18px 18px 14px 14px; }}
    .mascot:after {{ content:''; position:absolute; width:30px; height:16px; left:19px; top:8px; background:#7067f0; border-radius:16px 16px 4px 4px; }}
    .face {{ position:absolute; left:30px; top:28px; width:8px; height:8px; background:#101828; border-radius:50%; z-index:2; box-shadow:16px 0 0 #101828; }}
    .map {{ position:absolute; left:12px; top:43px; width:26px; height:19px; background:#ffe174; border-radius:4px; transform:rotate(-8deg); z-index:3; }}
    .logo {{ color:#22316f; font-size:28px; font-weight:800; letter-spacing:0; }}
    .logo-mark {{ display:inline-block; color:#00a778; font-weight:900; margin-right:6px; }}
    .hero h1 {{ margin:18px 0 10px; font-size:30px; color:#101828; letter-spacing:0; }}
    .sub {{ color:#667085; font-size:14px; margin:0; }}
    .pill {{ display:inline-block; background:#e7fff7; color:#007f63; border:1px solid #b8f3de; border-radius:999px; padding:8px 14px; font-weight:700; }}
    .card {{ background:white; border:1px solid #e5e7eb; border-radius:12px; margin-top:18px; padding:22px; box-shadow:0 4px 14px rgba(16,24,40,.07); }}
    .card h2 {{ margin:0 0 16px; font-size:20px; color:#101828; }}
    .row {{ display:flex; gap:14px; justify-content:space-between; align-items:flex-start; border-top:1px solid #edf0f3; padding:10px 0; text-align:left; }}
    .row span {{ color:#667085; min-width:170px; font-size:13px; font-weight:700; text-transform:uppercase; }}
    .row strong {{ color:#101828; font-size:14px; text-align:right; font-weight:600; }}
    .action {{ background:#fff8db; border:1px solid #f4d35e; border-radius:10px; padding:12px; margin-top:10px; }}
    .line {{ color:#475467; margin:8px 0; }}
    .footer {{ text-align:center; color:#667085; font-size:12px; margin-top:22px; }}
    @media (max-width:600px) {{
      .row {{ display:block; }}
      .row strong {{ display:block; text-align:left; margin-top:4px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="brand-row">
        <div class="brand">
          <div class="mascot"><div class="face"></div><div class="map"></div></div>
          <div class="logo"><span class="logo-mark">↻</span>VoxCity</div>
        </div>
        <div class="pill">{product_count} product(s) need attention</div>
      </div>
      <h1>{title}</h1>
      <p class="sub">Weekly product review monitoring focused on platform scores, low reviews, and concrete actions.</p>
    </div>
    {''.join(card_html)}
    <div class="footer">Voxy review monitoring - dashboard updated automatically when Google Sheet access is configured.</div>
  </div>
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
    dashboard.format("A:Q", {
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
    })
    dashboard.format("A7:Q7", {
        "horizontalAlignment": "CENTER",
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.91, "green": 0.94, "blue": 0.98},
    })
    print("Dashboard cells centered.")


base.try_send_email = try_send_newsletter_email
runner.update_google_sheet_dashboard = centered_update_google_sheet_dashboard


if __name__ == "__main__":
    sys.exit(runner.main())
