#!/usr/bin/env python3
"""Collect public football transfer items and write a static JSON feed."""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
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
DEFAULT_TRANSLATION_CACHE = ROOT / "data" / "translation_cache.json"
DEFAULT_PLAYER_CACHE = ROOT / "data" / "player_cache.json"


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

MAX_GENERAL_ITEMS = 60
MAX_GENERAL_PER_SOURCE = 6
MAX_ONLINE_TRANSLATION_MISSES = 16
MAX_WIKI_MISSES = 24
MAX_OPENAI_TRANSLATION_ITEMS = 160
OPENAI_TRANSLATION_BATCH_SIZE = 20

TRANSLATION_CACHE: dict[str, str] = {}
WIKI_CACHE: dict[str, dict[str, str]] = {}
PLAYER_CACHE: dict[str, dict[str, Any]] = {}
RUN_DIAGNOSTICS: dict[str, Any] = {}
ONLINE_TRANSLATION_MISSES = 0
WIKI_MISSES = 0

_PERSON_NAME_SKIP = frozenset({
    # grammar words
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "but",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "will", "would", "could", "should", "may", "might",
    # football structure
    "premier", "league", "serie", "bundesliga", "ligue", "laliga", "copa",
    "champions", "europa", "conference", "nations", "world", "cup",
    "fc", "united", "city", "town", "rovers", "albion", "athletic",
    # action/state words common in headlines
    "transfer", "loan", "deal", "bid", "offer", "sign", "signs", "signed",
    "joins", "joined", "move", "moves", "moved", "target", "targets",
    "contract", "fee", "million", "summer", "winter", "window", "season",
    "football", "soccer", "official", "confirmed", "breaking", "exclusive",
    "new", "old", "top", "big", "first", "second", "third",
    "report", "reports", "here", "we", "go",
    "why", "how", "what", "who", "when", "where",
    "english", "spanish", "italian", "german", "french", "portuguese",
    "international", "national", "domestic", "european",
    # days / months
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
})

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
    parser.add_argument("--translation-cache", type=Path, default=DEFAULT_TRANSLATION_CACHE)
    parser.add_argument("--player-cache", type=Path, default=DEFAULT_PLAYER_CACHE)
    parser.add_argument("--keep-existing-on-empty", action="store_true", default=True)
    args = parser.parse_args()

    load_entity_cache(args.entity_cache)
    load_translation_cache(args.translation_cache)
    load_player_cache(args.player_cache)
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
    apply_openai_translations(transfers)
    if not transfers and args.keep_existing_on_empty and args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        existing["generated_at"] = utc_now()
        existing["errors"] = errors
        write_json(args.output, existing)
        print(f"No fresh items found; kept existing data at {args.output}")
        return 0

    payload = {
        "generated_at": utc_now(),
        "diagnostics": RUN_DIAGNOSTICS,
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
    save_translation_cache(args.translation_cache)
    save_player_cache(args.player_cache)
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
            WIKI_CACHE[key] = normalise_cached_wiki_record({str(k): str(v) for k, v in value.items() if v is not None})


def normalise_cached_wiki_record(record: dict[str, str]) -> dict[str, str]:
    if not record:
        return record
    url = record.get("wiki_url", "")
    if not record.get("source_language"):
        record["source_language"] = "zh" if "zh.wikipedia.org" in url else "en" if "en.wikipedia.org" in url else "unknown"
    if not record.get("wiki_variant"):
        record["wiki_variant"] = "legacy-zh" if record["source_language"] == "zh" else "legacy-en"
    return record


def save_entity_cache(path: Path) -> None:
    payload = {
        "generated_at": utc_now(),
        "entities": dict(sorted(WIKI_CACHE.items())),
    }
    write_json(path, payload)


def load_translation_cache(path: Path) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    records = payload.get("translations", payload)
    if not isinstance(records, dict):
        return
    for key, value in records.items():
        if isinstance(key, str) and isinstance(value, str):
            TRANSLATION_CACHE[key] = value


def save_translation_cache(path: Path) -> None:
    payload = {
        "generated_at": utc_now(),
        "translations": dict(sorted(TRANSLATION_CACHE.items())),
    }
    write_json(path, payload)


def load_player_cache(path: Path) -> None:
    seed_player_cache()
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    records = payload.get("players", payload)
    if not isinstance(records, dict):
        return
    for key, value in records.items():
        if isinstance(key, str) and isinstance(value, dict):
            PLAYER_CACHE[key] = value
    seed_player_cache()


def save_player_cache(path: Path) -> None:
    payload = {
        "generated_at": utc_now(),
        "players": dict(sorted(PLAYER_CACHE.items())),
    }
    write_json(path, payload)


def seed_player_cache() -> None:
    PLAYER_CACHE.setdefault(
        "Erling Haaland",
        {
            "name": "Erling Haaland",
            "aliases": ["Erling Haaland", "Haaland", "Erling Braut Haaland", "哈兰德", "哈蘭德"],
            "zh_names": ["哈兰德", "哈蘭德"],
            "wikidata_id": "Q529207",
            "wiki_url": "https://en.wikipedia.org/wiki/Erling_Haaland",
            "description": "Erling Haaland：挪威职业足球运动员，司职前锋。",
        },
    )


def fetch_source(source: Source) -> list[dict[str, Any]]:
    raw = read_url(source.url)
    root = ET.fromstring(raw)
    parsed: list[dict[str, Any]] = []
    general_count = 0

    for element in root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = text_from(element, "title")
        description = clean_html(text_from(element, "description") or text_from(element, "summary"))
        link = text_from(element, "link")
        if not link:
            link_el = element.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.attrib.get("href", "") if link_el is not None else ""
        published = normalise_date(text_from(element, "pubDate") or text_from(element, "updated") or text_from(element, "published"))
        combined = f"{title} {description}".strip()

        category = classify_news_category(combined)
        if category == "ignore":
            continue
        if category == "general":
            if general_count >= MAX_GENERAL_PER_SOURCE:
                continue
            general_count += 1

        parsed.append(classify_item(combined, title, description, link, published, source, category))

    return parsed


def classify_item(combined: str, title: str, description: str, link: str, published: str, source: Source, category: str) -> dict[str, Any]:
    lowered = combined.lower()
    status = "rumour" if category == "transfer" else "general"
    for candidate, terms in STATUS_PATTERNS:
        if category == "transfer" and any(term in lowered for term in terms):
            status = candidate
            break

    league = "其他"
    for candidate, terms in LEAGUE_KEYWORDS.items():
        if any(term in lowered for term in terms):
            league = candidate
            break

    player = guess_player(title, combined)
    clubs = guess_clubs(combined)
    credibility = credibility_score(source.grade, status)
    heat = heat_score(source.grade, status, combined, published)
    collected_at = utc_now()

    entities = build_entities(player, clubs, league, combined)
    preserve_terms = [entity["name"] for entity in entities if entity.get("type") in {"player", "club"}]
    preserve_terms += extract_person_names(combined)
    preserve_terms = list(dict.fromkeys(preserve_terms))
    summary = summarise(description or title)
    allow_online_translation = category == "transfer"
    summary_zh, translation_provider = translate_summary(summary, source.language, preserve_terms, allow_online_translation)
    title_zh, title_translation_provider = translate_summary(title, source.language, preserve_terms, allow_online_translation)

    return {
        "id": stable_id(title, link),
        "title": title,
        "title_zh": title_zh,
        "title_translation_provider": title_translation_provider,
        "player": player,
        "from_club": clubs[0],
        "to_club": clubs[1],
        "league": league,
        "category": category,
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
                "language": source.language,
                "published_at": published or collected_at,
            }
        ],
    }


