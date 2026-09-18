"""
build_page.py — bakes cards.csv and fragments.json into ONE self-contained
HTML file. The page needs no server, no network and no API key: every reading
is assembled in the browser from the embedded fragment pool.

    python build_fragments.py            # first, generate the fragments
    python build_page.py                 # then, build the page
    open oracle.html

Re-run it whenever fragments.json changes.
"""

import argparse, csv, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oracle_rules as O
from oracle_rules import at

# Only the columns the browser actually needs for the spread logic.
KEEP = ["card", "rarity", "type", "elixir_cost", "targets", "transport",
        "damage_kind", "range_kind", "speed", "radius", "count",
        "count_comparison", "hitpoints", "usage_pct", "win_pct"]

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Deck Oracle</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Cormorant+Garamond:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
<style>
:root{
  --night:#0b0718; --night2:#150d2c; --veil:#1d1340;
  --gold:#d9b46a; --gold-dim:#8a7040; --ink:#ece4f7; --ink-dim:#a294c2;
  --glow:#8f6fe8;
}
:root:not([data-theme="light"]){color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--night);
  color:var(--ink);
  font-family:"Cormorant Garamond",Georgia,"Times New Roman",serif;
  font-size:18px; line-height:1.6;
  min-height:100vh;
  background-image:
    radial-gradient(1100px 700px at 50% -5%, #2a1a5c 0%, transparent 60%),
    radial-gradient(700px 500px at 12% 100%, #1b1042 0%, transparent 65%),
    radial-gradient(600px 500px at 88% 85%, #241250 0%, transparent 65%);
  background-attachment:fixed;
}
#stars{position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.55}
.wrap{position:relative;z-index:1;max-width:980px;margin:0 auto;padding:28px 18px 80px}
h1{
  font-family:Cinzel,Georgia,serif; font-weight:700;
  font-size:clamp(28px,5vw,46px); letter-spacing:.14em;
  text-align:center; margin:14px 0 6px; color:var(--gold);
  text-shadow:0 0 26px rgba(217,180,106,.35);
}
.sub{text-align:center;color:var(--ink-dim);font-style:italic;
  font-size:clamp(16px,2.4vw,21px);margin:0 auto 6px;max-width:34em;min-height:3em}
.hint{text-align:center;color:var(--gold-dim);font-size:14px;letter-spacing:.18em;
  text-transform:uppercase;font-family:Cinzel,serif;margin-bottom:6px}

/* ── crystal ball ───────────────────────────────────────────── */
.ball-wrap{display:flex;justify-content:center;margin:2px 0 14px}
.ball{width:clamp(150px,28vw,210px);height:auto;overflow:visible}
@keyframes drift{
  0%{transform:translate(0,0);opacity:0}
  15%{opacity:1}
  85%{opacity:1}
  100%{transform:translate(var(--dx),var(--dy));opacity:0}
}
.spark{animation:drift var(--dur) ease-in-out infinite;animation-delay:var(--del)}
@keyframes pulse{0%,100%{opacity:.5}50%{opacity:.95}}
.halo{animation:pulse 5s ease-in-out infinite}
@media (prefers-reduced-motion:reduce){.spark,.halo{animation:none}}

/* ── spread ─────────────────────────────────────────────────── */
/* Always four across, two rows. The cards shrink rather than reflow, so the
   spread keeps the same shape on a phone as on a desktop. */
.spread{display:grid;grid-template-columns:repeat(4,1fr);gap:clamp(5px,1.4vw,12px);
  margin:22px auto 8px;max-width:560px}
@media(max-width:560px){.spread{gap:5px;max-width:100%}}
.slot{
  position:relative;aspect-ratio:2/3;border-radius:10px;cursor:pointer;
  border:1px solid var(--gold-dim);
  background:
    repeating-linear-gradient(45deg,rgba(217,180,106,.08) 0 6px,transparent 6px 12px),
    linear-gradient(160deg,#241653,#140b30 70%);
  box-shadow:0 6px 20px rgba(0,0,0,.5), inset 0 0 26px rgba(143,111,232,.16);
  display:flex;align-items:center;justify-content:center;padding:clamp(3px,1vw,7px);
  transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}
.slot:hover,.slot:focus-visible{transform:translateY(-5px);border-color:var(--gold);
  box-shadow:0 12px 30px rgba(0,0,0,.6), inset 0 0 34px rgba(143,111,232,.3);outline:none}
.slot .mark{font-family:Cinzel,serif;font-size:clamp(15px,4vw,22px);
  color:var(--gold-dim);opacity:.85}
.slot.filled{
  background:linear-gradient(165deg,#3a2775,#1a1040 75%);
  border-color:var(--gold);
}
.slot .name{font-family:Cinzel,serif;font-size:clamp(8px,2.1vw,12px);
  text-align:center;color:var(--ink);line-height:1.35;word-break:break-word}
.slot .pos{position:absolute;top:4px;left:0;right:0;text-align:center;
  font-size:8px;letter-spacing:.12em;text-transform:uppercase;color:var(--gold-dim);
  font-family:Cinzel,serif}
.slot .clear{position:absolute;top:2px;right:4px;color:var(--ink-dim);
  font-size:13px;line-height:1;opacity:0;transition:opacity .15s}
.slot.filled:hover .clear{opacity:.85}

.count{text-align:center;color:var(--ink-dim);font-size:15px;letter-spacing:.05em}
.actions{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin:20px 0 10px}
button{
  font-family:Cinzel,serif;font-size:14px;letter-spacing:.14em;text-transform:uppercase;
  padding:13px 26px;border-radius:999px;cursor:pointer;
  background:linear-gradient(180deg,#3a2775,#241653);
  color:var(--gold);border:1px solid var(--gold-dim);
  transition:all .18s ease;
}
button:hover:not(:disabled){border-color:var(--gold);
  box-shadow:0 0 22px rgba(217,180,106,.3);transform:translateY(-2px)}
button:disabled{opacity:.35;cursor:not-allowed}
button.ghost{background:transparent;color:var(--ink-dim)}

/* ── picker ─────────────────────────────────────────────────── */
.veil{position:fixed;inset:0;background:rgba(6,3,16,.86);backdrop-filter:blur(3px);
  z-index:50;display:none;align-items:center;justify-content:center;padding:16px}
.veil.open{display:flex}
.panel{width:min(720px,100%);max-height:86vh;display:flex;flex-direction:column;
  background:linear-gradient(170deg,#1c1140,#100926);border:1px solid var(--gold-dim);
  border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.7);overflow:hidden}
.panel h2{font-family:Cinzel,serif;color:var(--gold);font-size:17px;letter-spacing:.12em;
  margin:0;padding:16px 18px 10px;text-align:center;font-weight:500}
.panel input{width:calc(100% - 36px);margin:0 18px 12px;padding:11px 14px;border-radius:8px;
  border:1px solid var(--gold-dim);background:rgba(0,0,0,.35);color:var(--ink);
  font-family:inherit;font-size:16px}
.panel input:focus{outline:none;border-color:var(--gold)}
.list{overflow-y:auto;padding:0 12px 14px;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:7px}
.opt{padding:9px 11px;border-radius:8px;cursor:pointer;border:1px solid transparent;
  display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:16px}
.opt:hover{background:rgba(143,111,232,.2);border-color:var(--gold-dim)}
.opt .el{color:var(--gold-dim);font-size:13px;font-family:Cinzel,serif;
  display:inline-flex;align-items:center;gap:3px;white-space:nowrap}
.opt .el svg{width:11px;height:14px;flex:none;opacity:.9}
.opt:hover .el{color:var(--gold)}
.opt.taken{opacity:.3;pointer-events:none}
.panel .close{margin:0 18px 16px}

/* ── reading ────────────────────────────────────────────────── */
.reading{margin-top:30px;display:none}
.reading.show{display:block;animation:rise .7s ease both}
@keyframes rise{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:none}}
.reading h2{font-family:Cinzel,serif;color:var(--gold);font-size:15px;
  letter-spacing:.22em;text-transform:uppercase;text-align:center;
  margin:34px 0 6px;font-weight:500}
.rule-line{width:120px;height:1px;margin:0 auto 20px;
  background:linear-gradient(90deg,transparent,var(--gold-dim),transparent)}
.entry{padding:0;margin:0 0 26px;max-width:38em;margin-left:auto;margin-right:auto}
.entry .pos{font-family:Cinzel,serif;font-size:11px;letter-spacing:.2em;
  text-transform:uppercase;color:var(--gold-dim)}
.entry .who{font-family:Cinzel,serif;color:var(--gold);font-size:16px;
  letter-spacing:.06em;margin:0 0 6px}
.entry p{margin:0 0 10px;font-size:19px;line-height:1.7}
.entry .voice{color:var(--ink-dim);font-style:italic;font-size:17px}
.entry .voice em{color:var(--gold-dim);font-style:italic}
.entry .src{font-size:13px;color:#6f6291;font-style:normal}
.omen{margin:0 0 20px}
.omen .t{font-family:Cinzel,serif;color:var(--gold);font-size:17px;margin-bottom:5px}
.omen .c{color:var(--ink-dim);font-style:italic}
.omen .f{font-size:14px;color:#6f6291;margin-top:4px}
.foot{text-align:center;color:#5d5182;font-size:12px;margin-top:40px;
  font-family:Cinzel,serif;letter-spacing:.1em}
</style></head><body>
<canvas id="stars"></canvas>
<div class="wrap">
  <h1>The Deck Oracle</h1>
  <p class="sub" id="intro"></p>

  <div class="ball-wrap">
    <svg class="ball" viewBox="0 0 200 230" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <radialGradient id="orb" cx="38%" cy="32%">
          <stop offset="0%" stop-color="#e9dcff" stop-opacity=".95"/>
          <stop offset="38%" stop-color="#9b7ae8" stop-opacity=".65"/>
          <stop offset="72%" stop-color="#4b2f93" stop-opacity=".55"/>
          <stop offset="100%" stop-color="#180d38" stop-opacity=".9"/>
        </radialGradient>
        <radialGradient id="halo" cx="50%" cy="50%">
          <stop offset="55%" stop-color="#8f6fe8" stop-opacity=".35"/>
          <stop offset="100%" stop-color="#8f6fe8" stop-opacity="0"/>
        </radialGradient>
        <linearGradient id="stand" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#d9b46a"/><stop offset="100%" stop-color="#6b5327"/>
        </linearGradient>
        <clipPath id="inside"><circle cx="100" cy="88" r="70"/></clipPath>
      </defs>
      <circle class="halo" cx="100" cy="88" r="100" fill="url(#halo)"/>
      <path d="M62 178 Q100 160 138 178 L146 196 Q100 186 54 196 Z" fill="url(#stand)" opacity=".9"/>
      <ellipse cx="100" cy="196" rx="52" ry="9" fill="#2a1d0c" opacity=".8"/>
      <circle cx="100" cy="88" r="70" fill="url(#orb)"/>
      <g clip-path="url(#inside)" id="sparks"></g>
      <ellipse cx="76" cy="60" rx="20" ry="13" fill="#fff" opacity=".22"
               transform="rotate(-28 76 60)"/>
      <circle cx="100" cy="88" r="70" fill="none" stroke="#d9b46a"
              stroke-opacity=".45" stroke-width="1.2"/>
    </svg>
  </div>

  <p class="hint">Touch a card to name it</p>
  <div class="spread" id="spread"></div>
  <p class="count" id="count"></p>

  <div class="actions">
    <button id="reveal" disabled>Choose 8 more</button>
    <button id="again" class="ghost">Read again</button>
  </div>

  <div class="reading" id="reading"></div>
  <p class="foot" id="foot"></p>
</div>

<div class="veil" id="veil">
  <div class="panel">
    <h2 id="panelTitle">Which figure shows itself?</h2>
    <input id="search" type="text" placeholder="Search…" autocomplete="off">
    <div class="list" id="list"></div>
    <button class="ghost close" id="closePicker">Close</button>
  </div>
</div>

<script>
const DATA = __DATA__;
const CARDS = DATA.cards, FRAG = DATA.fragments, POOL = DATA.pool;
const DECK_SIZE = 8;

/* ── intro lines, one drawn at random each time ─────────────── */
const INTROS = [
  "Choose eight, and the cards will tell you what you already suspect.",
  "The glass is clouded. Name your eight, and it will clear.",
  "Eight figures wait to be called. Choose, and read what you have chosen.",
  "Lay out eight. What you pick is never an accident.",
  "The cards are shuffled. Pick your eight, and learn what they know of you."
];
const pick = a => a[Math.floor(Math.random()*a.length)];
function newIntro(){ document.getElementById('intro').textContent = pick(INTROS); }

/* ── helpers mirroring oracle_rules.py ──────────────────────── */
const num = v => { if(v==null) return null;
  const m = String(v).replace(/,/g,'').match(/-?\d+(?:\.\d+)?/); return m?parseFloat(m[0]):null; };
const C = n => CARDS[n] || {};
const isSpell = r => (r.type||'').toLowerCase()==='spell';
const isBuilding = r => (r.type||'').toLowerCase()==='building';
const hitsAir = r => (r.targets||'').toLowerCase().includes('air');
const isAirUnit = r => (r.transport||'').toLowerCase()==='air';
const buildingsOnly = r => (r.targets||'').trim().toLowerCase().startsWith('building');
const speedOf = r => num(r.speed);
const movesQ = r => speedOf(r)!==null;
function isSplash(r){
  const dk=(r.damage_kind||'').toLowerCase();
  if(dk) return dk.startsWith('area');
  if(isSpell(r)) return true;
  if(num(r.radius)!==null) return true;
  return null;
}
function unitCount(r){ let n=num(r.count); if(n===null) n=num(r.count_comparison);
  return n?Math.round(n):1; }
const usage = r => num(r.usage_pct), winrate = r => num(r.win_pct);
const avgElixir = d => { const v=d.map(n=>num(C(n).elixir_cost)).filter(x=>x!==null);
  return v.length? Math.round(v.reduce((a,b)=>a+b,0)/v.length*100)/100 : null; };
const totalUsage = d => { const v=d.map(n=>usage(C(n))).filter(x=>x!==null);
  return v.length? Math.round(v.reduce((a,b)=>a+b,0)*10)/10 : null; };

const POSITIONS = [
  ['wall','The Wall', r=>isBuilding(r)&&!isSpell(r), r=>[num(r.hitpoints)||0, -(num(r.elixir_cost)||0)]],
  ['hidden_knowledge','Hidden Knowledge', isSpell, r=>[num(r.radius)||0, num(r.elixir_cost)||0]],
  ['ambition','The Ambition', r=>buildingsOnly(r)&&!isSpell(r), r=>[num(r.elixir_cost)||0]],
  ['burden','The Burden', ()=>true, r=>[num(r.elixir_cost)||0, num(r.hitpoints)||0]],
  ['everyday_self','The Everyday Self', ()=>true, r=>[-(num(r.elixir_cost)??99), num(r.usage_pct)||0]],
  ['well_trodden','The Well-Trodden Path', r=>usage(r)!==null, r=>[num(r.usage_pct)||0]],
  ['outsider','The Outsider', ()=>true, r=>[-(num(r.usage_pct)??99)]]
];
const cmpKey=(a,b)=>{ for(let i=0;i<Math.max(a.length,b.length);i++){
  const x=a[i],y=b[i]; if(x===y) continue;
  if(typeof x==='string'||typeof y==='string') return String(x)>String(y)?1:-1;
  return x>y?1:-1; } return 0; };

function assignPositions(deck){
  const taken=new Set(), out=[];
  for(const [key,label,fits,rank] of POSITIONS){
    let best=null;
    for(const n of deck){
      const r=C(n); if(taken.has(n)||!fits(r)) continue;
      const k=[...rank(r), n];
      if(best===null||cmpKey(k,best.k)>0) best={k,n};
    }
    if(best){ taken.add(best.n); out.push({key,label,card:best.n}); }
    else out.push({key,label,card:null});
  }
  return out;
}
function shadowCard(deck){
  const inDeck=new Set(deck); let best=null;
  for(const n of POOL){ if(inDeck.has(n)) continue;
    const u=usage(C(n)); if(u===null) continue;
    const k=[u,n]; if(best===null||cmpKey(k,best.k)>0) best={k,n}; }
  return best?best.n:null;
}
const RULES=[
  ['no_air_defense', d=>!d.some(n=>hitsAir(C(n))), ['Balloon','Lava Hound','Minion Horde','Bats']],
  ['no_splash', d=>!d.some(n=>isSplash(C(n))===true), ['Skeleton Army','Goblin Gang','Bats','Minion Horde']],
  ['no_building', d=>!d.some(n=>isBuilding(C(n))), ['Hog Rider','Royal Giant','Balloon','Ram Rider']],
  ['no_win_condition', d=>!d.some(n=>buildingsOnly(C(n))), []],
  ['heavy', d=>{const a=avgElixir(d); return a!==null&&a>=4.2;}, ['Hog Rider','Miner','Skeletons','Ice Spirit']],
  ['featherweight', d=>{const a=avgElixir(d); return a!==null&&a<=3.0;}, ['Golem','P.E.K.K.A.','Mega Knight','Electro Giant']],
  ['all_ground', d=>!d.some(n=>isAirUnit(C(n))), []],
  ['swarm_heavy', d=>d.filter(n=>unitCount(C(n))>=3).length>=3, ['Fireball','Arrows','Wizard','Valkyrie','Executioner']],
  ['no_cheap_cycle', d=>!d.some(n=>(num(C(n).elixir_cost)??99)<=2), []],
  ['all_slow', d=>{const m=d.filter(n=>movesQ(C(n))); return m.length>=3 && !m.some(n=>speedOf(C(n))>=90);},
    ['Hog Rider','Miner','Bandit','Royal Hogs']],
  ['all_swift', d=>{const m=d.filter(n=>movesQ(C(n))); const f=m.filter(n=>speedOf(C(n))>=90);
    return m.length>=3 && f.length>=Math.max(3,m.length-1);}, ['P.E.K.K.A.','Mega Knight','Golem']],
  ['tunnel_vision', d=>d.filter(n=>buildingsOnly(C(n))).length>=3,
    ['Skeleton Army','Inferno Tower','Mini P.E.K.K.A.']],
  ['meta_conformist', d=>{const s=totalUsage(d); return s!==null&&s>=60;}, []],
  ['meta_outsider', d=>{const s=totalUsage(d); return s!==null&&s<=20;}, []]
];
const EMPTY={
  wall:"You have built nothing to stand behind. Whatever comes, you meet it in the open.",
  hidden_knowledge:"Nothing here is hidden. You hold no answer in reserve, and everything you are is already on the table.",
  ambition:"Nothing in this spread is reaching for anything. You are defending a position you have not yet chosen."
};
const FIELD={ambition:'as_ambition',burden:'as_burden',everyday_self:'as_everyday',
  wall:'essence',hidden_knowledge:'essence',well_trodden:'essence',outsider:'essence'};

function voices(f,limit){ let c=f.community||[]; if(!Array.isArray(c)) c=[c];
  return c.slice().sort((a,b)=>(+b.posts||0)-(+a.posts||0)).slice(0,limit||3); }

function compose(deck){
  let blocks=[];
  for(const p of assignPositions(deck)){
    if(!p.card){ if(EMPTY[p.key]) blocks.push({pos:p.label,card:null,text:EMPTY[p.key]}); continue; }
    const f=FRAG.cards[p.card]; if(!f) continue;
    // One voice per card, and only when single cards survive into the reading.
    const b={pos:p.label,card:p.card,text:f[FIELD[p.key]]||f.essence||'',
             voices:voices(f,1)};
    const u=usage(C(p.card)), w=winrate(C(p.card));
    if(u!==null&&w!==null&&u>=8&&w<50&&f.caution) b.extra=f.caution;
    blocks.push(b);
  }
  const set=new Set(deck), used=new Set();
  const pairs=Object.entries(FRAG.archetypes||{})
    .sort((a,b)=>(b[1].triggers||[]).length-(a[1].triggers||[]).length||a[0].localeCompare(b[0]));
  for(const [name,af] of pairs){
    const t=af.triggers||[];
    if(t.length<2||!t.every(x=>set.has(x))||t.some(x=>used.has(x))) continue;
    t.forEach(x=>used.add(x));
    blocks.push({pos:'The Pairing',card:name,members:t,text:af.reading||'',
                 extra:af.caution||''});
  }
  // A pairing already says what its cards say, and says it better. When the
  // hand holds one, the single-card positions are dropped: otherwise the same
  // two cards get read twice, once apart and once together.
  const pairBlocks=blocks.filter(b=>b.pos==='The Pairing');
  let kept = pairBlocks.length ? pairBlocks : blocks;

  const sh=shadowCard(deck);
  if(sh&&FRAG.cards[sh]) kept.push({pos:'The Shadow',card:sh,
    text:FRAG.cards[sh].as_shadow||FRAG.cards[sh].essence||'',
    voices:voices(FRAG.cards[sh],1)});
  blocks=kept;
  let themes=selectThemes(weighThemes(FRAG.themes||[],deck,POOL.length,false),
                          weighThemes(mechanicalThemes(deck),deck,POOL.length,true));
  // Nothing is said twice in one spread.
  const said=new Set();
  for(const b of blocks){
    for(const s of [b.text,b.extra]) if(s) said.add(s.toLowerCase().split(/\s+/).join(' '));
  }
  themes=themes.map(t=>{
    const o={...t};
    for(const f of ['reading','shadow']){
      const k=(o[f]||'').toLowerCase().split(/\s+/).join(' ');
      if(k&&said.has(k)) o[f]=''; else if(k) said.add(k);
    }
    return o;
  }).filter(t=>t.reading||t.shadow);
  return {blocks,themes,warnings:grievanceThemes(deck)};
}

/* ── themes: the layer between single labels and the reading ──
   Mirrors themes.py exactly, so a spread reads the same in the browser as it
   does in the terminal. */
const USAGE_CAP=20, RARITY_STRENGTH=2, MIN_THEMES=3, MAX_THEMES=5, KEEP_RATIO=0.45;
// The titles live in oracle_rules.py; repeated here because the browser has
// no access to it and a rule key makes a poor heading.
const RULE_TITLE={
  no_air_defense:"Nothing in this deck can strike the sky",
  no_splash:"Every blow here falls on one target",
  no_building:"You have raised no walls",
  no_win_condition:"Nothing here is hunting the towers",
  heavy:"Your hand is slow to open",
  featherweight:"Nothing you hold can stop a mountain",
  all_ground:"You never leave the earth",
  swarm_heavy:"You trust in numbers",
  no_cheap_cycle:"You hold nothing you can spend freely",
  all_slow:"Nothing you hold is in a hurry",
  all_swift:"Everything you hold is already moving",
  tunnel_vision:"Most of what you hold looks past the living",
  meta_conformist:"You walk a well-trodden road",
  meta_outsider:"You walk where few have walked"};
const RULE_WEIGHT={all_slow:1,all_swift:1,tunnel_vision:.95,swarm_heavy:.9,
  meta_outsider:.9,meta_conformist:.75,heavy:.7,featherweight:.7,
  no_win_condition:.65,no_building:.55,no_air_defense:.55,no_splash:.5,
  no_cheap_cycle:.45,all_ground:.4};
const DEFAULT_RULE_WEIGHT=.6;

function rarityBonus(n){
  const u=usage(C(n)); if(u===null) return 1;
  return 1+RARITY_STRENGTH*(1-Math.min(u,USAGE_CAP)/USAGE_CAP);
}
function themeRarity(t,total){
  const n=Math.max(1,(t.cards||[]).length);
  return 1+Math.log(Math.max(1,total/n));
}
const RULE_CARRIERS={all_slow:d=>d.movers,all_swift:d=>d.fast,
  swarm_heavy:d=>d.swarms,tunnel_vision:d=>d.blind};

function weighThemes(list,deck,total,mechanical){
  const set=new Set(deck), out=[];
  for(const t of list){
    const present=(t.cards||[]).filter(c=>set.has(c));
    if(!present.length) continue;
    const base=present.reduce((a,c)=>a+rarityBonus(c),0);
    const tr=mechanical?(t.rule_weight??DEFAULT_RULE_WEIGHT):themeRarity(t,total);
    out.push({...t,deck_cards:present,weight:+(base*tr).toFixed(3)});
  }
  out.sort((a,b)=>b.weight-a.weight||a.id.localeCompare(b.id));
  return out;
}
function mechanicalThemes(deck){
  const out=[];
  for(const [key,fn,threats] of RULES){
    if(!fn(deck)) continue;
    const txt=(FRAG.rules||{})[key]; if(!txt) continue;
    let carriers=deck;
    // the detail lists live in the Python analyser; in the browser we rebuild
    // the few that matter and fall back to the whole hand for absences
    if(key==='all_slow') carriers=deck.filter(n=>movesQ(C(n)));
    if(key==='all_swift') carriers=deck.filter(n=>movesQ(C(n))&&speedOf(C(n))>=90);
    if(key==='swarm_heavy') carriers=deck.filter(n=>unitCount(C(n))>=3);
    if(key==='tunnel_vision') carriers=deck.filter(n=>buildingsOnly(C(n)));
    out.push({id:'m_'+key,source:'mechanics',label:RULE_TITLE[key]||key,
      reading:txt.omen||'',shadow:txt.counsel||'',
      cards:[...new Set(carriers)],rule_weight:RULE_WEIGHT[key]??DEFAULT_RULE_WEIGHT,
      voices:[]});
  }
  return out;
}
function selectThemes(community,mechanics){
  const chosen=[],seen=new Set();
  const take=t=>{ if(t&&!seen.has(t.id)){seen.add(t.id);chosen.push(t);} };
  take(community[0]); take(mechanics[0]);
  const rest=[...community,...mechanics].filter(t=>!seen.has(t.id))
    .sort((a,b)=>b.weight-a.weight||a.id.localeCompare(b.id));
  const top=Math.max(...[...chosen,...rest].map(t=>t.weight),0);
  for(const t of rest){
    if(chosen.length>=MAX_THEMES) break;
    if(chosen.length>=MIN_THEMES&&t.weight<top*KEEP_RATIO) break;
    take(t);
  }
  chosen.sort((a,b)=>b.weight-a.weight||a.id.localeCompare(b.id));
  return chosen;
}

/* ── warnings: built ONLY from what players said about facing such a hand.
   When nobody wrote about it, the second half simply does not appear. */
function grievanceThemes(deck){
  const set=new Set(deck), by=new Map();
  for(const g of (FRAG.grievances||[])){
    const present=(g.victim_cards||[]).filter(c=>set.has(c));
    if(!present.length) continue;
    const threats=(g.threat_cards||[]).filter(c=>!set.has(c));
    if(!threats.length) continue;          // already in their hand
    const key=(g.threat||'').trim().toLowerCase();
    if(!by.has(key)) by.set(key,{id:'g_'+key.replace(/[^a-z0-9]+/g,'_').slice(0,40),
      source:'grievance',label:g.threat,threat_cards:[],cards:[],voices:[]});
    const t=by.get(key);
    for(const c of threats) if(!t.threat_cards.includes(c)) t.threat_cards.push(c);
    for(const c of present) if(!t.cards.includes(c)) t.cards.push(c);
    t.voices.push({epithet:g.epithet||'',quote:g.quote||'',link:g.link||'',
                   score:+g.score||0});
  }
  const out=[];
  for(const [key,t] of by){
    const txt=(FRAG.threats||{})[key];
    if(!txt||!txt.reading) continue;
    t.voices.sort((a,b)=>b.score-a.score);
    t.posts=new Set(t.voices.map(v=>v.quote)).size;
    t.reading=txt.reading; t.shadow=txt.shadow||'';
    t.cards.sort();
    const base=t.cards.reduce((a,c)=>a+rarityBonus(c),0);
    t.weight=+(base*(1+Math.log(1+t.posts))).toFixed(3);
    out.push(t);
  }
  out.sort((a,b)=>b.weight-a.weight||a.id.localeCompare(b.id));
  return out.slice(0,3);
}

/* ── state and rendering ────────────────────────────────────── */
let deck=new Array(DECK_SIZE).fill(null), active=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function drawSpread(){
  $('spread').innerHTML = deck.map((n,i)=>`
    <div class="slot${n?' filled':''}" data-i="${i}" tabindex="0" role="button"
         aria-label="${n?esc(n):'Empty place '+(i+1)}">
      <span class="pos">${i+1}</span>
      ${n?`<span class="clear" data-clear="${i}">×</span><span class="name">${esc(n)}</span>`
        :'<span class="mark">✦</span>'}
    </div>`).join('');
  const filled=deck.filter(Boolean).length;
  $('count').textContent = filled===DECK_SIZE ? 'The spread is complete.'
    : `${filled} of ${DECK_SIZE} chosen`;
  const ready = filled===DECK_SIZE;
  $('reveal').disabled = !ready;
  $('reveal').textContent = ready ? 'Reveal my fortune'
                                  : `Choose ${DECK_SIZE-filled} more`;
}
function openPicker(i){
  active=i; $('search').value=''; renderList(''); $('veil').classList.add('open');
  setTimeout(()=>$('search').focus(),40);
}
const DROP = '<svg viewBox="0 0 12 16" xmlns="http://www.w3.org/2000/svg" '
  + 'aria-hidden="true"><path d="M6 0.5C6 0.5 11.2 7 11.2 10.6A5.2 5.2 0 0 1 6 15.7'
  + 'A5.2 5.2 0 0 1 0.8 10.6C0.8 7 6 0.5 6 0.5Z" fill="currentColor" '
  + 'fill-opacity=".22" stroke="currentColor" stroke-width="1.1"/>'
  + '<ellipse cx="4.3" cy="9.6" rx="1.1" ry="1.7" fill="currentColor" '
  + 'fill-opacity=".45"/></svg>';

function renderList(q){
  const taken=new Set(deck.filter(Boolean)); q=q.trim().toLowerCase();
  $('list').innerHTML = POOL.filter(n=>!q||n.toLowerCase().includes(q)).map(n=>{
    const e=num(C(n).elixir_cost);
    const cost = e!==null
      ? `<span class="el" title="${e} elixir">${e}${DROP}</span>`
      : '<span class="el"></span>';
    return `<div class="opt${taken.has(n)&&deck[active]!==n?' taken':''}" data-name="${esc(n)}">
      <span>${esc(n)}</span>${cost}</div>`;
  }).join('') || '<div class="opt taken">nothing by that name</div>';
}
// Turning a community label into a sentence. The labels are third-person
// predicates — "relies on crutches", "lacks defensive skill" — and dropped
// after "call you" they read as broken English. A verb phrase goes into the
// second person; anything else is named rather than forced into an article
// that does not fit ("an annoying", "a highest skill ceiling").
const IRREG={is:'are',has:'have',does:'do',was:'were',relies:'rely',cries:'cry',
  tries:'try',flies:'fly',denies:'deny',carries:'carry',goes:'go'};
const BASEV=new Set(['lack','play','spam','require','act','rely','use','get','give',
  'win','make','put','throw','refuse','overcommit','spend','encourage','need','know',
  'want','think','look','go','do','have','are','cry','flex']);
const NOT_VERB=['ss','us','ous','less','ness','ics'];
const PRON={their:'your',his:'your',her:'your',its:'your',them:'you',they:'you'};
const OPENINGS=['Others might say you {p}','There are those who say you {p}',
                'You have been told you {p}'];
const NAMINGS=['Others have a word for it: {l}','There is a name they give this: {l}',
               'You have heard it called this: {l}'];
const QUOTE_LEADS=['They may try to unsettle your balance with words like {q}',
                   'They say it in words like {q}','Their words for it run like {q}'];

function to2p(w){
  w=w.toLowerCase();
  if(IRREG[w]) return IRREG[w];
  if(BASEV.has(w)) return w;
  if(w.endsWith('ies')&&w.length>4) return w.slice(0,-3)+'y';
  if(/(ss|sh|ch|x|z)es$/.test(w)) return w.slice(0,-2);
  if(w.endsWith('s')&&!w.endsWith('ss')) return w.slice(0,-1);
  return w;
}
function isVerb(w){
  w=w.toLowerCase().replace(/,$/,'');
  if(IRREG[w]||BASEV.has(w)) return true;
  if(!/^[a-z']+s$/.test(w)) return false;
  return !NOT_VERB.some(s=>w.endsWith(s));
}
function secondPerson(label){
  let convert=true;
  return label.split(/\s+/).map(w=>{
    const low=w.toLowerCase();
    if(convert&&isVerb(low)){ convert=false; return to2p(low); }
    if(['and','or','but'].includes(low)){ convert=true; return low; }
    return PRON[low]||low;
  }).join(' ');
}
function crowdLine(label,quotes,variant){
  label=(label||'').trim(); if(!label) return '';
  variant=variant||0;
  const head=label.split(/\s+/)[0];
  let line = isVerb(head)
    ? OPENINGS[variant%OPENINGS.length].replace('{p}',esc(secondPerson(label)))
    : NAMINGS[variant%NAMINGS.length].replace('{l}','<em>'+esc(label)+'</em>');
  const qs=(quotes||[]).filter(q=>q).slice(0,2)
    .map(q=>'&ldquo;'+esc(q)+'&rdquo;');
  if(qs.length) line+='. '+QUOTE_LEADS[variant%QUOTE_LEADS.length]
    .replace('{q}',qs.join(' and '));
  return line+'.';
}

function render(){
  const {blocks,themes}=compose(deck.filter(Boolean));
  let h='', vi=0;

  for(const b of blocks){
    h+='<div class="entry">';
    if(b.card) h+=`<div class="who">${esc(b.card)}</div>`;
    if(b.text) h+=`<p>${esc(b.text)}</p>`;
    if(b.extra) h+=`<p>${esc(b.extra)}</p>`;
    const v=(b.voices||[])[0];
    if(v&&(v.trait||v.quote)) h+=`<p class="voice">${crowdLine(v.trait||'',[v.quote],vi++)}</p>`;
    h+='</div>';
  }

  for(const t of themes){
    h+='<div class="entry">';
    if(t.reading) h+=`<p>${esc(t.reading)}</p>`;
    if(t.shadow)  h+=`<p>${esc(t.shadow)}</p>`;
    if(t.source==='community'&&t.label)
      h+=`<p class="voice">${crowdLine(t.label,(t.voices||[]).map(v=>v.quote),vi++)}</p>`;
    h+='</div>';
  }

  const r=$('reading'); r.innerHTML=h; r.classList.add('show');
  r.scrollIntoView({behavior:'smooth',block:'start'});
}

/* ── events ─────────────────────────────────────────────────── */
$('spread').addEventListener('click',e=>{
  const c=e.target.closest('[data-clear]');
  if(c){ deck[+c.dataset.clear]=null; drawSpread(); return; }
  const s=e.target.closest('.slot'); if(s) openPicker(+s.dataset.i);
});
$('spread').addEventListener('keydown',e=>{
  if(e.key!=='Enter'&&e.key!==' ') return;
  const s=e.target.closest('.slot'); if(s){ e.preventDefault(); openPicker(+s.dataset.i); }
});
$('list').addEventListener('click',e=>{
  const o=e.target.closest('.opt'); if(!o||o.classList.contains('taken')) return;
  deck[active]=o.dataset.name; $('veil').classList.remove('open'); drawSpread();
});
$('search').addEventListener('input',e=>renderList(e.target.value));
$('search').addEventListener('keydown',e=>{
  if(e.key!=='Enter') return;
  const first=$('list').querySelector('.opt:not(.taken)');
  if(first){ deck[active]=first.dataset.name; $('veil').classList.remove('open'); drawSpread(); }
});
$('closePicker').onclick=()=>$('veil').classList.remove('open');
$('veil').addEventListener('click',e=>{ if(e.target===$('veil')) $('veil').classList.remove('open'); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape') $('veil').classList.remove('open'); });
// A thrown error inside render() used to leave the button looking dead with
// no clue why. Now it lands on the page where it can be read and reported.
$('reveal').onclick=()=>{
  try{ render(); }
  catch(e){
    const r=$('reading');
    r.innerHTML='<div class="entry"><div class="who">The glass has cracked</div>'
      +'<p>Something in this reading failed. Copy the line below and send it on.</p>'
      +'<p class="src" style="user-select:all;color:#e9b0b0">'
      +esc(e&&e.stack?String(e.stack).split('\n').slice(0,3).join(' | '):String(e))
      +'</p></div>';
    r.classList.add('show');
    r.scrollIntoView({behavior:'smooth',block:'start'});
    console.error(e);
  }
};
$('again').onclick=()=>{
  deck=new Array(DECK_SIZE).fill(null);
  $('reading').classList.remove('show'); $('reading').innerHTML='';
  newIntro(); drawSpread();
  window.scrollTo({top:0,behavior:'smooth'});
};

/* ── sparks inside the ball ─────────────────────────────────── */
(function(){
  const g=document.getElementById('sparks'); const NS='http://www.w3.org/2000/svg';
  for(let i=0;i<22;i++){
    const c=document.createElementNS(NS,'circle');
    const a=Math.random()*Math.PI*2, rr=Math.random()*60;
    c.setAttribute('cx',100+Math.cos(a)*rr); c.setAttribute('cy',88+Math.sin(a)*rr);
    c.setAttribute('r',(0.7+Math.random()*1.9).toFixed(2));
    c.setAttribute('fill', Math.random()<.4?'#ffe9b8':'#cbb4ff');
    c.setAttribute('class','spark');
    c.style.setProperty('--dx',(Math.random()*44-22).toFixed(1)+'px');
    c.style.setProperty('--dy',(-14-Math.random()*40).toFixed(1)+'px');
    c.style.setProperty('--dur',(3+Math.random()*4).toFixed(1)+'s');
    c.style.setProperty('--del',(Math.random()*5).toFixed(1)+'s');
    g.appendChild(c);
  }
})();

/* ── starfield ──────────────────────────────────────────────── */
(function(){
  const cv=document.getElementById('stars'), ctx=cv.getContext('2d');
  let stars=[];
  function fit(){ cv.width=innerWidth; cv.height=innerHeight;
    stars=Array.from({length:Math.min(170,Math.round(innerWidth*innerHeight/9000))},()=>({
      x:Math.random()*cv.width, y:Math.random()*cv.height,
      r:Math.random()*1.25+.2, p:Math.random()*Math.PI*2,
      s:.006+Math.random()*.016 })); }
  const reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
  function tick(){
    ctx.clearRect(0,0,cv.width,cv.height);
    for(const s of stars){ if(!reduce) s.p+=s.s;
      ctx.globalAlpha=.25+.55*(0.5+0.5*Math.sin(s.p));
      ctx.fillStyle='#e7dcff'; ctx.beginPath();
      ctx.arc(s.x,s.y,s.r,0,6.283); ctx.fill(); }
    ctx.globalAlpha=1; requestAnimationFrame(tick);
  }
  addEventListener('resize',fit); fit(); tick();
})();

newIntro(); drawSpread();
$('foot').textContent = 'build ' + (DATA.meta.built_at||'?') + ' · ' + (DATA.meta.generated_at
  ? 'drawn from what the crowd said ' + DATA.meta.generated_at : 'no snapshot');
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default=at("data", "cards.csv"))
    ap.add_argument("--fragments", default=at("data", "fragments.json"))
    ap.add_argument("--out", default=at("oracle.html"))
    a = ap.parse_args()

    if not os.path.isfile(a.fragments):
        sys.exit(f"missing {a.fragments} — run build_fragments.py first")

    all_cards = O.load_cards(a.cards)
    pool = O.playable(all_cards)
    frags = json.load(open(a.fragments, encoding="utf-8"))

    # Only cards that have both a database row and a fragment can be chosen.
    have = [n for n in pool if n in frags.get("cards", {})]
    missing = [n for n in pool if n not in frags.get("cards", {})]

    slim = {n: {k: (all_cards[n].get(k) or "") for k in KEEP if k != "card"}
            for n in pool}

    data = {"cards": slim, "pool": sorted(have, key=str.lower),
            "fragments": {"cards": frags.get("cards", {}),
                          "archetypes": frags.get("archetypes", {}),
                          "rules": frags.get("rules", {}),
                          "themes": frags.get("themes", []),
                          "threats": frags.get("threats", {}),
                          "grievances": frags.get("grievances", [])},
            "meta": {"generated_at": frags.get("meta", {}).get("generated_at", ""),
                     "built_at": __import__("datetime").datetime.now()
                                 .strftime("%Y-%m-%d %H:%M")}}

    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    open(a.out, "w", encoding="utf-8").write(html)

    size = os.path.getsize(a.out) / 1024
    print(f"{a.out}  ({size:.0f} KB)")
    print(f"  selectable cards : {len(have)} of {len(pool)} playable")
    print(f"  pairings         : {len(data['fragments']['archetypes'])}")
    print(f"  rules            : {len(data['fragments']['rules'])}")
    print(f"  themes           : {len(data['fragments']['themes'])}")
    gr = data["fragments"]["grievances"]
    warned = {c for g in gr for c in g.get("victim_cards", [])}
    print(f"  warnings         : {len(gr)} pairs, {len(warned)} cards can be warned")
    if not gr:
        print("  <-- note: no grievances, so no spread will get a second half")
    if missing:
        print(f"  <-- CHECK: {len(missing)} playable cards have no fragment and "
              f"cannot be chosen: {', '.join(missing[:8])}"
              + ("…" if len(missing) > 8 else ""))
    if len(have) < O.DECK_SIZE:
        print("  <-- CHECK: fewer cards than a deck needs; the page will be unusable")


if __name__ == "__main__":
    main()