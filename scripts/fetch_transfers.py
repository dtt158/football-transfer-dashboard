#!/usr/bin/env python3
"""Collect public football transfer items and write a static JSON feed."""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "transfers.json"
DEFAULT_ENTITY_CACHE = ROOT / "data" / "entity_cache.json"


LEAGUE_KEYWORDS = {
    "英超": [
        "premier league",
        "arsenal",
        "aston villa",
        "chelsea",
        "liverpool",
        "manchester city",
        "man city",
        "manchester united",
        "man united",
        "newcastle",
        "tottenham",
        "spurs",
        "west ham",
    ],
    "西甲": ["laliga", "la liga", "barcelona", "real madrid", "atletico", "sevilla", "valencia", "villarreal"],
    "意甲": ["serie a", "inter milan", "ac milan", "juventus", "napoli", "roma", "lazio", "atalanta"],
    "德甲": ["bundesliga", "bayern", "dortmund", "leverkusen", "leipzig", "stuttgart"],
    "法甲": ["ligue 1", "psg", "paris saint-germain", "marseille", "lyon", "monaco", "lille"],
    "葡超": ["liga portugal", "benfica", "porto", "sporting cp", "sporting lisbon", "braga"],
    "荷甲": ["eredivisie", "ajax", "psv", "feyenoord", "az alkmaar"],
    "沙特/其他": ["saudi", "al-hilal", "al nassr", "al-nassr", "al ahli", "al-ittihad", "mls"],
}

CLUB_ALIASES = {
    "Arsenal": ["arsenal"],
    "Aston Villa": ["aston villa"],
    "Atalanta": ["atalanta"],
    "Barcelona": ["barcelona", "barça", "barca"],
    "Bayern Munich": ["bayern", "bayern munich"],
    "Benfica": ["benfica"],
    "Borussia Dortmund": ["dortmund", "borussia dortmund"],
    "Brighton": ["brighton"],
    "Chelsea": ["chelsea"],
    "Fiorentina": ["fiorentina"],
    "Hearts": ["hearts"],
    "Inter Milan": ["inter milan", "inter"],
    "Juventus": ["juventus"],
    "Liverpool": ["liverpool"],
    "Manchester City": ["manchester city", "man city"],
    "Manchester United": ["manchester united", "man united"],
    "Napoli": ["napoli"],
    "Newcastle United": ["newcastle", "newcastle united"],
    "PSG": ["psg", "paris saint-germain"],
    "Porto": ["porto"],
    "Real Madrid": ["real madrid"],
    "Roma": ["roma"],
    "Sassuolo": ["sassuolo"],
    "Sporting CP": ["sporting cp", "sporting lisbon"],
    "Tottenham": ["tottenham", "spurs"],
    "West Ham": ["west ham"],
    "York City": ["york city"],
}

WIKI_TITLE_OVERRIDES = {
    "Fiorentina": {"zh": "佛罗伦萨足球俱乐部", "en": "ACF Fiorentina"},
    "Sassuolo": {"zh": "萨索罗足球俱乐部", "en": "US Sassuolo Calcio"},
    "Atalanta": {"zh": "亚特兰大贝加莫足球俱乐部", "en": "Atalanta BC"},
    "Inter Milan": {"zh": "国际米兰足球俱乐部", "en": "Inter Milan"},
    "AC Milan": {"zh": "AC米兰", "en": "AC Milan"},
    "Juventus": {"zh": "尤文图斯足球俱乐部", "en": "Juventus FC"},
    "Napoli": {"zh": "那不勒斯足球俱乐部", "en": "SSC Napoli"},
    "Roma": {"zh": "罗马体育俱乐部", "en": "AS Roma"},
    "Hearts": {"zh": "哈茨足球俱乐部", "en": "Heart of Midlothian F.C."},
    "York City": {"zh": "约克城足球俱乐部", "en": "York City F.C."},
}


