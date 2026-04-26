# Lightship MVP Baseline Inventory

- total predictions inspected: **6**

| filename | duration | fps | camera | traffic | weather | lighting | objects | hazards | class |
|---|---:|---:|---|---|---|---|---:|---:|---|
| `brown_2025-08-14_PKG_NC1-C.mp4` | 11.5s | 10.0 | unknown | unknown | unknown | unknown | 5 | 0 | reactivity |
| `final_test.mp4` | 5.0s | 30.0 | unknown | unknown | unknown | unknown | 0 | 0 | reactivity |
| `plr_snow_4818293461-C.mp4` | 22.5s | 30.040880566830904 | unknown | unknown | unknown | unknown | 0 | 0 | reactivity |
| `test_dashcam.mp4` | 5.0s | 30.0 | unknown | unknown | unknown | unknown | 0 | 0 | reactivity |
| `test_e2e.mp4` | 3.0s | 10.0 | unknown | light | clear | daylight | 5 | 0 | reactivity |
| `verify_e2e.mp4` | 5.0s | 30.0 | unknown | unknown | unknown | unknown | 0 | 0 | reactivity |

Each prediction was validated against `VideoOutput` (pydantic) and piped through
`config_generator.generate_client_configs` to confirm schema end-to-end.
