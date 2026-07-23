import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import voxy_review_monitor as base


OFFICIAL_RATING_CACHE = {}
HISTORY_HEADERS = [
    "Run timestamp",
    "Product",
    "Country",
    "City",
    "Owner",
    "Platform",
    "Reviews detected",
    "Global score",
    "Score change %",
    "Trend",
    "Low reviews",
    "Critical reviews",
    "Review rating",
    "Review author",
    "Review date",
    "Review text EN",
    "Status",
    "URL",
]


def first_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:[\.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def first_int(value):
    number = first_number(value)
    return int(number) if number is not None else None


def rating_summary_from_json(value):
    if isinstance(value, dict):
        aggregate = value.get("aggregateRating") if isinstance(value.get("aggregateRating"), dict) else value
        rating = first_number(
            aggregate.get("ratingValue")
            or aggregate.get("rating")
            or aggregate.get("averageRating")
        )
        count = first_int(
            aggregate.get("reviewCount")
            or aggregate.get("ratingCount")
            or aggregate.get("count")
        )
        if rating is not None and 0 < rating <= 5:
            return {"score": round(rating, 2), "review_count": count, "source": "page aggregate"}
        for child in value.values():
            found = rating_summary_from_json(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = rating_summary_from_json(child)
            if found:
                return found
    return None


def extract_official_rating_summary(html):
    soup = base.BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        text = script.string or script.get_text(" ", strip=True)
        if not text:
            continue
        try:
            found = rating_summary_from_json(json.loads(text))
        except json.JSONDecodeError:
            continue
        if found:
            return found

    patterns = [
        r'"ratingValue"\s*:\s*"?(\d+(?:[\.,]\d+)?)"?[^{}]{0,250}"reviewCount"\s*:\s*"?(\d+)"?',
        r'"reviewCount"\s*:\s*"?(\d+)"?[^{}]{0,250}"ratingValue"\s*:\s*"?(\d+(?:[\.,]\d+)?)"?',
        r'(\d+(?:[\.,]\d+)?)\s*/\s*5[^0-9]{0,80}(\d+)\s+(?:reviews|avis)',
        r'(\d+(?:[\.,]\d+)?)\s+(\d+)\s+(?:reviews|avis)',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if not match:
            continue
        first = first_number(match.group(1))
        second = first_number(match.group(2))
        if first is None or second is None:
            continue
        score, count = (first, int(second)) if first <= 5 else (second, int(first))
        if 0 < score <= 5:
            return {"score": round(score, 2), "review_count": count, "source": "page text"}
    return {}


def fetch_official_rating_summary_with_browser(url):
    try:
        with base.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(
                locale="fr-FR",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
            )
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
            html = page.content()
            visible_text = page.locator("body").inner_text(timeout=10000)
            browser.close()
        return extract_official_rating_summary(html) or extract_official_rating_summary(visible_text)
    except Exception as exc:
        print(f"Browser official platform score skipped for {url}: {exc}")
        return {}


def fetch_official_rating_summary(url):
    if url in OFFICIAL_RATING_CACHE:
        return OFFICIAL_RATING_CACHE[url]
    summary = {}
    try:
        request = Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        })
        with urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="ignore")
        summary = extract_official_rating_summary(html)
    except Exception as exc:
        print(f"Official platform score skipped for {url}: {exc}")
    if not summary:
        summary = fetch_official_rating_summary_with_browser(url)
    OFFICIAL_RATING_CACHE[url] = summary or {}
    return OFFICIAL_RATING_CACHE[url]


def concrete_action_for_review(review):
    text = base.clean_review_text_for_email(review.text, max_chars=260) or "Review text not available."
    lower_text = text.lower()
    if any(word in lower_text for word in ["wait", "waiting", "queue", "line", "late", "meeting point", "start time", "retard", "attente", "file"]):
        action = "Check the meeting point, start-time instructions, and queue handling against what the customer actually received."
    elif any(word in lower_text for word in ["guide", "staff", "rude", "group", "fast", "slow", "explication", "personnel"]):
        action = "Review the guide assignment and briefing for this departure, then compare the promised tour pace with the customer experience."
    elif any(word in lower_text for word in ["ticket", "entry", "entrance", "access", "voucher", "scan", "billet", "entree", "acces"]):
        action = "Verify the ticket/voucher flow and entrance instructions, then fix the exact step where the customer got blocked."
    elif any(word in lower_text for word in ["refund", "money", "price", "expensive", "value", "remboursement", "prix", "cher"]):
        action = "Check whether the advertised value matches the delivered experience, then prepare a refund or goodwill reply if the promise was missed."
    elif any(word in lower_text for word in ["cancel", "cancelled", "canceled", "annule", "annulation"]):
        action = "Audit the cancellation communication and make sure the customer received clear timing, reason, and next-step options."
    else:
        action = "Read this exact low-rated review and identify the operational failure before replying to the customer."
    return f"{action} Evidence: {review.rating}/5 - {text}"


