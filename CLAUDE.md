# ANSI Local AI Assistant — project context

Working language: **English** in this file and in conversation. The application
itself, its documentation in `docs/`, and everything an ANSI agent sees stay in **French**.

## What this is

An **offline** document assistant for ANSI — Agence Nationale pour la Société de l'Information,
Niger. ANSI builds software and digital services for the State; it is **not** a cybersecurity
agency. The assistant answers staff and interns' questions from imported internal documents,
citing its sources, without any data leaving the infrastructure.

Internship project, at **proof-of-concept** stage. Not a deployed product.

## The one absolute constraint: nothing leaves the machine

This is the project's reason to exist. Reject any proposal that violates it, even a technically
simpler one:

- **Never** an external AI API (OpenAI, Anthropic, Gemini, OpenRouter…).
- **Never** a cloud service for embeddings, retrieval or OCR.
- **Never** a CDN required at runtime.
- Inference goes through local Ollama only (`127.0.0.1:11434`).

Check **licences** before adding a dependency: PyMuPDF was rejected in favour of `pypdfium2`
because AGPL would be a problem for an ANSI deployment (§24 of the design document).

## Commands

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload   # API (voir note ci-dessous)
.\.venv\Scripts\python.exe -m pytest tests/test_units.py -q  # fast, no Ollama needed
.\.venv\Scripts\python.exe -m tests.smoke_rag                # end to end, slow, needs Ollama
.\.venv\Scripts\python.exe -m tests.evaluate                 # quality: accuracy, sources, refusals, latency
.\.venv\Scripts\python.exe -m app.create_admin               # create an administrator
```

From `frontend/`: `npm run dev` for development — **never on a server**.

**Always invoke tools as `python -m <tool>`, never the `.exe` shim in `Scripts/`.**
Smart App Control is enforced on this machine and blocks pip-generated launchers
(`uvicorn.exe`, `pytest.exe`...) as unsigned: *« Une stratégie de contrôle d'application a
bloqué ce fichier »*. `python.exe` is signed and trusted, so the module form always works.
Do not suggest disabling Smart App Control — turning it off is irreversible without a
Windows reinstall, and it is a reasonable protection to keep.

Optional PostgreSQL + pgvector: `docker start ansi-pgvector`, then set `DATABASE_URL`.

## Known traps (measured, not assumed)

- **This laptop is slow; the target is not.** A trivial question takes ~21 s here and a full answer
  ~40 s, ~90% of it generation, on a CPU sharing 16 GB with the IDE and browser. Before concluding
  "the model is unavailable", check free RAM and `CHAT_TIMEOUT_SECONDS`. **Do not treat these numbers
  as a design constraint**: deployment is on ANSI servers with a GPU, where a larger model becomes
  the main quality lever. Measurements describe this machine, not the target.
- **`qwen3:4b` emits its reasoning inside `content`** despite `think: false`, terminated by
  `</think>`. `extract_answer()` strips it and the stream hides it. Do not "simplify" that code.
- **The `0.18` similarity threshold separates nothing.** Measured: a question with no answer scored
  0.354 while a legitimate one scored 0.344. The **system prompt** is what refuses, not the
  threshold. Do not claim otherwise in documentation.
- **ANSI is the *Agence Nationale pour la Société de l'Information*** — it builds software and
  digital services for the State. It is **not** a cybersecurity agency; do not describe it as one.
- **`classification` is a label, not an enforcement.** Only `allowed_roles` restricts access. A
  document marked "confidentiel" with all roles allowed is readable by everyone.
- **Tests share the development database.** They must assert on their own data and never assume an
  empty database.
- **In development Vite drifts ports** (5173 → 5174…), so CORS accepts any localhost origin outside
  production. In production exactly one origin is allowed.
- **Tesseract language files** live in `backend/data/tessdata/` (not versioned): an artefact to carry
  to the offline environment, like the model weights.
- **Two-letter acronyms collide with French words** ("SI" vs "si"), so below three characters the
  glossary requires actual capitals.

## Never commit

`.env`, `credentials.txt`, `backend/data/` (database, imported documents, tessdata), `node_modules`,
`frontend/dist`, `.venv`. Check before every commit.

## Demonstration data

Use **non-sensitive or anonymised documents only**. The retention policy is still unsettled
(`CONVERSATION_RETENTION_DAYS=0` keeps history indefinitely): until that is decided, no real data
should be imported.

A demo is only as convincing as its corpus. Personal notes make the assistant look pointless;
procedures, notes de service and règlements make its value obvious.

## Project documentation

- `README.md` — status, getting started, features
- `docs/A_PROPOS_DU_PROJET.md` — what it does, how it differs from a generic ChatGPT
- `docs/ARCHITECTURE_TECHNIQUE.md` — pipeline, measurements, debts, roadmap
- `architecture_agent_ia_offline_ANSI.md` — original design document (the reference)

When a measurement is taken (latency, quality, scores), **record it in
`docs/ARCHITECTURE_TECHNIQUE.md`** with its date rather than leaving it in a conversation.

## How to work here

- Run read, test, build and analysis commands freely — no need to ask.
- **Ask before anything destructive or irreversible**: deleting files or documents, `git push`,
  resetting a database, purging history, stopping services you did not start, installing system
  software.
- Verify any interface change in a browser before calling it done.
- Prefer measurement over assertion: this project has an evaluation harness — use it.
- Report results that contradict a design assumption rather than dressing them up.
