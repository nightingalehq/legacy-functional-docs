# Security and compliance overview

Audience: whoever has to sign off on using this tool against a client's or
employer's mainframe source before an engagement starts — security review,
procurement, legal, or a client's own due-diligence process. It states what
the tool does with data, what leaves the machine it runs on, and what is
still the user's decision to make, rather than the tool's to enforce.

This is a description of the software as it exists in this repository, not
a compliance certification. Nothing here substitutes for your own
organisation's data-processing agreements, security review, or legal advice
for a specific engagement.

## Summary

| Question | Answer |
|---|---|
| Does this run on Anthropic's or any third party's infrastructure? | No. It runs entirely on whatever machine you invoke it from — your laptop, a client-supplied environment, a CI runner. |
| Does source code leave the machine by default? | No. `mfdoc ingest`, `derive`, `coverage`, `gate`, `calibrate`, `brief`, `validate` and `export` are local-only; no network calls anywhere in that code path. |
| What is the one exception? | `mfdoc batch`, an optional command that sends a **brief** (a derived fact summary, not raw source) to the Anthropic Claude API to draft module documentation unattended. It requires an explicit extra install (`pip install 'mfdoc[batch]'`) and an API key — nothing reaches it by accident. |
| Is there another path data can reach a model by? | Yes — writing documents interactively inside a Claude Code chat session (the `SKILL.md` workflow) also sends brief content to a model, via whatever is running Claude Code. This is the normal, intended way to use the tool for anything other than high-volume module docs; it is not something to avoid, but it does mean "no network access" describes the deterministic pipeline, not the documentation workflow as a whole. |
| Can sensitive literals be scrubbed before anything is sent anywhere? | Yes, via `options.redact` in `project.yml` — see "Redaction" below. It is opt-in and does nothing by default. |
| Is a run reproducible and auditable after the fact? | Yes — see "Audit trail" below. |
| Are there hard dependencies beyond the standard library? | `PyYAML` only, for the core pipeline. `anthropic` is required only for `mfdoc batch` and is declared as an optional extra, not a default dependency. |
| What can we do with this codebase under its license? | It's [MIT-licensed](../../LICENSE) — free to use commercially, modify, and redistribute, with no obligation to open-source your changes. Only condition: keep the copyright/license notice with any copy you redistribute. See "Licensing" below. |

## Data flow, in order

1. **Source ingestion.** Files named in `project.yml` are read from local
   disk (or a mounted path) into `.mfdoc/index.db`, a SQLite file on the
   same machine. Nothing here is transmitted anywhere.
2. **Extraction and derivation.** Pure computation over that same local
   database. No network calls.
3. **Brief generation.** A plain-text summary is generated from the
   database — still entirely local — with redaction applied at this point
   if configured (see below).
4. **Narrative writing — the one place data can leave the machine, and
   only if you choose one of these paths:**
   - Writing the document yourself, or via a Claude Code chat session,
     from the brief. Whatever's in the brief reaches whatever model backs
     that session.
   - `mfdoc batch --provider anthropic` (the default), which sends the brief
     to the Claude API directly over HTTPS, via the official `anthropic`
     Python SDK.
   - `mfdoc batch --provider vertex`, which sends the same brief to the same
     Claude model, but routed through Google Cloud Vertex AI instead — a
     different egress path (your GCP project, not Anthropic's endpoint
     directly), which changes whose infrastructure the brief transits and
     which data-processing terms apply. Needs `pip install 'mfdoc[vertex]'`
     and a GCP project with Vertex's Claude models enabled; credentials come
     from the ambient environment (Application Default Credentials), never
     handled by this tool directly.
5. **Validation.** Reads generated documents and the local database only.
   No network calls.

At no point does raw, un-briefed source code get transmitted as a whole —
what reaches a model is always a **brief**: a derived, structured summary,
not the source file. That said, briefs are not sanitised of business
content by design — they're supposed to contain the facts the narrative
needs to cite. See the next section for what that means in practice.

## What actually reaches a model, and why it's still disclosive

Even a brief, not the raw source, discloses meaningful information: field
names, dataset names, literal values in conditions (status codes, tolerance
percentages, customer-facing text), program and library names, and the
overall shape of the system. Dataset and program naming conventions alone
can reveal infrastructure layout. Decide, per engagement, whether:

- the source material is contractually restricted from leaving the client's
  environment at all, in which case run everything except `mfdoc batch` and
  do the narrative pass with a model deployment the contract permits (a
  private/on-prem deployment, or none at all — writing by hand from the
  brief works too, briefs are designed to be human-readable);
