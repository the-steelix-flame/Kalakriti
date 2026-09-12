# Kalakriti — phased build plan against the Business Model Addendum

This plan turns `Kalakriti_Business_Model_Addendum.md` into working software. It was
written after reading every relevant backend and app file, not from memory — each
claim below about what exists was checked against the code on **11 September 2026**.

Two rules govern every phase:

1. **Real, not mocked — except the four things the addendum itself says must stay
   mocked.** Amazon Seller Central, Flipkart Seller Hub, GeM registration and ONDC
   Network Participant registration legally require a GST number and PAN nobody on
   this team holds. Those four report `not_configured` and name the exact account
   that has to exist first, exactly like the four marketplace adapters already do
   today. Everything else — OTP, Razorpay, WhatsApp Business API, the Provenance
   Passport, clusters, GRNs, settlement, reviews — is real code against a real API,
   using sandbox or test credentials where the addendum names that path (§5).
2. **Nothing is done until it works from a sideloaded APK, on a real phone, with the
   laptop shut.** A screen that only works in `npx expo start --web` is not built.
   Every phase below ends with a real-device check, not a browser check.

---

## Part A — what the addendum asks for that already exists

Read this before assigning any phase below — do not rebuild these.

| Capability | State | Where |
|---|---|---|
| OTP auth: random code, salted-hash, expiring, rate-limited, real session revoke | **Real code**, not mocked. Only blocked by missing SMS credentials — see Phase 0. | `backend/auth.py`, `backend/sms.py` |
| Camera capture, on-device enhancement, quality gates | Real, on-device, no account needed | `app/src/vision/`, `backend/imaging.py` |
| Voice capture → bilingual AI copy, HSN autofill | Real, against a live NVIDIA key | `backend/vision.py`, `backend/pipeline.py` |
| Dynamic pricing (cost floor + market comps) | Real computation, not an LLM guess | `backend/main.py` `/v1/price` |
| Razorpay order creation + webhook | **Real.** A genuine order is created against the Razorpay API when keys are present, and the webhook verifies HMAC-SHA256 before marking anything paid. | `backend/main.py:862-932, 995-1053` |
| UPI intent fallback | Real — a `upi://pay` deep link needs no API at all | `backend/main.py:914-922` |
| Marketplace publish adapters (storefront, ONDC, GeM, Amazon, Shopify) | Real HTTP calls behind a credential gate, `not_configured` when the account doesn't exist. This already matches the addendum's **Model A** (§3): one master seller account, credentials set once, every artisan's listing pushed into it. | `backend/channels.py` |
| Per-channel seller readiness, with the reason for every field | Real, computed from the actual profile | `backend/seller.py` |
| Provenance Passport — signing | Real Ed25519 signature over a real content hash | `backend/main.py:1117-1133` |
| Bulk/custom order enquiries | Real form, real rows. This is what replaced the invented "Samuh" screen the addendum's own §1 table still credits — see Part B. | `backend/db.py:619-628` |
| Offline-first drafts, sync, per-field conflict resolution | Real | `app/src/lib/{drafts,sync,store}` |

None of this needs rebuilding. Where a phase below touches one of these files, it is
extending it, not replacing it.

---

## Part B — what the addendum asks for that does not exist yet

This is the honest gap, stated once so it isn't repeated in every phase. The
addendum's own strategy text (§1's crosswalk table) credits two things to the build
that are not in the code:

- **"Samuh cluster orders"** is listed as covering "connect directly with B2B
  buyers." The actual Samuh screen was removed for showing a fabricated
  fourteen-member consortium — see the comment at `backend/db.py:619-628`. What
  exists today is the enquiry form, which is real but is not cluster coordination.
- **"Digital Literacy Index"** is listed against the income-uplift impact goal.
  There is no such metric anywhere in the code — no column, no endpoint, no screen.

Everything from addendum §3 and §9–§14 — the entire cooperative/commission
operating model — is **zero percent built**. Concretely, none of these exist in the
schema or the app today:

- A role distinction between an artisan and a GST-holding Cluster Creator (§10.2)
- A `Cluster` entity, invite/join mechanism, or per-cluster commission percentage
- A Goods Receipt Note (§10.3)
- Net-settlement math: platform fee, GST-by-HSN, logistics deduction before a split (§9)
- Razorpay Route linked accounts, or the manual-UPI fallback for artisans with no PAN
- A review system for GST holders / Cluster Creators (§10.1)
- Capacity locking across simultaneous cluster memberships (§11's double-booking case)
- A WhatsApp Business Catalog channel (§3, §7) — not even a stub or an env var
- A Flipkart Samarth adapter (§3) — only Amazon Karigar exists among the invite-only programmes
- A public, login-free Provenance Passport verification page (§6) — the signature is
  minted correctly but never stored, and the QR code encodes a bare id string, not a
  URL, so scanning it today opens nothing
- A GST-rate-by-HSN reference table (§9 step 5, point 2) — HSN is an AI-suggested
  string with no rate attached to it anywhere
- Per-user sandbox credentials in Profile (§5) — every credential today is a single
  server-wide environment variable, not something an individual artisan or Cluster
  Creator connects themselves

Because the cluster/settlement system does not exist, **none of the fifteen edge
cases in addendum §11 can currently be tested** — there is nothing there to exercise
yet. Phase 11 below turns that whole table into real test scripts once the system
exists to run them against.

---

## Ordering and ownership

Phases 1–4 are sequential — each depends on the schema and endpoints the previous
phase adds. Phases 5–10 can run in parallel once Phase 4 lands, except where noted.
Module tags reuse the M1–M5 owners from `Kalakriti_Team_Modules.pdf`; this body of
work sits mostly outside any one module's existing file list, so it is tagged with
whichever owner's domain it extends, and the newest, most cross-cutting phases (4, 9,
10) are natural candidates for the team lead to run directly or pair on.

