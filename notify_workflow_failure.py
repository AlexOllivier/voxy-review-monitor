import os
import smtplib
from email.message import EmailMessage


def main():
    recipient = os.environ.get("VOXY_FAILURE_ALERT_EMAIL", "alexandre.ollivier@voxcity.co.uk").strip()
    if not recipient:
        print("No failure alert recipient configured.")
        return 0

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM") or user
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in {"true", "1", "yes", "oui"}
    run_url = os.environ.get("GITHUB_RUN_URL", "Not available")
    workflow_name = os.environ.get("GITHUB_WORKFLOW", "Voxy workflow")
    run_id = os.environ.get("GITHUB_RUN_ID", "Not available")
    repository = os.environ.get("GITHUB_REPOSITORY", "Not available")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "Voxy Workflow Failed"
    message.set_content(
        "\n".join([
            "Voxy Workflow Failed",
            "",
            f"Workflow: {workflow_name}",
            f"Repository: {repository}",
            f"Run ID: {run_id}",
            f"Run URL: {run_url}",
            "",
            "Please open the GitHub Actions run and check the failed step logs.",
        ])
    )

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(message)
    print(f"Failure alert sent to: {recipient}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
