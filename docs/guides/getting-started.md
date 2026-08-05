# Getting started (no assumed background)

This guide is for someone asked to run this tool who has not used Claude Code,
the command line, or Python before, and has never heard of Natural, Adabas,
Mantis or Supra either. If you already know what a REPL is, skip to the
[README](../../README.md) instead — this document spells out things that
guide would otherwise assume.

## What this thing actually is

A mainframe built decades ago in a "4GL" (fourth-generation language) —
usually Natural, sometimes Mantis — often has no documentation describing
what it does. The people who wrote it have retired or moved on. This project
reads the raw source code and produces a first draft of that missing
documentation: what each program does, what data it touches, how batch jobs
and screens fit together, and — importantly — a list of everything it
*couldn't* work out, phrased as questions for someone who still knows the
system.

It does not modernise, rewrite, or migrate anything. It reads and describes.

## Two things working together

**Claude Code** is an AI coding assistant that runs in your terminal. Think
of it as a very capable assistant that can read files, run commands, and
write documents, but only when you (or a defined "skill") tell it what to do
and in what order.

**This repository** is a "skill" for Claude Code — a packaged set of
instructions (`SKILL.md`) plus a Python program (`mfdoc`) that does the
actual reading of source code. The reason there are two parts, not one, is
important and explained below.

## Why an AI isn't just pointed at the code directly

The obvious approach — "give the AI all the source code, ask it to write
documentation" — produces documentation that *reads* well and is
*confidently wrong* in places, because the model fills small gaps with
plausible guesses rather than flagging them. Nobody can tell which
paragraphs are guesses just by reading the output, and a wrong paragraph
that looks right is more dangerous than an honest gap, because it stops
people from checking the actual code.

So the work is split:

1. A deterministic Python program (no AI involved) reads the source line by
   line and records verifiable facts — which programs exist, which call
   which, which data files they touch — into a small database.
