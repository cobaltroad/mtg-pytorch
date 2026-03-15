# Chatterfang, Squirrel General — Primer Analysis

**Commander:** Chatterfang, Squirrel General {2}{G} — 3/3 Legendary Creature — Squirrel Warrior, Forestwalk
**Color identity:** Golgari (BG)
**Source:** Moxfield primer by OblivionTy7
**Theme:** High-power / fringe cEDH Golgari tokens; Chatterfang's doubling trigger converts any token ETB into equal numbers of additional Squirrel tokens; primary win via Pitiless Plunderer or Warren Soultrader infinite Treasure/Squirrel loops into drain; multiple backup combo lines; midrange pressure as fallback

---

## Commander Ability (the trigger)

> Forestwalk.
> Whenever one or more tokens are put into play under your control, create that many 1/1 green Squirrel creature tokens.
> {B}, Sacrifice X Squirrels: Target creature gets -X/-X until end of turn.

Key constraints:
- **Doubling trigger, not replacement effect** — when any number of tokens ETB under the controller's control, an equal number of additional Squirrel tokens are created as a separate triggered ability; this is NOT "instead create double" — the original tokens still enter, and Squirrels are created on top
- **Tokens of any type trigger it** — Treasure tokens, Food tokens, Clue tokens, creature tokens from other effects, and even tokens created by Chatterfang's own Squirrel generation all trigger the ability if they ETB at a separately stacked time (though looping requires understanding stack sequencing)
- **Sacrifice outlet on the commander** — {B}, Sac X Squirrels gives -X/-X to a target creature; this allows Chatterfang to protect itself (sacrifice in response to removal), act as removal for opposing creatures, and function as a free free sacrifice outlet in combo lines; the author notes holding up {B} is a deterrent against removal
- **Forestwalk** — largely irrelevant in most games but provides occasional unblockability against green-heavy decks
- **The doubling relationship is positional, not textual** — no card that creates tokens has text saying "Chatterfang also creates Squirrels"; the synergy is entirely mediated through Chatterfang's trigger reading the board state

---

## Primary Synergy Packages

### 1. Core combo engine: Pitiless Plunderer + Chatterfang
The primary infinite combo:
- **Pitiless Plunderer**: Whenever a creature the controller controls dies, create a Treasure token; this Treasure triggers Chatterfang to create a Squirrel
- **Loop with 2 Squirrels**: Pay {B}, sacrifice 2 Squirrels → Chatterfang ability deals -2/-2 to any target creature; Plunderer creates 2 Treasures on the 2 Squirrel deaths → each Treasure triggers Chatterfang → 2 new Squirrels enter → before Chatterfang ability resolves, crack one Treasure for {B} → repeat
- **Key insight**: Each loop sacrifices 2 Squirrels, creates 2 Treasures (each triggering a Squirrel), and nets one additional Treasure per cycle; the loop generates infinite Treasures and infinite Squirrel ETB/LTB triggers
- **With only 1 Squirrel**: Can kill any creature on the board by targeting it repeatedly, but does not gain Treasures net positive; requires growing to 2 Squirrels first

### 2. Core combo engine: Warren Soultrader + Chatterfang
Secondary infinite combo; lower bar but requires a third piece to win:
- **Warren Soultrader**: Sacrifice a creature, lose 1 life, create a Treasure token; the Treasure triggers Chatterfang → create a Squirrel; sacrifice the Squirrel to Soultrader → create a Treasure → create another Squirrel; repeat
- **Loop**: Each iteration sacrifices 1 creature and loses 1 life, but nets a Treasure; the loop is limited only by the controller's life total
- **Requires an outlet to win**: Blood Artist, Zulaport Cutthroat, or Marionette Apprentice must be in play to convert the infinite Squirrel deaths into lethal damage; without an outlet the loop produces infinite Treasures but no win
- **Resilience**: Warren Soultrader costs only {2}{B}; easier to cast from graveyard or hand than Pitiless Plunderer ({3}{R}) in a BG deck; more accessible as a recovery line