def merge_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        key = normalise_key(item["player"], item["to_club"], item.get("category", "transfer"), item.get("id", ""))
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

    sorted_items = sorted(merged.values(), key=lambda item: (item["heat_score"], item["credibility_score"]), reverse=True)
    transfers = [item for item in sorted_items if item.get("category") == "transfer"]
    general = sorted(
        [item for item in sorted_items if item.get("category") == "general"],
        key=lambda item: DateOrLow(item.get("reported_at", "")),
        reverse=True,
    )[:MAX_GENERAL_ITEMS]
    return transfers + general


def apply_openai_translations(items: list[dict[str, Any]]) -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    RUN_DIAGNOSTICS["openai_configured"] = bool(api_key)
    RUN_DIAGNOSTICS["openai_requested"] = 0
    RUN_DIAGNOSTICS["openai_applied"] = 0
    RUN_DIAGNOSTICS["openai_error"] = ""
    RUN_DIAGNOSTICS["openai_model"] = get_openai_model()
    if not api_key:
        print("DeepSeek translation skipped: DEEPSEEK_API_KEY is not configured")
        return
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        preserve_terms = [entity["name"] for entity in item.get("entities", []) if entity.get("type") in {"player", "club", "league"}]
        for field, target in [("title", "title_zh"), ("summary", "summary_zh")]:
            source_text = item.get(field, "")
            if not source_text or has_cjk(source_text):
                continue
            cache_key = f"{item.get('sources', [{}])[0].get('language', 'en')}|{source_text}"
            current = item.get(target, "")
            if cache_key in seen:
                continue
            if current and current != source_text and not looks_undertranslated(current):
                continue
            seen.add(cache_key)
            requests.append(
                {
                    "id": cache_key,
                    "text": source_text,
                    "source_language": item.get("sources", [{}])[0].get("language", "en"),
                    "preserve_terms": preserve_terms,
                }
            )
            if len(requests) >= MAX_OPENAI_TRANSLATION_ITEMS:
                break
        if len(requests) >= MAX_OPENAI_TRANSLATION_ITEMS:
            break
    if not requests:
        print("OpenAI translation skipped: no uncached or under-translated text")
        return

    RUN_DIAGNOSTICS["openai_requested"] = len(requests)
    print(f"OpenAI translation requested for {len(requests)} text items")
    translated = deepseek_translate_batches(requests, api_key)
    if not translated:
        print("OpenAI translation returned no usable translations")
        return
    RUN_DIAGNOSTICS["openai_applied"] = len(translated)
    print(f"OpenAI translation applied to {len(translated)} text items")
    for item in items:
        language = item.get("sources", [{}])[0].get("language", "en")
        for field, target, provider in [
            ("title", "title_zh", "title_translation_provider"),
            ("summary", "summary_zh", "translation_provider"),
        ]:
            cache_key = f"{language}|{item.get(field, '')}"
            if cache_key in translated:
                item[target] = translated[cache_key]
                item[provider] = "openai"
                TRANSLATION_CACHE[cache_key] = translated[cache_key]


