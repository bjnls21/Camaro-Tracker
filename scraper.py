"""
1969 Camaro Hunter — Maximum Coverage Scraper
Sources:
- eBay Motors RSS (3 search variations)
- Kijiji RSS (Canada)
- Craigslist RSS (20 major US cities)
- ClassicCars.com
- BringATrailer RSS
- Mecum upcoming lots
- Barrett-Jackson docket
"""

import os, json, hashlib, smtplib, logging, time, random, re
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO   = os.environ.get("EMAIL_TO", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")

OUTPUT_FILE = Path("docs/listings.json")
SEEN_FILE   = Path("docs/seen_ids.json")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
    }

def clean_url(url):
    url = str(url).split("?")[0].split("#")[0]
    return re.sub(r"/$", "", url).lower().strip()

def uid(url):
    return hashlib.md5(clean_url(url).encode()).hexdigest()[:12]

def title_uid(title, source):
    key = re.sub(r"\s+", " ", title.lower().strip()) + "|" + source.lower()
    return hashlib.md5(key.encode()).hexdigest()[:12]

def get(url):
    for attempt in range(3):
        try:
            time.sleep(random.uniform(2, 5))
            r = requests.get(url, headers=get_headers(), timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            log.warning(f"Attempt {attempt+1} failed for {url[:80]}: {e}")
            time.sleep(random.uniform(5, 10))
    return None

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def is_1969_camaro(text):
    t = text.lower()
    has_camaro = "camaro" in t
    has_1969 = "1969" in t or bool(re.search(r"\b69\b", t))
    other_years = re.findall(r'\b(196[0-8]|197[0-9])\b', text)
    if other_years and "1969" not in text:
        return False
    return has_camaro and has_1969

def extract_price(text):
    m = re.search(r'US \$([\d,]+)', text) or re.search(r'\$([\d,]+)', text) or re.search(r'C\$([\d,]+)', text)
    if m:
        return f"${m.group(1)}", int(m.group(1).replace(",", ""))
    return "", 0

def parse_rss(r, source, is_auction=False, default_location="United States"):
    """Generic RSS parser — works for eBay, BaT, Kijiji, Craigslist."""
    listings = []
    seen = set()
    try:
        soup = BeautifulSoup(r.content, "xml")
    except Exception:
        soup = BeautifulSoup(r.content, "html.parser")

    for item in soup.find_all("item"):
        title_el = item.find("title")
        if not title_el:
            continue
        title = re.sub(r'<[^>]+>', '', title_el.get_text(strip=True))
        if not is_1969_camaro(title):
            continue

        # URL
        guid = item.find("guid")
        link_el = item.find("link")
        href = ""
        if guid:
            href = guid.get_text(strip=True)
        if not href and link_el:
            href = link_el.get_text(strip=True)
        if not href:
            continue

        href_clean = clean_url(href)
        if href_clean in seen:
            continue
        seen.add(href_clean)

        # Description / image / price
        desc_el = item.find("description") or item.find("content")
        desc = desc_el.get_text(strip=True) if desc_el else ""
        desc_html = str(desc_el) if desc_el else ""

        price_raw, price_num = extract_price(desc)
        img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_html, re.I)
        image = img_m.group(1) if img_m else ""

        # Location
        loc_el = item.find("g2:city") or item.find("location") or item.find("g2:region")
        location = loc_el.get_text(strip=True) if loc_el else default_location

        pub = item.find("pubDate") or item.find("dc:date")
        pub_text = pub.get_text(strip=True) if pub else now_iso()

        listings.append({
            "id": uid(href),
            "source": source,
            "title": title,
            "price": price_raw,
            "price_num": price_num,
            "url": href,
            "image": image,
            "location": location,
            "is_auction": is_auction,
            "listed_at": pub_text,
            "fetched_at": now_iso(),
            "is_new": True,
        })
    return listings


# ── eBay Motors ───────────────────────────────────────────────────────────────
def scrape_ebay():
    log.info("Scraping eBay Motors...")
    listings = []
    seen_hrefs = set()
    searches = [
        "https://www.ebay.com/sch/i.html?_nkw=1969+camaro&_sacat=6001&_sop=10&_ipg=100&_rss=1",
        "https://www.ebay.com/sch/i.html?_nkw=1969+chevrolet+camaro&_sacat=6001&_sop=10&_ipg=100&_rss=1",
        "https://www.ebay.com/sch/i.html?_nkw=69+camaro+chevrolet&_sacat=6001&_sop=10&_ipg=100&_rss=1",
    ]
    for url in searches:
        r = get(url)
        if r:
            items = parse_rss(r, "eBay Motors")
            for item in items:
                if clean_url(item["url"]) not in seen_hrefs:
                    seen_hrefs.add(clean_url(item["url"]))
                    listings.append(item)
        time.sleep(random.uniform(3, 6))
    log.info(f"  eBay: {len(listings)} listings")
    return listings