STATUS_PATTERNS = [
    ("official", ["official", "confirmed", "announced", "signs", "signed", "complete", "joins"]),
    ("advanced", ["here we go", "medical", "agreement", "agreed", "set to sign", "close to joining"]),
    ("negotiating", ["talks", "bid", "offer", "negotiating", "interest", "target", "approach"]),
]


SOURCE_REGISTRY = [
    {
        "name": "BBC Football",
        "url": "https://feeds.bbci.co.uk/sport/football/rss.xml",
        "grade": "B",
        "description": "英国主流媒体，适合追踪英超和欧洲转会新闻；转会确认仍以官方公告优先。",
        "kind": "rss",
        "language": "en",
        "region": "UK",
        "focus": "英超、英国足球、欧洲转会",
    },
    {
        "name": "ESPN Soccer",
        "url": "https://www.espn.com/espn/rss/soccer/news",
        "grade": "B",
        "description": "国际主流体育媒体，覆盖欧洲主流联赛和转会动态。",
        "kind": "rss",
        "language": "en",
        "region": "Global",
        "focus": "国际足球、欧洲主流联赛",
    },
    {
        "name": "The Guardian Football",
        "url": "https://www.theguardian.com/football/rss",
        "grade": "B",
        "description": "英国主流媒体，覆盖英超、欧洲足球和转会窗专题；适合作为多来源交叉验证。",
        "kind": "rss",
        "language": "en",
        "region": "UK",
        "focus": "英超、欧洲足球、转会窗",
    },
    {
        "name": "Football Italia",
        "url": "https://football-italia.net/feed/",
        "grade": "B",
        "description": "意大利足球英文媒体，适合追踪意甲、意大利俱乐部和意大利球员相关转会。",
        "kind": "rss",
        "language": "en",
        "region": "Italy",
        "focus": "意甲、意大利足球",
    },
    {
        "name": "Marca Fichajes",
        "url": "https://e00-marca.uecdn.es/rss/futbol/mercado-fichajes.xml",
        "grade": "B",
        "description": "西班牙主流体育媒体 Marca 的转会市场 RSS，适合追踪西甲和西语区转会新闻。",
        "kind": "rss",
        "language": "es",
        "region": "Spain",
        "focus": "西甲、转会市场",
    },
    {
        "name": "AS Primera",
        "url": "https://as.com/rss/futbol/primera.xml",
        "grade": "B",
        "description": "西班牙主流体育媒体 AS 的西甲 RSS，用于补充西甲和皇马、巴萨、马竞相关动态。",
        "kind": "rss",
        "language": "es",
        "region": "Spain",
        "focus": "西甲、皇马、巴萨、马竞",
    },
    {
        "name": "Get French Football News",
        "url": "https://www.getfootballnewsfrance.com/feed/",
        "grade": "B",
        "description": "法国足球英文专业站，适合追踪法甲、法国球员和法国媒体转会线索。",
        "kind": "rss",
        "language": "en",
        "region": "France",
        "focus": "法甲、法国球员",
    },
    {
        "name": "Get German Football News",
        "url": "https://www.getfootballnewsgermany.com/feed/",
        "grade": "B",
        "description": "德国足球英文专业站，适合追踪德甲和德国俱乐部相关转会。",
        "kind": "rss",
        "language": "en",
        "region": "Germany",
        "focus": "德甲、德国足球",
    },
    {
        "name": "Get Italian Football News",
        "url": "https://www.getfootballnewsitaly.com/feed/",
        "grade": "B",
        "description": "意大利足球英文专业站，补充意甲和意大利媒体线索。",
        "kind": "rss",
        "language": "en",
        "region": "Italy",
        "focus": "意甲、意大利足球",
    },
    {
        "name": "Get Spanish Football News",
        "url": "https://www.getfootballnewsspain.com/feed/",
        "grade": "B",
        "description": "西班牙足球英文专业站，补充西甲和西班牙媒体线索。",
        "kind": "rss",
        "language": "en",
        "region": "Spain",
        "focus": "西甲、西班牙足球",
    },
    {
        "name": "Transfermarkt",
        "url": "https://www.transfermarkt.co.uk/rss/news",
        "grade": "B",
        "description": "转会、身价和传闻聚合参考源；当前自动采集默认关闭，避免其反爬限制影响每小时更新。",
        "kind": "reference",
        "language": "en",
        "region": "Global",
        "focus": "身价、转会记录、传闻参考",
    },
    {
        "name": "Fabrizio Romano",
        "url": "https://x.com/FabrizioRomano",
        "grade": "A",
        "description": "高可信转会记者；首版作为社交信源目录展示，不自动抓取 X。",
        "kind": "social",
        "language": "en",
        "region": "Global",
        "focus": "全球转会、Here we go",
    },
    {
        "name": "David Ornstein",
        "url": "https://x.com/David_Ornstein",
        "grade": "A",
        "description": "The Athletic 记者，英超和英国俱乐部转会可信度高；社交目录源。",
        "kind": "social",
        "language": "en",
        "region": "UK",
        "focus": "英超、英国俱乐部",
    },
    {
        "name": "Matteo Moretto",
        "url": "https://x.com/MatteMoretto",
        "grade": "A",
        "description": "西班牙和意大利转会记者，西甲、意甲线索较强；社交目录源。",
        "kind": "social",
        "language": "es",
        "region": "Spain/Italy",
        "focus": "西甲、意甲",
    },
    {
        "name": "Gianluca Di Marzio",
        "url": "https://x.com/DiMarzio",
        "grade": "A",
        "description": "意大利转会记者，意甲和意大利俱乐部信息源；社交目录源。",
        "kind": "social",
        "language": "it",
        "region": "Italy",
        "focus": "意甲、意大利足球",
    },
    {
        "name": "Florian Plettenberg",
        "url": "https://x.com/Plettigoal",
        "grade": "B",
        "description": "德国 Sky 记者，德甲和德国俱乐部转会线索；社交目录源。",
        "kind": "social",
        "language": "de",
        "region": "Germany",
        "focus": "德甲、德国足球",
    },
    {
        "name": "Santi Aouna",
        "url": "https://x.com/Santi_J_FM",
        "grade": "B",
        "description": "Foot Mercato 记者，法甲、法国球员和欧洲转会线索；社交目录源。",
        "kind": "social",
        "language": "fr",
        "region": "France",
        "focus": "法甲、法国球员",
    },
    {
        "name": "Fabrice Hawkins",
        "url": "https://x.com/FabriceHawkins",
        "grade": "B",
        "description": "RMC Sport 记者，法国足球和法甲转会线索；社交目录源。",
        "kind": "social",
        "language": "fr",
        "region": "France",
        "focus": "法甲、法国足球",
    },
    {
        "name": "Ben Jacobs",
        "url": "https://x.com/JacobsBen",
        "grade": "B",
        "description": "英超、中东资本和部分欧洲转会线索；社交目录源，需与 A/S 来源交叉验证。",
        "kind": "social",
        "language": "en",
        "region": "UK/Global",
        "focus": "英超、沙特/中东相关转会",
    },
]


