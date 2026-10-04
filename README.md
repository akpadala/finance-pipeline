# Personal Finance Data Pipeline

A small but real ELT data pipeline for your own spending: bank transaction
exports → DuckDB → dbt (staging → intermediate → marts) → a Streamlit
budget dashboard. Built as a Data Engineering portfolio project and as an
actual budgeting tool.

**Stack:** Python, SQL, DuckDB, dbt, Streamlit. See `docs/architecture.md`
for the full data-flow diagram and the reasoning behind each layer.

---

## Quickstart (VS Code)

1. **Open the folder.** In VS Code: `File → Open Folder...` → select
   `finance-pipeline`.

2. **Create a virtual environment and install dependencies.** Open a
   terminal in VS Code (`` Ctrl+` ``) and run:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

   VS Code will usually prompt "Select Python Interpreter" — pick the one
   inside `.venv` so IntelliSense and the terminal agree on which Python
   you're using.

3. **Point dbt at the profile checked into this repo** (instead of the
   default `~/.dbt/profiles.yml`):

   ```bash
   export DBT_PROFILES_DIR=$(pwd)/finance_dbt      # Windows: set DBT_PROFILES_DIR=%cd%\finance_dbt
   ```

   (`make pipeline` below sets this for you automatically — this step
   only matters if you run `dbt` commands directly.)

4. **Run the whole pipeline:**

   ```bash
   make pipeline
   ```

   This generates a year of synthetic sample transactions, loads them
   into DuckDB, runs every dbt model, and runs all the dbt tests. No
   `make`? Run the five commands inside `Makefile`'s `pipeline` target by
   hand, in order — `generate-data`, `ingest`, `dbt-seed`, `dbt-run`,
   `dbt-test`.

5. **Launch the dashboard:**

   ```bash
   make dashboard
   ```

   Opens at `http://localhost:8501`.

You now have a working pipeline running against realistic synthetic data.

---

## Using your own real transactions

This is meant to become your actual budget tool, not stay a demo forever.
There are three ways to get your real data in, in increasing order of how
much they protect it at rest. All three expect the same column shape:

```
Transaction Date, Post Date, Description, Category, Type, Amount
```
(`Memo` is optional.) Export a CSV from your bank or card issuer's website
(almost all of them offer this — look for "Download" or "Export" near
your transaction list) and rename its columns to match if needed; most
banks already use something very close to this shape.

### Option A: plaintext in `data/raw/`

Drop the renamed CSV into `data/raw/` alongside (or instead of)
`sample_transactions.csv`, set your real budget targets in
`finance_dbt/seeds/category_budgets.csv`, then rerun `make pipeline` and
`make dashboard`. `.gitignore` already keeps everything in `data/raw/`
except the synthetic sample file out of git — but the file still sits in
plaintext on your disk the whole time it's there. Fine for a lot of
people; Option B exists for anyone who'd rather it didn't.

### Option B: encrypted at rest locally

```
python3 scripts/encrypt_bank_csv.py path/to/your_real_export.csv
```

This prompts for a passphrase (twice, to confirm), writes
`data/private/your_real_export.csv.enc`, verifies the encrypted file
actually decrypts back to the original bytes, and only then deletes the
plaintext input. `data/private/` is wholly gitignored. To run the
pipeline against it:

```
export BANK_DATA_PASSPHRASE="..."       # or you'll be prompted
python3 ingestion/load_to_duckdb.py     # decrypts straight into memory,
                                         # never writes plaintext back to disk
```

The key is derived from your passphrase via PBKDF2 (480,000 iterations,
random per-file salt) and is never itself stored anywhere — forget the
passphrase and the file is unrecoverable by design. See
`ingestion/crypto_utils.py` for the actual implementation.

### Option C: upload it through the dashboard

The "Try with your own data" expander in the dashboard's sidebar does
this without touching the command line at all: your CSV is encrypted the
moment it's received (a random key that lives only in that browser
session's server memory), processed through the *exact same* dbt models
as the sample data in an isolated, disposable database, and the whole
temp directory — ciphertext, database, dbt logs, all of it — is deleted
immediately after, success or failure. See `dashboard/secure_upload.py`
for the full flow, spelled out step by step in its docstring.

**What this does and doesn't actually protect**, stated plainly rather
than oversold:

- It genuinely minimizes how long your data exists as plaintext on disk,
  and where: a single isolated temp directory, for the few seconds
  dbt needs, deleted deterministically rather than left to OS cleanup.
- It is **not** end-to-end encryption. This server process has to be
  able to read your data to process it, so it necessarily holds the key
  at some point. "Encrypted" here means *at rest*, not *unreadable to
  the app itself* — anyone who could get arbitrary code execution on the
  server while your upload is being processed could read it, same as
  any program that touches your data at all.
- Only aggregated numbers (monthly totals, budget comparisons, which
  merchants recur) are kept in session memory for the rest of your
  session, never raw transaction rows.
- If you ever deploy this somewhere shared or public, default the live
  version to the sample data and keep this upload path clearly labeled
  as a "try it" feature. A free-tier hosted demo has no compliance
  backing behind it — fine for a portfolio piece showing how you'd
  *build* this, not the place for anyone's actual account numbers.

**Never commit your real financial data to a public GitHub repo** —
`.gitignore` covers `data/raw/*.csv` (except the sample) and all of
`data/private/`, but it's still worth checking `git status` before you
push if you've been experimenting with a real export.

---

## Phase 2: orchestration (Airflow) + ML categorization

Phase 1 above runs the pipeline by hand (`make pipeline`). Phase 2 adds
two independent, optional pieces on top of that working base.

### Airflow (real orchestration, via Docker Desktop)

This turns the `Makefile`'s chain into a real Airflow DAG
(`airflow/dags/finance_pipeline_dag.py`) with the same four steps —
`ingest_transactions → dbt_seed → dbt_run → dbt_test` — running under a
scheduler instead of by hand. Requires
[Docker Desktop](https://www.docker.com/products/docker-desktop/)
installed and running.

```powershell
cd airflow
docker compose up airflow-init      # first time only: sets up the metadata DB + admin user
docker compose up                   # starts the webserver + scheduler
```

Open `http://localhost:8080` (username and password are both `admin`),
find the `finance_pipeline` DAG, un-pause it (toggle on the left), and
click the play button to trigger a run. Click into the run to see each
task's status and logs — this is the same task graph described in
`docs/architecture.md`, just running under a real orchestrator now.
`Ctrl+C` in the terminal (then `docker compose down` from `airflow/`)
stops everything.

If a task fails, `alert_on_failure` in the DAG file logs a clear alert
with which task failed and why — that function is a real, working
extension point for wiring in a Slack webhook or email once you have
credentials for one; see the docstring in the DAG file for exactly what
to change.

### ML-based categorization (scikit-learn)

`ml/train_categorizer.py` trains a text classifier (TF-IDF over
character n-grams + logistic regression) on the categories the
rule-based macro already assigned, then applies it to (a) any
transactions the rules couldn't categorize, and (b) a handful of
merchants the rules have never seen at all, to demonstrate that it's
actually generalizing rather than memorizing keywords. Full rationale in
the file's docstring — including why this doesn't replace the rules.

```powershell
pip install -r ml\requirements.txt
python ml\train_categorizer.py
```

Prints a held-out accuracy score and a full classification report, then
the model's predictions on novel merchants like `PANERA BREAD` and
`COSTCO WHOLESALE`. Saves the trained model to `ml/model/categorizer.joblib`.

---

## Phase 3: containerized one-command demo + CI

Phase 1 and 2 above both assume you've got Python and dbt set up locally.
Phase 3 adds a path that needs none of that, plus automated testing on
every push.

### Docker Compose (the whole pipeline + dashboard, one command)

This is a *separate* stack from `airflow/docker-compose.yaml` — Airflow
containerizes the orchestrator; this containerizes the pipeline and
dashboard themselves, for the fastest possible "clone it and see it work"
path (no `make setup`, no local venv, no local dbt install at all).

```powershell
docker compose up --build
```

(or `make docker-up`, if you have `make` available). This builds one
image (see `Dockerfile`) and runs it as two services:
`pipeline` generates the synthetic data, loads it, and runs `dbt seed` /
`dbt run` / `dbt test` — then exits. `dashboard` waits for that to
*succeed* (`condition: service_completed_successfully` in
`docker-compose.yml` — not just "started") before launching Streamlit, so
you can never land on a dashboard backed by a half-built or failed
warehouse. Once it's up, open `http://localhost:8501`.

`Ctrl+C`, then `docker compose down`, stops everything. The generated
`data/` and `warehouse/` files are bind-mounted into your actual repo
folder (not hidden inside a container volume), so you can inspect
`warehouse/finance.duckdb` with any DuckDB client exactly like you could
running it without Docker.

One detail worth being able to explain: Streamlit defaults to binding
`localhost` *inside* the container, which Docker's port mapping can't
reach from your host machine — that's why the `command:` in
`docker-compose.yml` passes `--server.address=0.0.0.0` explicitly.

### CI (GitHub Actions)

`.github/workflows/ci.yml` runs the entire pipeline — generate synthetic
data → ingest → `dbt seed` → `dbt run` → `dbt test` — on every push and
pull request to `main`, on a clean GitHub-hosted runner. It needs no
secrets and no external services, because it runs against the committed
synthetic data generator, not real bank data — fully reproducible from a
bare checkout. A broken model now fails a GitHub Actions check with a red
X, instead of only failing quietly on your own machine.

Once this repo is pushed to GitHub (see below), every push triggers a
run automatically — check the **Actions** tab.

### Pushing this to GitHub

If you haven't already:

```powershell
git init
git add .
git commit -m "Personal finance data pipeline"
```

Then create a new empty repo on GitHub (no README/license/gitignore —
you already have those), and:

```powershell
git remote add origin https://github.com/<your-username>/finance-pipeline.git
git branch -M main
git push -u origin main
```

Push a small change afterward (or just re-push) and check the **Actions**
tab on GitHub to watch the CI workflow run — that green checkmark next to
your commits is worth linking to directly from your resume/LinkedIn.

### Cloud warehouse: why this stays on DuckDB

Phase 3 was originally scoped to include pointing this project at a real
cloud warehouse (Snowflake or BigQuery's free tier). That's deliberately
**not** done here, and that's a decision worth being able to defend
rather than something left unfinished:

- The dbt layer was never written against DuckDB-specific behavior. The
  only thing that changes to target Snowflake or BigQuery instead is
  `finance_dbt/profiles.yml` — a different `type:`, credentials, and
  warehouse/dataset name. Every model, macro, seed, and test is unchanged.
  That portability *is* the design decision worth talking about, not a
  gap to apologize for.
- A live trial-account connection adds account creation and credential
  management to a public portfolio repo for no functional gain — the SQL
  dbt compiles is identical either way — and free-tier trials expire,
  which risks an interviewer cloning this later and hitting a dead
  connection instead of a working demo.
- If you do want to wire up a real warehouse later (or during an
  interview, as a live extension), `docs/architecture.md`'s "Why DuckDB"
  section has the full reasoning, and the change is genuinely small: a
  new dbt profile, not a new project.

---

## Project structure

```
finance-pipeline/
├── data/raw/                  synthetic (or your own, plaintext) bank CSV exports
├── data/private/               your own exports, encrypted (gitignored; see
│                                "Using your own real transactions")
├── scripts/
│   ├── generate_sample_data.py  fabricates a year of realistic transactions
│   └── encrypt_bank_csv.py      encrypts a real export into data/private/
├── ingestion/
│   ├── load_to_duckdb.py        CSV (plaintext or decrypted) → DuckDB raw.transactions
│   │                             (idempotent; also used programmatically by
│   │                             dashboard/secure_upload.py)
│   └── crypto_utils.py          PBKDF2 + Fernet encrypt/decrypt helpers
├── finance_dbt/                 the dbt project
│   ├── models/staging/           1:1 cleanup of the raw source
│   ├── models/intermediate/      categorization + business logic
│   ├── models/marts/             star schema + reporting tables
│   ├── seeds/category_budgets.csv  your monthly budget targets
│   └── macros/categorize_transaction.sql
├── dashboard/
│   ├── app.py                   Streamlit entry point: loads data, renders the page
│   ├── dashboard_html.py        builds the single-page scroll-nav HTML (+ Plotly)
│   ├── transform.py             marts DataFrames → the dict dashboard_html expects
│   ├── secure_upload.py         "try your own data": encrypt → isolated dbt run → shred
│   ├── assets/                  banner photos + the bundled Plotly JS bundle
│   └── tests/test_transform.py  plain-assertion regression tests (no pytest needed)
├── airflow/                      Phase 2: real orchestration
│   ├── docker-compose.yaml       lean LocalExecutor Airflow stack
│   ├── Dockerfile                Airflow image + this project's deps
│   └── dags/finance_pipeline_dag.py  same 4 steps as `make pipeline`, as a DAG
├── ml/                           Phase 2: ML categorization add-on
│   └── train_categorizer.py      TF-IDF + logistic regression classifier
├── .github/workflows/ci.yml      Phase 3: runs the pipeline + dbt test on every push
├── Dockerfile                    Phase 3: image shared by both compose services
├── docker-compose.yml            Phase 3: one-command pipeline + dashboard
├── warehouse/                    generated DuckDB file (gitignored)
├── Makefile                      orchestrates the pipeline end to end
└── docs/architecture.md          data-flow diagram + design rationale
```

---

## Roadmap

This is deliberately scoped as Phase 1 of three, so it's buildable without
being overwhelming, and so there's a natural "what's next" answer in an
interview.

### Phase 1 — done (this repo)
Ingestion script, DuckDB warehouse, dbt project (staging / intermediate /
marts, tests, seeds), Streamlit dashboard, `Makefile` orchestration.

### Phase 2 — done (see "Phase 2" section above)
- **Airflow.** `airflow/dags/finance_pipeline_dag.py` — the same four
  steps as `make pipeline`, running as a real DAG under a Dockerized
  Airflow LocalExecutor stack, with an `on_failure_callback` alerting
  hook. This is the single highest-value addition for a DE resume —
  orchestration is the thing that separates "a script" from "a pipeline."
- **Data quality alerting.** `alert_on_failure` in the DAG fires on any
  task failure, including `dbt_test` — i.e. exactly when a data quality
  check catches a real problem. Logs a clear alert now; the docstring
  documents wiring it to a real Slack webhook or email once you have
  credentials for one (deliberately not hardcoded into a public repo).
- **ML-based categorization.** `ml/train_categorizer.py` — a TF-IDF +
  logistic regression classifier (scikit-learn) trained on the
  rule-assigned categories, evaluated on a held-out test set, and applied
  to merchants the rules have never seen. This is the ML piece from your
  original idea, done as a scoped, understandable add-on instead of a
  from-scratch pipeline.

### Phase 3 — done (see "Phase 3" section above)
- **Docker Compose.** `docker-compose.yml` + `Dockerfile` containerize the
  pipeline and dashboard, so `docker compose up --build` reproduces the
  whole stack with no local Python/dbt setup at all — a separate,
  simpler stack from the Airflow one added in Phase 2.
- **CI/CD.** `.github/workflows/ci.yml` runs the full pipeline and every
  dbt test on every push and pull request, so a broken model shows up as
  a failed GitHub Actions check, not a surprise later.
- **Cloud warehouse — deliberately deferred, not built.** Documented
  instead of live-wired: the dbt layer is already warehouse-agnostic (a
  `profiles.yml` change, not a rewrite, to target Snowflake/BigQuery), and
  standing up a real trial account added credential-management overhead
  and expiry risk to a public repo without changing any of the SQL. See
  the "Cloud warehouse" section above for the full reasoning — being able
  to explain *why* you didn't build something is as much a signal as
  building it.

---

## Resume bullets (draft)

Adjust these once you've actually built and run everything — specifics
you experienced firsthand (a bug you hit, a number you found surprising)
read as far more credible than generic bullet points.

```
Personal Finance Data Pipeline                                    2026
• Built an ELT pipeline (Python, DuckDB, dbt) that ingests bank
  transaction exports, applies rule-based categorization and data-quality
  tests across a staging/intermediate/marts layered architecture, and
  surfaces budget-vs-actual insights through a Streamlit dashboard
• Designed a star-schema data model (fact + dimension tables) with 15+
  dbt tests (uniqueness, referential integrity, accepted values) enforced
  on every pipeline run
• Implemented idempotent ingestion and orchestrated the pipeline as an
  Airflow DAG (Docker), chaining ingest → transform → test → serve with
  automatic retries and failure alerting
• Trained a TF-IDF + logistic regression text classifier (scikit-learn)
  to categorize transactions the rule-based layer couldn't, with a
  confidence threshold to route uncertain predictions for manual review
  instead of trusting low-confidence guesses
• Containerized the pipeline and dashboard (Docker Compose) and added a
  GitHub Actions CI workflow that runs the full dbt test suite on every
  push, catching data-quality regressions before merge
• Built a privacy-preserving upload path (PBKDF2 + Fernet/AES encryption,
  per-session isolated DuckDB databases, deterministic temp-file cleanup)
  letting the dashboard process a user's own real transaction export
  through the same tested dbt models without ever persisting it in
  plaintext or touching the shared warehouse
```

---

## On using AI to build this

This project was built with Claude's help — architecture decisions,
dbt/SQL, and the Streamlit dashboard were written collaboratively, the
way you'd pair with a senior engineer. Being upfront about that in an
interview is the right move, and it's also not a liability *if you can
actually explain every piece* — which is the whole point of the
walkthrough below and `docs/architecture.md`. Interviewers generally care
far less about "did you type every character" and far more about "do you
understand what this does and why it's built this way." Being able to
say "I used AI to help me build this, and here's exactly how the
categorization logic works and why I chose rules over ML for it" is a
strong answer. Not being able to explain a line in your own README is the
actual risk — so before an interview, reread `docs/architecture.md` and
make sure every design decision in it makes sense to you, not just to
whoever wrote it.

---

## Talking points / likely interview questions

- **"Walk me through the architecture."** → `docs/architecture.md`'s
  diagram, in order: raw CSV → DuckDB raw layer → dbt staging →
  intermediate (categorization) → marts (star schema) → dashboard.
- **"Why dbt instead of just writing pandas transformations?"** → dbt
  gives you version-controlled, tested, documented SQL transformations
  with lineage (`dbt docs generate` builds a dependency graph
  automatically) — that's the industry-standard "modern data stack"
  pattern, and it's declarative (you describe the end state; dbt figures
  out execution order) instead of imperative.
- **"Why DuckDB and not Postgres/Snowflake?"** → see the "Why DuckDB"
  section in `docs/architecture.md` — same SQL dialect family, zero
  infra, and the migration path to a real warehouse is a config change.
- **"How do you know your categorization is accurate?"** → the
  `accepted_values` dbt test constrains categories to a known list; the
  known failure mode (unseen/mixed-use merchants) and its fix (an ML
  classifier) are called out explicitly rather than hidden.
- **"What would you do differently at scale / for a team?"** → a real
  warehouse instead of a local file, CI enforcing tests before merge, and
  probably incremental dbt models instead of full-refresh tables once
  transaction volume gets large — covered in the Phase 3 roadmap above.
- **"Why Airflow instead of just cron?"** → cron can trigger a script on
  a schedule, but it can't express "this task depends on that one," retry
  a single failed step without rerunning everything before it, show you
  a history of every run, or alert you on failure. Airflow's DAG *is*
  the dependency graph (`ingest → dbt seed → dbt run → dbt test`) — the
  same four steps as the Makefile, just executed by something built to
  run pipelines instead of a build tool repurposed for it.
- **"Why LocalExecutor and not Celery/Kubernetes?"** → LocalExecutor runs
  tasks as local processes on one machine — the right amount of
  infrastructure for one person's pipeline. Celery/Kubernetes executors
  distribute tasks across multiple worker machines, which you reach for
  once a single machine's capacity is the actual bottleneck — not before.
- **"Doesn't the ML classifier just learn the same rules?"** → not
  quite — it's trained on the *outputs* of the rules (treating them as
  labels), but a TF-IDF + logistic regression model learns the general
  statistical pattern of which character sequences correlate with which
  category, so it generalizes to merchants no rule mentions at all (see
  the `NOVEL_EXAMPLES` predictions in `ml/train_categorizer.py`'s output)
  — that's the actual gap it closes versus the rules.
- **"Your model reported 99% accuracy — is that real?"** → No, and
  catching that is the actual point of this section. This dataset only
  has ~30 distinct merchants repeated many times, so a random
  transaction-level train/test split puts the same merchants on both
  sides — the 99% measures memorization of merchants already seen, not
  generalization to new ones. `ml/train_categorizer.py` reports that
  number labeled explicitly as a memorization check, not a real accuracy
  claim, and treats the `NOVEL_EXAMPLES` predictions (merchants absent
  from training entirely) as the actual generalization test — where it
  does noticeably worse, and says so via low confidence scores rather
  than guessing confidently and wrong. Being able to explain *why* a
  flattering number is misleading, unprompted, is a stronger signal than
  the number itself would have been.
- **"Why two separate Docker Compose stacks (root + `airflow/`)?"** →
  They answer different questions. `airflow/docker-compose.yaml`
  containerizes the *orchestrator* — Postgres metadata DB, webserver,
  scheduler — because Airflow itself needs infrastructure to run. The
  root `docker-compose.yml` containerizes the *pipeline and dashboard*,
  so someone without Python or dbt installed can still run
  `docker compose up --build` and see the whole thing work in one
  command. Keeping them separate means you can demo either path
  independently instead of forcing every reviewer through the heavier
  Airflow setup just to see the dashboard.
- **"Why didn't you actually connect a cloud warehouse?"** → A
  considered tradeoff, not a shortcut: the dbt models never depend on
  DuckDB-specific behavior, so targeting Snowflake or BigQuery is a
  `profiles.yml` change, not new SQL — the portability is already proven
  by the architecture, not something a live connection would add
  evidence for. What a live trial account *would* add is credential
  management in a public repo and a free tier that can expire and leave
  a dead connection for whoever clones this next — real cost for no
  functional difference in what the project demonstrates.
- **"Is the upload feature actually encrypted, or is that just a
  label?"** → The honest, specific answer, not just "yes": the uploaded
  bytes are encrypted (Fernet/AES) the instant they're received, with a
  random key that lives only in that browser session's server memory.
  Only the ciphertext — never plaintext — is written to disk, into an
  isolated 0700 temp directory that's deleted right after processing.
  What's *not* true is "end-to-end encrypted" — this process has to
  decrypt it to run the pipeline, so it's protection for data at rest,
  not from the app itself. Being precise about that distinction, instead
  of letting "encrypted" imply more than it does, is the actual signal —
  especially for a company whose core product is trust with financial
  data (worth saying outright in a Capital One interview specifically).
- **"Why not just use a passphrase for the dashboard upload too, like
  the local CLI path?"** → Different threat models need different key
  models. `scripts/encrypt_bank_csv.py` protects a file sitting on your
  own disk indefinitely between runs — a passphrase only you know is the
  right fit there. A dashboard upload is processed once and discarded
  within seconds; there's no human available to type a passphrase into a
  web request even if it made sense to. A random, ephemeral,
  session-scoped key (`Fernet.generate_key()`, held in
  `st.session_state`) is the right primitive for "protect this while it
  briefly exists," the same way a TLS session key differs from a
  long-term account password.
- **"How is this different from Mint / YNAB / Copilot / [any real
  budgeting app]?"** → It isn't trying to be one, and saying that plainly
  is a stronger answer than pretending otherwise. Those are consumer
  products optimized for day-to-day use — live bank sync via Plaid,
  mobile notifications, years of polish. This project demonstrates the
  data engineering that would sit *underneath* a product like that, not
  a competitor to the product itself:
  - Every number is traceable to a version-controlled, tested dbt model
    you can open and read — 15+ tests enforcing data quality on every
    run, not a black-box backend you have to trust.
  - The transformation layer is warehouse-portable (DuckDB → Snowflake/
    BigQuery is a `profiles.yml` change, not a rewrite) — a closed
    consumer app can't be repointed at anything.
  - It's orchestrated and tested like production data infra (Airflow
    DAG, idempotent ingestion, CI running the full suite on every push),
    not a one-off script with a UI on top.
  - Using CSV exports instead of a live Plaid/bank-OAuth connection is a
    *deliberate scope boundary*, not a missing feature — handling live
    bank credentials is real compliance and security surface area that
    doesn't belong in a portfolio project, and it isn't what this
    project is built to demonstrate anyway.
  - The security design is fully inspectable: you can read exactly how
    an upload is encrypted, isolated, and discarded, line by line,
    instead of trusting an opaque vendor backend to have done it right.

  One-line version for the actual conversation: *"I'm not trying to
  out-build Mint — I'm demonstrating the kind of tested, documented,
  auditable pipeline a company like Capital One would actually run
  behind a product like that. The dashboard is the visible output; the
  point is everything underneath it."*
