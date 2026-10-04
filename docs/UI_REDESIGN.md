# CareBlue UI redesign and verification

This records the earlier usability pass. The current appearance and structural
review is documented in [Material-inspired redesign](MATERIAL_REDESIGN.md).

Completed 3 October 2026. CareBlue remains a platform of independent hospital
workspaces, with separate interfaces for hospital staff, clinicians and platform
operators. The redesign follows the daily work of these users: finding patients,
booking visits, consulting, managing resources and collecting payments.

## Final website usability review

The full website received a further pass across public sign-in, hospital staff,
doctor, platform management and error pages. The resulting changes include:

- Mobile records place short fields side by side and show text for row actions.
  Actual patient, bill and audit event identifiers remain visible.
- Team access uses a compact staff/doctor list. Each person expands to reveal
  their access form; new staff are added in a labeled dialog with password
  visibility, validation and focus on the first field.
- Registration suggests a workspace address from the hospital name, shows its
  sign-in path and preserves an address entered by the user.
- Closed schedule days hide disabled time fields. Copying Monday marks the
  affected days as unsaved, and opening/closing a day updates its visible status.
- Unsaved changes are tracked per form, including dynamic fields. Saving one
  schedule keeps the warning for the other edited days. Reverting values clears
  the warning, and failed requests preserve entries and restore the save button.
- Billing shows the balance first on mobile and offers a full-balance shortcut
  while retaining partial payments. Ward and patient selectors are searchable;
  patient options include their record number.
- Disabled doctors show accurate access status and an unavailable booking
  shortcut. Doctor removal has a visible label and retains its confirmation.
- Validation uses the field errors and summary without a duplicate toast.
  Notification text no longer intercepts clicks on forms; dismiss buttons remain
  usable. Keyboard tooltips track the control that owns them.
- Searchable selectors expose one accessible control and communicate required
  state. Printed mobile tables restore their headers, rows and columns, and omit
  interactive search and action controls.

Final verification: 122 Python tests discovered, with 71 passing and 51 skipped
because a local PostgreSQL test URL was not configured; seven JavaScript form
regressions and all eight syntax checks passed. The responsive audit covered
41 page states at nine widths (369 checks) with no detected layout, labeling,
duplicate-ID, JavaScript or static-asset issues. All 12 core browser workflow
groups passed. Seven additional browser groups verified registration address
suggestions, staff access, disabled doctors, ward/bed assignment and discharge,
unsaved schedules, full-balance payments and mobile/print table behavior.
Final checks also covered preserved form names/actions across all 40 templates,
sorting reset without losing search, and retry controls under persistent errors.
Another 63 layout checks passed with long ward names, additional staff, visible
audit identifiers and paid billing. The restarted local application returned 200
from `/ready` and served the current registration and frontend files.

Desktop/mobile screenshots were reviewed across every audited page. Invoice,
prescription and mobile patient-register PDF samples each rendered as one A4
page and were visually inspected. Browser data changes used only a disposable
fixture database. The application database and schema were not changed by this
review. Browser coverage remains Chrome; this is not a screen-reader or
cross-browser certification.

## Follow-up: simplify daily use

A subsequent UX pass removed elements that repeated information or competed
with the task at hand:

- Removed sign-in marketing panels, decorative page labels, generic patient
  initials in clinical page headings and unused floating action markup.
- Removed the notification menu that only repeated appointment counts and the
  duplicate mobile overflow menu. Search, quick creation and profile access now
  use the same controls on desktop and mobile.
- Moved scheduling into the appointment workflow and quick actions, export into
  the appointment register, and admission history under beds and wards. Parent
  navigation remains highlighted on these secondary pages.
- Replaced duplicate status chips with the existing status selector. Sorting
  expands on demand, retains visible state when applied and stays out of empty
  or single-record results. Search/filter submissions preserve selected sorting.
- Removed single-page pagination footers and duplicate dashboard counters.
  List headings show all matching records, including across multiple pages.
- Removed the patient record sidebar that repeated contact information and
  visit history. Overview, prescriptions and visits remain available in tabs;
  those tabs fit or wrap on narrow screens.
