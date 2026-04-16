# User Stories

## Story 1: Branded Application Shell And Navigation
**As a user, I want to access the Lightship interface through a branded application shell, so that I can clearly understand where I am and move between the main workflow areas.**

### In scope
- Lightship logo in the top-left header
- Browser tab icon uses the Lightship logo
- Top navigation with `Run New Pipeline`
- Top navigation with `Pipeline Historical Runs`
- Contextual `Back` action on workflow/result pages
- Consistent dark branded visual theme across pages

### Out of scope
- Role-based navigation
- Multi-tenant branding
- Dynamic menu configuration from backend
- Mobile-specific navigation redesign beyond responsive behavior

### Acceptance criteria
1. **Entry point:** user opens the application root URL.
2. The user sees the Lightship branded header with logo in the top-left area.
3. The browser tab displays the Lightship logo instead of a default framework icon.
4. The user sees a top navigation bar containing:
   - `Run New Pipeline`
   - `Pipeline Historical Runs`
5. When the user clicks `Run New Pipeline`, the app navigates to the main pipeline workspace.
6. When the user clicks `Pipeline Historical Runs`, the app navigates to the historical runs page.
7. On run-processing and results pages, the user sees a `Back` action positioned in the top navigation area.
8. The visual theme remains consistent across main, run, results, evaluation, and history screens.

## Story 2: Configure And Run A New Batch Pipeline
**As a user, I want to upload videos, configure the pipeline, and launch processing from one workspace, so that I can quickly run a new batch without switching between multiple screens.**

### In scope
- Main workspace page for starting a new run
- Batch and Evaluation mode toggle
- Video-only upload area for batch mode
- Horizontal upload queue for selected files
- Preview section that appears only after upload
- Left-side pipeline wizard/sidebar
- Frame selection method selection
- Native FPS input with visible `FPS` suffix
- S3 bucket path input
- Output frames by hazard severity selection
- Run and reset actions
- Front-end validation before run starts

### Out of scope
- Actual backend pipeline execution
- File upload to cloud storage
- Persisting draft workspace state across refresh
- Support for non-video upload in batch mode

### Acceptance criteria
1. **Entry point:** user lands on `Run New Pipeline`.
2. The user sees a mode toggle with `Batch Mode` and `Evaluation`.
3. When `Batch Mode` is selected, the user sees:
   - a full-width upload area below the hero/header
   - a persistent left sidebar wizard
4. The upload area accepts one or multiple video files.
5. After at least one video is uploaded:
   - a horizontal queue of uploaded videos appears
   - the preview section appears below the queue
6. When the user clicks a video in the queue, the preview updates to show that selected video only.
7. When the user clicks the `x` on a queued video, that video is removed from the queue.
8. In the left sidebar, the user can configure:
   - frame selection method: `Native` or `Scene change`
   - native FPS input when `Native` is selected
   - S3 bucket path
   - output frames by hazard severity
9. The native FPS input always shows `FPS` next to the numeric value.
10. If the user tries to run without uploaded videos, the system shows a validation message.
11. If the user tries to run with `Native` selected but no FPS value, the system shows a validation message.
12. If the user tries to run without an S3 path, the system shows a validation message.
13. When the user clicks `Run Detection Pipeline` with valid inputs, the app navigates to the processing screen.

## Story 3: Monitor Processing Progress
**As a user, I want to see pipeline progress while processing is running, so that I can understand the current stage and know when results are ready.**

### In scope
- Dedicated processing screen
- Per-stage progress visualization
- Per-asset status display in batch mode
- Evaluation progress display in evaluation mode
- Optional in-app notification guidance for batch mode
- `Back` action in top navigation

### Out of scope
- Real backend processing logs
- Retry failed stages
- Background processing across browser sessions
- Cancel/pause/resume controls

### Acceptance criteria
1. **Entry point:** user starts a valid batch or evaluation run from the main workspace.
2. The app opens a processing screen with the same branded shell and top navigation.
3. The user sees a progress card with:
   - current stage text
   - percent complete
   - completed item count
4. In batch mode, the user sees each uploaded asset listed with statuses such as queued, running, and completed.
5. In evaluation mode, the user sees a benchmark-style progress summary instead of file cards.
6. In batch mode, the user may see an informational notification about browser notifications while the page remains open.
7. When processing completes, the app automatically navigates to the correct results screen.

## Story 4: Review Batch Results And Download JSON Output
**As a user, I want to review processed batch results and download the generated JSON files, so that I can inspect detections and keep the machine-readable output.**