# ── Kijiji (Canada) ───────────────────────────────────────────────────────────
def scrape_kijiji():
    log.info("Scraping Kijiji...")
    listings = []
    # Kijiji RSS feeds for major Canadian cities
    cities = [
        ("https://www.kijiji.ca/rss-srp-cars-trucks/canada/1969-camaro/k0l0?dc=true", "Canada"),
        ("https://www.kijiji.ca/rss-srp-cars-trucks/ontario/1969-camaro/k0l9004?dc=true", "Ontario, Canada"),
        ("https://www.kijiji.ca/rss-srp-cars-trucks/alberta/1969-camaro/k0l9003?dc=true", "Alberta, Canada"),
        ("https://www.kijiji.ca/rss-srp-cars-trucks/british-columbia/1969-camaro/k0l9007?dc=true", "BC, Canada"),
    ]
    seen = set()
    for url, location in cities:
        r = get(url)
        if not r:
            continue
        items = parse_rss(r, "Kijiji", default_location=location)
        for item in items:
            if clean_url(item["url"]) not in seen:
                seen.add(clean_url(item["url"]))
                listings.append(item)
        time.sleep(random.uniform(2, 4))
    log.info(f"  Kijiji: {len(listings)} listings")
    return listings


# ── Craigslist (20 major cities via RSS) ─────────────────────────────────────
def scrape_craigslist():
    log.info("Scraping Craigslist...")
    listings = []
    # Major US cities with RSS feeds
    cities = [
        ("https://losangeles.craigslist.org/search/cto?query=1969+camaro&format=rss", "Los Angeles, CA"),
        ("https://chicago.craigslist.org/search/cto?query=1969+camaro&format=rss", "Chicago, IL"),
        ("https://houston.craigslist.org/search/cto?query=1969+camaro&format=rss", "Houston, TX"),
        ("https://phoenix.craigslist.org/search/cto?query=1969+camaro&format=rss", "Phoenix, AZ"),
        ("https://dallas.craigslist.org/search/cto?query=1969+camaro&format=rss", "Dallas, TX"),
        ("https://miami.craigslist.org/search/cto?query=1969+camaro&format=rss", "Miami, FL"),
        ("https://atlanta.craigslist.org/search/cto?query=1969+camaro&format=rss", "Atlanta, GA"),
        ("https://detroit.craigslist.org/search/cto?query=1969+camaro&format=rss", "Detroit, MI"),
        ("https://denver.craigslist.org/search/cto?query=1969+camaro&format=rss", "Denver, CO"),
        ("https://seattle.craigslist.org/search/cto?query=1969+camaro&format=rss", "Seattle, WA"),
        ("https://minneapolis.craigslist.org/search/cto?query=1969+camaro&format=rss", "Minneapolis, MN"),
        ("https://nashville.craigslist.org/search/cto?query=1969+camaro&format=rss", "Nashville, TN"),
        ("https://portland.craigslist.org/search/cto?query=1969+camaro&format=rss", "Portland, OR"),
        ("https://lasvegas.craigslist.org/search/cto?query=1969+camaro&format=rss", "Las Vegas, NV"),
        ("https://sandiego.craigslist.org/search/cto?query=1969+camaro&format=rss", "San Diego, CA"),
        ("https://sfbay.craigslist.org/search/cto?query=1969+camaro&format=rss", "San Francisco, CA"),
        ("https://newyork.craigslist.org/search/cto?query=1969+camaro&format=rss", "New York, NY"),
        ("https://boston.craigslist.org/search/cto?query=1969+camaro&format=rss", "Boston, MA"),
        ("https://charlotte.craigslist.org/search/cto?query=1969+camaro&format=rss", "Charlotte, NC"),
        ("https://indianapolis.craigslist.org/search/cto?query=1969+camaro&format=rss", "Indianapolis, IN"),
    ]
    seen = set()
    for url, location in cities:
        r = get(url)
        if not r:
            continue
        items = parse_rss(r, "Craigslist", default_location=location)
        for item in items:
            if clean_url(item["url"]) not in seen:
                seen.add(clean_url(item["url"]))
                item["location"] = location
                listings.append(item)
        time.sleep(random.uniform(1, 3))
    log.info(f"  Craigslist: {len(listings)} listings")
    return listings