- Hid visual sequence numbers in mobile cards while retaining actual bill and
  patient record identifiers.

Follow-up validation: five focused Python workflow tests, five JavaScript form
regressions, all eight JavaScript syntax checks, 369 browser layout checks and
all 12 browser workflow groups passed. Additional browser checks verified
keyboard sorting, mobile quick booking, preserved sorting during search,
single-page cleanup and correct counts across a 42-record workspace. Clinical
headers received another 27 checks after the final visual adjustment. These
checks used an isolated fixture database; the local application database was
not changed by review actions. The sections below describe the initial redesign.

## Design system

`static/css/tokens.css` owns semantic colors, typography, spacing, elevation,
radii, control sizes and overlay levels. `static/css/style.css` owns components
and responsive behavior; `layout-utilities.css` provides small layout helpers.

| Foundation | Decision |
| --- | --- |
| Typography | System font; 15px body, 13px supporting text, 12px captions; 24–30px page titles |
| Spacing | 4, 8, 12, 16, 20, 24, 32, 40 and 48px tokens |
| Surfaces | Solid white panels on a neutral background, restrained borders and shadows |
| Controls | 44px standard height; explicit primary, secondary, tonal, ghost and destructive actions |
| Status | Semantic color accompanied by visible text and, where useful, an icon |
| Focus | Visible focus rings; keyboard support and focus restoration for overlays |
| Branding | Hospital name, logo and color remain customizable; derived control colors preserve readable labels |
| Navigation | Grouped by daily operations, resources and workspace; separate clinical and platform navigation |

`careblue/presentation.py` derives a control palette from the stored hospital
color. White labels on primary and hover backgrounds have at least 5.5:1
contrast. The original brand color remains stored unchanged. Invalid legacy
color values receive a safe presentation fallback. Automated tests cover
extreme colors and 216 sampled palettes, including active navigation contrast.

## Page coverage and resolved issues

Every page template was reviewed, including secondary settings and error pages.
Shared shell, navigation and pagination templates were also consolidated.

| Pages | Main changes |
| --- | --- |
| Staff, doctor and platform sign-in; registration; password reset | Clear role and workspace context, labeled fields, ordered MFA inputs, visible validation and password controls |
| Hospital dashboard | Today's patient queue and operational actions come first; supporting metrics and recent visits follow |
| Add patient; patient registry | Demographics and clinical history grouped separately; searchable records, mobile cards and useful empty states |
| Add doctor; doctor roster; credentials; weekly hours; absences | Profile/access/hours progression, consistent clinician cards, optional breaks, usable time inputs and clear absence handling |
| Book appointment; appointment register | Searchable people selection, keyboard time slots, loading/error feedback, preserved filters and compact actions |
| Billing list; new bill; bill detail; invoice | Clear ledger, linked visit selection, labeled dynamic items, payment progress, balance hierarchy and print layout |
| Pharmacy; beds; admissions | Stock exceptions, ward/bed availability, explicit state actions and readable admissions history |
| Audit history | Consistent search, sorting, labeled records and pagination |
| Hospital settings; team | Grouped branding/contact/hours/documents, role explanations and separate staff/doctor pagination |
| Doctor dashboard; own visits; patient list | Current consultation, waiting queue and upcoming visits; accurate chart scale and counts |
| Patient history; prescription editor; prescription print | Allergy visibility, clinical text wrapping, accessible tabs, labeled medicines, review/sign section and revision history |
| Platform dashboard; hospital management | Workspace metadata, correct Active/Trial/Suspended badges, grouped access controls and support history |
| 403/404, generic HTTP errors, 500 and database unavailable | Contextual explanations, recovery actions and readable reference details |

The highest-impact issues addressed were inconsistent component geometry,
competing page actions, clipped dropdowns, tablet time controls, weak brand
contrast, unlabeled dynamic fields and lost overlay focus. Mobile tables now
present labeled records while preserving table semantics. Long patient names,
newborn age zero, missing values, empty results and clinical line breaks are
handled explicitly.