def build_concrete_actions(product, reviews, limit=3):
    low_reviews = [review for review in reviews if review.rating <= product.threshold]
    return [concrete_action_for_review(review) for review in sorted(low_reviews, key=lambda item: item.rating)[:limit]]


def products_from_rows(rows):
    if not rows:
        return []
    headers = {str(value).strip().lower(): index for index, value in enumerate(rows[0]) if value}
    active_col = headers.get("active", headers.get("actif"))
    name_col = headers.get("product name", headers.get("nom produit"))
    url_col = headers.get("url to monitor", headers.get("url a surveiller", headers.get("url getyourguide")))
    emails_col = headers.get("alert emails", headers.get("emails alerte"))
    threshold_col = headers.get("star threshold", headers.get("seuil etoiles"))
    platform_col = headers.get("platform", headers.get("plateforme"))
    language_col = headers.get("language", headers.get("langue"))
    country_col = headers.get("country", headers.get("pays"))
    city_col = headers.get("city", headers.get("ville"))
    owner_col = headers.get("owner", headers.get("product owner", headers.get("responsable")))
    priority_col = headers.get("priority", headers.get("priorite"))
    alert_type_col = headers.get("alert type", headers.get("type alerte"))
    paused_col = headers.get("paused", headers.get("pause"))
    score_threshold_col = headers.get("score alert threshold", headers.get("seuil score"))
    platform_account_col = headers.get("platform account", headers.get("compte plateforme"))
    if None in {active_col, name_col, url_col, emails_col}:
        raise ValueError("Missing required columns in Google Sheet. Expected: Active, Product name, URL to monitor, Alert emails.")

    products = []
    for row_number, row in enumerate(rows[1:], start=2):
        def cell(index, default=""):
            return row[index] if index is not None and index < len(row) else default

        active = str(cell(active_col)).strip().lower()
        name = str(cell(name_col)).strip()
        url = str(cell(url_col)).strip()
        emails = base.split_emails(str(cell(emails_col)))
        if active not in {"oui", "yes", "true", "1", "x"} or base.truthy(cell(paused_col, "")):
            continue
        if not name and not url:
            continue
        if not name or not url or not emails:
            print(f"Row {row_number} skipped: product name, URL, or alert emails are missing.")
            continue
        product = base.Product(
            name=name,
            url=url,
            emails=emails,
            threshold=base.optional_float(cell(threshold_col, 4), 4.0),
            platform=str(cell(platform_col, "auto")).strip().lower(),
            language=str(cell(language_col, "en")).strip(),
            country=str(cell(country_col, "")).strip(),
            owner=str(cell(owner_col, "")).strip(),
            priority=str(cell(priority_col, "Medium")).strip() or "Medium",
            alert_type=str(cell(alert_type_col, "Both")).strip() or "Both",
            paused=False,
            score_threshold=base.optional_float(cell(score_threshold_col, ""), 3.0),
            platform_account=str(cell(platform_account_col, "")).strip(),
        )
        product.city = str(cell(city_col, "")).strip()
        products.append(product)
    return products


ORIGINAL_SUMMARIZE_REVIEWS = base.summarize_reviews


def summarize_reviews(product, reviews):
    summary = ORIGINAL_SUMMARIZE_REVIEWS(product, reviews)
    detected_score = summary.get("global_score")
    detected_review_count = len(reviews)
    official = fetch_official_rating_summary(product.url)
    official_score = official.get("score")
    official_review_count = official.get("review_count")
    if official_score is not None:
        summary["global_score"] = official_score
        summary["review_count"] = official_review_count or detected_review_count
        summary["alert"] = bool(official_score < product.score_threshold)
    summary["official_score"] = official_score
    summary["official_review_count"] = official_review_count
    summary["official_score_source"] = official.get("source", "")
    summary["detected_score"] = detected_score
    summary["detected_review_count"] = detected_review_count
    summary["city"] = getattr(product, "city", "")
    summary["concrete_actions"] = build_concrete_actions(product, reviews)
    if summary["concrete_actions"]:
        summary["suggestions"] = summary["concrete_actions"]
    return summary


def plain_ascii_text(value, max_chars=900):
    text = base.normalize_text(str(value or ""))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9 .,;:!?()'\"/@&%+-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def review_text_in_english(review):
    original = plain_ascii_text(review.text)
    if not original:
        return "Review text not available."
    translated = base.translate_to_english(original)
    if translated and not translated.lower().startswith("translation unavailable"):
        return plain_ascii_text(translated)
    return original