### In scope
- Batch completion popup
- Results page for batch mode
- Result file selector list
- Annotated frame gallery
- Structured output properties panel
- `Download all JSON files` action
- `Back` action in top navigation

### Out of scope
- Per-file JSON editing
- Raw JSON inline viewer
- ZIP packaging
- Sharing/exporting to third-party systems

### Acceptance criteria
1. **Entry point:** a batch run completes and the app opens the results page.
2. The user sees a completion popup indicating the process has completed.
3. When the user closes the popup, the batch results screen remains visible.
4. The results page shows a branded header and a top-right `Back` action in the header area.
5. The results summary section shows a single primary action: `Download all JSON files`.
6. The user does not see `Home` in the results action area.
7. The left side of the results content shows a selectable list of processed files from the run.
8. When the user selects a file, the main content updates to show:
   - annotated selected frames
   - structured output properties
9. The frame gallery visually represents detections, lanes, signs, and signals.
10. The properties panel displays each output property as a readable row rather than raw JSON.
11. When the user clicks `Download all JSON files`, the app downloads JSON output for all files in that run.

## Story 5: Run Evaluation Benchmark And Review Report
**As a user, I want to run an evaluation benchmark and view the final metrics report, so that I can assess pipeline quality against benchmark expectations.**

### In scope
- Evaluation mode from the main workspace toggle
- Evaluation information state on the workspace
- Evaluation processing flow
- Evaluation results page
- GT Coverage Summary
- Final metrics table
- `Back` action in top navigation

### Out of scope
- Editing evaluation datasets
- Uploading custom benchmark files
- Historical metric comparison charts
- Backend metric computation

### Acceptance criteria
1. **Entry point:** user lands on the main workspace and selects `Evaluation`.
2. The workspace updates to show evaluation-focused information instead of the batch upload flow.
3. The left sidebar remains visible and the main content explains the benchmark mode.
4. When the user clicks `Run Evaluation`, the app opens the processing screen.
5. The processing screen shows benchmark-oriented progress rather than file-by-file batch progress.
6. When evaluation finishes, the app navigates to the evaluation results page.
7. The evaluation results page shows:
   - GT Coverage Summary
   - Final metrics table
8. The user does not see the old multi-tab metric card layout.
9. The results are presented in the same branded style as the rest of the application.

## Story 6: Review Historical Pipeline Runs
**As a user, I want to browse previous pipeline runs and reopen their results, so that I can revisit outputs without rerunning the pipeline.**

### In scope
- Historical runs page
- Left sidebar menu for past runs
- Selection of a specific run
- Run video list for batch runs
- Reuse of results viewer for historical batch runs
- Reuse of evaluation report view for historical evaluation runs
- Download all JSON action for historical batch runs

### Out of scope
- Backend run history sync
- Search, filtering, or pagination
- Deleting historical runs
- Comparing two runs side by side

### Acceptance criteria
1. **Entry point:** user clicks `Pipeline Historical Runs` from the top navigation.
2. The app opens a historical runs page with the same branded shell.
3. The left side of the page shows a sidebar menu of past runs.
4. Each run item shows enough context to distinguish it, such as:
   - run identifier
   - mode
   - completion time
   - file count or evaluation label
5. When the user selects a run from the left sidebar, the right content updates for that run.
6. The page does not show an extra `Detection pipeline run` title section.
7. For batch runs:
   - `Download all JSON files` appears high in the content area
   - a horizontal `Run videos` list appears below
   - selecting a video shows annotated frames and structured output properties
8. For evaluation runs:
   - the evaluation report is shown instead of batch file content
9. If there are no historical runs, the page shows an empty state with a clear path back to `Run New Pipeline`.

## Story 7: Notification And Validation Feedback
**As a user, I want clear in-app feedback for validation and completion events, so that I can understand what is required and what has happened in the workflow.**

### In scope
- Validation banners/messages
- Notification banner component
- Batch completion popup
- Optional browser notification prompt for batch mode
- Dismiss behavior for in-app notifications

### Out of scope
- Email notifications
- Push notifications outside the browser
- Notification preferences center
- Notification history log

### Acceptance criteria
1. **Entry point:** user interacts with the app in a way that requires feedback.
2. If required configuration is missing, the app shows a clear in-app validation message.
3. If batch mode supports browser notifications, the user can request notification permission from the UI.
4. If the user dismisses a notification banner, it disappears from view.
5. When batch processing completes, the user receives a completion popup before reviewing results.
6. Completion and validation messages use the same visual language as the rest of the application.
