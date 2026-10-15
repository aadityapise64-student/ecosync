# EcoSync

EcoSync is a Flask-based waste-management application.

## Configuration

Set `SECRET_KEY` to a strong, private value before starting the application. Copy `.env.example` to `.env` for local configuration and replace the placeholder secret. The app accepts `DATABASE_URL`; otherwise, it uses SQLite in the local `instance` directory. Set `SESSION_COOKIE_SECURE=true` when serving the site over HTTPS.

## Live Roboflow scanner

The scanner uses the official `inference-sdk` Python client to call the Roboflow Workflow from the Flask server. The configured workflow is `aaditya-pise/general-segmentation-api-7`, with the `image` input and the `classes` parameter. The SDK sends the API key in the Authorization header. Its result is parsed recursively because workflow output names are defined by the workflow and can change; only compact class, confidence, and box fields are retained for supported waste labels.

For local development, open the project-root `.env` file (`C:\Users\Aaditya\OneDrive\Desktop\mini project\.env`) and replace the value on `ROBOFLOW_API_KEY=` with a newly rotated Roboflow key. Do not paste the key into chat, source files, templates, or commits. Set `ROBOFLOW_WORKSPACE=aaditya-pise`, `ROBOFLOW_WORKFLOW_ID=general-segmentation-api-7`, and `ROBOFLOW_API_URL=https://serverless.roboflow.com`; keep `AI_DEMO_MODE=false`. Restart Flask after changing `.env`. On Render, add the rotated key as the `ROBOFLOW_API_KEY` environment secret in the service dashboard and redeploy. Missing or rejected credentials show an unavailable message and award no points; synthetic detections are only enabled by explicitly setting `AI_DEMO_MODE=true` for local UI development.

Local databases, uploaded files, and presentation media are excluded from version control.

Uploaded request and scanner images are stored in the private local `instance/uploads` folder during local development. For a hosted pilot, the app can store them in a **private Supabase Storage bucket** by setting `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and `SUPABASE_STORAGE_BUCKET`. Create the bucket in Supabase before enabling those settings. The Flask server proxies authorized media after checking household ownership, worker/center assignment, or admin role; the secret key is never sent to a browser. If the cloud settings are absent, local storage remains active. If cloud storage is configured but unavailable, new image-backed requests/scans fail safely instead of silently claiming that media was persisted. Do not use the public `static` directory for user uploads.

## Dependencies

Install the packages in `requirements.txt` in a virtual environment before running the app. Production deployments should provide a strong `SECRET_KEY`, set `SESSION_COOKIE_SECURE=true` when HTTPS is enabled, and set `DATABASE_URL` to a managed PostgreSQL connection string. The app uses Flask-Migrate for schema changes; after installing dependencies, run `flask --app app:create_app db upgrade` to create or update the schema. Do not point migration commands at a database containing important data until its backup and migration strategy are confirmed.

For an existing database created before migrations were added, back it up first. Only if its schema exactly matches the baseline migration, mark that baseline with `flask --app app:create_app db stamp f9bf927b3210`, then run `flask --app app:create_app db upgrade` to apply later migrations. If the schema differs or you are unsure, stop and inspect a backup rather than stamping or running a migration against the only copy.

## Free-tier deployment preparation

`render.yaml` describes a Render free web service with a provider subdomain, HTTPS cookie setting, health check, and startup migration. Create the Supabase project separately. For a persistent Flask backend, use the Supabase **session pooler** connection string when direct database connections are unavailable from the host; configure `DATABASE_URL` with SSL required. Add `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and the private bucket name as Render environment variables. Add the Roboflow API key only if live inference is configured. Keep the repository private if it contains project materials; connect Render to the repository through its Git provider integration. The first request after idle may be delayed because free Render services spin down. Supabase Free projects can pause after low activity and have usage quotas; export important data regularly. Render's free filesystem is ephemeral, so leave private object storage enabled for hosted uploads. Do not use this deployment for important personal data or depend on it as a permanent service.
# Shared demo accounts

The Render prototype provisions its five published demo identities through
`provision_demo_accounts.py` after applying migrations. This operation is
idempotent and does not drop or recreate tables. `ENABLE_DEMO_ACCOUNTS=true`
must be set for it to run. These credentials are public and are for disposable
demonstration data only; do not use this setup for a production service or store
real personal data in the shared demo accounts.
