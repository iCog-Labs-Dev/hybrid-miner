# Hybrid Miner

Hybrid Miner is an independent package for graph-pattern mining with an
evidence-audited semantic-document ingestion path. It executes a pinned
`hyperon-miner` baseline and can exchange validated artifacts with
Neuro-Symbolic-LLM and other producers. It does not
run the frozen model, semantic parser, Tier 2 template store, or retrieval
service.

## Initial release scope

The initial release candidate covers standalone graph support mining and an
extraction-stage, depth-zero pipeline with versioned parser-document
interchange. Its capabilities are:

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
  PeTTa space. A rejection quarantines its complete source document.
- A depth-zero adapter that runs the pinned Hyperon Miner on a supplied fact
  graph and records alpha-normalized raw support with runtime revisions.
- An admitted-support entry point that runs PeTTa ingress and passes only its
  accepted documents' graph to the miner, with a versioned admission selection.
- Exact PeTTa binding recovery linked to validated source assertions, followed
  by construction of typed, source-linked candidate `PatternRecord` artifacts.
- Unit tests, a pinned-runtime integration check, formatting/lint/type-check
  configuration, CI, and a pinned upstream Git submodule.

Later phases cover additional source adapters, PeTTa-side
source-text offset validation and full canonicalization, candidate scoring,
Tier 2 promotion, non-myopic search, SVM/STLM control, and causal experiments.
Raw support is not a promoted template or a measure of neural utility.
The document contract also preserves source-extracted universal
rules with typed, rule-local variables and signed antecedent/consequent
clauses. Validation checks source-span bounds, variable scope, reference types,
and the supplied ontology signatures. It does not execute these rules or assert
their consequents. The emitter preserves these rules as declarative graph data,
not executable MeTTa rewrites or unconditional conclusions. Rule graphs pass
through the same admission, source-linkage and candidate-record workflow.
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
|   |   |-- admission.py
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
Hyperon Miner against raw graph and two-document fixtures: it requires
admission and exact source-linked bindings, then verifies that removing
metadata quarantines affected documents; if no facts survive, mining cannot start.
This check does not
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
  python -m pytest tests/integration -q
```

On PowerShell, set the same paths before invoking pytest:

```powershell
$env:PETTA_ROOT = (Resolve-Path ..\PeTTa).Path
$env:MINER_ROOT = (Resolve-Path .\hyperon-miner).Path
python -m pytest tests/integration -q
```

The integration suite covers fact-only, provisional and rule-bearing inputs,
as well as the standalone graph interface. The document checks run
Python ingestion
and emission, executes the packaged admission rule in PeTTa, passes only the
admitted graph to Hyperon Miner, recovers exact evidence bindings, and checks
that missing metadata produces no support output. The test creates its
intermediate files in pytest's temporary directory; it does not need an LLM,
GPU, or production corpus. The commands below show how to run each stage on
your own parser artifacts and inspect its outputs.

## Input interfaces

| Interface | Required input | Output and guarantees |
| --- | --- | --- |
| Standalone graph mining (python -m hybrid_miner.mining) | Caller-prepared MeTTa facts and runtime checkout paths | Raw pattern binding counts and runtime identity; no extraction or ontology-admission claim |
| Audited semantic mining (python -m hybrid_miner.mining.admitted_support) | Source documents, emitted facts, metadata, provenance, a supplied bundle and runtimes | Complete-document admission, selection manifest and support over accepted facts |
| Source-evidence audit and record construction | Semantic documents, the same bundle, admitted facts, provenance, and matching support artifacts | Source-linked exact bindings and extraction-stage candidate records |

`SemanticDocument` is the contract for character-span-grounded S1 text
extraction, not the universal input type of the graph miner. Its model/prompt
provenance, extraction status, and source spans are required only by that
workflow. `OntologyBundle` validates caller-supplied definitions; the miner
does not require any particular YAML catalog collection. Other graph producers
can use the standalone interface without fabricating text, model identities,
ontology versions, or character spans. They do not automatically gain the
text-evidence auditing and extraction-stage record-building capabilities.

### Standalone graph mining

For example, save these database-derived facts as `purchases.metta`:

~~~metta
(Purchased buyer1 item1)
(Product item1)
(Purchased buyer2 item2)
(Product item2)
~~~

Run the existing graph interface with the pinned runtime paths:

~~~sh
python -m hybrid_miner.mining --atoms purchases.metta --petta-root ../PeTTa --miner-root ./hyperon-miner --output purchases.support.jsonl --min-support 2
~~~

Inspect `purchases.support.jsonl` for the pattern
`(, (Product $v0) (Purchased $v1 $v0))` with support 2. No ontology bundle,
parser metadata, extraction status, or source spans are supplied. Other raw
patterns may also be returned: binding counts alone do not establish distinct
evidence, semantic correctness, or template utility. Use new output paths;
existing artifacts are not overwritten.

The runtime imports MeTTa code, not a sandboxed data format. Run only trusted
graph files; the raw interface does not validate arbitrary programs as safe.

### Search-depth baseline

The adapter fixes the pinned upstream conjunction depth to zero with conjunction
expansion enabled. Upstream's base case still expands each atomic candidate
once, producing two-clause conjunctions. A greater depth recursively adds
expansion steps; it is not a reasoning level and does not enable S2 or S3.
This release does not expose a higher-depth search setting or claim discovery
of longer conjunctions. Deeper search, search budgets, and guided/non-myopic
generation require separate runtime verification and evaluation before they
are added to the supported interface.

## Parser-document ingestion and PeTTa emission

The caller supplies a JSON ontology bundle and parser `SemanticDocument` JSONL.
From this repository's root:

```sh
python -m hybrid_miner.ingestion --bundle bundle.json --input documents.jsonl --accepted-output accepted.jsonl --rejected-output rejected.jsonl
python -m hybrid_miner.emission --bundle bundle.json --input accepted.jsonl --atoms-output facts.metta --metadata-output facts.metadata.metta --provenance-output facts.provenance.jsonl
python -m hybrid_miner.mining.admitted_support --bundle bundle.json --documents accepted.jsonl --emitted-atoms facts.metta --metadata facts.metadata.metta --provenance facts.provenance.jsonl --petta-root /path/to/PeTTa --miner-root ./hyperon-miner --admitted-output gated-facts.metta --admission-output admission.json --quarantine-output gate-rejected.jsonl --support-output support.jsonl --min-support 2
python -m hybrid_miner.mining.exact_matches --bundle bundle.json --documents accepted.jsonl --atoms gated-facts.metta --provenance facts.provenance.jsonl --admission admission.json --support support.jsonl --petta-root /path/to/PeTTa --miner-root ./hyperon-miner --min-support 2 --output exact-bindings.jsonl --rejected-output exact-rejected.jsonl
python -m hybrid_miner.mining.build_pattern_records --bundle bundle.json --documents accepted.jsonl --atoms gated-facts.metta --provenance facts.provenance.jsonl --admission admission.json --exact exact-bindings.jsonl --output pattern-records.jsonl --rejected-output pattern-records.rejected.jsonl
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
Admission receives the original exported documents through `--documents` and
reuses the shared document/bundle validator to check references, source bounds
and ontology compatibility. It compares emitted provenance to those original
source records without renumbering assertions or rewriting their evidence.
PeTTa then checks facts and required evidence in separate spaces. Arity,
allowed argument types and known types are checked against a separate
`&schema` compiled from the caller's bundle, not caller-supplied metadata.
Forging matching `AllowedType` evidence cannot change the bundle's definitions.
The policy is generic: no project-specific predicate names or fixed catalog
modules are built into these checks.

