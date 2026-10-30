# Complementary context selection v1

`retrieval_arena.graph.complementary_context`, `complementary_context.v1`.
Offline immutable operators `generate-complementary-context` and
`evaluate-complementary-context`, using the standard source/image/lock receipts
and sh/PowerShell launchers. No model, network or answer generation is used.

Generation receives complete finite cached query/page cosine scores, questions,
full corpus graphs/text and optionally qualified full heat bundles. It refuses
missing, duplicate or extra score IDs, unknown input files and noncanonical node
orders. Content hashes join raw page bytes in sorted node order; graph hashes
use the normalized operator contract. Existing bundles must match content,
graph, q, t, node order and array hashes. Missing declared bundles are computed
with the same fully qualified symmetric normalized operator, saved and loaded
again. This never authorizes embedding inference.

The fixed semantic seed is reserved in a four-page output. On all nonseed nodes,
semantic rank sorts cosine descending then ID. Structural rank sorts
`abs(H[s,v])/sqrt(real(H[s,s])*real(H[v,v]))` descending then ID, excluding
affinities at or below the declared zero tolerance (1e-10 in the consuming
study). Finite positive real diagonals and finite denominators are mandatory.
Fusion sums `1/(60+rank)` from each available ranking, with no structural term
for zero affinity. Three nonseed pages sort by fused score, cosine, then ID.
The normalized magnitude is invariant under global reversal and does not
represent an outgoing transition probability. No charge, scale, weight, seed
count or budget fitting occurs. Q0, M10 and M25 use identical policy with
q=0, .10, .25 at t=1; M10 is descriptive sensitivity.

B1/B4 retain semantic ordering. G4/G4h2 reuse `phase_navigation.traverse_once`:
outgoing breadth-first traversal, sorted-ID admission, 20 neighbors per
expansion, 40 total candidates including seed, four final pages, cosine minus
.02 times discovered shortest distance; ties by distance then ID. Only
`max_hops` changes from 1 to 2. There is no fallback to global scores. The
semantic seed need not survive the historical traversal rerank. All historical
B1/B4/G4 rows supplied to generation must reproduce exactly before generation
is admitted. Hybrid output always has four distinct pages on admitted corpora.

Generation mounts no targets, answers, judgments or requirements. Prediction
hashes and parity publish before labels are staged for the separate evaluator.
The evaluator allows only frozen predictions/parity, corpus graphs/text,
questions, direct targets, pooled judgments, population masks and source groups
for a blank review packet. It verifies hashes, exact question joins, corpus
membership and positive-judgment/target equality. This is mount-level separation
between stages; it does not blind the analyst to historical outcomes.

Evaluation reports hit, known-positive-set coverage, actual support identities,
shared/exclusive positive pairs, rescue/loss IDs, all paired cells, additional
hits only when the seed missed, additional positive identities even when the
seed hit, actual page/character counts, candidates, unjudged outputs and
selected-page route strata. No known targets is an unavailable endpoint;
all-panel saved-label access separately includes those rows as zero observed
hits. Unjudged outputs are never asserted irrelevant. Four pages is a maximum
page budget, not equal tokens or search effort. Character counts use UTF-8
decoded universal-newline full text, with no new passage extraction.

One grouped bootstrap resamples exact question texts jointly across methods,
surfaces and fixed population masks. Reports include common, all-panel and
arm-eligible views, absolute scores, within-arm differences, cross-arm
differences of differences, and original-label bridge. It is conditional on
already studied corpus/query/label identities. Corpus IDs and five evaluation
surfaces are passed in the study contract; no corpus name, filesystem path or
corpus-specific text enters the selector or evaluator. Primary surface,
common-positive arms and paired surface contrasts are explicit config fields.

Requirement scoring is separate: each evidence unit has alternative sets of
jointly necessary pages. A unit is covered only if one complete alternative
set is selected. A missing independent map returns untested, never inferred
from qrels or answer text. The blank source-group packet hides method names
and contains each method's outputs, full source text and empty uncertainty,
alternative and joint-support fields. It supplies no judgments. Examples are
deterministic illustrations, not a new evaluation cohort.

Fixtures in `unit_tests.test_complementary_context` cover a hand-worked fusion,
seed retention, ties and missing scores, zero/disconnected affinity, finite
normalization, reversal and permutation, matched-control parity, chain/capped
branch traversal, all paired cells and marginal definitions, joint/alternative
evidence scoring, no-target input admission and a small complete operator run.
Only actually exercised platforms may be claimed; the library adds no new
architecture-specific dependencies.
