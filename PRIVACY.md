# Privacy

ComfyUI EXIF Viewer does not include analytics, telemetry, advertising, accounts, or background update checks.

## Local data

Images and their metadata are processed locally. The app stores the recent-image list in `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\recent_images.json`, allowed image host settings in `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\settings.json`, and clipboard or explicitly downloaded images under `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\cache`.

`File > Clear Cache` removes the recent-image list and cache files created by the app. It does not delete original images outside the app cache.

## Network access

The app makes an image request only when the user explicitly drops or pastes an image URL whose HTTPS hostname matches `Settings > Allowed Image Sites`. The initial default is `ac-o.namu.la`; users can add exact hosts or `*.example.com` subdomain rules, remove rules, or use an empty list to disable URL downloads. Redirects outside the configured HTTPS host rules are rejected. Local images and their metadata are not uploaded by the app.

Clicking a confirmed Civitai resource opens its `https://civitai.red/...` page in the user's default browser. The browser and destination site then apply their own privacy policies.
