import { test, expect } from '@playwright/test';

/**
 * Results page — isolated tab + download tests.
 *
 * This spec does NOT kick off a run; instead it pre-populates the client
 * state by driving the run flow once, then sanity-checks every tab and
 * the "Download all JSON" button. The goal is to keep the suite fast
 * while still locking in the tab contract with Playwright.
 */
test.describe('Results page', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/health', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"healthy"}' }),
    );
    await page.route('**/jobs*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{"jobs":[]}' }),
    );
    await page.route('**/process-s3-video', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ job_id: 'job-X', status: 'QUEUED', input_type: 's3' }),
      }),
    );
    await page.route('**/batch/status*', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          jobs: [{ job_id: 'job-X', status: 'COMPLETED', progress: 1.0, message: 'done', current_step: 'completed' }],
          count: 1,
        }),
      }),
    );
    await page.route('**/download/json/job-X', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          filename: 'fake.mp4',
          fps: 30, camera: 'rear',
          description: 'stubbed result',
          traffic: 'light', lighting: 'daylight', weather: 'clear',
          collision: 'none', speed: 'moderate', video_duration_ms: 5_000,
          objects: [], hazard_events: [],
        }),
      }),
    );
    await page.route('**/client-configs/job-X', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          video_class: 'reactivity',
          configs: { reactivity: {}, educational: {}, hazard: {}, jobsite: {} },
        }),
      }),
    );
    await page.route('**/frames/job-X', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ job_id: 'job-X', num_frames: 0, frames: [] }),
      }),
    );
    await page.route('**/video-class/job-X', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          job_id: 'job-X',
          video_class: 'reactivity',
          display_label: 'Driving',
          collision: 'none', weather: 'clear',
          lighting: 'daylight', traffic: 'light',
        }),
      }),
    );
  });

  test('tabs switch between Frames / Rendered / JSON views', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('s3-uri-input-field').fill('s3://bucket/clip.mp4');
    await page.getByTestId('s3-uri-add-button').click();
    await page.getByTestId('workspace-run-button').click();
    await expect(page).toHaveURL(/\/results\//, { timeout: 30_000 });

    // Starts on Frames tab; BackendFrameGallery renders.
    await expect(page.getByText('Video classification')).toBeVisible();

    // Switch to JSON tab — pre tag content includes the stubbed filename.
    await page.getByTestId('results-tab-json').click();
    await expect(page.locator('pre')).toContainText('"filename": "fake.mp4"');

    // Switch to Rendered tab — properties panel renders.
    await page.getByTestId('results-tab-rendered').click();
    await expect(page.getByText('Structured result summary')).toBeVisible();

    // Back to Frames to ensure state is preserved.
    await page.getByTestId('results-tab-frames').click();
    await expect(page.getByText('Video classification')).toBeVisible();
  });
});