GRADE_BASE = {"S": 95, "A": 82, "B": 66, "C": 42}
TRANSFER_TERMS = [
    "transfer",
    "transfers",
    "sign",
    "signs",
    "signed",
    "joins",
    "deal",
    "bid",
    "offer",
    "rumour",
    "rumor",
    "target",
    "medical",
    "contract",
    "loan",
    "fichaje",
    "fichajes",
    "traspaso",
    "mercado",
    "cedido",
    "cesion",
    "cesión",
    "rumor",
    "rumores",
    "calciomercato",
    "mercato",
    "accordo",
    "prestito",
    "trattativa",
    "transfert",
    "transferts",
    "pret",
    "prêt",
    "rumeur",
    "rumeurs",
    "verpflichtet",
    "wechsel",
    "leihe",
    "angebot",
]

EXCLUDE_TERMS = [
    "podcast",
    " pod ",
    "joins the pod",
    "quiz",
    "match report",
    "preview",
    "ratings",
    "talking points",
]

TRANSLATION_CACHE: dict[str, str] = {}
WIKI_CACHE: dict[str, dict[str, str]] = {}

GLOSSARY_TRANSLATIONS = [
    ("is set to become", "即将成为"),
    ("are expecting", "预计"),
    ("after rejecting", "在拒绝后"),
    ("initial bid", "首次报价"),
    ("increased offer", "提高后的报价"),
    ("agreed a deal", "达成交易"),
    ("agreed", "达成一致"),
    ("deal", "交易"),
    ("bid", "报价"),
    ("offer", "报价"),
    ("target", "目标"),
    ("transfer", "转会"),
    ("transfers", "转会"),
    ("signed", "签下"),
    ("signing", "签约"),
    ("sign", "签约"),
    ("joins", "加盟"),
    ("joining", "加盟"),
    ("medical", "体检"),
    ("contract", "合同"),
    ("loan", "租借"),
    ("defender", "后卫"),
    ("midfielder", "中场"),
    ("striker", "前锋"),
    ("goalkeeper", "门将"),
    ("forward", "前锋"),
    ("club", "俱乐部"),
    ("manager", "主教练"),
    ("summer", "夏窗"),
    ("winter", "冬窗"),
]


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    grade: str
    description: str
    kind: str = "rss"
    language: str = "en"
    region: str = "Global"
    focus: str = "Football transfers"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--entity-cache", type=Path, default=DEFAULT_ENTITY_CACHE)
    parser.add_argument("--keep-existing-on-empty", action="store_true", default=True)
    args = parser.parse_args()

    load_entity_cache(args.entity_cache)
    sources = [Source(**source) for source in SOURCE_REGISTRY]
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    for source in sources:
        if source.kind != "rss":
            continue
        try:
            items.extend(fetch_source(source))
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            errors.append(f"{source.name}: {exc}")

    transfers = merge_items(items)
    if not transfers and args.keep_existing_on_empty and args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        existing["generated_at"] = utc_now()
        existing["errors"] = errors
        write_json(args.output, existing)
        print(f"No fresh items found; kept existing data at {args.output}")
        return 0

    payload = {
        "generated_at": utc_now(),
        "sources": [
            {
                "name": source.name,
                "grade": source.grade,
                "description": source.description,
                "url": source.url,
                "kind": source.kind,
                "language": source.language,
                "region": source.region,
                "focus": source.focus,
            }
            for source in sources
        ],
        "errors": errors,
        "transfers": transfers,
    }
    write_json(args.output, payload)
    save_entity_cache(args.entity_cache)
    print(f"Wrote {len(transfers)} transfer items to {args.output}")
    if errors:
        print("Fetch warnings:", "; ".join(errors), file=sys.stderr)
    return 0