### 3. Win conditions and outlets
Cards that convert infinite token loops into wins:
- **Blood Artist / Zulaport Cutthroat**: Each creature death pings an opponent and gains life; with infinite Squirrel sacrifice, drain all opponents to 0; searchable from Eldritch Evolution on a Squirrel
- **Marionette Apprentice**: Pings for each Treasure that leaves the battlefield (including being cracked); converts infinite Treasure cracking into drain damage; requires enough life for Soultrader loops
- **The Meathook Massacre**: Drains for creature ETBs and deaths; functions as a board wipe if needed; unique in that it can be played conservatively before the combo assembles
- **Finale of Devastation**: X ≥ 10 tutors any creature from library or graveyard to battlefield AND gives the entire team +X/+X and haste; closes games through combat damage when enough creatures are assembled; requires enough untapped Squirrels for the haste attack
- **Acererak the Archlich**: With Aluren, cast Acererak repeatedly for free → venture through Lost Mine of Phandelver and Dungeon of the Mad Mage → ping opponents, create tokens, draw cards; tokens from dungeons trigger Chatterfang, creating more Squirrels

### 4. Aluren + Acererak (independent combo, no Chatterfang required)
- **Requirements**: Aluren (cast creatures with CMC ≤ 3 for free) + Acererak the Archlich (CMC 3)
- **Loop**: Cast Acererak for free → venture through Lost Mine of Phandelver; choose treasure, 1/1 token, scry, ping, or draw at each dungeon room; return Acererak to hand; repeat
- **Value**: Creates Treasures (each triggers Chatterfang if present), creates creature tokens (each triggers Chatterfang), pings opponents, draws cards through dungeon rooms
- **Win**: Ping opponents to death through repeated dungeon completion, or draw into an outlet
- **Warning**: Aluren can be used by opponents; only cast it when Acererak is already in hand and the kill is available

### 5. Ruthless Knave + Chatterfang + Earthcraft (mana-generating loop)
- **Requirements**: Chatterfang + Ruthless Knave + Earthcraft + a Basic land + a creature to sacrifice
- **Loop**: Pay {2}{B} and sacrifice a creature to Knave → creates 2 Treasures + 2 Squirrels (from Chatterfang); tap 2 Squirrels on Earthcraft to untap a Basic twice, floating {BG}{BG}; use {BG}{BG} + crack a Treasure to replay the loop; nets one Treasure per cycle
- **Result**: Infinite Treasures; infinite Squirrels (some tapped, some untapped); draw through Knave's second ability (sacrifice 3 Treasures → draw a card)

### 6. Eternal Witness + Saw in Half + Culling the Weak (instant-speed loop)
- **Requirements**: Eternal Witness on battlefield; Saw in Half in hand; Culling the Weak in hand or graveyard; {2}{B}{B} to cast both; an outlet for the death triggers
- **Loop**: Cast Saw in Half targeting Eternal Witness → EWit is destroyed and replaced by two 1/1 EWit token copies → each token's ETB returns Saw in Half and any other card from graveyard; cast Culling the Weak sacrificing one EWit token for {B}{B}{B}{B}; use {B}{B}{B}{B} to cast Saw again targeting remaining EWit token; two new EWit tokens return Saw + Culling; repeat
- **Triggers Chatterfang**: EWit tokens are creature tokens; each pair of EWit tokens entering the battlefield triggers Chatterfang to create 2 Squirrels (which also have ETB/death triggers for outlets)
- **Win**: Blood Artist, Zulaport, or Marionette resolve the infinite death triggers
- **Does not require Chatterfang**: This combo generates infinite creature ETB and death triggers independently

### 7. Chatterfang + EWit + Saw in Half + Earthcraft (mana-positive variant)
- **Adds Earthcraft to the EWit loop**: Tap newly created EWit tokens and Squirrel tokens against a Basic to generate mana each loop; nets infinite colored mana limited only by Basic land types
- **Win**: With infinite colored mana, cast any outlet from hand or recast Chatterfang plus Finale of Devastation

### 8. Midrange board presence (fallback)
- **Chitterspitter**: Creates Squirrel tokens at sorcery speed for {2}{G}; goes tall with a +1/+1 counter for each Squirrel; provides a path to winning through combat when combos are not assembled
- **Blood Artist + board wipe**: Blood Artist + The Meathook Massacre drains opponents when a board wipe resolves; holding up this line deters opponents from casting their own board wipes
- **Squirrel Sanctuary**: Creates a Squirrel whenever Chatterfang returns to hand (bounce trigger); looping Chatterfang + Sanctuary generates Squirrels for Finale of Devastation combat win

---

## Combo Lines