def annotate_score_evolution_from_history(spreadsheet, summaries):
    previous_scores = base.latest_scores_from_history(spreadsheet)
    for summary in summaries:
        current_score = summary.get("global_score")
        previous_score = previous_scores.get(summary["product"])
        label, delta = base.trend_label(current_score, previous_score)
        summary["trend"] = label
        summary["trend_delta"] = delta
        if current_score is None:
            summary["score_change_percent"] = "No score"
        elif previous_score is None:
            summary["score_change_percent"] = "No history"
        elif previous_score == 0:
            summary["score_change_percent"] = "N/A"
        else:
            change = ((current_score - previous_score) / previous_score) * 100
            sign = "+" if change > 0 else ""
            summary["score_change_percent"] = f"{sign}{change:.1f}%"


def append_history_rows(spreadsheet, summaries):
    minimum_rows = max(100, len(summaries) * 4 + 20)
    history = base.get_or_create_worksheet(spreadsheet, "History", rows=minimum_rows, cols=len(HISTORY_HEADERS))
    history.resize(rows=max(history.row_count, minimum_rows), cols=len(HISTORY_HEADERS))
    rows = history.get_all_values()
    if not rows:
        history.update([HISTORY_HEADERS], value_input_option="USER_ENTERED")
    else:
        current_headers = [header.strip() for header in rows[0]]
        if current_headers != HISTORY_HEADERS:
            history.update("A1:R1", [HISTORY_HEADERS], value_input_option="USER_ENTERED")

    run_timestamp = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d %H:%M")
    new_rows = []
    for summary in summaries:
        low_reviews = [review for review in summary.get("reviews", []) if review.rating <= 4]
        if not low_reviews:
            low_reviews = [None]
        for review in low_reviews:
            new_rows.append([
                run_timestamp,
                summary["product"],
                summary.get("country", ""),
                summary.get("city", ""),
                summary.get("owner", ""),
                summary.get("platform", ""),
                summary.get("review_count", 0),
                summary["global_score"] if summary["global_score"] is not None else "N/A",
                summary.get("score_change_percent", "No history"),
                summary.get("trend", "No history"),
                summary.get("low_review_count", 0),
                summary.get("critical_review_count", 0),
                review.rating if review else "",
                plain_ascii_text(base.clean_author_for_email(review)) if review else "",
                plain_ascii_text(base.clean_date_for_email(review)) if review else "",
                review_text_in_english(review) if review else "",
                summary.get("status", "OK"),
                summary["url"],
            ])
    if new_rows:
        history.append_rows(new_rows, value_input_option="USER_ENTERED")


def dashboard_platform_score(item):
    score = item.get("official_score")
    if score is None:
        score = item.get("global_score")
    return f"{score}/5" if score is not None else "N/A"


def dashboard_review_number(item):
    review_count = item.get("official_review_count")
    if review_count is None:
        review_count = item.get("review_count")
    if review_count is None:
        review_count = item.get("detected_review_count")
    return review_count if review_count is not None else "N/A"


def dashboard_risk_signal(item):
    if item.get("status") == "ERROR":
        return "Technical check"
    if item.get("alert"):
        return "Score alert"
    if item.get("critical_review_count", 0) > 0:
        return "Critical reviews"
    if item.get("low_review_count", 0) > 0:
        return "Low reviews"
    return "Clear"


def dashboard_score_evolution(item):
    return item.get("score_change_percent") or "No history"


def dashboard_main_issue(item):
    themes = [theme for theme in item.get("themes", []) if theme]
    if themes:
        return ", ".join(themes[:2])
    if item.get("critical_review_count", 0) > 0:
        return "Critical customer experience issue"
    if item.get("low_review_count", 0) > 0:
        return "Recent low-rated feedback"
    return "No issue detected"


def email_entry_sort_key(entry):
    summary = entry["summary"]
    score = summary.get("official_score")
    if score is None:
        score = summary.get("global_score")
    if score is None:
        score = summary.get("detected_score")
    priority_rank = {"high": 0, "haute": 0, "medium": 1, "moyenne": 1, "low": 2, "basse": 2}
    priority = str(summary.get("priority") or "Medium").strip().lower()
    return (
        score if score is not None else 99,
        priority_rank.get(priority, 1),
        -int(summary.get("critical_review_count", 0) or 0),
        -int(summary.get("low_review_count", 0) or 0),
        str(summary.get("product", "")).lower(),
    )


