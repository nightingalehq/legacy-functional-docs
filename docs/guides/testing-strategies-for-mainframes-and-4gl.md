# Testing strategies for mainframes and 4GLs

Audience: anyone who has to explain — or sit through an explanation of —
why this tool can now draft *tests*, not just documentation, and what that
buys a team whose testing culture, historically, has been "an SME runs the
month-end batch in a UAT region and eyeballs the output." This guide has
two jobs: introduce the modern testing vocabulary a migration team will use,
and map each generated artifact back to a concept in that vocabulary so a
mainframe engineer and a modern-language engineer are pointing at the same
thing when they say "test."

For the mechanics of the commands themselves, see `SKILL.md`'s "Optional:
draft tests from the same facts" section and
[architecture.md](architecture.md)'s "Test generation" stage. This guide is
about *why*, and how to talk about it with a team.

## The gap this closes

A Natural or Mantis codebase that has run in production for twenty years
has, in a real sense, been tested continuously — by every transaction it
has ever processed without anyone complaining. What it almost never has is
an *automated, repeatable, inspectable* record of what "correct" means for
any one program in isolation. Testing knowledge lives in three places
instead: the heads of a few long-tenured staff, a UAT runbook that says
"compare today's output to last month's," and the source code itself,
which is the only artifact guaranteed to still be accurate.

That third one is exactly what this tool already reads to produce
documentation. Test generation is the same fact store, pointed at a
different, more precise question: not just "what does this program do,"
but "what specific input/branch/output combinations would prove it,"
expressed in a form (`pytest`, `JUnit`, or the source dialect itself) that
a migration team already knows how to run, read, and extend.

## Vocabulary, mapped to what you already have

Modern testing terms, defined plainly, with the mainframe-side equivalent
or gap each one names:

**Unit test** — exercises one function/subprogram in isolation, with
everything it depends on (a database read, another program call) replaced
by a stand-in so the test is fast, deterministic, and only fails when *that
unit* is wrong. The mainframe-side gap: a Natural subprogram usually
*isn't* isolated — it's wired straight to `FIND`/`READ`/`CALLNAT` against
real Adabas files and other programs, so there has never been a cheap way
to run "just this part." `mfdoc test-advisory`'s job is to say, per
program, whether that isolation is already possible (no external calls),
possible with named stand-ins (`needs-mock`, and it names exactly which
entity/callee), or not safely possible at all yet (`untestable-gap` — a
dynamic or missing call target).

