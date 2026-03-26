"""Manabase staple SQL — colored-mana-producing lands.

Selects lands whose oracle text shows they can produce colored mana:
  tap-for-colored     — dual lands, shock lands, check lands, filter lands,
                        pain lands, bounce lands, tri-lands, etc.
                        (Overgrown Tomb, Breeding Pool, Hinterland Harbor,
                        Command Tower, City of Brass, Mana Confluence)
  tap-for-any         — "add one mana of any color" phrasing used by Command
                        Tower, Exotic Orchard, Reflecting Pool, Rupture Spire
  X-for-color         — Nykthos-style: "{T}: Choose a color. Add …"
                        matched by the same ~* tap-add regex since the text
                        contains both {T} and "Add"

Fetch lands (Verdant Catacombs, Terramorphic Expanse) are classified under
utilityland.py since their oracle text does not contain a direct mana-add
clause.

Color identity filtering (card_ci ⊆ commander_ci) is applied by the caller,
so a mono-green commander will only receive lands that are colorless or green
in identity.

RATE reflects the approximate share of a 99-card Commander deck devoted to
the non-basic colored land package (not counting basic lands, which are
handled outside the staple system).
"""

from __future__ import annotations

RATE: float = 0.16

SQL: str = (
    "type_line ILIKE '%%Land%%'"
    " AND ("
    # standard "{T}: Add {W}/{U}/{B}/{R}/{G}" phrasing (also catches {T}: Add {G}{U}, etc.)
    "  oracle_text ~* '\\{T\\}.*[Aa]dd.*\\{[WUBRG]'"
    "  OR"
    # "add one mana of any color" — Command Tower, Exotic Orchard, Mana Confluence
    "  oracle_text ILIKE '%%add one mana of any color%%'"
    "  OR"
    # "add mana of any color" — Chromatic Lantern lands, etc.
    "  oracle_text ILIKE '%%add mana of any color%%'"
    " )"
)
