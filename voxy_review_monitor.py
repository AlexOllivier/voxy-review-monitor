import argparse
import hashlib
import json
import os
import re
import smtplib
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

try:
    from bs4 import BeautifulSoup
    from dotenv import load_dotenv
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from playwright.sync_api import sync_playwright
    import gspread
    import requests
except ImportError as exc:
    print(f"Module manquant: {exc.name}")
    print("Installe les dependances avec: pip install -r requirements.txt")
    raise


@dataclass
class Product:
    name: str
    url: str
    emails: list[str]
    threshold: float
    platform: str
    language: str


@dataclass
class Review:
    rating: float
    text: str
    author: str = ""
    date: str = ""
    source: str = ""

    @property
    def fingerprint(self) -> str:
        clean_text = normalize_text(self.text)[:700]
        raw = f"{self.rating}|{self.author}|{self.date}|{clean_text}"
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


THEME_KEYWORDS = {
    "Guide / staff": ["guide", "staff", "host", "driver", "employee", "rude", "friendly", "professional", "helpful"],
    "Organization": ["organization", "organised", "organized", "waiting", "wait", "late", "delay", "queue", "meeting point"],
    "Value for money": ["price", "expensive", "value", "money", "worth", "overpriced", "cost"],
    "Communication": ["communication", "instructions", "information", "email", "message", "unclear", "confusing"],
    "Booking / access": ["ticket", "booking", "reservation", "entry", "access", "cancel", "refund", "voucher"],
    "Experience quality": ["experience", "tour", "activity", "boring", "amazing", "interesting", "disappointing"],
}


IMPROVEMENT_LIBRARY = {
    "Guide / staff": "Review guide/staff briefing, tone, and service consistency.",
    "Organization": "Reduce waiting time, clarify the meeting process, and check day-of operations.",
    "Value for money": "Check perceived value: inclusions, price positioning, and expectation setting.",
    "Communication": "Improve pre-arrival instructions, confirmation messages, and on-page clarity.",
    "Booking / access": "Audit booking, ticketing, refund, and entry instructions end to end.",
    "Experience quality": "Compare the advertised promise with the actual customer experience.",
}


def split_emails(value: str) -> list[str]:
    return [email.strip() for email in re.split(r"[;,]", value or "") if email.strip()]


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def html_to_text(value: str) -> str:
    return normalize_text(BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True))


def parse_review_date(value: str) -> date | None:
    text = normalize_text(value)
    if not text:
        return None

    iso_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if iso_match:
        try:
            return datetime.strptime(iso_match.group(0), "%Y-%m-%d").date()
        except ValueError:
            pass

    common_formats = [
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%d %B %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%b %d, %Y",
    ]
    for fmt in common_formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def is_past_dated_review(review: Review, today: date) -> bool:
    parsed = parse_review_date(review.date)
    return parsed is not None and parsed < today


def load_products(xlsx_path: Path) -> list[Product]:
    workbook = load_workbook(xlsx_path, data_only=True)
    sheet = workbook["Produits"]
    headers = {
        str(cell.value).strip().lower(): index
        for index, cell in enumerate(sheet[1], start=1)
        if cell.value
    }

    active_header = "active" if "active" in headers else "actif"
    name_header = "product name" if "product name" in headers else "nom produit"
    url_header = "url to monitor" if "url to monitor" in headers else ("url a surveiller" if "url a surveiller" in headers else "url getyourguide")
    emails_header = "alert emails" if "alert emails" in headers else "emails alerte"
    threshold_header = "star threshold" if "star threshold" in headers else "seuil etoiles"
    platform_header = "platform" if "platform" in headers else "plateforme"
    language_header = "language" if "language" in headers else "langue"

    required = [active_header, name_header, url_header, emails_header]
    missing = [name for name in required if name not in headers]
    if missing:
        raise ValueError(f"Missing columns in the Produits sheet: {', '.join(missing)}")

    products: list[Product] = []
    for row in range(2, sheet.max_row + 1):
        active = str(sheet.cell(row, headers[active_header]).value or "").strip().lower()
        name = str(sheet.cell(row, headers[name_header]).value or "").strip()
        url = str(sheet.cell(row, headers[url_header]).value or "").strip()
        emails = split_emails(str(sheet.cell(row, headers[emails_header]).value or ""))
        threshold_cell = headers.get(threshold_header)
        platform_cell = headers.get(platform_header)
        language_cell = headers.get(language_header)
        threshold = sheet.cell(row, threshold_cell).value if threshold_cell else 4
        platform = str(sheet.cell(row, platform_cell).value or "auto").strip().lower() if platform_cell else "auto"
        language = str(sheet.cell(row, language_cell).value or "en").strip() if language_cell else "en"

        if active not in {"oui", "yes", "true", "1", "x"}:
            continue
        if not name and not url:
            continue
        if not name or not url or not emails:
            print(f"Row {row} skipped: product name, URL, or alert emails are missing.")
            continue

        products.append(Product(name=name, url=url, emails=emails, threshold=float(threshold or 4), platform=platform, language=language))
    return products


