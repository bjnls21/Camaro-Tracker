"""
1969 Camaro Listing Scraper — Fixed Version
- Strict 1969-only year filtering on ALL sources
- Improved eBay fetching (HTML + RSS fallback)
- Better Hemmings and ClassicCars selectors
- BringATrailer year-locked URL
"""

import os
import json
import hashlib
import smtplib
import logging
import time
import random
import re
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO   = os.environ.get("EMAIL_TO", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

OUTPUT_FILE = Path("docs/listings.json")
SEEN_FILE   = Path("docs/seen_ids.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def uid(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]

def get(url):
    for attempt in range(3):
        try:
            time.sleep(random.uniform(2, 4))
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return r
        except Exception as e:
            log.warning(f"Attempt {attempt+1} failed: {e}")
            time.sleep(6)
    return None

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def is_1969_camaro(text):
    """
    Strict check — title must contain 1969 AND camaro.
    Rejects 1968, 1970, and any other year.
    """
    t = text.lower()
    has_camaro = "camaro" in t
    has_1969   = "1969" in t or bool(re.search(r"\b69\b", t))
    # If another year like 1968 or 1970 appears but NOT 1969, reject it
    other_years = re.findall(r'\b(196[0-8]|197[0-9]|196[0-8])\b', text)
    if other_years and "1969" not in text:
        return False
    return has_camaro and has_1969

def extract_price(text):
    m = re.search(r'US \$([\d,]+)', text) or re.search(r'\$([\d,]+)', text)
    if m:
        raw = f"${m.group(1)}"
        num = int(m.group(1).replace(",", ""))
        return raw, num
    return "", 0


# ── SCRAPERS ──────────────────────────────────────────────────────────────────

def scrape_ebay():
    listings = []
    log.info("Scraping eBay Motors...")

    # HTML scrape (more reliable, shows photos)
    url = "https://www.ebay.com/sch/i.html?_nkw=1969+chevrolet+camaro&_sacat=6001&_sop=10&_ipg=50"
    r = get(url)
    if r:
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select("li.s-item"):
            title_el = item.select_one(".s-item__title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if title == "Shop on eBay":
                continue
            if not is_1969_camaro(title):
                continue

            link_el = item.select_one("a.s-item__link")
            href = link_el["href"].split("?")[0] if link_el else ""

            price_el = item.select_one(".s-item__price")
            price_text = price_el.get_text(strip=True) if price_el else ""
            price_raw, price_num = extract_price(price_text)
            if not price_raw:
                price_raw = price_text

            img_el = item.select_one("img.s-item__image-img")
            image = ""
            if img_el:
                image = img_el.get("src","") or img_el.get("data-src","")
            if image and "gif" in image.lower():
                image = ""

            loc_el = item.select_one(".s-item__location")
            location = loc_el.get_text(strip=True).replace("from ","") if loc_el else "United States"

            is_auction = bool(item.select_one(".s-item__time-left"))

            listings.append({
                "id":        uid(href or title),
                "source":    "eBay Motors",
                "title":     title,
                "price":     price_raw,
                "price_num": price_num,
                "url":       href,
                "image":     image,
                "location":  location,
                "is_auction": is_auction,
                "listed_at": now_iso(),
                "fetched_at": now_iso(),
                "is_new":    True,
            })

    # RSS fallback if HTML returned nothing
    if not listings:
        log.info("  eBay HTML got 0, trying RSS...")
        rss_url = "https://www.ebay.com/sch/i.html?_nkw=1969+camaro&_sacat=6001&_sop=10&_ipg=50&_rss=1"
        r = get(rss_url)
        if r:
            soup = BeautifulSoup(r.content, "xml")
            for item in soup.find_all("item"):
                title_el = item.find("title")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not is_1969_camaro(title):
                    continue
                guid = item.find("guid")
                href = guid.get_text(strip=True) if guid else ""
                desc_el = item.find("description")
                desc = desc_el.get_text(strip=True) if desc_el else ""
                price_raw, price_num = extract_price(desc)
                img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc, re.I)
                image = img_m.group(1) if img_m else ""
                listings.append({
                    "id":        uid(href or title),
                    "source":    "eBay Motors",
                    "title":     title,
                    "price":     price_raw,
                    "price_num": price_num,
                    "url":       href,
                    "image":     image,
                    "location":  "United States",
                    "is_auction": False,
                    "listed_at": now_iso(),
                    "fetched_at": now_iso(),
                    "is_new":    True,
                })

    log.info(f"  eBay: {len(listings)} listings")
    return listings


def scrape_hemmings():
    listings = []
    log.info("Scraping Hemmings...")
    url = "https://www.hemmings.com/classifieds/cars-for-sale/chevrolet/camaro?year1=1969&year2=1969"
    r = get(url)
    if not r:
        return listings
    soup = BeautifulSoup(r.text, "html.parser")

    seen = set()
    cards = (
        soup.select("a[href*='/classifieds/listing/']") or
        soup.select("div[class*='listing'] a") or
        soup.select("article a")
    )

    for card in cards:
        href = card.get("href","")
        if not href or href in seen or "/listing/" not in href:
            continue
        seen.add(href)
        full_url = "https://www.hemmings.com" + href if href.startswith("/") else href

        # Walk up to find the card container
        container = card
        for _ in range(4):
            if container.parent:
                container = container.parent

        full_text = container.get_text(" ", strip=True)
        if not is_1969_camaro(full_text + " " + full_url):
            continue

        title_el = container.select_one("h2,h3,h4,[class*='title'],[class*='heading']")
        title = title_el.get_text(strip=True) if title_el else full_text[:80]

        price_el = container.select_one("[class*='price'],[class*='Price']")
        price_text = price_el.get_text(strip=True) if price_el else "See listing"
        price_raw, price_num = extract_price(price_text)
        if not price_raw:
            price_raw = price_text

        img_el = container.select_one("img")
        image = ""
        if img_el:
            image = img_el.get("src","") or img_el.get("data-src","") or img_el.get("data-lazy","")

        loc_el = container.select_one("[class*='location'],[class*='city'],[class*='state']")
        location = loc_el.get_text(strip=True) if loc_el else "United States"

        listings.append({
            "id":        uid(full_url),
            "source":    "Hemmings",
            "title":     title or "1969 Chevrolet Camaro",
            "price":     price_raw,
            "price_num": price_num,
            "url":       full_url,
            "image":     image,
            "location":  location,
            "is_auction": False,
            "listed_at": now_iso(),
            "fetched_at": now_iso(),
            "is_new":    True,
        })

    log.info(f"  Hemmings: {len(listings)} listings")
    return listings


def scrape_classiccars():
    listings = []
    log.info("Scraping ClassicCars.com...")
    url = "https://classiccars.com/listings/find/1969/chevrolet/camaro"
    r = get(url)
    if not r:
        return listings
    soup = BeautifulSoup(r.text, "html.parser")

    seen = set()
    for a in soup.select("a[href*='/listings/']"):
        href = a.get("href","")
        if not href or href in seen or len(href) < 20 or "find" in href:
            continue
        seen.add(href)
        full_url = "https://classiccars.com" + href if href.startswith("/") else href

        container = a
        for _ in range(5):
            if container.parent:
                container = container.parent

        full_text = container.get_text(" ", strip=True)
        if not is_1969_camaro(full_text + " " + full_url):
            continue

        title_el = container.select_one("h2,h3,[class*='title'],[class*='heading']")
        title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)[:80]
        if not title or len(title) < 5:
            title = "1969 Chevrolet Camaro"

        price_el = container.select_one("[class*='price'],[class*='Price']")
        price_text = price_el.get_text(strip=True) if price_el else "See listing"
        price_raw, price_num = extract_price(price_text)
        if not price_raw:
            price_raw = price_text

        img_el = container.select_one("img")
        image = ""
        if img_el:
            image = img_el.get("src","") or img_el.get("data-src","")

        listings.append({
            "id":        uid(full_url),
            "source":    "ClassicCars.com",
            "title":     title,
            "price":     price_raw,
            "price_num": price_num,
            "url":       full_url,
            "image":     image,
            "location":  "United States",
            "is_auction": False,
            "listed_at": now_iso(),
            "fetched_at": now_iso(),
            "is_new":    True,
        })

    log.info(f"  ClassicCars.com: {len(listings)} listings")
    return listings


def scrape_bat():
    listings = []
    log.info("Scraping BringATrailer...")
    urls = [
        "https://bringatrailer.com/chevrolet/camaro/?s=1969",
        "https://bringatrailer.com/search/?s=1969+camaro",
    ]
    for url in urls:
        r = get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for card in soup.select("article.post, li.auctions-item, div[class*='listing']"):
            a = card.select_one("a[href*='bringatrailer.com']") or card.select_one("a[href]")
            if not a:
                continue
            href = a.get("href","")
            if not href.startswith("http"):
                href = "https://bringatrailer.com" + href

            title_el = card.select_one("h2,h3,[class*='title']")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)

            # Strict 1969 check — rejects 1968 and other years
            if not is_1969_camaro(title + " " + href):
                continue

            img_el = card.select_one("img")
            image = ""
            if img_el:
                image = img_el.get("src","") or img_el.get("data-src","")

            price_el = card.select_one("[class*='bid'],[class*='price'],[class*='amount']")
            price = price_el.get_text(strip=True) if price_el else "Auction"

            listings.append({
                "id":        uid(href),
                "source":    "BringATrailer",
                "title":     title,
                "price":     price,
                "price_num": 0,
                "url":       href,
                "image":     image,
                "location":  "United States",
                "is_auction": True,
                "listed_at": now_iso(),
                "fetched_at": now_iso(),
                "is_new":    True,
            })
        if listings:
            break

    log.info(f"  BringATrailer: {len(listings)} listings")
    return listings


