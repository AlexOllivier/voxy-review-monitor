import argparse
import hashlib
import json
import multiprocessing
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import voxy_review_monitor as base


OFFICIAL_RATING_CACHE = {}
HISTORY_HEADERS = [
    "Run timestamp",
    "Row type",
    "Product",
    "Country",
    "City",
    "Owner",
    "Platform",
    "URL",
    "Reviews detected",
    "Global score",
    "Score change vs last week",
    "Trend",
    "Risk signal",
    "Main issue",
    "Recommended action",
    "Low reviews",
    "Review rating",
    "Review severity",
    "Review author",
    "Review date",
    "Review text EN",
    "Review source",
    "Review ID",
    "Critical reviews",
    "Status",
]

ISSUE_SIGNALS = {
    "Booking / access / ticketing": [
        "ticket", "tickets", "entry", "entrance", "access", "voucher", "scan", "barcode", "qr",
        "booking", "reservation", "billet", "entree", "acces", "file", "queue",
    ],
    "Meeting point / timing": [
        "meeting point", "meet", "location", "address", "late", "delay", "waiting", "wait",
        "start time", "time slot", "retard", "attente", "horaire", "rendez",
    ],
    "Guide / staff experience": [
        "guide", "staff", "rude", "unfriendly", "friendly", "explanation", "explained", "group",
        "fast", "slow", "boring", "personnel", "explication", "groupe",
    ],
    "Audio guide / app / language": [
        "audio", "audioguide", "app", "application", "download", "headphone", "language",
        "translation", "anglais", "francais", "espagnol",
    ],
    "Value for money": [
        "expensive", "price", "money", "value", "worth", "overpriced", "refund", "remboursement",
        "prix", "cher", "argent",
    ],
    "Cancellation / availability": [
        "cancel", "cancelled", "canceled", "cancellation", "unavailable", "closed", "annule",
        "annulation", "ferme",
    ],
    "Experience quality": [
        "disappointed", "disappointing", "bad", "poor", "terrible", "awful", "not worth",
        "waste", "confusing", "crowded", "decu", "decevant", "mauvais",
    ],
}

ISSUE_ACTIONS = {
    "Booking / access / ticketing": "Audit the ticket, voucher, QR code, and entrance flow on the OTA page, then fix the exact access step that creates friction.",
    "Meeting point / timing": "Check the meeting point, address, start-time instructions, and queue handling against the customer journey described in the review.",
    "Guide / staff experience": "Review guide/staff briefing, tour pace, group management, and service tone for this product before replying to the customer.",
    "Audio guide / app / language": "Test the audio/app instructions from the customer perspective and clarify download, language, and on-site usage steps.",
    "Value for money": "Compare the advertised promise with the delivered experience and adjust listing copy, pricing expectations, or goodwill response if needed.",
    "Cancellation / availability": "Audit cancellation, closure, and availability communication so customers receive clear timing, reason, and next-step options.",
    "Experience quality": "Read the lowest-rated reviews and identify the concrete operational gap that made the experience feel poor.",
}


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


