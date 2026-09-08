# How the data layer works

Written after an audit that found the app losing everything on restart. This records
what is stored where, what is real, and where the honest limits are.

---

## 1. The bug that started this

Onboarding reappeared every time the app was closed and removed from recents.

The cause was one file. `src/lib/session.ts` persisted values like this:

```ts
if (Platform.OS === 'web' && typeof localStorage !== 'undefined') { … }
return mem.get(k) ?? null;          // ← native fell through to an in-memory Map
```

On a phone, language, session token, onboarding flag and current draft lived in a
`Map` that died with the process. `@react-native-async-storage/async-storage` was not
even a dependency. It worked in a browser and had never once worked on a phone.

Anything that reads persisted state during the first render must therefore wait for
storage to be read back. `StoreProvider` does not render its children until
`session.hydrate()` resolves; without that gate the app briefly believes there is no
user and flashes the language picker at somebody who chose Gujarati weeks ago.

---

## 2. Where each thing lives

| Data | Where | Why there |
|---|---|---|
| Session token | **expo-secure-store** (Android Keystore / iOS Keychain) | It is a bearer credential. A rooted device or another app reading AsyncStorage's plain JSON must not come away with a working login. |
| Language, onboarding flag, device id, "hide insights" | AsyncStorage | Losing these is an inconvenience, not a breach. |
| Cached profile, product cards, orders, enquiries, insights | AsyncStorage, under `kk.cache.` | Volume far past what a keystore is built for. Each entry carries `fetchedAt`. |
| Pending edits | AsyncStorage, `kk.outbox` | Must survive being killed mid-edit. |
| Everything authoritative | SQLite on the server | The phone holds a copy; the server holds the truth. |

On web, SecureStore has no implementation and falls back to `localStorage`. That is
stated rather than hidden: a browser tab has no keystore, and the web build is a
development and demo surface.

---

## 3. Local-first reads

```
open a screen
   ├── cached rows render immediately, marked stale
   └── refresh in the background
         ├── success → replace, cache, clear stale
         └── failure → keep cached rows, set offline
```

A screen never blanks because a request failed. It says "as of 4:10pm" instead.

---

## 4. Queued writes and field-level conflict resolution

Edits are optimistic: the local copy changes at once and the change is queued
(`src/lib/sync.ts`). Online it drains within a moment; offline it waits, and drains
when connectivity returns — on app foreground, or on a 20-second retry while anything
is pending.

Repeated edits to the same row collapse into one operation, so typing a price digit by
digit does not become nine queued writes. The **earliest** `baseUpdatedAt` is kept,
because that is the version the artisan was genuinely looking at when she started.

On replay the server (`patch_listing`) works out which fields somebody else changed
after that version, by reading the append-only event log — every edit is already
recorded as `edited: price, quantity`. Then:

| Situation | Result |
|---|---|
| Row unchanged since | edit applies |
| Row changed, but not in the fields this edit touches | those fields apply — **both changes survive** |
| Row changed in a field this edit also touches | server value kept, conflict returned with **both** values |

Last-write-wins was rejected because both sides are real work: the server copy may
hold a marketplace's price correction, and the local copy is what the artisan believes
she set. Comparing values instead of consulting the log was tried first and was wrong
— it flags every field the artisan changed offline as a conflict with itself.

---

## 5. What is counted, and what cannot be

This is the part worth reading twice.

**Storefront views are counted.** We serve `/l/{id}`, so every request is a real view.
The viewer is reduced to a salted SHA-256 of IP + user agent, bucketed by the hour, so
a buyer reloading while they decide counts once and the row cannot be traced back to a
person.

**Everything else comes from that marketplace's API, or is reported as absent.**