def looks_undertranslated(text: str) -> bool:
    latin_words = re.findall(r"[A-Za-z]{4,}", text)
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return len(latin_words) >= 3 and len(chinese_chars) < 10


def deepseek_translate_batches(requests: list[dict[str, Any]], api_key: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for start in range(0, len(requests), OPENAI_TRANSLATION_BATCH_SIZE):
        batch = requests[start : start + OPENAI_TRANSLATION_BATCH_SIZE]
        result = deepseek_translate_batch(batch, api_key)
        output.update(result)
    return output


def deepseek_translate_batch(batch: list[dict[str, Any]], api_key: str) -> dict[str, str]:
    model = get_openai_model()
    system_prompt = (
        "You are a football news translator. "
        "Translate the given items into natural Simplified Chinese. "
        "Preserve player names, club names, league names, amounts, years, scores, and URLs exactly. "
        'Return strict JSON only: {"translations":[{"id":"...","text":"..."}]}. '
        "Do not add explanations or markdown fences."
    )
    user_prompt = json.dumps({"items": batch}, ensure_ascii=False)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "transfer-dashboard/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            resp_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        RUN_DIAGNOSTICS["openai_error"] = f"http_{exc.code}: {body[:500]}"
        return {}
    except urllib.error.URLError as exc:
        RUN_DIAGNOSTICS["openai_error"] = f"url_error: {exc.reason}"
        return {}
    except TimeoutError:
        RUN_DIAGNOSTICS["openai_error"] = "timeout"
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        RUN_DIAGNOSTICS["openai_error"] = f"response_decode_error: {exc}"
        return {}
    text = extract_chat_completion_text(resp_payload)
    if not text.strip():
        RUN_DIAGNOSTICS["openai_error"] = f"empty_response_text: {json.dumps(resp_payload, ensure_ascii=False)[:500]}"
        return {}
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            RUN_DIAGNOSTICS["openai_error"] = f"json_parse_error: {text[:500]}"
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            RUN_DIAGNOSTICS["openai_error"] = f"json_parse_error: {exc}: {text[:500]}"
            return {}
    translations = parsed.get("translations", [])
    if not isinstance(translations, list):
        RUN_DIAGNOSTICS["openai_error"] = f"invalid_translation_shape: {text[:500]}"
        return {}
    return {
        str(item.get("id")): str(item.get("text", "")).strip()
        for item in translations
        if isinstance(item, dict) and item.get("id") and item.get("text")
    }


def get_openai_model() -> str:
    return (
        os.environ.get("DEEPSEEK_TRANSLATION_MODEL", "").strip()
        or os.environ.get("DEEPSEEK_MODEL", "").strip()
        or "deepseek-chat"
    )


def extract_chat_completion_text(payload: dict[str, Any]) -> str:
    try:
        return str(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        return ""


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


def classify_news_category(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in EXCLUDE_TERMS):
        return "ignore"
    if any(has_term(lowered, term) for term in TRANSFER_TERMS):
        return "transfer"
    return "general"


def has_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text, re.I) is not None


def guess_player(title: str, combined: str = "") -> str:
    cached = find_cached_players(combined or title)
    if cached:
        return str(cached[0].get("name") or "未知球员")
    title = re.sub(r"\s+-\s+.*$", "", title).strip()
    _NAME = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’’-]+(?:[- ][A-Z][A-Za-zÀ-ÖØ-öø-ÿ’’-]+){0,3}"
    patterns = [
        rf"\bfor\s+({_NAME})\b",
        rf"\bsign(?:s|ed|ing)?\s+({_NAME})\b",
        rf"\bjoins?\s+({_NAME})\b",
        rf"\b(?:appoints?|appointed|names?|named|hires?|hired)\s+({_NAME})\b",
        rf"\b({_NAME})\s+(?:appointed|named|confirmed|hired|sacked|resigns?|resigned|departs?|departed)\b",
        rf"\b(?:manager|coach|head\s+coach|boss)\s+({_NAME})\b",
        rf"\b({_NAME})\s+(?:extends?|extended|renews?|renewed)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            candidate = (match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)).strip()
            resolved = resolve_player_candidate(candidate)
            if resolved:
                return str(resolved.get("name") or candidate)
            if looks_like_person_name(candidate):
                return candidate
    for separator in [":", " - ", " | "]:
        if separator in title:
            candidate = title.split(separator, 1)[0].strip()
            resolved = resolve_player_candidate(candidate)
            if resolved:
                return str(resolved.get("name") or candidate)
            if looks_like_person_name(candidate):
                return candidate
    persons = extract_person_names(title)
    if persons:
        return persons[0]
    return "未知球员"


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


def build_entities(player: str, clubs: tuple[str, str], league: str, text: str = "") -> list[dict[str, str]]:
    entities: list[dict[str, str]] = []
    for record in find_cached_players(text):
        entities.append(player_entity_record(record))
    resolved_player = resolve_player_candidate(player)
    if resolved_player:
        entities.append(player_entity_record(resolved_player))
    elif looks_like_person_name(player):
        entities.append(entity_record(player, "player", f"{player}：新闻中提到的球员或转会相关人物。"))
    for club in clubs:
        if club and club != "未知":
            entities.append(entity_record(club, "club", f"{club}：新闻中提到的俱乐部或目标球队。"))
    if league and league != "其他":
        entities.append(entity_record(league, "league", f"{league}：该条新闻归类到的联赛或地区。"))
    return merge_entities([], entities)


def find_cached_players(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    matches: list[dict[str, Any]] = []
    for record in PLAYER_CACHE.values():
        aliases = record.get("aliases", []) or []
        for alias in aliases:
            alias_text = str(alias)
            if not alias_text:
                continue
            matched = alias_text in text if has_cjk(alias_text) else has_term(lowered, alias_text.lower())
            if matched:
                matches.append(record)
                break
    return sorted(matches, key=lambda item: len(str(item.get("name", ""))), reverse=True)


def resolve_player_candidate(candidate: str) -> dict[str, Any] | None:
    if not candidate or candidate in {"未知球员", "未知"}:
        return None
    for record in PLAYER_CACHE.values():
        aliases = [str(alias).lower() for alias in record.get("aliases", [])]
        if candidate.lower() == str(record.get("name", "")).lower() or candidate.lower() in aliases:
            return record
    if not looks_like_person_name(candidate):
        return None
    record = query_wikidata_player(candidate)
    if record:
        PLAYER_CACHE[str(record["name"])] = record
        return record
    return None


def query_wikidata_player(candidate: str) -> dict[str, Any] | None:
    params = urllib.parse.urlencode(
        {
            "action": "wbsearchentities",
            "format": "json",
            "language": "en",
            "uselang": "en",
            "type": "item",
            "limit": 5,
            "search": f"{candidate} footballer",
        }
    )
    url = f"https://www.wikidata.org/w/api.php?{params}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "transfer-dashboard/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    for result in payload.get("search", []):
        description = str(result.get("description", "")).lower()
        if not any(term in description for term in ["footballer", "soccer player", "association football"]):
            continue
        name = str(result.get("label") or candidate)
        aliases = set(str(alias) for alias in (result.get("aliases", []) or []))
        aliases.update({candidate, name, name.split(" ")[-1]})
        return {
            "name": name,
            "aliases": sorted(alias for alias in aliases if alias),
            "zh_names": [],
            "wikidata_id": result.get("id", ""),
            "wiki_url": result.get("concepturi", ""),
            "description": result.get("description", f"{name}：足球运动员。"),
        }
    return None


def player_entity_record(player: dict[str, Any]) -> dict[str, str]:
    name = str(player.get("name", ""))
    wiki = fetch_wiki_entity(name, "player")
    record = {
        "name": name,
        "type": "player",
        "description": wiki.get("description") or str(player.get("description") or f"{name}：足球运动员。"),
        "wiki_url": wiki.get("wiki_url") or str(player.get("wiki_url") or f"https://zh.wikipedia.org/w/index.php?search={urllib.parse.quote(name)}"),
        "search_url": f"https://www.google.com/search?q={urllib.parse.quote(name + ' football transfer')}",
    }
    for field in ["image_url", "wiki_title", "source_language", "wiki_variant", "translated_from"]:
        if wiki.get(field):
            record[field] = wiki[field]
    if player.get("wikidata_id"):
        record["wikidata_id"] = str(player["wikidata_id"])
    return record


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
    if wiki.get("source_language"):
        record["source_language"] = wiki["source_language"]
    if wiki.get("wiki_variant"):
        record["wiki_variant"] = wiki["wiki_variant"]
    if wiki.get("translated_from"):
        record["translated_from"] = wiki["translated_from"]
    return record


def fetch_wiki_entity(name: str, kind: str) -> dict[str, str]:
    global WIKI_MISSES
    cache_key = f"{kind}|{name}"
    if cache_key in WIKI_CACHE and cache_is_current(WIKI_CACHE[cache_key]):
        return WIKI_CACHE[cache_key]
    if WIKI_MISSES >= MAX_WIKI_MISSES:
        return {}
    WIKI_MISSES += 1
    if name in WIKI_TITLE_OVERRIDES:
        for language, title in WIKI_TITLE_OVERRIDES[name].items():
            result = query_wikipedia_title(title, language)
            if result:
                resolved = resolve_wiki_language(result)
                WIKI_CACHE[cache_key] = resolved
                return resolved
    search_name = wiki_search_name(name, kind)
    for language in ["zh", "en"]:
        result = query_wikipedia(search_name, language)
        if result and wiki_result_matches_kind(result, kind, name):
            resolved = resolve_wiki_language(result)
            WIKI_CACHE[cache_key] = resolved
            return resolved
    if search_name != name:
        for language in ["zh", "en"]:
            result = query_wikipedia(name, language)
            if result and wiki_result_matches_kind(result, kind, name):
                resolved = resolve_wiki_language(result)
                WIKI_CACHE[cache_key] = resolved
                return resolved
    WIKI_CACHE[cache_key] = {}
    return {}


def cache_is_current(record: dict[str, str]) -> bool:
    return bool(record.get("description") or record.get("wiki_title") or record.get("wiki_url"))


def resolve_wiki_language(result: dict[str, str]) -> dict[str, str]:
    if result.get("source_language") == "zh":
        return result
    zh_title = result.get("zh_title", "")
    english_image = result.get("image_url", "")
    if zh_title:
        for variant in ["zh-hans", "zh-hant"]:
            chinese = query_wikipedia_title(zh_title, "zh", variant=variant)
            if chinese:
                if not chinese.get("image_url") and english_image:
                    chinese["image_url"] = english_image
                chinese["translated_from"] = result.get("wiki_title", "")
                return chinese
    translated = dict(result)
    translated["description"] = translate_text_only(result.get("description", ""), "en")
    translated["translated_from"] = result.get("wiki_title", "")
    translated["wiki_variant"] = "translated-en"
    return translated


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


def query_wikipedia_title(title: str, language: str, variant: str | None = None) -> dict[str, str]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "extracts|pageimages|info|langlinks",
            "exintro": 1,
            "explaintext": 1,
            "piprop": "thumbnail",
            "pithumbsize": 360,
            "inprop": "url",
            "redirects": 1,
            "lllang": "zh",
        }
    )
    suffix = f"&variant={variant}" if variant else ""
    return query_wikipedia_api(f"https://{language}.wikipedia.org/w/api.php?{params}{suffix}", language, variant)