Interaction review also exposed two functional frontend defects: fields named
`method` or `action` could shadow native form properties, and an earlier patient
search response could overwrite a newer query or reopen a closed picker. Both
are fixed. Saving disables repeated submission and restores controls after
failure. Network errors and version conflicts preserve entered values. Booking
scroll behavior respects reduced-motion preferences.

## Eight implementation passes

1. **Audit:** inventoried every template, role, route, form dependency and shared component.
2. **Architecture:** established tokens, grouped navigation and consistent page structure.
3. **Components:** rebuilt forms, tables, cards, menus, dialogs, feedback and print surfaces.
4. **Responsive:** checked phone, tablet and desktop layouts, including overlay boundaries.
5. **Interaction:** exercised keyboard access, validation, saving, failures and focus restoration.
6. **Details:** aligned icons and controls, normalized spacing, checked long content and zero values.
7. **Consistency:** consolidated navigation/pagination and compared desktop/mobile page galleries.
8. **Final QA:** ran automated regressions, real browser workflows and visual print inspection.

## Verification results

| Check | Result |
| --- | --- |
| Python unittest discovery | 122 discovered: 71 passed; 51 PostgreSQL tests skipped because no local test PostgreSQL URL was configured |
| Focused final Python checks | Presentation, staff navigation, doctor authorization, branding/invoice and platform separation passed |
| Form JavaScript regressions | All five checks passed, including registration redirects, session expiry, protected-form preservation and native-property shadowing |
| JavaScript syntax | All eight application JavaScript files passed |
| Responsive browser audit | 41 page states × nine widths = 369 checks; no detected body/main overflow, duplicate IDs, unnamed buttons, missing visible field labels, console errors or failed static assets |
| Workflow browser review | All 12 workflow groups passed, including actual registration, booking, payment and signed prescription amendment |
| Print | Invoice and prescription rendered as one A4 page each and were visually inspected |
| Custom branding in browser | Bright yellow branding retained; actual button text contrast measured 6.06:1 |
| Reduced motion | Both explicit booking scroll actions used `auto` with reduced motion enabled |
| Compatibility | Original literal form names, IDs and actions checked against the pre-redesign baseline; none removed |
| Running application | `/ready` returned 200; current registration page loads the new token stylesheet |

The audited widths were 320, 375, 390, 430, 768, 1024, 1280, 1440 and 1920px.
Browser checks used installed Chrome in fresh headless sessions. Desktop and
mobile screenshots of all audited page states were visually reviewed. Seven
genuine new-workspace empty states were checked separately, along with 42-record
pagination, keyboard menus/dialogs, failed submissions and stale prescriptions.

The temporary review server used an isolated test database. No review patients,
payments, prescriptions or hospital accounts were inserted into the local
application database. Existing routes, permissions, database contracts and
clinical workflows remain in place; this redesign requires no schema migration.

This review does not establish Safari/Firefox, physical-device or screen-reader
compatibility. PostgreSQL verification remains covered by the existing CI job,
which provisions a PostgreSQL service; it was not executed locally.

## Files and repeatable checks

Important implementation files:

- `static/css/tokens.css`, `style.css` and `layout-utilities.css`
- `templates/base.html` and `templates/components/{navigation,pagination}.html`
- All authentication, admin, doctor, tenant, platform and error templates
- `static/js/script.js`, `forms.js` and relevant page scripts
- `careblue/presentation.py` and the tenant presentation context
- `tests/test_presentation.py` and `tests/test_forms.cjs`

Standard regression commands:

```text
python -m unittest discover -s tests
node --test tests/test_forms.cjs
```

In this local session the JavaScript checks ran through the available Node
runtime using the same test functions and Node assertions. CI uses the ordinary
`node --test` command.

Local review artifacts are retained under the ignored `instance/ui-review/`
directory: `browser-audit.json`, `interaction-report.json`, `final-checks.json`,
desktop/mobile screenshots and galleries, and invoice/prescription PDF previews.
These contain disposable fixture data and are not deployment assets.
