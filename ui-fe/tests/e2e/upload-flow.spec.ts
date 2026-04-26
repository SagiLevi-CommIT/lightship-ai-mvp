import { test, expect } from '@playwright/test';

/**
 * Upload flow — locked-in user journey
 *
 * 1. Navigate to the landing page.
 * 2. Verify the upload dropzone is present and the Run button is disabled
 *    (no assets yet).
 * 3. Verify Browse opens the native file picker.
 * 4. Add a tiny MP4 via file input.
 * 5. Verify the queue item appears with filename + size.
 * 6. Verify the Run button becomes enabled.
 *
 * Backend calls are intercepted so the test has no AWS dependency — it
 * exercises the UI state machine end-to-end without needing a deployed
 * Lambda.
 */
test.describe('Upload flow', () => {
  test.beforeEach(async ({ page }) => {
    // Silence unrelated backend calls at the boundary so the UI doesn't
    // flip into an error banner if the backend isn't reachable during
    // local test runs.
    await page.route('**/health', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"healthy"}' }),
    );
    await page.route('**/jobs*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{"jobs":[]}' }),
    );
  });

  test('landing page renders and Run is disabled without assets', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByTestId('upload-dropzone')).toBeVisible();

    const runButton = page.getByTestId('workspace-run-button');
    await expect(runButton).toBeVisible();
    await expect(runButton).toBeDisabled();
  });

  test('clicking the dropzone opens Browse', async ({ page }) => {
    await page.goto('/');

    const chooserPromise = page.waitForEvent('filechooser');
    await page.getByTestId('upload-dropzone').click();
    const chooser = await chooserPromise;

    expect(chooser.isMultiple()).toBe(true);
  });

  test('adding a video file enables Run and shows the queue item', async ({ page }) => {
    await page.goto('/');

    const dropzone = page.getByTestId('upload-dropzone');
    const fileInput = dropzone.locator('input[type="file"]');

    // Fake MP4 bytes — real MP4 header so `video/mp4` sniffing succeeds on Chromium.
    const mp4Bytes = Buffer.from(
      '00000020667479706d703432000000006d703432697361340000000866726565',
      'hex',
    );
    await fileInput.setInputFiles([
      {
        name: 'tiny.mp4',
        mimeType: 'video/mp4',
        buffer: mp4Bytes,
      },
    ]);

    await expect(page.getByText('tiny.mp4', { exact: false }).first()).toBeVisible();

    const runButton = page.getByTestId('workspace-run-button');
    await expect(runButton).toBeEnabled({ timeout: 5_000 });
  });

  test('dropping a video with no browser MIME type still adds it to the queue', async ({ page }) => {
    await page.goto('/');

    const dropzone = page.getByTestId('upload-dropzone');
    const dataTransfer = await page.evaluateHandle(() => {
      const bytes = new Uint8Array([
        0x00, 0x00, 0x00, 0x20, 0x66, 0x74, 0x79, 0x70,
        0x6d, 0x70, 0x34, 0x32, 0x00, 0x00, 0x00, 0x00,
      ]);
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], 'dashcam.mkv', { type: '' }));
      return transfer;
    });

    await dropzone.dispatchEvent('dragenter', { dataTransfer });
    await dropzone.dispatchEvent('dragover', { dataTransfer });
    await dropzone.dispatchEvent('drop', { dataTransfer });

    await expect(page.getByText('dashcam.mkv', { exact: false }).first()).toBeVisible();
    await expect(page.getByTestId('workspace-run-button')).toBeEnabled();

    await dataTransfer.dispose();
  });

  test('adding an S3 URI enables Run', async ({ page }) => {
    await page.goto('/');

    const input = page.getByTestId('s3-uri-input-field');
    await input.fill('s3://lightship-mvp-processing/input/videos/existing.mp4');
    await page.getByTestId('s3-uri-add-button').click();

    await expect(page.getByText('existing.mp4', { exact: false }).first()).toBeVisible();
    await expect(page.getByTestId('workspace-run-button')).toBeEnabled();
  });
});