Required evidence includes parser/ontology provenance and source spans, plus
factuality, conditions and alternatives when present in the source record.
Missing required fields, conflicting values and undeclared semantic evidence
quarantine the affected document. Optional source fields remain optional.
The checks establish contract and artifact consistency, not semantic truth,
and never infer new relations.

Admission is atomic per `(corpus_id, document_id)`, not per input file. If one
assertion or rule part fails, all graph entries from that document are excluded;
other intact documents can still be mined. No shortened source rule is released.
Malformed or unassignable input artifacts, incompatible global inputs and
runtime failures stop the run. The CLI exits zero when admitted facts are
available, even if some documents were quarantined; inspect the document counts
and rejection file. If no facts survive, integrated mining exits with code 2
and creates no support output. Fatal input/execution errors exit with code 1.

The `document-admission-v1` JSON handoff records every document's decision and
reasons and binds the selection to the bundle, original documents, original
provenance and admitted graph by SHA-256. It is an artifact-consistency record,
not authentication or a proof that the extraction is true. Supply its path as
`--admission` to exact matching and record construction. Both stages verify the
hashes and source-document coverage before excluding quarantined documents.
Without that option, their complete-export checks remain unchanged; a partial
graph cannot silently pass. Original document identities and assertion/rule
indices are preserved. If `--admission-output` is omitted, its path is derived
from the admitted graph by replacing its extension with `.admission.json`.

