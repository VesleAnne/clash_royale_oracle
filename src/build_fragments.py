"""
build_fragments.py — runs an LLM once over every card,
every known pairing and every deck rule, and writes the resulting text into
fragments.json. After this the reading engine assembles predictions from that
file and never calls a model again.

    pip install google-genai            # or: pip install openai
    # key in .env:  GEMINI_API_KEY=...  /  OPENAI_API_KEY=...

    python build_fragments.py --dry-run          # show the prompt
    python build_fragments.py --limit 6          # trial run on six cards
    python build_fragments.py                    # everything
    python build_fragments.py --provider openai --model gpt-5  #choosing another model or/and provider

Inputs:
    cards.csv           card database
    card_traits.csv     community labels
    platyer_traits.csv  community labels
    subjects.csv        subject weights
    common_phrases.md   STYLE EXAMPLES, one phrase per line. This file sets the
                        voice; the script refuses to run without it.

Output fragments.json:
    cards[name]        essence, as_ambition, as_burden, as_everyday,
                       as_shadow, caution   (+ community: raw labels)
    archetypes[name]   reading, caution, triggers, community
    rules[key]         omen, counsel
"""

import argparse, csv, json, os, random, re, sys, time
from collections import Counter, OrderedDict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oracle_rules as O
import subjects_map as SM
import themes as TH
from oracle_rules import ROOT, at

DEFAULT_MODEL = {"gemini": "gemini-3.7-flash", "openai": "gpt-5"}
FALLBACKS = {"gemini": ["gemini-3.6-flash", "gemini-3.5-flash-lite"],
             "openai": ["gpt-5-mini"]}

CARD_FIELDS = OrderedDict([
    ("essence",     "What holding this figure says about the person. The core reading."),
    ("as_ambition", "Read as what they are reaching for."),
    ("as_burden",   "Read as the weight they carry."),
    ("as_everyday", "Read as their ordinary, daily self."),
    ("as_shadow",   "Read as something they refused or left behind."),
    ("caution",     "The warning this figure carries. What it costs them."),
])
# One reading per community label. This is the heart of the oracle: every
# opinion the community voiced must come out as a statement about the person.
TRAIT_FIELDS = OrderedDict([
    ("reading", "The label restated as a trait of the person, second person, "
                "neutral or sympathetic. Never a judgement."),
    ("shadow",  "The cost of that same trait. One sentence, no blame."),
])

ARCH_FIELDS = OrderedDict([
    ("reading", "What choosing this pairing says about the person."),
    ("caution", "The warning this pairing carries."),
])

# One passage per THREAT named in the grievances. The second half of the
# reading is built only from these: if the crowd said nothing about this hand,
# that half is simply absent.
THREAT_FIELDS = OrderedDict([
    ("reading", "The warning. Who the reader should beware, rendered as a "
                "figure rather than a card."),
    ("shadow",  "What it costs the reader when that figure arrives. One sentence."),
])

THREAT_SCHEMA = {"type": "object",
                 "properties": {"threats": {"type": "array", "items": {
                     "type": "object",
                     "properties": dict({"key": {"type": "string"}},
                                        **{k: {"type": "string"}
                                           for k in THREAT_FIELDS}),
                     "required": ["key"] + list(THREAT_FIELDS),
                     "propertyOrdering": ["key"] + list(THREAT_FIELDS)}}},
                 "required": ["threats"]}

PERSONA = """You write fragments for an oracle that reads a player's deck of \
cards as a spread about their character, in the voice of a fortune teller.

STYLE IS SET BY THE EXAMPLES BELOW. Match their rhythm, their hedging, their \
second-person address and their length. Do not imitate their content — only \
their manner of speech.

{examples}

Every figure comes with facts. Two of them carry the most meaning about a \
person and must shape the reading whenever they are given:

- PACE — how fast the figure moves. The unhurried figure belongs to someone \
who refuses to be rushed, who arrives when they arrive. The swift figure \
belongs to someone who is already gone before the question is finished. Let \
pace decide the tempo of your sentences, not only their content.
- WHAT IT LOOKS AT — what the figure is willing to see. One that strikes both \
the ground and the sky misses nothing. One that answers only what stands in \
front of it is blind to whatever comes from above. One that ignores every \
living thing and walks straight at what its enemy has built is single-minded \
to the point of blindness — it does not defend, it only arrives.

TURNING AN OPINION INTO A TRAIT. Other people have said things about those who choose each figure. Most of it is an insult. Your task is never to repeat the insult and never to defend against it, but to name the disposition underneath it — as a preference the person holds, not a fault they have. Strip the judgement, keep the behaviour:

  "low skill card"            -> you do not like to think twice
  "repetitive"                -> you like stability, and return to what works
  "ultra defensive playstyle" -> you do not like to start conflicts
  "crutch"                    -> you would rather lean on something steady than prove you can stand alone
  "braindead"                 -> you trust the first answer that comes to you

Then name its cost in the same breath, without blame: what a person like that tends to miss. Never write that the reader is bad, lazy, unskilled or wrong.

Hard rules:
- Second person, present or near-future tense.
- Never name the game, the cards' game function, elixir, towers, decks, ladder, \
statistics or any mechanic. Translate every mechanic into human terms: \
"costs 8" becomes "asks everything of you before it will move"; "moves slowly" \
becomes "will not be hurried".
- Never insult the reader. Community opinions are hostile by nature; render \
one as a shadow the reader may acknowledge, never as a verdict about them. \
"braindead card" becomes "you have been told this path asks nothing of you".
- Each field is 1-2 sentences, under 40 words. Plain text. No markdown, no \
quotation marks around the whole line, no repeating the figure's name at the start.
- Do not invent facts about the figure beyond what you are given.
"""


