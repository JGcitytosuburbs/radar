"""Lead Tracker post checker.

Runs on GitHub twice an hour. Reads Google Alerts feeds (listed in alerts.json),
which Google fills with new Reddit and forum posts that use the alert's words.
Keeps the ones that fit Josh's four lists and saves them to posts.json.
The page then shows them under "New posts right now".

Reading Reddit directly from GitHub doesn't work (Reddit blocks GitHub's servers),
so READ_REDDIT_DIRECTLY is off.

To change what counts as a match, edit the RULES section below.
"""
import html, json, os, re, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ===== EDIT HERE: groups to read (only groups whose rules allow agents to help) =====
GROUPS = ["MovingtoNewJersey", "newjersey", "Hoboken", "jerseycity", "NYCapartments",
          "FirstTimeHomeBuyer", "movingtonyc"]

# ===== EDIT HERE: rules. A post matches a list when its title fits. =====
NJ_PLACES = r"(nj|new jersey|hoboken|jersey city|montclair|maplewood|south orange|summit|short hills|millburn|westfield|madison|chatham|morristown|morris county|cranford|bloomfield|denville|weehawken|edgewater|ridgewood|glen ridge|essex county|bergen|union county|suburbs?|weehawken|edgewater|west new york|glen ridge|bloomfield|millburn|short hills|morris township|morris plains|cranford|ridgewood|west orange|denville|boonton|mountain lakes|livingston|caldwell|verona|nutley|berkeley heights|new providence|florham park|basking ridge|bernardsville|upper montclair|hamilton park|downtown jersey city|paulus hook)"
NYC_PLACES = r"(nyc|new york|manhattan|brooklyn|queens|the city|astoria|park slope|upper west side|uws|upper east side|ues|harlem|lic|long island city|williamsburg|greenpoint|brooklyn heights|carroll gardens|cobble hill|fort greene|prospect heights|bushwick|bed-?stuy|ditmas park|bay ridge|sunnyside|forest hills|jackson heights|ridgewood queens|tribeca|west village|east village|greenwich village|chelsea|murray hill|upper manhattan|washington heights|inwood|yorkville|fidi|financial district|battery park|lower east side|soho)"
NEVER = r"(roommate|sublet|sublease|lease takeover|furniture|ticket|car insurance|auto insurance|\bcar\b|cars|dealer|vehicle|truck|parking|job|hiring|for rent:|\[for rent\]|giveaway|lost|found|scam|free |boxes|packing|nintendo|playstation|xbox|for sale|\(originally|\bobo\b|ac unit|air conditioner|fossil|climate|election|senate|congress|inspector|inspection|contractor|plumber|electrician|roofer|hvac|handyman|landscap\w*|painter|movers|moving company|attorney|lawyer|exterminator|pest|daycare|babysitter|dentist|doctor|pediatrician|vet\b|gym|restaurant)"

