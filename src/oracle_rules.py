"""
oracle_rules.py — all the spread logic. 

Three things are defined here and nothing else:
  POSITIONS  which roles exist and how a card is picked for each
  RULES      mechanical rules about what a deck is missing
  analyse()  takes 8 card names + the database and returns a breakdown

The breakdown is deterministic: one deck always yields the same result.
No prose lives here; that comes from the fragment pool.
"""

import csv, hashlib, json, os, re
from collections import OrderedDict

# Project layout: this file lives in <root>/src, data in <root>/data.
# Paths are anchored to the project root, so the scripts work the same whether
# you run them from the root or from inside src/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def at(*parts):
    return os.path.join(ROOT, *parts)


DECK_SIZE = 8

NUM_RX = re.compile(r"-?\d+(?:[.,]\d+)?")


def num(v, default=None):
    """'1,679' -> 1679.0 ; '1.20 (Melee: Medium)' -> 1.2 ; '' -> default"""
    if v is None:
        return default
    m = NUM_RX.search(str(v).replace(",", ""))
    return float(m.group()) if m else default


def load_cards(path):
    """cards.csv -> {name: row}. Rows without a name are dropped."""
    out = OrderedDict()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            name = (r.get("card") or "").strip()
            if name:
                out[name] = r
    return out


def playable(cards):
    """
    Cards a person can actually put in a deck.
    Dropped: tower troops (they occupy a separate slot) and spawn-only units,
    which have no price of their own.
    """
    out = OrderedDict()
    for name, r in cards.items():
        if (r.get("type") or "").strip().lower() == "tower troop":
            continue
        if r.get("in_main_comparison", "yes").strip().lower() == "no":
            continue
        if num(r.get("elixir_cost")) is None:
            continue        
        out[name] = r
    return out


def is_spell(r):
    return (r.get("type") or "").strip().lower() == "spell"


def is_building(r):
    return (r.get("type") or "").strip().lower() == "building"


def hits_air(r):
    t = (r.get("targets") or "").lower()
    return "air" in t


def is_air_unit(r):
    return (r.get("transport") or "").strip().lower() == "air"


def targets_buildings_only(r):
    t = (r.get("targets") or "").strip().lower()
    return t.startswith("building")


def is_splash(r):
    """
    Area damage. damage_kind is not filled in for every card, so spells and
    anything with an explicit radius count too.
    Returns True / False / None, where None means the data is missing.
    """
    dk = (r.get("damage_kind") or "").strip().lower()
    if dk:
        return dk.startswith("area")
    if is_spell(r):
        return True
    if num(r.get("radius")) is not None:
        return True
    return None


def speed_value(r):
    """'Medium (60)' -> 60.0 ; '' -> None. The number is the game's own scale."""
    return num(r.get("speed"))


def moves(r):
    """True if the card is a unit that walks or flies at all.
    Spells and buildings do not move, so they never count towards a speed rule."""
    return speed_value(r) is not None


def unit_count(r):
    n = num(r.get("count"))
    if n is None:
        n = num(r.get("count_comparison"))
    return int(n) if n else 1


def usage(r):
    return num(r.get("usage_pct"), None)


def winrate(r):
    return num(r.get("win_pct"), None)


# (key, label, which cards fit, how to rank them). Order matters: rarer
# positions claim their card first when two positions want the same one.
POSITIONS = [
    ("wall",            "The Wall",
     lambda r: is_building(r) and not is_spell(r),
     lambda r: (num(r.get("hitpoints"), 0), -num(r.get("elixir_cost"), 0))),
    ("hidden_knowledge", "Hidden Knowledge",
     is_spell,
     lambda r: (num(r.get("radius"), 0), num(r.get("elixir_cost"), 0))),
    ("ambition",        "The Ambition",
     lambda r: targets_buildings_only(r) and not is_spell(r),
     lambda r: (num(r.get("elixir_cost"), 0),)),
    ("burden",          "The Burden",
     lambda r: True,
     lambda r: (num(r.get("elixir_cost"), 0), num(r.get("hitpoints"), 0))),
    ("everyday_self",   "The Everyday Self",
     lambda r: True,
     lambda r: (-num(r.get("elixir_cost"), 99), num(r.get("usage_pct"), 0))),
    ("well_trodden",    "The Well-Trodden Path",
     lambda r: usage(r) is not None,
     lambda r: (num(r.get("usage_pct"), 0),)),
    ("outsider",        "The Outsider",
     lambda r: usage(r) is not None,
     lambda r: (-num(r.get("usage_pct"), 99),)),
]


