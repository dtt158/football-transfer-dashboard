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
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "transfers.json"


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
    },
    {
        "name": "ESPN Soccer",
        "url": "https://www.espn.com/espn/rss/soccer/news",
        "grade": "B",
        "description": "国际主流体育媒体，覆盖欧洲主流联赛和转会动态。",
        "kind": "rss",
    },
    {
        "name": "Transfermarkt",
        "url": "https://www.transfermarkt.co.uk/rss/news",
        "grade": "B",
        "description": "转会、身价和传闻聚合参考源；当前自动采集默认关闭，避免其反爬限制影响每小时更新。",
        "kind": "reference",
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
]

TRANSLATION_CACHE: dict[str, str] = {}

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--keep-existing-on-empty", action="store_true", default=True)
    args = parser.parse_args()

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
            }
            for source in sources
        ],
        "errors": errors,
        "transfers": transfers,
    }
    write_json(args.output, payload)
    print(f"Wrote {len(transfers)} transfer items to {args.output}")
    if errors:
        print("Fetch warnings:", "; ".join(errors), file=sys.stderr)
    return 0


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

    summary = summarise(description or title)
    summary_zh, translation_provider = translate_summary(summary)

    return {
        "id": stable_id(title, link),
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
            current["summary"] = item["summary"]
            current["reported_at"] = item["reported_at"]

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
    return any(term in lowered for term in TRANSFER_TERMS)


def guess_player(title: str) -> str:
    title = re.sub(r"\s+-\s+.*$", "", title).strip()
    for separator in [":", " - ", " | "]:
        if separator in title:
            candidate = title.split(separator, 1)[0].strip()
            if 3 <= len(candidate) <= 80:
                return candidate
    return title[:80] or "未知球员"


def guess_clubs(text: str) -> tuple[str, str]:
    clubs = []
    for league_terms in LEAGUE_KEYWORDS.values():
        for term in league_terms:
            if term in text.lower():
                clubs.append(title_case_club(term))
    clubs = list(dict.fromkeys(clubs))
    if len(clubs) >= 2:
        return clubs[0], clubs[1]
    if len(clubs) == 1:
        return "未知", clubs[0]
    return "未知", "未知"


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


def translate_summary(text: str) -> tuple[str, str]:
    if not text:
        return "", "empty"
    if has_cjk(text):
        return text, "original"
    if text in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[text], "cache"

    translated = translate_with_mymemory(text)
    if translated:
        TRANSLATION_CACHE[text] = translated
        return translated, "mymemory"

    fallback = glossary_translate(text)
    TRANSLATION_CACHE[text] = fallback
    return fallback, "glossary"


def translate_with_mymemory(text: str) -> str:
    params = urllib.parse.urlencode({"q": text[:500], "langpair": "en|zh-CN"})
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
