"""
read_deck.py — turns a deck of 8 cards into a spread.

    # no LLM: instant, free, plain assembly from the fragment pool
    python read_deck.py --raw "Hog Rider" Balloon Lumberjack Skeletons \\
        Musketeer Fireball "The Log" Cannon

    # with an LLM: the same fragments, stitched into flowing prose
    python read_deck.py "Hog Rider" Balloon ... Cannon

Determinism: a finished spread is cached under the deck's hash, and the order
of the cards does not matter. The second person with the same deck gets the
same text instantly and for free. A model does not repeat itself word for word
even at temperature 0, so determinism rests on the cache, not on a seed.
"""

import argparse, json, os, random, re, sys, time
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oracle_rules as O
import themes as TH
from oracle_rules import at
from build_fragments import load_env, sleep_i, LLM, DEFAULT_MODEL

MODEL_DEFAULT = "gemini-3.7-flash"

STITCH = """You are a tarot reader delivering a spread. You will be given the \
raw fragments of a reading: positions, the figure drawn at each, and the omens.

Rewrite them into one flowing reading in English. Keep every fragment's meaning \
and every named figure; you are smoothing prose, not inventing content. Do not \
add predictions that are not in the fragments. Do not drop a position.

Structure: an opening line, then Part I (what the cards say about the reader), \
then Part II (what to beware), then a single closing line. Use the position \
names as given. 250-400 words total.

Voice: second person, present tense, calm and certain. Never mention Clash \
Royale, elixir, decks, statistics or game mechanics by name. Never insult the \
reader — a hostile opinion is rendered as a shadow to acknowledge, never as a \
verdict. Plain prose, no markdown headers beyond "I." and "II.".
"""


def _opinions(f, limit=3):
    """Community opinions, already turned into statements about the person.
    These are the backbone of the reading, so take several (up to limit),
    heaviest first, rather than only the top one."""
    com = f.get("community") or []
    if isinstance(com, dict):
        com = [com]
    out = []
    for t in sorted(com, key=lambda x: -int(x.get("posts") or 0)):
        if not (t.get("reading") or t.get("trait")):
            continue
        out.append({"reading": t.get("reading", ""),
                    "shadow": t.get("shadow", ""),
                    "trait": t.get("trait", ""),
                    "quote": " ".join((t.get("quote") or "").split()[:15]),
                    "link": t.get("link", ""),
                    "polarity": t.get("polarity", ""),
                    "posts": t.get("posts", "")})
        if len(out) >= limit:
            break
    return out


def load_fragments(path):
    if not os.path.isfile(path):
        sys.exit(f"no fragment pool at {path}\n"
                 "run this first: python build_fragments.py --out fragments.json")
    return json.load(open(path, encoding="utf-8"))


# position -> which fragment field to show for it
FIELD_FOR = {
    "ambition": "as_ambition",
    "burden": "as_burden",
    "everyday_self": "as_everyday",
    "wall": "essence",
    "hidden_knowledge": "essence",
    "well_trodden": "essence",
    "outsider": "essence",
}

# what to say when a position stays empty; the absence is itself a reading
EMPTY_POSITION = {
    "wall": "You have built nothing to stand behind. Whatever comes, you meet "
            "it in the open.",
    "hidden_knowledge": "Nothing here is hidden. You hold no answer in reserve, "
                        "and everything you are is already on the table.",
    "ambition": "Nothing in this spread is reaching for anything. You are "
                "defending a position you have not yet chosen.",
}