def google_sheet_export_url(sheet_url: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url)
    if not match:
        raise ValueError("Invalid Google Sheets URL. Expected a URL containing /spreadsheets/d/{sheet_id}.")
    sheet_id = match.group(1)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"


def download_google_sheet(sheet_url: str, destination: Path) -> Path:
    export_url = google_sheet_export_url(sheet_url)
    response = requests.get(export_url, timeout=60)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type.lower():
        raise RuntimeError(
            "Google returned an HTML page instead of an Excel file. "
            "Make sure the Google Sheet is shared so this PC can access it, or publish/export it with link access."
        )
    destination.write_bytes(response.content)
    return destination


def detect_platform(product: Product) -> str:
    if product.platform and product.platform != "auto":
        return product.platform
    url = product.url.lower()
    known = {
        "getyourguide": "getyourguide",
        "tripadvisor": "tripadvisor",
        "trustpilot": "trustpilot",
        "booking.com": "booking",
        "viator": "viator",
        "google": "google",
        "airbnb": "airbnb",
    }
    for needle, name in known.items():
        if needle in url:
            return name
    return "other"


def flatten_jsonld(item) -> Iterable[dict]:
    if isinstance(item, list):
        for child in item:
            yield from flatten_jsonld(child)
    elif isinstance(item, dict):
        yield item
        for value in item.values():
            if isinstance(value, (dict, list)):
                yield from flatten_jsonld(value)


def rating_from_value(value) -> float | None:
    if value is None:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def first_text_value(obj: dict, keys: list[str]) -> str:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and normalize_text(value):
            return html_to_text(value)
        if isinstance(value, dict):
            nested = first_text_value(value, keys)
            if nested:
                return nested
    return ""


def author_from_value(value) -> str:
    if isinstance(value, dict):
        return normalize_text(value.get("name") or value.get("displayName") or value.get("username") or "")
    if isinstance(value, list):
        return ", ".join(filter(None, [author_from_value(item) for item in value]))
    return normalize_text(value or "")


def rating_from_object(obj: dict) -> float | None:
    rating_keys = ["ratingValue", "rating", "stars", "score", "value"]
    for key in rating_keys:
        if key not in obj:
            continue
        value = obj.get(key)
        if isinstance(value, dict):
            rating = rating_from_object(value)
        else:
            rating = rating_from_value(value)
        if rating is not None and 0 < rating <= 5:
            return rating

    for key in ["reviewRating", "starRating", "review_score", "reviewScore"]:
        value = obj.get(key)
        if isinstance(value, dict):
            rating = rating_from_object(value)
            if rating is not None and 0 < rating <= 5:
                return rating
        else:
            rating = rating_from_value(value)
            if rating is not None and 0 < rating <= 5:
                return rating
    return None


