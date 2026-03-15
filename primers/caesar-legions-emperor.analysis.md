# Caesar, Legion's Emperor — Primer Analysis

**Commander:** Caesar, Legion's Emperor {R}{W}{B} — 4/4 Legendary Creature — Human Soldier
**Color identity:** Mardu (RWB)
**Source:** Moxfield primer by unknown author
**Theme:** Aggressive go-wide tokens + aristocrats + burn; attack trigger sacrifices a creature to choose two of three modes (create soldiers, draw a card, deal X damage equal to token count); win via combat damage, aristocrats drain, or noncombat burn

---

## Commander Ability (the trigger)

> Whenever you attack, you may sacrifice another creature. When you do, choose two — create two 1/1 white Soldier creature tokens with haste that are tapped and attacking; or draw a card and you lose 1 life; or Caesar, Legion's Emperor deals X damage to any target, where X is the number of creature tokens you control.

Key constraints:
- **Attack trigger, not "Caesar attacks"** — Caesar triggers whenever the player attacks with any creature; Caesar himself does not need to be among the attackers; Reconnaissance can withdraw Caesar from combat after triggering
- **Sacrifice is optional ("you may")** — if no creature is available to sacrifice, or if sacrificing is undesirable, Caesar's trigger can be passed without activation; but the engine requires sac fodder to function
- **Reflexive trigger ("when you do")** — the sacrifice itself is not the trigger; the sacrifice fires a secondary reflexive trigger that presents the three-mode choice; the reflexive trigger goes on the stack separately; this is a two-layer trigger that standard regex on oracle text does not capture
- **Token count for mode 3 is evaluated at resolution** — the burn damage counts creature tokens as the reflexive trigger resolves, which happens before blocks are declared but after the two tapped-and-attacking soldiers enter; the new soldiers count toward the X
- **Mode 1 soldiers enter tapped and attacking** — they participate in combat immediately on the turn they are created, bypassing summoning sickness; they can be sacrificed to Caesar again next turn
- **Card draw costs 1 life** — mode 2 is primarily used in early turns when the token count is low and the burn (mode 3) isn't yet meaningful
- **Commander-reliant engine** — without Caesar the deck's other pieces are individually powerful but the compounding engine (sacrifice one token → create two soldiers → burn for token count) stops working

---

## Primary Synergy Packages

### 1. Repetitive token generators (sac fodder each turn)
The engine requires a creature to sacrifice each combat; repetitive token generators provide this:
- **Bitterblossom**: Creates a 1/1 evasive Faerie with flying each upkeep; permanent sac fodder starting turn 2; enables Caesar on curve at turn 4 with a token already in play
- **Black Market Connections**: Modular enchantment; can create a Mercenary token each turn, or provide Treasure and card draw; fills multiple roles
- **Loyal Apprentice**: 2-CMC creature that creates a 1/1 Thopter with flying on each attack step as long as Caesar (or any commander) is on the field; instant sac fodder loop
- **Skrelv's Hive**: Creates a 1/1 Mite token each upkeep; Bitterblossom variant in white
- **Dollmaker's Shop // Porcelain Gallery**: Creates a Doll token on each attack; two-room enchantment that activates early and later becomes a pump effect
- **Mardu Ascendancy / Hanweir Garrison**: Additional repetitive token producers on attack
- **Ophiomancer / Jadar, Ghoulcaller of Nephalia**: Creature-based token generators that provide sac fodder on their own trigger schedule

### 2. Attack trigger doublers
Doubling Caesar's trigger turns each attack step into two full cycles of sacrifice + two modes:
- **Isshin, Two Heavens as One**: Doubles all "whenever you attack" triggers; Caesar fires twice per attack; two sacrifices → up to four Soldier tokens entering tapped and attacking; burn fires twice off the full token count; rated 9/10 by the author
- **Windcrag Siege**: New Room enchantment that doubles attack triggers or creates token attackers with lifelink and haste; strict upgrade in the attack trigger category
- **Roaming Throne**: Doubles all triggered abilities (not just attack triggers); broader coverage than Isshin; targets death triggers and ETB triggers as well; considered a strict upgrade to Isshin in theory but draws more removal

### 3. Token doublers (amplify the two-soldier output)
Each mode-1 soldier creation is doubled:
- **Anointed Procession**: Each "create a token" effect creates twice as many; Caesar's mode 1 creates four Soldiers instead of two; rated 10/10
- **Mondrak, Glory Dominus**: Same doubling effect on a creature body; has built-in indestructible protection; also a sacrifice outlet; rated 9/10
- **Ojer Taq, Deepest Foundation**: Triples token creation (upgrade from doubling); very high power ceiling; difficult to cast but has land backside