def assign_positions(deck, cards):
    """
    Hand out roles. A card takes at most one role, and rarer roles choose
    first — otherwise a spell that is also the cheapest card would swallow
    Hidden Knowledge. The sort key ends with the name so ties never depend on
    iteration order.
    """
    taken, out = set(), OrderedDict()
    for key, label, fits, rank in POSITIONS:
        best = None
        for name in deck:
            r = cards.get(name)
            if r is None or name in taken:
                continue
            if not fits(r):
                continue
            k = tuple(rank(r)) + (name,)
            if best is None or k > best[0]:
                best = (k, name)
        if best:
            taken.add(best[1])
            out[key] = {"position": key, "label": label, "card": best[1]}
        else:
            out[key] = {"position": key, "label": label, "card": None}
    return out


def shadow_card(deck, pool):
    """
    The most played card that is NOT in the deck.
    Tower troops and cards without usage are excluded: otherwise Tower Princess
    and her 90%+ would be everyone's shadow.
    """
    best = None
    for name, r in pool.items():
        if name in deck:
            continue
        u = usage(r)
        if u is None:
            continue
        k = (u, name)
        if best is None or k > best[0]:
            best = (k, name)
    return best[1] if best else None


def _r_no_air_defense(deck, cards):
    hitters = [n for n in deck if hits_air(cards[n])]
    return (not hitters), {"air_answers": hitters}


def _r_no_splash(deck, cards):
    yes = [n for n in deck if is_splash(cards[n]) is True]
    unknown = [n for n in deck if is_splash(cards[n]) is None]
    return (not yes), {"splash": yes, "unknown": unknown}


def _r_no_building(deck, cards):
    b = [n for n in deck if is_building(cards[n])]
    return (not b), {"buildings": b}


def _r_no_win_condition(deck, cards):
    w = [n for n in deck if targets_buildings_only(cards[n])]
    return (not w), {"win_conditions": w}


def _r_heavy(deck, cards):
    avg = avg_elixir(deck, cards)
    return (avg is not None and avg >= 4.2), {"avg_elixir": avg}


def _r_featherweight(deck, cards):
    avg = avg_elixir(deck, cards)
    return (avg is not None and avg <= 3.0), {"avg_elixir": avg}


def _r_all_ground(deck, cards):
    air = [n for n in deck if is_air_unit(cards[n])]
    return (not air), {"air_units": air}


def _r_swarm_heavy(deck, cards):
    """Many multi-unit cards -> the deck folds to area damage."""
    sw = [n for n in deck if unit_count(cards[n]) >= 3]
    return (len(sw) >= 3), {"swarms": sw}


def _r_no_cheap_cycle(deck, cards):
    cheap = [n for n in deck if (num(cards[n].get("elixir_cost")) or 99) <= 2]
    return (len(cheap) == 0), {"cheap": cheap}


def _r_all_slow(deck, cards):
    """Every moving figure in the deck is slow. Spells and buildings ignored."""
    movers = [n for n in deck if moves(cards[n])]
    if len(movers) < 3:
        return False, {"movers": movers}
    fast = [n for n in movers if speed_value(cards[n]) >= 90]
    return (not fast), {"movers": movers,
                        "slowest": min((speed_value(cards[n]) for n in movers),
                                       default=None)}


def _r_all_swift(deck, cards):
    """Most moving figures are fast — the deck never plans, it reacts."""
    movers = [n for n in deck if moves(cards[n])]
    if len(movers) < 3:
        return False, {"movers": movers}
    fast = [n for n in movers if speed_value(cards[n]) >= 90]
    return (len(fast) >= max(3, len(movers) - 1)), {"fast": fast,
                                                    "movers": movers}


def _r_tunnel_vision(deck, cards):
    """Many figures ignore everything except buildings: they never defend."""
    blind = [n for n in deck if targets_buildings_only(cards[n])]
    return (len(blind) >= 3), {"blind": blind}


def _r_meta_conformist(deck, cards):
    s = total_usage(deck, cards)
    return (s is not None and s >= 60), {"usage_sum": s}


def _r_meta_outsider(deck, cards):
    s = total_usage(deck, cards)
    return (s is not None and s <= 20), {"usage_sum": s}