Phase 0 is not new work — it is the same blockers from
`Kalakriti_Status_and_Direction.pdf` section 3, restated here because every phase
after it is untestable on a real phone without it. It was deliberately reduced to
the minimum that makes a real phone able to reach the backend; the permanent
deployment and real SMS delivery are **Phase 12**, late on purpose, so the schema
under the hosted database has stopped moving before anything is migrated onto it.

---

## Phase 0 — make the app reachable, so any of this can be tested on a real phone

Nothing below can be verified by more than one person on one laptop until this is
done. If it is already complete by the time this phase starts, skip straight to
Phase 1.

**Do**
- Set an SMS provider (`MSG91_AUTH_KEY` + `MSG91_TEMPLATE_ID`, or Twilio) so OTP
  delivers to a real phone. `OTP_DEV_ECHO=true` only unblocks local development.
- Move the database to hosted Postgres (`DATABASE_URL`), object storage for images
  (`MEDIA_S3_*`), and the backend to a public HTTPS host (`PUBLIC_BASE_URL`).
- Set `JWT_SECRET` and `VIEW_SALT` so a redeploy does not silently log everyone out.
- Fix the release APK: restrict to `arm64-v8a`, add
  `android.enableMinifyInReleaseBuilds` and `enableShrinkResourcesInReleaseBuilds` to
  `android/gradle.properties`.

**Scope note.** The *permanent* host, hosted Postgres, object storage and real SMS
delivery are no longer this phase's job — they moved to **Phase 12**, so the
cooperative model can be built against a schema that is still changing without
re-deploying after every migration. Phase 0 now only has to get the app talking to a
backend it can reach from a real phone, which a disposable tunnel does in seconds.

**Done when:** the app on a real phone, on mobile data, with no cable and no shared
Wi-Fi, reads and writes against the backend over HTTPS — and a login completes, by
whatever OTP path is available at the time.

**Progress as of 11 September 2026** — `preflight.py` went from 1 failure and 6
warnings to 1 failure and 5 warnings:

| Item | State |
|---|---|
| Public HTTPS host | **Done for testing.** A Cloudflare quick tunnel serves the backend at a real `trycloudflare.com` hostname with a Google Trust Services certificate. The app reaches it over mobile data with no cable and no shared Wi-Fi, and `PUBLIC_BASE_URL` now passes. Disposable: the hostname changes when the tunnel restarts, so a permanent host is still needed. |
| `JWT_SECRET` | **Done.** Was set but only 25 characters, short enough to attack a signing key. Rotated to 86. |
| `VIEW_SALT` | **Done.** Set to 64 characters, so view counts no longer reset when the signing key rotates. |
| APK size | **Mostly done.** Building `arm64-v8a` alone took the release APK from 113 MB to 43 MB. The minify and shrink flags are still absent from `gradle.properties`, so there is more to take off. |
| SMS OTP | **Still the one blocker.** MSG91 is configured and authenticates, but the wallet is empty on every route, and MSG91 answers an empty-wallet send with `type: success` and a request id while delivering nothing. `preflight.py` now detects this explicitly rather than reporting the provider as configured. Needs either MSG91 credits or a `FAST2SMS_API_KEY`. |
| Hosted Postgres, object storage | Not started. Still a SQLite file and local disk. |

---

## Phase 1 — schema for the cooperative model

Backend only. No UI yet. Everything after this phase reads and writes these tables.

**New tables (`backend/db.py`)**

| Table | Purpose |
|---|---|
| `clusters` | One row per Cluster Creator's coordination group: id, owning `artisan_id` (must hold `gstin`), craft category, commission percentage, max order capacity, created_at |
| `cluster_memberships` | `artisan_id` × `cluster_id`, join date, status (active / left), per-member production capacity |
| `goods_receipts` | id, cluster_id, artisan_id, order_id, quantity, quality_status (pass/reject), logged_by, logged_at — the GRN from §10.3 |
| `settlements` | id, order_id, cluster_id, gross_amount, platform_fee, gst_amount, logistics_fee, net_amount, commission_amount, computed_at, status |
| `settlement_lines` | settlement_id, artisan_id, units, amount, payout_method (route / manual_upi), payout_status |
| `reviews` | id, cluster_id, reviewer_artisan_id, settlement_id (proves a completed cycle), criteria (JSON: paid_on_time, commission_fair, orders_regular), created_at |
| `hsn_gst_rates` | hsn_code, gst_rate, effective_from — a real static reference table from published GST slabs (nil / 5% / 12% / 18%), not invented per item |

**Schema changes**
- `Artisan.role`: enum `artisan | solo_seller | cluster_creator`, default `artisan`.
  The default matters: `artisan` is the only role that requires no paperwork, so a
  new signup is never blocked on documents they do not have.
