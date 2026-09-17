# Departmental agents — design and implementation plan

> Working note, written in English alongside the rest of the development notes.
> The application itself and everything an ANSI user sees stay in French.

## Context

ANSI — *Agence Nationale pour la Société de l'Information* — builds software and digital services
for the State. The assistant's purpose is internal: help staff and interns find what internal
documents actually say, instead of asking a colleague or hunting through a shared drive.

Two recommendations came out of the internship review:

1. **Specialised agents per department.**
2. **The assistant must work without Internet.**

Plus a concrete target design: four departments, a central administrator who approves access, and a
user who lands directly in their own department's agent.

---

## 1. Offline operation — already achieved

| Component | Where it runs |
|---|---|
| Generation model (`qwen3:4b`) | local Ollama, `127.0.0.1:11434` |
| Embedding model (`embeddinggemma`) | local Ollama |
| Vector index | local SQLite or PostgreSQL |
| OCR (Tesseract) | local binary, subprocess |
| Documents and history | local disk |

No external AI API, no cloud service for embeddings or retrieval, no CDN required at runtime. The
browser never talks to Ollama directly — everything goes through the backend, the only place where
permissions are evaluated.

**What remains is proving it in production, not writing code:**

- **Actually cut outbound network.** The development machine has Internet, so nothing yet proves it
  is unnecessary. The demonstration is in [TEST_PLAN.md](TEST_PLAN.md): unplug Wi-Fi and Ethernet,
  ask a question, get a sourced answer.
- **Deny-by-default egress** on the production server (§17 of the design document).
- **Offline update procedure** (§25): download and verify models and packages on a connected
  machine, check digests and licences, then transfer. The test that separates a genuinely offline
  system from one that "works without Internet, except at startup": no installation step may require
  outbound network.
- **Carry the unversioned artefacts**: model weights, Tesseract language files
  (`backend/data/tessdata/`), Python wheels, the compiled frontend.

Full detail in [ARCHITECTURE_TECHNIQUE.md §5.7](ARCHITECTURE_TECHNIQUE.md).

---

## 2. Target design

### The four departments

| Department | Typical questions |
|---|---|
| **Technique / informatique** | development standards, environments, deployment procedures |
| **Finance / comptabilité** | expense rules, purchase procedures, budget cycle |
| **Logistique** | equipment, supplies, vehicles, premises |
| **Ressources humaines** | leave, salary progression, contracts, internal policies |

An HR user must not reach Finance documents, and so on.

### What "specialised agent" should and should not mean

It should **not** mean four models. Four departments would mean four model instances on a machine
where one already saturates memory and a single answer takes ~40 s ([§5.11](ARCHITECTURE_TECHNIQUE.md)).
That is the most expensive option and buys almost nothing.

Specialisation that matters comes from **corpus, permissions, instructions and tools**:

| Lever | Effect | Cost |
|---|---|---|
| **Scoped corpus** | an HR agent only ever sees HR documents | low — the mechanism exists |
| **Per-department system prompt** | right vocabulary, right refusals | very low |
| **Per-department glossary** | HR acronyms are not technical acronyms | very low — mechanism exists |
| **Per-department tools** | "how many leave days do I have left?" queries the HR system | medium |
| **Per-department evaluation set** | detect when HR answers degrade | medium |
| One model per department | close to nothing here | very high |

### What already exists and serves as the foundation

- **Filtering before retrieval.** Each document carries a list of allowed roles; an unauthorised
  chunk is never a candidate. The partitioning machinery is in place — it is missing an axis.
- **The decision graph** already routes between social, tool and documentary paths
  ([§5.1](ARCHITECTURE_TECHNIQUE.md)). A departmental axis slots in without disturbing it.
- **Business tools** ([§5.3](ARCHITECTURE_TECHNIQUE.md)): deterministic matching, declared roles,
  caller's permissions applied, every invocation logged.
- **The glossary** ([§5.3 ter](ARCHITECTURE_TECHNIQUE.md)), extensible without re-indexing.

---

