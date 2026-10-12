# Hybrid Miner

Hybrid Miner is an independent package for semantic-record validation and
pattern mining. It will adapt the pinned `hyperon-miner` implementation and
exchange validated artifacts with the Neuro-Symbolic-LLM project. It does not
run the frozen model, semantic parser, Tier 2 template store, or retrieval
service.

## Current status

Implemented:

- `SemanticDocument`: typed entities and extraction hypotheses, source
  spans, confidence, parser/ontology provenance, schema validation, and
  deterministic JSON serialization.
- `PatternRecord`: candidate structure, source-linked provenance,
  deterministic JSON serialization, and a validator that restricts records
  to the current extraction-first stage.
- `OntologyBundle`: a caller-supplied, versioned JSON input that validates
  type and predicate signatures and checks `SemanticDocument` assertions.
- JSONL ingestion: validates parser documents against the bundle and writes
  accepted and quarantined records to separate artifacts.
- PeTTa emission: converts validated assertions to version-qualified relation
  facts, a separate queryable PeTTa evidence space, and a JSONL provenance
  sidecar.
- A depth-zero adapter that runs the pinned Hyperon Miner on the emitted fact
  graph and records alpha-normalized exact support with runtime revisions.
- Exact PeTTa binding recovery linked to validated source assertions, followed
  by construction of typed, source-linked candidate `PatternRecord` artifacts.
- A pinned PeTTa/Hyperon Miner smoke run for a controlled support-count fixture.
- Unit tests, formatting/lint/type-check configuration, CI, and a pinned
  upstream Git submodule.

Not implemented: production ontology catalog ownership, PeTTa-side assertion
validation and canonicalization, candidate scoring and Tier 2
promotion, non-myopic search, SVM/STLM control, or causal experiments.
The smoke run executes the submodule locally; CI does not yet execute PeTTa.

## Repository layout

```text
hybrid-miner/
|-- src/hybrid_miner/
|   |-- __init__.py
|   |-- contracts/
|   |   |-- __init__.py
|   |   |-- semantic_document.py
|   |   `-- pattern_record.py
|   |-- ontology/
|   |   `-- bundle.py
|   |-- ingestion/
|   |   |-- __main__.py
|   |   `-- semantic_documents.py
|   |-- emission/
|   |   |-- __main__.py
|   |   |-- emit_petta.py
|   |   `-- symbols.py
|   `-- mining/
|       |-- __main__.py
|       |-- petta_runner.py
|       |-- canonicalize_patterns.py
|       |-- exact_matches.py
|       `-- build_pattern_records.py
|-- tests/fixtures/
|   `-- example-ontology-bundle.json
|-- tests/unit/
|   |-- test_semantic_document.py
|   |-- test_pattern_record.py
|   |-- test_ontology_bundle.py
|   |-- test_semantic_document_ingestion.py
|   |-- test_petta_emission.py
|   |-- test_mining_canonicalization.py
|   |-- test_exact_matches.py
|   `-- test_pattern_record_builder.py
|-- tests/smoke/               # local pinned PeTTa runtime check
|-- hyperon-miner/             # pinned Git submodule
|-- .github/workflows/ci.yml
|-- .pre-commit-config.yaml
|-- pyproject.toml
`-- README.md
```

Planned package boundaries are `mining/` (upstream adapter and greedy search),
`evaluation/` (support, surprise, traces, and cost),
`controllers/` (later search control), and `causal/` (later intervention
results). These directories will be added only with implementations and
tests; they are not present today.

## Requirements

- Python 3.11 or later.
- Runtime dependency: `pydantic>=2.6,<3`.
- Development extras: pytest, Ruff, Black, mypy, and pre-commit, as declared
  in `pyproject.toml`.
- No model API, GPU, MORK, Redis, or Docker is required for contracts,
  ingestion, emission, or unit tests. Mining requires SWI-Prolog, the pinned
  Hyperon Miner submodule, and a PeTTa checkout at the revision recorded in
  `tests/smoke/petta_revision.txt`.

The `hyperon-miner/` submodule is pinned to
`e955479bca1603597529d175cc22ac1583c67e15`. Mining requires this
checkout; it is not needed for contracts, ingestion, or emission. Initialize it
before running the mining adapter:

```sh
git submodule update --init --recursive
```

## Installation and checks

From this repository's root:

```sh
python -m pip install -e ".[dev]"
python -m pytest tests/unit -q
python -m ruff check src tests
python -m black --check src tests
python -m mypy src/hybrid_miner
pre-commit install
```

CI runs the unit tests, formatting, lint, type checks, and submodule-pin
verification. It does not execute the upstream miner. The local PeTTa check
is documented in `tests/smoke/README.md`.

## Parser-document ingestion and PeTTa emission

The caller supplies a JSON ontology bundle and parser `SemanticDocument` JSONL.
From this repository's root:

```sh
python -m hybrid_miner.ingestion --bundle bundle.json --input documents.jsonl --accepted-output accepted.jsonl --rejected-output rejected.jsonl
python -m hybrid_miner.emission --bundle bundle.json --input accepted.jsonl --atoms-output facts.metta --metadata-output facts.metadata.metta --provenance-output facts.provenance.jsonl
python -m hybrid_miner.mining --atoms facts.metta --petta-root /path/to/PeTTa --miner-root ./hyperon-miner --output support.jsonl --min-support 2
python -m hybrid_miner.mining.exact_matches --bundle bundle.json --documents accepted.jsonl --atoms facts.metta --provenance facts.provenance.jsonl --support support.jsonl --petta-root /path/to/PeTTa --miner-root ./hyperon-miner --output exact-bindings.jsonl --rejected-output exact-rejected.jsonl
python -m hybrid_miner.mining.build_pattern_records --bundle bundle.json --documents accepted.jsonl --atoms facts.metta --provenance facts.provenance.jsonl --exact exact-bindings.jsonl --output pattern-records.jsonl --rejected-output pattern-records.rejected.jsonl
```

Ingestion rejects malformed records, ontology-version and signature mismatches,
and duplicate document identities without discarding the rejected source lines.
Emission revalidates its input. It uses readable, reversible escaping to keep
document and entity identities separate and qualifies catalog predicates by
ontology version. Repeated assertions of the same fact create one mining atom
but retain separate evidence records. Import `facts.metta` into the mining space
and `facts.metadata.metta` into a separate metadata space. The latter contains
predicate signatures, entity types, source spans, confidence, extraction status,
and parser provenance; JSONL retains the complete assertion record. The
metadata also maps each version-qualified PeTTa predicate symbol to its base
relation name and ontology version. The suffix is an exact-match graph identity,
not an embedding input or a replacement for the structured relation identity.
The mining adapter executes the upstream depth-zero miner and serializes observed
support with stable variable names, conjunction order, graph digest, and runtime
revisions. The exact-match audit then reruns each supported pattern in PeTTa,
checks its reported count, grounds every binding against emitted facts, and
joins each fact to validated document assertions and source spans. A mismatch
between the graph, documents, bundle, support revision, and provenance aborts
the run without output. Candidate-level failures, including a conjunction that
reuses one ground fact or crosses source documents, go to the rejected output.
Both exact-match outputs are audit artifacts, not promoted templates. The exact
query has a per-run timeout and a 10,000-binding limit per candidate; throughput
has not been established for large corpora. Record materialization rechecks each
accepted binding against the source documents, fact graph, and provenance before
constructing a validated Q1 `PatternRecord`. It stores the semantic relation
names in `canonical_term`, and retains version-qualified identity separately in
`ontology_groundings` and audit features. Every variable is exposed as an
interface variable in this initial representation; existential projection and
Tier 2 promotion require separate policies. These are candidate records, not
embedded or indexed Tier 2 templates. A record that cannot be represented
without conflicting variable types or source evidence is written to the
record-level rejected output.

## Contract use

```python
from hybrid_miner.contracts import (
    PatternRecord,
    SemanticDocument,
    validate_q1_pattern_record,
)
from hybrid_miner.ontology import OntologyBundle