def compose(analysis, frags, cards, pool):
    """
    Assemble a spread. No network, fully deterministic.

    Part I  — the figures in their positions, then the themes the hand carries.
    Part II — warnings, built ONLY from what players said about facing this
              kind of hand. When the crowd said nothing, part II is absent:
              the mechanics no longer fill that silence.
    """
    F, R = frags.get("cards", {}), frags.get("rules", {})
    missing = []
    parts = OrderedDict()

    # ── part I: the positions
    blocks = []
    for key, info in analysis["positions"].items():
        name = info["card"]
        if not name:
            if key in EMPTY_POSITION:
                blocks.append({"position": info["label"], "card": None,
                               "text": EMPTY_POSITION[key]})
            continue
        f = F.get(name)
        if not f:
            missing.append(name)
            continue
        text = f.get(FIELD_FOR.get(key, "essence")) or f.get("essence") or ""
        # No community voices here. A label belongs to a theme, and a theme
        # is said once for the whole hand — otherwise the same sentence lands
        # under every card that carries it, which is what the crowd's repeats
        # looked like before the theme layer existed.
        blocks.append({"position": info["label"], "card": name, "text": text,
                       "voices": _opinions(f, 1)})

    # The beloved-yet-failing contradiction rides on its own card.
    for p in analysis["cwr_paradox"]:
        f = F.get(p["card"])
        if f and f.get("caution"):
            for b in blocks:
                if b["card"] == p["card"]:
                    b["paradox"] = f["caution"]
                    break

    # ── pairings: every trigger must be in the deck, longest first
    deck_set = set(analysis["deck"])
    used = set()
    for name, af in sorted(frags.get("archetypes", {}).items(),
                           key=lambda kv: (-len(kv[1].get("triggers", [])), kv[0])):
        trig = af.get("triggers") or []
        if len(trig) < 2 or not set(trig) <= deck_set or set(trig) & used:
            continue
        used |= set(trig)
        blocks.append({"position": "The Pairing", "card": name, "cards": trig,
                       "text": af.get("reading", ""),
                       "paradox": af.get("caution") or None})

    # A pairing already says what its cards say, and says it better. When the
    # hand holds one, the single-card positions are dropped — otherwise the
    # same two cards are read twice, apart and together.
    pair_blocks = [b for b in blocks if b["position"] == "The Pairing"]
    if pair_blocks:
        blocks = pair_blocks

    sh = analysis["shadow"]
    if sh:
        f = F.get(sh)
        if f:
            blocks.append({"position": "The Shadow", "card": sh,
                           "text": f.get("as_shadow") or f.get("essence") or ""})
        else:
            missing.append(sh)
    parts["part_i"] = blocks

    # ── part I: the themes this hand carries, community and mechanics together
    deck = analysis["deck"]
    total = len(pool)
    community = TH.weigh(frags.get("themes", []), deck, cards, total)
    mech = TH.weigh(TH.mechanical_themes(analysis, cards, R), deck, cards,
                    total, mechanical=True)
    chosen = TH.select(community, mech)
    said = {" ".join((b.get("text") or "").lower().split()) for b in blocks}
    said |= {" ".join((b.get("paradox") or "").lower().split()) for b in blocks}
    clean_themes = []
    for t in chosen:
        t = dict(t)
        for field in ("reading", "shadow"):
            key = " ".join((t.get(field) or "").lower().split())
            if key and key in said:
                t[field] = ""              # already said above, drop the echo
            elif key:
                said.add(key)
        if t.get("reading") or t.get("shadow"):
            clean_themes.append(t)
    parts["themes"] = clean_themes

    # Warnings are computed but not shown: the reading is now a few paragraphs
    # of prose, and a list of enemies broke that shape. The data stays here so
    # the section can come back without another extraction run.
    gthemes = TH.grievance_themes(frags.get("grievances", []), deck, cards,
                                  frags.get("threats", {}))
    parts["warnings"] = TH.select_warnings(
        TH.weigh_grievances(gthemes, cards, total))

    return {"deck": deck, "deck_hash": analysis["deck_hash"], "parts": parts,
            "missing_fragments": sorted(set(missing)),
            "generated_from": frags.get("meta", {}).get("generated_at")}


# ── turning a community label into a sentence 
# The labels are third-person predicates: "relies on crutches", "lacks
# defensive skill". Dropped after "call you" they read as broken English,
# so a verb phrase is put into the second person and anything else is
# quoted instead of being forced into an article it does not fit
# ("an annoying", "a highest skill ceiling").

_IRREG = {"is": "are", "has": "have", "does": "do", "was": "were",
          "relies": "rely", "cries": "cry", "tries": "try", "flies": "fly",
          "denies": "deny", "carries": "carry", "goes": "go"}
# bare forms that appear with a plural subject: "lack basic game knowledge"
_BASE = {"lack", "play", "spam", "require", "act", "rely", "use", "get", "give",
         "win", "make", "put", "throw", "refuse", "overcommit", "spend",
         "encourage", "need", "know", "want", "think", "look", "go", "do",
         "have", "are", "cry", "flex"}
_NOT_VERB = ("ss", "us", "ous", "less", "ness", "ics")
_PRON = {"their": "your", "his": "your", "her": "your", "its": "your",
         "them": "you", "they": "you"}

_OPENINGS = ["Others might say you {p}",
             "There are those who say you {p}",
             "You have been told you {p}"]
_NAMINGS = ["Others have a word for it: {l}",
            "There is a name they give this: {l}",
            "You have heard it called this: {l}"]
_QUOTE_LEADS = ["They may try to unsettle your balance with words like {q}",
                "They say it in words like {q}",
                "Their words for it run like {q}"]


def _to_second_person(w):
    w = w.lower()
    if w in _IRREG:
        return _IRREG[w]
    if w in _BASE:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if re.search(r"(ss|sh|ch|x|z)es$", w):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _is_verb(w):
    w = w.lower().strip(",")
    if w in _IRREG or w in _BASE:
        return True
    if not re.fullmatch(r"[a-z']+s", w):
        return False
    return not any(w.endswith(s) for s in _NOT_VERB)