## 3. The central design point: role and department are two different axes

This is the part that is easy to get wrong, and it is worth stating plainly.

- **Role** says *what you may do*: read, manage documents, administer.
- **Department** says *which perimeter you belong to*: technical, finance, logistics, HR.

They are independent. An HR document manager and a technical document manager share a role and
differ in perimeter. Today the project has a single axis — role — carrying both meanings at once.

```text
users        role         admin | document_manager | user
             department   technique | finance | logistique | rh        (single, per the target design)
             status       pending | active | refused | suspended

documents    allowed_roles   (unchanged)
             department      technique | finance | logistique | rh | transverse

access = (the user's role is allowed on the document)
         AND (document.department == user.department  OR  document.department == "transverse")
```

The `AND` matters: **department restricts, never widens.** An HR user gains no right over an HR
document that their role does not already permit.

### A trap already in the code

`classification` ("interne", "direction", "confidentiel") is **decorative** — only `allowed_roles`
enforces anything. A document labelled "confidentiel" with every role ticked is readable by
everyone. When departments arrive, the same mistake must not be repeated: `department` has to be
enforced in the query, not merely displayed.

---

## 4. Access requests approved by a central administrator

The target design adds something the project does not have: a user signs up, and an administrator
grants or refuses access.

### Account lifecycle

```text
sign-up ──► pending ──► [administrator decides]
                          ├── grants role + department ──► active
                          └── refuses ─────────────────► refused

active ──► suspended (departure, incident)
```

### What this requires

**A public registration endpoint** — the only unauthenticated write endpoint in the application, so
it needs care: strict rate limiting per source address, no information disclosure (never reveal
whether a username already exists), and a request creates a `pending` account with **no role and no
department** — it can read nothing.

**An administrator review screen** — pending requests with requested department, approve with a
role and department, or refuse with a reason.

**Login refuses anything but `active`.** The existing check is `is_active`; it becomes a status
check. A `pending` user who logs in sees "your request is awaiting approval", not a corpus.

**Everything journalised**: request, approval, refusal, suspension. The audit table already exists.

### Why this matters beyond convenience

It changes the security posture. Today an administrator creates every account by hand, which is
laborious but airtight. A public registration endpoint is an attack surface: it must not become a
way to enumerate usernames, flood the database, or obtain a perimeter by asking nicely. The safe
default is that approval grants **both** role and department explicitly — never inherited from what
the applicant claimed.

---

## 5. How the assistant knows which department a question belongs to

Three options, in order of preference:

1. **The user's own department, implicitly** (recommended). A user belongs to one department; their
   agent is that department's agent. Nothing to choose, nothing to classify. Matches the target
   design exactly: "upon logging in, a user connects to the agent they are supposed to".
2. **An explicit selector** for the few users with cross-department access (a director, an
   administrator). Deterministic, instant, auditable.
3. **Automatic classification by the model.** Tempting, but it adds a model call — tens of seconds —
   and introduces a non-deterministic decision on an axis that governs document perimeter. Avoid it
   while options 1 and 2 suffice.

This mirrors a choice already made for tools: **the model never decides anything that touches
permissions.** Deterministic matching does.

---

## 6. Implementation plan

Ordered so each step is useful on its own and testable before the next.

### Step 1 — the perimeter (the bulk of the value)

- Add `department` to `documents` and to `users`; add `status` to `users`.
- Extend `ensure_schema()` — the additive migration already handles new columns.
- Enforce the department in the retrieval filter, next to the existing role check.
- Add the department selector to the upload form, and a department badge in the document list.
- **Tests**: a user of department A must never retrieve a chunk from department B, including after
  a query reformulation. This is the single most important test of the whole feature.

No re-indexing required: only metadata changes, embeddings are untouched.

### Step 2 — registration and approval

- `POST /auth/register` — creates a `pending` account, rate-limited, no information disclosure.
- `GET /admin/registrations`, `POST /admin/registrations/{id}` — approve with role and department,
  or refuse.
