# Browser smoke test — single-video happy path

Validates the Phase 1 fixes without running any automated tests. Run from a
fresh browser session so IndexedDB / service workers don't mask bugs.

## Preconditions

- AWS credentials usable by the Lambda and the CloudFront/ALB URL (or any
  plain-HTTP host). Confirm by visiting `http://<alb-dns>/health` and
  observing `{"status":"healthy"}`.
- The deployed backend has the `LambdaSelfInvoke` policy and the
  `_write_progress` helper (this commit). Run
  `aws logs tail /aws/lambda/lightship-mvp-backend --since 5m` in a side
  terminal.
- A short test MP4 (<= 30 seconds, <= 30 MB) on your local machine.

## Test matrix

| Browser           | URL scheme | Expected result |
| ----------------- | ---------- | --------------- |
| Chrome (incognito)| `http://`  | Full flow works |
| Firefox           | `http://`  | Full flow works |
| Safari / WebKit   | `http://`  | Full flow works |

`http://` (not only `https://`) is the critical scenario. Prior to Phase 1,
`crypto.randomUUID()` was undefined in a non-secure context and threw before
the asset could be queued.

## Steps

1. **Open the UI** at `http://<alb-dns>/`.
2. **Drag and drop** the test MP4 into the upload zone. Acceptance: the
   queue card appears with file name, size, and a video preview. No red
   error banner. No `TypeError: crypto.randomUUID is not a function` in
   DevTools console.
3. **Click Run** in the right sidebar. Acceptance: router navigates to
   `/run`. Progress bar is visible.
4. **Observe progress**. The bar should show discrete jumps (approx. 10%,
   30%, 90%, 100%) rather than staying at 0 until completion.
   - In DevTools → Network, filter on `/status`. Each poll response body
     should include `"progress"` strictly greater than `0.0` once the
     pipeline has started processing, even if a *different* Lambda instance
     handles the poll.
   - Confirm cross-invocation correctness: run
     `aws logs filter-log-events --log-group-name /aws/lambda/lightship-mvp-backend --filter-pattern "DynamoDB update OK"`
     and confirm there are at least 3 update lines for the `job_id`
     (`init`, `processing`, `finalize` steps) before `COMPLETED`.
5. **Navigate to the results page** (automatic on completion). Acceptance:
   - Frames gallery renders with at least one annotated thumbnail.
   - "Download JSON" produces a valid `output.json` that parses as JSON.
   - The rendered properties panel shows non-empty rows.
6. **Refresh the results page.** Acceptance: frames and JSON still load
   (this exercises the S3 cold-start path, not the in-memory cache).

## Failure triage

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| `crypto.randomUUID is not a function` in console | Running against stale UI build | Redeploy the ECS frontend (see `infrastructure/deploy.sh` frontend step) |
| `/status` returns `progress: 0` for 30+ seconds | Progress not persisted to Dynamo | Confirm backend Lambda was deployed after the `_write_progress` commit |
| `Worker dispatch failed: AccessDenied` | Self-invoke IAM not applied | Re-deploy `app-stack.yaml` to pick up `LambdaSelfInvoke` policy |
| Frames gallery empty on refresh | S3 `frames_manifest.json` missing | Check CloudWatch log for `frames_manifest.json upload failed` warning |

## Exit gate

All six steps pass on both Chrome (HTTP) and Firefox (HTTP) before Phase 1
is considered complete.
