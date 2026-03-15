# Ghave, Guru of Spores — Primer Analysis

**Commander:** Ghave, Guru of Spores {2}{W}{B}{G} — 0/0 Fungus Shaman, Abzan (WBG)
**Color identity:** Abzan (WBG)
**Source:** Moxfield primer by Abzkaban (from TappedOut, #1 ranked Ghave deck)
**Theme:** Infinite combo engine using +1/+1 counters + token generation + sacrifice outlets; highest-power casual to optimized

---

## Commander Ability (the trigger)

> Ghave, Guru of Spores enters with 5 +1/+1 counters.
> {1}, Remove a +1/+1 counter from a creature you control: Create a 1/1 green Saproling creature token.
> {1}, Sacrifice a creature: Put a +1/+1 counter on target creature you control.

Key design insight:
- Ghave is a **mana sink** — given enough mana, he can convert counters to tokens or tokens to counters at will
- He needs a **mana source** (Earthcraft, Ashnod's Altar, Phyrexian Altar, Cryptic Trilobite) AND a **counter source** (Doubling Season, Cathars' Crusade, Illusionist's Bracers, Renata, Good-Fortune Unicorn, Mikaeus) to go infinite
- Many combos are **independent of Ghave** (persist loops don't require him) — giving the deck 60+ different paths to infinite from the persist strategy alone

---

## Primary Synergy Packages

### 1. Counter sources (combo enablers)
Cards that add +1/+1 counters when tokens are created (enabling self-sustaining loops):
- **Doubling Season**: Tokens and counters are both doubled → removing 1 counter from Ghave makes 2 Saprolings; sacrificing 1 Saproling puts 2 counters on Ghave
- **Illusionist's Bracers**: Doubles Ghave's activated abilities
- **Cathars' Crusade**: Each creature ETB puts a +1/+1 counter on ALL creatures you control → each Saproling created via Ghave adds a counter back to Ghave
- **Good-Fortune Unicorn**: Each creature ETB puts a +1/+1 counter on a target creature
- **Renata, Called to the Hunt**: All creatures ETB with an additional +1/+1 counter (she has Persist-compatible synergy)
- **Mikaeus, the Unhallowed**: Non-humans get Undying; also enables Persist + Undying alternating loop

### 2. Mana sources (combo sustainers)
Cards that generate mana from the sacrifice/token loop:
- **Earthcraft**: Tap any creature to untap a basic land → Saprolings tap for mana
- **Ashnod's Altar**: Sacrifice a creature → {2} colorless (combos with anything)
- **Phyrexian Altar**: Sacrifice a creature → {1} colored mana (more flexible, enables colored mana combos)
- **Cryptic Trilobite**: Remove a counter from it → {C}{C}; store counters to activate abilities

### 3. Persist + Undying loops (commander-independent combos)
60+ different paths to infinite without Ghave:
- **Persist creatures**: Kitchen Finks (ETB: gain 2 life, -1/-1 on return), Puppeteer Clique (ETB: reanimate opponent's creature), Woodfall Primus (ETB: destroy non-creature permanent)
- **Undying creatures**: Young Wolf (1-mana 2/2), Strangleroot Geist (haste), Geralf's Messenger (ETB: opponents lose 2 life)
- **Mikaeus + any persist creature = alternating loops**: Persist brings back with -1/-1, Undying from Mikaeus removes it, Persist triggers again
- **Counter source negates -1/-1**: When Kitchen Finks returns with -1/-1, Cathars' Crusade immediately puts a +1/+1 counter on it, negating the -1/-1 and resetting the Persist loop

### 4. Recursion engine (Karmic Guide + Reveillark)
Commander-independent infinite loop:
- Sacrifice Karmic Guide first, then Reveillark
- Reveillark's LTB trigger returns Karmic Guide (power 2) and any other creature with power ≤ 2
- Karmic Guide returns Reveillark
- Result: Access to anything in graveyard at will; infinite ETB and death triggers with a free sac outlet

---

## Combo Lines

### Line 1: Ghave + Earthcraft + Doubling Season (infinite tokens + mana + counters)
1. Tap land for {G} → remove counter from Ghave → 2 Saprolings (Doubling Season)
2. Tap a Saproling (Earthcraft) to untap the land → tap land again → remove another counter
3. Sacrifice a tapped Saproling (Ghave's second ability) → 2 counters back on Ghave (Doubling Season)
- Result: Net more Saprolings each cycle → infinite tokens, infinite mana, Ghave becomes infinite/infinite

### Line 2: Ghave + Ashnod's Altar + Doubling Season (or any counter source)
1. Tap land → remove counter from Ghave → 2 Saprolings
2. Sacrifice one Saproling to Ashnod's Altar → {2}
3. Use {1} to remove another counter → 2 Saprolings; use {1} to sacrifice Saproling → 2 counters on Ghave
- Result: Infinite tokens, infinite colorless mana, Ghave infinite/infinite

### Line 3: Cathars' Crusade + Ashnod's Altar + Kitchen Finks (persist, commander-independent)
1. Sacrifice Kitchen Finks → {2} from Altar
2. Finks returns with -1/-1, triggers Cathars' Crusade → +1/+1 counter on all your creatures (cancels -1/-1)
3. Repeat → infinite mana, infinite ETB triggers (gain 2 life each time)

### Line 4: Mikaeus + Kitchen Finks + free sac outlet (alternating persist/undying)
1. Finks dies → persist triggers (returns with -1/-1 counter)
2. Finks dies again → no +1/+1 counter so undying triggers (returns with +1/+1 counter)
3. Finks dies again → no -1/-1 counter so persist triggers
4. Alternates forever → infinite ETB/death triggers

### Line 5: Karmic Guide + Reveillark + sac outlet (infinite recursion)
1. Sacrifice Karmic Guide → sacrifice Reveillark
2. Reveillark LTB: return Karmic Guide + any creature ≤ power 2
3. Karmic Guide ETB: return Reveillark
4. Infinite ETB triggers; recycles any creature in graveyard repeatedly

---

## Deckbuilding Principles Stated by Author

1. **Cards that fit multiple combos are preferred** — Ashnod's Altar combos with more lines than any other single card; select for modularity.
2. **Combo synergy > individual card value** — The deck is a machine of interlocking parts; no card should only fit one combo.
3. **Multiple infinite outcomes** — Mana, tokens, +1/+1 counters, death triggers, ETB triggers — having infinite resources in multiple dimensions gives flexibility in win conditions.
4. **Commander-independent backup combos** — Persist + Undying loops work without Ghave; 60+ combo paths means the deck is resilient to commander removal.
5. **High power philosophy with flexibility** — Not cEDH (by stated design) but uses the same modular thinking; optimized list included for those who want it.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Counter-to-token / token-to-counter conversion loop | Ghave's bidirectional conversion creates self-sustaining loops given a mana source + counter source; not modeled | Ghave (the hub), Doubling Season / Cathars' Crusade (counter sources), Altars (mana sources) |
| Persist + counter-placement = infinite loop | Persist creature + any ETB counter placer negates the -1/-1 counter, enabling infinite sacrifice loops | Kitchen Finks, Woodfall Primus (persist); Cathars' Crusade, Renata, Good-Fortune Unicorn (counter cancelers) |
| Mikaeus + persist = alternating undying/persist | Mikaeus grants undying to non-humans; a persist creature alternates between persist and undying for infinite loops | Mikaeus the Unhallowed (the enabler), all persist non-human creatures |
| Karmic Guide + Reveillark recursion loop | Classic infinite ETB/death loop; very specific creature power ≤ 2 requirement; enables any ETB-based win condition | Karmic Guide, Reveillark, Saffi Eriksdotter (three-piece recursion loop) |
| Doubling Season counter doubling | When tokens or counters would be placed, double them; creates massive imbalance in any loop | Doubling Season, Illusionist's Bracers (ability doubling), Anointed Procession (token doubling) |
