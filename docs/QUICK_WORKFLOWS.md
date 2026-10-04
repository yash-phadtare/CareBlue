# Faster daily workflows

Implemented October 3, 2026. These changes apply separately to each hospital workspace.

## Patients and visits

- A patient can be registered with a name alone. Age, gender, contact and clinical notes are optional. Unknown age is stored as null; age zero remains a valid age.
- **Save & add visit** opens booking with the patient already selected. Existing patient records can be edited with protection against overwriting another user's changes.
- Booking defaults to **Walk-in today**: select a patient and doctor, then add the visit. New patients can also be registered within this form in the same transaction.
- Date and time appear only for timed bookings. Weekly hours, breaks, fifteen-minute intervals and future-date restrictions no longer prevent a valid booking. A timed slot still cannot be reserved twice.
- Patient search accepts names and phone numbers. Doctor search accepts names and specialties. Selected patients outside the initial list remain available through search or a direct booking shortcut.
- Walk-ins appear in the doctor's daily queue without a fabricated appointment time. Queue ordering is consistent across SQLite and PostgreSQL.

## Doctors and users

- A doctor profile and optional sign-in credentials can be created together. Contact, experience and consultation fee are optional.
- Weekly availability saves all seven days in one transaction. **Copy Monday to all** fills the remaining days. Weekly hours are suggestions for timed bookings.
- Changes to hours and absences preserve existing visits. Absences need only a first date; the final date defaults to the same day and an empty reason becomes `Unavailable`. Existing visits in an absence range are reported for review. A recorded absence prevents new bookings on those dates.
- Team search finds staff names and emails and doctor names and usernames. Password generation is available when creating access.
- A doctor username can change without a forced password reset. Saving unchanged staff or doctor access keeps existing sessions. Actual access changes still revoke old sessions.

## Billing

- A linked visit automatically includes its consultation fee and selects its patient.
- Empty extra-charge rows are optional and removable. Partially entered charges are rejected clearly.
- The total updates while charges are entered. **Create bill & record payment** supports a full payment with no amount entry, or an explicitly entered partial payment.
- Bill creation and initial payment commit together. Invalid or excessive payments roll back the bill. Request tokens prevent repeat registration, booking, billing and payment where supplied by the forms.

## Consultation and prescriptions

- Diagnosis, medicine entries and advice can be saved as personal templates within the doctor's hospital account. Patient identity, allergies and history are excluded from reusable templates.
- **Reuse last prescription** loads the same patient's last signed prescription by that doctor. Replacing entered diagnosis, medicines or advice requires confirmation. Reuse clears the review confirmation.
- Medicine dose, frequency, duration and route are grouped together. Additional strength, meal and timing instructions are collapsed. An advice-only consultation needs no medicine.
- Patient history and allergies update with the prescription in one transaction. Concurrent changes are detected before saving.
- **Ctrl+Enter** saves a draft. Signing requires explicit review and complete medicine details. **Sign & next patient** can complete the current visit and open the next waiting patient. Completion no longer depends on the appointment date being in the past.
- Signed amendments retain their reason, immutable revision history and identity snapshot. Printable prescriptions show the full recorded gender, honest unknown age, signed advice and snapshotted allergies.

## Validation and migration

Schema version 6 makes patient age and appointment time nullable, adds request-token indexes and a patient clinical version, and introduces prescription templates. Apply it through `flask --app app db-upgrade` after backing up an existing installation. Web workers do not migrate schemas automatically. SQLite-to-PostgreSQL imports include prescription templates.

Local verification:

- 152 Python tests: 87 passed; 65 PostgreSQL integration cases skipped because no local PostgreSQL test URL is configured. CI already provisions PostgreSQL and runs the inherited integration cases.
- Seven existing JavaScript form regressions passed; all nine application JavaScript files passed syntax checks.
- 42 page states across nine viewport widths: 378 layout, label and structure checks passed with no browser errors or failed static assets.
- 19 browser workflow groups passed, including registration, inline patient booking, custom times, weekly hours, optional access setup, team search, full and partial payments, clinical history, prescription reuse, signing, printing and draft recovery.
- Migration tests cover populated legacy data, rollback during version 6, preserved foreign keys, nullable queue visits and retained uniqueness for timed visits.

Browser checks run against a disposable database. The actual local database was backed up before upgrading from schema 5 to 6; original row contents in all 22 existing data tables were verified unchanged, and integrity and foreign-key checks passed. The local app runs at `http://127.0.0.1:5001`.
