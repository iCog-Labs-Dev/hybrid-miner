# Hybrid Miner

Hybrid Miner is an independent repository for ontology-constrained semantic
formation, PeTTa-based pattern mining, candidate evaluation, and later causal
promotion decisions. It exchanges versioned data records with the
Neuro-Symbolic-LLM project. The pinned `hyperon-miner/` submodule supplies an
upstream mining implementation; this repository will define and test the
paper-specific workflow around it.

## Current implementation

The first Phase 0 module is an installable semantic-document contract in
`src/hybrid_miner/contracts/semantic_document.py`. It implements the structured
S1 extraction record in Appendix A.4 of the Hybrid Pattern Mining and Semantic
Formation design note:

- source document and corpus identifiers;
- source text and half-open Unicode code-point spans;
- model, parser, prompt, and ontology version provenance;
- typed entities and extraction hypotheses;
- confidence, alternatives, and optional interpretation notes;
- an explicit `ProvisionalRelation` representation; and
- deterministic JSON serialization for artifact comparison.

The document validates argument references, declared argument types, span
bounds, duplicate entity identifiers, confidence, and provisional-relation
structure. `validate_against_signatures()` additionally checks ontology
versions, rejects undeclared predicate names, and checks each observed argument
type against the corresponding allowed type set. A document with no
assertions is valid; the parser must not invent a relation merely to fill a
record.

The caller supplies signatures from the ontology versions named in the
document. Ontology catalog loading and the complete typed ingestion boundary
are subsequent modules. This contract does not claim to perform PeTTa
ingestion, canonicalization, graph matching, or pattern mining.

## Repository boundaries

```text
Neuro-Symbolic-LLM semantic parser
    -> versioned SemanticDocument JSONL
Hybrid Miner ontology validation and PeTTa ingestion
    -> canonical S1 semantic graph
Hybrid Miner greedy and later non-myopic mining
    -> versioned candidate / promotion artifact
Neuro-Symbolic-LLM Tier 2 promotion and MORK retrieval
```

The same ontology bundle version must be used to compile parser prompts and
validate their output. The parser and miner repositories must exchange
serialized artifacts or a released contract package; neither should import
the other's working-tree files by path.

The local ontology and `PatternRecord` prototypes currently present in
Neuro-Symbolic-LLM remain transitional until this repository publishes their
canonical replacements and serialization compatibility is tested. No
cross-repository contract ownership changes are asserted by this first module.

## Development

Python 3.11 or later is required. From this repository's root:

```sh
python -m pip install -e ".[dev]"
python -m pytest tests/unit -q
python -m ruff check src tests
python -m black --check src tests
python -m mypy src/hybrid_miner
pre-commit install
```

Pre-commit runs Ruff and Black on changed Python files. CI additionally runs
mypy and the contract tests. It verifies that the upstream miner remains a
git submodule, but does not claim to execute its PeTTa implementation yet.
The submodule is currently pinned to
`e955479bca1603597529d175cc22ac1583c67e15`. Initialize it only when
the mining adapter is implemented:

```sh
git submodule update --init --recursive
```

## Implementation sequence

1. Migrate the versioned ontology catalog and define its signature compiler.
2. Make parser output conform to `SemanticDocument`; preserve rejected records.
3. Validate, canonicalize, and ingest S1 assertions in PeTTa.
4. Implement the paper's typed greedy baseline using the pinned upstream miner.
5. Publish a canonical candidate and promotion contract for Tier 2.
6. Add the non-myopic, SVM, causal, and reasoning stages in the order specified
   by the design note.

Each stage requires source-linked quality and cost measurements. Benchmarks
must identify their input corpus, ontology and parser versions, miner revision,
hardware, and exact software versions. Generated vectors or unmined example
terms cannot establish semantic quality or end-to-end performance.