RULES = {
    # People moving to New Jersey
    "nj": [
        r"\bmidtown direct\b.*\b(towns?|which|best|looking|moving|move|buy\w*|live|living|family)\b|\b(towns?|which|best|looking|moving|move|buy\w*|live|living)\b.*\bmidtown direct\b",
        r"\b(leaving|moving out of|moving from|getting out of) (brooklyn|manhattan|queens|nyc|new york|the city)\b",
        r"\b(moving|move|relocating|relocate|relocation)\b.*\b" + NJ_PLACES + r"\b",
        r"\b(leave|leaving|moving out of|moving from|get out of)\b.*\b" + NYC_PLACES + r"\b",
        r"\b(buy|buying|purchase|purchasing|first home|first house)\b.*\b" + NJ_PLACES + r"\b",
        r"\b" + NYC_PLACES + r"\b.*\bto\b.*\b" + NJ_PLACES + r"\b",
        r"\b(which|what|best|good|recommend\w*)\b.*\btowns?\b",
        r"\btowns?\b.*\b(commute|recommend\w*|families|family|kids)\b",
        r"\bcommut\w*\b.*\b(nyc|manhattan|midtown|penn|path|grand central|wtc|fidi|city)\b",
        r"\bwhere (to|should (i|we)) (live|move|buy)\b",
    ],
    # People buying in New York City
    "nyc": [
        r"\b(co-?op|condo|board package|board interview|flip tax|maintenance fee)\b",
        r"\b(buy|buying|purchase|purchasing|first home|first apartment)\b.*\b" + NYC_PLACES + r"\b",
        r"\b" + NYC_PLACES + r"\b.*\b(buy|buying|purchase|first[- ]time buyer)\b",
        r"\brent (or|vs\.?|versus) buy\b",
    ],
    # People asking for an agent
    "agent": [
        r"\b(realtor|real estate agent|buyer'?s agent|listing agent|broker)\b.*\b(recommend\w*|suggest\w*|looking for|need|good|great|any|trust)\b",
        r"\b(recommend\w*|suggest\w*|looking for|need|good|great)\b.*\b(realtor|real estate agent|buyer'?s agent|listing agent)\b",
        r"\b(pick|picking|choose|choosing|find|finding|interview\w*)\b.*\b(realtor|agent|listing agent|broker)\b",
    ],
    # People selling
    "sell": [
        r"\b(selling|sell|list|listing)\b.*\b(my|our)\b.*\b(house|home|condo|co-?op|apartment|townhouse|place)\b",
        r"\blisting agent\b",
        r"\b(sell|selling) (and|then|before) (buy|buying)\b",
        r"\bthinking (of|about) selling\b",
        r"\b(what('?s| is) (my|our) (house|home|condo|place) worth|home value|how much (can|could|would) (i|we) (get|sell)|price (my|our) (house|home|condo))\b",
        r"\b(downsiz\w*|empty nest\w*)\b",
        r"\b(need|have|going) to sell\b|\bsell(ing)? (fast|quickly|before)\b",
        r"\b(for sale by owner|fsbo)\b",
        r"\b(should (i|we) sell|sell or rent (out )?(my|our)|rent (out )?or sell)\b",
        r"\b(estate|inherited) (house|home|property)\b|\binherited a (house|home)\b",
    ],
}
# Groups where every post is on-topic for a list, even without a matching title
WHOLE_GROUP = {"MovingtoNewJersey": "nj", "movingtonyc": "nyc"}
# Groups that only count for some lists (keeps general NJ chatter out of the NYC list, and so on)
GROUP_LISTS = {
    "newjersey": ["nj", "agent", "sell"], "Hoboken": ["nj", "agent", "sell"], "jerseycity": ["nj", "agent", "sell"],
    "NYCapartments": ["nj", "nyc"], "FirstTimeHomeBuyer": ["nj", "nyc", "agent"],
    "MovingtoNewJersey": ["nj", "agent", "sell"], "movingtonyc": ["nyc", "agent"],
}
# A post only counts when it's clearly about New Jersey or New York City:
# a strong place name in the title, or it was posted in a local group.
NJ_STRONG = r"\b(nj|midtown direct|new jersey|hoboken|jersey city|montclair|maplewood|south orange|short hills|millburn|morristown|morris county|cranford|bloomfield|denville|weehawken|edgewater|ridgewood|glen ridge|essex county|bergen county|union county|weehawken|edgewater|west new york|glen ridge|bloomfield|millburn|short hills|morris township|morris plains|cranford|ridgewood|west orange|denville|boonton|mountain lakes|livingston|caldwell|verona|nutley|berkeley heights|new providence|florham park|basking ridge|bernardsville|upper montclair|hamilton park|downtown jersey city|paulus hook)\b"
NJ_MAYBE = r"\b(summit|madison|westfield|chatham|suburbs?)\b"   # also common words, so only with other NJ clues
NYC_STRONG = r"\b(nyc|new york|manhattan|brooklyn|queens|astoria|park slope|upper west side|upper east side|harlem|long island city|williamsburg|greenpoint|brooklyn heights|carroll gardens|cobble hill|fort greene|prospect heights|bushwick|bed-?stuy|ditmas park|bay ridge|sunnyside|forest hills|jackson heights|ridgewood queens|tribeca|west village|east village|greenwich village|chelsea|murray hill|upper manhattan|washington heights|inwood|yorkville|fidi|financial district|battery park|lower east side|soho)\b"
NJ_SUBS = {"njparents", "newjersey", "movingtonewjersey", "hoboken", "jerseycity", "maplewood", "morristown", "southorange", "westfieldnj", "summitnj", "montclairnj"}
NYC_SUBS = {"nyc", "asknyc", "nycapartments", "movingtonyc", "brooklyn", "astoria", "queens", "manhattan"}