def query_wikipedia(name: str, language: str) -> dict[str, str]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": name,
            "gsrlimit": 1,
            "prop": "extracts|pageimages|info|langlinks",
            "exintro": 1,
            "explaintext": 1,
            "piprop": "thumbnail",
            "pithumbsize": 360,
            "inprop": "url",
            "redirects": 1,
            "lllang": "zh",
        }
    )
    return query_wikipedia_api(f"https://{language}.wikipedia.org/w/api.php?{params}", language, "original")


def query_wikipedia_api(url: str, language: str, variant: str | None) -> dict[str, str]:
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
        "source_language": language,
        "wiki_variant": variant or language,
        "zh_title": zh_langlink_title(page),
    }


def zh_langlink_title(page: dict[str, Any]) -> str:
    for link in page.get("langlinks", []) or []:
        if link.get("lang") == "zh":
            return str(link.get("*") or "")
    return ""


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
    words = re.findall(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’’-]+", value)
    return 2 <= len(words) <= 4


def extract_person_names(text: str) -> list[str]:
    """Return all 2-4 word capitalized sequences in text that look like person names."""
    club_lower = {alias.lower() for aliases in CLUB_ALIASES.values() for alias in aliases}
    found: list[str] = []
    for match in re.finditer(
        r"\b([A-Z][A-Za-zÀ-ÖØ-öø-ÿ\’-]+(?:[ ][A-Z][A-Za-zÀ-ÖØ-öø-ÿ\’-]+){1,3})\b",
        text,
    ):
        candidate = match.group(1)
        words = candidate.split()
        if any(w.lower() in _PERSON_NAME_SKIP for w in words):
            continue
        if candidate.lower() in club_lower:
            continue
        found.append(candidate)
    return list(dict.fromkeys(found))


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


