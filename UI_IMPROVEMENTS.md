# UI improvements — 12 September 2026

The eight selected UI improvements are implemented in the web application.

| Selection | Delivered |
| --- | --- |
| U1 · Consistent design | Shared green-and-slate palette, typography, spacing, forms, buttons, cards, alerts, and refreshed sign-in/error screens. |
| U2 · Sidebar navigation | Collapsible desktop sidebar, mobile drawer, click-to-expand menu groups, current-page highlighting, retained permission checks, and menu search. |
| U3 · Dashboard | Clear balance cards, quick actions, inventory attention summary, recent vouchers, and readable chart-data tables. The new activity endpoint checks voucher access and scopes results to company and allowed/selected locations. |
| U4 · Tables | Sticky headers, scrollable regions, numeric/date sorting, column visibility, pointer/keyboard resizing, saved filters, saved widths/columns, row counts, and reset. Dynamically loaded rows refresh the controls. |
| U5 · Responsive layout | Mobile navigation, wrapping actions and filters, compact balance cards, larger touch targets, and horizontally scrollable tables. |
| U6 · Loading and feedback | Dashboard loading/error/empty states, recent-activity feedback, accessible upload status, correctly styled flash categories, and connection notices. |
| U7 · Formatting | Two-decimal monetary displays, aligned numbers, visible negative amounts, company currency context, and day-month-year dates in enhanced display tables. Entry values and accounting calculations are unchanged. |
| U8 · Accessibility | Skip link, visible keyboard focus, labelled controls, expanded-state announcements, mobile drawer keyboard handling, semantic table headings, keyboard column resizing, chart-data alternatives, and reduced-motion styling. |

## Table behaviour

View controls operate on rows already loaded by the screen; they do not change accounting totals. Grouped tables, merged cells, existing custom header sorting, and editable entry grids retain their original row behaviour. Width and visibility preferences are stored locally per user/company/table. Search filters last for the browser session. Reset clears the saved view, restores available rows to their original order, and does not resurrect deleted records. Print styles show all columns and rows hidden only by these view controls.

## Verification

- 203 automated tests passed, plus 182 template/navigation subtests. The database-schema integration test was excluded from this offline run.
- New endpoint tests cover permission denial, missing company, company scoping, location restrictions, serialization, and connection cleanup on failure.
- Production frontend build, JavaScript syntax, and whitespace checks passed.
- Browser checks used an isolated local preview with sample data and database access blocked. Checked desktop/mobile dashboard, ledger form, sign-in, menu expansion, chart-data disclosure, sorting, filtering, reload persistence, column hiding/resizing, dynamic row insertion, and reset order.
- Live posting, imports, and every individual production screen were not exercised against real company records. The standalone Android application was not redesigned.

The application uses the rebuilt frontend assets under `static/dist`. Restart the running web application and refresh the browser to load the complete update.