# ── BringATrailer ─────────────────────────────────────────────────────────────
def scrape_bat():
    log.info("Scraping BringATrailer...")
    listings = []
    urls = [
        "https://bringatrailer.com/feed/?s=1969+camaro",
        "https://bringatrailer.com/feed/?s=1969+chevrolet+camaro",
    ]
    seen = set()
    for url in urls:
        r = get(url)
        if not r:
            continue
        items = parse_rss(r, "BringATrailer", is_auction=True)
        for item in items:
            if clean_url(item["url"]) not in seen:
                seen.add(clean_url(item["url"]))
                listings.append(item)
    log.info(f"  BringATrailer: {len(listings)} listings")
    return listings


# ── ClassicCars.com ───────────────────────────────────────────────────────────
def scrape_classiccars():
    log.info("Scraping ClassicCars.com...")
    listings = []
    urls = [
        "https://classiccars.com/listings/find/1969/chevrolet/camaro",
        "https://www.classiccars.com/listings/find/1969/chevrolet/camaro",
    ]
    seen = set()
    for url in urls:
        r = get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        cards = (
            soup.select("div.listing-card") or
            soup.select("article.listing") or
            soup.select("div[class*='listing-item']") or
            soup.select("a[href*='/listings/']")
        )
        for card in cards:
            a = card if card.name == "a" else card.select_one("a[href*='/listings/']")
            if not a:
                continue
            href = a.get("href", "")
            if not href or len(href) < 10 or "find" in href:
                continue
            full_url = "https://classiccars.com" + href if href.startswith("/") else href
            if clean_url(full_url) in seen:
                continue
            full_text = card.get_text(" ", strip=True)
            if not is_1969_camaro(full_text + " " + full_url):
                continue
            seen.add(clean_url(full_url))
            title_el = card.select_one("h2,h3,[class*='title'],[class*='heading']")
            title = title_el.get_text(strip=True) if title_el else "1969 Chevrolet Camaro"
            price_el = card.select_one("[class*='price'],[class*='Price']")
            price_text = price_el.get_text(strip=True) if price_el else "See listing"
            price_raw, price_num = extract_price(price_text)
            if not price_raw:
                price_raw = price_text
            img_el = card.select_one("img")
            image = ""
            if img_el:
                image = img_el.get("src","") or img_el.get("data-src","")
            listings.append({
                "id": uid(full_url),
                "source": "ClassicCars.com",
                "title": title,
                "price": price_raw,
                "price_num": price_num,
                "url": full_url,
                "image": image,
                "location": "United States",
                "is_auction": False,
                "listed_at": now_iso(),
                "fetched_at": now_iso(),
                "is_new": True,
            })
        if listings:
            break
    log.info(f"  ClassicCars.com: {len(listings)} listings")
    return listings


# ── Mecum Upcoming Lots ───────────────────────────────────────────────────────
def scrape_mecum():
    log.info("Scraping Mecum...")
    listings = []
    url = "https://www.mecum.com/lots/search/?searchQuery=1969+camaro"
    r = get(url)
    if not r:
        return listings
    soup = BeautifulSoup(r.text, "html.parser")
    seen = set()
    for card in soup.select("div.lot-item, article.lot, div[class*='lot']"):
        a = card.select_one("a[href*='/lots/']")
        if not a:
            continue
        href = a.get("href","")
        if not href:
            continue
        full_url = "https://www.mecum.com" + href if href.startswith("/") else href
        if clean_url(full_url) in seen:
            continue
        full_text = card.get_text(" ", strip=True)
        if not is_1969_camaro(full_text + " " + full_url):
            continue
        seen.add(clean_url(full_url))
        title_el = card.select_one("h2,h3,[class*='title'],[class*='name']")
        title = title_el.get_text(strip=True) if title_el else "1969 Chevrolet Camaro"
        price_el = card.select_one("[class*='price'],[class*='estimate'],[class*='sold']")
        price_text = price_el.get_text(strip=True) if price_el else "See listing"
        price_raw, price_num = extract_price(price_text)
        if not price_raw:
            price_raw = price_text
        img_el = card.select_one("img")
        image = img_el.get("src","") or img_el.get("data-src","") if img_el else ""
        listings.append({
            "id": uid(full_url),
            "source": "Mecum Auctions",
            "title": title,
            "price": price_raw or "Upcoming Auction",
            "price_num": price_num,
            "url": full_url,
            "image": image,
            "location": "United States",
            "is_auction": True,
            "listed_at": now_iso(),
            "fetched_at": now_iso(),
            "is_new": True,
        })
    log.info(f"  Mecum: {len(listings)} listings")
    return listings


