"""
themes.py — the layer between single labels and the finished reading.

Three jobs, none of which needs a model at read time:

  cluster_labels()   group ~200 community labels into ~20 themes
  weigh()            score every theme against one deck
  select()           pick the themes the reading will show

Weighting, as decided:
  * a theme's weight is the sum over the DECK cards that carry it,
  * each card counts more the rarer it is (usage_pct),
  * each theme counts more the fewer cards in the whole base carry it,
  * how many people said it does NOT enter the weight — the voices stay
    inside the theme as evidence, not as force.

Selection: floating 3 to 5 themes, with a quota that guarantees the reading
never consists only of what the crowd said, nor only of what the mechanics
show — unless one of those sources is empty for this deck.
"""

import hashlib, json, math, os, random, re, sys, time
from collections import Counter, OrderedDict, defaultdict

# Embedding vectors are cached here so a quota error never throws away work
# that has already been paid for.
ROOT_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")


def _sleep_i(sec):
    """Sleep in slices so Ctrl-C lands during a long quota wait."""
    end = time.time() + sec
    while time.time() < end:
        time.sleep(min(0.5, end - time.time()))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oracle_rules as O

# A card at or above this usage counts as fully ordinary and gets no bonus.
# Chosen from the real distribution: median usage is 3%, only 14 of 129 cards
# are above 10%, so a cap at the top of the range would flatten everything.
USAGE_CAP = 20.0
RARITY_STRENGTH = 2.0          # a 0%-usage card weighs this much more than a 20% one

MIN_THEMES, MAX_THEMES = 3, 5
# A theme joins the reading beyond the third if it reaches this share of the
# strongest theme's weight. Keeps a uniform deck short and a motley deck long.
KEEP_RATIO = 0.45

STOP = set("a an the is are as on of to too and it its their they you your with "
           "for in at be have has do does no not so very just that this".split())


#  text vectors

def _tokens(text):
    """Crude stemming. Enough to collapse lack/lacks/lacking before any
    vector maths happens, which keeps the clusters cleaner and cheaper."""
    out = []
    for w in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split():
        if w in STOP or len(w) < 2:
            continue
        w = re.sub(r"(ings|ing|ies|ed|es|s|ly)$", "", w)
        out.append(w or None)
    return [w for w in out if w]