def collect_reviews_from_json(data, source: str) -> list[Review]:
    reviews: list[Review] = []
    stack = [data]
    text_keys = ["reviewBody", "reviewText", "text", "body", "content", "comment", "description", "message"]
    date_keys = ["datePublished", "publishedDate", "createdAt", "created_at", "date", "reviewDate"]

    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
            continue
        if not isinstance(item, dict):
            continue

        stack.extend(value for value in item.values() if isinstance(value, (dict, list)))

        item_type = item.get("@type") or item.get("type") or item.get("__typename") or ""
        looks_like_review = bool(re.search(r"review|comment|rating", str(item_type), re.I))
        has_review_words = any(key in item for key in text_keys + ["reviewRating", "ratingValue", "stars", "score"])
        if not (looks_like_review or has_review_words):
            continue

        rating = rating_from_object(item)
        if rating is None:
            continue

        text = first_text_value(item, text_keys)
        author = author_from_value(item.get("author") or item.get("user") or item.get("customer") or item.get("reviewer"))
        date = first_text_value(item, date_keys)

        # Avoid aggregate-only ratings when there is no individual review signal.
        if not text and not author and not date and re.search(r"aggregate|summary|product", str(item_type), re.I):
            continue

        reviews.append(Review(rating=rating, text=text, author=author, date=date, source=source))
    return reviews


def extract_jsonld_reviews(html: str) -> list[Review]:
    soup = BeautifulSoup(html, "html.parser")
    reviews: list[Review] = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        reviews.extend(collect_reviews_from_json(data, "json-ld"))
    return reviews


def extract_script_json_reviews(html: str) -> list[Review]:
    soup = BeautifulSoup(html, "html.parser")
    reviews: list[Review] = []
    script_ids = {"__NEXT_DATA__", "__NUXT_DATA__", "ng-state", "apollo-state"}

    for script in soup.find_all("script"):
        raw = script.string or script.get_text("", strip=False)
        if not raw or len(raw) < 20:
            continue

        script_type = (script.get("type") or "").lower()
        script_id = script.get("id") or ""
        if script_id in script_ids or "json" in script_type:
            try:
                reviews.extend(collect_reviews_from_json(json.loads(raw), f"script-json:{script_id or script_type}"))
                continue
            except json.JSONDecodeError:
                pass

        if not re.search(r"review|rating|stars|score", raw, re.I):
            continue
        for match in re.finditer(r"(\{[^{}]{0,5000}(?:ratingValue|reviewRating|stars|score|reviewText|reviewBody)[^{}]{0,5000}\})", raw, re.I):
            try:
                reviews.extend(collect_reviews_from_json(json.loads(match.group(1)), "embedded-json-fragment"))
            except json.JSONDecodeError:
                continue
    return reviews


def extract_visible_reviews(html: str) -> list[Review]:
    soup = BeautifulSoup(html, "html.parser")
    reviews: list[Review] = []
    rating_pattern = re.compile(r"([1-5](?:[.,]\d+)?)\s*(/|out of|sur|stars?|etoiles?|étoiles?)", re.I)
    candidates = soup.find_all(attrs={"aria-label": rating_pattern})
    candidates += soup.find_all(attrs={"title": rating_pattern})

    for node in candidates:
        label = node.get("aria-label") or node.get("title") or ""
        rating = rating_from_value(label)
        if rating is None or rating > 5:
            continue
        container = node
        for _ in range(5):
            if container.parent:
                container = container.parent
        text = normalize_text(container.get_text(" ", strip=True))
        reviews.append(Review(rating=rating, text=text[:1600], source="visible-rating-label"))

    reviewish = soup.find_all(attrs={
        "class": re.compile(r"review|avis|comment|testimonial", re.I)
    })
    reviewish += soup.find_all(attrs={
        "data-testid": re.compile(r"review|avis|comment|testimonial", re.I)
    })
    for node in reviewish:
        text = normalize_text(node.get_text(" ", strip=True))
        if len(text) < 20:
            continue
        rating = None
        for attr in ["aria-label", "title", "data-rating", "data-score"]:
            rating = rating_from_value(node.get(attr))
            if rating is not None:
                break
        if rating is None:
            rating = rating_from_value(text[:250])
        if rating is None or rating > 5:
            continue
        reviews.append(Review(rating=rating, text=text[:1600], source="visible-review-block"))
    return reviews


def extract_embedded_reviews(html: str) -> list[Review]:
    reviews: list[Review] = []
    patterns = [
        r'"ratingValue"\s*:\s*"?(\d+(?:[.,]\d+)?)"?',
        r'"rating"\s*:\s*"?(\d+(?:[.,]\d+)?)"?',
        r'"stars"\s*:\s*"?(\d+(?:[.,]\d+)?)"?',
        r'"score"\s*:\s*"?(\d+(?:[.,]\d+)?)"?',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.I):
            rating = rating_from_value(match.group(1))
            if rating is None or rating > 5:
                continue
            start = max(0, match.start() - 500)
            end = min(len(html), match.end() + 900)
            snippet = html_to_text(html[start:end])
            reviews.append(Review(rating=rating, text=snippet[:1600], source="embedded-rating-pattern"))
    return reviews


