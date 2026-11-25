"""Canonical identifiers for persisted per-line material consumption bases."""

MCL_KG_PER_PIECE = "mcl_kg_per_piece"
WEIGHT_PER_PIECE = "weight_per_piece"
ONE_ITEM_PER_OCCURRENCE = "one_item_per_occurrence"

WEIGHT_PER_PIECE_BASES = frozenset({MCL_KG_PER_PIECE, WEIGHT_PER_PIECE})