def load_entity_cache(path: Path) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    records = payload.get("entities", payload)
    if not isinstance(records, dict):
        return
    for key, value in records.items():
        if isinstance(key, str) and isinstance(value, dict):
            WIKI_CACHE[key] = {str(k): str(v) for k, v in value.items() if v is not None}


def save_entity_cache(path: Path) -> None:
    payload = {
        "generated_at": utc_now(),
        "entities": dict(sorted(WIKI_CACHE.items())),
    }
    write_json(path, payload)


def fetch_source(source: Source) -> list[dict[str, Any]]:
    raw = read_url(source.url)
    root = ET.fromstring(raw)
    parsed: list[dict[str, Any]] = []

    for element in root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = text_from(element, "title")
        description = clean_html(text_from(element, "description") or text_from(element, "summary"))
        link = text_from(element, "link")
        if not link:
            link_el = element.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.attrib.get("href", "") if link_el is not None else ""
        published = normalise_date(text_from(element, "pubDate") or text_from(element, "updated") or text_from(element, "published"))
        combined = f"{title} {description}".strip()

        if not is_transfer_related(combined):
            continue

        parsed.append(classify_item(combined, title, description, link, published, source))

    return parsed


def classify_item(combined: str, title: str, description: str, link: str, published: str, source: Source) -> dict[str, Any]:
    lowered = combined.lower()
    status = "rumour"
    for candidate, terms in STATUS_PATTERNS:
        if any(term in lowered for term in terms):
            status = candidate
            break

    league = "其他"
    for candidate, terms in LEAGUE_KEYWORDS.items():
        if any(term in lowered for term in terms):
            league = candidate
            break

    player = guess_player(title)
    clubs = guess_clubs(combined)
    credibility = credibility_score(source.grade, status)
    heat = heat_score(source.grade, status, combined, published)
    collected_at = utc_now()

    entities = build_entities(player, clubs, league)
    preserve_terms = [entity["name"] for entity in entities if entity.get("type") in {"player", "club"}]
    summary = summarise(description or title)
    summary_zh, translation_provider = translate_summary(summary, source.language, preserve_terms)
    title_zh, title_translation_provider = translate_summary(title, source.language, preserve_terms)

    return {
        "id": stable_id(title, link),
        "title": title,
        "title_zh": title_zh,
        "title_translation_provider": title_translation_provider,
        "player": player,
        "from_club": clubs[0],
        "to_club": clubs[1],
        "league": league,
        "status": status,
        "summary": summary,
        "summary_zh": summary_zh,
        "translation_provider": translation_provider,
        "fee": guess_fee(combined),
        "contract": "",
        "reported_at": published or collected_at,
        "collected_at": collected_at,
        "credibility_score": credibility,
        "heat_score": heat,
        "entities": entities,
        "tags": tags_for(status, combined),
        "sources": [
            {
                "name": source.name,
                "url": link or source.url,
                "grade": source.grade,
                "published_at": published or collected_at,
            }
        ],
    }