### 4. Aristocrats / drain package
Creature death and ETB triggers drain life from opponents:
- **Mirkwood Bats**: Triggers on each token creation AND on each sacrifice; with Caesar's ability creating two soldiers and consuming one sacrifice, Mirkwood Bats pings three times per Caesar trigger cycle; rated 9/10
- **Kambal, Profiteering Mayor**: Pings the table when tokens enter the battlefield under the controller's control; compounds with the mass token generation
- **Commissar Severina Raine**: Sacrifice outlet + lifegain + card draw; also has an attack trigger that drains the table based on army size; runs the aristocrats strategy independently of Caesar
- **Blood Artist / Zulaport Cutthroat / Nadier's Nightblade**: Standard drain on creature death; every sacrificed token and every soldier lost in combat pings opponents
- **Elas il-Kor, Sadistic Pilgrim**: Pings on creature ETB and on creature death; both directions of Caesar's token cycle (creation and sacrifice) trigger it
- **Teysa Karlov**: Doubles death triggers; every sacrifice pings twice through Blood Artist / Mirkwood Bats; considered an upgrade path toward a pure aristocrats variant

### 5. Burn / noncombat damage (mode 3 amplifiers)
Caesar's third mode scales with token count; these amplify or supplement that damage:
- **Warleader's Call**: ETB burn effect (one damage per token ETB) + blanket +1/+1 anthem; doubles as a token-ETB pinger and a pump effect; rated 8/10
- **Goblin Bombardment**: Free sacrifice outlet that deals 1 damage per sacrificed creature; can threaten burst damage at instant speed; protects board from wipes by converting tokens to direct damage; rated 10/10
- **Agate Instigator**: Pings the whole table for each creature ETB; with Offspring doubles the effect; rated 6/10
- **Court of Embereth**: Creates tokens each turn (monarch upside) while also dealing X damage to opponents equal to token count each upkeep; redundant coverage of Caesar's mode 3 effect

### 6. Combat enablers and evasion
Maximizing the combat damage path:
- **Reconnaissance**: 1-CMC enchantment that allows withdrawing attackers from combat after triggers resolve; gives Caesar and all attackers pseudo-vigilance; prevents losses in unfavorable blocks; rated 10/10
- **Song of Totentanz**: Creates an X-token army of haste Rats; doubles as a massive finisher or haste-enabler for an existing token army; rated 10/10
- **Adeline, Resplendent Cathar**: Creates tokens equal to opponents without triggering off attacking; vigilance body; rated 6/10
- **Anim Pakal, Thousandth Moon**: Compounds tokens every combat (one Gnome per non-Gnome attacker); synergizes with both combat and aristocrats packages; rated 7/10
- **Sundering Eruption**: Destroys an opponent's land, then prevents blocks on the next attack as a follow-up; rated 7/10; modal (land if needed)

---

## Combo Lines

### Line 1: Caesar + Isshin + Mirkwood Bats + Mondrak (exponential snowball, not infinite)
- **Requirements:** Caesar on field, Isshin on field, Mirkwood Bats on field, Mondrak on field; at least one creature to sacrifice; ability to attack
- **Loop:** Declare attackers → Isshin doubles Caesar's trigger → two Caesar triggers fire; sacrifice one creature to each trigger (or sacrifice one and let the second pass if no second sac outlet); each trigger with Mondrak creates four Soldiers (two doubled to four); Mirkwood Bats pings three times per trigger cycle (once for the sacrifice, twice for each pair of tokens with Mondrak doubling to four = five pings total if both triggers fire)
- **Result:** Four to eight new tapped-and-attacking Soldiers per combat step; opponent life totals drain rapidly; board compounds every turn without opponent interaction
- **Win:** Combat damage from the growing soldier army, supplemented by Mirkwood Bats drain each trigger cycle

### Line 2: Caesar + Goblin Bombardment + large token count (burst burn win)
- **Requirements:** Caesar on field; Goblin Bombardment on field; Song of Totentanz or Secure the Wastes generating a large number of tokens; enough creature tokens to sacrifice
- **Sequence:** Cast Song of Totentanz for X ≥ 10; create a large Rat army with haste; attack; Caesar triggers → sacrifice one creature → mode 3 deals X damage equal to token count; simultaneously sacrifice remaining tokens to Goblin Bombardment targeting same opponent; combine noncombat burn with Bombardment damage
- **Result:** Can kill a single opponent in one attack step without requiring combat damage to resolve
- **Win:** Kill opponents sequentially with combined mode-3 burn + Bombardment damage

