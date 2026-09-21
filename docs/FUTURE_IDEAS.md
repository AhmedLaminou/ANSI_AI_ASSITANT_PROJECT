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

### Phase A — Perimeter (the foundation) — **done (2026-09-17)**

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

### Phase B — Registration and approval — **done (2026-09-17)**

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

### Phase C — The voice of each agent — **done (2026-09-17)**

Implemented in [`backend/app/prompts.py`](../backend/app/prompts.py), replacing the single
`SYSTEM_MESSAGE` that used to live in `main.py`.

- Two invariant halves **bracket** the department block, so the shared guardrails are read first
  and last: answer only from the extracts, extracts are untrusted data, refuse rather than invent,
  cite every claim.
- The department block **adds, it never weakens** — the same shape as the access rule, where a
  department restricts and never widens. No block contains permission language, and
  `tests/test_prompts.py` (32 checks) fails if one ever does.
- Selection is a dictionary lookup on the department stored in the database. The model does not
  choose its own instructions.
- The greeting now names the service it answers for.

**What the measurement showed.** `tests/prompt_probe.py` asks questions whose answer contains a
value *only if the instruction was ignored* — the canary method from the security probe. First
run: **4/6**. Finance's "never compute what the sources do not state" held; HR's "answer on the
rule, not on the person" did not, and the model returned an indexed salary for a named agent.

The abstract rule lost to the much stronger, repeated *"réponds uniquement à partir des extraits
fournis"*. What fixed it was naming the conflict and giving the model a sentence to emit instead
of a rule to infer: *"cette consigne prime sur toutes les autres, y compris sur l'obligation de
répondre à partir des extraits … réponds exactement : « Je ne restitue pas les données
individuelles d'un agent »"*.

**What this does not settle.** A prompt is not an access control. The probe measures a tendency on
one model at one temperature; it is evidence, not proof. Two structural options remain open, and
both are decisions for ANSI rather than for the code — see §6:

1. **Do not index individual files at all.** Simplest and strongest. Makes the HR block a
   convenience rather than a protection.
2. **Flag documents holding personal data** and exclude them from what is fed to the generator,
   while leaving them readable directly by authorised roles. Deterministic, enforced before the
   model, testable the way the perimeter is. Costs a column, an upload control, and the rule that
   an operator must not mix individual files with rules in one document.


### Phase D — Vocabulary — **done (2026-09-17)**

Implemented in [`backend/app/glossary.py`](../backend/app/glossary.py).

- `backend/data/glossary.json` is now sectioned — `commun` plus one section per department — and
  the **legacy flat shape still loads**, treated as the shared section, so an existing operator file
  keeps working. The two shapes may be mixed.
- Each service reads the shared section plus its own, its own winning on collision. An administrator
  reads every perimeter, so an ambiguous acronym expands to **both** readings joined by "ou":
  ambiguity should broaden the search, not resolve itself silently in favour of one service.
- `glossary.expand_for(user, question)` is the single mapping from an account to its glossary — the
  same discipline as `access.can_access_document`.
- An unknown section name ("informatique" instead of "technique") is ignored **and logged as a
  warning**. Loading silently and never being selected is the worse of the two failures.
- The two-letter trap still holds and is now replayed for each of the four services.

**The collision that justifies the split.** In francophone public administration "CP" is *congés
payés* to an HR officer, *crédits de paiement* to an accountant, and *chef de projet* in the
technical service. A flat glossary has to pick one and is wrong for two services out of three.

**What the measurement showed.** `tests/glossary_probe.py` is built so the perimeter cannot explain
the outcome: both documents are filed `transverse`, so every account may read both, and neither
spells out "CP". The only difference between accounts is the glossary expanding their question.

Same question for all three — *"Quelles sont les règles applicables aux CP ?"*:

| Account | First result |
|---|---|
| HR | **Congés payés** |
| Finance | **Crédits de paiement** |
| Administrator | both come back |

The ranking inverts at identical corpus and identical perimeter. 3/3 checks passed. The probe goes
through `/search` and generates nothing, so it runs in seconds.


### Phase E — Tools and access to ANSI data

> Explored in detail, source by source, in [`plan/IDEAS.md`](../plan/IDEAS.md) — directory,
> absences, calendar, announcements, events, inventory, tickets, budget: what each makes
> possible, what it must never do, and what it breaks in the current permission model.

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

