# Material-inspired CareBlue redesign

## Product and scope

Observed: Flask with server-rendered Jinja pages, vanilla JavaScript, local Lucide icons, shared CSS tokens, SQLite development storage and PostgreSQL production support. The app separates hospital staff, doctors and platform operators. Authorization and clinical/payment transactions run on the server. Existing user changes and schema version 6 are preserved.

The primary daily tasks are patient registration and visit booking, consultation and prescription writing, and billing/payment. Administrators also organize doctors, availability, beds, inventory and user access. Platform operators manage hospital workspaces. The assumed design priority is quick, careful work in a hospital workspace; no new approval steps or clinical defaults are introduced.

## Coverage map

| Screen family | Observed issue | Structural change | Shared primitives and checks |
| --- | --- | --- | --- |
| Auth, registration, reset, platform sign-in | Plain panel, optional security fields compete with sign-in, no appearance preference | Calm expressive sign-in composition, progressively revealed optional fields, device-aware appearance | Auth card, fields, appearance menu; actual registration/login and validation |
| Staff, doctor and platform shell | Blue/light-only tokens, duplicated page/list labels, lost context on detail navigation | Violet tonal surfaces, clear care/workspace groups, remembered themes, reliable return to filtered lists | Navigation, breadcrumbs, menu, safe context links; roles, keyboard, mobile drawer |
| Admin daily work | Full-width queue and duplicated recent data dominate operational actions | Waiting queue as main task, operational review and quick actions alongside it | Priority card, work columns, operation links; real counts and focused queue filters |
| Doctor daily work | Counts used as a long title, upcoming actions compete with today's work | Stable page title, concise daily summary, next patient first, waiting list and supporting weekly context | Queue, summary pills, cards; prescribe/complete and real chart data |
| Patients, doctors, visits, billing, pharmacy, audit and admissions | Different row/card patterns, filters reset after a detail task | Shared collection surface, consistent context actions, optional date scopes for visits, filtered-list return | Tables/mobile cards, toolbars, sorting and pagination; search, sorting and back context |
| Registration/booking, doctor profile/access, hours, absences | Recent faster workflows work but use a rigid/light-only visual treatment | Apply common form hierarchy and readable state handling while keeping minimal fields and single-save workflows | Fields, segmented choices, expansion panels and save feedback; daily workflows and failed submissions |
| Prescription, clinical record and print | Long spaced editor; no dark display theme; details lose patient-list context | Compact editor, review remains explicit, reusable entries retained, contextual return and theme-independent printed documents | Clinical cards, editable history, templates, tabs; drafts, signing, stale writes, print |
| Bill detail and invoice | Balance and payment are separate from list context | Keep payment visible with line items, return to current ledger filters, consistent document ink | Balance panel, payment form, invoice; full/partial payment and print |
| Hospital settings/team; platform workspace detail | Settings run as one long page; staff and doctor controls need visual grouping | Branding/contact first, occasional settings revealed progressively, coherent access surfaces | Settings sections, details, safe links, dialogs; branding, role protection and pagination |
| Empty, no-results, 403/404/500/503 and interrupted forms | States exist but lack theme consistency | Semantic status pairs, clear next actions, durable errors, entered values retained | Empty state, banner, snackbar, validation summary; mobile, keyboard and simulated failure |

## Direction

A custom system inspired by Material You: violet-led warm surfaces, pill actions, 20–32px cards and overlays, Roboto served locally, meaningful status colors, and restrained organic forms on authentication/empty states. Hospital colors remain stored unchanged and receive tested control palettes in both themes. This is not a claim of exact Material Design specification compliance.

Light, dark and device modes use semantic color pairs. Appearance is the only new browser-stored preference. Patient data is not stored by this redesign. Theme selection is available in the account menu and on public pages and follows device changes when the device setting is selected.

## Practical workflow changes