- `Artisan.capacity_units` and `Artisan.capacity_committed`: one pair of numbers per
  person, which is what addendum §11's double-booking case decrements.

  **Correction to an earlier draft of this plan.** This section previously called
  for `Artisan.cluster_id` as "the real membership link". That is wrong and was not
  built: a single foreign key allows exactly one cluster per artisan, which
  contradicts edge cases 1 and 2, where an artisan belongs to two clusters at once
  and the app has to stop the same weeks of work being promised twice. Membership is
  many-to-many through `cluster_memberships`, and capacity is guarded on the artisan
  rather than per membership — one pair of hands, one pair of numbers.

**Migration**
- Extend `backend/migrate_to_postgres.py` with the new tables, in dependency order.
- **A column migration is also required, and this is the part that is easy to
  miss.** `Base.metadata.create_all()` creates missing *tables* and never alters an
  existing one, so the three new columns on `Artisan` do not appear in any database
  that already had an `artisans` table. Nothing warns you: every table exists, the
  app imports cleanly, and the failure arrives later as a 500 from the first screen
  that reads the new column. `backend/migrate_schema.py` diffs the models against the
  live database and adds what is missing; `db.warn_if_schema_behind()` logs the gap at
  startup naming that command. Every later phase that adds a column needs the same
  one-line run.

**Done when:** the backend imports cleanly, every new table is created on both SQLite
and Postgres, the unique constraints reject a duplicate membership and a second review
on one settlement cycle, an unknown HSN code reads back as unknown rather than zero,
and the existing rows survive the migration with their new columns backfilled.

---

## Phase 2 — role split, cluster creation, and joining

**Backend (`backend/main.py`, new `backend/clusters.py`)**
- `POST /v1/clusters` — a `cluster_creator`-role artisan with a `gstin` creates a
  cluster: commission %, craft category, capacity. Reject if the caller has no GSTIN,
  with the same plain-language `why` pattern `seller.py` already uses.