def merge_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        key = normalise_key(item["player"], item["to_club"])
        if key not in merged:
            merged[key] = item
            continue
        current = merged[key]
        current["sources"].extend(item["sources"])
        current["credibility_score"] = min(100, max(current["credibility_score"], item["credibility_score"]) + 5)
        current["heat_score"] = min(100, max(current["heat_score"], item["heat_score"]) + 8)
        current["tags"] = sorted(set(current.get("tags", []) + item.get("tags", [])))
        if status_rank(item["status"]) > status_rank(current["status"]):
            current["status"] = item["status"]
        if DateOrLow(item["reported_at"]) > DateOrLow(current["reported_at"]):
            current["title"] = item.get("title", current.get("title", ""))
            current["title_zh"] = item.get("title_zh", current.get("title_zh", ""))
            current["summary"] = item["summary"]
            current["summary_zh"] = item.get("summary_zh", current.get("summary_zh", ""))
            current["reported_at"] = item["reported_at"]
        current["entities"] = merge_entities(current.get("entities", []), item.get("entities", []))

    return sorted(merged.values(), key=lambda item: (item["heat_score"], item["credibility_score"]), reverse=True)


def read_url(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 transfer-dashboard/1.0 (+https://github.com/)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def text_from(element: ET.Element, tag: str) -> str:
    found = element.find(tag)
    if found is None:
        found = element.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
    return (found.text or "").strip() if found is not None else ""


def clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def is_transfer_related(text: str) -> bool:
    lowered = text.lower()
    if any(term in lowered for term in EXCLUDE_TERMS):
        return False
    return any(has_term(lowered, term) for term in TRANSFER_TERMS)


def has_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text, re.I) is not None


def guess_player(title: str) -> str:
    title = re.sub(r"\s+-\s+.*$", "", title).strip()
    patterns = [
        r"\bfor\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+(?:[- ][A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+){0,3})\b",
        r"\bsign(?:s|ed|ing)?\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+(?:[- ][A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+){0,3})\b",
        r"\bjoins?\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+(?:[- ][A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+){0,3})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            candidate = match.group(1).strip()
            if looks_like_person_name(candidate):
                return candidate
    for separator in [":", " - ", " | "]:
        if separator in title:
            candidate = title.split(separator, 1)[0].strip()
            if 3 <= len(candidate) <= 80:
                return candidate
    return title[:80] or "未知球员"


def guess_clubs(text: str) -> tuple[str, str]:
    clubs = []
    lowered = text.lower()
    for club, aliases in CLUB_ALIASES.items():
        if any(has_term(lowered, alias) for alias in aliases):
            clubs.append(club)
    for league_terms in LEAGUE_KEYWORDS.values():
        for term in league_terms:
            if has_term(lowered, term):
                clubs.append(title_case_club(term))
    clubs = list(dict.fromkeys(clubs))
    if len(clubs) >= 2:
        return clubs[0], clubs[1]
    if len(clubs) == 1:
        return "未知", clubs[0]
    return "未知", "未知"


def build_entities(player: str, clubs: tuple[str, str], league: str) -> list[dict[str, str]]:
    entities: list[dict[str, str]] = []
    if looks_like_person_name(player):
        entities.append(entity_record(player, "player", f"{player}：新闻中提到的球员或转会相关人物。"))
    for club in clubs:
        if club and club != "未知":
            entities.append(entity_record(club, "club", f"{club}：新闻中提到的俱乐部或目标球队。"))
    if league and league != "其他":
        entities.append(entity_record(league, "league", f"{league}：该条新闻归类到的联赛或地区。"))
    return merge_entities([], entities)


def entity_record(name: str, kind: str, description: str) -> dict[str, str]:
    wiki = fetch_wiki_entity(name, kind)
    record = {
        "name": name,
        "type": kind,
        "description": wiki.get("description") or description,
        "wiki_url": wiki.get("wiki_url") or f"https://zh.wikipedia.org/w/index.php?search={urllib.parse.quote(name)}",
        "search_url": f"https://www.google.com/search?q={urllib.parse.quote(name + ' football transfer')}",
    }
    if wiki.get("image_url"):
        record["image_url"] = wiki["image_url"]
    if wiki.get("wiki_title"):
        record["wiki_title"] = wiki["wiki_title"]
    return record


def fetch_wiki_entity(name: str, kind: str) -> dict[str, str]:
    cache_key = f"{kind}|{name}"
    if cache_key in WIKI_CACHE:
        return WIKI_CACHE[cache_key]
    if name in WIKI_TITLE_OVERRIDES:
        for language, title in WIKI_TITLE_OVERRIDES[name].items():
            result = query_wikipedia_title(title, language)
            if result:
                WIKI_CACHE[cache_key] = result
                return result
    search_name = wiki_search_name(name, kind)
    for language in ["zh", "en"]:
        result = query_wikipedia(search_name, language)
        if result and wiki_result_matches_kind(result, kind, name):
            WIKI_CACHE[cache_key] = result
            return result
    if search_name != name:
        for language in ["zh", "en"]:
            result = query_wikipedia(name, language)
            if result and wiki_result_matches_kind(result, kind, name):
                WIKI_CACHE[cache_key] = result
                return result
    WIKI_CACHE[cache_key] = {}
    return {}


def wiki_search_name(name: str, kind: str) -> str:
    if kind == "club":
        return f"{name} football club"
    if kind == "player":
        return f"{name} footballer"
    if kind == "league":
        return f"{name} football league"
    return name


def wiki_result_matches_kind(result: dict[str, str], kind: str, name: str) -> bool:
    text = f"{result.get('wiki_title', '')} {result.get('description', '')}".lower()
    aliases = CLUB_ALIASES.get(name, [name.lower()]) if kind == "club" else [name.lower()]
    has_name = any(alias.lower() in text for alias in aliases)
    if kind == "club":
        has_club_context = any(term in text for term in ["football club", "association football", "足球", "俱乐部", "俱樂部"])
        return has_name and has_club_context
    if kind == "player":
        return has_name and any(term in text for term in ["footballer", "association football", "足球运动员", "足球運動員"])
    if kind == "league":
        return any(term in text for term in ["league", "football", "联赛", "聯賽"])
    return True


def query_wikipedia_title(title: str, language: str) -> dict[str, str]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "extracts|pageimages|info",
            "exintro": 1,
            "explaintext": 1,
            "piprop": "thumbnail",
            "pithumbsize": 360,
            "inprop": "url",
            "redirects": 1,
        }
    )
    return query_wikipedia_api(f"https://{language}.wikipedia.org/w/api.php?{params}")