KEEP_DAYS = 14

# ===== EDIT HERE: specific posts to never show (paste the t3_ id from posts.json) =====
HIDE = {
    "t3_1y37bj8",   # "Where would you live in NYC area as a single 30M making $250k?"
}
HIDE_TITLES = {"where would you live in nyc area as a single 30m making $250k?"}   # hides cross-posts of the same post
READ_REDDIT_DIRECTLY = False
UA = "BuyerRadar/1.0 (personal feed reader for one real estate agent; reads public RSS twice an hour)"
ATOM = "{http://www.w3.org/2005/Atom}"


def lists_for(title, group):
    t = title.lower()
    if re.search(NEVER, t):
        return []
    hits = [name for name, pats in RULES.items() if any(re.search(p, t) for p in pats)]
    if group in WHOLE_GROUP and WHOLE_GROUP[group] not in hits:
        hits.insert(0, WHOLE_GROUP[group])
    # In NYCapartments and FirstTimeHomeBuyer, the NJ list needs an NJ place in the title
    if group in ("NYCapartments", "FirstTimeHomeBuyer") and "nj" in hits and not re.search(r"\b" + NJ_PLACES + r"\b", t):
        hits.remove("nj")
    # Likewise, the NYC list needs an NYC place unless the group is all about NYC
    if group != "movingtonyc" and "nyc" in hits and not re.search(r"\b" + NYC_PLACES + r"\b", t):
        hits.remove("nyc")
    allowed = GROUP_LISTS.get(group, list(RULES))
    return [h for h in hits if h in allowed]


ELSEWHERE = r"\b(california|norcal|socal|bay area|los angeles|san diego|texas|austin|dallas|houston|florida|miami|tampa|orlando|chicago|illinois|seattle|bellevue|washington state|washington,? dc|d\.c\.|\bdc\b|colorado|denver|arizona|phoenix|ohio|georgia|atlanta|carolina|charlotte|raleigh|kansas|missouri|michigan|minnesota|wisconsin|indiana|tennessee|nashville|virginia|maryland|massachusetts|boston|pennsylvania|philly|philadelphia|pittsburgh|oregon|portland|utah|nevada|las vegas|canada|ontario|uk|london|australia)\b"