def dedupe_reviews(reviews: Iterable[Review]) -> list[Review]:
    seen: set[str] = set()
    unique: list[Review] = []
    for review in reviews:
        if review.rating <= 0 or review.rating > 5:
            continue
        review.text = normalize_text(review.text)
        review.author = normalize_text(review.author)
        review.date = normalize_text(review.date)
        key = review.fingerprint
        if key not in seen:
            seen.add(key)
            unique.append(review)
    return unique


def fetch_reviews(product: Product) -> list[Review]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            locale=product.language or "en",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
        )
        page.set_default_timeout(5000)
        page.goto(product.url, wait_until="domcontentloaded", timeout=90000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        click_labels = [
            "reviews", "avis", "show more", "voir plus", "load more",
            "read more", "more reviews", "all reviews", "see reviews",
            "tous les avis", "afficher plus", "accept", "accepter",
            "j'accepte", "allow all", "tout accepter"
        ]
        for label in click_labels:
            for _ in range(2):
                try:
                    page.get_by_text(re.compile(label, re.I)).first.click(timeout=1500)
                    page.wait_for_timeout(1200)
                except Exception:
                    break

        for _ in range(8):
            page.mouse.wheel(0, 3500)
            page.wait_for_timeout(900)

        html = page.content()
        browser.close()

    return dedupe_reviews([
        *extract_jsonld_reviews(html),
        *extract_script_json_reviews(html),
        *extract_visible_reviews(html),
        *extract_embedded_reviews(html),
    ])


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def send_email(recipients: list[str], subject: str, body: str) -> None:
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

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(message)


def translate_to_english(text: str) -> str:
    if not text or os.environ.get("TRANSLATE_REVIEWS_TO_ENGLISH", "false").lower() not in {"true", "1", "yes", "oui"}:
        return ""

    api_key = os.environ.get("DEEPL_API_KEY", "").strip()
    if not api_key:
        return ""

    endpoint = "https://api-free.deepl.com/v2/translate"
    if api_key.endswith(":fx") is False:
        endpoint = "https://api.deepl.com/v2/translate"

    try:
        response = requests.post(
            endpoint,
            data={"auth_key": api_key, "text": text[:4500], "target_lang": "EN"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return normalize_text(payload["translations"][0]["text"])
    except Exception as exc:
        return f"translation unavailable ({exc})"


def build_alert_body(product: Product, reviews: list[Review]) -> str:
    lines = [
        f"Voxy review alert for: {product.name}",
        f"Link: {product.url}",
        f"Threshold: {product.threshold} stars or less",
        f"Check date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Detected reviews:",
    ]
    for index, review in enumerate(reviews, start=1):
        translation = translate_to_english(review.text)
        lines.extend([
            "",
            f"{index}. Rating: {review.rating}/5",
            f"Author: {review.author or 'not detected'}",
            f"Date: {review.date or 'not detected'}",
            f"Review content: {review.text or 'not available on the page'}",
            f"English translation: {translation or 'not enabled or not available'}",
            f"Detection source: {review.source or 'not specified'}",
        ])
    return "\n".join(lines)


def summarize_reviews(product: Product, reviews: list[Review]) -> dict:
    review_count = len(reviews)
    global_score = round(sum(review.rating for review in reviews) / review_count, 2) if review_count else None
    low_reviews = [review for review in reviews if review.rating <= product.threshold]
    critical_reviews = [review for review in reviews if review.rating < 3]
    all_text = " ".join(review.text.lower() for review in reviews if review.text)

    theme_counts = {}
    for theme, keywords in THEME_KEYWORDS.items():
        theme_counts[theme] = sum(all_text.count(keyword) for keyword in keywords)
    ranked_themes = [theme for theme, count in sorted(theme_counts.items(), key=lambda item: item[1], reverse=True) if count > 0]
    if not ranked_themes and review_count:
        ranked_themes = ["General satisfaction"]

    suggestions = [IMPROVEMENT_LIBRARY[theme] for theme in ranked_themes if theme in IMPROVEMENT_LIBRARY][:5]
    if not suggestions and review_count:
        suggestions = ["Read the latest low-rated reviews manually and identify recurring friction points."]

    critical_points = []
    for review in sorted(low_reviews, key=lambda item: item.rating)[:8]:
        text = review.text or "Review text not available."
        critical_points.append(f"{review.rating}/5 - {text[:260]}")

    platform = detect_platform(product)
    return {
        "product": product.name,
        "url": product.url,
        "platform": platform,
        "review_count": review_count,
        "global_score": global_score,
        "low_review_count": len(low_reviews),
        "critical_review_count": len(critical_reviews),
        "alert": bool(global_score is not None and global_score < 3),
        "themes": ranked_themes[:6],
        "suggestions": suggestions,
        "critical_points": critical_points,
        "reviews": reviews,
    }


def clean_sheet_name(value: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]\*:/\\?]", " ", value or "Product").strip()[:28] or "Product"
    name = base
    index = 2
    while name in used:
        suffix = f" {index}"
        name = f"{base[:31 - len(suffix)]}{suffix}"
        index += 1
    used.add(name)
    return name


def style_header(row) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(color="FFFFFF", bold=True)
    for cell in row:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def autofit_columns(sheet, min_width: int = 12, max_width: int = 70) -> None:
    for column_cells in sheet.columns:
        width = min(max_width, max(min_width, max(len(str(cell.value or "")) for cell in column_cells) + 2))
        sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = width


def build_dashboard_report(summaries: list[dict], report_path: Path) -> None:
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Dashboard"
    summary_sheet.append([
        "Product",
        "Platform",
        "Reviews detected",
        "Global score",
        "Low reviews",
        "Critical reviews",
        "Alert",
        "Top themes",
        "Improvement suggestions",
        "URL",
    ])
    style_header(summary_sheet[1])

    for item in summaries:
        summary_sheet.append([
            item["product"],
            item["platform"],
            item["review_count"],
            item["global_score"] if item["global_score"] is not None else "N/A",
            item["low_review_count"],
            item["critical_review_count"],
            "ALERT: score below 3" if item["alert"] else "OK",
            "\n".join(item["themes"]),
            "\n".join(item["suggestions"]),
            item["url"],
        ])
        if item["alert"]:
            for cell in summary_sheet[summary_sheet.max_row]:
                cell.fill = PatternFill("solid", fgColor="F8CBAD")

    used_names = {"Dashboard"}
    for item in summaries:
        sheet = workbook.create_sheet(clean_sheet_name(item["product"], used_names))
        sheet.append(["Metric", "Value"])
        style_header(sheet[1])
        metrics = [
            ("Product", item["product"]),
            ("Platform", item["platform"]),
            ("URL", item["url"]),
            ("Reviews detected", item["review_count"]),
            ("Global score", item["global_score"] if item["global_score"] is not None else "N/A"),
            ("Alert", "ALERT: score below 3" if item["alert"] else "OK"),
            ("Top themes", "\n".join(item["themes"]) or "No theme detected"),
            ("Improvement suggestions", "\n".join(item["suggestions"]) or "No suggestion available"),
            ("Critical points", "\n".join(item["critical_points"]) or "No critical point detected"),
        ]
        for metric, value in metrics:
            sheet.append([metric, value])

        start_row = sheet.max_row + 2
        sheet.append([])
        sheet.append(["Rating", "Author", "Date", "Source", "Review content"])
        style_header(sheet[sheet.max_row])
        for review in item["reviews"]:
            sheet.append([
                review.rating,
                review.author or "Not detected",
                review.date or "Not detected",
                review.source or "Not specified",
                review.text or "Not available on the page",
            ])
        for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.freeze_panes = f"A{start_row + 1}"
        autofit_columns(sheet)

    for row in summary_sheet.iter_rows(min_row=1, max_row=summary_sheet.max_row):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    summary_sheet.freeze_panes = "A2"
    autofit_columns(summary_sheet)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(report_path)


def google_rows_for_dashboard(summaries: list[dict]) -> list[list]:
    rows = [[
        "Product",
        "Platform",
        "Reviews detected",
        "Global score",
        "Low reviews",
        "Critical reviews",
        "Alert",
        "Top themes",
        "Improvement suggestions",
        "URL",
    ]]
    for item in summaries:
        rows.append([
            item["product"],
            item["platform"],
            item["review_count"],
            item["global_score"] if item["global_score"] is not None else "N/A",
            item["low_review_count"],
            item["critical_review_count"],
            "ALERT: score below 3" if item["alert"] else "OK",
            "\n".join(item["themes"]),
            "\n".join(item["suggestions"]),
            item["url"],
        ])
    return rows


def google_rows_for_product(summary: dict) -> list[list]:
    rows = [
        ["Metric", "Value"],
        ["Product", summary["product"]],
        ["Platform", summary["platform"]],
        ["URL", summary["url"]],
        ["Reviews detected", summary["review_count"]],
        ["Global score", summary["global_score"] if summary["global_score"] is not None else "N/A"],
        ["Alert", "ALERT: score below 3" if summary["alert"] else "OK"],
        ["Top themes", "\n".join(summary["themes"]) or "No theme detected"],
        ["Improvement suggestions", "\n".join(summary["suggestions"]) or "No suggestion available"],
        ["Critical points", "\n".join(summary["critical_points"]) or "No critical point detected"],
        [],
        ["Rating", "Author", "Date", "Source", "Review content"],
    ]
    for review in summary["reviews"]:
        rows.append([
            review.rating,
            review.author or "Not detected",
            review.date or "Not detected",
            review.source or "Not specified",
            review.text or "Not available on the page",
        ])
    return rows


def get_or_create_worksheet(spreadsheet, title: str, rows: int = 100, cols: int = 12):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def update_google_sheet_dashboard(sheet_url: str, summaries: list[dict]) -> None:
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not service_account_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is required to update the shared Google Sheet dashboard.")

    credentials = json.loads(service_account_json)
    client = gspread.service_account_from_dict(credentials)
    spreadsheet = client.open_by_url(sheet_url)

    dashboard = get_or_create_worksheet(spreadsheet, "Dashboard", rows=max(100, len(summaries) + 10), cols=12)
    dashboard.clear()
    dashboard.update(google_rows_for_dashboard(summaries), value_input_option="USER_ENTERED")
    dashboard.freeze(rows=1)

    used_names = {"Produits", "Dashboard"}
    for summary in summaries:
        title = clean_sheet_name(summary["product"], used_names)
        worksheet = get_or_create_worksheet(spreadsheet, title, rows=max(100, len(summary["reviews"]) + 20), cols=8)
        worksheet.clear()
        worksheet.update(google_rows_for_product(summary), value_input_option="USER_ENTERED")
        worksheet.freeze(rows=1)

    print("Shared Google Sheet dashboard updated.")


def build_score_alert_body(summary: dict) -> str:
    return "\n".join([
        f"Voxy score alert for: {summary['product']}",
        f"Link: {summary['url']}",
        f"Platform: {summary['platform']}",
        f"Global score: {summary['global_score']}/5",
        f"Reviews detected: {summary['review_count']}",
        "",
        "Top themes:",
        "\n".join(f"- {theme}" for theme in summary["themes"]) or "- No theme detected",
        "",
        "Improvement suggestions:",
        "\n".join(f"- {suggestion}" for suggestion in summary["suggestions"]) or "- No suggestion available",
        "",
        "Critical points:",
        "\n".join(f"- {point}" for point in summary["critical_points"]) or "- No critical point detected",
    ])


def seen_key(product: Product, review: Review) -> str:
    product_key = hashlib.sha256(product.url.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{product_key}:{review.fingerprint}"


def score_alert_key(product: Product, summary: dict) -> str:
    product_key = hashlib.sha256(product.url.encode("utf-8", errors="ignore")).hexdigest()[:16]
    score = summary["global_score"] if summary["global_score"] is not None else "na"
    return f"{product_key}:score-alert:{score}:{summary['review_count']}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor reviews across multiple platforms and send email alerts.")
    parser.add_argument("--xlsx", default="template_surveillance_avis_multi_plateformes.xlsx", help="Chemin du fichier Excel.")
    parser.add_argument("--sheet-url", default="", help="Google Sheets URL to download before running.")
    parser.add_argument("--state", default="avis_deja_signales.json", help="Memory file for reviews already reported.")
    parser.add_argument("--report", default="voxy_dashboard_report.xlsx", help="Dashboard report Excel file to generate.")
    parser.add_argument("--timezone", default="Europe/Paris", help="Timezone used to decide whether a review date is from today.")
    parser.add_argument("--only-at-hour", type=int, default=None, help="Exit unless the current hour in --timezone matches this value.")
    parser.add_argument("--update-google-sheet-dashboard", action="store_true", help="Write Dashboard and product tabs back into the shared Google Sheet.")
    parser.add_argument("--include-past-dated-reviews", action="store_true", help="Include reviews whose detected date is before today.")
    parser.add_argument("--dry-run", action="store_true", help="Test without sending email.")
    parser.add_argument("--baseline", action="store_true", help="Save current reviews without sending alerts.")
    args = parser.parse_args()

    load_dotenv()
    sheet_url = args.sheet_url or os.environ.get("GOOGLE_SHEET_URL", "")
    input_xlsx = Path(args.xlsx)
    if sheet_url:
        input_xlsx = download_google_sheet(sheet_url, Path("voxy_current_shared_sheet.xlsx"))
        print(f"Downloaded shared Google Sheet to: {input_xlsx}")

    products = load_products(input_xlsx)
    seen = load_seen(Path(args.state))
    new_seen = set(seen)
    subject_prefix = os.environ.get("ALERT_SUBJECT_PREFIX", "[Alerte avis]")
    local_now = datetime.now(ZoneInfo(args.timezone))
    local_today = local_now.date()

    if args.only_at_hour is not None and local_now.hour != args.only_at_hour:
        print(f"Not the scheduled local hour yet: {local_now.strftime('%Y-%m-%d %H:%M')} ({args.timezone}).")
        return 0

    if not products:
        print("No active products found in the Excel file.")
        return 0

    summaries: list[dict] = []
    for product in products:
        print(f"Checking: {product.name}")
        try:
            reviews = fetch_reviews(product)
        except Exception as exc:
            print(f"Error while checking {product.name}: {exc}")
            continue

        for review in reviews:
            new_seen.add(seen_key(product, review))

        if args.include_past_dated_reviews:
            current_reviews = reviews
        else:
            current_reviews = [review for review in reviews if not is_past_dated_review(review, local_today)]
            skipped = len(reviews) - len(current_reviews)
            if skipped:
                print(f"Skipped {skipped} review(s) dated before {local_today.isoformat()} ({args.timezone}).")

        summary = summarize_reviews(product, current_reviews)
        summaries.append(summary)

        low_reviews = [review for review in current_reviews if review.rating <= product.threshold and seen_key(product, review) not in seen]

        if args.baseline:
            print(f"Baseline: {len(reviews)} reviews saved, no email sent.")
            continue

        if summary["alert"] and score_alert_key(product, summary) not in seen:
            score_body = build_score_alert_body(summary)
            score_subject = f"{subject_prefix} {product.name}: global score below 3"
            if args.dry_run:
                print("DRY RUN - score alert email not sent")
                print(score_body)
            else:
                send_email(product.emails, score_subject, score_body)
                print(f"Score alert sent to: {', '.join(product.emails)}")
            new_seen.add(score_alert_key(product, summary))

        if not low_reviews:
            print(f"OK: no new reviews rated {product.threshold} stars or less.")
            continue

        body = build_alert_body(product, low_reviews)
        subject = f"{subject_prefix} {product.name}: {len(low_reviews)} avis <= {product.threshold}"
        if args.dry_run:
            print("DRY RUN - email not sent")
            print(body)
        else:
            send_email(product.emails, subject, body)
            print(f"Alert sent to: {', '.join(product.emails)}")

    if summaries:
        build_dashboard_report(summaries, Path(args.report))
        print(f"Dashboard report saved: {args.report}")
        if args.update_google_sheet_dashboard:
            if not sheet_url:
                raise RuntimeError("--update-google-sheet-dashboard requires GOOGLE_SHEET_URL or --sheet-url.")
            update_google_sheet_dashboard(sheet_url, summaries)

    save_seen(Path(args.state), new_seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
