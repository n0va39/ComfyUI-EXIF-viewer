# Privacy

ComfyUI EXIF Viewer does not include analytics, telemetry, advertising, accounts, or background update checks.

## Local data

Images and their metadata are processed locally. The app stores the recent-image list in `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\recent_images.json` and stores clipboard or explicitly downloaded images under `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\cache`.

`File > Clear Cache` removes the recent-image list and cache files created by the app. It does not delete original images outside the app cache.

## Network access

The app makes an image request only when the user explicitly drops or pastes an allowed `https://ac-o.namu.la/...` image URL. Redirects outside that HTTPS host are rejected. Local images and their metadata are not uploaded by the app.

Clicking a confirmed Civitai resource opens its `https://civitai.red/...` page in the user's default browser. The browser and destination site then apply their own privacy policies.
