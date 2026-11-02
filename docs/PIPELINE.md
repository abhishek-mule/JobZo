# Human-Approved Action Pipeline

JobZo's pipeline turns a large, noisy job feed into a small number of
**evidence-backed decisions**, then executes only what you explicitly approve.

It is local-first. Jobs, evidence, approvals, drafts, and follow-ups live in
SQLite. Email is written to a local outbox unless you configure SMTP.

```
discover → filter → score → approval queue → execute → follow up → learn
```

The optimization target is **interviews per hour of your time**, not
applications submitted.

---

## The one rule

**Nothing consequential happens before an `ApprovalRequest` is approved.**

That includes:

- submitting an application
- sending a cold email
- sending a follow-up email

A rejected or expired request never executes. A failed execution leaves the
request in `failed` with the error attached — it does not pretend to succeed.

### Approval state machine

```
pending ──► approved ──► executed
   │            └──────► failed
   ├──────────► rejected
   └──────────► expired
```

Every request stores the exact preview you saw (`payload_json`) and the
evidence behind it (`evidence_json`): opportunity factors, trust signals,
fresher classification, contact verification, and eligibility.

Requests are idempotent. Re-queuing the same application returns the existing
request instead of creating a duplicate.

---

## Daily workflow

```bash
# 1. Queue the best current opportunities and show the 15-minute brief.
jobzo brief --queue

# 2. Review each card. Approve / reject / edit / skip.
jobzo inbox

# 3. See what was filtered out and why.
jobzo brief

# 4. Add a verified recruiter contact (never guessed).
jobzo contact-add \
  --company Acme \
  --name "Jane Doe" \
  --role "Talent Acquisition" \
  --email jane@acme.com \
  --source-url https://acme.com/careers \
  --evidence-kind official_careers_page

# 5. Draft a personalized email. This only queues an approval.
jobzo cold-email <application-id>

# 6. Approve and send (or write a local .eml).
jobzo inbox
# or explicitly:
jobzo email-send <draft-id> --approve

# 7. Work the follow-up schedule.
jobzo followups --draft
```

Useful supporting commands:

```bash
jobzo queue --limit 5 --fresh   # queue only, no brief
jobzo contacts Acme             # contact evidence and verification levels
jobzo contact-discover Acme --url https://acme.com/careers
jobzo drafts --status pending_approval
jobzo approve <request-id>      # approve then execute immediately
jobzo reject <request-id> --note "not a fit"
```

---

## Stage 1 — Opportunistic filtering

Jobs are rejected before they reach your queue when there is hard evidence
against them:

- experienced-only role for a fresher profile
- suspicious/scam signals (fees, unrealistic pay, impersonation, etc.)
- role family outside the target profile
- inactive or expired posting
- location incompatible with the profile
- failed hard eligibility rules

Soft concerns become **warnings**, not silent rejections. Expired, fake, or
irrelevant jobs never consume approval time.

The brief reports how many jobs were auto-rejected so the filtering is visible,
not hidden.

---

## Stage 2 — Opportunity score

The score is an auditable composition of existing JobZo signals, not a second
matching engine.

| Factor | Weight |
|---|---:|
| Skills match | 25 |
| Fresher eligibility | 20 |
| Experience compatibility | 15 |
| Employer/job authenticity | 15 |
| Role relevance | 10 |
| Application accessibility | 5 |
| Hiring freshness | 5 |
| Location compatibility | 5 |

Outputs:

- `total` (0–100) and classification (`excellent`, `strong`, `moderate`, `weak`)
- per-factor points, weights, and reasons
- hard rejects and warnings
- expected value, effort estimate, and value per minute

A hard reject forces the total to `0` and closes the request as rejected — it
cannot be approved into an application.

---

## Stage 3 — Contact verification

A valid email is not a recruiter. JobZo keeps four dimensions separate:

| Dimension | Question | Evidence |
|---|---|---|
| Identity | Is this person/team tied to the company? | official page, job posting, public profile, user confirmation |
| Domain | Does the email belong to the employer? | official company domain |
| Mailbox | Does the mailbox exist? | independent mailbox check |
| Hiring relevance | Is this person likely to hire for the role? | recruiter/talent/founder/hiring-manager role |

Resulting statuses:

- `verified` — identity + domain + relevance
- `verified_domain` — domain + relevance, identity not independently proven
- `verified_identity` — identity proven, no usable email
- `unverified` — insufficient evidence
- `risky` — free-mail address or deliverability/reputation risk

Rules:

- **Never guess an address** from a name pattern.
- **Never scrape LinkedIn.** A public profile URL may be stored as evidence.
- `contact-discover` only reads official company pages.
- Outreach requires a verified, hiring-relevant contact with an email.

---

## Stage 4 — Outreach safety

Drafting and sending are separate operations.

- Every draft creates a pending approval request.
- `send_draft` refuses unless the linked request is `approved`.
- One message per contact/job/type; dedupe key includes the follow-up.
- 72-hour minimum between messages to the same contact.
- Maximum 2 outbound messages per company per day.
- Editing a draft invalidates its prior approval and re-queues it.
- Free-mail and low-confidence contacts are rejected before drafting.

### Transports

Default: **local outbox**

```
logs/outbox/<timestamp>-<hash>.eml
```

The outbox is honest: it does **not** mark the draft as sent, does not create
an outbound interaction, and does not claim delivery. You can inspect the file
and send it yourself.

Real SMTP activates only when configured:

```bash
export JOBZO_SMTP_HOST=smtp.gmail.com
export JOBZO_SMTP_PORT=587
export JOBZO_SMTP_USER=you@gmail.com
export JOBZO_SMTP_PASSWORD="app-password"
export JOBZO_SMTP_FROM=you@gmail.com
export JOBZO_SMTP_SSL=0          # 1 for implicit TLS (usually port 465)
```

Use an app password, not your account password.

---

## Stage 5 — Follow-ups

Follow-ups are scheduled only for submitted applications, in business days
(weekends skipped):

| Day | Kind | Action |
|---:|---|---|
| 3 | `status_check` | Check the portal manually; no email |
| 6 | `recruiter_followup` | Email only with a verified contact |
| 12 | `final_followup` | Polite close-the-loop email |

Rules:

- Terminal statuses (`rejected`, `offer`, `accepted`, `declined`, `interview`)
  cancel pending follow-ups.
- Without a verified contact, email follow-ups are skipped; the status check
  still happens.
- Scheduling is idempotent per application/kind.
- Applications submitted before this feature are backfilled automatically the
  next time you run `jobzo followups` or `jobzo brief`.
- Follow-up emails go through the same approval gate as cold email.

---

## Audit trail

| Table | Purpose |
|---|---|
| `approval_requests` | Preview, evidence, status, decision note, timestamps |
| `contact_evidence` | Source URL, excerpt, confidence per claim |
| `contact_verifications` | Separate identity/domain/mailbox/relevance flags |
| `outreach_drafts` | Exact subject/body, approval link, send result |
| `followups` | Schedule, status, linked contact and draft |

The original application/contact records are left intact; the pipeline adds
adjacent tables instead of rewriting their history.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `JOBZO_DB_PATH` | `~/.jobzo/jobzo.db` | SQLite database |
| `JOBZO_OUTBOX_DIR` | `logs/outbox` | Local `.eml` directory |
| `JOBZO_SMTP_*` | unset | Enables real SMTP sending |
| `JOBZO_SKIP_MIGRATIONS` | unset | Skip Alembic on startup (tests) |

Profile data comes from `resumes/master/profile.yaml`.

---

## Non-goals

- No automatic applications without approval.
- No bulk or spray-and-pray outreach.
- No guessed email addresses.
- No LinkedIn scraping.
- No claim that a local outbox file was delivered.
- No silent submission when an application form is not detected.
