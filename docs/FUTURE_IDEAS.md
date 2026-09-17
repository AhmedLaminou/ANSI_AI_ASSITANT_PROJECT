# Departmental agents — design and full implementation plan

> Working note in English, alongside the other development notes.
> The application and everything an ANSI user sees stay in French.

## Context and goal

ANSI — *Agence Nationale pour la Société de l'Information* — builds software and digital services
for the State. The assistant is internal: it should let staff and interns find what internal
documents actually say instead of asking a colleague or digging through a shared drive.

Concrete target:

- **Four departments**: technique/informatique, finance/comptabilité, logistique, ressources humaines.
- **One central administrator** approves or refuses every access request.
- **Each user has a role and a department**, and on login lands directly in their department's agent.
- **An HR user must not reach Finance documents**, and so on.
- Typical question: *"comment se passe une augmentation de salaire ?"* — an HR policy question,
  answered from the HR corpus with the document and page cited.

**Deployment target is ANSI servers, not a development laptop.** That distinction drives several
choices below.

---

## 1. Sizing: what changes on real servers

Measurements taken on the development laptop — ~21 s for a trivial question, ~40 s for a full
answer, 0.5 GB of free RAM — describe **that machine**, not the target. They should not constrain
the design. On a properly sized server:

| | Development laptop | ANSI server (target) |
|---|---|---|
| Inference | CPU, shared with IDE and browser | GPU |
| Latency per answer | ~40 s | seconds |
| Concurrent users | one, effectively | several, real |
| Viable model | `qwen3:4b` | 8B–32B class |
| Inference server | Ollama | Ollama, or **vLLM** for real concurrency (§14) |
| Database | SQLite | PostgreSQL + pgvector |

Three consequences worth planning for:

**A larger model becomes viable, and quality follows.** The measured ceiling today is extraction
ability, not retrieval: on the evaluation set both models scored **12/12 on sources** while `qwen3:4b`
scored 12/12 on answers and `qwen3:0.6b` only 2/12 ([§5.8](ARCHITECTURE_TECHNIQUE.md)). Retrieval is
not the bottleneck — the generation model is. A bigger model on a GPU is the single largest quality
lever available.

**Concurrency stops being theoretical.** Ollama serves requests sequentially; with real simultaneous
users that queues. This is where vLLM deserves the evaluation the design document already called for.

**PostgreSQL + pgvector stops being optional.** Already implemented and verified at parity
([§5.2](ARCHITECTURE_TECHNIQUE.md)); on a server with several writers, SQLite's single-writer lock is
disqualifying.

Sizing questions to answer with the infrastructure team: GPU model and VRAM, expected simultaneous
users, corpus volume per department, backup and retention capacity.

---

## 2. Why still not four models

I previously argued this partly from laptop memory limits. That argument was weak and I withdraw it.
The real reason stands on its own and has nothing to do with hardware:

**Four copies of the same model are not four specialised agents.** Loading `qwen3:14b` four times
yields four identical models. What makes an agent "HR" is the corpus it may read, the permissions
applied, the vocabulary it understands and the tools it may call — none of which live in the weights.

Genuinely different models per department would mean **fine-tuning** one per department: labelled
training data for each, a training pipeline, per-model evaluation, and re-training whenever
procedures change. That is a different project, an order of magnitude larger, and it would still not
solve access control — the part that actually matters here.

The defensible architecture is **one model, four agent configurations**:

| Lever | What it produces | Cost |
|---|---|---|
| Scoped corpus | an HR agent only ever sees HR documents | low — mechanism exists |
| Per-department system prompt | right vocabulary, right refusals | very low |
| Per-department glossary | HR acronyms ≠ technical acronyms | very low — mechanism exists |
| Per-department tools | "how many leave days left?" queries the HR system | medium |
| Per-department evaluation | detect when HR answers degrade | medium |
| A separate model per department | nothing that the above does not already give | very high |

---

## 3. The central design point: role and department are separate axes

- **Role** = *what you may do*: read, manage documents, administer.
- **Department** = *which perimeter you belong to*: technique, finance, logistique, RH.

They are independent. An HR document manager and a technical document manager share a role and
differ in perimeter. The project today has one axis — role — carrying both meanings.