def load_env(explicit=None):
    """Read API keys from a .env file."""
    cands = [explicit] if explicit else []
    if not explicit:
        here = os.path.dirname(os.path.abspath(__file__))
        for base in (os.getcwd(), here):
            cands += [os.path.join(base, ".env"),
                      os.path.join(base, "..", ".env"),
                      os.path.join(base, "..", "..", ".env")]
    for path in cands:
        if not path or not os.path.isfile(path):
            continue
        loaded = []
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            k, v = (x.strip() for x in line.split("=", 1))
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k and k not in os.environ:
                os.environ[k] = v
                loaded.append(k)
        if loaded:
            print(f"# .env: {path} -> {', '.join(loaded)}", file=sys.stderr)
        return
    if explicit:
        sys.exit(f"file not found: {explicit}")


def strict_schema(node):
    """Copy of a schema for OpenAI strict mode: additionalProperties forbidden,
    and Gemini-only keys dropped."""
    if isinstance(node, dict):
        out = {k: strict_schema(v) for k, v in node.items()
               if k != "propertyOrdering"}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [strict_schema(x) for x in node]
    return node


def sleep_i(sec):
    """Sleep in half-second slices."""
    end = time.time() + sec
    while time.time() < end:
        time.sleep(min(0.5, end - time.time()))


class LLM:
    """One interface over Gemini and OpenAI. Falls back to weaker models on 503."""

    def __init__(self, provider, model, temperature=0.9, tries=3):
        self.provider = provider
        self.chain = [model] + [m for m in FALLBACKS[provider] if m != model]
        self.temperature, self.tries = temperature, tries
        self.usage = Counter()
        if provider == "gemini":
            from google import genai
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                sys.exit("no GEMINI_API_KEY (put it in .env)")
            self.client = genai.Client(api_key=key)
        else:
            from openai import OpenAI
            if not os.environ.get("OPENAI_API_KEY"):
                sys.exit("no OPENAI_API_KEY (put it in .env)")
            self.client = OpenAI()

    def _once(self, model, system, prompt, schema, name):
        if self.provider == "gemini":
            from google.genai import types
            cfg = types.GenerateContentConfig(
                system_instruction=system, temperature=self.temperature,
                max_output_tokens=8192, response_mime_type="application/json",
                response_schema=schema,
                # automatic function calling on every call.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True))
            r = self.client.models.generate_content(
                model=model, contents=prompt, config=cfg)
            um = getattr(r, "usage_metadata", None)
            if um:
                self.usage["in"] += um.prompt_token_count or 0
                self.usage["out"] += um.candidates_token_count or 0
            return r.text or ""
        r = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_schema", "json_schema": {
                "name": name, "strict": True, "schema": strict_schema(schema)}},
        )
        if getattr(r, "usage", None):
            self.usage["in"] += r.usage.prompt_tokens or 0
            self.usage["out"] += r.usage.completion_tokens or 0
        return r.choices[0].message.content or ""

    def call(self, system, prompt, schema, name):
        """Structured call. Returns parsed JSON, or None if every attempt failed."""
        plan = [(m, t) for m in self.chain for t in range(self.tries)]
        last = None
        for step, (model, attempt) in enumerate(plan):
            try:
                raw = self._once(model, system, prompt, schema, name).strip()
                raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw)
                if not raw:
                    raise ValueError("empty response")
                return json.loads(raw)
            except KeyboardInterrupt:
                raise
            except Exception as e:                         
                last = e
                if step == len(plan) - 1:
                    break
                wait = min(60, 5 * 2 ** attempt) + random.uniform(0, 3)
                print(f"  {type(e).__name__}: {str(e)[:90]} — retry in {wait:.0f}s",
                      file=sys.stderr)
                sleep_i(wait)
        print(f"  failed: {last}", file=sys.stderr)
        return None

    def text(self, system, prompt, max_tokens=2048):
        """Free-form call without a schema. Used by the reader to stitch a spread."""
        plan = [(m, t) for m in self.chain for t in range(self.tries)]
        last = None
        for step, (model, attempt) in enumerate(plan):
            try:
                if self.provider == "gemini":
                    from google.genai import types
                    cfg = types.GenerateContentConfig(
                        system_instruction=system, temperature=self.temperature,
                        max_output_tokens=max_tokens,
                        automatic_function_calling=(
                            types.AutomaticFunctionCallingConfig(disable=True)))
                    r = self.client.models.generate_content(
                        model=model, contents=prompt, config=cfg)
                    out = (r.text or "").strip()
                else:
                    r = self.client.chat.completions.create(
                        model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": prompt}])
                    out = (r.choices[0].message.content or "").strip()
                if not out:
                    raise ValueError("empty response")
                return out
            except KeyboardInterrupt:
                raise
            except Exception as e:                        
                last = e
                if step == len(plan) - 1:
                    break
                sleep_i(min(60, 5 * 2 ** attempt) + random.uniform(0, 3))
        print(f"  stitching failed: {last}", file=sys.stderr)
        return None


#  schemas

def obj(fields, key):
    props = {key: {"type": "string"}}
    props.update({k: {"type": "string"} for k in fields})
    return {"type": "object", "properties": props,
            "required": [key] + list(fields),
            "propertyOrdering": [key] + list(fields)}


CARD_SCHEMA = {"type": "object",
               "properties": {"cards": {"type": "array",
                                        "items": obj(CARD_FIELDS, "card")}},
               "required": ["cards"]}
ARCH_SCHEMA = {"type": "object",
               "properties": {"pairings": {"type": "array",
                                           "items": obj(ARCH_FIELDS, "name")}},
               "required": ["pairings"]}
TRAIT_SCHEMA = {"type": "object",
                "properties": {"traits": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "reading": {"type": "string"},
                        "shadow": {"type": "string"}},
                    "required": ["id", "reading", "shadow"],
                    "propertyOrdering": ["id", "reading", "shadow"]}}},
                "required": ["traits"]}

