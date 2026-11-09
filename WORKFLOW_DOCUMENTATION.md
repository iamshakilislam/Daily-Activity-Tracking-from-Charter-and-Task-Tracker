# Resource Daily Activity Report

The script now fetches projects from Worqcircle and builds Charter-only member snapshots. Task Tracker fetching is disabled; neither `Datewise Summary` nor `Dashboard` is read. Tracker URLs, activity, counts, and accuracy remain blank for new snapshots, meaning not fetched rather than zero activity. Existing progress history is preserved.

## Configuration

Required environment variables:

- `WORQCIRCLE_API_KEY`: the `x-api-key` value for the analytics API.
- `GOOGLE_SERVICE_ACCOUNT_JSON`: raw JSON, base64 JSON, or a JSON file path; a local `service_account.json` is also supported.
- `AUDIT_SHEET_URL`
- `LOCAL_RESOURCE_POOL_URL`

Optional:

- `WORQCIRCLE_API_ENDPOINT`: defaults to `https://dv3rilzwfskd8.cloudfront.net`.
- `PROJECT_NAME_FILTER`: exact project name, ignoring case and surrounding whitespace.

ClickUp credentials are no longer required. Keep credentials in local `.env` or GitHub secrets; do not commit them.

## Project fetching and selection

The script calls `GET /api/v1/project-drawer/external/analytics` with `x-api-key`, `Accept: application/json`, and `limit`/`skip` pagination. It reads `projectDrawers` and the `filteredTotalCount` (falling back to `count`). Invalid responses, duplicate projects, HTTP errors, and premature pagination termination stop the run before Google Sheets writes.

Worqcircle field mapping:

| API field | Report use |
| --- | --- |
| `project_drawer_name` | Project Name and optional name filter |
| `project_status` | Ticket Status and project eligibility |
| `projectCharterLink` | Charter source spreadsheet |
| `client_alias` | Client Alias |
| `pdl[].email` | Ignored PDL matching and audit context |
| `deliveryLead` | Audit delivery lead |

Status normalization replaces hyphens and underscores with spaces and ignores case. Eligible statuses remain `project received`, `project in progress`, and `pending schedule approval`. The latest status view uses all fetched project statuses, including completed projects. Projects whose PDL emails match the existing ignore list are skipped.

## Charter and output behavior

1. Read `Team Member Details` from each eligible project's Charter.
2. Keep remote members with a valid QAI ID and name, using the existing Charter validation rules. Include inactive/offboarded members as well as active ones.
3. Read Start Date and Final Delivery Date from `Project Delivery Plan`.
4. Build one snapshot per Charter member, using Charter status for engagement and team status.
5. Enrich email/contact from the resource pool `Assign` tab; Charter name takes priority over the pool name.
6. Append to `Daily Project Progress` and rebuild `Latest Member Status` using the existing engagement rules.
7. Replace `Run Errors` and append unique current-run errors to `Error History`.

Snapshot dates use `Asia/Dhaka`. Existing Google Sheets retry and cache behavior remains in use. Tracker links are optional and are neither validated nor opened. Active members without tracker rows do not produce tracker-related errors.

Audit columns now use `Project Status` and `Project URL`. Existing historical `ClickUp Status` and `ClickUp Task URL` columns are renamed during schema migration so their values remain intact. New Worqcircle project URLs are blank because the supplied API example does not establish a project UI URL.

Legacy tracker helper functions remain available but are not called by `main()`.

## Run

```bash
pip install -r requirements.txt
python resource_daily_activity_and_task_report.py
```

Running the script updates the configured Google Sheets. GitHub Actions runs it every hour at minute 40 and supports manual runs.
