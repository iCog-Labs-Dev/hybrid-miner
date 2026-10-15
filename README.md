# Hybrid Miner

Hybrid Miner is an independent package for semantic-record validation and
pattern mining. It executes a pinned `hyperon-miner` baseline and exchanges
validated artifacts with the Neuro-Symbolic-LLM project. It does not
run the frozen model, semantic parser, Tier 2 template store, or retrieval
service.

## Initial release scope

The initial release candidate covers an extraction-stage, depth-zero mining
pipeline and its versioned parser-document interchange. Its capabilities are:

- `SemanticDocument`: typed entities and extraction hypotheses, source
  spans, confidence, parser/ontology provenance, schema validation, and
  deterministic JSON serialization.
- `PatternRecord`: candidate structure, source-linked provenance,
  deterministic JSON serialization, and an extraction-stage policy validator.
- `OntologyBundle`: a caller-supplied, versioned JSON input that validates
  type and predicate signatures and checks `SemanticDocument` assertions.
- JSONL ingestion: validates parser documents against the bundle and writes
  accepted and quarantined records to separate artifacts.
- PeTTa emission: converts validated assertions to version-qualified relation
  facts, a separate queryable PeTTa evidence space, and a JSONL provenance
  sidecar.
- PeTTa ingress: a packaged MeTTa rule checks emitted facts against evidence,
  predicate declarations, entity types, allowed argument types, and recorded
  source spans. The rule places each assertion in an accepted or quarantined
  PeTTa space. A rejected assertion prevents release of a partial mining graph.
- A depth-zero adapter that runs the pinned Hyperon Miner on a supplied fact
  graph and records alpha-normalized raw support with runtime revisions.
- An admitted-support entry point that runs PeTTa ingress and passes only its
  accepted graph to the miner; quarantine stops mining before support output.
- Exact PeTTa binding recovery linked to validated source assertions, followed
  by construction of typed, source-linked candidate `PatternRecord` artifacts.
- Unit tests, a pinned-runtime integration check, formatting/lint/type-check
  configuration, CI, and a pinned upstream Git submodule.

Later phases cover production ontology-catalog integration, PeTTa-side
source-text offset validation and full canonicalization, candidate scoring,
Tier 2 promotion, non-myopic search, SVM/STLM control, and causal experiments.
Raw support is not a promoted template or a measure of neural utility.
The separate CI runtime job executes PeTTa admission and Hyperon Miner on a
small deterministic fixture. It does not assess parser extraction quality or
benchmark mining performance.

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
|   |   |-- semantic_documents.py
|   |   |-- petta_gate.py
|   |   `-- ingest_semantic_assertion.metta
|   |-- emission/
|   |   |-- __main__.py
|   |   |-- emit_petta.py
|   |   `-- symbols.py
|   `-- mining/
|       |-- __main__.py
|       |-- admitted_support.py
|       |-- petta_runner.py
|       |-- canonicalize_patterns.py
|       |-- exact_matches.py
|       `-- build_pattern_records.py
|-- tests/fixtures/
|   `-- example-ontology-bundle.json
|-- tests/integration/
|   `-- test_petta_runtime.py
|-- tests/unit/
|   |-- test_semantic_document.py
|   |-- test_pattern_record.py
|   |-- test_ontology_bundle.py
|   |-- test_semantic_document_ingestion.py
|   |-- test_petta_emission.py
|   |-- test_mining_canonicalization.py
|   |-- test_exact_matches.py
|   |-- test_pattern_record_builder.py
|   |-- test_admitted_support.py
|   `-- test_petta_gate.py
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
  Hyperon Miner submodule, and a PeTTa checkout. PeTTa requires SWI-Prolog
  9.3 or newer and Python for Janus interoperability. The imported upstream
  miner utility `boost_petta.py` also imports SciPy. The runtime exercised for
  ingress and mining is PeTTa revision
  `ebdb51168135f7fdf68c59acf7521a1b85c19ba0`.

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

CI runs unit tests, formatting, lint, type checks, and submodule-pin
verification. A separate job also executes the pinned PeTTa runtime and
Hyperon Miner against a deterministic two-document fixture: it requires
admission and exact source-linked bindings, then verifies that removing
metadata quarantines assertions and prevents mining. This check does not
replace manual review of real parser output.

### Reproducing the PeTTa runtime check

Install SWI-Prolog 9.3 or newer and ensure `swipl` is on `PATH`. On Ubuntu,
the [SWI-Prolog stable PPA](https://www.swi-prolog.org/build/PPA.html) provides
the runtime used by CI:

```sh
sudo apt-get update
sudo apt-get install -y software-properties-common
sudo add-apt-repository -y ppa:swi-prolog/stable
sudo apt-get update
sudo apt-get install -y swi-prolog-nox python3-scipy
```

On Windows, install SWI-Prolog from its
[official download page](https://www.swi-prolog.org/download/stable) and check
`swipl --version` in PowerShell. Clone PeTTa from
`https://github.com/trueagi-io/PeTTa.git` and check out the revision above; do
not run this check against its moving default branch. From the hybrid-miner
checkout, initialize the pinned Hyperon Miner submodule and install this
package with development dependencies and SciPy:

