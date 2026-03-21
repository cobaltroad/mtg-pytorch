---
name: look-up
description: Look up a Magic: The Gathering card by name from the MTGJSON AtomicCards cache. Use when the user says "look up [card]", "what does [card] do", or "find card [name]".
---

Look up the MTG card "$ARGUMENTS" in the MTGJSON AtomicCards cache by running this command from the project root:

```bash
docker compose run --rm --no-deps ingest python3 -c "
import json, sys, subprocess

result = subprocess.run(['find', '/data', '-name', 'mtgjson_AtomicCards.json'],
                       capture_output=True, text=True)
candidates = [p for p in result.stdout.strip().split('\n') if p]
if not candidates:
    print('AtomicCards cache not found — run: docker compose run --rm ingest')
    sys.exit(1)
path = candidates[0]

data = json.load(open(path))['data']

query = '$ARGUMENTS'.lower()
matches = [k for k in data if k.lower() == query]
if not matches:
    matches = [k for k in data if query in k.lower()]
if not matches:
    print(f'Card not found: $ARGUMENTS')
    sys.exit(1)

card_name = matches[0]
face = data[card_name][0]
ci = face.get('colorIdentity', [])
print(f'=== {card_name} ===')
print(f'Mana Cost : {face.get(\"manaCost\") or \"—\"}  (CMC {face.get(\"manaValue\", \"?\")})')
print(f'Type      : {face.get(\"type\", \"—\")}')
print(f'Colors    : {face.get(\"colors\", [])}   Identity: {ci}')
if face.get(\"power\"):
    print(f'P/T       : {face[\"power\"]}/{face[\"toughness\"]}')
if face.get(\"loyalty\"):
    print(f'Loyalty   : {face[\"loyalty\"]}')
kw = face.get('keywords', [])
if kw:
    print(f'Keywords  : {\", \".join(kw)}')
legal = face.get('legalities', {}).get('commander', 'unknown')
print(f'Commander : {legal}')
print()
print(face.get('text') or '(no oracle text)')
"
```

Display the output as-is. If multiple cards match the query, note the closest match used.