def translate_summary(
    text: str,
    source_language: str = "en",
    preserve_terms: list[str] | None = None,
    allow_online: bool = True,
) -> tuple[str, str]:
    if not text:
        return "", "empty"
    if has_cjk(text):
        return text, "original"
    preserve_terms = preserve_terms or []
    cache_key = f"{source_language}|{text}"
    if cache_key in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[cache_key], "cache"

    protected_text, placeholders = protect_terms(text, preserve_terms)
    global ONLINE_TRANSLATION_MISSES
    if allow_online and ONLINE_TRANSLATION_MISSES < MAX_ONLINE_TRANSLATION_MISSES:
        ONLINE_TRANSLATION_MISSES += 1
        translated = translate_with_mymemory(protected_text, source_language)
        if translated:
            restored = restore_terms(translated, placeholders)
            TRANSLATION_CACHE[cache_key] = restored
            return restored, "mymemory"

    fallback = restore_terms(glossary_translate(protected_text), placeholders)
    TRANSLATION_CACHE[cache_key] = fallback
    return fallback, "glossary"


def translate_text_only(text: str, source_language: str = "en") -> str:
    if not text or has_cjk(text):
        return text
    translated = translate_with_mymemory(text[:500], source_language)
    return translated or glossary_translate(text)


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
        with urllib.request.urlopen(request, timeout=6) as response:
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


def normalise_key(player: str, to_club: str, category: str = "transfer", item_id: str = "") -> str:
    if category == "general":
        return f"general-{item_id}"
    if player in {"未知球员", "未知", ""} or to_club in {"未知", ""}:
        return f"transfer-{item_id}"
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