```sh
git submodule update --init --recursive
git clone https://github.com/trueagi-io/PeTTa.git ../PeTTa
git -C ../PeTTa checkout ebdb51168135f7fdf68c59acf7521a1b85c19ba0
python -m pip install -e ".[dev]" scipy
swipl --version
```

On a POSIX shell, run the real-runtime integration check with explicit
checkout paths:

```sh
PETTA_ROOT="$(cd ../PeTTa && pwd)" MINER_ROOT="$(pwd)/hyperon-miner" \
  python -m pytest tests/integration/test_petta_runtime.py -q
```

On PowerShell, set the same paths before invoking pytest:

```powershell
$env:PETTA_ROOT = (Resolve-Path ..\PeTTa).Path
$env:MINER_ROOT = (Resolve-Path .\hyperon-miner).Path
python -m pytest tests/integration/test_petta_runtime.py -q
```

The expected result is one passing integration test. It runs Python ingestion
and emission, executes the packaged admission rule in PeTTa, passes only the
admitted graph to Hyperon Miner, recovers exact evidence bindings, and checks
that missing metadata produces no support output. The test creates its
intermediate files in pytest's temporary directory; it does not need an LLM,
GPU, or production corpus. The commands below show how to run each stage on
your own parser artifacts and inspect its outputs.

## Parser-document ingestion and PeTTa emission

The caller supplies a JSON ontology bundle and parser `SemanticDocument` JSONL.
From this repository's root:

```sh
python -m hybrid_miner.ingestion --bundle bundle.json --input documents.jsonl --accepted-output accepted.jsonl --rejected-output rejected.jsonl
python -m hybrid_miner.emission --bundle bundle.json --input accepted.jsonl --atoms-output facts.metta --metadata-output facts.metadata.metta --provenance-output facts.provenance.jsonl
python -m hybrid_miner.mining.admitted_support --bundle bundle.json --emitted-atoms facts.metta --metadata facts.metadata.metta --provenance facts.provenance.jsonl --petta-root /path/to/PeTTa --miner-root ./hyperon-miner --admitted-output gated-facts.metta --quarantine-output gate-rejected.jsonl --support-output support.jsonl --min-support 2
python -m hybrid_miner.mining.exact_matches --bundle bundle.json --documents accepted.jsonl --atoms gated-facts.metta --provenance facts.provenance.jsonl --support support.jsonl --petta-root /path/to/PeTTa --miner-root ./hyperon-miner --output exact-bindings.jsonl --rejected-output exact-rejected.jsonl
python -m hybrid_miner.mining.build_pattern_records --bundle bundle.json --documents accepted.jsonl --atoms gated-facts.metta --provenance facts.provenance.jsonl --exact exact-bindings.jsonl --output pattern-records.jsonl --rejected-output pattern-records.rejected.jsonl
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
The PeTTa ingress rule checks that each emitted assertion has a corresponding
fact, evidence record, predicate declaration, entity-type and allowed-type
declarations, and source-span metadata. It makes one admission decision per
assertion; Python serializes that decision without repeating the same PeTTa
presence queries. Document schema, ontology-bundle compatibility, and
source-span offsets are validated against the source text before emission.
The ingress rule checks artifact consistency, not independent semantic truth,
and does not infer new relations. Predicate arity and argument-type validation
belong to document validation; ingress checks that the emitted declarations
are present, not whether they independently satisfy the YAML signature.
If any assertion fails, the command exits nonzero, writes an empty graph, and
records the failures. The integrated `admitted_support` command does not call
Hyperon Miner or create support output after quarantine; it passes the gate's
returned graph path directly to mining on success. Repair the source artifact
and rerun rather than mining an inconsistent subset. The separate
`python -m hybrid_miner.mining --atoms graph.metta ...` command remains available
for independent raw-graph mining; it does not attest PeTTa admission.
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
constructing a validated extraction-stage `PatternRecord`. It stores the
semantic relation
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
    validate_extraction_pattern_record,
)
from hybrid_miner.ontology import OntologyBundle

bundle = OntologyBundle.load_json("ontology-bundle.json")
document = SemanticDocument.model_validate_json(document_json)
bundle.validate_document(document)
pattern = validate_extraction_pattern_record(
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

A parser-derived graph has produced source-linked candidate records with
the pinned runtime. This does not establish general mining quality,
cross-repository promotion, or hardware performance.
