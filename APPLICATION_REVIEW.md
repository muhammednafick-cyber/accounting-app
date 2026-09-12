# Application review — 11 September 2026

## Fixes

- Database connections: closing a wrapper twice no longer releases or rolls back a connection already reused by another request. Empty batch inserts and default-sized fetches now use valid driver calls. Connection URLs retain encoded credentials and TLS options, and the pool maximum cannot be smaller than its minimum.
- Startup: template and static paths resolve from the application directory even when launched elsewhere. Packaged installations save their session key beside the executable.
- Mobile login: users without company assignments cannot select an arbitrary company; invalid company identifiers return a validation error.
- Recurring vouchers: January 31 advances to the last day of February, including leap years. Non-finite amounts are rejected. Voucher creation and schedule advancement share a transaction, with rollback and connection cleanup on failure. Empty narrations are supported.
- Analysis dashboard: failed loads show a visible error rather than zero balances. A cancelled sales request cannot overwrite the newly selected year's results. The development server proxies the dashboard-data endpoint. Production assets were rebuilt.
- Tests: corrected the chatbot stock test to use the current closing-stock interface and isolated the invoice export test from item-mapping database lookups. Added targeted regression tests.

## Verification

- 198 tests passed, with 182 subtests, across regression, chatbot, permissions, routing, toolkit, skills, phrasebook, invoice extraction, and report compiler tests.
- The final test run explicitly blocked PostgreSQL connections. Application construction tests mocked schema initialization.
- All application templates parsed and navigation destinations resolved.
- Python compilation and undefined-name checks passed for the application and database modules.
- Frontend production build and syntax checks for standalone web and Android JavaScript passed.
- Git whitespace checks passed.

## Limits

This is a source review and automated offline verification, not proof that every workflow is bug-free. Database-writing integration tests were not run against the configured accounting database. The report schema test was excluded because it requires PostgreSQL. Live posting, imports against real master data, concurrent database transactions, authenticated browser workflows, external AI services, and the installed Android app still require an isolated integration environment.

The frontend build reports advisory warnings about bundle size and outdated browser compatibility data; neither blocks the build.