def _tfidf(texts):
    """Pure-stdlib TF-IDF. The fallback when no embedding model is available."""
    docs = [_tokens(t) for t in texts]
    df = Counter()
    for d in docs:
        df.update(set(d))
    n = len(docs)
    vecs = []
    for d in docs:
        tf = Counter(d)
        v = {w: (1 + math.log(c)) * math.log((n + 1) / (df[w] + 1))
             for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs


def _cos_sparse(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


LAST_MODE = {"mode": "not run"}


def _emb_cache_path(cache_dir, model, text):
    h = hashlib.sha1((model + "\x00" + text).encode("utf-8")).hexdigest()[:20]
    return os.path.join(cache_dir, h + ".json")


def _retry_delay(err):
    """Google returns the wait it wants in the error body. Honour it: guessing
    a shorter pause just burns another request against the same quota."""
    m = re.search(r"'?retryDelay'?\s*[:=]\s*'?(\d+(?:\.\d+)?)s", str(err))
    if m:
        return min(120.0, float(m.group(1)) + 1.0)
    return None


def embed_gemini(texts, model="gemini-embedding-001", cache_dir=None,
                 batch=32, tries=5, pace=0.4):
    """
    Real embeddings, if a key is around. Returns dense unit vectors, or None
    if the run could not be completed, so the caller falls back to TF-IDF.

    Vectors are cached on disk by text, which matters more than it sounds:
    a quota error halfway through used to throw away everything already paid
    for. Now a rerun picks up where it stopped.
    """
    cache_dir = cache_dir or os.path.join(ROOT_CACHE, "embeddings")
    os.makedirs(cache_dir, exist_ok=True)

    vectors, todo = {}, []
    for t in texts:
        p = _emb_cache_path(cache_dir, model, t)
        if os.path.exists(p):
            try:
                vectors[t] = json.load(open(p, encoding="utf-8"))
                continue
            except json.JSONDecodeError:
                os.remove(p)
        if t not in [x for x in todo]:
            todo.append(t)

    if not todo:
        LAST_MODE["mode"] = f"embeddings ({model}, all cached)"
        return [vectors[t] for t in texts]

    try:
        from google import genai
    except ImportError:
        _embed_failed("google-genai is not installed")
        return None
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        _embed_failed("no GEMINI_API_KEY / GOOGLE_API_KEY in the environment")
        return None
    client = genai.Client(api_key=key)

    print(f"# embeddings: {len(vectors)} cached, {len(todo)} to fetch "
          f"({model}, batches of {batch})", file=sys.stderr)
    done = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        last = None
        for attempt in range(tries):
            try:
                r = client.models.embed_content(model=model, contents=chunk)
                for t, e in zip(chunk, r.embeddings):
                    v = list(e.values)
                    n = math.sqrt(sum(x * x for x in v)) or 1.0
                    v = [x / n for x in v]
                    vectors[t] = v
                    json.dump(v, open(_emb_cache_path(cache_dir, model, t), "w",
                                      encoding="utf-8"))
                done += len(chunk)
                time.sleep(pace)                 # spread the load a little
                break
            except KeyboardInterrupt:
                raise
            except Exception as e:               # noqa: BLE001
                last = e
                txt = str(e).upper()
                quota = "429" in txt or "RESOURCE_EXHAUSTED" in txt
                # 403/401/400 will not heal with time: a bad key, a blocked
                # host, a wrong model name. Retrying only wastes the wait.
                permanent = any(c in txt for c in
                                ("403", "401", "400", "PERMISSION_DENIED",
                                 "UNAUTHENTICATED", "INVALID_ARGUMENT",
                                 "NOT_FOUND"))
                if permanent:
                    print(f"#   permanent error, not retrying: "
                          f"{str(e).splitlines()[0][:110]}", file=sys.stderr)
                    break
                if attempt == tries - 1:
                    break
                wait = _retry_delay(e) or min(90.0, 5 * 2 ** attempt)
                wait += random.uniform(0, 2)
                print(f"#   {'quota' if quota else type(e).__name__} on "
                      f"{i + 1}-{i + len(chunk)} of {len(todo)} — "
                      f"waiting {wait:.0f}s (try {attempt + 2}/{tries})",
                      file=sys.stderr)
                _sleep_i(wait)
        else:
            last = last or RuntimeError("exhausted retries")
        if len(vectors) < len(set(texts)) and last is not None and \
                any(t not in vectors for t in chunk):
            _embed_failed(f"{type(last).__name__}: {str(last).splitlines()[0][:150]}",
                          cached=len(vectors), total=len(set(texts)))
            return None

    LAST_MODE["mode"] = f"embeddings ({model})"
    return [vectors[t] for t in texts]


def _embed_failed(msg, cached=None, total=None):
    print("!" * 62)
    print(f"! EMBEDDINGS FAILED — {msg}")
    if cached is not None:
        print(f"! {cached} of {total} vectors are cached and will be reused;")
        print("! run the command again later and it will resume, not restart.")
    print("! falling back to word overlap (TF-IDF). Synonyms that share no")
    print("! words will NOT be merged: 'does not use brain' and 'plays")
    print("! mindlessly' will stay separate themes.")
    print("!" * 62)
    LAST_MODE["mode"] = "tfidf (embeddings failed)"


def _cos_dense(a, b):
    return sum(x * y for x, y in zip(a, b))


#  clustering

# Cosine lives on a different scale in each space: TF-IDF vectors of short
# phrases are nearly orthogonal, embeddings of the same phrases sit high.
# One threshold cannot serve both.
THRESHOLD = {"tfidf": 0.32, "embed": 0.80}


def cluster_labels(traits, threshold=None, use_embeddings=True):
    """
    Group labels that say the same thing.

    Polarity is a HARD partition, never crossed. Without it "requires no skill"
    and "requires high skill" land in the same cluster: they share almost every
    word, and negation barely moves a vector. This is the known weak spot of
    every similarity measure, embeddings included, and the polarity column
    fixes it for free.
    """
    buckets = defaultdict(list)
    for t in traits:
        buckets[(t.get("polarity") or "neutral").lower()].append(t)

    themes, tid = [], 0
    for polarity, group in sorted(buckets.items()):
        texts = [t["trait"] for t in group]
        dense = embed_gemini(texts) if use_embeddings else None
        if dense:
            vecs, cos, thr = dense, _cos_dense, (threshold or THRESHOLD["embed"])
        else:
            vecs, cos, thr = _tfidf(texts), _cos_sparse, (threshold or THRESHOLD["tfidf"])
            if LAST_MODE["mode"] == "not run":
                LAST_MODE["mode"] = "tfidf (embeddings off)"
        LAST_MODE["threshold"] = thr

        # Greedy agglomeration against cluster centroids. With a couple of
        # hundred short strings this is instant and, unlike k-means, needs no
        # guess about how many themes there are.
        members, centroids = [], []
        order = sorted(range(len(group)),
                       key=lambda i: -int(group[i].get("posts") or 0))
        for i in order:
            best, best_sim = None, 0.0
            for ci, c in enumerate(centroids):
                sim = cos(vecs[i], c)
                if sim > best_sim:
                    best, best_sim = ci, sim
            if best is not None and best_sim >= thr:
                members[best].append(i)
                centroids[best] = _merge(centroids[best], vecs[i], len(members[best]))
            else:
                members.append([i])
                centroids.append(vecs[i])

        for m in members:
            ms = [group[i] for i in m]
            ms.sort(key=lambda t: (-int(t.get("posts") or 0),
                                   -int(t.get("score") or 0)))
            tid += 1
            themes.append({
                "id": f"t{tid:03d}",
                "polarity": polarity,
                "label": ms[0]["trait"],          # loudest member names the theme
                "variants": sorted({t["trait"] for t in ms}),
                "members": ms,
                "source": "community",
            })
    themes.sort(key=lambda t: -len(t["members"]))
    return themes


def _merge(centroid, vec, count):
    """Running mean of a cluster, for both vector shapes."""
    w = (count - 1) / count
    if isinstance(centroid, dict):
        out = {k: v * w for k, v in centroid.items()}
        for k, v in vec.items():
            out[k] = out.get(k, 0.0) + v / count
        norm = math.sqrt(sum(x * x for x in out.values())) or 1.0
        return {k: v / norm for k, v in out.items()}
    out = [c * w + v / count for c, v in zip(centroid, vec)]
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


#  carriers

def attach_carriers(themes, by_card, archetypes):
    """
    Work out which CARDS carry each theme. A label about a pairing is carried
    by every card of that pairing, so 'log bait' reaches Goblin Barrel,
    Princess and Goblin Gang alike.
    """
    where = defaultdict(set)                      # (subject, trait) -> cards
    for card, ts in by_card.items():
        for t in ts:
            where[(t["subject"], t["trait"])].add(card)
    for name, a in archetypes.items():
        for t in a["traits"]:
            where[(t["subject"], t["trait"])] |= set(a["triggers"])

    for th in themes:
        cards = set()
        for m in th["members"]:
            cards |= where.get((m["subject"], m["trait"]), set())
        th["cards"] = sorted(cards)
    return themes


def theme_rarity(theme, total_cards):
    """A theme on three cards must outweigh a theme on forty. Otherwise the
    three biggest themes win every reading and every spread looks alike."""
    n = max(1, len(theme.get("cards") or []))
    return 1.0 + math.log(max(1.0, total_cards / n))


def rarity_bonus(row):
    """A rare card speaks louder. Cards with no usage figure get no bonus
    rather than the maximum one — absence of data is not rarity."""
    u = O.usage(row or {})
    if u is None:
        return 1.0
    return 1.0 + RARITY_STRENGTH * (1.0 - min(u, USAGE_CAP) / USAGE_CAP)


#  mechanical themes

# Which cards in a deck actually demonstrate a rule. An absence is shown by
# the whole hand, so those rules take the deck entire.
# How much each rule says about a PERSON, as opposed to about a hand of cards.
# An excess is specific and revealing; an absence is common and says less.
# Without this every absence rule carries the whole deck, so they all weigh
# exactly the same and get ordered alphabetically, which is no order at all.
RULE_WEIGHT = {
    "all_slow": 1.00, "all_swift": 1.00, "tunnel_vision": 0.95,
    "swarm_heavy": 0.90, "meta_outsider": 0.90, "meta_conformist": 0.75,
    "heavy": 0.70, "featherweight": 0.70,
    "no_win_condition": 0.65, "no_building": 0.55, "no_air_defense": 0.55,
    "no_splash": 0.50, "no_cheap_cycle": 0.45, "all_ground": 0.40,
}
DEFAULT_RULE_WEIGHT = 0.6

RULE_CARRIERS = {
    "all_slow":       lambda d, c, det: det.get("movers", []),
    "all_swift":      lambda d, c, det: det.get("fast", []),
    "swarm_heavy":    lambda d, c, det: det.get("swarms", []),
    "tunnel_vision":  lambda d, c, det: det.get("blind", []),
}


def mechanical_themes(analysis, cards, rule_text):
    """Turn the fired deck rules into themes of the same shape, so the
    selection step can weigh both kinds on one scale."""
    out = []
    for r in analysis["rules_fired"]:
        txt = (rule_text or {}).get(r["rule"])
        if not txt:
            continue
        carriers = RULE_CARRIERS.get(r["rule"], lambda d, c, det: list(d))(
            analysis["deck"], cards, r.get("detail") or {})
        out.append({
            "id": "m_" + r["rule"],
            "source": "mechanics",
            "polarity": "neutral",
            "label": r["title"],
            "reading": txt.get("omen", ""),
            "shadow": txt.get("counsel", ""),
            "cards": sorted(set(carriers)),
            "rule_weight": RULE_WEIGHT.get(r["rule"], DEFAULT_RULE_WEIGHT),
            "figures": r.get("threats") or [],
            "members": [],
        })
    return out


#  grievance themes

def grievance_themes(pairs, deck, cards, threat_text):
    """
    Turn resolved grievances into themes for the second half of the reading.

    A grievance fires when the VICTIM side is in the deck. The threat is what
    the reading warns about, and the epithet is what the person's own kind
    calls that threat — which is the whole point: the warning is voiced by
    players, not derived from a column.

    A threat that is also in the deck is dropped: warning someone about a card
    they are holding reads as nonsense.
    """
    deck_set = set(deck)
    by_threat = OrderedDict()
    for p in pairs:
        present = [c for c in p["victim_cards"] if c in deck_set]
        if not present:
            continue
        threats = [c for c in p["threat_cards"] if c not in deck_set]
        if not threats:
            continue                     # the threat is already in their hand
        key = p["threat"].strip().lower()
        t = by_threat.setdefault(key, {
            "id": "g_" + re.sub(r"[^a-z0-9]+", "_", key)[:40],
            "source": "grievance",
            "polarity": "negative",
            "label": p["threat"],
            "threat_cards": [],
            "cards": [],                 # the victim side: what makes it fire
            "voices": [],
            "members": [],
        })
        for c in threats:
            if c not in t["threat_cards"]:
                t["threat_cards"].append(c)
        for c in present:
            if c not in t["cards"]:
                t["cards"].append(c)
        t["voices"].append({"epithet": p.get("epithet", ""),
                            # the CSV loader calls it `evidence`, the baked
                            # fragments.json calls it `quote`
                            "quote": p.get("quote") or p.get("evidence") or "",
                            "link": p.get("link", ""),
                            "score": int(p.get("score") or 0),
                            "victim": p.get("victim") or ", ".join(
                                p.get("victim_cards") or [])})

    out = []
    for key, t in by_threat.items():
        t["voices"].sort(key=lambda v: -v["score"])
        t["posts"] = len({v["quote"] for v in t["voices"]})
        txt = (threat_text or {}).get(key) or (threat_text or {}).get(t["label"])
        if txt:
            t["reading"] = txt.get("reading", "")
            t["shadow"] = txt.get("shadow", "")
        t["cards"] = sorted(t["cards"])
        out.append(t)
    return out


def weigh_grievances(themes, cards, total_cards):
    """
    Weight is the victim side — the cards in the hand that make the warning
    apply — with the same rarity bonus as everywhere else, times how many
    separate voices said it. Unlike the community themes of part one, voices
    DO count here: a threat two people warned about is a better warning than
    one somebody mentioned once.
    """
    out = []
    for th in themes:
        if not th.get("cards") or not th.get("reading"):
            continue
        base = sum(rarity_bonus(cards.get(c)) for c in th["cards"])
        voices = 1.0 + math.log(1 + th.get("posts", 1))
        th = dict(th)
        th["weight"] = round(base * voices, 3)
        th["weight_parts"] = {"cards": len(th["cards"]), "base": round(base, 2),
                              "voices": round(voices, 2)}
        out.append(th)
    out.sort(key=lambda t: (-t["weight"], t["id"]))
    return out


#  weighing

def weigh(themes, deck, cards, total_cards, mechanical=False):
    """Score one theme against one deck. Deterministic, no randomness."""
    deck_set = set(deck)
    scored = []
    for th in themes:
        present = [c for c in (th.get("cards") or []) if c in deck_set]
        if not present:
            continue
        base = sum(rarity_bonus(cards.get(c)) for c in present)
        # A mechanical theme is evidenced by the hand rather than by a label
        # attached to particular cards, so its rarity factor is flat.
        tr = (th.get("rule_weight", DEFAULT_RULE_WEIGHT) if mechanical
              else theme_rarity(th, total_cards))
        th = dict(th)
        th["deck_cards"] = present
        th["weight"] = round(base * tr, 3)
        th["weight_parts"] = {"cards": len(present), "base": round(base, 2),
                              "theme_rarity": round(tr, 2)}
        scored.append(th)
    scored.sort(key=lambda t: (-t["weight"], t["id"]))
    return scored


def select(community, mechanics, min_n=MIN_THEMES, max_n=MAX_THEMES,
           keep_ratio=KEEP_RATIO):
    """
    Pick what the reading shows.

    The strongest theme of each source goes in first, so a reading is never
    made entirely of what the crowd said, nor entirely of what the mechanics
    show. If one source has nothing for this deck — a hand of cards nobody has
    ever written about — the other simply fills every place.

    The remaining places go by weight, and stop when a theme falls below
    keep_ratio of the leader: a uniform deck gets three, a motley one five.
    """
    chosen, seen = [], set()

    def take(t):
        if t and t["id"] not in seen:
            seen.add(t["id"])
            chosen.append(t)

    take(community[0] if community else None)
    take(mechanics[0] if mechanics else None)

    rest = sorted([t for t in community + mechanics if t["id"] not in seen],
                  key=lambda t: (-t["weight"], t["id"]))
    top = max([t["weight"] for t in chosen + rest], default=0.0)
    for t in rest:
        if len(chosen) >= max_n:
            break
        if len(chosen) >= min_n and t["weight"] < top * keep_ratio:
            break
        take(t)

    chosen.sort(key=lambda t: (-t["weight"], t["id"]))
    return chosen


def select_warnings(grievances, max_n=3):
    """The second half shows nothing at all when the crowd said nothing about
    this hand. That is deliberate: mechanics no longer fill the gap."""
    return grievances[:max_n]


def build(traits, by_card, archetypes, threshold=None, use_embeddings=True):
    """One-off: labels -> themes with carriers. Run offline, store the result."""
    themes = cluster_labels(traits, threshold, use_embeddings)
    return attach_carriers(themes, by_card, archetypes)


#  persistence

THEME_COLS = ["theme_id", "theme_label", "polarity", "subject", "trait",
              "about", "posts", "cards", "skip", "move_to"]


def write_themes(themes, json_path, csv_path):
    """
    Two files, on purpose.

    themes.json is what the pipeline reads: clustering is frozen here, so
    fragments and pages stop depending on an embedding call and on a threshold
    that might drift between runs.

    themes.csv is the same thing laid out one LABEL per row, for review by
    hand. Two columns are yours: `skip` drops a label, and `move_to` puts it
    into another theme — write the target theme_id, or any name of your own and
    every label carrying that name becomes a theme together. Without this the
    only lever is the global threshold, and nudging it to separate one bad pair
    scatters ten good ones.
    """
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    json.dump({"generated_at": __import__("datetime").date.today().isoformat(),
               "mode": LAST_MODE.get("mode"),
               "threshold": LAST_MODE.get("threshold"),
               "themes": themes}, open(json_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    rows = []
    for t in themes:
        for m in t["members"]:
            rows.append({"theme_id": t["id"], "theme_label": t["label"],
                         "polarity": t["polarity"], "subject": m["subject"],
                         "trait": m["trait"], "about": m.get("about", ""),
                         "posts": m.get("posts", 0),
                         "cards": ";".join(t.get("cards") or []),
                         "skip": "", "move_to": ""})
    import csv as _csv
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.DictWriter(f, fieldnames=THEME_COLS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def read_overrides(csv_path):
    """{(subject, trait): {'skip': bool, 'move_to': str}} from the review sheet."""
    import csv as _csv
    out = {}
    if not csv_path or not os.path.isfile(csv_path):
        return out
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            key = ((r.get("subject") or "").strip(), (r.get("trait") or "").strip())
            skip = (r.get("skip") or "").strip().lower() in ("1", "x", "y", "yes",
                                                             "true", "да", "+")
            move = (r.get("move_to") or "").strip()
            if skip or move:
                out[key] = {"skip": skip, "move_to": move}
    return out


def apply_overrides(themes, overrides):
    """Drop skipped labels, regroup moved ones, discard themes left empty."""
    if not overrides:
        return themes, 0, 0
    dropped = moved = 0
    groups = OrderedDict()
    for t in themes:
        for m in t["members"]:
            o = overrides.get((m["subject"], m["trait"]), {})
            if o.get("skip"):
                dropped += 1
                continue
            gid = o.get("move_to") or t["id"]
            if o.get("move_to"):
                moved += 1
            g = groups.setdefault(gid, {"id": gid, "polarity": t["polarity"],
                                        "label": t["label"], "variants": [],
                                        "members": [], "source": "community"})
            g["members"].append(m)
    out = []
    for gid, g in groups.items():
        g["members"].sort(key=lambda m: (-int(m.get("posts") or 0),
                                         -int(m.get("score") or 0)))
        g["label"] = g["members"][0]["trait"]        # loudest member names it
        g["variants"] = sorted({m["trait"] for m in g["members"]})
        out.append(g)
    out.sort(key=lambda t: -len(t["members"]))
    return out, dropped, moved


def load_themes(json_path):
    if not json_path or not os.path.isfile(json_path):
        return None
    d = json.load(open(json_path, encoding="utf-8"))
    return d.get("themes")


if __name__ == "__main__":
    import argparse, json
    import subjects_map as SM
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default=O.at("data", "cards.csv"))
    ap.add_argument("--traits", default=O.at("data", "card_traits.csv"))
    ap.add_argument("--player-traits", default=O.at("data", "player_traits.csv"))
    ap.add_argument("--subjects", default=O.at("data", "subjects.csv"))
    ap.add_argument("--threshold", type=float,
                    help="cosine cut-off; defaults per vector space")
    ap.add_argument("--no-embeddings", action="store_true")
    ap.add_argument("--env-file")
    ap.add_argument("--out", default=O.at("data", "themes.json"))
    ap.add_argument("--sheet", default=O.at("data", "themes.csv"),
                    help="review sheet: one label per row, with skip / move_to")
    ap.add_argument("--no-write", action="store_true",
                    help="only print the clusters, write nothing")
    a = ap.parse_args()
    try:
        from build_fragments import load_env
        load_env(a.env_file)
    except Exception:                                      
        pass

    cards = O.load_cards(a.cards)
    traits = (SM.load_traits(a.traits, "card")
              + SM.load_traits(a.player_traits, "player"))
    m = SM.build_map(list(cards), traits, SM.load_subject_weights(a.subjects))
    th = build(traits, m["by_card"], m["archetypes"], a.threshold,
               not a.no_embeddings)
    ov = read_overrides(a.sheet)
    if ov:
        th, dropped, moved = apply_overrides(th, ov)
        th = attach_carriers(th, m["by_card"], m["archetypes"])
        print(f"review sheet applied: {dropped} labels dropped, {moved} moved")
    pool = O.playable(cards)
    for t in th:
        t["rarity_factor"] = round(theme_rarity(t, len(pool)), 2)

    solo_n = sum(1 for t in th if len(t["members"]) == 1)
    print(f"\nMODE: {LAST_MODE['mode']}, threshold {LAST_MODE.get('threshold')}")
    print(f"labels {len(traits)} -> themes {len(th)} "
          f"({solo_n} of them from a single label)\n")
    for t in th:
        if len(t["members"]) < 2 and not t["cards"]:
            continue
        print(f"[{len(t['members'])} labels | {len(t['cards'])} cards | "
              f"x{t['rarity_factor']}] {t['polarity']}: {t['label']}")
        if len(t["variants"]) > 1:
            print("    also: " + " | ".join(t["variants"][1:6]))
        print("    cards: " + ", ".join(t["cards"][:8])
              + ("…" if len(t["cards"]) > 8 else ""))
    solo = [t for t in th if len(t["members"]) == 1]
    print(f"\nthemes of one label: {len(solo)}")
    if not a.no_write:
        n = write_themes(th, a.out, a.sheet if not ov else a.sheet + ".new")
        sheet = a.sheet if not ov else a.sheet + ".new"
        print(f"\nwrote {a.out}  ({len(th)} themes)")
        print(f"wrote {sheet}  ({n} rows, one per label)")
        print("Edit the sheet to fix clusters by hand: `skip` drops a label, "
              "`move_to` regroups it.\nbuild_fragments.py reads themes.json "
              "and will not call the embedding API again.")