**Test double / mock / stub / seam** — a "test double" is any stand-in for
a real dependency; "mock" and "stub" are specific flavours (a mock records
and can assert *how* it was called, a stub just returns canned data). A
"seam" (the term comes from Michael Feathers' *Working Effectively with
Legacy Code*, the standard reference for exactly this situation) is a
point in the code where you *could* insert a test double without changing
behaviour — often it doesn't exist yet and has to be introduced deliberately.
`mfdoc test-advisory`'s seam suggestions are proposals for where to
introduce one (e.g. "extract the `MILL-ORDER` read behind a lookup this
unit takes as a parameter") — advisory prose only; nothing in this tool
edits source to apply one.

**Integration test** — exercises more than one unit together, deliberately
*not* isolated, because the thing worth proving is that they cooperate
correctly (e.g. a whole unit of work between `FIND` and `END TRANSACTION`
touching two Adabas files). `mfdoc test-advisory` routes a member here
(`integration-only`) when its `transaction_scopes()` commit spans more than
one entity — mocking two entities as if they were independent would prove
nothing real.

**Characterization test** — a test that asserts what code *currently does*,
not what it's supposed to do, written specifically to give you a safety net
before you change or migrate that code. This is the single most relevant
concept for a legacy migration, and it's the default status
`mfdoc test-plan` gives every scenario (`status: characterization`): assert
the cited source excerpt's actual behaviour, bugs included, so a rewrite can
be checked against it before anyone decides whether a given quirk is a bug
worth fixing or a business rule worth keeping.

**Spec test** — a test that asserts *intended* behaviour, per a documented
source of truth (here, the module doc `mfdoc batch`/the interactive path
already produced). Where intent and current behaviour agree, one test would
do; where they might not, `test-overlay.yml` is how that's flagged so
`test-gen`/`test-batch` render both, distinctly named, so the disagreement
is visible in the test suite itself rather than only in someone's memory.

**Regression test** — any test kept around specifically to catch a
previously-working behaviour breaking again. Every characterization test
here is, by construction, a regression test for a migration: its whole
purpose is to fail the moment a rewrite drifts from what production has
actually been doing.

**Golden master (a.k.a. approval testing)** — a close cousin of
characterization testing: capture a real system's actual output for real
inputs, and compare against it later instead of writing individual
assertions by hand. Useful context for why "assert exactly what the cited
excerpt does, don't guess a nicer answer" (`reference/test-writing-rules.md`)
is the right instruction for the render stage — the goal, at
`characterization` status, is fidelity to what's there, not correctness by
some other standard.

**Expected failure (`xfail` / `@Disabled`)** — a test a suite runs but
doesn't require to pass, because the behaviour it checks is known-broken
and tracked, not because nobody's watching it. A `bug-desired` scenario
(from a human-*promoted* `test-overlay.yml` entry — see below) renders as
exactly this: a test of the *intended* fix, deliberately marked expected-to-
fail until the underlying defect is actually remediated. This turns "we
know MMP0100 has always had this edge case" from a verbal aside into a
tracked, dated, citeable line in a test file.

**Given/When/Then** — a common shape for describing one test scenario in
plain language: the starting state (Given), the action taken (When), the
expected outcome (Then). `mfdoc test-plan`'s `test_case` rows are stored in
exactly this shape (`given_json`/`when_json`/`then_json`) because it's the
natural translation of "parameters and mocks in place, this branch's
condition is true, here's what the cited source then does."

**Test pyramid** — the usual guidance that a healthy suite has many fast
unit tests, fewer integration tests, and very few slow end-to-end tests.
Relevant here mainly for what this tool *doesn't* attempt: it derives
unit- and integration-level scenarios from source facts, but has no way to
drive a 3270 screen or a real batch scheduler, so end-to-end coverage is
out of scope — a human-designed integration/UAT layer still has a job to
do above whatever this generates.

## What each command operationalizes

| Command | Concept it operationalizes |
|---|---|
| `mfdoc test-plan` | Turns cited facts into Given/When/Then scenarios — the raw material every test type below is rendered from. Model-free: nothing here is a judgement call. |
| `mfdoc test-advisory` | Names seams and picks unit vs. integration scope — the "can/should this be a unit test" judgement, made from facts (CRUD, call graph, transaction scope), not guessed. |
| `mfdoc test-overlay-draft` | The *only* place a model may propose a characterization/spec/bug distinction — and it can only propose; a human moving `review_status` past `draft` is what makes it real, the same promotion ladder `validate.py` already enforces on narrative docs (`draft` → `in_review` → `sme_approved` → `signed_off`). |
| `mfdoc test-gen` / `mfdoc test-batch` | Renders scenarios into a real test file in the target language/framework, one test per scenario, still cited back to source — a characterization test, a spec test, or an `xfail`-marked bug-desired test, depending on that scenario's (human-confirmed) status. |
| `mfdoc test-validate` | Checks the render didn't drift: every citation resolves, every `MEMBER:BR-nnn` reference names a real derived scenario, front matter is complete — the same trust mechanism `mfdoc validate` gives narrative docs, applied to code instead of prose. |

## Why this is worth explaining to the team, not just running

The tests this produces are a first draft, exactly like the documentation
is — reviewed, corrected, and promoted by humans, never treated as ground
truth on arrival. The organisational value isn't the test files themselves;
it's what having them, and being able to talk about them in this vocabulary,
changes:

- **A shared artifact instead of tribal memory.** "MMP0100 has always
  allowed partial release within a 2.5% tolerance" stops being something
  only J. Price remembers and becomes `MMP0100:BR-004`/`BR-011` — the same
  cited, versioned handle the module doc uses, now backed by a test a
  modern-language engineer can run and a mainframe engineer can trace back
  to the exact `DECIDE FOR` block.
- **An objective bar for "did the rewrite actually work."** Migration
  reviews often stall on subjective disagreement about whether new-language
  output "looks right." A characterization test converts that into
  pass/fail against the legacy system's actual recorded behaviour —
  disagreements move to *whether a specific difference is a bug worth
  fixing* (promote it via the overlay), not whether the rewrite is
  trustworthy in general.
- **A place to put "yes, we know" without losing track of it.** Every
  long-lived system accumulates known quirks nobody has prioritised fixing.
  `bug-current`/`bug-desired` test pairs make that explicit and dated
  instead of institutional folklore that evaporates when the one person who
  remembers it retires — which is the exact same failure mode this whole
  project exists to fix for documentation.
- **A common vocabulary across two engineering cultures.** A COBOL/Natural
  team and a Python/Java migration team often don't test the same way or
  use the same words for it. Given/When/Then scenarios, named seams, and an
  explicit characterization-vs-spec split give both sides the same handle
  on the same behaviour, which is most of what "alignment" means in
  practice on a migration project.

## Rolling this out without overclaiming trust

1. Start with `test-plan` + `test-advisory` alone (both model-free) and
   review the `test-plan-register.md`/`testability-report.md` output the
   same way you'd review a first coverage report — as a map of what's
   knowable from source, not a finished test suite.
2. Only run `test-overlay-draft` once module docs exist and have had at
   least one round of SME review — a divergence proposal is only as good
   as the "intended behaviour" text it's compared against. Treat every
   `draft` entry as a question to a domain expert, exactly like a
   gap-register item; do not promote an entry you haven't actually
   verified.
3. Render (`test-gen`/`test-batch`) after that, and run `test-validate`
   every time, the same way `mfdoc validate` gates narrative docs — a
   generated test with an invalid citation or an invented scenario id is
   worse than no test, for the same reason a confidently wrong sentence in
   a doc is worse than an admitted gap.
4. Treat the rendered files as a first draft a human runs, reads, and
   corrects in the target repo's own toolchain — this tool deliberately
   does not execute them itself (see `docs/guides/architecture.md`'s note
   that stages 0–2, 4, and the derive parts of stage 5 are the only ones
   without a model in the loop; rendering still needs a human to actually
   run the result).