bundle = OntologyBundle.load_json("ontology-bundle.json")
document = SemanticDocument.model_validate_json(document_json)
bundle.validate_document(document)
pattern = validate_q1_pattern_record(
    PatternRecord.from_canonical_json(pattern_json)
)
document_artifact = document.canonical_json()
pattern_artifact = pattern.to_canonical_json()
```

`ontology-bundle.json`, `document_json`, and `pattern_json` represent input
artifacts. The bundle requires `ontology-bundle-v1`, a bundle identity and
version, ontology component versions, declared types, and predicate signatures.
It checks document provenance and assertion signatures. Matching a candidate
to its source document and constructing the runtime PeTTa graph remain
downstream work. The semantic-document `SourceSpan` and pattern
`PatternSourceSpan` are different types and have distinct package exports.

`SemanticDocument` carries an explicit `semantic-document-v1` version.
`PatternRecord` currently has no in-record schema version. Pin a released
package revision or introduce a versioned envelope before downstream
publication. Its local copy in Neuro-Symbolic-LLM is transitional and must
be removed after compatibility and dependency pinning are verified.

## Component boundaries

```text
Neuro-Symbolic-LLM parser
  -> SemanticDocument
Hybrid Miner validation and emission
  -> PeTTa facts and assertion provenance
PeTTa matching and depth-zero support counting
  -> canonicalized support records
Exact binding recovery and candidate construction
  -> source-linked PatternRecord candidates
Future candidate evaluation
  -> promotion decisions
Neuro-Symbolic-LLM Tier 2 promotion
  -> TemplateRecord, MORK storage, FAISS retrieval
```

The repositories must exchange serialized artifacts or a pinned package,
not import each other's working-tree files. The parser and miner must use
the same versioned ontology bundle. Ownership of the ontology files
currently held in Neuro-Symbolic-LLM is still being resolved with the
parser team.

## Implementation requirements

- Validate ontology versions, predicate signatures, argument types, and
  source spans before graph ingestion. Preserve rejected input for audit.
- Canonicalize accepted assertions into PeTTa without fabricating relations
  or discarding parser provenance.
- Implement bounded, typed greedy expansion over extracted graphs. Record
  observed support, exact-match evidence, candidate ancestry, features,
  and runtime cost.
- Evaluate candidates on source-linked corpora and explicit hard negatives.
  Every result must identify corpus, ontology, parser, miner revision,
  hardware, metric, and baseline.
- Require a validated `PatternRecord` before Tier 2 promotion. Template
  embeddings and MORK insertion belong to Neuro-Symbolic-LLM, not this
  package.
- Keep later learned-search and causal modules separate from the greedy
  baseline until each has tests and measured evidence.

The pinned smoke fixture establishes one upstream support count. A small
parser-derived graph has also produced one source-linked candidate record.
This does not establish general mining quality, cross-repository promotion,
or hardware performance.
