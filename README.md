# CareBlue

CareBlue is a shared platform where hospitals create independent workspaces and
customize their own management system. It uses Flask, SQLite for development, and
PostgreSQL plus private S3 storage for production. Python 3.11+ is required.

The interface uses a warm Material-inspired design system, locally served Roboto,
responsive layouts, hospital branding and light/dark/device appearance modes.
See [current UI/UX redesign and verification](docs/MATERIAL_REDESIGN.md) for page
coverage, workflow changes, browser checks and remaining verification limits.

Daily work now supports name-only patient registration, inline registration while
booking, walk-in queues, optional timed appointments, a single weekly-hours save,
immediate bill payments and personal prescription templates. See
[Faster daily workflows](docs/QUICK_WORKFLOWS.md) for the current rules and checks.

## Local setup (PowerShell)

```powershell
python -m pip install -r requirements.txt
$env:SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
$env:MFA_ENCRYPTION_KEY = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
flask --app app db-upgrade
flask --app app create-platform-admin --email operator@example.com --name Operator
python app.py
```

Save the generated keys in your development environment or secret manager and
reuse them across restarts. The commands above generate new keys each time they
are run. CareBlue reads environment variables; it does not automatically load
`.env.example`. Never commit actual keys. Production also requires `DATABASE_URL`
and `S3_BUCKET`; see `.env.example` for the remaining settings.

Open `/register` to create a hospital workspace and its first administrator.
For an operator-created hospital, run
`flask --app app create-admin --email admin@example.com --name Administrator --hospital CareBlue`.
To create another administrator for the same hospital, use
`flask --app app create-admin --hospital-id <id>` and supply their own credentials.
Administrators belong to a hospital independently of its original creator.

## Hospital workspaces

Each hospital has a sign-in address at `/h/<slug>`. Staff and doctors should use
that address: the same email or doctor username may belong to different hospitals.
The generic `/login` supports unambiguous accounts only. Hospital ownership comes
from the authenticated account, and permissions are checked on every request.
Patients, visits, prescribing, billing, beds, inventory and uploads remain scoped
to that hospital.

Self-service registration is enabled by default and creates a 14-day Starter trial.
Set `REGISTRATION_ENABLED=0` to close new registrations. Existing and CLI-created
hospitals start Active; migrated workspaces use `/h/hospital-<id>`.

Hospital administrators use `/hospital/settings` to change the hospital name,
logo, brand color, contact information, timezone, default scheduling hours,
document style and footer. Hours prefill new doctor schedules. Timezone changes
are blocked while scheduled visits exist. New prescription versions retain the
contact details, style and footer saved with that version; invoices use current
hospital settings. Logos are public branding; doctor photographs use authenticated
asset routes and private tenant directories or S3 keys.

`/hospital/team` manages staff roles and doctor access. Account changes revoke
existing sessions, and the web interface protects the last active administrator.

| Role | Access |
| --- | --- |
| Administrator | All hospital operations, settings, team and audit history |
| Reception | Patients, appointments and beds |
| Billing | Invoices and payments |
| Pharmacy | Inventory and stock movements |
| Doctor | Own clinical appointments and prescribing workflows |

## Platform administration

Operators sign in at `/platform/login` using separate platform accounts, created
with `flask --app app create-platform-admin`. These accounts manage hospital
metadata, Starter/Growth/Enterprise plan labels, trial dates and Active/Trial/
Suspended status. Changes require a reason and appear in platform support history.
Suspension and expired trials block hospital sessions and sign-in. Operators
cannot access hospital clinical records through the platform interface.

Plan labels and access management are implemented. Automated subscription payment
collection, recurring billing and plan-based feature limits are not connected to
a payment provider. Patient billing remains a separate hospital workflow.

## Releases and migrations

Back up your existing database, then run `flask --app app db-upgrade` as an explicit
release operation before starting updated workers. Build and startup do not
migrate databases. `/ready` returns 503 until the schema is current.

Version 4 preserves existing IDs and hospital ownership, creates separate hospital
records, moves all hospital foreign keys to them, removes legacy floating-point
money columns, and adds prescription identity snapshots and archival flags.
The base SQL file is the version-0 baseline; the versioned runner is the upgrade
entry point. Legacy upgrades and versioned changes share one transaction. Failed
upgrades roll back; existing duplicate bookings or invalid relationships must be
resolved before retrying. Run the upgrade while old workers are stopped because
the old application uses columns removed by version 4.

Version 5 adds workspace addresses, customization, tenant staff roles and separate
platform accounts. It replaces global staff-email and doctor-username uniqueness
with uniqueness within each hospital, preserving existing account IDs, sessions
and clinical relationships. Stop workers during the upgrade, back up the database,
then restart all workers with the updated application.

Existing prescription versions capture the identity available at migration time
and display that provenance. Their original historical identities cannot be
reconstructed. New prescription versions preserve the identity reviewed when
that version was saved. Archived beds and medicines remain available to historical
records and are excluded from new assignments and inventory searches.

## MFA key rotation

`MFA_ENCRYPTION_KEY` is a Fernet key separate from the session `SECRET_KEY`.
Changing the session key revokes cookies without invalidating new MFA secrets.
To rotate MFA encryption, configure a new primary key and place old Fernet keys
in `MFA_PREVIOUS_ENCRYPTION_KEYS`, then run:

```powershell
flask --app app rotate-mfa-keys
```

For MFA secrets created by older versions, set `CAREBLUE_LEGACY_SESSION_KEY` to
the original session key before running the command. Keep the session key unchanged
until legacy secrets have been re-encrypted. After verification, remove old keys
from the environment. The command updates all secrets atomically. Enrollment
requires a configured persistent MFA key.

## Development and verification

Routes declare access on their blueprints; additional endpoints must use
`careblue.routes.blueprints.access`. Undeclared application endpoints deny access.
Booking, prescribing, billing, and admission operations live in `careblue/workflows.py`.
They receive a connection and leave commit/rollback to the caller. Requests reuse
one connection; write requests acquire a SQLite write transaction before domain
validation. Teardown rolls back uncommitted work and closes the connection.
Use `conn.insert(...)` when an inserted ID is needed; `conn.execute(...)` does not
implicitly add `RETURNING`. Monetary database fields contain integer cents;
presentation uses the explicit `from_minor` filter.

```powershell
python -m unittest discover -s tests -v
```

Set `TEST_POSTGRES_URL` to a disposable PostgreSQL database to run the same
workflow tests against isolated PostgreSQL schemas. CI supplies PostgreSQL 17.
The test suite uses temporary databases and does not change your working database.
