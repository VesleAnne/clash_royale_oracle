"""
subjects_map.py — maps subject names from the Reddit export onto the card database.

The community writes however it likes: "Hog", "elixir golem", "X-Box",
"mega knight hog rider", "log bait". Each name ends up as one of:
  card       — a card straight out of cards.csv
  archetype  — a pairing whose trigger cards are known
  skip       — not a deck at all (a context, not a spread)
  unresolved — not mapped; needs a hand-written line in MANUAL

An archetype's triggers are the cards whose presence makes the engine
recognise the pairing in a deck. Some are read out of the name itself
("mega knight hog rider"); the rest are written by hand, because nothing in
the words "log bait" tells you what is in it.
"""

import csv, json, os, re, sys

# Project layout: this file lives in <root>/src, data in <root>/data.
# Paths are anchored to the project root, so the scripts work the same whether
# you run them from the root or from inside src/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def at(*parts):
    return os.path.join(ROOT, *parts)


# Names that cannot be derived from the name itself. Values:
#   a string  -> the same card under another name
#   a list    -> an archetype and its trigger cards
#   None      -> not a deck at all (a context); no fragment is made for it
MANUAL = {
    # card aliases
    "Hog": "Hog Rider",
    "X-Box": "X-Bow",
    "elixir golem": "Elixir Golem",
    "gob giant": "Goblin Giant",
    "mega PEKKA": "Mega Knight",
    "Ronin": None,                     # no such card in the database
    "Evolutions": None,                # neither a card nor a deck
    # archetypes whose contents are written by hand
    "Lumberloon": ["Lumberjack", "Balloon"],
    "log bait": ["Goblin Barrel", "Princess", "Goblin Gang"],
    "hyper bait": ["Goblin Barrel", "Princess"],
    "mortar bait": ["Mortar", "Goblin Barrel"],
    "xbow 3.0": ["X-Bow", "Tesla"],
    "hog cycle": ["Hog Rider", "Skeletons", "Ice Spirit"],
    "Hog 2.6": ["Hog Rider", "Musketeer", "Cannon", "Ice Spirit"],
    "2.6 Hog": ["Hog Rider", "Musketeer", "Cannon", "Ice Spirit"],
    "bowler-ebarbs": ["Bowler", "Elite Barbarians"],
    "Hog Firecracker": ["Hog Rider", "Firecracker"],
    # names that auto-extraction gets WRONG, not just misses:
    "hog freeze": ["Hog Rider", "Freeze"],          # "hog" is not a card name
    "sparky gob giant": ["Sparky", "Goblin Giant"], # "gob giant" -> Giant otherwise
    "Mortar Bait": ["Mortar", "Goblin Barrel"],
    "Rocket Cycle": ["Rocket", "Skeletons"],
    "hog, pekka, mega knight and goblin barrel spam decks":
        ["Hog Rider", "P.E.K.K.A.", "Mega Knight", "Goblin Barrel"],
    "Balloon Freeze": ["Balloon", "Freeze"],
    "Freeze + Balloon": ["Balloon", "Freeze"],
    "bowler freeze balloon": ["Bowler", "Freeze", "Balloon"],
    "Elixir Golem and Mega Knight": ["Elixir Golem", "Mega Knight"],
    "bridge spam": ["Bandit", "Royal Ghost", "Battle Ram"],
    "2.6": ["Hog Rider", "Musketeer", "Cannon", "Ice Spirit"],
    "PEKKA": "P.E.K.K.A.",
    # short forms and misspellings the community uses in grievance posts
    "MK": "Mega Knight", "mk": "Mega Knight", "megaknight": "Mega Knight",
    "Evo mk": "Mega Knight", "evo mk": "Mega Knight", "Evo MK": "Mega Knight",
    "RG": "Royal Giant", "rg": "Royal Giant",
    "E gaint": "Electro Giant", "egiant": "Electro Giant",
    "e-giant": "Electro Giant", "E giant": "Electro Giant",
    "evo witch": "Witch", "Evo Witch": "Witch",
    "evo executioner": "Executioner", "evo firecracker": "Firecracker",
    "hero valk": "Valkyrie", "hero wizard": "Wizard",
    "loon": "Balloon", "gy": "Graveyard", "valk": "Valkyrie",
    "ewiz": "Electro Wizard", "e wiz": "Electro Wizard",
    "electro wizard": "Electro Wizard", "ice golem": "Ice Golem",
    "ronin": None, "Ronin": None,          # not in the card database
    # ── names seen only in grievance posts
    "ebarbs": "Elite Barbarians", "recruits": "Royal Recruits",
    "Rascals": "Rascal Boy", "rascals": "Rascal Boy",
    "evo valk": "Valkyrie", "electrogiant": "Electro Giant",
    "mini PEKA": "Mini P.E.K.K.A.", "mini peka": "Mini P.E.K.K.A.",
    "Drill decks": "Goblin Drill", "drill decks": "Goblin Drill",
    "hogs deck": "Hog Rider", "hogs": "Hog Rider",
    "3m deck": "Three Musketeers", "3m": "Three Musketeers",
    "OG splashyard": "Graveyard", "splashyard": "Graveyard",
    "classic logbait": ["Goblin Barrel", "Princess", "Goblin Gang"],
    "logbait": ["Goblin Barrel", "Princess", "Goblin Gang"],
    "hyperbait": ["Goblin Barrel", "Princess"],
    "Lumberloon bait": ["Lumberjack", "Balloon", "Goblin Barrel"],
    "lumberloon bait": ["Lumberjack", "Balloon", "Goblin Barrel"],
    "2.9 hogs AQ": ["Hog Rider", "Archer Queen"],
    "Hogs-Recruits": ["Royal Hogs", "Royal Recruits"],
    "recruits piggies": ["Royal Recruits", "Royal Hogs"],
    "megaknight hogrider": ["Mega Knight", "Hog Rider"],
    "pekka bridge spam": ["P.E.K.K.A.", "Bandit", "Royal Ghost"],
    "PEKKA Bridge Spam": ["P.E.K.K.A.", "Bandit", "Royal Ghost"],
    "Pekka BS": ["P.E.K.K.A.", "Bandit", "Royal Ghost"],
    "Goblin": "Goblins",                # the community writes it singular
    "Marcher": "Magic Archer",          # a nickname, not a card name

    "Golem beatdown": "Golem",
    "2.6 hog": ["Hog Rider", "Musketeer", "Cannon", "Ice Spirit"],
    "Recruits pigs": ["Royal Recruits", "Royal Hogs"],
    "Evo Firecracker": "Firecracker",
    "Evo Valkyrie": "Valkyrie",
    # not decks but contexts. No fragments are generated for these.
    "midladder": None, "Midladder": None, "midladder spam decks": None,
    "hero": None, "toxic and boring playstyle": None,
    "mid-ladder": None, "midladder spam decks ": None,
    "2v2": None, "players": None, "Ultimate Champion": None,
    "evo win condition": None, "tower troops": None,
    "midladder slop decks": None,
    # grievance victims that name no deck at all
    "my deck": None, "this deck": None, "the deck": None, "deck": None,
    "push": None, "melee card": None, "melee cards": None,
    "spam decks": None, "meta": None, "everyone": None,
    "mid ladder players": None, "mid-ladder players": None,
    # ── grievance sides that name a rank, a mood or nothing at all
    "mid ladder": None, "ladder": None, "ladder players": None,
    "trophy road players": None, "players at 7k trophies": None,
    "11k": None, "10yr vet": None, "level 10": None, "level 10 deck": None,
    "someone that's played for two weeks": None,
    "someone that\u2019s played for two weeks": None,
    "skilled opponents": None, "defensive players": None, "bait player": None,
    "original deck": None, "homemade deck": None, "homemade decks": None,
    "quick cycle deck": None, "cheap cycle deck": None,
    "decks to see what works": None, "Commons": None,
    "anti-midladder deck": None, "deck compositions": None,
    "deck principal dele": None, "Reddit community": None,
    "Other players": None, "heroes": None, "control decks": None,
    "tanks and big pushes": None, "Cycle/Bait/Hyperbait decks": None,
    # "beatdown" and "midladder crap" are CLASSES of deck, not fixed lists.
    # They need a "any N of these cards" trigger, which does not exist yet.
    "beatdown": None, "Beatdown": None, "beatdown deck": None,
    "beatdown decks": None, "classic beatdown player": None,
    "midladder crap": None, "midladder decks": None,
    # tower troops were removed from cards.csv, so their labels have no carrier
    "Dagger Duchess": None, "Tower Princess": None, "Cannoneer": None,
    "Royal Chef": None,
    "The Ronin": None,                  # not in the card database
    "meta": None, "meta decks": None, "cycle decks": None,
    "2v2 teammates": None,
}


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def extract_cards(subject, card_names):
    """Pull card names out of a string like 'mega knight hog rider'.
    Longer names are tried first so 'Hog Rider' beats 'Hog'."""
    t = " " + _norm(subject) + " "
    found = []
    for c in sorted(card_names, key=len, reverse=True):
        cl = _norm(c)
        if not cl:
            continue
        if re.search(r"\b" + re.escape(cl) + r"\b", t):
            found.append(c)
            t = t.replace(cl, " ")
    return found