### Line 3: Caesar + Isshin + Reconnaissance (safe attack loop)
- **Requirements:** Caesar, Isshin, and Reconnaissance on field; any attackers on field
- **Loop:** Declare all creatures as attackers → all attack triggers fire (doubled by Isshin) → before the damage step, use Reconnaissance to remove any creatures the player does not want to trade; creatures withdrawn are considered to have attacked this turn for all trigger purposes
- **Result:** Attack triggers (Caesar's token creation, Anim Pakal's Gnome creation, Loyal Apprentice's Thopter, Adeline's tokens, Commissar Severina's drain) all fire every turn without risk of losing any creatures in combat; board grows unchecked
- **Win:** Accumulate enough tokens over several turns that combat damage overwhelms defenses, or mode 3 burn kills opponents

---

## Deckbuilding Principles Stated by Author

1. **Caesar requires sac fodder before he's cast** — Caesar should not come down without a creature or repeating token generator already in play; without sac fodder he is a 5-CMC 4/4 with no immediate effect; the ideal hand has a 2-3 CMC token generator (Bitterblossom, Loyal Apprentice, Ophiomancer) plus lands and a rock before Caesar arrives on turn 4
2. **Repetitive generators are better than impulse generators** — Bitterblossom, Skrelv's Hive, Loyal Apprentice, and Dollmaker's Shop each produce a token every turn cycle; Secure the Wastes, Martial Coup, and Grand Crescendo produce many tokens once; repetitive sources recover from board wipes more reliably because they regenerate sac fodder immediately
3. **Isshin is the highest-priority non-Caesar card** — doubling Caesar's trigger doubles the soldiers created (two triggers = up to four tapped-and-attacking soldiers), doubles mode 3 burn, and doubles card draw; rated the highest synergy card in the deck at 9/10; plan around him being removed frequently
4. **Be aggressive early, disclose power level honestly** — the deck can have an overwhelming board state by turns 6-9 without interaction; the author advises front-foot play and honest pre-game disclosure about Anointed Procession, Mondrak, and Charismatic Conqueror; the deck is closer to bracket 4 than bracket 3
5. **The deck has three distinct win modes** — (a) go-wide combat with token army, (b) noncombat burn via mode 3 + Warleader's Call + Goblin Bombardment, and (c) aristocrats drain via Mirkwood Bats + Blood Artist; the deck can be built to specialize in any one or kept generalist; the current list runs all three
6. **Board wipes are the primary weakness** — Caesar's engine restarts slowly without existing sac fodder; the deck includes Teferi's Protection, Grand Crescendo, and Goblin Bombardment as protection; the author considers replacing narrow board wipes (Hour of Reckoning) with more modular ones (Farewell, Austere Command) that hit non-creature permanents

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Attack-trigger commander (positional, not textual) | Caesar's core trigger fires "whenever you attack" — it is not tied to any keyword on the permanents synergizing with him; no card in the deck has oracle text saying "when you sacrifice a creature during combat, create Soldiers"; the trigger relationship is entirely about Caesar's text being active in a combat step, not about oracle text overlap between cards | Caesar + Loyal Apprentice; Caesar + Bitterblossom; Caesar + Adeline |
| Reflexive "when you do" trigger | The sacrifice is optional; the second trigger ("when you do, choose two") is a reflexive ability that fires only if the sacrifice occurs; the two-trigger structure (attack trigger → reflexive trigger) is not a pattern standard oracle text regex captures; the pipeline would see "sacrifice a creature" and "create tokens" as separate unrelated clause fragments, not as a causal chain | Caesar's full oracle text; Commissar Severina Raine's similar structure |
| Token count as burn scalar (mode 3) | Caesar's third mode deals X damage where X equals the number of creature tokens currently controlled; the damage scales dynamically with board state; the pipeline cannot model a card that queries the current token count at resolution as a damage multiplier; it cannot identify which token producers increase mode 3's damage output | Mondrak + Caesar mode 3; Isshin doubling soldiers entering → higher token count → more damage |
| Attack trigger doubling (Isshin / Windcrag) | Isshin doubles "whenever you attack" triggers; this is a second-order meta-synergy where one card amplifies another card's trigger rather than a direct oracle-text synergy with Caesar's output; the pipeline models producer → consumer edges, not amplifier → trigger count edges | Isshin, Two Heavens as One + Caesar; Windcrag Siege + Caesar |
| Sac-to-create-and-attack in one action | Caesar's mode 1 creates soldiers that enter tapped and attacking on the same combat step they are created; they participate in the current combat as attackers but are created mid-combat stack resolution; the pipeline cannot model "token created after attackers declared" → "token participates in current combat" as a positional game-state relationship | Caesar mode 1 + Warleader's Call (tokens entering tapped and attacking still trigger Warleader's Call) |
| Reconnaissance pseudo-vigilance loop | Reconnaissance untaps attacking creatures before damage, effectively giving pseudo-vigilance and enabling attack triggers without committing to damage; the synergy is between the combat phase timing of Reconnaissance's activation window and attack trigger resolution; no oracle text on other cards says "safe to attack with" | Reconnaissance + Caesar + any high-value attacker like Caesar himself |
| Repetitive token generator as sac fodder engine | The primer distinguishes repetitive generators (one token per turn cycle, permanent) from impulse generators (many tokens once, then empty); this classification is a deckbuilding heuristic about steady-state value that the pipeline cannot model from oracle text alone | Bitterblossom vs. Secure the Wastes; Loyal Apprentice vs. Call the Coppercoats |
| Mirkwood Bats triple-ping on Caesar trigger | Mirkwood Bats pings once per "token or Treasure created" and once per "sacrifice" trigger; with Caesar's one sacrifice creating two soldiers, Mirkwood Bats fires three separate times per Caesar trigger (one sacrifice ping + two token ETB pings); this multi-ping calculation requires understanding that Caesar's trigger generates exactly two distinct "token creation" events and one "sacrifice" event simultaneously | Mirkwood Bats + Caesar (three pings per trigger cycle, not one) |
