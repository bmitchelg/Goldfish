"""EDH-Goldfish CLI v4.1.1.

Design contract (Brandon, 2026-10-31):
- Parser uses ONLY Scryfall structured fields + Scryfall community function
  tags. No oracle-text parsing except the two agreed land exceptions.
- Tags are for COUNTING ONLY. No game-state effect is ever inferred from a tag.
- Play policy is Brandon's exact 10-step procedure.
- Measurements are concrete counts/distributions only: no archetypes, no
  composite scores, no TTK/EOT, no invented constants.
"""

__version__ = "4.1.1"
