# Joo Films Studio

The original portfolio is the baseline. Studio stores editable content separately, and the server applies published edits to the original markup. The Studio is available at `/admin`.

## Run locally

Requires Python 3.9 or later. Run in the repository root.

```sh
export CMS_LOCAL_DB=/tmp/joo-films-preview.sqlite
printf 'Studio password (20+ characters) '
read -rs CMS_ADMIN_PASSWORD
printf '\n'
export CMS_ADMIN_PASSWORD
python3 api/cms.py
```

Open [local Studio](http://127.0.0.1:8765/admin). Local previews and publishing use the SQLite file at `CMS_LOCAL_DB`.

Run the isolated suite with `python3 -B cms_test.py`. It exercises requests through the same handler and uses a temporary SQLite database.

## Vercel preview

1. Use the `joo-films-admin-staging` branch with the existing Vercel project. Verify that Production Branch is `main` and that Preview Deployment Protection is enabled if available.
2. Add `CMS_ADMIN_PASSWORD`, `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` to the Preview environment for this branch. Use a preview database and keep the password and token in Vercel's secret fields. The Vercel Upstash integration supplies `KV_REST_API_URL` and `KV_REST_API_TOKEN`, which Studio also accepts. Use a randomly generated password of at least 20 characters.
3. Redeploy the branch as Preview and open `/admin` on the preview URL. Create a draft edit, check the public page stays at its published revision, preview the draft, publish to the preview environment, then restore a snapshot and confirm the preview publication.
4. Test actual YouTube and Vimeo playback on desktop and mobile. Provider embedding and unlisted links can have domain or privacy restrictions.

`CMS_ENABLE_PRODUCTION=yes` explicitly enables the Studio backend in Vercel Production. Production has its own database credentials and password. Environment and preview branch are part of each storage key. Preview pages and authenticated drafts include no-index headers.

## Editing and recovery

- Use Page builder to edit existing sections, hide or reorder them, and add sections or pages. Added pages live at `/pages/<address>`. Choose a section inside the preview to open its inspector.
- Use Videos & logos to search the current galleries and brand grid. Add images through HTTPS URLs or site-relative paths. Add videos through YouTube or Vimeo URLs. The gallery determines the aspect ratio for items added to an existing collection.
- Save draft stores edits. Preview saves and refreshes the draft canvas. Publish updates the current environment and saves the previous published content as a snapshot.
- The Home background playlist supports YouTube and Vimeo, sequence or shuffle, per-clip start and display duration, and black overlay opacity. Reset restores the original montage.
- Analytics counts browser page views and outbound portfolio, WhatsApp, email and Instagram clicks over 30 days. It excludes Studio preview and respects browser privacy signals.
- Backups keeps the latest 25 snapshots. Download JSON for offline retention. Restore or import loads a draft, which you can review and publish. Backup files contain content configuration. Linked assets stay at their providers.
