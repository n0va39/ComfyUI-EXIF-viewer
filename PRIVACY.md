# Privacy

ComfyUI EXIF Viewer does not include analytics, telemetry, advertising, accounts, or background update checks.

## Local data

Images and their metadata are processed locally. The app stores the recent-image list in `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\recent_images.json`, allowed image host settings in `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\settings.json`, and clipboard or explicitly downloaded images under `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\cache`.

`File > Clear Cache` removes the recent-image list and cache files created by the app. It does not delete original images outside the app cache.

## Network access

The app makes an image request only when the user explicitly drops or pastes an image URL whose HTTPS hostname matches `Settings > Allowed Image Sites`. The initial defaults are `ac-o.namu.la`, `civitai.com`, `civitai.red`, `image.civitai.com`, and Civitai's file-delivery host `image-b2.civitai.com`; users can add exact hosts or `*.example.com` subdomain rules, remove rules, or use an empty list to disable URL downloads. Redirects outside the configured HTTPS host rules are rejected. Local images and their metadata are not uploaded by the app.

For `civitai.com/images/<id>` and `civitai.red/images/<id>` links, the app requests the matching public `civitai.com` page to resolve its original `image.civitai.com` asset and public generation metadata. Direct `image.civitai.com` image links with an asset UUID are normalized to the same asset's `original=true` variant. When their filename also provides a numeric Civitai image ID, the app may request the matching public page and supplements metadata only if the page and URL asset UUIDs match.

Clicking a confirmed Civitai resource opens its `https://civitai.red/...` page in the user's default browser. The browser and destination site then apply their own privacy policies.
