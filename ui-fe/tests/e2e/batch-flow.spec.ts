import { test, expect } from '@playwright/test';

/**
 * Batch flow — the whole UI → /batch/status → results journey.
 *
 * Everything hits the UI boundary; the backend is faked entirely via
 * `page.route`. This lets us lock in the new Phase 4 batch code path
 * (parallel submit, single-fetch polling, Frames/Rendered/JSON tabs)
 * without needing a deployed pipeline.
 */
test.describe('Batch flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/health', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"healthy"}' }),
    );
    await page.route('**/jobs*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{"jobs":[]}' }),
    );

    // Two S3 URIs → two job_ids, both complete on the first /batch/status.
    let nextJobId = 1;
    await page.route('**/process-s3-video', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: `job-${nextJobId++}`, status: 'QUEUED', input_type: 's3' }),
      }),
    );
    await page.route('**/batch/status*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jobs: [
            { job_id: 'job-1', status: 'COMPLETED', progress: 1.0, message: 'done', current_step: 'completed' },
            { job_id: 'job-2', status: 'COMPLETED', progress: 1.0, message: 'done', current_step: 'completed' },
          ],
          count: 2,
        }),
      }),
    );
    await page.route('**/download/json/job-*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          filename: 'fake.mp4',
          fps: 30,
          camera: 'rear',
          description: 'stubbed result',
          traffic: 'light',
          lighting: 'daylight',
          weather: 'clear',
          collision: 'none',
          speed: 'moderate',
          video_duration_ms: 5_000,
          objects: [],
          hazard_events: [],
        }),
      }),
    );
    await page.route('**/client-configs/job-*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ video_class: 'reactivity', configs: {
          reactivity: {}, educational: {}, hazard: {}, jobsite: {},
        } }) }),
    );
    await page.route('**/frames/job-*', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ job_id: 'job-1', num_frames: 0, frames: [] }),
      }),
    );
    await page.route('**/video-class/job-*', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          job_id: 'job-1',
          video_class: 'reactivity',
          display_label: 'Driving',
          collision: 'none',
          weather: 'clear',
          lighting: 'daylight',
          traffic: 'light',
        }),
      }),
    );
  });

  test('two S3 videos flow through run and results with tabs', async ({ page }) => {
    await page.goto('/');

    // Add two S3-backed assets.
    const input = page.getByTestId('s3-uri-input-field');
    const addBtn = page.getByTestId('s3-uri-add-button');
    for (const name of ['a.mp4', 'b.mp4']) {
      await input.fill(`s3://bucket/input/${name}`);
      await addBtn.click();
    }

    await expect(page.getByText('a.mp4').first()).toBeVisible();
    await expect(page.getByText('b.mp4').first()).toBeVisible();

    await page.getByTestId('workspace-run-button').click();

    // Results page should land with both files in the left rail.
    await expect(page).toHaveURL(/\/results\//, { timeout: 30_000 });
    await expect(page.getByText('a.mp4').first()).toBeVisible();
    await expect(page.getByText('b.mp4').first()).toBeVisible();

    // Tabs render and switch.
    await page.getByTestId('results-tab-json').click();
    await expect(page.locator('pre')).toContainText('"filename"');

    await page.getByTestId('results-tab-rendered').click();
    await expect(page.getByText('Structured result summary')).toBeVisible();

    await page.getByTestId('results-tab-frames').click();
  });
});