- a hosted model is acceptable for this engagement, and if so, which
  provider and which data-handling terms apply (Anthropic's, if using
  `mfdoc batch --provider anthropic` or Claude Code directly against the
  Claude API; Google Cloud's, if using `mfdoc batch --provider vertex` — the
  model is the same Claude model either way, but the egress path and the
  applicable data-processing agreement are not).

This is a decision to make explicitly, once per engagement, not something
the tool decides for you. It cannot know your contract terms.

## Credentials and hard-coded secrets in source

Legacy 4GL source routinely contains hard-coded passwords, user IDs and
connection strings, most often in literals, comments or `PARM` strings.
**You will find them, because the tool ingests everything verbatim into the
fact store and reflects them back in briefs and citations unless you
redact.** Before ingesting a new codebase:

- Decide the disclosure path in advance — who gets told, and how fast, when
  a live credential turns up. Finding one mid-engagement with no plan is a
  bad time to invent one.
- Consider adding known secret shapes and any already-found values to
  `options.redact.patterns` (see below) so they don't propagate into briefs
  or generated documents in the first place.
- Treat a scanner surfacing these as a **finding worth reporting** to the
  client, not a defect in the tool — this is arguably a differentiator
  against a human reading the same code, who might not think to flag it.

## Redaction

`options.redact` in `project.yml` controls this (`redact.py`):

```yaml
redact:
  enabled: false
  patterns: []
  #   - 'AB123456C'      # an exact value already found in this codebase
  #   - 'Tr0ub4dor&3'
```

- **Disabled by default.** Nothing is redacted unless you turn it on.
- **No built-in default patterns.** The tool does not guess at what counts
  as sensitive in an unfamiliar client's source — that would be exactly the
  kind of invention this project's citation discipline forbids elsewhere.
  You supply exact values or regexes for whatever has already been found
  in *this* codebase, as you find it.
- **Applied at brief-generation time**, not only when a document is
  rendered — so a redacted value never reaches a prompt in the first place,
  regardless of whether you use `mfdoc batch` or write interactively from
  the brief.
- **Does not touch the fact store.** `.mfdoc/index.db` always contains the
  literal, unredacted source — see "The index as a security artefact"
  below for why that matters and what to do about it.
- **Is only as good as the patterns you supply.** It is a targeted control
  for known values, not a data-loss-prevention scanner. Don't rely on it to
  catch a secret you haven't already found by some other means.

## The index as a security artefact

`.mfdoc/index.db` is a full copy of every ingested source line, plus every
extracted fact, in a single SQLite file on local disk. It is:

- **Gitignored by default** (see `.gitignore`) — necessary, and not
  sufficient on its own. It still exists on disk, on whatever machine
  produced it, for as long as nobody deletes it.
- **Rebuildable from source at any time** — it holds no information that
  isn't derivable from the source files again, so there's no cost to
  deleting it once an engagement is done, and no reason to keep it longer
  than the engagement needs.
- **Not encrypted at rest by the tool.** If the machine's disk isn't
  encrypted, or the file is copied somewhere that isn't, that's a gap this
  tool does not close for you.

Decide, per engagement, where this file is allowed to live, who can access
the machine it's on, and when it gets deleted. Treat it with the same care
as the source it was built from — it is functionally a copy of it.

## Audit trail

Every run records, without any extra configuration:

- **`project.yml`** — committed to version control, per the README — records
  exactly which source paths, dialects and options produced a given index.
- **`ingest_run`** (in the database) — tool version and the full config used,
  timestamped.
- **`source_file.sha256`** — a hash of every ingested file, so "was this the
  same source we looked at in March" is answerable exactly, not from memory.
- **Citations** (`[[MEMBER:LINE]]`) in every generated document — traceable
  back to an exact source line, mechanically checked by `mfdoc validate`.

Put together, a documentation run is reproducible and its provenance is
checkable months later — worth stating explicitly to a client, since it's a
genuine, verifiable difference from a consultant reading code by eye and
writing prose in a word processor.

## Dependencies and supply chain

- **Core pipeline:** `PyYAML` is the only runtime dependency (see
  `pyproject.toml`). Everything else used by `ingest`, `derive`, `coverage`,
  `gate`, `calibrate`, `brief`, `validate` and `export` is Python standard
  library. This is deliberate — these commands are meant to run inside
  client environments with restricted or no internet egress, and a smaller
  dependency surface is a smaller thing to have reviewed.
- **`mfdoc batch --provider anthropic` (default) only:** the `anthropic`
  package, declared as the optional `batch` extra
  (`pip install 'mfdoc[batch]'`), never installed by default.