def keep_lists(title, sub, snippet=""):
    """Which of Josh's lists a post belongs in, or [] to skip it.
    The title decides the topic; the title or the preview text can show the place."""
    t = title.lower()
    x = (snippet or "").lower()
    s = (sub or "").lower()
    if re.search(NEVER, t):
        return []
    # Agents and landlords advertising themselves, not people looking for help
    if re.search(r"\b(i'?m|i am|we are|we're) an? (licensed |local |nj |new jersey |nyc )?(leasing agent|realtor|real estate agent|real estate broker|broker|agent|landlord|property manager)\b|\bleasing agent\b", t + " " + x):
        return []
    here = re.search(NJ_STRONG, t + " " + x) or re.search(NYC_STRONG, t + " " + x)
    if re.search(ELSEWHERE, t + " " + x) and not here:
        return []      # clearly about another place
    nj = bool(re.search(NJ_STRONG, t + " " + x)) or s in NJ_SUBS or (bool(re.search(NJ_MAYBE, t)) and s in NJ_SUBS)
    nyc = bool(re.search(NYC_STRONG, t + " " + x)) or s in NYC_SUBS
    hits = [name for name, pats in RULES.items() if any(re.search(p, t) for p in pats)]
    out = []
    if "nj" in hits and nj:
        out.append("nj")
    if "nyc" in hits and nyc and not (nj and not re.search(NYC_STRONG, t)):
        out.append("nyc")
    for name in ("agent", "sell"):
        if name in hits and (nj or nyc):
            out.append(name)
    # People LOOKING for a rental (not offering rooms, sublets or lease takeovers; NEVER already drops those)
    seeking = r"\b(looking for|iso|in search of|searching for|need|needs|help (me )?find(ing)?|hunting for|trying to find)\b.{0,40}\b(apartment|apt|1 ?br|2 ?br|3 ?br|one bedroom|two bedroom|studio|place to (live|rent)|rental|a place)\b"
    moving_need = r"\b(moving|relocating) to (nyc|new york|the city|nj|new jersey|hoboken|jersey city|brooklyn|queens|manhattan)\b.{0,80}\b(apartment|place|rental|lease|neighborhood)\b"
    offering = r"\b(available|for rent|for lease|take over my lease|i am offering|we have|move-in ready|dm for details|price drop|\$\d[\d,]* ?/ ?(mo|month))\b"
    if (nj or nyc) and (re.search(seeking, t + " " + x) or re.search(moving_need, t + " " + x)) and not re.search(offering, t):
        out.append("rent")
    # Comparing two of Josh's towns ("Maplewood vs South Orange") = actively choosing where to live
    towns = r"(hoboken|jersey city|weehawken|edgewater|west new york|montclair|upper montclair|glen ridge|bloomfield|maplewood|south orange|millburn|short hills|summit|chatham|madison|morristown|morris township|westfield|cranford|ridgewood|west orange|denville|boonton|livingston|caldwell|new providence|berkeley heights|mountain lakes|florham park|verona|nutley)"
    if re.search(r"\b" + towns + r"\b\s*(,\s*\w[\w ]*\s*)?(vs\.?|versus|or)\s+\b" + towns + r"\b", t) and "nj" not in out:
        out.insert(0, "nj")
    # In the "moving to" groups, a post still needs to be about finding a home, not dating, jobs or nightlife
    whole = {"movingtonewjersey": "nj", "movingtonyc": "nyc"}.get(s)
    buy_words = r"\b(buy|buying|bought|purchas\w*|mortgage|down payment|pre-?approv\w*|co-?ops?|condos?|closing costs?|realtor|real estate|broker|agent|house|home ?buyer|first home|sell|selling)\b"
    nj_words = buy_words[:-3] + r"|towns?|schools?|school district|commut\w*|relocat\w*|neighborhoods?|where (to|should we) live|family|kids)\b"
    home_words = nj_words[:-3] + r"|live|living|move|moving|relocat\w*|apartment|apt|area|budget|afford\w*|lease|rent|renting)\b"
    social = r"\b(dating|date|nightlife|bars?|clubs?|make friends|meet people|friends|singles|gym|restaurants?)\b"
    if whole and whole not in out and not re.search(social, t + " " + x[:80]):
        if re.search(home_words, t + " " + x):
            out.insert(0, whole)
    return out