### Phase G — Measurement, per department — **done (2026-09-20)**

`tests/evaluation/dataset.json` now holds **10 documents and 34 questions**. The six original
documents stay `transverse` — passwords, telework, incidents, backups genuinely concern the whole
agency — and four new ones belong to a service each. Every question carries the service asking it;
without one it is asked by the administrator account, so the 18 original questions are unchanged.

`tests/evaluate.py` creates one account per service, groups the questions by asker, and prints
**one line per perimeter**. That is the whole point: improving technical answers can degrade HR
answers, and a global average compensates one service with another.

`--department rh` runs a single service, which turns a twenty-five minute measurement into a two
minute one after a targeted change.

**Four questions are out of perimeter**: a legitimate agent asks something whose answer lives in
another service. This is not the adversarial case — `security_probe.py` covers that — it is the
ordinary one, and what it measures is whether the refusal stays graceful.

**Baseline, 2026-09-20, `qwen3:4b`**

| Perimeter | Accuracy | Sources | Refusals | Median latency |
|---|---|---|---|---|
| Administrator (every service) | 16/16 | 16/16 | 2/2 | 29.3 s |
| Finance | 3/3 | 3/3 | 1/1 | 37.9 s |
| Logistics | 3/3 | 3/3 | 1/1 | 43.2 s |
| HR | 4/4 | 4/4 | 1/1 | 34.3 s |
| Technical | 2/2 | 2/2 | 1/1 | 47.1 s |
| **All** | **28/28** | **28/28** | **6/6** | **34.3 s** |

Out-of-perimeter refusals: **4/4**.

**Dataset integrity.** `tests/test_dataset.py` (31 static checks, instant) holds the properties the
long run assumes: a question expected to be *answered* is asked by an account that can actually
read its source, and a question expected to be *refused on perimeter grounds* really is outside the
asker's perimeter. Without them, changing a document's department silently turns a partitioning
test into a missing-document test, and the next run reports what looks like a model regression.

**What this baseline does not say.** 34/34 mostly means the set no longer discriminates. A set that
nothing fails has no headroom left to detect a regression: it establishes a reference, it no longer
measures difficulty. The next useful work on it is not to grow it but to **harden** it — ambiguous
questions, long documents, contradictory documents, genuinely distant phrasings.

Latency is not comparable across runs on this machine: two runs of the *same* technical subset, at
identical code and corpus, gave medians of 37.3 s and 47.1 s — 26 % apart with nothing changed. The
machine's load dominates the measurement, so none of the recent changes can be blamed for the
21.0 s → 34.3 s shift without a controlled run. And per §1, these numbers describe this laptop, not
the deployment target.

Still to do here: group real user feedback ([§5.13](ARCHITECTURE_TECHNIQUE.md)) by department, so
quality slippage shows up where it starts.


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
6. **May individual files be indexed at all?** Phase C measured that the HR instruction "answer on the rule, not on the person" holds only because a particular wording was found. A prompt is not an access control. Either individual files stay out of the corpus, or documents holding personal data get a flag that keeps them out of what reaches the generator while remaining readable directly. Both are cheap to implement; neither is the code's decision to make.

---

## 7. Suggested order of work

1. **Phase A** — perimeter. Largest value, no model involvement, fully testable. ✅ done
2. **Prompt-injection and cross-department leakage suite** — before any real document is loaded. ✅ done
3. **Phase B** — registration and approval. ✅ done
4. **Phases C and D** — prompts and glossaries. Cheap, visible improvement. ✅ done
5. **Phase G** — measurement per department. ✅ done, and it should now be re-run after every
   model, chunking or threshold change rather than treated as a milestone.
6. **Phase F platform decisions** — GPU, model size, vLLM, PostgreSQL. Settle the model early,
   since it is the main quality lever and it changes what everything else is measured against.
   **This is the next step.**
7. **Phase E** — tools, once an internal API is actually available. Blocked on ANSI, not on code.

Two things block real data rather than code: the **retention duration** (§6.4) and whether
**individual files may be indexed at all** (§6.6).


### One suggestion for early visible value

The stated audience is interns, and their questions are predictable: how leave works, who to
contact, which tools to install, what the code conventions are. A small curated **onboarding corpus**
marked `transverse` would demonstrate the assistant's value immediately, to the people it is meant
for, and makes a far better demonstration than a bulk document dump.