2. Only once those facts exist does anything resembling narrative get
   written, and every sentence is required to point back at an exact file
   and line number. If a fact isn't in the database, it doesn't go in the
   document — the tool will refuse to publish it (see "Why does validation
   fail" below).

## What you need on your machine

- **Python 3.10 or newer.** Python is a programming language; you're not
  going to write any, just run a program written in it. Check what you have
  with `python3 --version` in a terminal. If that command isn't recognised,
  or reports a version below 3.10, install Python from
  [python.org](https://www.python.org/downloads/) first.
- **A terminal.** On Mac, this is the "Terminal" app. On Windows, use
  PowerShell, or better, Claude Code's own bundled terminal if you're running
  it there. On Linux, whatever terminal came with your distribution.
- **The source code you're documenting**, exported off the mainframe as flat
  files (a listing, an unload, an export — whatever your mainframe team
  calls it). This tool never connects to a mainframe itself; someone else
  gets the files off it first.

You do **not** need an Anthropic/Claude API key or account to run the core
pipeline. That's only needed for the optional `mfdoc batch` command, covered
below.

## Getting the code onto your machine

This project lives on GitHub, a website that hosts code. "Git" is the
version-control tool GitHub is built on; you don't need to understand either
in depth — you just need the files sitting in a folder on your machine. Two
ways to get there:

**Option A — download a ZIP (simplest, no extra software).** On the GitHub
page for this repository, click the green **Code** button, then **Download
ZIP**. Unzip it wherever you keep projects (e.g. your Documents folder or
home directory). This gives you a snapshot — fine if you're just going to
run the tool and won't be pulling in later updates yourself.

**Option B — `git clone` (better if you'll update it later).** This needs
Git installed:

- Check with `git --version` in a terminal. If it's not recognised, install
  it from [git-scm.com](https://git-scm.com/downloads) (Windows/Mac) or via
  your package manager (Linux, e.g. `sudo apt install git`).
- Then, in a terminal, navigate to where you want the folder to end up (e.g.
  `cd ~/Documents`) and run:

  ```bash
  git clone <repository-url>
  ```

  replacing `<repository-url>` with the URL from the same green **Code**
  button on GitHub (use the HTTPS one unless you already have SSH keys set
  up — if that sentence means nothing to you, use HTTPS). This downloads the
  files into a new `legacy-functional-docs` folder, and later, `git pull`
  from inside that folder fetches any updates.

Either way, you should end up with a folder containing `SKILL.md`,
`pyproject.toml`, and the other files listed under "Layout" in the
[README](../../README.md). Everything from here on assumes your terminal is
open *inside* that folder — `cd` into it if it isn't
(`cd legacy-functional-docs` or `cd path/to/wherever/you/put/it`).

## Installing it

### (Optional but recommended) set up a virtual environment first

A "virtual environment" is a self-contained pocket of Python packages for
one project, so what you install here can't clash with anything else on your
machine (or vice versa) — like a project having its own toolbox rather than
sharing the house one. Skipping this step won't stop the tool from working;
it just means the packages install machine-wide instead.

From inside the project folder:

```bash
python3 -m venv .venv
```

This creates a `.venv` folder holding the isolated environment (one-time
step). Then activate it — you'll need to do this once per new terminal
session, not just once ever:

- **Mac/Linux:** `source .venv/bin/activate`
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **Windows (Command Prompt):** `.venv\Scripts\activate.bat`

Your terminal prompt should now show `(.venv)` at the start of the line —
that's confirmation it's active. Everything installed below (`pip install
-e .`, etc.) now goes into this pocket rather than system-wide. To leave it
later, run `deactivate`; to come back, `cd` into the project folder and
activate it again.

### Install the tool

Open a terminal, navigate into this folder (`cd` followed by the path — e.g.
`cd ~/legacy-functional-docs`), and run:

```bash
pip install -e .
```

`pip` is Python's package installer. This command reads `pyproject.toml`
(a file describing what this project needs) and makes the `mfdoc` command
available everywhere in your terminal. `-e` means "editable" — useful if
you're also going to change the code, harmless if you're not. This is a
one-time step.

Check it worked:

```bash
mfdoc --help
```

If you see a list of sub-commands (`ingest`, `derive`, `coverage`, ...),
installation succeeded.

## The five commands you'll actually run

Everything else in the README is detail on top of this sequence. Copy
`config/project.example.yml` to `project.yml`, edit the paths inside it to
point at your source files (the comments in that file explain each setting),
then:

```bash
mfdoc ingest   --config project.yml    # read the source into a local database
mfdoc derive   --config project.yml    # work out call graphs, data usage, etc.
mfdoc coverage --config project.yml    # report how much it understood
mfdoc gate     --config project.yml    # pass/fail check against your quality bar
mfdoc brief    --config project.yml --system   # produce a first fact summary to read
```

Nothing here calls out to the internet or to any AI model. It's all local,
reading files and writing to a small file called `.mfdoc/index.db` inside
your project folder (a "SQLite database" — think of it as a single-file
spreadsheet that a program can query quickly).

### Reading the coverage report

`mfdoc coverage` prints numbers like `line_recognition_rate: 0.94`. That
means the tool understood 94% of the lines it looked at; the rest were
flagged rather than guessed at. `mfdoc gate` turns those numbers into a
simple pass/fail against thresholds you set in `project.yml`, so you don't
have to eyeball the raw numbers yourself. **If it fails, stop and fix the
input or the tool's understanding of your dialect before generating any
documentation** — see `mfdoc calibrate` in the main README and
[`reference/mantis-supra.md`](../../reference/mantis-supra.md) if you're
working with Mantis or Supra source, which typically need this step.

### Turning facts into documents

`mfdoc brief` produces a plain-text summary of everything the tool knows
about one program, one data file, or the whole system — call this a
"brief." A brief is not the final document; it's the raw material. Either:

- Ask Claude Code (in a chat session, with this skill installed) to write
  the actual document from the brief, following the rules in
  [`reference/writing-rules.md`](../../reference/writing-rules.md), or
- For programs specifically (the highest-volume, most repetitive document
  type), run `mfdoc batch`, which does this automatically for every program
  at once. This is the one command that talks to the internet — see below.

### The one command that needs an API key

`mfdoc batch` sends each program's brief to Anthropic's Claude API to have
the narrative written automatically, instead of doing it one at a time in a
chat session. This is optional, and only relevant once you have more than a
handful of programs to document (a real mainframe system might have
thousands). It needs:

```bash
pip install 'mfdoc[batch]'
```

and an `ANTHROPIC_API_KEY` environment variable set to a key from
[console.anthropic.com](https://console.anthropic.com/). If you don't have
one, skip this — everything else works without it, just interactively
through Claude Code instead of unattended.

**Before you run this against real client source**, read
[`security-and-compliance.md`](security-and-compliance.md) — it covers what
data actually leaves your machine when you use this command and what
doesn't.

### Checking the output is trustworthy

```bash
mfdoc validate --config project.yml --docs docs/functional
```

This is the step that enforces the "no invented facts" rule mechanically.
It checks every citation in every generated document (things that look like
`[[MMP0100:55]]`) actually points at a real line in a real file, and that
every sentence making a factual claim either has one of those citations or
is explicitly marked as uncertain. If it fails, it tells you exactly which
sentence and why — fix the document (or, more often, regenerate it from a
better brief) rather than deleting the check.

## Terms you'll see and what they mean

| Term | Plain meaning |
|---|---|
| Natural / Adabas | A mainframe programming language (Natural) and the database it usually talks to (Adabas), both from Software AG |
| Mantis / Supra | Another such language/database pair, from a different vendor |
| DDM | "Data Definition Module" — Adabas's description of a data file's fields |
| FDT | "Field Definition Table" — the lower-level, more technical version of the same thing |
| Copybook | Reusable data-layout code shared between COBOL programs, similar in spirit to Natural's copycode |
| JCL | "Job Control Language" — mainframe scripts that run batch jobs |
| CICS | A mainframe system for running interactive (screen-based) transactions |
| Dialect | Which of the above languages/formats a given source file is written in — this tool needs to know, either by guessing or by being told |
| Member | One self-contained unit of source — one program, one copybook, one data definition — inside a larger file |
| Citation | A `[[MEMBER:LINE]]` tag pointing at the exact source line a claim comes from |
| Gap | Something the tool could not work out; becomes a question for a human expert |
| Brief | The plain-text fact summary handed to a human or a model before writing narrative |

## If something goes wrong

- `mfdoc: command not found` — the `pip install -e .` step either wasn't run
  or didn't complete; re-run it and check for errors in its output.
- A dialect's recognition rate is low (`mfdoc coverage` or `mfdoc gate`
  reports it) — this is expected for Mantis and Supra on first run; see
  "Reading the coverage report" above.
- `mfdoc batch` fails immediately — almost always a missing
  `ANTHROPIC_API_KEY` or a missing `pip install 'mfdoc[batch]'`.
- Anything else — the [main README](../../README.md)'s "Known limitations"
  section lists the things this tool is upfront about not doing well; check
  there before assuming it's a bug.

## Where to go next

- [README.md](../../README.md) — the full command reference and project layout
- [architecture.md](architecture.md) — how the pieces fit together, for anyone
  who wants the technical picture
- [security-and-compliance.md](security-and-compliance.md) — data handling,
  what reaches a model, and what to check before using this on client source
- [extending.md](extending.md) — for developers adding a new dialect or
  document type