# ── Barrett-Jackson ───────────────────────────────────────────────────────────
def scrape_barrett_jackson():
    log.info("Scraping Barrett-Jackson...")
    listings = []
    url = "https://www.barrett-jackson.com/Media/Home/Summary/camaro/"
    r = get(url)
    if not r:
        return listings
    soup = BeautifulSoup(r.text, "html.parser")
    seen = set()
    for card in soup.select("div.lot, article, div[class*='vehicle'], div[class*='lot']"):
        a = card.select_one("a[href]")
        if not a:
            continue
        href = a.get("href","")
        if not href:
            continue
        full_url = "https://www.barrett-jackson.com" + href if href.startswith("/") else href
        if clean_url(full_url) in seen:
            continue
        full_text = card.get_text(" ", strip=True)
        if not is_1969_camaro(full_text + " " + full_url):
            continue
        seen.add(clean_url(full_url))
        title_el = card.select_one("h2,h3,[class*='title'],[class*='name']")
        title = title_el.get_text(strip=True) if title_el else "1969 Chevrolet Camaro"
        img_el = card.select_one("img")
        image = img_el.get("src","") or img_el.get("data-src","") if img_el else ""
        listings.append({
            "id": uid(full_url),
            "source": "Barrett-Jackson",
            "title": title,
            "price": "Upcoming Auction",
            "price_num": 0,
            "url": full_url,
            "image": image,
            "location": "United States",
            "is_auction": True,
            "listed_at": now_iso(),
            "fetched_at": now_iso(),
            "is_new": True,
        })
    log.info(f"  Barrett-Jackson: {len(listings)} listings")
    return listings


# ── DEDUP + SEEN ──────────────────────────────────────────────────────────────
def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(s):
    SEEN_FILE.write_text(json.dumps(list(s)))

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
      <h1 style="color:#C8281E;font-size:28px;margin:0;">🚗 CAMARO HUNTER HQ</h1>
      <p style="color:#888;margin:6px 0 0;">Daily Alert — {datetime.now().strftime('%B %d, %Y')}</p>
    </div>
    <div style="background:#F5F0E8;padding:24px;">
      <p><strong>{len(new_listings)} new 1969 Camaro listing(s)</strong> found across {len(by_source)} source(s).</p>"""

    for source, items in sorted(by_source.items()):
        html += f'<h3 style="color:#C8281E;border-bottom:2px solid #C8281E;padding-bottom:6px;">{source} ({len(items)})</h3>'
        for l in items:
            img = f'<img src="{l["image"]}" style="width:120px;height:80px;object-fit:cover;border-radius:4px;flex-shrink:0;">' if l.get("image") else ""
            html += f"""<div style="background:white;border:1px solid #ddd;border-radius:6px;padding:14px;margin:10px 0;display:flex;gap:14px;align-items:center;">
              {img}<div>
                <a href="{l['url']}" style="font-weight:bold;color:#1a5276;">{l['title']}</a><br>
                <span style="color:#27ae60;font-weight:bold;">{l['price'] or 'See listing'}</span>
                <span style="color:#888;font-size:12px;margin-left:8px;">{l['source']}</span>
              </div></div>"""

    html += f"""<div style="text-align:center;margin-top:24px;">
        <a href="https://bjnls21.github.io/Camaro-Tracker"
           style="background:#C8281E;color:white;padding:14px 28px;border-radius:4px;text-decoration:none;font-weight:bold;">
          VIEW ALL IN DASHBOARD →
        </a></div></div></body></html>"""

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
        ("eBay Motors",       scrape_ebay),
        ("Kijiji",            scrape_kijiji),
        ("Craigslist",        scrape_craigslist),
        ("BringATrailer",     scrape_bat),
        ("ClassicCars.com",   scrape_classiccars),
        ("Mecum",             scrape_mecum),
        ("Barrett-Jackson",   scrape_barrett_jackson),
    ]:
        try:
            results = fn()
            all_fetched.extend(results)
            log.info(f"  ✓ {name}: {len(results)} listings")
        except Exception as e:
            log.error(f"{name} failed: {e}", exc_info=True)

    # Deduplicate by URL and by title+source
    new_listings = []
    merged = {}
    seen_title_ids = set()

    for l in all_fetched:
        tid = title_uid(l["title"], l["source"])
        if l["id"] in merged or tid in seen_title_ids:
            continue
        seen_title_ids.add(tid)
        l["is_new"] = l["id"] not in seen_ids
        if l["is_new"]:
            new_listings.append(l)
        merged[l["id"]] = l

    # Keep old listings too
    for lid, l in existing.items():
        if lid not in merged:
            l["is_new"] = False
            merged[lid] = l

    final = sorted(
        merged.values(),
        key=lambda x: (not x["is_new"], x.get("fetched_at",""))
    )[:1000]

    output = {
        "updated_at": now_iso(),
        "total": len(final),
        "new_count": len(new_listings),
        "listings": final,
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