RULE_SCHEMA = {"type": "object",
               "properties": {"rules": {"type": "array", "items": {
                   "type": "object",
                   "properties": {"rule": {"type": "string"},
                                  "omen": {"type": "string"},
                                  "counsel": {"type": "string"}},
                   "required": ["rule", "omen", "counsel"],
                   "propertyOrdering": ["rule", "omen", "counsel"]}}},
               "required": ["rules"]}



# The game's own speed scale, read out of strings like "Medium (60)".
# The model must never see the number: it would start doing arithmetic on it.
SPEED_WORDS = [
    (45,  "will not be hurried; it arrives long after it was called"),
    (60,  "keeps an even, unhurried pace"),
    (90,  "moves quickly, ahead of most things around it"),
    (999, "is already gone before you finish deciding"),
]


def speed_phrase(r):
    v = O.speed_value(r)
    if v is None:
        return None
    for limit, words in SPEED_WORDS:
        if v <= limit:
            return words
    return SPEED_WORDS[-1][1]


def targets_phrase(r):
    """What the figure is willing to look at. This is the second field the
    persona leans on, so it is always stated, never left implicit."""
    t = (r.get("targets") or "").strip()
    if not t:
        return None
    low = t.lower()
    if low.startswith("building"):
        return ("looks past every living thing and walks straight at what its "
                "enemy has built; it does not defend, it only arrives")
    if "air" in low and "ground" in low:
        return "sees both what walks and what flies; nothing passes it unseen"
    if low == "air":
        return "answers only what flies, and ignores the ground entirely"
    if low == "ground":
        return ("answers only what stands in front of it, and is blind to "
                "anything that comes from above")
    return f"strikes: {t}"


def card_brief(name, r, traits):
    """Turn one database row into prompt text. All numbers are translated
    HERE, not by the model: given raw figures it starts interpreting them
    and inventing facts."""
    bits = []

    price = O.num(r.get("elixir_cost"))
    if price is not None:
        bits.append(f"PRICE: {price:g} out of 10 "
                    f"({'cheap' if price <= 2 else 'heavy' if price >= 6 else 'middling'})")

    sp = speed_phrase(r)
    if sp:
        bits.append("PACE: " + sp)
    elif (r.get("type") or "").strip().lower() in ("spell", "building"):
        bits.append("PACE: it does not move at all; it waits where it was put")

    tg = targets_phrase(r)
    if tg:
        bits.append("WHAT IT LOOKS AT: " + tg)

    if (r.get("type") or "").strip():
        bits.append("KIND: " + r["type"].strip())
    if O.is_air_unit(r):
        bits.append("travels through the air, never touching the ground")

    splash = O.is_splash(r)
    if splash is True:
        bits.append("harms everything around it, not one thing")
    elif splash is False:
        bits.append("harms one thing at a time")

    n = O.unit_count(r)
    if n >= 3:
        bits.append(f"arrives not alone but as a crowd of {n}")

    rng = (r.get("range_kind") or "").strip()
    if rng:
        bits.append(f"keeps a {rng} distance")

    hp, dmg = O.num(r.get("hitpoints")), O.num(r.get("damage"))
    if hp is not None and dmg is not None:
        bits.append(f"endurance {hp:g}, force {dmg:g} (compare with others)")
    if (r.get("rarity") or "").strip():
        bits.append("rarity: " + r["rarity"].strip())

    u, w = O.usage(r), O.winrate(r)
    if u is not None:
        bits.append(f"chosen by {u:g}% of people"
                    + (f", succeeds {w:g}% of the time" if w is not None else ""))
    # Widely loved and quietly losing — the one contradiction worth naming.
    if u is not None and w is not None and u >= 8 and w < 50:
        bits.append("BELOVED YET FAILING: many choose it, it loses more than it wins")

    out = [f"FIGURE: {name}"] + ["  " + b for b in bits]
    d = (r.get("description") or "").strip()
    if d:
        out.append("  its own words: " + d[:200])
    for t in (traits or [])[:3]:
        line = f"  people say of those who choose it: {t['trait']}"
        if t["quote"]:
            line += f' — "{t["quote"]}"'
        line += f" [{t['polarity']}, {t['posts']} voices]"
        out.append(line)
    return "\n".join(out)


