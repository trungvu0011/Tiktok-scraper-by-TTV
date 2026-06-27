# TikTok Transcript Generator (Chrome Extension)

A lightweight extension that adds a **Get Transcript** button on TikTok video pages and calls:

- `POST /api/ext/v1/transcript`

## Files

- `manifest.json` - Extension config (MV3)
- `content.js` - Injects button on TikTok video pages and calls API
- `content.css` - Floating button/panel styles
- `popup.html` + `popup.js` - Configure API base URL and extension token

## Install (Developer Mode)

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select this folder: `tiktok-transcript-generator`

## Configure

Click extension icon, set:

- `API Base URL` (for example `http://127.0.0.1:3010` in local dev)
- `Extension Token` (must match server `EXTENSION_PLUGIN_TOKEN`)

## Usage

1. Open a TikTok video page, e.g. `https://www.tiktok.com/@xxx/video/123`
2. Click **Get Transcript** (bottom-right floating button)
3. Transcript text appears in panel
4. Click **Copy** to copy transcript

## API Contract Used

Request body sent by extension:

```json
{
  "url": "<current tiktok page url>",
  "language": "auto",
  "includeFormats": ["txt"]
}
```

Required header:

- `x-extension-token: <token>`
