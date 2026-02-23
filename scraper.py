"""
1969 Camaro Listing Scraper
Fetches listings from eBay, Craigslist (via AutoTempest RSS),
Hemmings, ClassicCars.com, BringATrailer, and Cars & Bids.
Saves results to docs/listings.json so the dashboard can read it.
Also emails you a daily summary of NEW listings.
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

OUTPUT_FILE  = Path("docs/listings.json")
SEEN_FILE    = Path("docs/seen_ids.json")

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
    """Make a short stable ID from a string."""
    return hashlib.md5(s.encode()).hexdigest()[:12]

def get(url):
    for attempt in range(3):
        try:
            time.sleep(random.uniform(2, 4))
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return r
        except Exception as e:
            log.warning(f"Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(6)
    return None

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# ── SCRAPERS ──────────────────────────────────────────────────────────────────

def scrape_ebay():
    listings = []
    log.info("Scraping eBay Motors...")
    # eBay RSS feed — public, no auth needed
    url = "https://www.ebay.com/sch/i.html?_nkw=1969+camaro&_sacat=6001&_sop=10&_ipg=50&_rss=1"
    r = get(url)
    if not r:
        return listings
    soup = BeautifulSoup(r.content, "xml")
    for item in soup.find_all("item"):
        title = item.find("title")
        if not title:
            continue
        title_text = title.get_text(strip=True)
        if not any(k in title_text.lower() for k in ["camaro","1969","69"]):
            continue

        link_tag = item.find("link")
        link = ""
        if link_tag:
            link = link_tag.next_sibling
            if not link or not str(link).startswith("http"):
                guid = item.find("guid")
                link = guid.get_text(strip=True) if guid else ""
        link = str(link).strip()

        desc = item.find("description")
        desc_text = desc.get_text(strip=True) if desc else ""

        # Price
        price_match = re.search(r'US \$([\d,]+)', desc_text) or re.search(r'\$([\d,]+)', desc_text)
        price_raw = f"${price_match.group(1)}" if price_match else ""
        price_num = int(price_match.group(1).replace(",","")) if price_match else 0

        # Image
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_text, re.I)
        image = img_match.group(1) if img_match else ""

        # Date
        pub = item.find("pubDate")
        pub_text = pub.get_text(strip=True) if pub else now_iso()

        is_auction = "bid" in desc_text.lower() and "buy it now" not in desc_text.lower()

        listings.append({
            "id":       uid(link or title_text),
            "source":   "eBay Motors",
            "title":    title_text,
            "price":    price_raw,
            "price_num": price_num,
            "url":      link,
            "image":    image,
            "location": "United States",
            "is_auction": is_auction,
            "listed_at": pub_text,
            "fetched_at": now_iso(),
            "is_new":   True,
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

    # Hemmings listing cards
    cards = soup.select("a[href*='/classifieds/listing/']")
    seen_hrefs = set()
    for card in cards:
        href = card.get("href","")
        if not href or href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        full_url = "https://www.hemmings.com" + href if href.startswith("/") else href

        title_el = card.select_one("h2,h3,.title,strong")
        title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:80]
        if not title or len(title) < 5:
            continue

        price_el = card.select_one(".price,[class*='price']")
        price = price_el.get_text(strip=True) if price_el else "See listing"
        price_num = 0
        pm = re.search(r'[\d,]+', price.replace("$",""))
        if pm:
            price_num = int(pm.group().replace(",",""))

        img_el = card.select_one("img")
        image = img_el.get("src","") if img_el else ""

        loc_el = card.select_one("[class*='location'],[class*='city']")
        location = loc_el.get_text(strip=True) if loc_el else "United States"

        listings.append({
            "id":       uid(full_url),
            "source":   "Hemmings",
            "title":    title,
            "price":    price,
            "price_num": price_num,
            "url":      full_url,
            "image":    image,
            "location": location,
            "is_auction": False,
            "listed_at": now_iso(),
            "fetched_at": now_iso(),
            "is_new":   True,
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

    for card in soup.select("a.listing-card, div.listing-card, article[class*='listing']"):
        href = card.get("href","")
        if not href:
            a = card.select_one("a[href*='/listing']")
            href = a.get("href","") if a else ""
        if not href:
            continue
        full_url = "https://classiccars.com" + href if href.startswith("/") else href

        title_el = card.select_one("h2,h3,.title,[class*='title']")
        title = title_el.get_text(strip=True) if title_el else "1969 Chevrolet Camaro"

        price_el = card.select_one("[class*='price']")
        price = price_el.get_text(strip=True) if price_el else "See listing"
        price_num = 0
        pm = re.search(r'[\d,]+', price.replace("$",""))
        if pm:
            price_num = int(pm.group().replace(",",""))

        img_el = card.select_one("img")
        image = img_el.get("src","") or img_el.get("data-src","") if img_el else ""

        listings.append({
            "id":       uid(full_url),
            "source":   "ClassicCars.com",
            "title":    title,
            "price":    price,
            "price_num": price_num,
            "url":      full_url,
            "image":    image,
            "location": "United States",
            "is_auction": False,
            "listed_at": now_iso(),
            "fetched_at": now_iso(),
            "is_new":   True,
        })
    log.info(f"  ClassicCars.com: {len(listings)} listings")
    return listings


def scrape_bat():
    listings = []
    log.info("Scraping BringATrailer...")
    # BaT has a JSON search endpoint
    url = "https://bringatrailer.com/wp-json/bringatrailer/1.0/data/listings-filter?per_page=50&listing_status=open&s=1969+camaro"
    r = get(url)
    if not r:
        # fallback HTML
        r = get("https://bringatrailer.com/search/?s=1969+camaro")
        if not r:
            return listings
        soup = BeautifulSoup(r.text, "html.parser")
        for card in soup.select("article.post,div.listing-card"):
            a = card.select_one("a[href*='bringatrailer.com']")
            if not a:
                continue
            href = a.get("href","")
            title_el = card.select_one("h2,h3")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            if "1969" not in title and "camaro" not in title.lower():
                continue
            img_el = card.select_one("img")
            image = img_el.get("src","") if img_el else ""
            listings.append({
                "id": uid(href),
                "source": "BringATrailer",
                "title": title,
                "price": "Auction",
                "price_num": 0,
                "url": href,
                "image": image,
                "location": "United States",
                "is_auction": True,
                "listed_at": now_iso(),
                "fetched_at": now_iso(),
                "is_new": True,
            })
        return listings

    try:
        data = r.json()
        items = data if isinstance(data, list) else data.get("listings", data.get("data", []))
        for item in items:
            title = item.get("post_title", item.get("title",""))
            if "1969" not in title and "camaro" not in title.lower():
                continue
            href = item.get("url", item.get("permalink",""))
            bid = item.get("bid_formatted", item.get("current_bid","Auction"))
            img = item.get("thumbnail","")
            listings.append({
                "id": uid(href),
                "source": "BringATrailer",
                "title": title,
                "price": str(bid),
                "price_num": 0,
                "url": href,
                "image": img,
                "location": "United States",
                "is_auction": True,
                "listed_at": now_iso(),
                "fetched_at": now_iso(),
                "is_new": True,
            })
    except Exception as e:
        log.warning(f"BaT JSON error: {e}")

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
    for card in soup.select("a.auction-card, article.listing, div[class*='auction']"):
        href = card.get("href","")
        if not href:
            a = card.select_one("a")
            href = a.get("href","") if a else ""
        if not href:
            continue
        if not href.startswith("http"):
            href = "https://carsandbids.com" + href
        title_el = card.select_one("h2,h3,[class*='title']")
        title = title_el.get_text(strip=True) if title_el else "1969 Chevrolet Camaro"
        if "camaro" not in title.lower() and "1969" not in title:
            continue
        price_el = card.select_one("[class*='price'],[class*='bid']")
        price = price_el.get_text(strip=True) if price_el else "Auction"
        img_el = card.select_one("img")
        image = img_el.get("src","") if img_el else ""
        listings.append({
            "id": uid(href),
            "source": "Cars & Bids",
            "title": title,
            "price": price,
            "price_num": 0,
            "url": href,
            "image": image,
            "location": "United States",
            "is_auction": True,
            "listed_at": now_iso(),
            "fetched_at": now_iso(),
            "is_new": True,
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
        return json.loads(OUTPUT_FILE.read_text())
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
      <p style="font-size:16px;color:#333;"><strong>{len(new_listings)} new listing(s)</strong> found across {len(by_source)} source(s).</p>
    """
    for source, items in sorted(by_source.items()):
        html += f'<h3 style="color:#C8281E;border-bottom:2px solid #C8281E;padding-bottom:6px;">{source} ({len(items)})</h3>'
        for l in items:
            html += f"""
            <div style="background:white;border:1px solid #ddd;border-radius:6px;padding:14px;margin:10px 0;display:flex;gap:14px;">
              {'<img src="'+l["image"]+'" style="width:100px;height:70px;object-fit:cover;border-radius:4px;flex-shrink:0;">' if l.get("image") else ''}
              <div>
                <a href="{l['url']}" style="font-size:15px;font-weight:bold;color:#1a5276;text-decoration:none;">{l['title']}</a><br>
                <span style="color:#27ae60;font-weight:bold;font-size:15px;">{l['price'] or 'See listing'}</span>
                <span style="color:#888;font-size:12px;margin-left:10px;">{l['source']}</span>
              </div>
            </div>"""
    html += f"""
      <div style="text-align:center;margin-top:24px;">
        <a href="https://bjnls21.github.io/camaro-tracker" 
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
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

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

    # Mark new vs seen
    new_listings = []
    merged = {}
    for l in all_fetched:
        l["is_new"] = l["id"] not in seen_ids
        if l["is_new"]:
            new_listings.append(l)
        merged[l["id"]] = l

    # Keep old listings that are still relevant (up to 500 total)
    for lid, l in existing.items():
        if lid not in merged:
            l["is_new"] = False
            merged[lid] = l

    final = sorted(merged.values(), key=lambda x: (not x["is_new"], x.get("fetched_at","")))[:500]

    # Save
    output = {
        "updated_at": now_iso(),
        "total": len(final),
        "new_count": len(new_listings),
        "listings": final,
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    log.info(f"Saved {len(final)} listings ({len(new_listings)} new) to {OUTPUT_FILE}")

    # Update seen IDs
    seen_ids.update(l["id"] for l in all_fetched)
    save_seen(seen_ids)

    # Email
    if new_listings:
        send_email(new_listings)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
