# SQL-based oracle-text filters for exile-casting mechanics.  These identify
# cards that generate or consume "cast from exile" game states — relevant for
# commanders like Prosper, Tome-Bound (treasures when you cast from exile) or
# Faldorn, Dread Wolf Herald (wolf token when you cast from exile).
#
# Key breakdown:
#   cast_from_exile       — cards that explicitly let you cast cards from exile
#                           ("you may cast it ... from exile", cascade/discover,
#                           foretell, suspend, hideaway, impulse-draw payoffs)
#   cast_from_exile_payoff — cards that trigger or scale when you cast a spell
#                           from exile (Prosper, Faldorn, Gonti-style payoffs)

PATTERNS: dict[str, str] = {
    "cast_from_exile": (
        "oracle_text ILIKE '%%cast%%from exile%%'"
        " OR oracle_text ILIKE '%%you may cast%%without paying%%mana cost%%'"
    ),
    "cast_from_exile_payoff": (
        "oracle_text ILIKE '%%whenever you cast%%from exile%%'"
        " OR oracle_text ILIKE '%%cast a spell from anywhere other than%%hand%%'"
        " OR oracle_text ILIKE '%%cast a spell from exile%%'"
        " OR oracle_text ILIKE '%%casts a spell from exile%%'"
        " OR oracle_text ILIKE '%%play a card from exile%%'"
        " OR oracle_text ILIKE '%%plays a card from exile%%'"
        " OR oracle_text ILIKE '%%plays a land from exile%%'"
        " OR oracle_text ILIKE '%%didn%%t cast%%from%%hand%%'"
    ),
}
