# Anim Pakal, Thousandth Moon — Primer Analysis

**Commander:** Anim Pakal, Thousandth Moon {2}{R}{W} — 1/3 Human Soldier, Boros
**Color identity:** Boros (RW)
**Source:** Moxfield primer by Hydrax (last updated 2026-02-12)
**Theme:** +1/+1 counter accumulation → mass Gnome token generation on attack; ETB burn payoffs; Skullclamp engine

---

## Commander Ability (the trigger)

> Whenever Anim Pakal, Thousandth Moon attacks, she gets +1/+1 counter, then create a number of 1/1 colorless Gnome artifact creature tokens equal to the number of +1/+1 counters on her. Those tokens are tapped and attacking.
> (This also triggers if any other non-Gnome creature attacks — Anim Pakal doesn't need to be one of the attackers)

Key constraints:
- **Self-buffing**: She grows by 1 counter each attack, so Gnome production scales over time
- **Gnomes are tapped and attacking**: They can be blocked and die immediately; protection for them (Dolmen Gate, Losheel) is essential
- **Trigger fires when ANY non-Gnome creature attacks** — Anim Pakal just needs to be in play, not necessarily attacking
- **Gnomes are artifact creature tokens**: Artifact ETB payoffs (Reckless Fireweaver, Purphoros) trigger for each Gnome

---

## Primary Synergy Packages

### 1. Skullclamp — the primary card draw engine
Skullclamp is specifically called out as a key card draw piece:
- **Mechanism**: Equip Skullclamp ({1}) to a 1/1 Gnome token → the Gnome has 1 toughness - 1 = 0 → immediately dies → draw 2 cards
- **Iteration**: Each Gnome is a free 2-card draw for {1}; with 10+ Gnomes generated per attack in the late game, this draws the entire hand and more
- **Also enables other sacrifice triggers**: Phyrexian Altar, Bennie Bracks (draw when creatures with counters or tokens ETB), etc.

**Model insight:** Skullclamp's synergy with 1-toughness token strategies (Aristocrats, Gnomes, Saprolings, etc.) is a critical pattern the pipeline currently cannot detect. The model needs to learn that "1/1 token" + "Skullclamp" = "draw 2 per token sacrifice."

### 2. ETB burn / pingers (the secondary win condition)
Each Gnome ETBing triggers artifact/creature ETB payoffs:
- **Reckless Fireweaver**: Each artifact ETB → 1 damage to each opponent; 10 Gnomes = 10 damage each
- **Purphoros, God of the Forge**: Each creature ETB → 2 damage to each opponent; 10 Gnomes = 20 damage each
- **Weftstalker Ardent, Ingenious Artillerist, Warleader's Call, Impact Tremors, Molten Gatekeeper, General Kreat, Agate Instigator**: Various burn on creature/artifact ETB
- **Devilish Valet**: Power doubles for each creature ETB; with 10 Gnomes, power reaches 1024+

**Model insight:** This is a textbook "ETB pinger" pattern — mass token generation triggers burn. The pipeline's TRIGGER_PATTERNS should have "artifact enters battlefield" → Reckless Fireweaver / Purphoros / Impact Tremors as a consumer pattern.

### 3. +1/+1 counter acceleration
Faster counter accumulation = more Gnomes sooner:
- **Per-combat counter placers**: Luminarch Aspirant (one counter to any creature at combat start), Agent Bishop Man in Black, Keleth Sunmane Familiar (commander attack → counter on target creature)
- **Per-upkeep/turn**: Noble Heritage, Orzhov Advokist, Agitator Ant (end of turn counters)
- **Trigger doublers**: Roaming Throne (doubles Anim Pakal's counter gain AND Gnome creation), Mondrak Glory Dominus (doubles tokens), Windcrag Siege
- **Spell-based**: Homestead Courage, Angelfire Ignition, Feat of Resistance, Angelic Intervention (protection + counter)

### 4. Gnome protection / evasion
1/1 Gnomes entering tapped and attacking are extremely vulnerable:
- **Combat damage prevention**: Dolmen Gate (attacking creatures you control can't receive combat damage), Losheel Clockwork Scholar (first-strike creatures + Gnomes don't receive combat damage)
- **Evasion**: Eldrazi Monument (flying + indestructible + +1/+1 to all tokens), Starry-Eyed Skyrider, Toby Beastie Befriender (flying to tokens)
- **Reconnaissance**: Activate at instant speed to remove creatures from combat before damage → Gnomes deal damage (unblocked) but can be pulled back if blocked; effectively gives free attacking Gnomes

### 5. Card draw
- **Token-based draw**: Losheel Clockwork Scholar (draw when non-token artifact creature ETBs — Gnomes trigger this), Tocasia's Welcome, Bennie Bracks Zoologist, Caretaker's Talent, Enduring Innocence
- **Neyali Suns' Vanguard**: Impulse draw when opponent is attacked by tokens (attack all three opponents for 3 draws)
- **Skullclamp**: As described above — most efficient draw engine in the deck

---

## Combo Lines

No formal infinite combos stated (primer says "no combos but can demolish tables by turn 7"). Closest to combo:

### Engine: Anim Pakal + Purphoros/Reckless Fireweaver + counter accumulation
- Turn 5: Anim Pakal has 4 counters (1 per attack, 3 attacks) + Purphoros in play
- Swing with any non-Gnome creature: Make 4 Gnomes → 8 damage to each opponent via Purphoros
- Turn 6: 5 counters → 5 Gnomes → 10 damage
- By turn 7-8 with Mondrak (doubling Gnomes): 10 Gnomes → 20 damage per attack

### Engine: Anim Pakal + Skullclamp
- Late game: 8 counters → 8 Gnomes per attack → equip Skullclamp to each for {1} each → draw 16 cards for 8 mana
- Immediate rebuild after board wipes; consistent late-game gas

---

## Deckbuilding Principles Stated by Author

1. **Early creature (turn 2) > mana rock (turn 2)** — Non-Gnome creatures needed to trigger Anim Pakal's ability; prefer Arabella, Keleth, or Luminarch Aspirant over a mana rock on turn 2.
2. **Protect Anim Pakal** — Instant-speed protection (Mithril Coat, Angelic Intervention) is held in reserve; the deck's power is tied to her being in play.
3. **Card draw source online early** — Welcoming Vampire / Losheel prioritized before "win more" pieces; sustained card draw is the lifeblood.
4. **Track commander damage** — Anim Pakal becomes large via counters; 21 commander damage is achievable by mid-game.
5. **Roaming Throne naming "Human"** — Doubles Anim Pakal's trigger for double counters AND double Gnomes simultaneously.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| **Skullclamp + 1-toughness tokens** | Equipping Skullclamp to a 1/1 token causes immediate death → draw 2; highest-priority gap per TODO.md | Skullclamp (the combo piece); all 1/1 token generators (Gnome, Saproling, etc.) |
| Mass attack-phase token generation | Creating tokens during the Declare Attackers step (tapped and attacking) is a unique timing that enables combat damage + ETB triggers simultaneously | Anim Pakal (the producer), all ETB payoffs (consumers) |
| Artifact token ETB → burn | Gnomes are artifact creature tokens; both artifact-ETB pingers (Reckless Fireweaver) and creature-ETB pingers (Purphoros) trigger | Reckless Fireweaver, Purphoros, Ingenious Artillerist, Impact Tremors (consumers); Gnome tokens (producers) |
| Reconnaissance "untap all after damage" | Reconnaissance allows attacking, dealing damage, then untapping all attackers before combat ends — effectively giving vigilance while triggering all "attack" triggers | Reconnaissance (unique timing interaction with Anim Pakal's attack trigger) |
| Trigger doubling (Roaming Throne) | Roaming Throne doubles commander's triggered ability — doubles both the counter gain AND the Gnome creation | Roaming Throne, Mondrak Glory Dominus (token doubling) |