def google_rows_for_dashboard(summaries):
    global_summary = base.build_global_synthesis(summaries)
    rows = [
        ["Voxy weekly alert dashboard", ""],
        ["Products checked", global_summary["product_count"]],
        ["Average score", global_summary["average_score"] if global_summary["average_score"] is not None else "N/A"],
        ["Products needing attention", sum(1 for item in summaries if item.get("alert") or item.get("low_review_count", 0) or item.get("critical_review_count", 0))],
        ["Critical reviews", global_summary["critical_reviews"]],
        [],
        ["Product", "Country", "City", "Owner", "Platform", "Score evolution", "Platform score", "Review number", "Risk signal", "Main issue", "Recommended action"],
    ]
    for item in summaries:
        rows.append([
            item["product"],
            item.get("country", ""),
            item.get("city", ""),
            item.get("owner", ""),
            item.get("platform", ""),
            dashboard_score_evolution(item),
            dashboard_platform_score(item),
            dashboard_review_number(item),
            dashboard_risk_signal(item),
            dashboard_main_issue(item),
            (item.get("concrete_actions") or item.get("suggestions") or ["No action needed"])[0],
        ])
    return rows


def update_google_sheet_dashboard(sheet_url, summaries):
    client = base.google_client_from_env()
    if client is None:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is required to update the shared Google Sheet dashboard.")
    spreadsheet = client.open_by_url(sheet_url)
    annotate_score_evolution_from_history(spreadsheet, summaries)
    dashboard = base.get_or_create_worksheet(spreadsheet, "Dashboard", rows=max(100, len(summaries) + 10), cols=11)
    dashboard.resize(rows=max(100, len(summaries) + 10), cols=11)
    dashboard.clear()
    dashboard.update(base.rectangularize_rows(google_rows_for_dashboard(summaries)), value_input_option="USER_ENTERED")
    dashboard.freeze(rows=7, cols=1)
    append_history_rows(spreadsheet, summaries)
    print("Shared Google Sheet alert dashboard updated.")


def build_summary_email_body(recipient, entries, timezone_name):
    now = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
    sorted_entries = sorted(entries, key=email_entry_sort_key)
    lines = [
        "Products Reviews Update",
        "",
        f"Recipient: {recipient}",
        f"Check time: {now} ({timezone_name})",
        f"Products in alert: {len(sorted_entries)}",
        "",
    ]
    if not sorted_entries:
        lines.extend([
            "No new bad reviews or score alerts detected.",
            "",
        ])
    for index, entry in enumerate(sorted_entries, start=1):
        summary = entry["summary"]
        lines.extend([
            f"{index}. {summary['product']}",
            f"Country: {summary.get('country') or 'N/A'}",
            f"City: {summary.get('city') or 'N/A'}",
            f"Owner: {summary.get('owner') or 'N/A'}",
            f"Priority: {summary.get('priority') or 'Medium'}",
            f"Platform: {summary.get('platform') or 'N/A'}",
            f"Status: {base.product_health_with_icon(summary)}",
            f"Trend: {base.trend_with_arrow(summary)}",
            f"Score: {summary['global_score'] if summary['global_score'] is not None else 'N/A'}/5",
            *([f"Platform score: {summary.get('official_score')}/5 from {summary.get('official_review_count') or 'N/A'} reviews"] if summary.get("official_score") is not None else []),
            f"Voxy sample: {summary.get('detected_score') if summary.get('detected_score') is not None else 'N/A'}/5 from {summary.get('detected_review_count') or 0} detected reviews",
            f"Low reviews: {summary['low_review_count']}",
            f"Critical reviews < 3: {summary['critical_review_count']}",
            f"Action: {(summary.get('concrete_actions') or summary.get('suggestions') or ['Review product feedback'])[0]}",
            f"Link: {summary['url']}",
            "",
        ])
    return "\n".join(lines).strip()


