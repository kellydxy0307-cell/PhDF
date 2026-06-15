# UI Assets

This folder contains the runtime image assets used by the desktop floating-ball UI.

## Active assets

- `floating_ball_60.png`
- `expand_settings_1_2x.png`
- `expand_close_1_2x.png`
- `api_url.png`
- `api_key.png`
- `model_name.png`
- `temperature.png`
- `request_timeout.png`
- `save_settings.png`
- `clear_settings.png`
- `gif/collapsed_loading_ball_60.png`
- `gif/expanded_loading_bg.png`
- `gif/loading_frames/*.png`
- `gif/expand_frames/*.png`

## Notes

- The app loads these files from `phdfloating/app_ui.py`.
- Runtime image scaling uses an alpha-safe resize path to avoid dark fringes on transparent edges.
- If you replace UI assets, keep the transparent background and preserve the expected canvas size or aspect ratio.
