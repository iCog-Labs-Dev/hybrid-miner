"""Versioned data contracts shared with semantic parsers and runtime consumers."""

from hybrid_miner.contracts.pattern_record import (
    PatternProvenance,
    PatternRecord,
    PatternRecordError,
    validate_extraction_pattern_record,
)
from hybrid_miner.contracts.pattern_record import (
    SourceSpan as PatternSourceSpan,
)
from hybrid_miner.contracts.semantic_document import (
    EntityRecord,
    ParserProvenance,
    RuleLiteral,
    RuleVariable,
    SemanticAssertion,
    SemanticDocument,
    SemanticRule,
    SourceSpan,
)

__all__ = [
    "EntityRecord",
    "ParserProvenance",
    "RuleLiteral",
    "RuleVariable",
    "SemanticAssertion",
    "SemanticDocument",
    "SemanticRule",
    "SourceSpan",
    "PatternProvenance",
    "PatternRecord",
    "PatternRecordError",
    "PatternSourceSpan",
    "validate_extraction_pattern_record",
]