def resolve(subject, card_names, manual=None):
    """-> ('card', name) | ('archetype', [cards]) | ('skip', None) | ('unresolved', [])"""
    manual = MANUAL if manual is None else manual
    low = {c.lower(): c for c in card_names}
    # The community does not agree on capitalisation: "golem"/"Golem",
    # "PEKKA"/"Pekka", "lumberloon"/"Lumberloon" are all the same subject.
    manual_low = {k.lower(): v for k, v in manual.items()}

    key = subject if subject in manual else (
        subject.lower() if subject.lower() in manual_low else None)
    if key is not None:
        v = manual[key] if key in manual else manual_low[key]
        if v is None:
            return ("skip", None)
        if isinstance(v, str):
            return ("card", v) if v in card_names else ("unresolved", [])
        present = [c for c in v if c in card_names]
        return ("archetype", present) if len(present) >= 2 else ("unresolved", present)

    if subject in card_names:
        return ("card", subject)
    if subject.lower() in low:
        return ("card", low[subject.lower()])

    found = extract_cards(subject, card_names)
    if len(found) >= 2:
        return ("archetype", found)
    if len(found) == 1:
        return ("card", found[0])
    return ("unresolved", [])


def load_traits(path, about="card"):
    """A traits CSV -> [{subject, trait, about, polarity, quote, link, posts}].

    Two files feed this: card_traits.csv holds what the community said about
    the CARDS, player_traits.csv what it said about the PEOPLE who play them.
    The `about` tag travels with each label so the generator can phrase them
    differently: a card label has to be turned into a trait, a player label
    already is one and only needs the insult taken out."""
    if not path or not os.path.isfile(path):
        return []
    out, skipped = [], [0]
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            s = (r.get("subject") or "").strip()
            if not s:
                continue
            # Hand review: put anything in a `skip` column to drop the label.
            # Leaving `example` empty keeps the label and drops only its quote.
            if (r.get("skip") or "").strip().lower() in ("1", "x", "y", "yes",
                                                         "true", "да", "+"):
                skipped[0] += 1
                continue
            out.append({
                "subject": s, "about": about,
                "trait": (r.get("trait") or "").strip(),
                "polarity": (r.get("polarity") or "").strip(),
                "quote": " ".join((r.get("example") or "").split()[:15]),
                "link": (r.get("example_link") or "").strip(),
                "posts": int(r.get("distinct_posts") or 0),
                "score": int(r.get("total_score") or 0),
            })
    if skipped[0]:
        print(f"# {os.path.basename(path)}: {skipped[0]} labels marked skip",
              file=sys.stderr)
    return out