- **`mfdoc batch --provider vertex` only:** the `anthropic` package's Vertex
  extra (which pulls in `google-auth`), declared as the optional `vertex`
  extra (`pip install 'mfdoc[vertex]'`), never installed by default.
- **Dev-only:** `pytest`, declared as the `dev` extra, not installed or
  needed to run the tool itself.

## What this tool does not do (and why that matters here)

- It does not modify, execute, or connect to the source system it
  documents. It reads files handed to it; nothing here reaches back into a
  live mainframe.
- It does not phone home, collect telemetry, or transmit anything to its
  own maintainers. The tool itself (everything under `src/mfdoc/`) makes
  only the explicit, user-initiated calls `mfdoc batch` makes to whichever
  provider `--provider` selects (the Anthropic API directly, or the same
  Claude models via Google Cloud Vertex AI) — there is no other network
  code in the installed package to audit for this. The one exception in
  the repository as a whole is `scripts/fetch_cobol_course_fixtures.py`, a
  dev-only, opt-in script (not part of the installed package, never run in
  CI or by any `mfdoc` command) that pulls public, appropriately-licensed
  fixture files from GitHub — see
  [Supplementary smoke fixtures from public corpora](extending.md#supplementary-smoke-fixtures-from-public-corpora).
- It does not claim citation *accuracy*, only citation *resolution* —
  `mfdoc validate` proves every citation points at a real line; it does not
  prove that line actually supports the claim next to it. A citation
  pointing at the wrong line currently passes validation. Sampling-based
  accuracy checking is an open item, not yet built (tracked in the plan
  doc's Phase 5) — don't represent citation validation as a stronger
  guarantee than it is when describing this to a client.

## Known correctness limitations relevant to reliance decisions

These affect how much weight to put on the output, which is a compliance
question as much as a technical one — see the README's "Known limitations"
for the full list. The two most likely to matter for a sign-off decision:

- The dialect scanners are heuristic pattern matchers, not grammars. They
  are built to flag what they can't parse (as a measurable recognition
  rate) rather than to silently guess — but a codebase with unusual local
  conventions can still have a real recognition gap. Check `mfdoc gate`
  output before treating any output as authoritative.
- Dynamic dispatch (a call target held in a variable, not written literally)
  cannot be resolved from static source analysis. Call graphs built from
  such code are incomplete *by nature*, not by tooling gap, and are
  reported as such rather than hidden — do not represent them to a client
  as complete.

## Licensing — what the MIT license permits

This repository is [MIT-licensed](../../LICENSE). That's a permissive
license, relevant to procurement/legal sign-off separately from the data-
handling questions above:

- **Free to use, including commercially.** No fee, no royalty, no
  restriction on using it for a paid client engagement.
- **Free to copy, modify and redistribute.** You can fork it, change the
  scanners or templates for a client's dialect, bundle it into an internal
  toolset, or hand modified copies to a third party — the license doesn't
  restrict any of that.
- **No obligation to share changes back.** Unlike a copyleft license
  (e.g. GPL), MIT does not require you to publish modifications or make
  derivative works open source. Internal, private forks are fine.
- **Only real condition: keep the copyright notice and license text.**
  The MIT text in `LICENSE` must be included with any copy or substantial
  portion of the software you redistribute — including a modified fork.
  That's the entire obligation; there's no notice requirement toward end
  clients whose *source code* you run it against, only toward recipients of
  the tool itself.
- **No warranty, no liability.** The software is provided "as is" — the
  license disclaims all warranties and caps the authors' liability at
  nothing. This is standard for open-source software and is separate from
  whatever warranty terms your own engagement contract with a client sets;
  the MIT license governs the tool, not your service agreement.
- **What it does *not* grant:** the license covers this codebase only. It
  says nothing about the client's source code you feed into it — that
  remains the client's property under whatever terms your engagement
  contract sets, untouched by this tool's own license.

## Checklist before a new engagement touches this tool

1. Confirm where the source is contractually allowed to live and be
   processed, and pick a machine/environment consistent with that.
2. Decide whether a hosted model may be used for the narrative stage at
   all, and if so, whose data-handling terms apply.
3. Run the deterministic pipeline (`ingest` → `derive` → `coverage` →
   `gate`) before deciding anything about the narrative stage — it's local
   regardless, and the gate result may change the answer to (2) by showing
   how much of the work even needs a model versus needing calibration.
4. Set `options.redact.patterns` for anything already known to be sensitive
   in this codebase before generating any brief, and agree a disclosure
   path for anything found afterwards.
5. Agree a retention and disposal plan for `.mfdoc/index.db` before it's
   created, not after.
6. Keep `project.yml` and generated docs in version control as the audit
   trail; do not commit the index.