# key, title, detector, and the figures that exploit this gap
RULES = [
    ("no_air_defense", "Nothing in this deck can strike the sky",
     _r_no_air_defense, ["Balloon", "Lava Hound", "Minion Horde", "Bats"]),
    ("no_splash", "Every blow here falls on one target",
     _r_no_splash, ["Skeleton Army", "Goblin Gang", "Bats", "Minion Horde"]),
    ("no_building", "You have raised no walls",
     _r_no_building, ["Hog Rider", "Royal Giant", "Balloon", "Ram Rider"]),
    ("no_win_condition", "Nothing here is hunting the towers",
     _r_no_win_condition, []),
    ("heavy", "Your hand is slow to open",
     _r_heavy, ["Hog Rider", "Miner", "Skeletons", "Ice Spirit"]),
    ("featherweight", "Nothing you hold can stop a mountain",
     _r_featherweight, ["Golem", "P.E.K.K.A.", "Mega Knight", "Electro Giant"]),
    ("all_ground", "You never leave the earth",
     _r_all_ground, []),
    ("swarm_heavy", "You trust in numbers",
     _r_swarm_heavy, ["Fireball", "Arrows", "Wizard", "Valkyrie", "Executioner"]),
    ("no_cheap_cycle", "You hold nothing you can spend freely",
     _r_no_cheap_cycle, []),
    ("all_slow", "Nothing you hold is in a hurry",
     _r_all_slow, ["Hog Rider", "Miner", "Bandit", "Royal Hogs"]),
    ("all_swift", "Everything you hold is already moving",
     _r_all_swift, ["P.E.K.K.A.", "Mega Knight", "Golem"]),
    ("tunnel_vision", "Most of what you hold looks past the living",
     _r_tunnel_vision, ["Skeleton Army", "Inferno Tower", "Mini P.E.K.K.A."]),
    ("meta_conformist", "You walk a well-trodden road",
     _r_meta_conformist, []),
    ("meta_outsider", "You walk where few have walked",
     _r_meta_outsider, []),
]


def avg_elixir(deck, cards):
    vals = [num(cards[n].get("elixir_cost")) for n in deck if n in cards]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def total_usage(deck, cards):
    vals = [usage(cards[n]) for n in deck if n in cards]
    vals = [v for v in vals if v is not None]
    return round(sum(vals), 1) if vals else None


def cwr_paradox(deck, cards):
    """Cards everyone plays that nevertheless lose more than they win."""
    out = []
    for n in deck:
        r = cards.get(n) or {}
        u, w = usage(r), winrate(r)
        if u is not None and w is not None and u >= 8 and w < 50:
            out.append({"card": n, "usage_pct": u, "win_pct": w})
    return sorted(out, key=lambda x: (-x["usage_pct"], x["card"]))


def deck_hash(deck):
    """Determinism key: independent of the order the cards were given in."""
    return hashlib.sha1("|".join(sorted(deck)).encode()).hexdigest()[:16]



def analyse(deck, cards, pool=None):
    deck = list(dict.fromkeys(deck))               # duplicates collapse quietly
    missing = [n for n in deck if n not in cards]
    known = [n for n in deck if n in cards]
    pool = pool if pool is not None else playable(cards)

    fired = []
    for key, title, fn, threats in RULES:
        hit, detail = fn(known, cards)
        if hit:
            present = [t for t in threats if t in pool]
            fired.append({"rule": key, "title": title, "detail": detail,
                          "threats": present})

    # confidence: how many of the deck's cards have damage_kind filled in
    dk_known = sum(1 for n in known if is_splash(cards[n]) is not None)

    return {
        "deck": deck,
        "deck_hash": deck_hash(deck),
        "unknown_cards": missing,
        "size_ok": len(deck) == DECK_SIZE,
        "positions": assign_positions(known, cards),
        "shadow": shadow_card(set(known), pool),
        "avg_elixir": avg_elixir(known, cards),
        "total_usage": total_usage(known, cards),
        "cwr_paradox": cwr_paradox(known, cards),
        "rules_fired": fired,
        "coverage": {"cards_in_db": len(known),
                     "splash_known": dk_known, "splash_total": len(known)},
    }


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default=at("data", "cards.csv"))
    ap.add_argument("deck", nargs="+")
    a = ap.parse_args()
    cards = load_cards(a.cards)
    print(json.dumps(analyse(a.deck, cards), ensure_ascii=False, indent=2))