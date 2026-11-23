"""Buyer Radar post checker.

Runs on GitHub every 30 minutes. Reads the public "newest posts" feed of a few
Reddit groups (the same feeds a news app like Feedly reads), keeps only posts
whose TITLE fits one of Josh's four lists, and saves them to posts.json.
The radar page then shows them under "New posts right now".

To change what counts as a match, edit the RULES section below.
"""
import json, os, re, sys, time, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ===== EDIT HERE: groups to read (only groups whose rules allow agents to help) =====
GROUPS = ["MovingtoNewJersey", "newjersey", "Hoboken", "jerseycity", "NYCapartments",
          "FirstTimeHomeBuyer", "movingtonyc"]

# ===== EDIT HERE: rules. A post matches a list when its title fits. =====
NJ_PLACES = r"(nj|new jersey|jersey|hoboken|jersey city|montclair|maplewood|south orange|summit|short hills|millburn|westfield|madison|chatham|morristown|morris county|cranford|bloomfield|denville|weehawken|edgewater|ridgewood|glen ridge|essex county|bergen|union county|suburbs?)"
NYC_PLACES = r"(nyc|new york|manhattan|brooklyn|queens|the city|astoria|park slope|upper west side|uws|upper east side|ues|harlem|lic|long island city)"
NEVER = r"(roommate|sublet|sublease|lease takeover|furniture|ticket|car insurance|auto insurance|\bcar\b|cars|dealer|vehicle|truck|parking|job|hiring|for rent:|\[for rent\]|giveaway|lost|found|scam)"

RULES = {
    # People moving to New Jersey
    "nj": [
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
KEEP_DAYS = 14
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


def fetch(group):
    req = urllib.request.Request(f"https://www.reddit.com/r/{group}/new/.rss?limit=50", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def main(out="posts.json"):
    try:
        old = json.load(open(out))
    except (OSError, ValueError):
        old = {"posts": []}
    by_id = {p["id"]: p for p in old.get("posts", [])}
    ok, errors = 0, []
    for g in GROUPS:
        try:
            for p in parse_feed(fetch(g), g):
                by_id[p["id"]] = p
            ok += 1
        except Exception as e:  # one group failing shouldn't stop the rest
            errors.append(f"{g}: {e}")
        time.sleep(3)  # be gentle
    cutoff = time.time() - KEEP_DAYS * 86400
    posts = sorted((p for p in by_id.values() if p["t"] >= cutoff), key=lambda p: -p["t"])[:300]
    json.dump({"checked": int(time.time()), "groups_ok": ok, "errors": errors, "posts": posts}, open(out, "w"), indent=0)
    print(f"{ok}/{len(GROUPS)} groups read, {len(posts)} posts kept")
    for e in errors:
        print("error:", e)
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main(*sys.argv[1:])
