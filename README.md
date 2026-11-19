# ViralClip AI

A self-hosted creator studio for importing videos you own or are permitted to reuse, finding candidate short-form moments, rendering vertical clips, and submitting clips through TikTok's Content Posting API.

## What it does
- Upload MP4, MOV, WEBM and MKV files or import a YouTube URL.
- When OPENAI_API_KEY is configured, transcribe audio with Whisper and ask GPT-4o mini to rank short-form moments and suggest captions/hashtags.
- Without an OpenAI key, create balanced sample excerpts instead. These are not AI-ranked.
- Render 9:16 clips with FFmpeg and preview/download them.
- Connect your own TikTok developer app and publish only after a manual review and click.
- Store TikTok OAuth token data encrypted at rest using TOKEN_ENCRYPTION_KEY.

## Render configuration
The service is configured for Render's free web-service plan. Free filesystems are ephemeral; uploaded videos and connected tokens can be lost after restarts. Persistent disks require a paid plan. Do not upgrade automatically.

Required environment variables:
- APP_USERNAME
- APP_PASSWORD
- TOKEN_ENCRYPTION_KEY (Fernet key; use a generated value compatible with Python cryptography Fernet)

Optional:
- OPENAI_API_KEY (enables transcript-based AI highlight ranking; OpenAI usage is billed separately)

TikTok:
- TIKTOK_CLIENT_KEY
- TIKTOK_CLIENT_SECRET
- TIKTOK_REDIRECT_URI

Set the exact redirect URI in your TikTok developer app to:
https://YOUR-RENDER-DOMAIN/oauth/tiktok/callback

Request TikTok scopes user.info.basic and video.publish. TikTok must approve the app/scopes; posting may remain private-only until its audit. TikTok may review or delay posts.

## Safety and limitations
- Only use videos you own or have permission to edit and publish.
- AI ranking suggests promising moments; it cannot guarantee views or virality.
- Review every clip, caption, hashtags, privacy setting and rights before publishing.
- TikTok API availability and account eligibility are controlled by TikTok.
- Upload size is limited to 1 GB.
