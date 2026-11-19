# ViralClip AI

A self-hosted creator workflow for importing permitted video, rendering vertical candidate excerpts, and submitting reviewed clips through TikTok's Content Posting API.

## Deploy
1. In Render, create a Blueprint from this repository and select `render.yaml` (or create a Docker web service from the repo).
2. Set `APP_USERNAME` and a strong `APP_PASSWORD`.
3. Add TikTok developer credentials as described below.
4. Set the TikTok app's redirect URL to the exact public URL plus `/oauth/tiktok/callback`.

## TikTok setup
Create/configure your own TikTok developer app and request the Content Posting API scopes `user.info.basic` and `video.publish`. Set:
- `TIKTOK_CLIENT_KEY`
- `TIKTOK_CLIENT_SECRET`
- `TIKTOK_REDIRECT_URI`

TikTok must approve the app and scopes. Posting can be restricted to private/test visibility until TikTok audits the app. TikTok may process or review a post after upload. Do not share secrets in chat or commit them to GitHub.

## Important
- Use only videos you own or have permission to reuse.
- Candidate clips are evenly sampled excerpts, not AI-detected viral moments. Review before posting.
- Render's free service filesystem is ephemeral and free instances may sleep. Add persistent storage only after choosing a paid plan; video files can otherwise disappear after restarts.
- Uploads are limited to 2 GB in the application.
