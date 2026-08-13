# Resource Daily Activity and Task Report Workflow

## Short Version

The workflow pulls ClickUp projects, reads each project’s Charter and Task Tracker Google Sheets, joins them by `QAI ID`, and generates daily activity rows.

For each eligible project it:

1. Reads project metadata from ClickUp.
2. Skips projects whose PDL email is on the ignore list.
3. Loads the Charter sheet and builds `QAI ID -> charter status` and `QAI ID -> name` maps.
4. Loads the Task Tracker sheet and extracts only today’s Bangladesh-time activity.
5. Adds annotation/QC counts and accuracy data.
6. Appends normalized rows to `Daily Project Progress` in the local resource pool sheet.
7. Refreshes `Latest Member Status` from the accumulated progress data.
8. Writes any issues to the `Run Errors` tab in the audit sheet.

The workflow is designed to be run manually or through GitHub Actions.

---

## Detailed Workflow

### Purpose

The workflow produces a daily operational snapshot for resources and projects by combining:

- ClickUp project metadata
- Charter team membership/status data
- Task Tracker daily activity data
- Local resource pool member details

The output is written back into Google Sheets for reporting and auditability.

### Criteria

This is the inclusion logic that drives the workflow:

- A project is processed when its ClickUp status is in the target set.
- A project is skipped when its PDL email matches the ignored list.
- A charter row is included only when `Type` is `remote`.
- A member is retained in the daily report when it has a valid `QAI ID` in the charter membership map.
- “Today” means the current date in `Asia/Dhaka`.

Partial data does not always stop the workflow. If a required reference is missing, the workflow records an error and continues wherever possible.

### Main Inputs

The workflow depends on these inputs:

- `CLICKUP_API_TOKEN`
- `GOOGLE_SERVICE_ACCOUNT_JSON` or a local `service_account.json`
- `AUDIT_SHEET_URL`
- `LOCAL_RESOURCE_POOL_URL`

Optional runtime controls:

- `PROJECT_NAME_FILTER`
- `HIGHLIGHT_TRACKER_ERRORS` is read from the environment, but it is not currently used in the active execution path
- `RESOURCE_LOOKUP_TAB` is read from the environment, but the current code still uses the `Assign` tab for the local resource pool lookup

Default and fallback behavior:

- If `GOOGLE_SERVICE_ACCOUNT_JSON` is not set, the workflow falls back to a local `service_account.json` file when present.
- If `PROJECT_NAME_FILTER` is empty, all matching ClickUp projects are processed.
- `HIGHLIGHT_TRACKER_ERRORS` defaults to `true` when not provided.
- The service account JSON can be provided as raw JSON, a file path, or base64-encoded JSON.

### Main Output Sheets

The workflow updates these sheets/tabs:

- `Daily Project Progress` in the local resource pool spreadsheet
- `Latest Member Status` in the local resource pool spreadsheet
- `Run Errors` in the audit spreadsheet
- `Error History` in the audit spreadsheet

It also reads these source tabs:

- ClickUp list `900201326056`
- Charter sheet tab `Team Member Details`
- Tracker summary tab `Datewise Summary`
- Tracker dashboard tab `Dashboard`
- Local resource pool reference tab `Assign`

### Retry, Cache, and Access Behavior

The workflow uses in-memory caches for the current run:

- `spreadsheet_cache` avoids reopening the same Google Spreadsheet URL
- `worksheet_cache` avoids reopening the same worksheet tab
- `local_resource_pool_cache` avoids re-parsing the local resource pool sheet

Google Sheets reads are retried on quota errors with backoff.

If a sheet is inaccessible, the code tries to log a readable message that often includes the service account email so the sheet can be shared correctly.

### Error Log Behavior

- `Run Errors` is the current-run view. It is replaced on every ETL run and
  contains only unique errors from that run.
- `Error History` is append-only. It keeps the unique errors from every ETL run
  with a `Run ID`, timestamp, project context, ClickUp/Charter/Task Tracker
  URLs, reason type, QAI ID, and Charter row when available.
- Charter validation errors use explicit reason types: `missing_name`,
  `missing_qai_id`, `invalid_qai_id_format`, and `missing_status`.

### High-Level Execution Order

The entry point is `main()`.

#### 1. Load configuration

`load_runtime_config()` reads environment variables, resolves service account JSON, and validates required settings.

#### 2. Authenticate Google Sheets

`authenticate_google()`:

- writes the service account JSON to a temporary file
- authorizes `gspread`
- opens the audit spreadsheet

#### 3. Fetch ClickUp tasks

`fetch_clickup_tasks()` paginates through the ClickUp list and retrieves all tasks, including closed and archived ones.

#### 4. Optional project filtering