def query_wikipedia(name: str, language: str) -> dict[str, str]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": name,
            "gsrlimit": 1,
            "prop": "extracts|pageimages|info",
            "exintro": 1,
            "explaintext": 1,
            "piprop": "thumbnail",
            "pithumbsize": 360,
            "inprop": "url",
            "redirects": 1,
        }
    )
    return query_wikipedia_api(f"https://{language}.wikipedia.org/w/api.php?{params}")


def query_wikipedia_api(url: str) -> dict[str, str]:
    payload: dict[str, Any] = {}
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "transfer-dashboard/1.0"})
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
            if attempt == 2:
                return {}
            sleep(0.4 * (attempt + 1))

    pages = payload.get("query", {}).get("pages", {})
    if not pages:
        return {}
    page = next(iter(pages.values()))
    extract = clean_html(str(page.get("extract", ""))).strip()
    if not extract:
        return {}
    return {
        "description": first_paragraph(extract),
        "image_url": page.get("thumbnail", {}).get("source", ""),
        "wiki_url": page.get("fullurl", ""),
        "wiki_title": page.get("title", ""),
    }


def first_paragraph(text: str) -> str:
    paragraph = text.split("\n", 1)[0].strip()
    if len(paragraph) > 420:
        paragraph = paragraph[:420].rsplit(" ", 1)[0] + "..."
    return paragraph