# ===== EDIT HERE: topics for "What people are asking this week" (counted from all local posts, not just leads) =====
TOPICS = {
    "commute": r"\b(commut\w*|nj transit|path train|the path|penn station|midtown|train to)\b",
    "schools": r"\b(schools?|school district|kindergarten|pre-?k)\b",
    "taxes": r"\b(property tax\w*|taxes)\b",
    "towns": r"\b(which|what|best|good|favorite) (town|towns|neighborhood|neighborhoods|area)\b|\bwhere (to|should (i|we)) (live|move)\b",
    "coop": r"\b(co-?ops?|condos?|board (package|interview|approval))\b",
    "rentbuy": r"\b(rent (vs\.?|or|versus) buy|renting (vs\.?|or) buying|keep renting)\b",
    "afford": r"\b(afford\w*|budget|down payment|mortgage|interest rates?|closing costs?)\b",
    "selling": r"\b(selling|sell (my|our)|listing (my|our)|list (my|our))\b",
    "family": r"\b(kids?|children|baby|toddler|growing family)\b",
}
SEEN = []   # local posts seen this run, for topic counts


def note_topics(pid, title, sub, snippet, ts):
    t = (title + " " + snippet).lower()
    s = (sub or "").lower()
    here = re.search(NJ_STRONG, t) or re.search(NYC_STRONG, t) or s in NJ_SUBS or s in NYC_SUBS
    if not here or (re.search(ELSEWHERE, t) and not (re.search(NJ_STRONG, t) or re.search(NYC_STRONG, t))):
        return
    found = [k for k, rx in TOPICS.items() if re.search(rx, t)]
    SEEN.append({"id": pid, "t": ts, "topics": found})


