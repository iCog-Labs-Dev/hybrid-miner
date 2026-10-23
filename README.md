# Hybrid Miner

Hybrid Miner is an independent package for graph-pattern mining with an
evidence-preserving semantic-document ingestion pipeline. Built on Hyperon
Miner and the PeTTa runtime, it supports standalone use and provides versioned
contracts and source-linked mining artifacts for integration with the [Neuro-Symbolic-LLM](https://github.com/iCog-Labs-Dev/Neuro-Symbolic-LLM) project.

## Interfaces

| Interface | Input | Output |
| --- | --- | --- |
| Standalone graph mining | Trusted MeTTa facts | Raw pattern binding counts and runtime provenance |
| Source-grounded document mining | Semantic documents and a caller-supplied ontology bundle | Admission decisions, exact source-linked bindings, and candidate pattern records |

The graph interface requires no parser, ontology bundle, language model, or text
spans. The document interface accepts existing extraction records; it does not
perform the extraction or require a particular set of ontology catalogs.

## Repository structure

```text
hybrid-miner/
|-- src/hybrid_miner/
|   |-- contracts/       # Versioned document and candidate-record models
|   |-- ontology/        # Caller-supplied types and predicate signatures
|   |-- ingestion/       # JSONL validation, PeTTa admission, and selections
|   |-- emission/        # Fact/rule encoding, identities, and provenance
|   `-- mining/          # Runtime adapter, exact evidence, and candidate records
|-- tests/
|   |-- fixtures/        # Example inputs
|   |-- unit/            # Contract and deterministic behavior checks
|   `-- integration/     # Real-runtime graph and document workflows
|-- hyperon-miner/       # Pinned upstream Git submodule
|-- .github/workflows/ci.yml
|-- .pre-commit-config.yaml
|-- pyproject.toml
`-- README.md
```

Document ingestion and evidence handling are separate from the standalone
graph adapter. The submodule supplies the upstream miner; this package adds
the Python interfaces, evidence handling, and candidate-record construction.

## Requirements and setup

- Python 3.11+; `pydantic>=2.6,<3` is installed with the package.
- Mining and PeTTa admission additionally require SWI-Prolog 9.3+, Python/Janus
  interoperability, SciPy, and the supported runtime checkouts below.
- Contract validation, JSONL ingestion, and emission do not require PeTTa.

| Runtime | Supported revision |
| --- | --- |
| PeTTa | `ebdb51168135f7fdf68c59acf7521a1b85c19ba0` |
| Hyperon Miner submodule | `e955479bca1603597529d175cc22ac1583c67e15` |

Install SWI-Prolog using its [platform instructions](https://www.swi-prolog.org/Download.html)
and ensure `swipl` is on `PATH`. On Ubuntu, CI uses the
[SWI-Prolog stable PPA](https://www.swi-prolog.org/build/PPA.html).
Prepare the source and runtime checkouts:

```sh
git clone --recurse-submodules https://github.com/iCog-Labs-Dev/hybrid-miner.git
cd hybrid-miner
python -m pip install -e . scipy
git submodule update --init --recursive
git clone https://github.com/trueagi-io/PeTTa.git ../PeTTa
git -C ../PeTTa checkout ebdb51168135f7fdf68c59acf7521a1b85c19ba0
swipl --version
git -C ../PeTTa rev-parse HEAD
git -C ./hyperon-miner rev-parse HEAD
```

If PeTTa already exists, use that checkout instead of cloning over it. Compare
checkout revisions with the table and adjust CLI paths if they are elsewhere.
SciPy must also be available to the Python environment used by Janus.
Installing the Python package alone does not install these external runtimes.
The listed revisions identify the exercised baseline; compatibility with moving
upstream branches is not implied. Mining records the revisions actually used.

## Quickstart: standalone graph mining

Save these facts as `purchases.metta`:

```metta
(Purchased buyer1 item1)
(Product item1)
(Purchased buyer2 item2)
(Product item2)
```

With the runtime paths from setup:

```sh
python -m hybrid_miner.mining --atoms purchases.metta --petta-root ../PeTTa --miner-root ./hyperon-miner --output purchases.support.jsonl --min-support 2
```

The output should include `(, (Product $v0) (Purchased $v1 $v0))` with support
`2`; other patterns may appear. Each JSONL row contains `pattern`, `support`,
`graph_sha256`, `petta_revision`, and `miner_revision`.

Use fresh output paths for every workflow; existing artifacts are not
overwritten. MeTTa imports can execute code, so run only trusted graph files.
Raw support counts matching variable bindings, not distinct documents or
independently verified evidence.

## Source-grounded document workflow

Supply `documents.jsonl` containing `SemanticDocument` records and
`bundle.json` containing your ontology definitions. The following commands use
the same runtime paths as the quickstart and work as single-line invocations in
POSIX shells or PowerShell.

A [sample bundle](tests/fixtures/example-ontology-bundle.json) illustrates the
ontology JSON format; use definitions compatible with your own documents.

1. Validate records, preserving rejected source lines separately.

```sh
python -m hybrid_miner.ingestion --bundle bundle.json --input documents.jsonl --accepted-output accepted.jsonl --rejected-output rejected.jsonl
```

2. Emit facts, queryable metadata, and JSONL provenance.

```sh
python -m hybrid_miner.emission --bundle bundle.json --input accepted.jsonl --atoms-output facts.metta --metadata-output facts.metadata.metta --provenance-output facts.provenance.jsonl
```

3. Check emitted artifacts in PeTTa and mine only admitted documents.

```sh
python -m hybrid_miner.mining.admitted_support --bundle bundle.json --documents accepted.jsonl --emitted-atoms facts.metta --metadata facts.metadata.metta --provenance facts.provenance.jsonl --petta-root ../PeTTa --miner-root ./hyperon-miner --admitted-output gated-facts.metta --admission-output admission.json --quarantine-output gate-rejected.jsonl --support-output support.jsonl --min-support 2
```

4. Recover exact bindings and link them to original source evidence.

```sh
python -m hybrid_miner.mining.exact_matches --bundle bundle.json --documents accepted.jsonl --atoms gated-facts.metta --provenance facts.provenance.jsonl --admission admission.json --support support.jsonl --petta-root ../PeTTa --miner-root ./hyperon-miner --min-support 2 --output exact-bindings.jsonl --rejected-output exact-rejected.jsonl
```

5. Construct typed candidate records from verified bindings.

```sh
python -m hybrid_miner.mining.build_pattern_records --bundle bundle.json --documents accepted.jsonl --atoms gated-facts.metta --provenance facts.provenance.jsonl --admission admission.json --exact exact-bindings.jsonl --output pattern-records.jsonl --rejected-output pattern-records.rejected.jsonl
```

Inspect counts, rejection files, facts, bindings, source slices, and candidate
records. Validation checks structure, source bounds, references, ontology
versions, and signatures; it does not establish that the extraction is true.

### Admission policy

Each stage validates the input it independently accepts. Ingestion checks JSONL
documents; emission revalidates documents before encoding; admission compares
emitted provenance to the originals and checks corresponding graph evidence.
PeTTa checks facts and required metadata in separate spaces against a schema
compiled from the supplied bundle. Metadata cannot redefine allowed signatures.
Required evidence includes source spans and parser/ontology provenance, plus
factuality, conditions, and alternatives when present in the source record.

A failing assertion or rule fragment quarantines its complete
`(corpus_id, document_id)`, not the whole input file. Other intact documents can
proceed. Fatal global inconsistencies and runtime errors stop the run; partial
source rules are not released.

`admission.json` records the selection and fingerprints its input artifacts.
It is a consistency record, not authentication. Pass it to both downstream
commands so they can recognize excluded documents without rewriting original
documents or provenance. It binds the selection to the bundle, original
documents, provenance, and admitted graph by SHA-256. Without `--admission`,
downstream complete-export checks still apply; a partial graph cannot silently
pass them. Original document identities and assertion/rule indices are retained.

Integrated admission/mining exits 0 when facts survive and mining completes,
even if some documents were quarantined; 2 when none survive, without support
output; and 1 for fatal input/execution errors.

### Support and exact evidence

Exact recovery reruns supported patterns in PeTTa, checks their reported counts,
grounds each binding against the admitted graph, and joins it to original source
assertions or rule fragments. Candidate construction then verifies those bindings
before creating records. A graph, bundle, runtime-revision, or provenance
mismatch aborts the exact run without output.

Exact checking excludes bindings that reuse one ground fact across clauses,
while retaining other qualifying bindings of the same pattern. Outputs record
both `reported_support` and `qualifying_support`; candidate observed support
uses the latter. Additional evidence occurrences for a fact do not increase
binding support. Bindings crossing source documents remain rejected.

Exact bindings are ordered by corpus, document, and variable assignment.
Candidate construction keeps each binding's evidence and confidence values
together, producing deterministic records when input artifacts are unchanged.

The default minimum qualifying support is 2. If you override the mining
threshold, set your intended threshold explicitly on exact matching too.
Exact queries default to a 120-second per-run timeout, 10,000 bindings per
candidate, 64 MiB stdout, and a separate 1 MiB stderr ceiling. The first three
are exposed as `--timeout-seconds`, `--max-bindings`, and
`--max-output-bytes`. Exceeding a budget creates neither exact output;
these limits do not cap the Prolog process's total memory.

## Contract API and candidate semantics

| Contract | Version | Purpose |
| --- | --- | --- |
| `SemanticDocument` | `semantic-document-v1` | Typed entities, assertions/rules, source spans, confidence, and extraction provenance |
| `OntologyBundle` | `ontology-bundle-v1` | Caller-supplied types, predicate signatures, and ontology versions |
| `PatternRecord` | `pattern-record-v1` | Candidate structure, types, evidence, features, and evaluation fields |
| Admission selection | `document-admission-v1` | Per-document decisions and artifact fingerprints |

For existing, nonempty pipeline outputs:

```python
from pathlib import Path

from hybrid_miner.contracts import (
    PatternRecord,
    SemanticDocument,
    validate_extraction_pattern_record,
)
from hybrid_miner.ontology import OntologyBundle

bundle = OntologyBundle.load_json("bundle.json")
document_json = Path("accepted.jsonl").read_text(encoding="utf-8").splitlines()[0]
pattern_json = Path("pattern-records.jsonl").read_text(encoding="utf-8").splitlines()[0]
document = SemanticDocument.model_validate_json(document_json)
bundle.validate_document(document)
pattern = validate_extraction_pattern_record(
    PatternRecord.from_canonical_json(pattern_json)
)
document_artifact = document.canonical_json()
pattern_artifact = pattern.to_canonical_json()
```

This validates and serializes existing artifacts; it does not emit or mine
graphs. Unsupported contract versions are rejected. The document `SourceSpan`
and candidate `PatternSourceSpan` are distinct types with separate exports.

### Identity and provenance

Emission uses readable, reversible escaping to separate document and entity
identities. Facts and queryable evidence occupy separate spaces; JSONL
provenance retains complete source assertion/rule records.

Graph predicates retain version-qualified identity, while candidate
`canonical_term` uses semantic names and preserves versions in
`ontology_groundings` and its qualified-pattern trace. The graph string is not
an embedding input, and stripping version suffixes must not merge incompatible
predicate definitions.

Pattern IDs hash the compact UTF-8 JSON array
`["pattern-id-v1", bundle_id, bundle_version, qualified_pattern]`, avoiding
delimiter ambiguity. Producers own ontology definitions and extraction policy;
consumers exchange serialized artifacts or compatible released packages rather
than importing another repository's working tree.

### Typing, provisional relations, and rules

Candidate type constraints use a string for one type or a sorted array for
alternatives: `{"$v0": ["TypeA", "TypeB"]}` means TypeA **or** TypeB. Catalog
signature constraints are intersected at every position where a variable occurs
within one binding. Permitted concrete-type variation across separate bindings
is retained, with concrete assignments in `expensive_trace.binding_types`.
Catalog alternatives reflect signature permissions, not only observed types.

Provisional relations remain explicit wrappers, not newly approved predicates.
Their relation-ID operand is distinct from entity arguments; variable relation
slots use the record-level sort `ProvisionalRelationId`. Observed relation IDs
and signatures remain in the trace. These record-level sorts do not add types
to the caller's ontology or establish that different relations are synonymous.
Source-linked matching supports catalog heads and explicit provisional wrappers;
unlisted bare heads and variable predicate heads remain unsupported.

Source-extracted universal conjunctive rules are encoded as declarative graph
data with typed, scoped variables and signed literals. `semantic-rule-v1`
versions this encoding, not another ontology catalog. The graph contains an
`ExtractedRule` declaration and signed antecedent/consequent entries. Rule-local
variables cannot escape their source rule or bind as unconditional entities.
Complete rules are retained in `expensive_trace.rule_contexts`, and the trace
marks mined fragments as structural, not executable implications. A fragment
does not authorize dropping premises or asserting conclusions.

### Candidate evaluation boundary

Records carry `status: "candidate"`; all variables are exposed as interface
variables. Existential projection and application-specific acceptance need
separate policies. `cheap_features` contains pre-evaluation structure; qualifying
support belongs in `expensive_endpoint.observed_support`, with raw support and
evaluation provenance in `expensive_trace`. Exact results must not leak into
input features used to predict that endpoint.

## Current limits

The fixed depth-zero baseline expands atomic seeds once, producing two-clause
candidates. Higher-depth or learned search, rule execution, candidate approval,
embedding, and indexing are not provided by this interface. Large-corpus
throughput and downstream utility have not been established.

Implementation references: [document contracts](src/hybrid_miner/contracts/semantic_document.py),
[ontology validation](src/hybrid_miner/ontology/bundle.py),
[PeTTa runner](src/hybrid_miner/mining/petta_runner.py), and
[candidate construction](src/hybrid_miner/mining/build_pattern_records.py).

## Development and checks

```sh
python -m pip install -e ".[dev]"
python -m pytest tests/unit -q
python -m ruff check src tests
python -m black --check src tests
python -m mypy src/hybrid_miner
pre-commit install
```

For real-runtime integration coverage, complete setup and set checkout paths.
On PowerShell:

```powershell
$env:PETTA_ROOT = (Resolve-Path ..\PeTTa).Path
$env:MINER_ROOT = (Resolve-Path .\hyperon-miner).Path
python -m pytest tests/integration -q
```

On a POSIX shell:

```sh
PETTA_ROOT="$(cd ../PeTTa && pwd)" MINER_ROOT="$(pwd)/hyperon-miner" python -m pytest tests/integration -q
```

CI checks unit tests, formatting, lint, types, the submodule pin, and a separate
pinned-runtime integration suite. These checks do not replace inspection of
real input meaning and generated artifacts.