| Platform | Views | Watchers | Sold / Orders / Revenue | Inventory |
|---|---|---|---|---|
| Storefront | counted here | no such feature | orders table | listing quantity |
| Amazon | **not exposed** — Business Reports / Brand Analytics needs Brand Registry and an async report request | no such concept | Orders API v0 | FBA Inventory API |
| Shopify | **not exposed** — Analytics API needs `read_analytics`, rarely granted to custom apps | no such metric | Admin GraphQL, behind `SHOPIFY_READ_ORDERS` | Admin GraphQL |
| ONDC | **no such concept** — Beckn carries search/order/fulfilment, not analytics | none | `on_confirm` callbacks | listing quantity |
| GeM | **not exposed** — catalogue, bids and orders only | none | seller orders endpoint | listing quantity |

An unavailable metric returns `{value: null, available: false, why: "…"}` and the app
renders a dash with the reason, tappable. It is **never** a zero. A zero says nobody
looked; the truth is that nobody counted, and those are different claims. An artisan
deciding whether a channel is worth the effort deserves to know it tells her nothing.

---

## 6. Cross-artisan insights

"What other artisans are doing" is computed from listings other sellers actually
published on this network. Three protections:

- only `published`/`active`/`sold` listings count — a draft is private working material
- no seller is named, and no row maps to one person
- **a category with fewer than 5 distinct artisans is dropped entirely**

That last one is the important one. With two sellers in a category, an "average price"
is one subtraction away from being somebody's price. Ranges are quartile bands rather
than exact means for the same reason. When there is not enough data the response says
`enough: false` and the app says so, rather than dressing thin data up as a trend.

The preference to hide the section is persisted, so hiding it is permanent until it is
turned back on in Profile.

---

## 7. What was removed, and why

**`/v1/trends`** asked Nemotron for "three trends with a realistic rupee band" and the
app rendered them with a confidence percentage. A language model guessing what handloom
sells for is not market data, and an artisan who priced against it would have been
pricing against nothing.

**The "Samuh" screen** displayed a fourteen-member artisan consortium filling a
500-piece order — named weavers, per-member progress bars, a ₹2,08,000 payout. The
members were a hardcoded array and the Join button set a boolean.

The underlying idea is sound and is the best answer to the middleman problem: a B2B
buyer wants 500 units in 45 days, one weaver can make 40, so the order goes to a trader
who subcontracts the cluster at a fraction of the price. Digitising one artisan's
catalogue does not change that — it just gives the middleman a better photograph.

What is real is the demand side, so that is what the screen is now. Every product page
carries a form for a larger quantity or a custom variation; those become `Enquiry`
rows. It is called **Bulk and custom orders**, because that is what an artisan would
call it.

**"Server address" in Profile.** Backend configuration is `EXPO_PUBLIC_API_URL` at
build time. An artisan should never be asked to type a URL.

---

## 8. Access control

Before this work, `GET /v1/listings/{id}` and its PATCH took no credentials: anyone who
guessed an id could read or edit a seller's unpublished drafts, costs and buyer orders.
Both now go through `_own_listing`, which admits the owning artisan, or a guest holding
the device token that created an unclaimed draft, and 403s everyone else. Enquiries and
orders are scoped the same way.

---

## 9. Translation integrity

The catalogue (`src/i18n/catalog.ts`) is authored in English and Hindi; seven more
languages are generated and committed, so the interface needs no network.

Two failure modes are now caught rather than shipped:

- **Script leakage.** The model occasionally emitted `અकेલા` for `અકેલા` — one or two
  Devanagari letters inside an otherwise correct Gujarati word. It reads as a stumble
  and survives review because the string is 95% right. `check_scripts.py` finds these
  and repairs them by block offset (the Indic blocks are ISCII-aligned, so the mapping
  is a lookup, not a guess); where no counterpart exists the string falls back to
  English, because a visible gap beats mangled text. `translate_ui.py` now rejects
  such output at generation time.
- **Silently failed batches.** Kannada and Odia were 306 of 330 strings still English
  from an earlier run that appeared to succeed. `translate_ui.py` is incremental by
  default and treats "identical to the English source" as a gap to refill.

A missing string falls back to **English, never Hindi**. An untranslated Gujarati
string showing English is a visible gap somebody will fix; showing Hindi looks like the
language switch is broken.