| Task | Previously | Current behavior |
| --- | --- | --- |
| Find and edit a patient | Detail tasks could return to the default list; phone matches could be discarded in the picker | Name/phone search shows identifying details; edit, save, cancel and booking preserve the originating search/sort/page |
| Register and book | Faster registration was already implemented, but supporting fields competed with the main task | Name-only registration, Save & add visit, or inline creation during booking; walk-in first, timed booking and optional patient details revealed when needed |
| Manage doctors and hours | Large profile cards, seven spacious schedule cards, repeated navigation | Compact roster with direct Hours/Book actions, seven compact weekly rows, Copy Monday and one save; breaks, biography and account creation remain optional |
| Write a prescription | Reuse tools and patient details preceded the main editor; several long sections | Diagnosis and medicines beside record/review on desktop; templates, advice, history editing and revisions expand independently; allergies stay prominent; draft/save and signing remain explicit |
| Create and pay a bill | Large patient option sets; an older selected visit could be omitted; users might recreate an existing bill | Searchable bounded initial options with the selected patient/visit retained, automatic fee, expandable extras, full/partial collection in one submission, and existing-bill redirect |
| Manage staff | Access forms compete for attention; new staff initially defaulted to administrator | Searchable people list, per-person expandable access, password generator, and Reception as the new-staff default; administrator remains an explicit choice |
| Review visits | Deep filters and separate sorting controls increase work; export could omit actual values | One-click date scopes and column sorting, compact labeled mobile records; export includes the matching results across pages with literal patient-entered text |
| Start daily work | Recent activity dominates the dashboard | Waiting patients and operational review first; billing and doctors next; latest bookings expand on demand |

Return destinations are checked against known collections, role and permission. External URLs, malformed paths and unauthorized destinations are rejected. These links preserve navigation context without exposing another hospital's records. Date scopes filter lists only; they add no booking-time restrictions.

The initial billing patient/visit lists are bounded at 50/100. A specifically linked older visit and its correct patient are appended when necessary, and remote visit selection can supply a patient absent from the initial options. Bed assignment uses the same remote patient search. Consultation amounts, explicit absences, collision protection, clinical review, version conflicts, payment idempotency and tenant permissions remain enforced.

## Design system and accessibility behavior

