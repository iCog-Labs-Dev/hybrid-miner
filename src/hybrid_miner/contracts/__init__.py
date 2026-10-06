"""Versioned data contracts shared with semantic parsers and runtime consumers."""

from hybrid_miner.contracts.pattern_record import (
    PatternProvenance,
    PatternRecord,
    PatternRecordError,
    validate_q1_pattern_record,
)
from hybrid_miner.contracts.pattern_record import (
    SourceSpan as PatternSourceSpan,
)
from hybrid_miner.contracts.semantic_document import (
    EntityRecord,
    ParserProvenance,
    SemanticAssertion,
    SemanticDocument,
    SourceSpan,
)

__all__ = [
    "EntityRecord",
    "ParserProvenance",
    "SemanticAssertion",
    "SemanticDocument",
    "SourceSpan",
    "PatternProvenance",
    "PatternRecord",
    "PatternRecordError",
    "PatternSourceSpan",
    "validate_q1_pattern_record",
]