def review_count_from_value(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    match = re.search(r"\d[\d\s.,]*", text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits else None


def rating_summary_from_json(value):
    if isinstance(value, dict):
        aggregate = value.get("aggregateRating") if isinstance(value.get("aggregateRating"), dict) else value
        rating = first_number(
            aggregate.get("ratingValue")
            or aggregate.get("rating")
            or aggregate.get("averageRating")
        )
        count = review_count_from_value(
            aggregate.get("reviewCount")
            or aggregate.get("ratingCount")
            or aggregate.get("reviewsCount")
            or aggregate.get("totalReviews")
            or aggregate.get("numberOfReviews")
            or aggregate.get("reviews_count")
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


def clean_page_text_for_rating(value):
    text = base.html_to_text(str(value or ""))
    text = base.normalize_text(text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def nearby_review_count(text, start, end, radius=180):
    window_start = max(0, start - radius)
    window_end = min(len(text), end + radius)
    window = text[window_start:window_end]
    count_patterns = [
        r"(\d[\d\s.,]*)\s*(?:reviews|review|avis|opiniones|recensioni|bewertungen|beoordelingen)",
        r"(?:reviews|review|avis|opiniones|recensioni|bewertungen|beoordelingen)\s*(\d[\d\s.,]*)",
    ]
    for pattern in count_patterns:
        match = re.search(pattern, window, flags=re.IGNORECASE)
        if match:
            count = review_count_from_value(match.group(1))
            if count is not None:
                return count
    return None


def extract_rating_summary_from_text(text):
    text = clean_page_text_for_rating(text)
    if not text:
        return {}

    paired_patterns = [
        r"(?<!\d)([1-5](?:[\.,]\d{1,2})?)\s*(?:/ ?5|out of 5|sur 5).{0,180}?(\d[\d\s.,]*)\s*(?:reviews|review|avis|opiniones|recensioni|bewertungen|beoordelingen)",
        r"(?<!\d)([1-5](?:[\.,]\d{1,2})?)\s+(\d[\d\s.,]*)\s*(?:reviews|review|avis|opiniones|recensioni|bewertungen|beoordelingen)",
        r"(\d[\d\s.,]*)\s*(?:reviews|review|avis|opiniones|recensioni|bewertungen|beoordelingen).{0,180}?(?<!\d)([1-5](?:[\.,]\d{1,2})?)\s*(?:/ ?5|out of 5|sur 5)?",
    ]
    for pattern in paired_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        first = first_number(match.group(1))
        second = first_number(match.group(2))
        count_first = review_count_from_value(match.group(1))
        count_second = review_count_from_value(match.group(2))
        if first is None or second is None:
            continue
        score, count = (first, count_second) if first <= 5 else (second, count_first)
        if count is not None and 0 < score <= 5:
            return {"score": round(score, 2), "review_count": count, "source": "visible page text"}

    score_patterns = [
        r"(?<!\d)([1-5](?:[\.,]\d{1,2})?)\s*(?:/ ?5|out of 5|sur 5)",
        r"(?:rating|note|score)\s*[:\-]?\s*([1-5](?:[\.,]\d{1,2})?)",
    ]
    for pattern in score_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            score = first_number(match.group(1))
            count = nearby_review_count(text, match.start(), match.end())
            if score is not None and count is not None and 0 < score <= 5:
                return {"score": round(score, 2), "review_count": count, "source": "visible page text"}
    return {}


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
    visible_text = soup.get_text(" ", strip=True)
    return extract_rating_summary_from_text(visible_text) or extract_rating_summary_from_text(html)


def exhaust_product_page(page):
    click_labels = [
        "accept", "accepter", "j'accepte", "allow all", "tout accepter",
        "reviews", "avis", "customer reviews", "see reviews", "all reviews",
        "read reviews", "more reviews", "show more", "load more", "read more",
        "voir les avis", "tous les avis", "voir plus", "afficher plus",
    ]
    for label in click_labels:
        for _ in range(3):
            try:
                page.get_by_text(re.compile(label, re.I)).first.click(timeout=1800)
                page.wait_for_timeout(1200)
            except Exception:
                break

    previous_height = 0
    stable_rounds = 0
    for _ in range(18):
        try:
            height = page.evaluate("document.body.scrollHeight")
        except Exception:
            height = previous_height
        page.mouse.wheel(0, 4200)
        page.wait_for_timeout(900)
        for label in ["show more", "load more", "read more", "voir plus", "afficher plus", "more reviews", "tous les avis"]:
            try:
                page.get_by_text(re.compile(label, re.I)).first.click(timeout=900)
                page.wait_for_timeout(800)
            except Exception:
                pass
        if height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_height = height
        if stable_rounds >= 3:
            break
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
    except Exception:
        pass


def wait_for_real_product_page(page):
    blocked_markers = [
        "just a moment",
        "enable javascript and cookies",
        "checking your browser",
        "cf_chl",
        "challenge-platform",
    ]
    for _ in range(4):
        try:
            html = page.content().lower()
            text = page.locator("body").inner_text(timeout=5000).lower()
        except Exception:
            html = ""
            text = ""
        if not any(marker in html or marker in text for marker in blocked_markers):
            return True
        page.wait_for_timeout(8000)
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
    return False


def new_stealth_page(browser, locale="en-US"):
    context = browser.new_context(
        locale=locale,
        timezone_id="Europe/Paris",
        viewport={"width": 1440, "height": 1200},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en', 'fr']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    """)
    return context, context.new_page()


def fetch_official_rating_summary_with_browser(url):
    try:
        with base.sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            context, page = new_stealth_page(browser, locale="en-US")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            real_page_loaded = wait_for_real_product_page(page)
            exhaust_product_page(page)
            html = page.content()
            visible_text = page.locator("body").inner_text(timeout=10000)
            context.close()
            browser.close()
        if not real_page_loaded:
            return {"blocked": True, "source": "platform anti-bot challenge"}
        return (
            extract_official_rating_summary(html)
            or extract_rating_summary_from_text(visible_text)
            or extract_rating_summary_from_text(html)
        )
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
    text = plain_ascii_text(review_text_in_english(review), max_chars=260) or "Review text not available."
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


def issue_signal_scores(reviews):
    scores = {}
    evidence = {}
    for review in reviews:
        text = plain_ascii_text(review.text, max_chars=1600).lower()
        if not text:
            continue
        weight = max(1, int(6 - review.rating))
        for issue, keywords in ISSUE_SIGNALS.items():
            hits = sum(1 for keyword in keywords if keyword in text)
            if hits:
                scores[issue] = scores.get(issue, 0) + hits * weight
                evidence.setdefault(issue, []).append(review)
    return scores, evidence


def analyze_review_wording(reviews):
    low_reviews = [review for review in reviews if review.rating <= 4]
    if not low_reviews:
        return {
            "main_issue": "No issue detected",
            "recommended_action": "No action needed",
            "issue_evidence": "",
        }
    scores, evidence = issue_signal_scores(low_reviews)
    if scores:
        main_issue = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]
        selected_reviews = sorted(evidence.get(main_issue, []), key=lambda item: item.rating)[:2]
    else:
        main_issue = "Experience quality"
        selected_reviews = sorted(low_reviews, key=lambda item: item.rating)[:2]

    snippets = []
    for review in selected_reviews:
        text = review_text_in_english(review)
        snippets.append(f"{review.rating}/5: {text[:180]}")
    return {
        "main_issue": main_issue,
        "recommended_action": ISSUE_ACTIONS.get(main_issue, ISSUE_ACTIONS["Experience quality"]),
        "issue_evidence": " | ".join(snippets),
    }


def build_concrete_actions(product, reviews, limit=3):
    low_reviews = [review for review in reviews if review.rating <= product.threshold]
    wording = analyze_review_wording(low_reviews)
    actions = []
    if wording["recommended_action"] != "No action needed":
        evidence = f" Evidence: {wording['issue_evidence']}" if wording.get("issue_evidence") else ""
        actions.append(f"{wording['recommended_action']}{evidence}")
    actions.extend(concrete_action_for_review(review) for review in sorted(low_reviews, key=lambda item: item.rating)[:limit])
    return actions[:limit]


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
        if official_review_count is not None:
            summary["review_count"] = official_review_count
        elif detected_review_count:
            summary["review_count"] = detected_review_count
        summary["alert"] = bool(official_score < product.score_threshold)
    summary["official_score"] = official_score
    summary["official_review_count"] = official_review_count
    summary["official_score_source"] = official.get("source", "")
    summary["detected_score"] = detected_score
    summary["detected_review_count"] = detected_review_count
    summary["city"] = getattr(product, "city", "")
    if official.get("blocked"):
        summary["status"] = "TECHNICAL_CHECK"
        summary["error"] = "Blocked or unreadable URL: platform anti-bot page returned instead of product content."
        summary["data_quality_note"] = "Blocked or unreadable URL"
    elif official_score is None and not detected_review_count:
        summary["status"] = "TECHNICAL_CHECK"
        summary["error"] = "No score or reviews extracted after full URL traversal."
        summary["data_quality_note"] = "No score or reviews extracted"
    elif official_score is None:
        summary["data_quality_note"] = "Platform score not extracted"
    elif official_review_count is None and not detected_review_count:
        summary["data_quality_note"] = "Review count not extracted"
    else:
        summary["data_quality_note"] = ""
    wording = analyze_review_wording(reviews)
    summary["main_issue"] = wording["main_issue"]
    summary["recommended_action"] = wording["recommended_action"]
    summary["issue_evidence"] = wording["issue_evidence"]
    summary["concrete_actions"] = build_concrete_actions(product, reviews)
    if summary["concrete_actions"]:
        summary["suggestions"] = summary["concrete_actions"]
    return summary


def repair_mojibake_text(value):
    text = str(value or "")
    markers = ("Ã", "Â", "â", "ðŸ", "�")
    if any(marker in text for marker in markers):
        try:
            repaired = text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired and sum(repaired.count(marker) for marker in markers) < sum(text.count(marker) for marker in markers):
                text = repaired
        except Exception:
            pass
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return base.normalize_text(text)


def plain_ascii_text(value, max_chars=900):
    text = repair_mojibake_text(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9 .,;:!?()'\"/@&%+-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def email_safe_text(value, max_chars=900):
    return plain_ascii_text(value, max_chars=max_chars) or "Not provided"


def review_text_in_english(review):
    original = base.normalize_text(str(review.text or ""))
    if not original.strip():
        return "Review text not available."
    translated = base.translate_to_english(original)
    if translated and not translated.lower().startswith("translation unavailable"):
        return plain_ascii_text(translated)
    return plain_ascii_text(original)


def review_severity(review):
    if review is None:
        return ""
    if review.rating < 3:
        return "Critical"
    if review.rating < 4:
        return "Poor"
    return "Low"


def history_minimum_rows(summaries):
    return max(100, len(summaries) * 4 + 20)


def history_needs_reset(rows):
    if not rows:
        return True
    current_headers = [header.strip() for header in rows[0]]
    if current_headers != HISTORY_HEADERS:
        return True
    for row in rows[1:]:
        if any(str(cell).strip() for cell in row) and len(row) < len(HISTORY_HEADERS):
            return True
    return False


def value_from_history_row(row, header_lookup, header):
    index = header_lookup.get(header.lower())
    return row[index] if index is not None and index < len(row) else ""


def migrate_history_rows(rows):
    if not rows:
        return [HISTORY_HEADERS]
    old_headers = [header.strip().lower() for header in rows[0]]
    header_lookup = {header: index for index, header in enumerate(old_headers) if header}
    migrated = [HISTORY_HEADERS]
    for row in rows[1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        new_row = []
        for header in HISTORY_HEADERS:
            if header == "Review severity":
                rating = base.parse_optional_float(value_from_history_row(row, header_lookup, "Review rating"))
                if rating is None:
                    new_row.append("")
                else:
                    class ReviewStub:
                        def __init__(self, rating):
                            self.rating = rating
                    new_row.append(review_severity(ReviewStub(rating)))
                continue
            new_row.append(value_from_history_row(row, header_lookup, header))
        migrated.append(new_row)
    return migrated


def prepared_history_worksheet(spreadsheet, summaries, reset_incompatible=True):
    minimum_rows = history_minimum_rows(summaries)
    history = base.get_or_create_worksheet(spreadsheet, "History", rows=minimum_rows, cols=len(HISTORY_HEADERS))
    history.resize(rows=max(history.row_count, minimum_rows), cols=len(HISTORY_HEADERS))
    rows = history.get_all_values()
    if reset_incompatible and history_needs_reset(rows):
        migrated_rows = migrate_history_rows(rows)
        history.clear()
        history.update(base.rectangularize_rows(migrated_rows), value_input_option="USER_ENTERED")
        rows = migrated_rows
    return history, rows


def parse_history_timestamp(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def current_score_value(summary):
    for key in ("official_score", "global_score", "detected_score"):
        score = summary.get(key)
        if score is not None:
            return score
    return None


def current_review_count(summary):
    for key in ("official_review_count", "review_count", "detected_review_count"):
        count = summary.get(key)
        if count is not None:
            return int(count)
    return None


def metric_from_history_row(row, headers):
    try:
        product_index = headers.index("product")
    except ValueError:
        return None
    if len(row) <= product_index:
        return None
    product = row[product_index].strip()
    if not product:
        return None

    try:
        review_index = headers.index("reviews detected")
        score_index = headers.index("global score")
        if len(row) > max(review_index, score_index):
            review_count = base.parse_optional_float(row[review_index])
            score = base.parse_optional_float(row[score_index])
            if review_count is not None and score is not None:
                return product, int(review_count), score
    except ValueError:
        pass

    numeric_cells = []
    for index, cell in enumerate(row[product_index + 1:], start=product_index + 1):
        number = base.parse_optional_float(cell)
        if number is not None:
            numeric_cells.append((index, number))
    for index, number in numeric_cells:
        if number > 5:
            score = next((candidate for candidate_index, candidate in numeric_cells if candidate_index > index and candidate <= 5), None)
            if score is not None:
                return product, int(number), score
    return None


def previous_week_metrics_from_history_rows(rows, now=None):
    latest = {}
    if len(rows) < 2:
        return latest
    now = now or datetime.now(ZoneInfo("Europe/Paris"))
    target_date = now.date() - timedelta(days=7)
    target_iso_year, target_iso_week, _ = target_date.isocalendar()
    headers = [header.strip().lower() for header in rows[0]]
    try:
        timestamp_index = headers.index("run timestamp")
    except ValueError:
        return latest
    for row in rows[1:]:
        if len(row) <= timestamp_index:
            continue
        timestamp = parse_history_timestamp(row[timestamp_index])
        if timestamp is None:
            continue
        iso_year, iso_week, _ = timestamp.date().isocalendar()
        if iso_year != target_iso_year or iso_week != target_iso_week:
            continue
        metric = metric_from_history_row(row, headers)
        if metric is None:
            continue
        product, review_count, score = metric
        current = latest.get(product)
        if current is None or timestamp > current["timestamp"]:
            latest[product] = {
                "timestamp": timestamp,
                "score": score,
                "review_count": review_count,
            }
    return latest


def review_history_id(summary, review):
    raw = "|".join([
        str(summary.get("product", "")),
        str(summary.get("url", "")),
        str(review.rating),
        base.clean_author_for_email(review),
        base.clean_date_for_email(review),
        base.normalize_text(review.text or "")[:500],
    ])
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def existing_review_ids(rows):
    if len(rows) < 2:
        return set()
    headers = [header.strip().lower() for header in rows[0]]
    try:
        row_type_index = headers.index("row type")
        review_id_index = headers.index("review id")
    except ValueError:
        return set()
    ids = set()
    for row in rows[1:]:
        if len(row) <= max(row_type_index, review_id_index):
            continue
        if row[row_type_index].strip().upper() == "LOW_REVIEW" and row[review_id_index].strip():
            ids.add(row[review_id_index].strip())
    return ids


def annotate_score_evolution_from_history(spreadsheet, summaries):
    _, rows = prepared_history_worksheet(spreadsheet, summaries, reset_incompatible=False)
    previous_metrics = previous_week_metrics_from_history_rows(rows)
    for summary in summaries:
        current_score = current_score_value(summary)
        current_reviews = current_review_count(summary)
        if current_score is None or current_reviews is None:
            summary["status"] = "TECHNICAL_CHECK"
            summary["error"] = "Missing score or review count for this URL."
        previous = previous_metrics.get(summary["product"])
        previous_score = previous["score"] if previous else None
        label, delta = base.trend_label(current_score, previous_score)
        summary["trend"] = label
        summary["trend_delta"] = delta
        if current_score is None:
            summary["score_change"] = "Check required"
        elif previous_score is None:
            summary["score_change"] = "No history"
        else:
            change = round(current_score - previous_score, 2)
            sign = "+" if change > 0 else ""
            summary["score_change"] = f"{sign}{change:.1f} vs last week"


def append_history_rows(spreadsheet, summaries):
    history, rows = prepared_history_worksheet(spreadsheet, summaries)
    known_review_ids = existing_review_ids(rows)
    run_timestamp = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d %H:%M")
    new_rows = []
    for summary in summaries:
        main_issue = dashboard_main_issue(summary)
        recommended_action = (summary.get("recommended_action") or summary.get("concrete_actions") or summary.get("suggestions") or ["No action needed"])
        if isinstance(recommended_action, list):
            recommended_action = recommended_action[0]
        new_rows.append([
            run_timestamp,
            "PRODUCT_SUMMARY",
            summary["product"],
            summary.get("country", ""),
            summary.get("city", ""),
            summary.get("owner", ""),
            summary.get("platform", ""),
            summary["url"],
            current_review_count(summary) if current_review_count(summary) is not None else "Check required",
            current_score_value(summary) if current_score_value(summary) is not None else "Check required",
            summary.get("score_change", "No history"),
            summary.get("trend", "No history"),
            dashboard_risk_signal(summary),
            main_issue,
            recommended_action,
            summary.get("low_review_count", 0),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            summary.get("critical_review_count", 0),
            summary.get("status", "OK"),
        ])
        low_reviews = [review for review in summary.get("reviews", []) if review.rating <= 4]
        for review in low_reviews:
            review_id = review_history_id(summary, review)
            if review_id in known_review_ids:
                continue
            known_review_ids.add(review_id)
            new_rows.append([
                run_timestamp,
                "LOW_REVIEW",
                summary["product"],
                summary.get("country", ""),
                summary.get("city", ""),
                summary.get("owner", ""),
                summary.get("platform", ""),
                summary["url"],
                current_review_count(summary) if current_review_count(summary) is not None else "Check required",
                current_score_value(summary) if current_score_value(summary) is not None else "Check required",
                summary.get("score_change", "No history"),
                summary.get("trend", "No history"),
                dashboard_risk_signal(summary),
            main_issue,
            recommended_action,
            summary.get("low_review_count", 0),
            review.rating,
            review_severity(review),
            plain_ascii_text(base.clean_author_for_email(review)),
            plain_ascii_text(base.clean_date_for_email(review)),
            review_text_in_english(review),
            plain_ascii_text(getattr(review, "source", "")),
            review_id,
            summary.get("critical_review_count", 0),
            summary.get("status", "OK"),
        ])
    if new_rows:
        history.append_rows(new_rows, value_input_option="USER_ENTERED")


def dashboard_platform_score(item):
    score = current_score_value(item)
    return f"{score}/5" if score is not None else "Check required"


def dashboard_review_number(item):
    review_count = item.get("official_review_count")
    if review_count is None:
        review_count = item.get("review_count")
    if review_count is None:
        review_count = item.get("detected_review_count")
    return review_count if review_count is not None else "Check required"


def dashboard_risk_signal(item):
    if item.get("status") in {"ERROR", "TECHNICAL_CHECK"}:
        return "Technical check"
    if current_score_value(item) is None or current_review_count(item) is None:
        return "Technical check"
    if item.get("alert"):
        return "Score alert"
    if item.get("critical_review_count", 0) > 0:
        return "Critical reviews"
    if item.get("low_review_count", 0) > 0:
        return "Low reviews"
    return "Clear"


def dashboard_score_evolution(item):
    value = item.get("score_change") or "No history"
    return "Check required" if str(value).lower() in {"no score", "score unavailable", "n/a"} else value


def dashboard_main_issue(item):
    if item.get("data_quality_note"):
        return item["data_quality_note"]
    if item.get("main_issue"):
        return item["main_issue"]
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
        ["Average score", global_summary["average_score"] if global_summary["average_score"] is not None else "Check required"],
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
            item.get("recommended_action") or (item.get("concrete_actions") or item.get("suggestions") or ["No action needed"])[0],
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
            f"{index}. {email_safe_text(summary['product'], 220)}",
            f"Country: {email_safe_text(summary.get('country'))}",
            f"City: {email_safe_text(summary.get('city'))}",
            f"Owner: {email_safe_text(summary.get('owner'))}",
            f"Priority: {email_safe_text(summary.get('priority') or 'Medium')}",
            f"Platform: {email_safe_text(summary.get('platform'))}",
            f"Status: {email_safe_text(base.product_health(summary))}",
            f"Score evolution: {email_safe_text(dashboard_score_evolution(summary))}",
            f"Score: {current_score_value(summary) if current_score_value(summary) is not None else 'Check required'}/5",
            *([f"Platform score: {summary.get('official_score')}/5 from {summary.get('official_review_count') or 'Check required'} reviews"] if summary.get("official_score") is not None else []),
            f"Voxy sample: {summary.get('detected_score') if summary.get('detected_score') is not None else 'Check required'}/5 from {summary.get('detected_review_count') or 0} detected reviews",
            f"Low reviews: {summary['low_review_count']}",
            f"Critical reviews < 3: {summary['critical_review_count']}",
            f"Action: {email_safe_text((summary.get('concrete_actions') or summary.get('suggestions') or ['Review product feedback'])[0], 1100)}",
            f"Link: {summary['url']}",
            "",
        ])
    return "\n".join(lines).strip()


def failure_alert_email():
    return os.environ.get("VOXY_FAILURE_ALERT_EMAIL", "alexandre.ollivier@voxcity.co.uk").strip()


def send_technical_issue_alert(summaries, timezone_name):
    recipient = failure_alert_email()
    if not recipient:
        return
    technical_items = [
        summary for summary in summaries
        if summary.get("status") in {"ERROR", "TECHNICAL_CHECK"} or summary.get("error")
    ]
    if not technical_items:
        return
    now = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
    lines = [
        "Voxy Technical Alert",
        "",
        f"Check time: {now} ({timezone_name})",
        f"Product(s) needing technical attention: {len(technical_items)}",
        "",
    ]
    for index, summary in enumerate(technical_items, start=1):
        lines.extend([
            f"{index}. {email_safe_text(summary.get('product'), 220)}",
            f"Platform: {email_safe_text(summary.get('platform'))}",
            f"Status: {email_safe_text(summary.get('status') or 'Technical check')}",
            f"Issue: {email_safe_text(summary.get('error') or 'Missing score, review count, or readable review data.', 500)}",
            f"URL: {summary.get('url') or 'Not provided'}",
            "",
        ])
    base.try_send_email([recipient], "Voxy Technical Alert", "\n".join(lines).strip())


def product_timeout_seconds():
    try:
        return int(os.environ.get("VOXY_PRODUCT_TIMEOUT_SECONDS", "180"))
    except ValueError:
        return 180


def max_parallel_products():
    try:
        value = int(os.environ.get("VOXY_MAX_PARALLEL_PRODUCTS", "2"))
    except ValueError:
        value = 2
    return max(1, min(value, 6))


def product_retry_attempts():
    try:
        value = int(os.environ.get("VOXY_PRODUCT_RETRY_ATTEMPTS", "1"))
    except ValueError:
        value = 1
    return max(0, min(value, 2))


def product_check_worker(product, result_queue):
    try:
        reviews = base.fetch_reviews(product)
        summary = summarize_reviews(product, reviews)
        result_queue.put(("ok", reviews, summary))
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def check_product_with_timeout(product):
    timeout_seconds = product_timeout_seconds()
    if timeout_seconds <= 0:
        reviews = base.fetch_reviews(product)
        summary = summarize_reviews(product, reviews)
        return reviews, summary

    context = multiprocessing.get_context()
    result_queue = context.Queue()
    process = context.Process(target=product_check_worker, args=(product, result_queue))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(10)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(5)
        raise TimeoutError(f"Product check exceeded {timeout_seconds} seconds.")
    if result_queue.empty():
        raise RuntimeError(f"Product check stopped without returning data. Exit code: {process.exitcode}.")
    status, *payload = result_queue.get()
    if status == "ok":
        return payload[0], payload[1]
    raise RuntimeError(payload[0] if payload else "Product check failed.")


def stop_product_process(process):
    if not process.is_alive():
        return
    process.terminate()
    process.join(10)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(5)


def iter_product_checks(products):
    max_parallel = max_parallel_products()
    timeout_seconds = product_timeout_seconds()
    if max_parallel <= 1:
        for product in products:
            print(f"Checking: {product.name}")
            try:
                reviews, summary = check_product_with_timeout(product)
                yield product, reviews, summary, None
            except Exception as exc:
                yield product, [], None, exc
        return

    context = multiprocessing.get_context()
    pending = []
    remaining = iter(products)

    def start_next_product():
        try:
            product = next(remaining)
        except StopIteration:
            return False
        print(f"Checking: {product.name}")
        result_queue = context.Queue()
        process = context.Process(target=product_check_worker, args=(product, result_queue))
        process.start()
        pending.append({
            "product": product,
            "process": process,
            "queue": result_queue,
            "deadline": time.monotonic() + timeout_seconds if timeout_seconds > 0 else None,
        })
        return True

    while len(pending) < max_parallel and start_next_product():
        pass

    while pending:
        emitted_result = False
        for job in list(pending):
            product = job["product"]
            process = job["process"]
            deadline = job["deadline"]
            if deadline is not None and time.monotonic() > deadline and process.is_alive():
                stop_product_process(process)
                pending.remove(job)
                emitted_result = True
                yield product, [], None, TimeoutError(f"Product check exceeded {timeout_seconds} seconds.")
            elif not process.is_alive():
                process.join()
                pending.remove(job)
                emitted_result = True
                if job["queue"].empty():
                    yield product, [], None, RuntimeError(f"Product check stopped without returning data. Exit code: {process.exitcode}.")
                else:
                    status, *payload = job["queue"].get()
                    if status == "ok":
                        yield product, payload[0], payload[1], None
                    else:
                        yield product, [], None, RuntimeError(payload[0] if payload else "Product check failed.")
            while len(pending) < max_parallel and start_next_product():
                pass
        if not emitted_result:
            time.sleep(0.25)


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
    print(
        f"Voxy will check {len(products)} product(s) in automatic waves "
        f"of up to {max_parallel_products()} product(s), "
        f"with {product_timeout_seconds()} seconds per product and "
        f"{product_retry_attempts()} retry attempt(s)."
    )
    for product, reviews, summary, error in iter_product_checks(products):
        if error:
            retry_error = error
            for attempt in range(1, product_retry_attempts() + 1):
                print(f"Retrying {product.name} after error: {retry_error} (attempt {attempt})")
                try:
                    reviews, summary = check_product_with_timeout(product)
                    retry_error = None
                    break
                except Exception as exc:
                    retry_error = exc
            if retry_error:
                print(f"Error while checking {product.name}: {retry_error}")
                error_summary = base.summarize_error(product, retry_error)
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

    if not args.dry_run:
        send_technical_issue_alert(summaries, args.timezone)

    base.save_seen(Path(args.state), new_seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