def load_grievances(path, card_names, min_evidence_words=2):
    """
    grievances.csv -> ordered pairs (victim -> threat), both sides resolved
    against the card base.

    A victim or a threat may be an archetype, in which case it expands to the
    cards that make it up: a grievance voiced about "2.6" belongs to every
    card of that deck. Rows where either side resolves to nothing are kept in
    `unresolved` rather than dropped silently, so a name worth adding to
    MANUAL is visible instead of just missing.

    Rows marked `skip` in a hand-review column are dropped, as everywhere else.
    """
    if not path or not os.path.isfile(path):
        return {"pairs": [], "unresolved": [], "skipped": 0}
    pairs, unresolved, skipped = [], [], 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("skip") or "").strip().lower() in ("1", "x", "y", "yes",
                                                         "true", "да", "+"):
                skipped += 1
                continue
            victim = (r.get("complainant") or "").strip()
            threat = (r.get("threat") or "").strip()
            ev = (r.get("evidence") or "").strip()
            if not victim or not threat:
                continue
            if len(ev.split()) < min_evidence_words:
                unresolved.append((victim, threat, "quote too short"))
                continue
            kv, vv = resolve(victim, card_names)
            kt, vt = resolve(threat, card_names)
            if kv not in ("card", "archetype"):
                unresolved.append((victim, threat, f"victim {kv}"))
                continue
            if kt not in ("card", "archetype"):
                unresolved.append((victim, threat, f"threat {kt}"))
                continue
            vcards = [vv] if kv == "card" else list(vv)
            tcards = [vt] if kt == "card" else list(vt)
            pairs.append({
                "victim": victim, "victim_cards": vcards, "victim_kind": kv,
                "threat": threat, "threat_cards": tcards, "threat_kind": kt,
                "epithet": (r.get("epithet") or "").strip(),
                "evidence": ev,
                "score": int(r.get("score") or 0),
                "link": (r.get("link") or "").strip(),
                "post_id": (r.get("post_id") or "").strip(),
            })
    return {"pairs": pairs, "unresolved": unresolved, "skipped": skipped}