`filter_tasks_for_test_run()` limits processing to one exact project name if `PROJECT_NAME_FILTER` is set.

#### 5. Keep only target project statuses

The workflow processes only projects whose ClickUp status is one of:

- `project received`
- `project in progress`
- `pending schedule approval`

#### 6. Loop through each project

For every matching project, the workflow:

- captures audit context such as ClickUp status, PDL, and delivery lead
- skips the project if its PDL email is in the ignored list
- reads the `Project Charter Link`, `Task/Progress Tracker`, and `Client Alias` custom fields
- records missing fields as errors

#### 7. Read charter data

If a charter URL exists, `get_qai_id_and_status_pair()` opens the charter spreadsheet and reads the `Team Member Details` tab.

It expects the charter sheet to contain:

- `QAI ID`
- `Name` or `Full Name`
- `Status`
- `Type`

`Name` or `Full Name` becomes the resource name for new report rows.

For remote members, the Charter must provide both a name and a valid QAI ID in
the exact format `QAI_XX123` (for example, `QAI_DK1001`). Invalid or incomplete
member rows are excluded from the daily report and recorded in `Run Errors`.

Only rows where `Type == remote` are kept.

The result is a map:

- key: `QAI ID`
- value: charter status

This step also records issues such as:

- missing `QAI ID`
- missing `Status`
- missing required charter columns
- inaccessible charter sheets

Implementation detail:

- only rows where `Type` is exactly `remote` are used in the status map
- if `QAI ID` is missing, that row is logged and skipped
- if `Status` is missing, the row is still recorded in the map with an empty status so downstream logic can continue

#### 8. Read tracker data

If tracker data is available, `get_tracker_data()` opens the tracker spreadsheet and reads the `Datewise Summary` tab.

It then:

- filters rows to the current Bangladesh date
- aggregates task counts per `QAI ID`
- marks whether tracker activity happened today
- adds annotation accuracy and QC accuracy from the `Dashboard` tab

Implementation detail:

- the tracker date comparison uses `Asia/Dhaka`
- the activity detection treats any of the following as activity: explicit `Yes`, annotation count greater than zero, QC count greater than zero, annotator present, or reviewer present
- accuracy values are read from the dashboard and formatted as percentage strings when present

#### 9. Build the daily activity report

`build_today_activity_report()` produces one row per charter-side remote resource.

For each `QAI ID`, it writes:

- `QAI ID`
- `Engagement (today)`
- `Charter Team Status`
- `Task Tracker Activity (today)`
- `Today Task Tracker Activity (Annotation)`
- `Today Task Tracker Activity (QC)`
- `Annotation Accuracy (today)`
- `QC Accuracy (today)`

Important behavior:

- Charter members are included even if they are no longer active.
- If a member is currently active in the charter but missing from today’s tracker rows, that is logged as an error.
- `Engagement (today)` and `Charter Team Status` both start from the charter status, then downstream logic may adjust them later in the pipeline.

#### 10. Enrich with project metadata

The generated activity dataframe is then augmented with:

- `Client Alias`
- `Project Name`
- `Start Date`
- `Task Tracker Link`
- `Ticket Status`

#### 11. Append to daily project progress

`append_daily_project_progress()`:

- looks up member metadata from the local resource pool `Assign` tab
- converts the activity dataframe into the canonical progress schema
- appends rows to `Daily Project Progress`

The appended row includes:

- snapshot timestamp/date/time in Asia/Dhaka
- project identity fields
- member identity fields
- tracker metrics
- accuracy metrics

Before append, the function also:

- ensures the destination worksheet exists
- expands row and column counts when needed
- rewrites the sheet header if the existing headers do not match the canonical schema

The local resource pool lookup expects at least these columns:

- `QAI ID`
- `Full Name`
- `Email`
- `Contact`

#### 12. Refresh latest member status

After all projects are processed, `refresh_latest_member_status_sheet()` rebuilds `Latest Member Status` from `Daily Project Progress`.

It:

- keeps the latest row per `QAI ID` and project
- replaces ticket status using the latest ClickUp project status map
- resolves final engagement using charter and ticket status rules
- fills blank `Email` and `Contact` fields from the local resource pool `Assign`
  tab, without changing existing values or `Daily Project Progress` history

Engagement resolution rule:

- if the existing engagement is `Removed`, it stays `Removed`
- if the ticket is not in a target status and charter status is `active`, `inactive`, or `offboarded`, engagement becomes `Worked`
- otherwise the current engagement value is preserved

#### 13. Write audit log

Finally, `write_audit_log()` uploads all captured issues to the `Run Errors` tab.

Each audit row includes:

- run timestamp
- project
- ClickUp status
- PDL
- delivery lead
- reason type
- issue text