### Line 1: Chatterfang + Pitiless Plunderer (infinite Treasures + Squirrels)
- **Requirements:** Chatterfang and Pitiless Plunderer on field; {B} floating or available; 2 Squirrels on field
- **Loop:** Pay {B}, sacrifice 2 Squirrels to Chatterfang targeting any creature (or Chatterfang itself); Plunderer creates 2 Treasures on the 2 Squirrel deaths; each Treasure triggers Chatterfang → 2 new Squirrels enter the battlefield; before Chatterfang's sacrifice ability resolves, crack one Treasure for {B}; repeat steps
- **Result:** Infinite Treasures; infinite Squirrel ETB and death triggers; can kill any creatures on the board as a byproduct
- **Win:** Blood Artist / Zulaport Cutthroat / Marionette Apprentice / Meathook Massacre (any drain outlet) converts infinite Squirrel deaths into lethal damage; Acererak the Archlich wins by dungeon venturing; Finale of Devastation pumps the Squirrel army for combat

### Line 2: Chatterfang + Warren Soultrader (infinite Treasures, life-limited)
- **Requirements:** Chatterfang and Warren Soultrader on field; a creature to sacrifice; an outlet in play
- **Loop:** Sacrifice any creature to Soultrader → lose 1 life, create a Treasure; Treasure triggers Chatterfang → create a Squirrel; sacrifice the Squirrel to Soultrader → lose 1 life, create another Treasure → create another Squirrel; repeat
- **Result:** As many Treasures as the controller's life total permits; infinite Squirrel ETB and death triggers within that constraint
- **Win:** Blood Artist / Zulaport turn the infinite Squirrel deaths into gain-based drain; Marionette works if sufficient life is available; Finale of Devastation with enough untapped Squirrels for combat

### Line 3: Aluren + Acererak the Archlich (independent, no Chatterfang required)
- **Requirements:** Aluren on field; Acererak in hand; dungeon deck available
- **Loop:** Cast Acererak for {0} via Aluren → venture through Lost Mine of Phandelver and/or Dungeon of the Mad Mage; on each venture, choose: ping opponents (Cave of Carnage / Great Shaft rooms), create Treasure (Treasure Vault), create 1/1 tokens (Goblin Warren), draw cards, or scry; Acererak returns to hand; repeat from any Squirrels/Treasures created → Chatterfang creates Squirrels from each token room
- **Result:** Ping opponents to 0 through repeated dungeon completion; or draw into an outlet and use infinite Treasures to cast it
- **Win:** Dungeon completion damage accumulates; or Blood Artist resolves alongside infinite creature ETBs

### Line 4: Eternal Witness + Saw in Half + Culling the Weak (instant-speed, Chatterfang optional)
- **Requirements:** Eternal Witness on field; Saw in Half in hand; Culling the Weak in hand or graveyard; {2}{B}{B} initial mana; an outlet in play (Blood Artist, Zulaport, etc.)
- **Loop:** Cast Saw in Half on EWit → two 1/1 EWit tokens created (triggers Chatterfang: 2 Squirrels); EWit token ETBs return Saw and one other card (Culling if needed); cast Culling on one EWit token → {B}{B}{B}{B}; cast Saw on remaining EWit token → two more EWit tokens + 2 more Squirrels; return Saw + Culling; repeat
- **Result:** Infinite EWit token ETBs and deaths; infinite Squirrel ETBs and deaths; outlet drains opponents
- **Win:** Any drain outlet resolves; or Chatterfang's Squirrel accumulation reaches Finale of Devastation threshold

---

## Deckbuilding Principles Stated by Author