```text
users        role         admin | document_manager | user
             department   technique | finance | logistique | rh
             status       pending | active | refused | suspended

documents    allowed_roles   (unchanged)
             department      technique | finance | logistique | rh | transverse

access = (role allowed on the document)
         AND (document.department == user.department OR document.department == "transverse")
```

**Department restricts; it never widens.** An HR user gains no right over an HR document their role
does not already permit.

### A trap already in the code

`classification` ("interne", "direction", "confidentiel") is **decorative** — only `allowed_roles`
enforces anything. A document labelled "confidentiel" with every role ticked is readable by all.
`department` must not repeat that mistake: it has to be enforced **in the retrieval query**, not
merely displayed.

---

## 4. Implementation plan

Six phases. Each is useful alone and testable before the next.

### Phase A — Perimeter (the foundation)

**Schema** (`backend/app/database.py`)

- `documents.department` — string, indexed, default `"transverse"`.
- `users.department` — string, nullable (a pending account has none).
- `users.status` — `pending | active | refused | suspended`, default `active` for existing rows.
- Extend `ensure_schema()`; it already performs additive column migration on both engines.
- No re-indexing: metadata only, embeddings untouched.

**Enforcement** (`backend/app/main.py`)

- `can_access_document()` gains the department condition next to the role check.
- Every retrieval path must use it: `/chat`, `/chat/stream`, `/search`, `/documents`,
  `/documents/{id}/preview`, and the `visible_documents()` helper in `tools.py`.
- The graph's injected `retrieve` already applies ACL, so reformulation inherits the restriction —
  but that must be **tested**, not assumed.

**API**

- `POST /documents/upload` — accept `department`.
- `GET /documents` — filter by the caller's department; an optional `department` parameter for
  admins only.

**Interface** (`frontend/src/App.jsx`)

- Department selector in the upload form; department badge on document cards.
- The connected user's department shown in the sidebar, so the perimeter is never ambiguous.

**Tests — the most important of the whole feature**

- A user of department A never retrieves a chunk from department B, **including after a query
  reformulation**.
- A `transverse` document is reachable from every department.
- Department restricts but never widens: an HR user is still refused an HR document whose
  `allowed_roles` excludes their role.
- `tools.py` counts respect the department.

### Phase B — Registration and approval

**Schema**: reuse `users.status`; add `users.requested_department` and `users.request_reason`.

**Endpoints**

| Endpoint | Purpose | Auth |
|---|---|---|
| `POST /auth/register` | create a `pending` account | **none** |
| `GET /admin/registrations` | list pending requests | admin |
| `POST /admin/registrations/{id}/approve` | grant role + department | admin |
| `POST /admin/registrations/{id}/refuse` | refuse with a reason | admin |

**This is the application's first unauthenticated write endpoint**, so it needs care:

- Rate limit per source address — the sliding-window limiter already exists, reuse it.
- **No information disclosure**: the response is identical whether or not the username exists.
  Otherwise registration becomes a username enumeration oracle.
- A `pending` account has **no role and no department** — it can read nothing.
- Approval grants role and department **chosen by the administrator**, never inherited from what the
  applicant requested. `requested_department` is a hint, not an instruction.
- Login rejects any status but `active`, distinguishing "awaiting approval" from "refused" only to
  an authenticated-enough caller.
- Journalise request, approval, refusal, suspension — the audit table exists.

**Tests**: a pending account reads nothing; approval grants exactly what the admin chose; a refused
account cannot log in; registration does not reveal existing usernames.

### Phase C — The voice of each agent

- A per-department system prompt appended to the shared one (`SYSTEM_MESSAGE` in `main.py`).
- Keep every shared guardrail: answer only from the extracts, treat extracts as untrusted data,
  refuse rather than invent, cite sources.
- Per-department additions: vocabulary, tone, and what is out of scope — an HR user should not
  receive firewall configuration advice.
- The welcome screen already lists the queryable corpus; it should name the department too.

### Phase D — Vocabulary

- Split `backend/data/glossary.json` per department; keep a shared section.
- Mind the trap already hit: two-letter acronyms collide with ordinary French words ("SI" vs "si"),
  so below three characters the acronym must be capitalised. The same acronym may mean different
  things in different departments — itself an argument for separate glossaries.

### Phase E — Tools and access to ANSI data

This is where departments earn their keep, and where "agents" stop being document search.