def merge_entities(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for entity in existing + incoming:
        name = entity.get("name", "").strip()
        if name:
            merged[name.lower()] = entity
    return list(merged.values())


def looks_like_person_name(value: str) -> bool:
    if not value or len(value) > 48:
        return False
    lowered = value.lower()
    blocked = {
        "official",
        "liverpool",
        "manchester",
        "real madrid",
        "barcelona",
        "arsenal",
        "chelsea",
        "transfer",
        "transfers",
        "offer",
        "offers",
        "accept",
        "accepts",
        "confirm",
        "confirms",
        "appointment",
        "coach",
        "head",
        "city",
        "why",
        "how",
        "what",
    }
    if any(word in lowered for word in blocked):
        return False
    words = re.findall(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+", value)
    return 2 <= len(words) <= 4


def title_case_club(value: str) -> str:
    special = {
        "psg": "PSG",
        "mls": "MLS",
        "laliga": "LaLiga",
        "la liga": "LaLiga",
    }
    return special.get(value, value.title())


def credibility_score(grade: str, status: str) -> int:
    score = GRADE_BASE.get(grade, 40)
    if status == "official":
        score += 12
    elif status == "advanced":
        score += 8
    elif status == "rumour":
        score -= 8
    return max(0, min(100, score))


def heat_score(grade: str, status: str, text: str, published: str) -> int:
    score = GRADE_BASE.get(grade, 40) - 10
    lowered = text.lower()
    if status == "official":
        score += 14
    elif status == "advanced":
        score += 10
    if any(term in lowered for term in ["million", "£", "€", "$", "record", "star", "striker"]):
        score += 8
    age_hours = hours_since(published)
    if age_hours <= 6:
        score += 12
    elif age_hours <= 24:
        score += 8
    elif age_hours <= 72:
        score += 3
    else:
        score -= 6
    return max(0, min(100, score))


def hours_since(value: str) -> float:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 9999
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def guess_fee(text: str) -> str:
    match = re.search(r"([£€$]\s?\d+(?:\.\d+)?\s?(?:m|million|bn|billion))", text, re.I)
    return match.group(1) if match else ""


def tags_for(status: str, text: str) -> list[str]:
    tags = [status]
    lowered = text.lower()
    for term in ["medical", "loan", "contract", "bid", "release clause", "free transfer"]:
        if term in lowered:
            tags.append(term.replace(" ", "-"))
    return sorted(set(tags))


def summarise(text: str) -> str:
    cleaned = clean_html(text)
    return cleaned[:260] + ("..." if len(cleaned) > 260 else "")


def translate_summary(text: str, source_language: str = "en", preserve_terms: list[str] | None = None) -> tuple[str, str]:
    if not text:
        return "", "empty"
    if has_cjk(text):
        return text, "original"
    preserve_terms = preserve_terms or []
    cache_key = f"{source_language}|{text}|{'|'.join(sorted(preserve_terms))}"
    if cache_key in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[cache_key], "cache"

    protected_text, placeholders = protect_terms(text, preserve_terms)
    translated = translate_with_mymemory(protected_text, source_language)
    if translated:
        restored = restore_terms(translated, placeholders)
        TRANSLATION_CACHE[cache_key] = restored
        return restored, "mymemory"

    fallback = restore_terms(glossary_translate(protected_text), placeholders)
    TRANSLATION_CACHE[cache_key] = fallback
    return fallback, "glossary"


def protect_terms(text: str, terms: list[str]) -> tuple[str, dict[str, str]]:
    protected = text
    placeholders: dict[str, str] = {}
    for index, term in enumerate(sorted(set(terms), key=len, reverse=True)):
        if not term or term == "未知":
            continue
        placeholder = f"ZXQENTITY{index}ZXQ"
        protected = re.sub(re.escape(term), placeholder, protected)
        placeholders[placeholder] = term
    return protected, placeholders


def restore_terms(text: str, placeholders: dict[str, str]) -> str:
    restored = text
    for placeholder, term in placeholders.items():
        restored = restored.replace(placeholder, term)
        restored = restored.replace(placeholder.lower(), term)
        restored = restored.replace(placeholder.title(), term)
    return restored


def translate_with_mymemory(text: str, source_language: str) -> str:
    lang = source_language if source_language in {"en", "es", "it", "fr", "de", "pt", "nl"} else "en"
    params = urllib.parse.urlencode({"q": text[:500], "langpair": f"{lang}|zh-CN"})
    url = f"https://api.mymemory.translated.net/get?{params}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "transfer-dashboard/1.0"})
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        return ""

    translated = payload.get("responseData", {}).get("translatedText", "")
    translated = clean_html(str(translated)).strip()
    if not translated or translated.lower() == text.lower():
        return ""
    return translated


def glossary_translate(text: str) -> str:
    translated = text
    for english, chinese in GLOSSARY_TRANSLATIONS:
        translated = re.sub(rf"\b{re.escape(english)}\b", chinese, translated, flags=re.I)
    return translated


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def normalise_date(value: str) -> str:
    if not value:
        return ""
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(title: str, link: str) -> str:
    return hashlib.sha1(f"{title}|{link}".encode("utf-8")).hexdigest()[:16]


def normalise_key(player: str, to_club: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{player}-{to_club}".lower()).strip("-")


def status_rank(status: str) -> int:
    return {"expired": 0, "rumour": 1, "negotiating": 2, "advanced": 3, "official": 4}.get(status, 1)


def DateOrLow(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