The separate
`python -m hybrid_miner.mining --atoms graph.metta ...` command remains available
for independent raw-graph mining; it does not attest PeTTa admission.
The mining adapter executes the upstream depth-zero miner and serializes observed
support with stable variable names, conjunction order, graph digest, and runtime
revisions. The exact-match audit then reruns each supported pattern in PeTTa,
checks its reported count, grounds every binding against emitted facts, and
joins each fact to validated document assertions and source spans. A mismatch
between the graph, documents, bundle, support revision, and provenance aborts
the run without output. The audit records raw `reported_support` separately from
`qualifying_support` under the `distinct-ground-facts-v1` selection policy.
Bindings that reuse a ground fact across clauses are listed in `excluded_bindings`;
other valid bindings of the same pattern are retained. Set `--min-support` on the
exact-match command to require a minimum qualifying count (default: 2, matching
the mining default). When overriding the mining threshold, explicitly pass the
same threshold to exact matching; it is not automatically propagated. Patterns below
that threshold go to the rejected output. Other evidence failures, including a
binding that crosses source documents, still reject the candidate. Support counts
variable bindings, not distinct documents. Candidate `observed_support` uses the
qualifying count, with the raw count and policy retained in its expensive trace.
Several source assertions may support the same grounded fact. Exact evidence
keeps each occurrence linked by its `atom`; record construction associates all
of them with the corresponding grounded clause and preserves their spans and
confidences. Additional evidence occurrences do not increase binding support.
Pattern IDs hash the compact UTF-8 JSON array
`["pattern-id-v1", bundle_id, bundle_version, qualified_pattern]`, so delimiters
inside identifiers cannot make different identity tuples collide. This replaces
the unreleased delimiter-concatenated IDs; regenerate earlier candidate artifacts
before comparing IDs. It does not change the semantic display or mining support.
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

Source-linked exact matching and candidate construction accept catalog-defined
predicate heads and the explicit, bundle-qualified `ProvisionalRelation`
wrapper. An unlisted bare head or a variable predicate head remains rejected.
The provisional wrapper's first operand is a relation identifier, not an
entity. It may be fixed (`rel:repair`) or a pattern variable; variable relation
slots receive the record-level sort `ProvisionalRelationId`, distinct from
ontology entity types. This does not add a type or predicate to the caller's
ontology. Entity-argument types still come from validated source assertions.

Candidate semantic terms retain `ProvisionalRelation` and its relation operand;
they do not rename the wrapper to an approved predicate. Versioned wrapper
identity remains in `ontology_groundings` and the qualified-pattern trace.
For each provisional clause, `expensive_trace.provisional_relations` records
the observed relation IDs and argument signatures from the exact source
bindings. A variable relation slot can match different IDs: such a template
is generic, not a claim that those relations are synonymous or newly approved.
These observed IDs are exact-stage evidence, not cheap controller features.
The existing `PatternRecord` contract can represent these records without a
new field or contract version. Conflicting variable types, forged evidence,
wrong bundle identity and insufficient distinct-fact support remain explicit
rejection conditions.

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
`PatternRecord` requires `pattern-record-v1`; unversioned and unsupported
records are rejected. Downstream consumers must install a compatible released
package rather than maintain an independent copy of the contract. The builder
populates `cheap_features` only from candidate structure available before exact
evaluation and rejects exact-support keys in that feature map. Exact observed
support is stored in `expensive_endpoint`, with a compact exact-evaluation
summary and runtime provenance in `expensive_trace`. Neither is a valid input
feature when training a controller to predict the exact endpoint.

### Declarative rule graphs

`semantic-rule-v1` is a graph-encoding version, not another ontology catalog.
For each source rule, emission creates an `ExtractedRule` declaration and
`RuleAntecedentPositive`/`RuleAntecedentNegative` and
`RuleConsequentPositive`/`RuleConsequentNegative` expressions. Their heads carry
the encoding version. Every literal includes the source rule's document-scoped
identity and a known or provisional predicate-identity operand. Rule-local
variables become scoped ground symbols, alpha-normalized by first use; they
cannot bind as an unconditional source entity or escape to another rule.

Known predicate operands use `pred:` plus a qualified catalog identity;
provisional operands use `prov:` plus bundle and relation identity. In candidate
semantic views, fixed predicate operands omit version decoration, while the
qualified pattern and source context retain it. Pattern variables in those
positions have the record-level sorts `RuleId`, `RulePredicateId`,
`RuleQuantifier` and `RuleVariable[<type>]`. These sorts do not add vocabulary
to the supplied ontology. Entity constants retain their document identities.

PeTTa admission requires the complete rule's graph and metadata for every
encoded fragment. Missing premises, conclusions or required metadata prevent
release of a partial source document. Exact recovery compares rule provenance to
the original validated documents. Candidate `expensive_trace.rule_contexts`
retains the complete source rules; `rule_fragment_semantics` explicitly marks
a mined subgraph as structural, not a newly inferred executable implication.
Omitting a premise from a mined fragment therefore does not authorize a shorter
rule. Rules remain extraction hypotheses: this path performs no deduction,
rule firing, ontology closure or predicate invention. Its supported logical
subset is the document contract's universally quantified conjunctive rules
with typed local variables and signed literals, not arbitrary first-order logic.

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
the same versioned ontology bundle for the audited document workflow. The
producer owns its ontology definitions and extraction policy; this package
owns the accepted document/bundle formats and mining interfaces. Raw graph
mining does not load the parser or require those artifacts.

## Implementation requirements

- For semantic-document ingestion, validate ontology versions, predicate
  signatures, argument types, and source spans before graph ingestion.
  Preserve rejected input for audit.
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