def load_subject_weights(path):
    """subjects.csv -> {subject: weights}: how much a subject is about the player."""
    if not path or not os.path.isfile(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            s = (r.get("subject") or "").strip()
            if s:
                out[s] = {"kind": (r.get("kind") or "").strip(),
                          "about_player": int(r.get("about_player") or 0),
                          "about_card": int(r.get("about_card") or 0)}
    return out


def build_map(card_names, traits, weights, manual=None):
    """
    Builds two tables:
      by_card[card name]     = [community labels]
      archetypes[pair name]  = {triggers, traits, about_player}
    plus the list of names that could not be mapped.
    """
    by_card, archetypes, unresolved, skipped = {}, {}, [], []
    subjects = {t["subject"] for t in traits} | set(weights)
    by_subject = {}
    for t in traits:
        by_subject.setdefault(t["subject"], []).append(t)

    for s in sorted(subjects):
        kind, val = resolve(s, card_names, manual)
        ts = sorted(by_subject.get(s, []), key=lambda x: (-x["posts"], -x["score"]))
        w = weights.get(s, {})
        if kind == "card":
            by_card.setdefault(val, []).extend(ts)
        elif kind == "archetype":
            a = archetypes.setdefault(s, {"name": s, "triggers": val, "traits": [],
                                          "about_player": 0})
            a["traits"].extend(ts)
            a["about_player"] += w.get("about_player", 0)
        elif kind == "skip":
            skipped.append(s)
        else:
            unresolved.append(s)

    # "Hog 2.6" and "2.6 Hog" are the same pairing. Collapse by trigger set,
    # keeping the name the community used more often.
    merged = {}
    for name, a in archetypes.items():
        key = tuple(sorted(a["triggers"]))
        cur = merged.get(key)
        if cur is None or (a["about_player"], len(a["traits"])) > \
                (cur["about_player"], len(cur["traits"])):
            if cur:
                a["traits"] = a["traits"] + cur["traits"]
                a["about_player"] += cur["about_player"]
                a.setdefault("aliases", []).append(cur["name"])
            merged[key] = a
        else:
            cur["traits"] += a["traits"]
            cur["about_player"] += a["about_player"]
            cur.setdefault("aliases", []).append(name)
    archetypes = {a["name"]: a for a in merged.values()}

    for c, ts in by_card.items():
        ts.sort(key=lambda x: (-x["posts"], -x["score"]))
    return {"by_card": by_card, "archetypes": archetypes,
            "unresolved": unresolved, "skipped": skipped}


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default=at("data", "cards.csv"))
    ap.add_argument("--traits", default=at("data", "card_traits.csv"))
    ap.add_argument("--player-traits", default=at("data", "player_traits.csv"))
    ap.add_argument("--subjects", default=at("data", "subjects.csv"))
    ap.add_argument("--grievances", default=at("data", "grievances.csv"))
    a = ap.parse_args()
    names = [r["card"] for r in csv.DictReader(
        open(a.cards, newline="", encoding="utf-8-sig")) if r.get("card")]
    traits = (load_traits(a.traits, "card")
              + load_traits(a.player_traits, "player"))
    print(f"labels: {len(traits)} "
          f"(about cards {sum(1 for t in traits if t['about'] == 'card')}, "
          f"about players {sum(1 for t in traits if t['about'] == 'player')})")
    m = build_map(names, traits, load_subject_weights(a.subjects))
    print(f"cards with community labels : {len(m['by_card'])}")
    for c, ts in sorted(m["by_card"].items(), key=lambda x: -len(x[1]))[:10]:
        print(f"   {c:20} {', '.join(t['trait'] for t in ts)[:60]}")
    print(f"\narchetypes with a known make-up: {len(m['archetypes'])}")
    for n, a_ in sorted(m["archetypes"].items()):
        print(f"   {n:24} <- {', '.join(a_['triggers'])}")
    g = load_grievances(a.grievances, names)
    if g["pairs"] or g["unresolved"]:
        vict = {c for p in g["pairs"] for c in p["victim_cards"]}
        print(f"\ngrievances                  : {len(g['pairs'])} usable pairs, "
              f"{len(vict)} cards can be warned")
        if g["skipped"]:
            print(f"   marked skip              : {g['skipped']}")
        why = {}
        for v, t, reason in g["unresolved"]:
            why.setdefault(reason, []).append(f"{v} -> {t}")
        for reason, items in sorted(why.items()):
            print(f"   dropped, {reason:16} : {len(items)}  e.g. {items[0][:52]}")

    print(f"\nskipped (not decks)         : {len(m['skipped'])} — "
          f"{', '.join(m['skipped'])}")
    print(f"UNRESOLVED                  : {len(m['unresolved'])} — "
          f"{', '.join(m['unresolved'])}")
    if m["unresolved"]:
        print("   add them to MANUAL inside subjects_map.py")