| Department | Candidate tools | Source |
|---|---|---|
| RH | leave balance, grade and seniority, holiday calendar | SIRH |
| Finance | budget line status, expense-claim state | accounting system |
| Logistique | equipment inventory, stock, vehicle booking | inventory system |
| Technique | environment status, deployment history, on-call rota | internal APIs |

Each tool keeps the contract already established in `tools.py` and §18 of the design document:

- a fixed function, **no parameter taken from the question** — a crafted question cannot alter a query;
- declared roles **and** declared department;
- the caller's permissions applied inside the tool;
- every invocation journalised;
- **never free-form SQL and never shell access** for the model.

**Orchestration**: the LangGraph router already dispatches social / tool / documentary. Adding
departmental tools means registering them with a department and extending the deterministic matcher —
no architectural change.

**On LangChain**: not needed. `langchain-core` is already present as a LangGraph dependency; the
project calls Ollama directly in about sixty lines of `httpx`. Full LangChain would add chains,
agents and retrievers the project never uses, enlarging the dependency surface with no capability
gain — a poor trade for a system that must be auditable and transferable offline. LangGraph earns its
place because the flow genuinely branches; LangChain would not.

### Phase F — Production platform

- Compiled frontend served by nginx; **never `npm run dev` on a server**.
- HTTPS, `COOKIE_SECURE=true`, one explicit CORS origin.
- PostgreSQL + pgvector, backed up and restore-tested.
- Secrets injected by the system, not sitting in `.env`.
- GPU inference; evaluate vLLM against Ollama under real concurrency.
- Deny-by-default outbound firewall.
- Supervision: availability, latency, refusal rate, errors.
- **Offline update procedure** (§25): fetch and verify models and packages on a connected machine,
  check digests and licences, transfer. No install step may require outbound network.
- Carry the unversioned artefacts: model weights, Tesseract language files, wheels, built frontend.

### Phase G — Measurement, continuous from Phase A

- An evaluation set **per department**, otherwise improving technical answers can silently degrade HR
  answers.
- Group existing user feedback ([§5.13](ARCHITECTURE_TECHNIQUE.md)) by department to see where
  quality slips first.
- Re-run `tests.evaluate` after every model, chunking or threshold change.

---

## 5. Security work that grows with departments

**Prompt-injection tests — still missing, now more important.** Partitioning raises the stakes: a
crafted document placed in one department must not be able to make the assistant reveal another's
content. The defence exists (extracts declared untrusted in the system prompt); it has never been
tested ([§6.1](ARCHITECTURE_TECHNIQUE.md)).

**Cross-department leakage tests.** The systematic version of the Phase A test: for every pair of
departments, confirm no path — chat, stream, search, preview, tools, reformulation — returns the
other's content.

**Session invalidation.** Changing a user's department must take effect immediately. Authorisation
already reads role from the database on each request rather than from the token, so department should
follow the same rule — never trust a claim carried in the JWT.

**Retention policy.** Still unsettled, still the blocker before any real data, whatever the
departmental split.

---

## 6. Decisions that belong to ANSI, not to the code

1. **Can a user belong to several departments?** The target design says one. If a director needs
   cross-department reading, that is a second mechanism, not a wider single department.
2. **What is `transverse`?** Règlement intérieur, charte informatique, onboarding material — the
   proposal above, not a decision.
3. **Who approves?** One central administrator is the stated design; at scale a per-department
   approver is a different permission model.
4. **Retention duration**, and what must never be journalised.
5. **Which internal systems may be queried** by tools, with what credentials and what minimum rights.

---

## 7. Suggested order of work

1. **Phase A** — perimeter. Largest value, no model involvement, fully testable.
2. **Prompt-injection and cross-department leakage suite** — before any real document is loaded.
3. **Phase B** — registration and approval.
4. **Phases C and D** — prompts and glossaries. Cheap, visible improvement.
5. **Phase F platform decisions** — GPU, model size, vLLM, PostgreSQL. Settle the model early,
   since it is the main quality lever and it changes what everything else is measured against.
6. **Phase E** — tools, once an internal API is actually available.
7. **Phase G** — continuously from step 1.

### One suggestion for early visible value

The stated audience is interns, and their questions are predictable: how leave works, who to
contact, which tools to install, what the code conventions are. A small curated **onboarding corpus**
marked `transverse` would demonstrate the assistant's value immediately, to the people it is meant
for, and makes a far better demonstration than a bulk document dump.