Shared semantic tokens drive warm surfaces, tonal selection, readable status pairs, focus rings, native controls and theme-independent document ink. The interface uses 4px spacing increments, 20–32px surface radii, pill actions and 48px primary controls. Roboto is a local variable font with `font-display: swap`; its SIL Open Font License is included beside the font, sourced from [Google Fonts](https://github.com/google/fonts/tree/main/ofl/roboto).

Menus, dialogs, the command palette, drawer, searchable pickers and record tabs support keyboard operation and focus return. Selected filters include text/checkmarks, and clinical/payment states include labels. Validation opens all collapsed ancestors of an invalid field before focusing it. Dynamic medicine and billing rows retain associated labels and focus after removal. Short/enlarged layouts use a static weekly-hours save bar so it cannot cover fields. Reduced-motion preference suppresses transitions. Printed prescriptions and invoices retain white paper and dark ink in either appearance.

## Before and after evidence

Local screenshots use disposable records. The same representative admin dashboard measures 2489px → 1456px at 390px width, and 1318px → 1000px at 1440px width. The signed prescription editor measures 1819px → 1264px at 1440px; its fresh mobile view measures 2239px → 1984px at 390px. These are captured page heights with the default sections shown, not task-time measurements. Expanding optional content increases height. Desktop screenshots have a 1000px viewport minimum.

The preserved before directory covers sign-in, admin/doctor dashboards, booking, billing and prescribing at 390/1440px. Final all-screen screenshots cover both themes; clinical and collection review directories additionally capture fresh mobile entry and expanded states. Review artifacts remain ignored local files rather than application assets.

Local comparison sheets: [mobile](../instance/ui-review/material-before-after-390.png) and [desktop](../instance/ui-review/material-before-after-1440.png). These links work in this workspace; ignored review artifacts are not included in a repository checkout.

## Verification

Completed 3 October 2026, using isolated SQLite fixtures on port 5002. The project preview on port 5001 uses the existing local database. No redesign migration is required: schema remains version 6.

| Verification | Result |
| --- | --- |
| Python suite | 171 tests: 97 passed, 74 PostgreSQL cases skipped because `TEST_POSTGRES_URL` is unset locally |
| JavaScript | 13 regression tests passed: 7 forms and 6 appearance; all 10 scripts parse |
| Full screen sweep | 42 screens × 2 themes × 9 widths (320–1920px): 756 layout checks; no horizontal overflow, missing visible field labels, unnamed buttons, duplicate IDs, runtime errors or failed static assets detected |
| Text contrast | 3920 computed text checks across 84 page/theme states; no failures at the applicable 4.5:1 or 3:1 threshold; 16 semantic foreground/background/control pairs checked per state |
| Hospital palette | 216 sampled hospital colors tested in light/dark modes; original branding value is preserved |
| Independent enlarged text | All 42 screens in both themes at 320px/200% text: 84 reflow cases passed; focused weekly-hours controls and short-height overlays also checked |
| Main task flows | 12 main and 7 edge groups passed: minimal/inline registration, custom-time booking, one-save hours, doctor access, full/partial billing, templates, clinical history, drafts, review/sign/next and print |
| Appearance and recovery | 13 groups passed: keyboard menus, device theme, persistence/storage fallback, drawer/dialog focus, context returns, interrupted form recovery, collapsed-field validation, reduced motion and print |
| Additional billing/team/filter flows | 5 groups passed; 16 independent final targeted checks passed in both themes |
| Clinical validation | 10 focused checks passed, including live history, safe reuse, hidden-field reveal, draft state and signing validation |
| Final practical edge cases | 6 groups passed: initial/remote linked visits retain patients outside initial options, dynamic bill labels/focus, copy-address success/manual fallback and phone search/assignment in an available bed |
| Local database | Schema 6, SQLite integrity and foreign-key checks passed; live records differ from the earlier backup and are retained |

Evidence under `instance/ui-review/`: `practical-unit-final.log`, `final-js-verification.json`, `material-after/browser-audit.json`, `material-after/actual-text-contrast.json`, `fast-interactions.json`, `fast-edge-interactions.json`, `material-after/interactions.json`, `material-after/extra-interactions.json`, `clinical-final-flows.json`, `practical-final-flows.json`, `final-local-data-verification.json` and the independent-audit reports. Browser write tests for this redesign use disposable records only. The final local-data check compares all 22 existing tables to the pre-workflow backup, without printing records; that comparison cannot attribute changes made since the backup. No records are restored by the review.

The CI workflow runs both database suites with a PostgreSQL 17 service, the JavaScript tests and syntax checks. That workflow has not been executed remotely in this session. Local browser checks use Chromium, not a complete browser/device matrix. Text enlargement, computed contrast and keyboard checks are useful evidence, but do not constitute WCAG certification or a full screen-reader audit. Real mobile keyboards, production-scale load, every hospital customization and every exceptional clinical/payment input require further validation. No claim of zero defects or measured task-time savings is made.

## Maintenance handoff

Use `tokens.css` for new semantic colors and common dimensions, `style.css` for components, `workspace.css` for shared layouts, and `collections.css`/`clinical.css` for those screen families. New controls should follow the existing labels, focus behavior, status text, responsive layout and reduced-motion rules. Use `context_link()` and `back_url()` for collection-to-detail tasks; retain server authorization and validation. Avoid theme-specific hard-coded text colors and keep print documents independent of screen appearance.

Run `python -m unittest discover -s tests -v`, `node --test tests/test_forms.cjs tests/test_theme.cjs`, and JavaScript syntax checks after relevant behavior changes. Set `TEST_POSTGRES_URL` to an isolated test database to include PostgreSQL coverage. Review both themes, narrow/mobile entry, expanded optional states, keyboard actions and print when adding a screen.