def scrape_carsandbids():
    listings = []
    log.info("Scraping Cars & Bids...")
    url = "https://carsandbids.com/search#?q=1969+camaro"
    r = get(url)
    if not r:
        return listings
    soup = BeautifulSoup(r.text, "html.parser")
    for card in soup.select("a[href*='/auctions/']"):
        href = card.get("href","")
        if not href:
            continue
        if not href.startswith("http"):
            href = "https://carsandbids.com" + href
        title_el = card.select_one("h2,h3,[class*='title']")
        title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:80]
        if not is_1969_camaro(title):
            continue
        img_el = card.select_one("img")
        image = img_el.get("src","") if img_el else ""
        price_el = card.select_one("[class*='price'],[class*='bid']")
        price = price_el.get_text(strip=True) if price_el else "Auction"
        listings.append({
            "id":        uid(href),
            "source":    "Cars & Bids",
            "title":     title,
            "price":     price,
            "price_num": 0,
            "url":       href,
            "image":     image,
            "location":  "United States",
            "is_auction": True,
            "listed_at": now_iso(),
            "fetched_at": now_iso(),
            "is_new":    True,
        })
    log.info(f"  Cars & Bids: {len(listings)} listings")
    return listings


# ── DEDUP + SEEN ──────────────────────────────────────────────────────────────