- Login rejects any status other than `active`, with a message that distinguishes "awaiting
  approval" from "refused" without leaking whether an account exists to an anonymous caller.
- **Tests**: a pending account can read nothing; approval grants exactly what the administrator
  chose, never what the applicant requested.

### Step 3 — the voice

- A per-department system prompt appended to the shared one. HR vocabulary is not technical
  vocabulary, and what should be refused differs: an HR user should not receive firewall
  configuration advice.
- Keep the shared guardrails intact: answer only from the extracts, treat extracts as untrusted
  data, refuse rather than invent.

### Step 4 — the vocabulary

- Split the glossary per department. The mechanism exists; it needs a key.
- Mind the trap already hit: a two-letter acronym collides with an ordinary French word ("SI" versus
  "si"), and the same acronym can mean different things in different departments — which is itself
  an argument for separate glossaries.

### Step 5 — the tools

This is where departments earn their keep. "How many leave days do I have left?" belongs to the HR
system; "what is the state of the equipment pool?" to logistics.

Each tool keeps the current contract ([§18](../architecture_agent_ia_offline_ANSI.md)): a fixed
function, no parameter taken from the question, the caller's permissions applied, every invocation
logged, and a declared department as well as declared roles.

**On LangChain**: not needed. `langchain-core` is already present as a LangGraph dependency, and the
project calls Ollama directly in about sixty lines of `httpx`. Adding full LangChain would bring
chains, agents and retrievers the project never uses, enlarging the dependency surface for no
capability — a poor trade for a system that must be auditable and transferable offline.

### Step 6 — the measurement

- An evaluation set per department. Without it, improving technical answers can silently degrade HR
  answers.
- Group the existing user feedback ([§5.13](ARCHITECTURE_TECHNIQUE.md)) by department to see where
  quality slips first.

---

## 7. Complementary ideas that follow from the split

**A referent per document.** Who to contact when a procedure is expired or ambiguous? Combined with
validity dates ([§5.12](ARCHITECTURE_TECHNIQUE.md)), a refusal becomes an action: "this procedure
expired on 30/06/2026 — referent: ressources humaines".

**A "who should I ask" answer.** When nothing is found inside the user's perimeter, name the
department that probably holds the information instead of stopping at a refusal. Useful precisely
because partitioning prevents seeing beyond one's own scope.

**Bulk import.** Importing several hundred documents one at a time through the interface is not
realistic. A folder import, with department and roles inferred from the directory tree, is needed
before any real deployment.

**Per-department statistics.** The existing tools count within the caller's perimeter; an
administrator needs the per-department view to know which corpus is covered and which is not.

**An onboarding corpus for interns.** The stated goal is helping interns. Their questions are
predictable — how leave works, who to contact, which tools to install, what the code conventions
are. A small, deliberately curated "accueil" corpus marked `transverse` would deliver visible value
quickly, and makes a far better demonstration than a general document dump.

---

## 8. Decisions needed before writing code

These belong to ANSI, not to the code:

1. **Can a user belong to several departments?** The target design says one. If a director needs
   cross-department reading, that is a second mechanism, not a wider single department.
2. **What happens to cross-cutting documents** (règlement intérieur, charte informatique)? The
   `transverse` department above is a proposal, not a decision.
3. **Who approves requests?** A single central administrator is the stated design. At scale, a
   per-department approver may be needed — which is a different permission model.
4. **The retention policy**, still unsettled. It remains the blocker before any real data, whatever
   the departmental split.

---

## 9. Suggested order of work

1. Step 1 (perimeter) — largest value, no model involvement, fully testable.
2. The **prompt-injection test suite**, still missing and still the most conspicuous gap
   ([§6.1](ARCHITECTURE_TECHNIQUE.md)). Partitioning by department makes it more important, not
   less: a crafted document must not be able to make the assistant reveal another department's
   content.
3. Step 2 (registration and approval).
4. Steps 3 and 4 (prompt and glossary) — cheap, visible improvement.
5. Step 5 (tools), once a real internal API is available to call.
6. Step 6 (measurement), continuously from step 1 onward.