- `GET /v1/clusters` — browse, filterable by craft category and location, returning
  commission %, connected platforms (from that creator's `MarketplaceAccount` rows),
  review rating (or `"New on platform, no reviews yet"` per §10.1's cold-start rule),
  and past payout averages if any settlements exist.
- `POST /v1/clusters/{id}/join` — by invite code or by browsing; writes a
  `cluster_memberships` row and sets `Artisan.cluster_id`.
- `POST /v1/clusters/{id}/leave`.
- Capacity lock: when a bulk enquiry is accepted against a member's capacity, an
  endpoint decrements `Artisan.capacity_units` **once**, regardless of which cluster
  proposed the order — addresses §11's "same artisan, two clusters, same category"
  case directly.

**App**
- A signup-time branch, framed exactly as §10.2 suggests: *"Are you managing just
  your own shop, or do you also want to coordinate a cluster of other artisans?"*
  Solo Seller sees nothing new. Cluster Creator unlocks a Samuh-style dashboard tab.
  This choice is changeable later from Profile, per §10.2.
- A **cluster browse screen** for non-GST artisans: cards showing commission %,
  connected platforms, star rating (or the cold-start string), craft category.
  Terms visible **before** joining, never after — the addendum is explicit that this
  is the trust-building move.
- A **cluster dashboard** for Cluster Creators: member list, invite by phone/QR,
  commission slider, capacity setting.

**Done when:** two real phones, two real accounts — one creates a cluster with a
real GSTIN in the profile, the other joins it by QR code, and both see the
commission percentage and connected platforms agree on both screens.

**Progress as of 11 September 2026 — backend complete, app built, device check
outstanding.**

*Correction to this phase as written above.* It said join "writes a
`cluster_memberships` row and sets `Artisan.cluster_id`". There is no
`Artisan.cluster_id` — Phase 1 removed it deliberately, because one foreign key
allows one cluster per artisan and contradicts edge cases 1 and 2. Membership is
many-to-many through `cluster_memberships` alone, and that is what was built.

| Piece | State |
|---|---|
| `backend/clusters.py` | Done. Create, browse, join by id or invite code, leave, the role switch, and the capacity guard. Invite codes use an alphabet with no look-alike characters, because the realistic path is a field officer reading one down a phone line. |
| Routes in `main.py` | Done. Eleven endpoints. Refusals carry a plain-language `why` the app shows as-is, and use 409 for state conflicts against 400 for bad input, because the app renders those differently. |
| `backend/test_clusters.py` | 29 checks, all passing, through the real HTTP layer. Covers §11 rows 1, 2, 3, 10, 11 and 14 by assertion rather than description. |
| `backend/test_clusters_live.py` | Same flow against the public HTTPS host the phone uses. Catches what the in-process test cannot — a signing key that differs between processes, which it did on the first run. |
| `Clusters.tsx`, `ClusterDashboard.tsx` | Done, typechecked, in the installed APK. Terms, commission, platforms and rating all sit above the Join button, never behind it. |
| Role switch in Profile | Done. All three roles, both directions, server-side refusals shown in the artisan's own language. |
| 72 interface strings | Done, English and Hindi authored. The other seven need `translate_ui.py`. |
| **Device check** | **Done on one device, 12 September 2026.** Signed in as the seeded Cluster Creator by password, against Supabase over HTTPS on mobile data. The role branch showed "My cluster" rather than the browse screen, the dashboard rendered the invite code as a QR and as readable letters, and the member row showed the weaver's live remaining capacity. The two-phone half of the "Done when" still needs OTP, so it waits for Phase 12. |

**A design note worth keeping.** `connected_platforms()` originally ignored its
arguments and returned server-wide channel availability. That would have told a
joining member their work reaches GeM when the cluster owner has no PAN on file.
A channel is live for a cluster only when the server holds the credentials **and**
the owner passes `seller.readiness` for it, and the reason it is not names the
missing field.

**Edge cases covered:** §11 rows — two clusters same category double-booking,
different commission percentages as a visible market signal, new cluster cold-start,
Solo Seller → Cluster Creator toggle, terms visible before joining.

---

## Phase 3 — Goods Receipt Notes

**Backend**
- `POST /v1/clusters/{id}/grn` — quantity, quality_status, artisan_id, against a
  specific bulk order. Only the Cluster Creator (or a designated dispatch point they
  name) can log one.
- `GET /v1/clusters/{id}/grn?order_id=` — running tally per artisan against the
  order's committed quantity.

**App**
- A GRN logging screen for Cluster Creators: pick the artisan, enter quantity
  received, mark pass/reject, one tap per delivery. Icon-forward, matching the
  app's accessibility pattern, since dispatch points are not always literate either.

**Done when:** a Cluster Creator logs three separate deliveries against one bulk
order on a real phone, and the running tally is correct including one rejected unit.

**Progress as of 11 September 2026 — backend and app complete, device check pending.**

| Piece | State |
|---|---|
| `backend/grn.py` | Done. Log a receipt, void one, list them, and a tally computed from rows every time rather than held in a drifting column. |
| Routes | Done. Three endpoints. Only the cluster owner may log a receipt — a member writing their own would be writing their own payslip, and that is a 403, not a validation error. |
| `backend/test_grn.py` | 20 checks, all passing. Covers the spec's three-deliveries-with-a-reject case and §11 rows 5 and 6. |
| `GoodsReceipts.tsx` | Done, typechecked. Member picked from a list rather than typed, and the accepted count updates live as the numbers change — it is the one thing the artisan standing there wants to know. |
| 35 interface strings | Done, English and Hindi authored. |
| **Device check** | **Done, 12 September 2026.** Recorded a delivery of 8 with 2 rejected on the phone. The live "6 good / 2 not good" card computed as the numbers were typed, the tally moved from 12/11/1 to 20/17/3, the form cleared, and the row is in Supabase. Capacity released correctly, 22 free to 30. |

**Two decisions worth keeping.** Rejected units are a separate field from received,
not a pass/fail status: "twenty came, two cracked" is the normal case, and a toggle
would force whoever logs it to round that to a lie in one direction. And capacity is
freed by the *total* delivered, rejects included, because the weeks of work were
genuinely spent — replacing a rejected batch is new work needing a new commitment,
which makes the cost of a reject visible instead of hiding it in a number that never
moves.

**Edge cases covered:** §11 — late or short delivery recalculates against actual
GRN quantities, not committed ones; a rejected batch reduces that artisan's payout
and the shortfall is visible as either absorbed capacity or a reduced fulfilment.

---

## Phase 4 — net settlement and payout splitting

The addendum is explicit (§9) that the split happens on the **net realised amount**,
not the buyer's gross payment. This phase is the arithmetic and the money movement.

**Backend (new `backend/settlement.py`)**
1. **Platform fee.** Pull the actual net-settlement figure from each channel's own
   API or settlement report where that is exposed. Where a channel's API does not
   expose it — this will be most of them, initially — the app must say so with the
   same `{value: null, available: false, why: "..."}` shape `analytics.py` already
   uses everywhere else, and fall back to letting the Cluster Creator enter the
   figure manually from the platform's own dashboard, logged and auditable rather
   than guessed.
2. **GST.** Look up `hsn_gst_rates` by the listing's HSN code from Phase 1. Never
   hardcode a zero — most handloom and handicraft HSN headings are nil or 5%, but
   this is category-dependent and the table is the single source of truth, the same
   one already driving HSN autofill in `pipeline.py`.
3. **Logistics fee.** Read whatever the platform's settlement report itemises;
   `logistics.py` already tracks courier charges for the storefront channel and is
   the natural place to extend this.
4. **Net amount** = gross − platform fee − GST − logistics fee.
5. **Split** = commission % of net to the Cluster Creator, remainder divided across
   `goods_receipts` rows by units actually received and passing quality — not by
   units originally committed.
6. **Payout.**
   - Razorpay Route: create a linked account per payee (bank account + PAN, no GST
     needed, per §9) and trigger the split transfer once a settlement is computed.
     Real API calls against Razorpay's sandbox, testable with no GST.
   - No PAN: the amount is pre-computed and queued for the Cluster Creator to
     approve and send by manual UPI — "review and approve," not manual arithmetic.
7. **Documents.** Auto-generate one real GST tax invoice (Cluster Creator → buyer)
   and one internal settlement slip per artisan (units × rate, after all three
   deductions, minus commission). Slips are what feeds Phase 10's income metric.

**App**
- Fair-Floor pricing screen updated to show gross list price and net expected payout
  side by side, deductions itemised — the addendum calls this out by name in §9 as
  the thing that keeps the existing pricing promise honest once a cluster is involved.
- A settlement history screen per artisan: slip list, running income total.
- A Cluster Creator payout screen: pending Route transfers, pending manual UPI
  approvals, generated invoices.

**Done when:** a real settled order — even a ₹1 test transaction in Razorpay
sandbox — produces a correct net amount, a correct split across two GRN-logged
artisans with different quantities, one of them paid by a live Route sandbox
transfer and the other queued for manual UPI, and both settlement slips readable in
the app on a real phone.

**Progress as of 12 September 2026 — the arithmetic is done and tested; money
movement and documents are not.**

| Piece | State |
|---|---|
| `backend/settlement.py` | Done. The three deductions, net realised amount, commission on net, and the split across goods receipts. `explain()` returns the deductions as an ordered list because the order is the argument — somebody should be able to follow the money down the page and reach their own number. |
| Routes | Done. Compute, list, and a per-listing take-home estimate. A member reading the list sees only their own line; another artisan's payout and the cluster commission are not theirs to read. |
| `backend/test_settlement.py` | 21 checks, all passing, against Supabase. Covers §11 rows 5 and 8. |
| Razorpay Route transfers | **Not built.** Lines are computed and marked `route` or `manual_upi`, and nothing has been sent. |
| GST invoice and settlement slips | **Not built.** |
| App screens | **Not built.** The Fair-Floor screen does not yet show take-home beside the sticker price, though the endpoint behind it works. |

**What the tests actually pin down**, since this is the part that decides whether
somebody is paid fairly:

- Commission is taken from the **net**, not the sticker price. On a ₹20,000 order
  with ₹1,000 GST and ₹500 shipping, a 10% commission is ₹1,850 and not ₹2,000.
- The split follows **units accepted**. An artisan who delivered 15 with 3 rejected
  is paid for 12, and the other artisan's share is untouched by those rejects.
- The lines sum to the distributable amount **exactly**; the last line absorbs the
  rounding remainder, because a settlement two paise short is one somebody has to
  explain.
- An unknown marketplace fee, an unknown shipping cost, or an HSN with no published
  rate each stop the settlement in `needs_input` and name what is missing. None of
  them defaults to zero.

**ONDC, end to end, 12 September 2026.** Asked for separately and built alongside
this phase, because a settlement needs an order and until now no ONDC order could
ever arrive.

The gap was structural, not a missing credential. ONDC is not a marketplace with an
API we call - it is the Beckn protocol, and on it we are a **BPP**, a Seller
Platform. Buyer apps call *us*. `channels.ondc()` pushed an unsolicited `on_search`
at the gateway, which is how a catalogue gets indexed, and then nothing happened,
because every step after that is a buyer app POSTing to endpoints that did not
exist.

`backend/ondc.py` plus six endpoints in `main.py` are those endpoints: `/search`,
`/select`, `/init`, `/confirm`, `/status`, `/cancel`, and the registry's
`/ondc-site-verification.html`. Each ACKs immediately and posts the substantive
answer back to the buyer app's own `bap_uri`, which is what Beckn requires.

| Question | Answer |
|---|---|
| How does our price reach the marketplace | `Listing.price`, rendered into the Beckn catalogue in `on_search`, restated as a quote in `on_select`, restated again in `on_confirm`. All three read the same row, so a price edited in the app is the price ONDC quotes next search. There is no second catalogue to keep in step. |
| How does an order reach the app | `/confirm` is the only call in the protocol that writes an `Order` row. From that moment it is an ordinary order: it appears in the Orders tab, counts toward earnings, and Phase 4 settles it like any other. |

`backend/test_ondc.py` runs a real buyer app against it - a local HTTP server plays
the `bap_uri` so the asynchronous callbacks land somewhere real, which is the half a
request/response test would skip and the half where `transaction_id` mismatches
hide. 21 checks, all passing. Stock decrements once, a retried confirm is one order
rather than two, an oversell is refused with a Beckn NACK before the buyer pays, and
a cancel returns the pieces to stock.

**A test that was wrong before the code was.** The first draft asserted that HSN
5007 handloom was nil-rated and that the net was ₹19,500. The table says 5%, so the
code was right and the test was wrong. That is precisely the instinct
`gst_rates.py` exists to stop — most of these headings *feel* like they should be
nil-rated — and it is worth recording that it caught its author.

---

## Phase 4b — our own marketplace, because the others need a GST number

Added 12 September 2026, ahead of the prototype demo, and it is a scope decision
rather than new scope. Amazon, Flipkart, GeM and ONDC all require a GST number and
PAN before a seller account can exist. The integrations for all four are written and
correct; none of them can be live for a demo. What was always available is the one
channel nobody has to approve.

| Piece | State |
|---|---|
| `backend/market.py` | Done. A browsable shop over every published listing, with search and categories, server-rendered so it opens on anything. |
| `GET /market`, `GET /v1/market` | Done. The same shop as a page and as JSON, the second for a buyer view inside the app. |
| Real checkout | Done. Razorpay Checkout on the product page, with the test-card hint shown in the page. |
| `POST /v1/orders/{id}/payment` | Done. Verifies the payment without waiting for a webhook. |
| `backend/test_market.py` | 13 checks, all passing, three of them attacks. |

**The payment is not mocked, and does not need to be.** Razorpay test mode is a real
API, real order objects, real signatures, with money that does not exist. That is
strictly better than a hand-rolled fake for a demo: the same code path runs in
production and only the key changes, so the demo proves something. A mock would have
to be torn out later and would prove nothing.

**Why the confirmation endpoint exists.** A webhook needs a public URL Razorpay can
reach, and the quick-tunnel hostname changes on every restart. The buyer who stays on
the page can be confirmed there and then, from the signed response their own checkout
returned. The webhook stays as the backstop for the buyer who pays and closes the
browser. Both routes converge on the same status change and both are idempotent.

**Two checks, because either alone has a hole.** The signature proves the message
came from Razorpay; it does not prove the payment was captured or that the amount
matches. A buyer authorising ₹1 against a ₹2,000 order produces a perfectly valid
signature, so the payment is also fetched from Razorpay and the amount compared. The
test asserts exactly that case.

---

## Phase 5 — reviews

**Backend**
- `POST /v1/clusters/{id}/reviews` — only accepted when the caller has a
  `settlements` row under that cluster with `status = paid`, and only one review per
  settlement cycle, both enforced server-side per §10.1, not just in the UI.
- Review payload is structured, not freeform text: `paid_on_time`,
  `commission_felt_fair`, `orders_came_regularly`, each a small icon-rated scale.
- `GET /v1/clusters/{id}` includes the aggregate star rating and count, or the
  cold-start string when zero reviews exist.

**App**
- A review prompt surfaced after a settlement slip is marked paid — icon-plus-voice
  pattern, matching every other input surface in the app, not a text box.

**Done when:** a review can only be left after a real settlement exists for that
relationship, a second attempt on the same cycle is rejected, and a brand-new
cluster shows the cold-start string rather than zero stars.

**Done on the backend, 12 September 2026.** `clusters.add_review`,
`review_eligibility` and `reviews_for`, plus two endpoints.
`backend/test_reviews.py` is 13 checks, all passing.

The constraint is the feature, and it is enforced against the database rather than
in a screen: a review needs a `settlements` row under that cluster with status
`paid` **and** a line paying the reviewer. A settlement that is computed but unpaid
does not unlock one. That is §11.9's "friends leaving five stars" case refused
structurally - there is no row to hang the review on until money has moved.

Three smaller decisions worth keeping: an empty review is refused, because it would
still move the count; one review is reported as *too few to average* rather than as
a rating; and a reviewer is shown by first name only, since a cluster is often a
handful of people in one town and a named two-star review is an accusation with an
address attached.

**Not done:** the app-side review prompt after a slip is marked paid.

---

## Phase 6 — Provenance Passport, made actually verifiable

The crypto already works. This phase makes it reachable.

**Backend**
- Add `passport_id`, `passport_signature`, `passport_public_key` columns to
  `Listing`, written when `/v1/passport` is called so a passport survives past the
  single response that minted it.
- `GET /passport/{id}` — a public, unauthenticated HTML page (same pattern as
  `/l/{lid}`): raw photo next to the enhanced one, the itemised AI-operation log,
  and a signature-validity check computed server-side against the stored public key.
  No login, works in any phone browser, exactly as §6 specifies.
- Publish the public key openly (e.g. at `/passport/{id}/pubkey`), since the
  addendum's whole argument for using an open standard is that third-party tools can
  verify independently of this app.

**App**
- Fix the QR code in `Create.tsx` to encode the full `PUBLIC_BASE_URL`-based
  verification URL, not the bare `passport.id` string it encodes today — currently
  scanning it opens nothing.

**Done when:** scanning the QR code on a real product, on a real phone, in a plain
camera app with no Kalakriti app installed, opens a working page showing both
photos and a valid signature check.

**Progress as of 12 September 2026** — the code is done; the host is the remaining
gap.

| Item | State |
|---|---|
| Passport stored on the listing | **Done.** `raw_url`, `passport_id`, `passport_payload`, `passport_signature`, `passport_public_key` and `passport_at` are on `Listing` and migrated into Supabase and the local SQLite file. The signed bytes are kept verbatim, because re-serialising the claims would reorder a key and report a valid signature as forged. |
| `GET /passport/{id}` | **Done.** `backend/passport.py` renders it; `main.py` holds the three-line route, the same split `market.py` uses. Public, no token, no JavaScript: raw photo beside the enhanced one, every operation in order, and the signature check. |
| `GET /passport/{id}/pubkey` | **Done.** Publishes the public key, the signature and the exact signed payload, plus the five steps to verify them. A stranger's Ed25519 library was run against it and agreed. |
| Raw photograph kept | **Done.** The pipeline stores `<listing>_raw.jpg` alongside the enhanced image. Listings photographed before this change have no original, and the page says so rather than showing the enhanced one twice. |
| Minted on every path | **Done, and moved.** Minting happens in `pipeline.analyse_into`, so auto mode, manual mode and the background-job path all get one. The job path had no client to ask for a passport and so could never have had one. |
| Claims are true | **Done.** The maker, GI tag and place now come off the artisan's own row. The app used to send `artisanId: 'ART-UP-VNS-4471'` and a Varanasi GI tag for every photograph taken anywhere in India, which put three false statements inside a signature whose only purpose is that its statements are true. Unknown fields are signed as null. |
| QR code | **Done.** It encodes `verifyUrl`, not the bare id. Scanning used to open nothing at all. |
| Tests | **Done.** `backend/test_passport.py`, 15 checks, all passing, including that editing one claim by a single character makes the page say "could NOT be verified" instead of rendering the new claim. |
| **Scanned from a real phone** | **Blocked on the host, not the code.** The QR encodes `PUBLIC_BASE_URL`, which is still a Cloudflare quick tunnel: the hostname dies when the tunnel restarts, so a QR printed on a product stops resolving. Images are also still on the container filesystem, and the passport page is the one page whose whole argument collapses when a photograph 404s. Both are **Phase 12**: a permanent host and object storage. |

---

## Phase 7 — WhatsApp Business Catalog channel

Matches §3 and §7 exactly: one catalog on one business number, buyer-initiated only,
no broadcast.

**Paperwork (start immediately, in parallel with everything above)**
- Register a WhatsApp Business Cloud API test number through Meta for Developers —
  free, no GST required, per addendum §4's sandbox bucket.

**Backend (new `backend/whatsapp.py`)**
- Real calls to the Cloud API's Product Catalog endpoints: create/update a catalog
  item per published listing, tagged internally by `artisan_id` the same way the
  existing four adapters tag by seller.
- A sixth adapter in `channels.py`, `not_configured` until the test number and token
  exist, following the exact pattern `_missing()` / `_result()` already establish.
- A `seller.py` requirements entry for WhatsApp (likely just `phone_verified`).
- State plainly, in the adapter's own docstring the way `channels.py` already
  documents every other adapter's limits: the catalog is invisible until a buyer
  messages first, so this is a fallback channel, not a discovery channel.

**App**
- WhatsApp appears in the channel picker once configured, with the same "why this
  needs X" readiness pattern the other four channels use.

**Done when:** a product published from a real phone appears in a real WhatsApp
catalog, and a message sent to that test number from a different phone can browse it
and place an order that lands in the app's Orders tab.

---

## Phase 8 — Flipkart Samarth channel

Structurally identical to Amazon Karigar. Per addendum §3, there is no public API
for auto-onboarding — the realistic path is an NGO or cluster applying to the
programme, or connecting to an account that already exists inside it.

**Paperwork**
- A Cluster Creator or the project applies to Flipkart Samarth on behalf of the
  cluster — this is the account-opening step from `Kalakriti_Status_and_Direction.pdf`
  section 6, extended to a fifth marketplace.

**Backend**
- A `flipkart` adapter in `channels.py`, same shape as `amazon()`: credential-gated,
  real HTTP calls to Flipkart's Seller API once a seller account exists,
  `not_configured` with the exact missing variables until then.
- A `seller.py` requirements entry.

**Done when:** `preflight.py` reports Flipkart alongside the other four channels
with the same honesty — either a real publish, or a named list of missing
credentials. This phase is code-complete even while the account approval is
pending, exactly like ONDC and GeM are today.

---

## Phase 9 — per-user sandbox credentials in Profile

Implements addendum §5's three-tier framing at the individual level, so a Cluster
Creator can prove their own Razorpay or WhatsApp integration works without waiting
for the whole platform's credentials.

**Backend**
- New encrypted-at-rest columns (or a small `sandbox_credentials` table keyed by
  `artisan_id` + provider), storing a pasted Razorpay test key or WhatsApp test
  number/token per user.
- Payment and WhatsApp calls check for a per-user sandbox credential first, falling
  back to the platform-wide environment variable if none is set — additive, not a
  replacement for Phase 0/7's global configuration.

**App**
- A "Connect a real account" section in Profile: paste a Razorpay test key, or a
  WhatsApp test number and token. Stored with `expo-secure-store`, per the
  addendum's own recommendation and consistent with how the session token is
  already kept (`docs/ARCHITECTURE.md` §2).

**Done when:** a teammate pastes their own personal Razorpay sandbox key into their
own profile on their own phone, and a test order routes through it — proving the
integration without anyone sharing a shared secret.

---

## Phase 10 — Digital Literacy Index

The addendum's own crosswalk table (§1) already claims this exists; it does not.
This phase makes the claim true, built from signals the system already logs once
Phase 4 lands.

**Backend**
- A computed score per artisan from real signals already present: voice-mode
  (Bolo Mode) usage ratio vs manual typing, listing completion rate, days active,
  and — once Phase 4 exists — income delta read from real settlement slips. No
  invented weighting presented as precise; show the underlying numbers alongside
  the index, the same transparency the app already applies to every other metric.
- `GET /v1/insights` (already exists in `analytics.py`) gains a `literacyIndex`
  block, following the existing `{value, available, why}` honesty shape when a
  signal genuinely has too little history to compute.

**App**
- A small trend on Home or Profile — a number and a short "based on" line, not a
  decorative badge with no explanation behind it.

**Done when:** the index changes visibly for a real test account after a week of
real usage, and the numbers behind it are inspectable, not just a single opaque
score.

---

## Phase 11 — the full edge-case matrix, for real

Addendum §11 lists fifteen edge cases. None of them can be tested today because the
underlying system does not exist. Once Phases 1–5 are done, run every row as a
literal script on real devices — not a unit test, a person doing the thing on a
phone — and record pass/fail.

| # | Edge case | Verified by |
|---|---|---|
| 1 | Artisan joins two clusters, different craft categories | Both memberships visible, no conflict, Phase 2 |
| 2 | Same artisan, two clusters, same category, both want capacity | Capacity decrements once regardless of which cluster asked first, Phase 2 |
| 3 | Two clusters offer different commission % | Both visible before joining, artisan free to choose, Phase 2 |
| 4 | Cluster Creator leaves mid-order | Order freezes, buyer payment refunds if incomplete, members notified, Phase 4 |
| 5 | Artisans deliver late or short | Settlement recalculates off actual GRN quantities, Phase 3+4 |
| 6 | Some units fail quality check | Payout reduces for that artisan, shortfall flagged, Phase 3+4 |
| 7 | Non-GST artisan wants no cluster | Capture/voice/price tools work standalone; Publish stays disabled without a cluster, Phase 2 |
| 8 | Artisan has no PAN | Route split unavailable, falls back to manual UPI with pre-computed amount, Phase 4 |
| 9 | Fake five-star review gaming | Rejected — one review per settled cycle, enforced server-side, Phase 5 |
| 10 | New cluster, zero history | "New on platform, no reviews yet" shown, never 0 stars, Phase 2+5 |
| 11 | Solo Seller → Cluster Creator later | Togglable in Profile, short setup wizard, Phase 2 |
| 12 | Buyer verifies a Passport with no app installed | Public page opens with no login, Phase 6 |
| 13 | Bulk order ships from multiple addresses | Multiple pickup/business addresses already supported by `Address`, verify against a multi-location cluster, Phase 2+3 |
| 14 | Member wants cluster terms before joining | Commission % and connected platforms shown pre-join, never hidden, Phase 2 |
| 15 | Amazon/Shopify metric a platform won't expose | Already correctly handled today — `{available:false, why}` pattern, verify it still holds once settlement pulls from the same APIs, Phase 4 |

**Done when:** every row above has a dated pass, recorded by whoever ran it, on a
real phone. The hosted backend arrives in Phase 12, so rows run before that point
run against the tunnel — which is fine for every row here except where a row is
about hosting itself.

---

## Phase 12 — the real deployment, and OTP that genuinely reaches a stranger

Everything before this ran against a laptop behind a disposable tunnel. That was the
right trade while the cooperative model was being built — a tunnel costs nothing and
takes thirty seconds, and none of Phases 1–11 are *about* hosting. This phase is
where the backend stops being a laptop.

It sits second-to-last on purpose. Deploying early means re-deploying after every
schema change in Phases 1–4, and paying for a hosted database while the tables under
it are still moving. Deploying here means one migration, against a schema that has
stopped changing.

**The database**
- Hosted Postgres — Neon's free tier scales to zero and suits a demo that idles.
- `python migrate_to_postgres.py` carries existing rows across in dependency order,
  including every table from Phase 1, and `setval`s the sequences afterwards.
- `python migrate_schema.py` against the hosted database, because `create_all` adds
  missing tables and never missing columns.

**Object storage**
- Cloudflare R2 (`MEDIA_S3_*` and `MEDIA_PUBLIC_BASE`). This is not optional once
  hosted: container filesystems are replaced on deploy, and a marketplace will
  already have published the image URL. The listing stays live with a dead
  photograph, and nothing raises an error.

**The host**
- Render from `render.yaml`, on `starter` — not free. The free tier gives 512 MB and
  `onnxruntime` plus the matting weights plus OCR need roughly 700 MB resident, so a
  free instance is OOM-killed on the first photograph and the symptom is a restart
  loop rather than an error message.
- `PUBLIC_BASE_URL` set by hand to the `https://` address Render shows, scheme
  included. Every listing URL, image path and webhook target is built from it.

**Real OTP, which is the point of this phase**
- An SMS provider with **credits actually on it**. The trap is already documented and
  `preflight.py` now catches it: MSG91 answers a send from an empty wallet with
  `type: success` and a request id, delivers nothing, and the app tells the artisan a
  code is on its way. MSG91's own signup codes still arrive on your handset, which
  makes an empty wallet look like a working integration.
- MSG91 additionally needs a DLT-approved template, which takes days and cannot be
  compressed — so whoever owns this starts the paperwork at Phase 2, not here.
  Fast2SMS is the fallback with free trial credit and no DLT template of your own.
- `OTP_DEV_ECHO` **not set**. If it is set, the code comes back in the API response,
  and that is a login bypass for anyone who can call the endpoint.

**The app**
- Rebuild the APK against the permanent `https://` host, with the private-range HTTP
  fallbacks removed from `EXPO_PUBLIC_API_URL` entirely. At that point
  `plugins/withLocalNetworkAccess.js` can come out of `app.json`, and the app will
  refuse cleartext to anything, anywhere — which is the posture it should ship in.
- Add the minify and shrink flags to `android/gradle.properties` if Phase 0 left them
  undone.

**Done when:** `python preflight.py --production` exits zero, and somebody whose
phone has never touched this laptop installs the APK, receives a real SMS, signs in,
joins a cluster, and opens a storefront link — with every laptop in the room shut.

---

## Phase 13 — close the loop

- Correct `README.md` and the addendum's own §1 crosswalk table: Samuh is enquiries,
  not cluster coordination, until Phase 2 actually ships it; Digital Literacy Index
  is real only after Phase 10.
- Re-run every phase's "Done when" check one more time, back to back, on one phone,
  with the laptop shut, backend hosted, from a fresh install of the APK.
- Update `docs/ARCHITECTURE.md` with the new tables from Phase 1 and the settlement
  flow from Phase 4, the same way it currently documents storage and sync.

**Done when:** a person who has never seen this project can install the APK, sign
in with a real OTP, join a cluster, and watch a settled order pay out — with nobody
touching a laptop during the demo.