def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen_ids):
    SEEN_FILE.write_text(json.dumps(list(seen_ids)))

def load_existing():
    if OUTPUT_FILE.exists():
        data = json.loads(OUTPUT_FILE.read_text())
        return data.get("listings", []) if isinstance(data, dict) else data
    return []


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def send_email(new_listings):
    if not new_listings or not EMAIL_FROM or not EMAIL_PASS:
        return
    by_source = {}
    for l in new_listings:
        by_source.setdefault(l["source"], []).append(l)

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;">
    <div style="background:#0F0D0B;padding:24px;text-align:center;">
      <h1 style="color:#C8281E;font-size:28px;margin:0;letter-spacing:2px;">🚗 CAMARO HUNTER HQ</h1>
      <p style="color:#888;margin:6px 0 0;">Daily Alert — {datetime.now().strftime('%B %d, %Y')}</p>
    </div>
    <div style="background:#F5F0E8;padding:24px;">
      <p style="font-size:16px;color:#333;">
        <strong>{len(new_listings)} new 1969 Camaro listing(s)</strong> found across {len(by_source)} source(s).
      </p>"""

    for source, items in sorted(by_source.items()):
        html += f'<h3 style="color:#C8281E;border-bottom:2px solid #C8281E;padding-bottom:6px;">{source} ({len(items)})</h3>'
        for l in items:
            img_tag = f'<img src="{l["image"]}" style="width:120px;height:80px;object-fit:cover;border-radius:4px;flex-shrink:0;">' if l.get("image") else ''
            html += f"""
            <div style="background:white;border:1px solid #ddd;border-radius:6px;padding:14px;margin:10px 0;display:flex;gap:14px;align-items:center;">
              {img_tag}
              <div>
                <a href="{l['url']}" style="font-size:15px;font-weight:bold;color:#1a5276;text-decoration:none;">{l['title']}</a><br>
                <span style="color:#27ae60;font-weight:bold;">{l['price'] or 'See listing'}</span>
                <span style="color:#888;font-size:12px;margin-left:10px;">{l['source']}</span>
              </div>
            </div>"""

    html += f"""
      <div style="text-align:center;margin-top:24px;">
        <a href="https://bjnls21.github.io/Camaro-Tracker"
           style="background:#C8281E;color:white;padding:14px 28px;border-radius:4px;text-decoration:none;font-weight:bold;font-size:14px;letter-spacing:1px;">
          VIEW ALL IN DASHBOARD →
        </a>
      </div>
    </div></body></html>"""

    plain = f"New 1969 Camaro Listings — {datetime.now().strftime('%B %d, %Y')}\n\n"
    for l in new_listings:
        plain += f"[{l['source']}] {l['title']} — {l['price']}\n{l['url']}\n\n"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚗 {len(new_listings)} New 1969 Camaro Listing(s) — {datetime.now().strftime('%b %d')}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_FROM, EMAIL_PASS)
        smtp.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    log.info(f"Email sent — {len(new_listings)} new listings.")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=== 1969 Camaro Tracker Starting ===")
    OUTPUT_FILE.parent.mkdir(exist_ok=True)

    seen_ids = load_seen()
    existing = {l["id"]: l for l in load_existing()}

    all_fetched = []
    for name, fn in [
        ("eBay Motors",     scrape_ebay),
        ("Hemmings",        scrape_hemmings),
        ("ClassicCars.com", scrape_classiccars),
        ("BringATrailer",   scrape_bat),
        ("Cars & Bids",     scrape_carsandbids),
    ]:
        try:
            results = fn()
            all_fetched.extend(results)
        except Exception as e:
            log.error(f"{name} failed: {e}", exc_info=True)

    new_listings = []
    merged = {}
    for l in all_fetched:
        l["is_new"] = l["id"] not in seen_ids
        if l["is_new"]:
            new_listings.append(l)
        merged[l["id"]] = l

    for lid, l in existing.items():
        if lid not in merged:
            l["is_new"] = False
            merged[lid] = l

    final = sorted(
        merged.values(),
        key=lambda x: (not x["is_new"], x.get("fetched_at",""))
    )[:500]

    output = {
        "updated_at": now_iso(),
        "total":      len(final),
        "new_count":  len(new_listings),
        "listings":   final,
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    log.info(f"Saved {len(final)} listings ({len(new_listings)} new) → {OUTPUT_FILE}")

    seen_ids.update(l["id"] for l in all_fetched)
    save_seen(seen_ids)

    if new_listings:
        send_email(new_listings)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
