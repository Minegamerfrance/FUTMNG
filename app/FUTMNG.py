# -*- coding: utf-8 -*-
"""FUTMNG - FIFA 17 Ultimate Team Database (read-only).

Designed for the MNG FIFA 17 local server and the FUTMNG project. The application only reads server
files and never writes to the FUT database/state.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import subprocess
import urllib.error
import urllib.request
import tkinter as tk

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    Image = None
    ImageTk = None
    PIL_AVAILABLE = False
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Iterable, List, Optional, Set, Tuple

APP_TITLE = "FUTMNG - FIFA 17 Ultimate Team Database"
APP_VERSION = "1.0.3"
GITHUB_OWNER = "Minegamerfrance"
GITHUB_REPO = "FUTMNG"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
GITHUB_RAW_HEADS_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/assets/heads"

# ----------------------------- Models -------------------------------------

@dataclass
class Card:
    asset_id: int
    resource_id: int
    name: str
    rating: int = 0
    position: str = ""
    team_id: int = 0
    league_id: int = 0
    nation: int = 0
    attributes: List[int] = field(default_factory=list)
    quality: str = ""
    version: int = 0
    rare_flag: int = 0
    card_type: str = ""
    card_type_name: str = ""
    source: str = "catalogue"

@dataclass
class TournamentReward:
    tournament_id: int
    tournament_name: str
    resource_id: int
    description: str = ""

@dataclass
class Acquisition:
    packable: bool = False
    pack_names: List[str] = field(default_factory=list)
    sbc_direct: bool = False
    sbc_names: List[str] = field(default_factory=list)
    sbc_pack_possible: bool = False
    cup_reward: bool = False
    cup_names: List[str] = field(default_factory=list)
    marketable: bool = False
    exclusive: bool = False
    notes: List[str] = field(default_factory=list)

# ----------------------------- JS parsing ---------------------------------

def _find_matching(text: str, start: int, opening: str = "{", closing: str = "}") -> int:
    depth = 0
    quote = None
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
            continue
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _assignment_block(text: str, name: str, opening: str, closing: str) -> str:
    m = re.search(rf"\b(?:const|let|var)\s+{re.escape(name)}\s*=", text)
    if not m:
        return ""
    p = text.find(opening, m.end())
    if p < 0:
        return ""
    q = _find_matching(text, p, opening, closing)
    return text[p:q + 1] if q >= 0 else ""


def _clean_text(value: str) -> str:
    # fut-backend.js contains a few historical UTF-8/latin-1 mojibake strings.
    # Repair only when the common marker is present, otherwise keep the text.
    if any(mark in value for mark in ("Ã", "Â", "â")):
        try:
            repaired = value.encode("latin-1").decode("utf-8")
            if repaired:
                value = repaired
        except Exception:
            pass
    return value.replace("\\'", "'").replace('\\"', '"')


def _string_field(block: str, field_name: str) -> str:
    m = re.search(rf"\b{re.escape(field_name)}\s*:\s*(['\"])(.*?)\1", block, re.S)
    if not m:
        return ""
    return _clean_text(m.group(2))


def _expr_field(block: str, field_name: str) -> str:
    m = re.search(rf"\b{re.escape(field_name)}\s*:\s*([A-Za-z_$][\w$]*|-?\d+)", block)
    return m.group(1) if m else ""


def _number(expr: str, constants: Dict[str, int], default: int = 0) -> int:
    if not expr:
        return default
    if re.fullmatch(r"-?\d+", expr):
        try:
            return int(expr)
        except ValueError:
            return default
    return int(constants.get(expr, default))


def _array_numbers(block: str, field_name: str) -> List[int]:
    m = re.search(rf"\b{re.escape(field_name)}\s*:\s*\[([^\]]*)\]", block, re.S)
    if not m:
        return []
    return [int(x) for x in re.findall(r"-?\d+", m.group(1))]


def parse_constants(text: str) -> Dict[str, int]:
    constants: Dict[str, int] = {}
    # Numeric literals first.
    for name, value in re.findall(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*(-?\d+)\s*;", text):
        constants[name] = int(value)
    # Simple aliases. Repeat because aliases can chain.
    for _ in range(8):
        changed = False
        for name, other in re.findall(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\s*;", text):
            if other in constants and constants.get(name) != constants[other]:
                constants[name] = constants[other]
                changed = True
        if not changed:
            break
    return constants


def iter_object_blocks(text: str, max_len: int = 7000) -> Iterable[str]:
    # A tolerant object-literal scanner. Nested blocks are also yielded; callers
    # filter by required fields and deduplicate by resourceId.
    starts = [m.start() for m in re.finditer(r"\{", text)]
    for p in starts:
        q = _find_matching(text, p, "{", "}")
        if q < 0:
            continue
        if q - p + 1 <= max_len:
            yield text[p:q + 1]


def card_from_block(block: str, constants: Dict[str, int]) -> Optional[Card]:
    if "resourceId" not in block or "assetId" not in block or "rating" not in block:
        return None
    rid = _number(_expr_field(block, "resourceId"), constants)
    aid = _number(_expr_field(block, "assetId"), constants)
    rating = _number(_expr_field(block, "rating"), constants)
    name = _string_field(block, "name") or _string_field(block, "displayName")
    if rid <= 0 or aid <= 0 or rating <= 0 or not name:
        return None
    attrs = _array_numbers(block, "attributes")
    if attrs and len(attrs) > 8:
        attrs = attrs[:6]
    position = _string_field(block, "position")
    # Avoid mistaking managers, stadiums, kits, balls and consumables for players.
    # Player definitions in this backend always carry a position and/or the six FUT stats.
    if not position and len(attrs) < 6:
        return None
    return Card(
        asset_id=aid,
        resource_id=rid,
        name=name,
        rating=rating,
        position=position,
        team_id=_number(_expr_field(block, "teamId"), constants),
        league_id=_number(_expr_field(block, "leagueId"), constants),
        nation=_number(_expr_field(block, "nation"), constants),
        attributes=attrs,
        quality=_string_field(block, "quality"),
        version=_number(_expr_field(block, "version"), constants),
        rare_flag=_number(_expr_field(block, "rareFlag"), constants),
        card_type=_string_field(block, "cardType"),
        card_type_name=_string_field(block, "cardTypeName"),
        source="runtime JS",
    )


def parse_runtime_cards(text: str, constants: Dict[str, int]) -> Dict[int, Card]:
    found: Dict[int, Card] = {}
    for block in iter_object_blocks(text):
        card = card_from_block(block, constants)
        if not card:
            continue
        current = found.get(card.resource_id)
        score = lambda c: sum([
            bool(c.position), bool(c.attributes), bool(c.card_type), bool(c.card_type_name),
            bool(c.team_id), bool(c.league_id), bool(c.nation), bool(c.rare_flag)
        ])
        if current is None or score(card) > score(current):
            found[card.resource_id] = card

    # COMMUNITY_SBC_CARDS is transformed by a JavaScript `.map(...)` at runtime.
    # Reproduce that final metadata so Kopa/Kahn/Barthez/etc. appear as Icons
    # instead of looking like base cards in the browser.
    community = _assignment_block(text, "COMMUNITY_SBC_CARDS", "[", "]")
    if community:
        for block in iter_object_blocks(community, max_len=3500):
            card = card_from_block(block, constants)
            if not card:
                continue
            is_icon = bool(re.search(r"\bicon\s*:\s*true\b", block))
            card.quality = card.quality or "gold"
            card.version = card.version or 6
            card.rare_flag = card.rare_flag or (12 if is_icon else 29)
            card.card_type = card.card_type or ("icon" if is_icon else "mng_icon")
            card.card_type_name = card.card_type_name or ("LÉGENDES" if is_icon else "HALL OF FAME")
            found[card.resource_id] = card
    return found


def parse_exclusive_ids(text: str, constants: Dict[str, int]) -> Set[int]:
    block = _assignment_block(text, "SBC_EXCLUSIVE_RESOURCE_IDS", "[", "]")
    if not block:
        # Assignment contains `new Set([ ... ])`; find from identifier directly.
        m = re.search(r"SBC_EXCLUSIVE_RESOURCE_IDS\s*=\s*new\s+Set\s*\(\s*\[([^\]]*)\]", text, re.S)
        content = m.group(1) if m else ""
    else:
        content = block[1:-1]
    ids = set()
    for token in re.findall(r"[A-Za-z_$][\w$]*|-?\d+", content):
        value = _number(token, constants)
        if value > 0:
            ids.add(value)
    return ids


def parse_sbc_direct_rewards(text: str, constants: Dict[str, int]) -> Dict[int, List[str]]:
    rewards: Dict[int, List[str]] = {}
    # The live server queues final player rewards using source:'SBC_*' and rewardResourceId.
    for block in iter_object_blocks(text, max_len=4000):
        source = _string_field(block, "source")
        if not source.upper().startswith("SBC_") or "rewardResourceId" not in block:
            continue
        rid = _number(_expr_field(block, "rewardResourceId"), constants)
        if rid <= 0:
            continue
        player_name = _string_field(block, "playerName")
        label = player_name or source.replace("SBC_", "").replace("_FINAL", "").replace("_", " ").title()
        if "LOAN" in source.upper():
            label += " (prêt)"
        rewards.setdefault(rid, [])
        if label not in rewards[rid]:
            rewards[rid].append(label)
    return rewards


def parse_tournament_rewards(text: str, constants: Dict[str, int]) -> Dict[int, List[TournamentReward]]:
    arr = _assignment_block(text, "OFFLINE_TOURNAMENT_DEFS", "[", "]")
    result: Dict[int, List[TournamentReward]] = {}
    if not arr:
        return result
    for block in iter_object_blocks(arr, max_len=2000):
        rid = _number(_expr_field(block, "rewardResourceId"), constants)
        if rid <= 0:
            continue
        tid = _number(_expr_field(block, "id"), constants)
        name = _string_field(block, "name") or f"Coupe {tid}"
        desc = _string_field(block, "description")
        tr = TournamentReward(tid, name, rid, desc)
        result.setdefault(rid, []).append(tr)
    return result


def parse_pack_defaults(text: str, constants: Dict[str, int]) -> List[dict]:
    block = _assignment_block(text, "PACKS", "{", "}")
    packs: List[dict] = []
    if not block:
        return packs
    for obj in iter_object_blocks(block, max_len=2500):
        pid = _number(_expr_field(obj, "id"), constants)
        name = _string_field(obj, "name")
        tier = _string_field(obj, "tier")
        if pid <= 0 or not name or tier not in {"bronze", "silver", "gold"}:
            continue
        p = {
            "id": pid,
            "name": name,
            "tier": tier,
            "count": _number(_expr_field(obj, "count"), constants, 12),
            "players": _number(_expr_field(obj, "players"), constants, 0),
            "rares": _number(_expr_field(obj, "rares"), constants, 0),
            "specialChance": 0.0,
            "guaranteedLegends": _number(_expr_field(obj, "guaranteedLegends"), constants, 0),
            "maxPurchases": _number(_expr_field(obj, "maxPurchases"), constants, 0),
            "displayGroup": _string_field(obj, "displayGroup"),
        }
        m = re.search(r"\bspecialChance\s*:\s*([0-9.]+)", obj)
        if m:
            try:
                p["specialChance"] = float(m.group(1))
            except ValueError:
                pass
        packs.append(p)
    # Deduplicate nested matches by id.
    return list({p["id"]: p for p in packs}.values())

# ----------------------------- Server loading ------------------------------

class ServerDatabase:
    def __init__(self, root: Path):
        self.root = root
        self.data_dir = root / "data"
        self.catalog_path = self.data_dir / "fifa17-card-catalog.json"
        self.backend_path = root / "fut-backend.js"
        self.pack_admin_path = self.data_dir / "mng-pack-admin.json"
        self.state_path = self.data_dir / "local-fut-state.json"
        self.image_cache = self.data_dir / "online-images" / "cache"
        self.image_db_path = self.image_cache / "database.json"
        self.totw_weeks_path = self.data_dir / "fifa17-totw-weeks.json"

        self.cards: List[Card] = []
        self.by_resource: Dict[int, Card] = {}
        self.constants: Dict[str, int] = {}
        self.exclusive_ids: Set[int] = set()
        self.sbc_rewards: Dict[int, List[str]] = {}
        self.tournament_rewards: Dict[int, List[TournamentReward]] = {}
        self.pack_defs: List[dict] = []
        self.pack_admin: dict = {}
        self.image_records: Dict[int, dict] = {}
        self.totw_week_by_resource: Dict[int, int] = {}
        self.backend_text = ""
        self._acq_cache: Dict[int, Acquisition] = {}

    def validate(self) -> None:
        missing = [p for p in [self.catalog_path, self.backend_path] if not p.exists()]
        if missing:
            raise FileNotFoundError("Fichiers serveur introuvables :\n" + "\n".join(str(p) for p in missing))

    def load(self) -> None:
        self.validate()
        with self.catalog_path.open("r", encoding="utf-8") as f:
            catalog = json.load(f)
        self.backend_text = self.backend_path.read_text(encoding="utf-8", errors="replace")
        self.constants = parse_constants(self.backend_text)
        self.exclusive_ids = parse_exclusive_ids(self.backend_text, self.constants)
        self.sbc_rewards = parse_sbc_direct_rewards(self.backend_text, self.constants)
        self.tournament_rewards = parse_tournament_rewards(self.backend_text, self.constants)
        self.pack_defs = parse_pack_defaults(self.backend_text, self.constants)

        if self.pack_admin_path.exists():
            try:
                self.pack_admin = json.loads(self.pack_admin_path.read_text(encoding="utf-8"))
            except Exception:
                self.pack_admin = {}

        if self.image_db_path.exists():
            try:
                raw = json.loads(self.image_db_path.read_text(encoding="utf-8"))
                for key, value in (raw.get("images") or {}).items():
                    try:
                        self.image_records[int(key)] = value
                    except Exception:
                        pass
            except Exception:
                pass

        if self.totw_weeks_path.exists():
            try:
                weeks = json.loads(self.totw_weeks_path.read_text(encoding="utf-8"))
                for week, entry in (weeks.get("weeks") or {}).items():
                    for player in entry.get("players", []):
                        rid = int(player.get("resourceId", 0) or 0)
                        if rid:
                            self.totw_week_by_resource[rid] = int(week)
            except Exception:
                pass

        cards: Dict[int, Card] = {}
        for source_name in ("base", "specials"):
            for row in catalog.get(source_name, []):
                try:
                    c = Card(
                        asset_id=int(row.get("assetId", 0) or 0),
                        resource_id=int(row.get("resourceId", 0) or 0),
                        name=str(row.get("name") or row.get("displayName") or "Inconnu"),
                        rating=int(row.get("rating", 0) or 0),
                        position=str(row.get("position") or ""),
                        team_id=int(row.get("teamId", 0) or 0),
                        league_id=int(row.get("leagueId", 0) or 0),
                        nation=int(row.get("nation", 0) or 0),
                        attributes=[int(x) for x in row.get("attributes", [])[:6]],
                        quality=str(row.get("quality") or ""),
                        version=int(row.get("version", 0) or 0),
                        rare_flag=int(row.get("rareFlag", row.get("rareflag", 0)) or 0),
                        card_type=str(row.get("cardType") or ""),
                        card_type_name=str(row.get("cardTypeName") or ""),
                        source="catalogue",
                    )
                    if c.resource_id:
                        cards[c.resource_id] = c
                except Exception:
                    continue

        # Add runtime-defined custom cards that are injected by fut-backend.js.
        runtime_cards = parse_runtime_cards(self.backend_text, self.constants)
        for rid, runtime in runtime_cards.items():
            if rid not in cards:
                cards[rid] = runtime
            else:
                base = cards[rid]
                # Runtime definition wins only where it clearly carries richer/current info.
                if runtime.card_type and not base.card_type_name:
                    base.card_type = runtime.card_type
                    base.card_type_name = runtime.card_type_name
                if runtime.attributes and not base.attributes:
                    base.attributes = runtime.attributes

        self.cards = sorted(cards.values(), key=lambda c: (-c.rating, c.name.lower(), c.resource_id))
        self.by_resource = {c.resource_id: c for c in self.cards}

    def image_path(self, card: Card) -> Optional[Path]:
        direct = self.image_cache / f"p{card.resource_id}.png"
        if direct.exists():
            return direct
        record = self.image_records.get(card.resource_id)
        if record and record.get("enabled", True):
            # The cache stores current files as p<resourceId>.png; the database's
            # `png` field may point to a source directory that is not shipped.
            p = self.image_cache / Path(str(record.get("png", ""))).name
            if p.exists():
                return p
        return None

    def _card_category(self, card: Card) -> str:
        if card.version == 0:
            return "base"
        t = card.card_type.lower()
        if t == "flashback" or card.rare_flag == 32:
            return "flashback"
        if t in {"foundation", "foundations"}:
            return "foundation"
        if card.rare_flag == 12 or t in {"icon", "legend", "legends"}:
            return "legends"
        if t in {"mng_icon", "hall_of_fame", "halloffame", "hof"}:
            return "hall_of_fame"
        mapping = {
            "totw": "totw", "otw": "otw", "tots": "tots", "toty": "toty",
            "ultimate_scream": "ultimate_scream", "fut_birthday": "fut_birthday", "futmas": "futmas",
            "sbc": "sbc", "premium_sbc": "sbc"
        }
        return mapping.get(t, "special")

    def _event_allowed(self, card: Card) -> bool:
        cfg_events = {
            "legends": True, "hall_of_fame": True, "otw": True, "tots": True,
            "toty": True, "ultimate_scream": True, "fut_birthday": True, "futmas": True,
        }
        cfg_events.update(self.pack_admin.get("events") or {})
        cat = self._card_category(card)
        if cat in cfg_events and cfg_events.get(cat) is False:
            return False
        if cat == "totw":
            promo = {"totw": True}
            promo.update(self.pack_admin.get("packPromoEnabled") or {})
            if promo.get("totw") is False:
                return False
            # Match backend behavior: without the week map, TOTW is not in the normal special pool.
            week = self.totw_week_by_resource.get(card.resource_id)
            enabled_weeks = {int(x) for x in (self.pack_admin.get("packTotwWeeks") or [1]) if str(x).isdigit()}
            return bool(week and week in enabled_weeks)
        return True

    def resolved_store_packs(self) -> List[dict]:
        overrides = self.pack_admin.get("storePacks") or {}
        out = []
        defaults = {
            "base": True, "legends": True, "hall_of_fame": True, "totw": True, "otw": True,
            "tots": True, "toty": True, "ultimate_scream": True, "fut_birthday": True, "futmas": True,
        }
        for p in self.pack_defs:
            ov = overrides.get(str(p["id"]), {}) or {}
            if ov.get("enabled") is False:
                continue
            r = dict(p)
            r["enabled"] = True
            # Backend ignores tier/count/players overrides for built-in packs but accepts price/chance/content.
            try:
                r["specialChance"] = float(ov.get("specialChance", r.get("specialChance", 0)) or 0)
            except Exception:
                pass
            r["guaranteedLegends"] = int(ov.get("guaranteedLegends", r.get("guaranteedLegends", 0)) or 0)
            contents = dict(defaults)
            contents.update(ov.get("contents") or {})
            if not any(bool(v) for v in contents.values()):
                contents["base"] = True
            r["contents"] = contents
            if ov.get("name"):
                r["name"] = str(ov["name"])
            out.append(r)
        # Custom packs are handled by backend only for IDs 900000..999999.
        for custom in self.pack_admin.get("customPacks") or []:
            try:
                pid = int(custom.get("id", 0))
            except Exception:
                continue
            if not (900000 <= pid <= 999999):
                continue
            if (overrides.get(str(pid), {}) or {}).get("enabled") is False:
                continue
            r = {
                "id": pid, "name": str(custom.get("name") or f"Pack MNG {pid}"),
                "tier": str(custom.get("tier") or "gold"), "players": int(custom.get("players", 12) or 0),
                "specialChance": float(custom.get("specialChance", 0) or 0),
                "guaranteedLegends": int(custom.get("guaranteedLegends", 0) or 0),
                "enabled": True,
            }
            ov = overrides.get(str(pid), {}) or {}
            contents = dict(defaults)
            contents.update(ov.get("contents") or {})
            r["contents"] = contents
            out.append(r)
        return out

    def acquisition(self, card: Card) -> Acquisition:
        rid = card.resource_id
        cached = self._acq_cache.get(rid)
        if cached is not None:
            return cached
        a = Acquisition()
        a.exclusive = rid in self.exclusive_ids
        a.sbc_direct = rid in self.sbc_rewards
        a.sbc_names = list(self.sbc_rewards.get(rid, []))
        a.cup_reward = rid in self.tournament_rewards
        a.cup_names = [x.tournament_name for x in self.tournament_rewards.get(rid, [])]

        # Direct exclusives are intentionally blocked from normal market/pack pools.
        a.marketable = not a.exclusive

        category = self._card_category(card)
        if not a.exclusive and self._event_allowed(card):
            for pack in self.resolved_store_packs():
                if int(pack.get("players", 0) or 0) <= 0:
                    continue
                contents = pack.get("contents") or {}
                if category == "base":
                    if contents.get("base") is True and str(pack.get("tier")) == card.quality.lower():
                        a.pack_names.append(str(pack.get("name")))
                else:
                    selected = category if category in contents else ""
                    if selected and contents.get(selected) is True:
                        has_special_path = float(pack.get("specialChance", 0) or 0) > 0
                        if category == "legends" and int(pack.get("guaranteedLegends", 0) or 0) > 0:
                            has_special_path = True
                        if contents.get("base") is False:
                            has_special_path = True
                        if has_special_path:
                            a.pack_names.append(str(pack.get("name")))
        a.pack_names = sorted(set(a.pack_names))
        a.packable = bool(a.pack_names)

        # Weekly SBC can award the active TOTW through a guaranteed player pack.
        if category == "totw":
            week = self.totw_week_by_resource.get(rid)
            active_weeks = {int(x) for x in (self.pack_admin.get("packTotwWeeks") or [1]) if str(x).isdigit()}
            if week and week in active_weeks:
                a.sbc_pack_possible = True

        if a.sbc_direct:
            a.notes.append("Récompense joueur directe d'un SBC.")
        if a.cup_reward:
            a.notes.append("Récompense directe d'une coupe hors ligne.")
        if a.exclusive:
            a.notes.append("Exclu des pools normaux packs/marché par le backend.")
        if not a.packable and category == "totw" and not self.totw_week_by_resource:
            a.notes.append("Le fichier fifa17-totw-weeks.json est absent : le backend ne peut pas l'activer dans les packs TOTW normaux.")
        self._acq_cache[rid] = a
        return a

# ----------------------------- Display names -------------------------------

def friendly_quality(card: Card) -> str:
    """French/user-facing quality name. Never expose internal identifiers."""
    t = (card.card_type or "").strip().lower()
    explicit = {
        "rare_gold": "OR RARE",
        "nonrare_gold": "OR",
        "gold": "OR",
        "rare_silver": "ARGENT RARE",
        "nonrare_silver": "ARGENT",
        "silver": "ARGENT",
        "rare_bronze": "BRONZE RARE",
        "nonrare_bronze": "BRONZE",
        "bronze": "BRONZE",
    }
    if card.version == 0 and t in explicit:
        return explicit[t]

    q = (card.quality or "").strip().lower()
    if q == "gold":
        return "OR RARE" if card.version == 0 and card.rare_flag else "OR"
    if q == "silver":
        return "ARGENT RARE" if card.version == 0 and card.rare_flag else "ARGENT"
    if q == "bronze":
        return "BRONZE RARE" if card.version == 0 and card.rare_flag else "BRONZE"
    return (card.quality or "—").replace("_", " ").upper()


def friendly_card_type(card: Card) -> str:
    """Name shown in the interface for every card family."""
    t = (card.card_type or "").strip().lower()

    # Base cards use their actual FUT quality name.
    if card.version == 0:
        return friendly_quality(card)

    # Custom MNG families / FIFA 17 families.
    if t == "flashback" or card.rare_flag == 32:
        return "FLASHBACK"
    if t in {"foundation", "foundations"}:
        return "FONDATION"
    if t in {"premium_sbc", "premiumsbc"}:
        return "SBC PREMIUM"
    if t == "sbc":
        return "SBC"
    if t in {"icon", "legend", "legends"} or card.rare_flag == 12:
        return "LÉGENDES"
    if t in {"mng_icon", "hall_of_fame", "halloffame", "hof"}:
        return "HALL OF FAME"

    names = {
        "otw": "OTW",
        "totw": "TOTW",
        "tots": "TOTS",
        "toty": "TOTY",
        "futmas": "FUTMAS",
        "ultimate_scream": "ULTIMATE SCREAM",
        "fut_birthday": "FUT BIRTHDAY",
        "flashback": "FLASHBACK",
        "foundation": "FONDATION",
    }
    if t in names:
        return names[t]

    # Prefer a human-provided name only when it is not one of the old technical labels.
    raw_name = (card.card_type_name or "").strip()
    if raw_name and raw_name.lower() not in {"fifa 17 icon", "mng special", "sbc reward"}:
        return raw_name.upper()
    return (t or "CARTE SPÉCIALE").replace("_", " ").upper()


def quality_group(card: Card) -> str:
    """Coarse colour family used by the quality filter."""
    q = (card.quality or "").strip().lower()
    if q == "gold":
        return "OR"
    if q == "silver":
        return "ARGENT"
    if q == "bronze":
        return "BRONZE"
    return q.upper()

# ----------------------------- UI -----------------------------------------

class FUTMNGApp(tk.Tk):
    def __init__(self, initial_root: Optional[Path] = None):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1420x860")
        self.minsize(1120, 700)
        self.configure(bg="#0d1117")
        self.db: Optional[ServerDatabase] = None
        self.filtered: List[Card] = []
        self.current_card: Optional[Card] = None
        self.current_photo = None
        self._head_loading: Set[int] = set()
        self._head_missing: Set[int] = set()

        self._setup_style()
        self._build_ui()
        root = initial_root or autodetect_server_root(Path(__file__).resolve().parent)
        if root:
            self.after(80, lambda: self.load_server(root))
        else:
            self.status_var.set("Serveur non détecté. Clique sur « Choisir le serveur ».")

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background="#111827", fieldbackground="#111827", foreground="#e5e7eb", rowheight=31, borderwidth=0)
        style.configure("Treeview.Heading", background="#1f2937", foreground="#f9fafb", relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#334155")], foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground="#111827", background="#111827", foreground="#111827")
        style.configure("Vertical.TScrollbar", background="#374151", troughcolor="#111827", arrowcolor="#e5e7eb")

    def _build_ui(self):
        top = tk.Frame(self, bg="#101826", height=68)
        top.pack(fill="x")
        tk.Label(top, text="FUTMNG", bg="#101826", fg="#facc15", font=("Segoe UI", 20, "bold")).pack(side="left", padx=(20, 10), pady=15)
        tk.Label(top, text=f"FIFA 17 • lecture seule • v{APP_VERSION}", bg="#101826", fg="#94a3b8", font=("Segoe UI", 10)).pack(side="left", pady=20)
        tk.Button(top, text="Choisir le serveur", command=self.choose_server, bg="#2563eb", fg="white", activebackground="#1d4ed8", relief="flat", font=("Segoe UI", 10, "bold"), padx=14, pady=7).pack(side="right", padx=(8, 20), pady=14)
        self.update_button = tk.Button(top, text="↻  MISE À JOUR", command=self.check_for_updates, bg="#facc15", fg="#111827", activebackground="#eab308", relief="flat", font=("Segoe UI", 10, "bold"), padx=14, pady=7)
        self.update_button.pack(side="right", padx=(8, 0), pady=14)

        filters = tk.Frame(self, bg="#0d1117")
        filters.pack(fill="x", padx=16, pady=(12, 8))
        self.search_var = tk.StringVar()
        search = tk.Entry(filters, textvariable=self.search_var, bg="#111827", fg="#f9fafb", insertbackground="white", relief="flat", font=("Segoe UI", 12))
        search.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        search.insert(0, "")
        self.search_var.trace_add("write", lambda *_: self.refresh_list())

        self.type_var = tk.StringVar(value="Tous les types")
        self.obtain_var = tk.StringVar(value="Toutes les obtentions")
        self.quality_var = tk.StringVar(value="Toutes qualités")
        for var, values, width in [
            (self.type_var, ["Tous les types", "Base", "OR RARE", "LÉGENDES", "HALL OF FAME", "FLASHBACK", "OTW", "TOTW", "SBC", "FONDATION", "Autres spéciales"], 18),
            (self.obtain_var, ["Toutes les obtentions", "Packable", "SBC direct", "Coupe", "Marché", "Exclusif"], 20),
            (self.quality_var, ["Toutes qualités", "OR", "ARGENT", "BRONZE"], 16),
        ]:
            cb = ttk.Combobox(filters, textvariable=var, values=values, state="readonly", width=width)
            cb.pack(side="left", padx=4)
            cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_list())

        content = tk.PanedWindow(self, orient="horizontal", bg="#0d1117", sashwidth=6, bd=0)
        content.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        left = tk.Frame(content, bg="#111827")
        right = tk.Frame(content, bg="#0f172a")
        content.add(left, minsize=560, width=760)
        content.add(right, minsize=420)

        columns = ("rating", "name", "pos", "type", "pack", "sbc", "cup", "rid")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        specs = [
            ("rating", "GEN", 58, "center"), ("name", "Joueur", 235, "w"), ("pos", "POS", 58, "center"),
            ("type", "Carte", 115, "w"), ("pack", "PACK", 58, "center"), ("sbc", "SBC", 58, "center"),
            ("cup", "COUPE", 68, "center"), ("rid", "Resource ID", 110, "e"),
        ]
        for col, title, width, anchor in specs:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "name"))
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        self.preview = tk.Frame(right, bg="#0f172a")
        self.preview.pack(fill="both", expand=True, padx=18, pady=16)
        self.title_label = tk.Label(self.preview, text="Sélectionne un joueur", bg="#0f172a", fg="#f8fafc", font=("Segoe UI", 20, "bold"), anchor="w")
        self.title_label.pack(fill="x")
        self.subtitle_label = tk.Label(self.preview, text="", bg="#0f172a", fg="#94a3b8", font=("Segoe UI", 10), anchor="w")
        self.subtitle_label.pack(fill="x", pady=(2, 10))

        card_area = tk.Frame(self.preview, bg="#111827", highlightthickness=1, highlightbackground="#334155")
        card_area.pack(fill="x", pady=(0, 12))
        self.portrait_label = tk.Label(card_area, text="IMAGE\nNON DISPONIBLE", bg="#111827", fg="#64748b", font=("Segoe UI", 12, "bold"), width=22, height=11)
        self.portrait_label.pack(side="left", padx=16, pady=16)
        stats = tk.Frame(card_area, bg="#111827")
        stats.pack(side="left", fill="both", expand=True, padx=(4, 16), pady=16)
        self.rating_big = tk.Label(stats, text="--", bg="#111827", fg="#facc15", font=("Segoe UI", 34, "bold"), anchor="w")
        self.rating_big.pack(fill="x")
        self.position_big = tk.Label(stats, text="", bg="#111827", fg="#e2e8f0", font=("Segoe UI", 13, "bold"), anchor="w")
        self.position_big.pack(fill="x")
        self.stats_label = tk.Label(stats, text="", bg="#111827", fg="#cbd5e1", font=("Consolas", 11), justify="left", anchor="nw")
        self.stats_label.pack(fill="both", expand=True, pady=(10, 0))

        self.badges_frame = tk.Frame(self.preview, bg="#0f172a")
        self.badges_frame.pack(fill="x", pady=(0, 12))

        self.details_text = tk.Text(self.preview, bg="#111827", fg="#dbeafe", insertbackground="white", relief="flat", height=14, wrap="word", font=("Segoe UI", 10), padx=12, pady=10)
        self.details_text.pack(fill="both", expand=True)
        self.details_text.configure(state="disabled")

        bottom = tk.Frame(self, bg="#101826", height=32)
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value=f"{APP_TITLE} v{APP_VERSION}")
        tk.Label(bottom, textvariable=self.status_var, bg="#101826", fg="#94a3b8", font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=16, pady=6)

    def _install_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def _head_candidates(self, card: Card, resource_only: bool = False) -> List[str]:
        """Return Frosty-style p<ID> names.

        Resource ID is always checked first. This is essential for special cards:
        several versions of the same player share the same assetId, while the
        resourceId identifies the exact card/dynamic image. Asset ID remains a
        fallback when no card-specific image exists.
        """
        ids: List[int] = []
        values = (card.resource_id,) if resource_only else (card.resource_id, card.asset_id)
        for value in values:
            try:
                value = int(value)
            except Exception:
                continue
            if value > 0 and value not in ids:
                ids.append(value)
        names: List[str] = []
        for value in ids:
            for ext in ("png", "dds", "PNG", "DDS"):
                names.append(f"p{value}.{ext}")
        return names

    def _is_resource_head(self, card: Card, path: Optional[Path]) -> bool:
        """True when the selected file belongs to the exact resource/card ID."""
        if not path:
            return False
        try:
            return path.stem.lower() == f"p{int(card.resource_id)}".lower()
        except Exception:
            return False

    def _local_head_path(self, card: Card) -> Optional[Path]:
        """Look in FUTMNG assets/cache first, then in the server's historical image cache."""
        root = self._install_root()
        dirs = [root / "assets" / "heads", root / "cache" / "heads"]
        for directory in dirs:
            for name in self._head_candidates(card):
                path = directory / name
                if path.exists() and path.is_file():
                    return path
        if self.db:
            server_img = self.db.image_path(card)
            if server_img and server_img.exists():
                return server_img
        return None

    def _display_head(self, path: Path) -> bool:
        """Display PNG or DDS. Pillow is used when available, which adds DDS support."""
        try:
            if PIL_AVAILABLE:
                with Image.open(path) as im:
                    im = im.convert("RGBA")
                    im.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
                    x = (256 - im.width) // 2
                    y = (256 - im.height) // 2
                    canvas.alpha_composite(im, (x, y))
                    photo = ImageTk.PhotoImage(canvas)
            else:
                if path.suffix.lower() != ".png":
                    return False
                photo = tk.PhotoImage(file=str(path))
                # Keep older 128x128 server cache readable without Pillow.
                if photo.width() <= 160 and photo.height() <= 160:
                    photo = photo.zoom(2, 2)
            self.current_photo = photo
            self.portrait_label.config(image=photo, text="", width=256, height=256)
            return True
        except Exception:
            return False

    def _download_head_async(self, card: Card, resource_only: bool = False, keep_current: bool = False) -> None:
        """Download only the selected player's image from GitHub and cache it locally.

        For a special card, ``resource_only`` lets FUTMNG look for the dynamic
        image without replacing a usable base portrait while the request runs.
        """
        rid = int(card.resource_id)
        loading_key = (rid, bool(resource_only))
        missing_key = (rid, bool(resource_only))
        if loading_key in self._head_loading or missing_key in self._head_missing:
            return
        self._head_loading.add(loading_key)
        if not keep_current:
            self.portrait_label.config(image="", text="CHARGEMENT\nDU VISAGE…", width=22, height=11)
        if resource_only:
            self.status_var.set(f"Recherche de l'image spéciale de {card.name} sur GitHub…")
        else:
            self.status_var.set(f"Téléchargement du visage de {card.name} en arrière-plan…")

        names = self._head_candidates(card, resource_only=resource_only)
        cache_dir = self._install_root() / "cache" / "heads"

        def worker():
            found: Optional[Path] = None
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                for name in names:
                    url = f"{GITHUB_RAW_HEADS_BASE}/{name}"
                    req = urllib.request.Request(url, headers={"User-Agent": f"FUTMNG/{APP_VERSION}"})
                    try:
                        with urllib.request.urlopen(req, timeout=12) as response:
                            data = response.read()
                        if not data:
                            continue
                        target = cache_dir / name
                        target.write_bytes(data)
                        found = target
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code == 404:
                            continue
                        raise
                self.after(0, lambda p=found, c=card, ro=resource_only: self._finish_head_download(c, p, resource_only=ro))
            except Exception as exc:
                self.after(0, lambda c=card, e=exc, ro=resource_only: self._finish_head_download(c, None, e, resource_only=ro))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_head_download(self, card: Card, path: Optional[Path], error: Optional[Exception] = None, resource_only: bool = False) -> None:
        rid = int(card.resource_id)
        key = (rid, bool(resource_only))
        self._head_loading.discard(key)
        # Do not replace the preview if the user selected another player meanwhile.
        if not self.current_card or int(self.current_card.resource_id) != rid:
            return
        if path and self._display_head(path):
            label = "Image spéciale" if resource_only else "Visage"
            self.status_var.set(f"{label} de {card.name} chargée depuis GitHub et mise en cache ✓")
            self._render_badges(card, self.db.acquisition(card), True)
            return
        self._head_missing.add(key)
        # A failed special-image lookup must not erase a base portrait already shown.
        if error and not resource_only:
            self.portrait_label.config(image="", text="IMAGE\nINDISPONIBLE", width=22, height=11)
            self.status_var.set(f"Impossible de charger le visage de {card.name} depuis GitHub")
        else:
            self.portrait_label.config(image="", text="AUCUN VISAGE\nSUR GITHUB", width=22, height=11)
            self.status_var.set(f"Aucun fichier p<ID>.png/.dds trouvé pour {card.name}")
        self._render_badges(card, self.db.acquisition(card), False)

    def _render_badges(self, card: Card, acq: Acquisition, has_image: bool) -> None:
        for child in self.badges_frame.winfo_children():
            child.destroy()
        badges = [
            ("PACKABLE", acq.packable, "#16a34a"),
            ("SBC DIRECT", acq.sbc_direct, "#7c3aed"),
            ("COUPE", acq.cup_reward, "#ea580c"),
            ("MARCHÉ", acq.marketable, "#2563eb"),
            ("EXCLUSIF", acq.exclusive, "#dc2626"),
            ("IMAGE", has_image, "#0891b2"),
        ]
        for text, enabled, color in badges:
            bg = color if enabled else "#1f2937"
            fg = "white" if enabled else "#64748b"
            tk.Label(self.badges_frame, text=text, bg=bg, fg=fg, font=("Segoe UI", 9, "bold"), padx=9, pady=5).pack(side="left", padx=(0, 6))

    @staticmethod
    def _version_tuple(value: str) -> Tuple[int, ...]:
        value = (value or "0").strip().lower().lstrip("v")
        nums = re.findall(r"\d+", value)
        return tuple(int(x) for x in nums[:4]) or (0,)

    def check_for_updates(self):
        """Check the latest public GitHub Release without blocking the UI."""
        if getattr(self, "_update_check_running", False):
            return
        self._update_check_running = True
        self.update_button.config(text="VÉRIFICATION…", state="disabled")
        self.status_var.set("Vérification des mises à jour FUTMNG sur GitHub…")

        def worker():
            try:
                req = urllib.request.Request(
                    GITHUB_LATEST_RELEASE_API,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": f"FUTMNG/{APP_VERSION}",
                    },
                )
                with urllib.request.urlopen(req, timeout=12) as response:
                    release = json.loads(response.read().decode("utf-8"))
                self.after(0, lambda: self._handle_release_info(release))
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    msg = "Aucune Release publique n'a été trouvée. Vérifie que FUTMNG est public et qu'une Release est publiée."
                else:
                    msg = f"GitHub a répondu avec l'erreur HTTP {exc.code}."
                self.after(0, lambda m=msg: self._update_error(m))
            except Exception as exc:
                self.after(0, lambda e=exc: self._update_error(f"Impossible de vérifier les mises à jour.\n\n{e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_update_check(self):
        self._update_check_running = False
        self.update_button.config(text="↻  MISE À JOUR", state="normal")

    def _update_error(self, message: str):
        self._finish_update_check()
        self.status_var.set(f"FUTMNG v{APP_VERSION} • vérification GitHub impossible")
        messagebox.showerror("Mise à jour FUTMNG", message)

    def _handle_release_info(self, release: dict):
        self._finish_update_check()
        tag = str(release.get("tag_name") or release.get("name") or "0")
        latest = tag.lstrip("vV")
        if self._version_tuple(latest) <= self._version_tuple(APP_VERSION):
            self.status_var.set(f"FUTMNG v{APP_VERSION} • à jour ✓")
            messagebox.showinfo("Mise à jour FUTMNG", f"FUTMNG est déjà à jour.\n\nVersion installée : {APP_VERSION}\nDernière version : {latest}")
            return

        asset = None
        for item in release.get("assets") or []:
            name = str(item.get("name") or "")
            lname = name.lower()
            if lname.endswith(".zip") and (lname.startswith("futmng") or "futmng-github" in lname):
                asset = item
                break
        if asset is None:
            messagebox.showwarning(
                "Mise à jour FUTMNG",
                f"La version {latest} existe, mais aucun ZIP FUTMNG n'est attaché à la Release.\n\nAjoute FUTMNG-GitHub-v{latest}.zip dans les fichiers de la Release.",
            )
            self.status_var.set(f"Mise à jour {latest} disponible • ZIP manquant")
            return

        body = str(release.get("body") or "").strip()
        excerpt = body[:700] + ("…" if len(body) > 700 else "")
        prompt = f"Une nouvelle version de FUTMNG est disponible.\n\nInstallée : {APP_VERSION}\nDisponible : {latest}"
        if excerpt:
            prompt += f"\n\nNouveautés :\n{excerpt}"
        prompt += "\n\nTélécharger et installer maintenant ?"
        if messagebox.askyesno("Mise à jour FUTMNG", prompt):
            self._download_update(asset, latest)
        else:
            self.status_var.set(f"FUTMNG v{APP_VERSION} • mise à jour {latest} disponible")

    def _download_update(self, asset: dict, latest: str):
        url = str(asset.get("browser_download_url") or "")
        if not url:
            self._update_error("Le lien de téléchargement de la Release est introuvable.")
            return
        self.update_button.config(text="TÉLÉCHARGEMENT…", state="disabled")
        self.status_var.set(f"Téléchargement de FUTMNG v{latest}…")

        def worker():
            try:
                target = Path(tempfile.gettempdir()) / f"FUTMNG-update-v{latest}.zip"
                req = urllib.request.Request(url, headers={"User-Agent": f"FUTMNG/{APP_VERSION}"})
                with urllib.request.urlopen(req, timeout=30) as response, target.open("wb") as out:
                    total = int(response.headers.get("Content-Length") or 0)
                    done = 0
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 100 / total)
                            self.after(0, lambda p=pct: self.status_var.set(f"Téléchargement FUTMNG v{latest} : {p}%"))
                self.after(0, lambda: self._launch_updater(target, latest))
            except Exception as exc:
                self.after(0, lambda e=exc: self._update_error(f"Le téléchargement a échoué.\n\n{e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _launch_updater(self, zip_path: Path, latest: str):
        self.update_button.config(text="INSTALLATION…", state="disabled")
        install_root = Path(__file__).resolve().parent.parent
        helper = install_root / "updater" / "futmng_updater.py"
        if not helper.exists():
            self._update_error(f"Le module de mise à jour est introuvable :\n{helper}")
            return
        python_exe = sys.executable
        try:
            subprocess.Popen(
                [python_exe, str(helper), "--zip", str(zip_path), "--install-root", str(install_root), "--pid", str(os.getpid()), "--restart"],
                cwd=str(install_root),
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
            )
        except Exception as exc:
            self._update_error(f"Impossible de lancer l'installateur.\n\n{exc}")
            return
        self.status_var.set(f"Installation de FUTMNG v{latest}… fermeture de l'application.")
        self.after(300, self.destroy)

    def choose_server(self):
        folder = filedialog.askdirectory(title="Choisir le dossier « serveur fifa 17 »")
        if folder:
            self.load_server(Path(folder))

    def load_server(self, root: Path):
        # Accept either the exact server folder or a parent containing it.
        root = normalize_server_root(root)
        try:
            db = ServerDatabase(root)
            db.load()
        except Exception as exc:
            messagebox.showerror("FUTMNG", f"Impossible de charger le serveur.\n\n{exc}")
            self.status_var.set("Erreur de chargement du serveur.")
            return
        self.db = db
        self.refresh_list()
        self.status_var.set(f"Serveur : {root}  •  {len(db.cards):,} cartes détectées  •  lecture seule".replace(",", " "))

    def _matches_type(self, card: Card) -> bool:
        value = self.type_var.get()
        if value == "Tous les types":
            return True
        label = friendly_card_type(card)
        if value == "Base":
            return card.version == 0
        if value == "OR RARE":
            return card.version == 0 and label == "OR RARE"
        if value in {"LÉGENDES", "HALL OF FAME", "FLASHBACK", "OTW", "TOTW", "FONDATION"}:
            return label == value
        if value == "SBC":
            return label in {"SBC", "SBC PREMIUM"}
        if value == "Autres spéciales":
            known = {"LÉGENDES", "HALL OF FAME", "FLASHBACK", "OTW", "TOTW", "SBC", "SBC PREMIUM", "FONDATION"}
            return card.version > 0 and label not in known
        return True

    def refresh_list(self):
        if not self.db:
            return
        query = self.search_var.get().strip().lower()
        quality = self.quality_var.get().lower()
        obtain = self.obtain_var.get()
        result: List[Card] = []
        for card in self.db.cards:
            if query and query not in card.name.lower() and query not in str(card.asset_id) and query not in str(card.resource_id):
                continue
            if quality != "toutes qualités" and quality_group(card).lower() != quality:
                continue
            if not self._matches_type(card):
                continue
            acq = self.db.acquisition(card)
            if obtain == "Packable" and not acq.packable: continue
            if obtain == "SBC direct" and not acq.sbc_direct: continue
            if obtain == "Coupe" and not acq.cup_reward: continue
            if obtain == "Marché" and not acq.marketable: continue
            if obtain == "Exclusif" and not acq.exclusive: continue
            result.append(card)
        self.filtered = result
        self.tree.delete(*self.tree.get_children())
        for card in result:
            acq = self.db.acquisition(card)
            ctype = friendly_card_type(card)
            self.tree.insert("", "end", iid=str(card.resource_id), values=(
                card.rating, card.name, card.position, ctype[:18], "OUI" if acq.packable else "—",
                "OUI" if acq.sbc_direct else "—", "OUI" if acq.cup_reward else "—", card.resource_id
            ))
        self.status_var.set(f"{len(result):,} résultat(s) • {len(self.db.cards):,} cartes chargées • lecture seule".replace(",", " "))

    def on_select(self, _event=None):
        if not self.db:
            return
        sel = self.tree.selection()
        if not sel:
            return
        card = self.db.by_resource.get(int(sel[0]))
        if not card:
            return
        self.current_card = card
        acq = self.db.acquisition(card)
        self.title_label.config(text=card.name)
        self.subtitle_label.config(text=f"Asset ID {card.asset_id}  •  Resource ID {card.resource_id}  •  source {card.source}")
        self.rating_big.config(text=str(card.rating or "--"))
        self.position_big.config(text=f"{card.position or '—'}   •   {friendly_card_type(card)}")
        labels = ["PAC", "SHO", "PAS", "DRI", "DEF", "PHY"]
        attrs = card.attributes[:6]
        self.stats_label.config(text="\n".join(f"{lab:<3}  {attrs[i] if i < len(attrs) else '--':>3}" for i, lab in enumerate(labels)))

        self.current_photo = None
        img = self._local_head_path(card)
        image_ok = bool(img and self._display_head(img))

        # Special cards can share the player's assetId. If only the base portrait
        # is available locally, show it as a temporary fallback but continue in
        # the background looking for p<resourceId>.png/.dds on GitHub.
        exact_resource_image = self._is_resource_head(card, img)
        is_special_resource = int(card.resource_id or 0) != int(card.asset_id or 0)
        if image_ok and is_special_resource and not exact_resource_image:
            self._download_head_async(card, resource_only=True, keep_current=True)
        elif not image_ok:
            self.portrait_label.config(image="", text="CHARGEMENT\nDU VISAGE…", width=22, height=11)
            self._download_head_async(card)
        self._render_badges(card, acq, image_ok)

        lines = [
            f"Qualité : {friendly_quality(card)}    Type : {friendly_card_type(card)}    Version : {card.version}    Rare flag : {card.rare_flag}",
            f"Club ID : {card.team_id}    Ligue ID : {card.league_id}    Nation ID : {card.nation}",
            "",
            "OBTENTION",
            f"• Packable actuellement : {'OUI' if acq.packable else 'NON'}",
        ]
        if acq.pack_names:
            lines.append("  Packs : " + ", ".join(acq.pack_names))
        lines.append(f"• SBC direct : {'OUI' if acq.sbc_direct else 'NON'}")
        if acq.sbc_names:
            lines.append("  SBC : " + ", ".join(acq.sbc_names))
        if acq.sbc_pack_possible:
            lines.append("• Peut aussi sortir du pack joueur TOTW garanti du SBC hebdomadaire.")
        lines.append(f"• Récompense de coupe : {'OUI' if acq.cup_reward else 'NON'}")
        if acq.cup_names:
            lines.append("  Coupe : " + ", ".join(acq.cup_names))
        lines.append(f"• Marché normal : {'OUI' if acq.marketable else 'NON'}")
        if acq.notes:
            lines += ["", "NOTES"] + ["• " + n for n in acq.notes]
        if img:
            lines += ["", f"Portrait cache : {img.name}"]
        elif card.version == 0:
            lines += ["", "Portrait : les joueurs normaux utilisent Frosty dans ton serveur ; aucun PNG cache n'est fourni pour cette carte."]

        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", "\n".join(lines))
        self.details_text.configure(state="disabled")

# ----------------------------- Detection ----------------------------------

def normalize_server_root(path: Path) -> Path:
    path = path.resolve()
    candidates = [
        path,
        path / "serveur fifa 17",
        path / "serveur 17" / "serveur fifa 17",
    ]
    for c in candidates:
        if (c / "data" / "fifa17-card-catalog.json").exists() and (c / "fut-backend.js").exists():
            return c
    return path


def autodetect_server_root(script_dir: Path) -> Optional[Path]:
    search_roots = [script_dir, script_dir.parent, script_dir.parent.parent, Path.cwd()]
    seen = set()
    for root in search_roots:
        try:
            root = root.resolve()
        except Exception:
            continue
        if root in seen:
            continue
        seen.add(root)
        exact = normalize_server_root(root)
        if (exact / "data" / "fifa17-card-catalog.json").exists() and (exact / "fut-backend.js").exists():
            return exact
        # One-level sibling discovery only, to stay fast and predictable.
        try:
            for child in root.iterdir():
                if child.is_dir() and child.name.lower() == "serveur fifa 17":
                    exact = normalize_server_root(child)
                    if (exact / "data" / "fifa17-card-catalog.json").exists():
                        return exact
        except Exception:
            pass
    return None


def main():
    initial = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    app = FUTMNGApp(initial)
    app.mainloop()


if __name__ == "__main__":
    main()