def parse_feed(xml_text, group):
    root = ET.fromstring(xml_text)
    posts = []
    for e in root.findall(ATOM + "entry"):
        title = (e.findtext(ATOM + "title") or "").strip()
        link_el = e.find(ATOM + "link")
        url = link_el.get("href") if link_el is not None else ""
        when = e.findtext(ATOM + "published") or e.findtext(ATOM + "updated") or ""
        try:
            ts = int(datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
        author = (e.findtext(ATOM + "author/" + ATOM + "name") or "").replace("/u/", "")
        pid = (e.findtext(ATOM + "id") or url).strip()
        lists = lists_for(title, group)
        if lists and url:
            posts.append({"id": pid, "title": title, "sub": group, "url": url, "t": ts, "author": author, "lists": lists})
    return posts


def real_url(u):
    """Google Alerts links go through google.com/url?...&url=REAL; return REAL."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
    return q.get("url", [u])[0]


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    s = re.sub(r"\s*[-|]\s*Reddit\s*$", "", s, flags=re.I)  # drop " - Reddit"
    s = re.sub(r"\s*[:|\-–]\s*r/\w+\s*$", "", s)          # then drop " : r/newjersey"
    return re.sub(r"\s+", " ", s).strip()


def parse_alerts(xml_text, list_name):
    root = ET.fromstring(xml_text)
    posts = []
    for e in root.findall(ATOM + "entry"):
        link_el = e.find(ATOM + "link")
        url = real_url(link_el.get("href")) if link_el is not None else ""
        title = clean(e.findtext(ATOM + "title"))
        snippet = clean(e.findtext(ATOM + "content"))[:220]
        ts = 0
        for tag in ("published", "updated"):   # Google sometimes sends 1970 here; fall back to the next date, then to now
            try:
                ts = int(datetime.fromisoformat((e.findtext(ATOM + tag) or "").replace("Z", "+00:00")).timestamp())
            except ValueError:
                ts = 0
            if ts > 1_000_000_000:
                break
        if ts <= 1_000_000_000:
            ts = int(time.time())
        if not url or not title:
            continue
        m = re.search(r"reddit\.com/r/(\w+)/comments/(\w+)", url)
        sub = m.group(1) if m else ""
        note_topics(("t3_" + m.group(2)) if m else url, title, sub, snippet, ts)
        lists = keep_lists(title, sub, snippet)   # [] = skipped for now, but held so later rule changes can bring it back
        where = "r/" + sub if sub else urllib.parse.urlparse(url).netloc.replace("www.", "")
        pid = ("t3_" + m.group(2)) if m else url
        posts.append({"id": pid, "title": title, "sub": sub, "where": where, "url": url, "t": ts, "author": "", "lists": lists, "snippet": snippet})
    return posts


def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def fetch(group):
    req = urllib.request.Request(f"https://www.reddit.com/r/{group}/new/.rss?limit=50", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def main(out="posts.json"):
    try:
        old = json.load(open(out))
    except (OSError, ValueError):
        old = {"posts": []}
    by_id = {}
    # re-check older posts AND skipped ("held") posts with today's rules, so a rule change never loses a real post
    for p in old.get("held", []) + old.get("posts", []):
        p["lists"] = keep_lists(p["title"], p.get("sub", ""), p.get("snippet", ""))
        by_id[p["id"]] = p
    ok, errors = 0, []
    try:
        feeds = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts.json"))).get("feeds", [])
    except (OSError, ValueError):
        feeds = []
    for f in feeds:
        try:
            for p in parse_alerts(fetch_url(f["url"]), f["list"]):
                if p["id"] in by_id:
                    old_p = by_id[p["id"]]
                    p["lists"] = sorted(set(old_p["lists"]) | set(p["lists"])) if p["lists"] else []
                    p["t"] = old_p["t"]            # keep when it was first found
                    by_id[p["id"]] = p
                else:
                    by_id[p["id"]] = p
            ok += 1
        except Exception as e:
            errors.append(f"alert {f.get('list')}: {e}")
        time.sleep(2)
    for g in (GROUPS if READ_REDDIT_DIRECTLY else []):
        try:
            for p in parse_feed(fetch(g), g):
                by_id[p["id"]] = p
            ok += 1
        except Exception as e:  # one group failing shouldn't stop the rest
            errors.append(f"{g}: {e}")
        time.sleep(3)  # be gentle
    cutoff = time.time() - KEEP_DAYS * 86400
    recent = sorted((p for p in by_id.values() if p["t"] >= cutoff), key=lambda p: -p["t"])
    posts = [p for p in recent if p["lists"] and p["id"] not in HIDE and p["title"].strip().lower() not in HIDE_TITLES][:300]
    held = [p for p in recent if not p["lists"]][:600]
    # topics: remember what local posts were about for 7 days, then count
    seen = {s["id"]: s for s in old.get("seen", [])}
    for s in SEEN:
        seen.setdefault(s["id"], s)
    week = time.time() - 7 * 86400
    seen = [s for s in seen.values() if s["t"] >= week][-800:]
    counts = {}
    for s in seen:
        for k in s["topics"]:
            counts[k] = counts.get(k, 0) + 1
    topics = sorted(({"key": k, "count": c} for k, c in counts.items()), key=lambda x: -x["count"])
    day_ago = time.time() - 86400
    checked_week = len(seen)
    checked_today = sum(1 for s in seen if s["t"] >= day_ago)
    # Companies moving in: news check about once an hour (see moves.py)
    moves, moves_checked = old.get("moves", []), old.get("moves_checked", 0)
    CHECK_COMPANIES = False   # "Companies moving in" tab is off; set True to bring it back
    if CHECK_COMPANIES and time.time() - moves_checked > 55 * 60:
        try:
            from moves import update_moves
            moves, m_err = update_moves(moves)
            errors += m_err
            moves_checked = int(time.time())
        except Exception as e:
            errors.append(f"news: {e}")
    json.dump({"checked": int(time.time()), "groups_ok": ok, "errors": errors, "posts": posts,
               "moves": moves, "moves_checked": moves_checked,
               "topics": topics, "seen": seen, "held": held, "checked_week": checked_week, "checked_today": checked_today}, open(out, "w"), indent=0)
    print(f"{ok} feeds read, {len(posts)} posts kept, {checked_week} local posts checked this week, topics: {topics[:5]}")
    for e in errors:
        print("error:", e)
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main(*sys.argv[1:])