`Reason Type` is derived from the issue text using keyword classification, for example:

- quota and `429` messages become `quota_limit`
- permission problems become `permission_issue`
- missing columns become `missing_column`
- missing tracker rows become `missing_activity`
- missing fields become `missing_field`

---

## Data Flow By Function

### ClickUp layer

- `fetch_clickup_tasks()`
- `filter_tasks_for_test_run()`
- `get_custom_field_value()`
- `get_task_start_date()`

These functions read project-level metadata and custom fields from ClickUp.

### Charter layer

- `get_qai_id_and_status_pair()`

This reads charter membership and status information from Google Sheets.

### Tracker layer

- `get_tracker_data()`
- `build_today_activity_report()`
- `get_accuracy_from_dashboard()`

These functions read daily operational activity and accuracy metrics from the tracker workbook.

### Resource pool layer

- `get_local_resource_pool_context()`
- `get_member_reference_lookup()`
- `append_daily_project_progress()`
- `refresh_latest_member_status_sheet()`

These functions enrich the report with member identity data and maintain the reporting sheets.

### Audit layer

- `add_project_error()`
- `write_audit_log()`
- `classify_issue_type()`

These functions collect and persist run-time errors for later review.

### Formatting and utility helpers

- `normalize_service_account_json()`
- `normalize_field_value()`
- `format_clickup_date_value()`
- `normalize_qai_id()`
- `clean_sheet_text()`
- `clean_sheet_value()`
- `resolve_dataframe_columns()`

These helpers normalize inputs so the workflow can survive slightly inconsistent data coming from ClickUp and Google Sheets.

### Present but not central to the main flow

- `highlight_error_cell()`
- `batch_highlight_errors()`
- `normalize_date_columns()`
- `convert_to_number()`
- `load_resource_type_lookup()`
- `RESOURCE_LOOKUP_TAB`
- `HIGHLIGHT_TRACKER_ERRORS`

These are available in the module, but the current main workflow does not rely on them heavily or at all.

---

## Key Rules and Assumptions

- Time-based filtering uses Bangladesh time (`Asia/Dhaka`).
- Only ClickUp projects in target statuses are processed.
- Projects tied to ignored PDL emails are skipped.
- Charter rows are only considered when `Type` is `remote`.
- Tracker activity is built from rows dated today in Bangladesh time.
- A project can still generate audit errors even if output rows are not produced.
- The local resource pool is used to enrich `QAI ID` rows with `Full Name`, `Email`, and `Contact`.
- The workflow is biased toward continuing after partial failures rather than aborting immediately.

---

## Error Handling

The workflow is intentionally tolerant of partial failures.

Common failure cases:

- missing env vars
- ClickUp API failure
- Google Sheets permission errors
- Google Sheets quota limits
- missing charter/tracker tabs
- missing required columns
- incomplete project fields

When failures occur:

- the project is logged in `all_errors`
- the issue is categorized when possible
- processing continues for other projects
- the audit sheet is updated at the end of the run

Typical failure categories include:

- ClickUp HTTP failure
- Google Sheets permission error
- Google Sheets quota limit
- missing charter or tracker worksheet
- missing required sheet columns
- missing project custom fields
- empty or incomplete activity data

---

## Practical Run Notes

When this runs successfully, the expected operational sequence is:

1. Fetch project list from ClickUp.
2. For each eligible project, build a daily QAI activity snapshot.
3. Append the snapshot to `Daily Project Progress`.
4. Recompute `Latest Member Status`.
5. Save all issues to `Run Errors`.

If you are debugging a specific project, set `PROJECT_NAME_FILTER` to an exact ClickUp project name.

## Implementation Notes

This section calls out a few details another engineer may want to know immediately.

### ClickUp project selection

- The workflow fetches all tasks from the configured ClickUp list, then keeps only tasks whose status is in the target status set.
- It also includes closed and archived tasks so older projects remain visible if they still match the status filter.

### Member identity resolution

- The Charter is the source of truth for `Full Name` when it provides a `Name`
  or `Full Name` value; the local resource pool is the fallback for the name
  and remains the source of truth for `Email` and `Contact`.
- `QAI ID` matching is normalized so formatting differences do not break joins.

### Date handling

- Snapshot timestamps are generated in `Asia/Dhaka`.
- Tracker row filtering also uses `Asia/Dhaka`, so “today” is consistent across the workflow.

### Sheet schema handling

- Several helper functions normalize headers and values to survive minor sheet formatting changes.
- If a destination sheet already exists with a mismatched header row, the workflow rewrites it to the canonical schema.

### Commented legacy code

- There is a commented-out `update_local_resource_pool_sheet()` block in the workflow implementation.
- It looks like an older append-only implementation and is not part of the active workflow.