1. **Pitiless Plunderer / Warren Soultrader are the primary win conditions** — the deck is built to search for either one and combo off; Eldritch Evolution on a Squirrel can find either outlet piece directly; when shields are down, assembling Plunderer or Soultrader is the correct line
2. **Hold up {B} as deterrent** — Chatterfang's sacrifice ability requires {B} and Squirrels; maintaining {B} open while Squirrels are on board forces opponents to play around potential -X/-X removal or Chatterfang self-sacrifice; the threat is often as valuable as the activation
3. **Multiple backup combo lines provide redundancy** — the deck does not rely solely on Pitiless Plunderer; EWit + Saw + Culling, Chatterfang + Knave + Earthcraft, and Aluren + Acererak are independent win lines that work around disruption; the author explicitly lists which combos do not require Chatterfang
4. **Grindy midrange is the true fallback** — Blood Artist + board wipes deter opponents from sweeping; Chitterspitter generates a tall Squirrel army; Skullclamp provides card draw on any 1/1 Squirrel death; the deck can function as a midrange Golgari deck when combos are unavailable
5. **Outlet flexibility for Soultrader loop** — Warren Soultrader limits wins to the controller's life total; Blood Artist and Zulaport Cutthroat are preferred outlets because they add life back to the pool as they drain opponents, partially offsetting Soultrader's life loss; Marionette Apprentice works only with sufficient life; Meathook Massacre requires the highest life total among players
6. **Aluren is a dangerous enabler** — the author warns explicitly: Aluren can be used by all players; only cast it when Acererak is in hand and the kill is ready; it should not be cast as a value play

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Token doubling via commander trigger | Chatterfang's trigger creates additional Squirrel tokens equal to the number of tokens entering under the controller's control; this doubling relationship is not expressed in the oracle text of any token-producing card; no other card says "when tokens enter, create more tokens" except Chatterfang itself; the pipeline cannot detect that every token producer in the deck is a Chatterfang enabler | Pitiless Plunderer (Treasure → Squirrel), Ruthless Knave (Treasure → Squirrel), Aluren + Acererak (dungeon tokens → Squirrel) |
| Pitiless Plunderer as combo piece via Chatterfang | Pitiless Plunderer creates a Treasure when a creature dies; in isolation this is a mana ramp effect; the combo only exists because each Treasure triggers Chatterfang to create a Squirrel, which can be sacrificed again; the three-card relationship (Plunderer → Treasure token → Chatterfang trigger → Squirrel → sacrifice → Plunderer) is a cycle that oracle text matching cannot reconstruct | Pitiless Plunderer + Chatterfang + 2 Squirrels |
| Warren Soultrader loop requiring life payment | Warren Soultrader's cost structure (sacrifice + lose 1 life → Treasure) means the loop is bounded by life total rather than mana; the pipeline has no model for a combo whose limit is life total rather than card availability or mana; it cannot identify that Blood Artist is required specifically because it offsets life loss during the loop | Warren Soultrader + Blood Artist (life recovery during loop) |
| Outlet card selection for infinite loops | The deck needs a third piece (drain outlet) to win from the Plunderer and Soultrader loops; the primer identifies that Eldritch Evolution on a Squirrel can fetch Blood Artist, Zulaport Cutthroat, or Marionette Apprentice to serve as the outlet; this "tutor to complete the combo" relationship is a search-based synergy, not an oracle text synergy | Eldritch Evolution + Squirrel → Blood Artist / Zulaport Cutthroat (outlet fetched to complete loop) |
| Saw in Half token-cloning creates cascade of ETB triggers | Saw in Half destroys a creature and creates two 1/1 token copies; those copies have the same ETB abilities as the original (Eternal Witness: return card from graveyard); both copies trigger Chatterfang (2 tokens enter = 2 Squirrels); the interaction requires knowing that token copies retain ETB abilities, that destroying the original is mandatory, and that the result is two independent ETB triggers — none of which can be derived from oracle text matching alone | Saw in Half + Eternal Witness + Chatterfang |
| Earthcraft as Squirrel-powered mana engine | Earthcraft lets the controller tap a creature it controls to untap a Basic land of the same basic land type; in the Knave loop, newly created Squirrels tap against Basics for mana; the pipeline cannot model "Squirrel token enters tapped from Chatterfang's trigger → tap it to Earthcraft → untap a Basic → float mana" as a productive mana cycle | Earthcraft + Squirrel tokens from Chatterfang + Basic Forests/Swamps |
| Dungeon-token-to-Squirrel chain | Acererak venturing through dungeons creates 1/1 tokens in specific rooms (e.g., Goblin Warren in Lost Mine of Phandelver); those tokens trigger Chatterfang → Squirrels; the pipeline cannot model the dungeon mechanic at all (dungeon rooms are not oracle text on Acererak and the room benefits are not card-to-card synergy edges) | Acererak + Aluren + Lost Mine of Phandelver (token rooms) → Chatterfang Squirrel trigger |
| Sacrifice commander as protection (threat deterrence) | Chatterfang's {B}, Sac X Squirrels ability is used as a deterrence signal — holding open {B} with Squirrels on board is a non-oracle-text strategic signal that the pipeline cannot model; the "threat of activation" is a table-politics pattern, not an oracle text synergy | Chatterfang self-sacrifice ability used as deterrent to protect itself from removal |
| Combo piece identification through primer context | The primer specifies that Pitiless Plunderer is the primary win condition; from oracle text alone, Plunderer reads as a mana ramp card; its status as a primary win condition is entirely dependent on Chatterfang being in the command zone and is only discoverable through the primer's explicit designation, not through oracle text analysis | Pitiless Plunderer classified as win condition (not ramp) by primer context |