def _second_person(label):
    out, convert = [], True
    for w in label.split():
        low = w.lower()
        if convert and _is_verb(low):
            out.append(_to_second_person(low))
            convert = False
        elif low in ("and", "or", "but"):
            out.append(low)
            convert = True
        else:
            out.append(_PRON.get(low, low))
    return " ".join(out)


def crowd_line(label, quotes, variant=0):
    """One sentence naming what the crowd said, plus their own words."""
    label = (label or "").strip()
    if not label:
        return ""
    head = label.split()[0]
    if _is_verb(head):
        line = _OPENINGS[variant % len(_OPENINGS)].format(p=_second_person(label))
    else:
        line = _NAMINGS[variant % len(_NAMINGS)].format(l=label)
    qs = [q for q in (quotes or []) if q][:2]
    if qs:
        joined = " and ".join('\u201c' + q + '\u201d' for q in qs)
        line += ". " + _QUOTE_LEADS[variant % len(_QUOTE_LEADS)].format(q=joined)
    return line + "."


def render_raw(comp):
    """Readable text with no LLM. Doubles as the input for stitching."""
    out, vi = [], 0
    for b in comp["parts"]["part_i"]:
        if b.get("card"):
            out.append(b["card"])
        if b.get("text"):
            out.append(b["text"])
        if b.get("paradox"):
            out.append(b["paradox"])
        v = (b.get("voices") or [None])[0]
        if v and (v.get("trait") or v.get("quote")):
            out.append(crowd_line(v.get("trait", ""), [v.get("quote")], vi))
            vi += 1
        out.append("")

    for t in comp["parts"].get("themes") or []:
        if t.get("reading"):
            out.append(t["reading"])
        if t.get("shadow"):
            out.append(t["shadow"])
        if t.get("source") == "community" and t.get("label"):
            out.append(crowd_line(t["label"],
                                  [v.get("quote") for v in (t.get("voices") or [])],
                                  vi))
            vi += 1
        out.append("")
    return "\n".join(out).rstrip()


def stitch(raw_text, provider, model, cache_dir, deck_hash, env_file=None):
    """Stitching is the only place the reader touches a model at all.
    The result is cached by deck hash, so repeats cost nothing."""
    os.makedirs(cache_dir, exist_ok=True)
    p = os.path.join(cache_dir, deck_hash + ".txt")
    if os.path.exists(p):
        print(f"# from the spread cache: {p}", file=sys.stderr)
        return open(p, encoding="utf-8").read()

    load_env(env_file)
    llm = LLM(provider, model or DEFAULT_MODEL[provider], temperature=0.9)
    txt = llm.text(STITCH, "Fragments:\n\n" + raw_text)
    if not txt:
        print("stitching failed — returning the raw assembly", file=sys.stderr)
        return raw_text
    open(p, "w", encoding="utf-8").write(txt)
    return txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("deck", nargs="+", help="8 card names")
    ap.add_argument("--cards", default=at("data", "cards.csv"))
    ap.add_argument("--fragments", default=at("data", "fragments.json"))
    ap.add_argument("--cache", default=at(".cache", "readings"))
    ap.add_argument("--provider", default="gemini", choices=["gemini", "openai"])
    ap.add_argument("--model")
    ap.add_argument("--raw", action="store_true", help="assemble without an LLM")
    ap.add_argument("--json", action="store_true", help="print the structure instead of prose")
    ap.add_argument("--env-file")
    a = ap.parse_args()

    cards = O.load_cards(a.cards)
    pool = O.playable(cards)
    analysis = O.analyse(a.deck, cards, pool)

    if analysis["unknown_cards"]:
        print("not in the database: " + ", ".join(analysis["unknown_cards"]),
              file=sys.stderr)
        near = [c for c in pool if any(
            u.lower() in c.lower() for u in analysis["unknown_cards"])]
        if near:
            print("  close matches: " + ", ".join(near[:8]), file=sys.stderr)
        sys.exit(1)
    if not analysis["size_ok"]:
        print(f"deck has {len(analysis['deck'])} cards, expected {O.DECK_SIZE}",
              file=sys.stderr)

    comp = compose(analysis, load_fragments(a.fragments), cards, pool)
    if comp["missing_fragments"]:
        print("no fragments for: " + ", ".join(comp["missing_fragments"]),
              file=sys.stderr)

    if a.json:
        print(json.dumps({"analysis": analysis, "composed": comp},
                         ensure_ascii=False, indent=2))
        return

    raw = render_raw(comp)
    print(raw if a.raw else stitch(raw, a.provider, a.model, a.cache,
                                   comp["deck_hash"], a.env_file))
    print(f"\n[{comp['deck_hash']}] snapshot {comp['generated_from']}",
          file=sys.stderr)


if __name__ == "__main__":
    main()