"""Companies moving in: reads Google News for companies opening or moving offices near Josh.
When a company opens an office here, its people need homes. fetch_posts.py calls update_moves()."""
import html, re, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

# ===== EDIT HERE: places to watch, grouped by the towns Josh suggests for them =====
AREAS = {
    "morris": ["Morristown", "Parsippany", "Morris County", "Florham Park", "Whippany", "East Hanover",
               "Basking Ridge", "Bridgewater", "Berkeley Heights", "Summit", "Madison NJ", "Short Hills", "Livingston"],
    "hudson": ["Hoboken", "Jersey City", "Weehawken", "Newark"],
    "essex": ["Montclair", "Maplewood", "West Orange", "Roseland"],
    "nyc": ["Manhattan", "New York City", "Brooklyn"],
}
# Words that mean a company is bringing people here
SIGNAL = r"\b(opens?|opening|new (global |u\.?s\.? |north american |regional )?(hq|headquarters|office|offices|campus|hub)|headquarters|relocat\w*|moves? (its |their )?(hq|headquarters|offices?)|moving (its )?(hq|headquarters|offices?)|expands?|expansion|signs? (a )?lease|inks? (a )?lease|leases? \d|square feet|sq\.? ?ft|\d[\d,]* (new )?jobs|to hire|hiring)\b"
# Things that look like news but aren't about employers moving people in
SKIP = r"\b(obituar\w*|restaurants?|bars?|cafes?|bakery|bakeries|pizza\w*|delis?|brewer\w*|salons?|spas?|gyms?|boutiques?|stores?|shops?|retail\w*|reopen\w*|bicycles?|refinanc\w*|loans?|apartments?|units|towers?|condos?|residential|schools?|police|fire|church\w*|arrest\w*|crime|shooting|lawsuits?|sues|closing|closes|layoffs?|cuts?|trims?|eliminat\w*|at risk|bankrupt\w*|warehouses?|dispensar\w*|cannabis|hotels?|museums?|library|parks?|parking|urgent care|daycare|renovation|stock|ipo|trading|recycling|umicore|belgi\w*|antwerp|chicago|crisis|retain\w*|sports|hot dogs?|stands?|forced|bay area|news & notes|roundup|and more|needs an?|grief|nonprofit|charity)\b"
NYC_STRONG = r"\b(establish\w*|creat\w* (up to )?\d[\d,]* (new )?jobs|\d[\d,]* new jobs|first (new york|nyc|manhattan|east coast|u\.?s\.?) (office|hq|headquarters)|expand\w* (to|in|into) new york|moves? (its )?(hq|headquarters) to (new york|manhattan|nyc))\b"   # moves inside Manhattan don't bring new people; keep companies coming from elsewhere

UA = "BuyerRadar/1.0 (personal news reader for one real estate agent)"
KEEP_DAYS = 30


def _feed(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query + " when:30d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return ET.fromstring(r.read())


def _company(title):
    """Best guess at the company name: the words before 'opens', 'moves', etc."""
    m = re.match(r"^(.{2,60}?)\s+(opens|to open|will open|is opening|moves|to move|relocates|to relocate|signs|inks|expands|to expand|leases|picks|chooses|selects|plans|announces|unveils|debuts|launches|establishes|sets|brings|takes|lands|commits|invests|to establish|will establish)\b", title, re.I)
    c = m.group(1).strip(" '\"‘’“”") if m else ""
    if len(c.split()) > 5 or re.search(r"\b(governor|mayor|announces?|news|state|development|city|county|report)\b", c, re.I):
        c = ""
    if not c:   # "... Announces AI Company DDN to Establish ..."
        m2 = re.search(r"\b(?i:company|firm|startup|maker|developer|provider)\s+([A-Z][\w&.'\-]*(?:\s+[A-Z][\w&.'\-]*){0,3}?)\s+(?i:to|will|opens|expands|is|has)\b", title) \
            or re.search(r"\b(?i:announces)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3}?)(?:’s|'s)\s", title)
        return m2.group(1) if m2 else ""
    return re.sub(r"^.*\b(developer|maker|firm|company|startup|provider|giant)\s+", "", c, flags=re.I)


def update_moves(old_moves):
    """Returns (moves, errors). Keeps earlier finds for 30 days."""
    by_key, errors = {}, []
    for m in old_moves or []:
        by_key[m["key"]] = m
    for area, places in AREAS.items():
        q = "(" + " OR ".join(f'"{p}"' for p in places) + ") (headquarters OR \"new office\" OR relocate OR relocating OR \"signs lease\" OR jobs OR expands)"
        try:
            root = _feed(q)
        except Exception as e:
            errors.append(f"news {area}: {e}")
            continue
        for it in root.findall(".//item"):
            raw = html.unescape(it.findtext("title") or "")
            source = (it.findtext("source") or "").strip()
            title = re.sub(r"\s+-\s+[^-]+$", "", raw).strip()   # drop " - Source Name"
            title = re.sub(r"\s+\|\s+[^|]+$", "", title).strip()   # and " | Source Name"
            t_low = title.lower()
            if not re.search(SIGNAL, t_low) or re.search(SKIP, t_low):
                continue
            place = next((p for p in places if p.lower().replace(" nj", "") in t_low), "")
            if not place:
                continue
            if area == "nyc" and not re.search(NYC_STRONG, t_low):
                continue
            try:
                ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
            except Exception:
                ts = int(time.time())
            company = _company(title)
            key = (company or title).lower()[:60] + "|" + place.lower()
            if key in by_key:
                continue          # same company and place already found (often several articles)
            by_key[key] = {"key": key, "title": title, "source": source, "url": it.findtext("link") or "",
                           "t": ts, "company": company, "place": place.replace(" NJ", ""), "area": area}
        time.sleep(2)
    cutoff = time.time() - KEEP_DAYS * 86400
    moves = sorted((m for m in by_key.values() if m["t"] >= cutoff), key=lambda m: -m["t"])[:60]
    return moves, errors


if __name__ == "__main__":
    ms, errs = update_moves([])
    for m in ms:
        print(time.strftime("%b %d", time.localtime(m["t"])), "|", m["area"], "|", m["place"], "|", m["company"] or "?", "|", m["title"][:90], "|", m["source"])
    print(len(ms), "found", errs)