def main():
    parser = argparse.ArgumentParser(description="Monitor reviews and send selective bad-review alerts.")
    parser.add_argument("--xlsx", default="template_surveillance_avis_multi_plateformes.xlsx")
    parser.add_argument("--sheet-url", default="")
    parser.add_argument("--state", default="avis_deja_signales.json")
    parser.add_argument("--report", default="voxy_dashboard_report.xlsx")
    parser.add_argument("--timezone", default="Europe/Paris")
    parser.add_argument("--only-at-hour", type=int, default=None)
    parser.add_argument("--update-google-sheet-dashboard", action="store_true")
    parser.add_argument("--include-past-dated-reviews", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()

    base.load_dotenv()
    base.products_from_rows = products_from_rows
    base.summarize_reviews = summarize_reviews
    base.google_rows_for_dashboard = google_rows_for_dashboard
    base.update_google_sheet_dashboard = update_google_sheet_dashboard
    base.build_summary_email_body = build_summary_email_body

    sheet_url = args.sheet_url or os.environ.get("GOOGLE_SHEET_URL", "")
    input_xlsx = Path(args.xlsx)
    if sheet_url and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip():
        products = base.load_products_from_google_sheet(sheet_url)
    elif sheet_url:
        input_xlsx = base.download_google_sheet(sheet_url, Path("voxy_current_shared_sheet.xlsx"))
        print(f"Downloaded shared Google Sheet to: {input_xlsx}")
        products = base.load_products(input_xlsx)
    else:
        products = base.load_products(input_xlsx)

    seen = base.load_seen(Path(args.state))
    new_seen = set(seen)
    subject_prefix = base.english_subject_prefix()
    local_now = datetime.now(ZoneInfo(args.timezone))
    local_today = local_now.date()
    if args.only_at_hour is not None and local_now.hour != args.only_at_hour:
        print(f"Not the scheduled local hour yet: {local_now.strftime('%Y-%m-%d %H:%M')} ({args.timezone}).")
        return 0
    if not products:
        print("No active products found.")
        return 0

    summaries = []
    alert_entries_by_recipient = {}
    for product in products:
        print(f"Checking: {product.name}")
        try:
            reviews = base.fetch_reviews(product)
        except Exception as exc:
            print(f"Error while checking {product.name}: {exc}")
            error_summary = base.summarize_error(product, exc)
            error_summary["city"] = getattr(product, "city", "")
            summaries.append(error_summary)
            continue

        for review in reviews:
            new_seen.add(base.seen_key(product, review))

        if args.include_past_dated_reviews:
            current_reviews = reviews
        else:
            current_reviews = [review for review in reviews if not base.is_past_dated_review(review, local_today)]
            skipped = len(reviews) - len(current_reviews)
            if skipped:
                print(f"Skipped {skipped} review(s) dated before {local_today.isoformat()} ({args.timezone}).")

        summary = summarize_reviews(product, current_reviews)
        summaries.append(summary)
        low_reviews = [
            review for review in current_reviews
            if review.rating <= product.threshold and base.seen_key(product, review) not in seen
        ]
        if args.baseline:
            print(f"Baseline: {len(reviews)} reviews saved, no email sent.")
            continue

        alert_reasons = []
        if summary["alert"] and base.score_alert_key(product, summary) not in seen and base.alert_type_allows(product, "score"):
            alert_reasons.append(f"score below {product.score_threshold}")
            new_seen.add(base.score_alert_key(product, summary))
        if low_reviews and base.alert_type_allows(product, "low_review"):
            alert_reasons.append(f"{len(low_reviews)} new review(s) <= {product.threshold}/5")
        if alert_reasons:
            for recipient in product.emails:
                alert_entries_by_recipient.setdefault(recipient, []).append({"summary": summary, "reasons": alert_reasons})
        else:
            print(f"OK: no new bad reviews or score alert for {product.name}.")

    if alert_entries_by_recipient and not args.baseline:
        base.annotate_email_trends(sheet_url, summaries)
        for recipient, entries in sorted(alert_entries_by_recipient.items()):
            body = build_summary_email_body(recipient, entries, args.timezone)
            subject = "Products Reviews Update"
            if args.dry_run:
                print("DRY RUN - bad review alert email not sent")
                print(body)
            elif base.try_send_email([recipient], subject, body):
                print(f"Bad review alert sent to: {recipient}")
    elif not args.baseline:
        manual_run = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        recipients = sorted({email for product in products for email in product.emails})
        if manual_run and recipients:
            print("OK: no products with new bad reviews or score alerts. Sending manual confirmation email.")
            for recipient in recipients:
                body = build_summary_email_body(recipient, [], args.timezone)
                subject = "Products Reviews Update"
                if args.dry_run:
                    print("DRY RUN - no-alert confirmation email not sent")
                    print(body)
                elif base.try_send_email([recipient], subject, body):
                    print(f"No-alert confirmation sent to: {recipient}")
        else:
            print("OK: no products with new bad reviews or score alerts to email.")

    if summaries:
        base.build_dashboard_report(summaries, Path(args.report))
        print(f"Dashboard report saved: {args.report}")
        if args.update_google_sheet_dashboard:
            if not sheet_url:
                raise RuntimeError("--update-google-sheet-dashboard requires GOOGLE_SHEET_URL or --sheet-url.")
            update_google_sheet_dashboard(sheet_url, summaries)

    base.save_seen(Path(args.state), new_seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