def arch_brief(name, a, cards):
    """A pairing is described through its members' pace and gaze, so the model
    can say what the combination reveals that neither figure does alone."""
    out = [f"PAIRING: {name}", "  made of: " + ", ".join(a["triggers"])]
    for c in a["triggers"]:
        r = cards.get(c) or {}
        price = O.num(r.get("elixir_cost"))
        parts = []
        if price is not None:
            parts.append(f"price {price:g}")
        sp = speed_phrase(r)
        if sp:
            parts.append(sp)
        tg = targets_phrase(r)
        if tg:
            parts.append(tg)
        d = (r.get("description") or "").strip()
        if d:
            parts.append(d[:100])
        out.append(f"  - {c}: " + "; ".join(parts))
    for t in a["traits"][:3]:
        line = f"  people say of those who choose it: {t['trait']}"
        if t["quote"]:
            line += f' — "{t["quote"]}"'
        line += f" [{t['polarity']}, {t['posts']} voices]"
        out.append(line)
    return "\n".join(out)


#  cache

def slug(s):
    return re.sub(r"[^\w.-]", "_", s)[:80]


class Cache:
    """One file per card / pairing. A rerun only pays for what is new.
    Failed batches are never written, so they are retried next time."""

    def __init__(self, root):
        self.root = root
        for sub in ("cards", "archetypes", "traits", "threats", "misc"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)

    def get(self, kind, key):
        p = os.path.join(self.root, kind, slug(key) + ".json")
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except json.JSONDecodeError:
                os.remove(p)
        return None

    def put(self, kind, key, val):
        json.dump(val, open(os.path.join(self.root, kind, slug(key) + ".json"),
                            "w", encoding="utf-8"), ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default=at("data", "cards.csv"))
    ap.add_argument("--traits", default=at("data", "card_traits.csv"),
                    help="what the community said about the CARDS")
    ap.add_argument("--player-traits", default=at("data", "player_traits.csv"),
                    help="what the community said about the PEOPLE who play them")
    ap.add_argument("--subjects", default=at("data", "subjects.csv"))
    ap.add_argument("--grievances", default=at("data", "grievances.csv"),
                    help="ordered pairs: who warns about whom")
    ap.add_argument("--phrases", default=at("data", "common_phrases.md"))
    ap.add_argument("--out", default=at("data", "fragments.json"))
    ap.add_argument("--cache", default=at(".cache", "fragments"))
    ap.add_argument("--provider", default="gemini", choices=["gemini", "openai"])
    ap.add_argument("--model")
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--env-file")
    ap.add_argument("--no-embeddings", action="store_true",
                    help="cluster labels on word overlap instead of embeddings")
    ap.add_argument("--themes-file", default=at("data", "themes.json"),
                    help="frozen clusters; regenerate with themes.py")
    ap.add_argument("--themes-sheet", default=at("data", "themes.csv"))
    ap.add_argument("--recluster", action="store_true",
                    help="ignore themes.json and cluster again")
    ap.add_argument("--cluster-threshold", type=float,
                    help="cosine cut-off for clustering; defaults per mode")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    load_env(a.env_file)
    model = a.model or DEFAULT_MODEL[a.provider]

    # ── style examples: the whole tone of the output depends on this file
    if not os.path.isfile(a.phrases):
        sys.exit(f"missing {a.phrases}. It holds the style examples, one phrase "
                 "per line, and sets the voice of every fragment. "
                 "Without it there is nothing to imitate.")
    lines = [l.strip().strip(" -*\t").strip()
             for l in open(a.phrases, encoding="utf-8")
             if l.strip() and not l.strip().startswith("#")]
    if len(lines) < 3:
        sys.exit(f"{a.phrases}: only {len(lines)} phrases — too few for a style sample")
    examples = ("Examples of the voice you must write in:\n"
                + "\n".join("  " + l for l in lines[:40]))
    system = PERSONA.format(examples=examples)
    print(f"style examples: {len(lines)} "
          f"(first {min(40, len(lines))} go into the prompt)")

    # ── data
    cards = O.load_cards(a.cards)
    pool = O.playable(cards)
    traits = (SM.load_traits(a.traits, "card")
              + SM.load_traits(a.player_traits, "player"))
    weights = SM.load_subject_weights(a.subjects)
    m = SM.build_map(list(cards), traits, weights)
    with_speed = sum(1 for r in pool.values() if O.speed_value(r) is not None)
    with_targets = sum(1 for r in pool.values() if (r.get("targets") or "").strip())
    n_card = sum(1 for t in traits if t["about"] == "card")
    n_player = len(traits) - n_card
    print(f"community labels: {len(traits)} "
          f"(about cards {n_card}, about players {n_player})")
    if not n_player:
        print(f"  note: no {os.path.basename(a.player_traits)} found — "
              "coverage will be much thinner")
    print(f"cards: {len(cards)}, playable: {len(pool)}, "
          f"with community labels: {len(m['by_card'])}, "
          f"pairings: {len(m['archetypes'])}")
    print(f"  pace known for {with_speed}/{len(pool)}, "
          f"gaze known for {with_targets}/{len(pool)}")
    if m["unresolved"]:
        print("  UNRESOLVED: " + ", ".join(m["unresolved"])
              + "  -> add them to MANUAL in subjects_map.py")

    names = list(pool)
    if a.limit:
        names = names[:a.limit]

    if a.dry_run:
        print(f"\n--dry-run. Provider {a.provider}, model {model}.")
        print(f"Batches: cards {(len(names) + a.batch - 1) // a.batch}, "
              f"pairings {(len(m['archetypes']) + a.batch - 1) // a.batch}, "
              f"rules 1.\n")
        print("=== system prompt ===")
        print(system + "\n")
        print("=== sample card ===")
        print(card_brief(names[0], pool[names[0]], m["by_card"].get(names[0])))
        for demo in ("Golem", "Hog Rider", "Mega Knight"):
            if demo in pool:
                print(f"\n=== sample card: {demo} ===")
                print(card_brief(demo, pool[demo], m["by_card"].get(demo)))
        if m["archetypes"]:
            k = sorted(m["archetypes"])[0]
            print("\n=== sample pairing ===")
            print(arch_brief(k, m["archetypes"][k], cards))
        return

    llm = LLM(a.provider, model, a.temperature)
    cache = Cache(a.cache)
    frag_cards, frag_arch, failed = {}, {}, []

    # ── cards
    todo = []
    for n in names:
        got = cache.get("cards", n)
        if got:
            frag_cards[n] = got
        else:
            todo.append(n)
    print(f"cards: {len(frag_cards)} cached, {len(todo)} to generate")
    spec = "\n".join(f"- {k}: {v}" for k, v in CARD_FIELDS.items())
    try:
        for i in range(0, len(todo), a.batch):
            batch = todo[i:i + a.batch]
            prompt = ("For each FIGURE below write these fields:\n" + spec +
                      "\n\nEvery fact is already translated into human terms. "
                      "Use PACE and WHAT IT LOOKS AT in at least one field per "
                      "figure. Never restate raw numbers.\n"
                      "Where a figure carries lines beginning 'people say', the "
                      "'essence' field MUST be built on what they said, turned "
                      "into a trait as instructed. The crowd's opinion outranks "
                      "the facts: the facts only colour it.\n\n" +
                      "\n\n".join(card_brief(n, pool[n], m["by_card"].get(n))
                                  for n in batch))
            data = llm.call(system, prompt, CARD_SCHEMA, "card_fragments")
            if data is None:
                failed += batch                     # not cached -> retried later
                continue
            got = {c["card"]: {k: " ".join((c.get(k) or "").split())
                               for k in CARD_FIELDS}
                   for c in data.get("cards", []) if c.get("card") in set(batch)}
            for n in batch:
                if n in got:
                    cache.put("cards", n, got[n])
                    frag_cards[n] = got[n]
                else:
                    failed.append(n)
            print(f"  [{min(i + a.batch, len(todo))}/{len(todo)}] "
                  f"got {len(got)}/{len(batch)}")

        # ── pairings
        arch_todo = []
        for k in sorted(m["archetypes"]):
            got = cache.get("archetypes", k)
            if got:
                frag_arch[k] = got
            else:
                arch_todo.append(k)
        print(f"pairings: {len(frag_arch)} cached, {len(arch_todo)} to generate")
        aspec = "\n".join(f"- {k}: {v}" for k, v in ARCH_FIELDS.items())
        for i in range(0, len(arch_todo), a.batch):
            batch = arch_todo[i:i + a.batch]
            prompt = ("For each PAIRING below write these fields:\n" + aspec +
                      "\n\nA pairing is a habit, not an object. Say what the "
                      "combination reveals that neither figure reveals alone — "
                      "especially where their pace or their gaze differ.\n"
                      "Where lines beginning 'people say' are present, build "
                      "'reading' on them first.\n\n" +
                      "\n\n".join(arch_brief(k, m["archetypes"][k], cards)
                                  for k in batch))
            data = llm.call(system, prompt, ARCH_SCHEMA, "pairing_fragments")
            if data is None:
                failed += batch
                continue
            got = {c["name"]: {k: " ".join((c.get(k) or "").split())
                               for k in ARCH_FIELDS}
                   for c in data.get("pairings", []) if c.get("name") in set(batch)}
            for k in batch:
                if k in got:
                    cache.put("archetypes", k, got[k])
                    frag_arch[k] = got[k]
                else:
                    failed.append(k)
            print(f"  [{min(i + a.batch, len(arch_todo))}/{len(arch_todo)}] "
                  f"got {len(got)}/{len(batch)}")
    except KeyboardInterrupt:
        print("\ninterrupted — saving what is done", file=sys.stderr)

    # ── community labels: one reading per label, generated separately from the
    # cards so that NO opinion is silently dropped. Counts must match exactly.
    trait_rows, _seen_ids = [], {}
    dup = 0
    for t in traits:
        tid = slug(t["subject"] + "__" + t["trait"])
        if tid in _seen_ids:
            # The same label can appear in both files (said of the card and of
            # the player). One reading serves both; keep the louder source.
            dup += 1
            if int(t.get("posts") or 0) > int(_seen_ids[tid].get("posts") or 0):
                _seen_ids[tid].update(dict(t, id=tid))
            continue
        row = dict(t, id=tid)
        _seen_ids[tid] = row
        trait_rows.append(row)
    if dup:
        print(f"community labels: {dup} duplicates merged "
              f"({len(traits)} rows -> {len(trait_rows)} distinct)")
    trait_readings = {}
    t_todo = []
    for t in trait_rows:
        got = cache.get("traits", t["id"])
        if got:
            trait_readings[t["id"]] = got
        else:
            t_todo.append(t)
    print(f"community labels: {len(trait_readings)} cached, "
          f"{len(t_todo)} to generate (of {len(trait_rows)} total)")
    tspec = "\n".join(f"- {k}: {v}" for k, v in TRAIT_FIELDS.items())
    try:
        for i in range(0, len(t_todo), a.batch * 2):
            batch = t_todo[i:i + a.batch * 2]
            lines_t = []
            for t in batch:
                q = f' — someone wrote: "{t["quote"]}"' if t["quote"] else ""
                aimed = ("at the PERSON who plays it"
                         if t.get("about") == "player" else "at the FIGURE itself")
                lines_t.append(
                    f'LABEL id={t["id"]}\n'
                    f'  subject: {t["subject"]}\n'
                    f'  aimed {aimed}\n'
                    f'  the label: {t["trait"]}{q}\n'
                    f'  tone of the original: {t["polarity"]}, '
                    f'{t["posts"]} voices')
            prompt = ("Each LABEL below is something people said about those "
                      "who choose a figure. For each write:\n" + tspec +
                      "\n\nCopy the id back exactly. Name the disposition "
                      "under the label; never repeat the insult, never argue "
                      "with it, never call the reader bad at anything.\n\n"
                      + "\n\n".join(lines_t))
            data = llm.call(system, prompt, TRAIT_SCHEMA, "trait_readings")
            if data is None:
                failed += [t["id"] for t in batch]
                continue
            ids = {t["id"] for t in batch}
            got = {c["id"]: {k: " ".join((c.get(k) or "").split())
                             for k in TRAIT_FIELDS}
                   for c in data.get("traits", []) if c.get("id") in ids}
            for t in batch:
                if t["id"] in got:
                    cache.put("traits", t["id"], got[t["id"]])
                    trait_readings[t["id"]] = got[t["id"]]
                else:
                    failed.append(t["id"])
            print(f"  [{min(i + a.batch * 2, len(t_todo))}/{len(t_todo)}] "
                  f"got {len(got)}/{len(batch)}")
    except KeyboardInterrupt:
        print("\ninterrupted — saving what is done", file=sys.stderr)

    # attach the source data back onto each reading
    for t in trait_rows:
        r = trait_readings.get(t["id"])
        if r is not None:
            r.update({"subject": t["subject"], "trait": t["trait"],
                      "polarity": t["polarity"], "quote": t["quote"],
                      "link": t["link"], "posts": t["posts"]})

    # ── threats: the second half of the reading, built from grievances
    gr = SM.load_grievances(a.grievances, list(cards))
    threat_readings = {}
    if gr["pairs"]:
        by_key = OrderedDict()
        for p in gr["pairs"]:
            k = p["threat"].strip().lower()
            e = by_key.setdefault(k, {"key": k, "label": p["threat"],
                                      "cards": set(), "victims": set(),
                                      "epithets": [], "quotes": []})
            e["cards"] |= set(p["threat_cards"])
            e["victims"] |= set(p["victim_cards"])
            if p["epithet"]:
                e["epithets"].append(p["epithet"])
            if p["evidence"]:
                e["quotes"].append((p["score"], p["evidence"]))
        t_todo = []
        for k, e in by_key.items():
            got = cache.get("threats", k)
            if got:
                threat_readings[k] = got
            else:
                t_todo.append(e)
        print(f"threats: {len(threat_readings)} cached, {len(t_todo)} to generate "
              f"(of {len(by_key)})")
        tspec = "\n".join(f"- {k}: {v}" for k, v in THREAT_FIELDS.items())
        try:
            for i in range(0, len(t_todo), a.batch):
                batch = t_todo[i:i + a.batch]
                lines_t = []
                for e in batch:
                    qs = [q for _, q in sorted(e["quotes"], reverse=True)[:3]]
                    lines_t.append(
                        f'THREAT key={e["key"]}\n'
                        f'  the figure: {e["label"]}'
                        + (f' (in the game: {", ".join(sorted(e["cards"]))})'
                           if e["cards"] else "") + "\n"
                        f'  feared by those who play: '
                        f'{", ".join(sorted(e["victims"])[:6])}\n'
                        + (f'  they call it: {"; ".join(sorted(set(e["epithets"]))[:4])}\n'
                           if e["epithets"] else "")
                        + "".join(f'  someone wrote: "{q}"\n' for q in qs))
                prompt = ("Each THREAT below is something players warned each "
                          "other about. For each write:\n" + tspec +
                          "\n\nCopy the key back exactly. Speak to the reader, "
                          "not about the game: name the KIND of opponent, and "
                          "where they gave it a name of their own, let that "
                          "name show through without repeating the insult.\n\n"
                          + "\n\n".join(lines_t))
                data = llm.call(system, prompt, THREAT_SCHEMA, "threat_fragments")
                if data is None:
                    failed += [e["key"] for e in batch]
                    continue
                keys = {e["key"] for e in batch}
                got = {c["key"]: {k2: " ".join((c.get(k2) or "").split())
                                  for k2 in THREAT_FIELDS}
                       for c in data.get("threats", []) if c.get("key") in keys}
                for e in batch:
                    if e["key"] in got:
                        cache.put("threats", e["key"], got[e["key"]])
                        threat_readings[e["key"]] = got[e["key"]]
                    else:
                        failed.append(e["key"])
                print(f"  [{min(i + a.batch, len(t_todo))}/{len(t_todo)}] "
                      f"got {len(got)}/{len(batch)}")
        except KeyboardInterrupt:
            print("\ninterrupted — saving what is done", file=sys.stderr)
        for k, e in by_key.items():
            r = threat_readings.get(k)
            if r is not None:
                r.update({"label": e["label"],
                          "threat_cards": sorted(e["cards"]),
                          "epithets": sorted(set(e["epithets"]))[:4]})

    # ── deck rules: one omen and one counsel per rule defined in oracle_rules
    rules = cache.get("misc", "rules")
    if not rules:
        lines_r = []
        for key, title, _fn, threats in O.RULES:
            lines_r.append(f"- {key}: {title}")
        prompt = ("Each line below describes a whole spread, not one figure: "
                  "something the person lacks, or has too much of. Several "
                  "concern pace (nothing hurries, everything hurries) and gaze "
                  "(nothing watches the sky, most of it looks past the living)."
                  "\n\nThese are OBSERVATIONS about the reader, not warnings "
                  "about opponents. Do not tell them to beware of anything and "
                  "do not mention enemies; the warnings come from elsewhere. "
                  "For each write:\n"
                  "- omen: name the absence or the excess as a trait of the "
                  "person. 1-2 sentences.\n"
                  "- counsel: what that trait costs them, in themselves. "
                  "One sentence, no enemy in it.\n\n" + "\n".join(lines_r))
        data = llm.call(system, prompt, RULE_SCHEMA, "rule_fragments")
        valid = {k for k, *_ in O.RULES}
        rules = {r["rule"]: {"omen": " ".join(r["omen"].split()),
                             "counsel": " ".join(r["counsel"].split())}
                 for r in (data or {}).get("rules", []) if r.get("rule") in valid}
        if rules:
            cache.put("misc", "rules", rules)
    rules = rules or {}

    # ── community labels are attached as a SEPARATE field, next to the
    # generated text rather than inside it, so they can be edited by hand.
    def _attach(ts):
        out = []
        for t in ts:
            tid = slug(t["subject"] + "__" + t["trait"])
            r = trait_readings.get(tid, {})
            out.append({"trait": t["trait"], "about": t.get("about", "card"),
                        "polarity": t["polarity"],
                        "quote": t["quote"], "link": t["link"],
                        "posts": t["posts"],
                        "reading": r.get("reading", ""),
                        "shadow": r.get("shadow", "")})
        return out

    # EVERY label is attached, not just the top three: the whole point is that
    # no community opinion is dropped from the oracle.
    for n, f in frag_cards.items():
        ts = m["by_card"].get(n)
        if ts:
            f["community"] = _attach(ts)
    for k, f in frag_arch.items():
        a_ = m["archetypes"][k]
        f["triggers"] = a_["triggers"]
        if a_.get("aliases"):
            f["aliases"] = a_["aliases"]
        if a_["traits"]:
            f["community"] = _attach(a_["traits"])

    # ── themes: cluster the labels, then let each cluster borrow the wording
    # of its loudest member. No extra model call — a theme is a grouping of
    # things already written, not a new thing to write.
    theme_list = []
    if trait_readings:
        # Clustering is frozen in data/themes.json by themes.py. Reading it
        # keeps this run free of embedding calls and, more importantly, stops
        # the themes from quietly changing between runs.
        clustered = None if a.recluster else TH.load_themes(a.themes_file)
        if clustered:
            clustered = TH.attach_carriers(clustered, m["by_card"],
                                           m["archetypes"])
            print(f"themes: loaded {len(clustered)} from "
                  f"{os.path.basename(a.themes_file)} (no embedding call)")
        else:
            clustered = TH.build(traits, m["by_card"], m["archetypes"],
                                 threshold=a.cluster_threshold,
                                 use_embeddings=not a.no_embeddings)
            ov = TH.read_overrides(a.themes_sheet)
            if ov:
                clustered, dr, mv = TH.apply_overrides(clustered, ov)
                clustered = TH.attach_carriers(clustered, m["by_card"],
                                               m["archetypes"])
                print(f"review sheet applied: {dr} dropped, {mv} moved")
            print(f"clustering mode: {TH.LAST_MODE['mode']}, "
                  f"threshold {TH.LAST_MODE.get('threshold')}")
            TH.write_themes(clustered, a.themes_file, a.themes_sheet)
            print(f"themes: wrote {a.themes_file}")
        for t in clustered:
            if not t.get("cards"):
                continue
            lead = None
            for mem in t["members"]:
                r = trait_readings.get(slug(mem["subject"] + "__" + mem["trait"]))
                if r and r.get("reading"):
                    lead = r
                    break
            if not lead:
                continue
            theme_list.append({
                "id": t["id"], "source": "community", "polarity": t["polarity"],
                "label": t["label"], "variants": t["variants"][:6],
                "cards": t["cards"], "reading": lead["reading"],
                "shadow": lead.get("shadow", ""),
                "voices": [{"subject": mem["subject"], "trait": mem["trait"],
                            "quote": mem["quote"], "link": mem["link"],
                            "posts": mem["posts"]}
                           for mem in t["members"][:4] if mem.get("quote")],
            })
        print(f"themes: {len(theme_list)} clusters carry text "
              f"(from {len(clustered)} clusters, {len(trait_readings)} labels)")

    doc = {"meta": {"generated_at": date.today().isoformat(),
                    "provider": a.provider, "model": model,
                    "style_source": os.path.abspath(a.phrases),
                    "style_examples": len(lines),
                    "cards_total": len(pool), "cards_done": len(frag_cards),
                    "cards_with_pace": with_speed,
                    "cards_with_targets": with_targets,
                    "archetypes_total": len(m["archetypes"]),
                    "archetypes_done": len(frag_arch),
                    "rules_total": len(O.RULES), "rules_done": len(rules),
                    "threats_done": len(threat_readings),
                    "themes_done": len(theme_list),
                    "grievance_pairs": len(gr["pairs"]),
                    "community_labels_total": len(trait_rows),
                    "community_labels_done": len(trait_readings),
                    "labels_about_cards": n_card,
                    "labels_about_players": n_player,
                    "skipped_subjects": m["skipped"],
                    "unresolved_subjects": m["unresolved"]},
           "cards": frag_cards, "archetypes": frag_arch, "rules": rules,
           "trait_readings": trait_readings, "threats": threat_readings,
           "themes": theme_list,
           "grievances": [{"victim_cards": p["victim_cards"],
                           "threat": p["threat"],
                           "threat_cards": p["threat_cards"],
                           "epithet": p["epithet"], "quote": p["evidence"],
                           "link": p["link"], "score": p["score"]}
                          for p in gr["pairs"]]}
    json.dump(doc, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    md = doc["meta"]
    print(f"\n{a.out}")
    print(f"  cards    : {md['cards_done']}/{md['cards_total']}")
    print(f"  pairings : {md['archetypes_done']}/{md['archetypes_total']}")
    print(f"  rules    : {md['rules_done']}/{md['rules_total']}")
    print(f"  themes   : {md['themes_done']}")
    print(f"  threats  : {md['threats_done']} "
          f"(from {md['grievance_pairs']} grievance pairs)")
    if gr["unresolved"]:
        print(f"  grievances dropped: {len(gr['unresolved'])} "
              "— see subjects_map.py --grievances for why")
    print(f"  community labels: {md['community_labels_done']}/"
          f"{md['community_labels_total']} turned into readings")
    if md["community_labels_done"] < md["community_labels_total"]:
        print("  <-- CHECK: some community opinions produced no reading; "
              "they will be missing from the oracle")
    # A label whose subject resolved to nothing reaches no reading in the app.
    attached = set()
    for ts in m["by_card"].values():
        attached |= {slug(t["subject"] + "__" + t["trait"]) for t in ts}
    for a_ in m["archetypes"].values():
        attached |= {slug(t["subject"] + "__" + t["trait"]) for t in a_["traits"]}
    skipped = set(m["skipped"])          # deliberately dropped: not decks
    orphan = sorted({t["subject"] for t in trait_rows
                     if t["id"] not in attached and t["subject"] not in skipped})
    dropped = sorted({t["subject"] for t in trait_rows
                      if t["subject"] in skipped})
    if dropped:
        print(f"  labels dropped on purpose (not decks): {', '.join(dropped)}")
    if orphan:
        print(f"  <-- CHECK: labels of {len(orphan)} subjects reach no card or "
              f"pairing: {', '.join(orphan)} — add them to MANUAL "
              "in subjects_map.py")
    print(f"  with community labels: cards "
          f"{sum(1 for v in frag_cards.values() if v.get('community'))}, pairings "
          f"{sum(1 for v in frag_arch.values() if v.get('community'))}")
    if failed:
        print(f"  FAILED: {len(failed)} — {', '.join(sorted(set(failed))[:8])}; "
              "not cached, just run the script again")
    if md["rules_done"] < md["rules_total"]:
        print("  <-- CHECK: not every rule got text")
    if llm.usage:
        print(f"  tokens: in {llm.usage['in']}, out {llm.usage['out']}")


if __name__ == "__main__":
    main()