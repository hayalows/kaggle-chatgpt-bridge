# Kaggle ChatGPT Bridge

A small read-only API that lets a Custom GPT search and inspect Kaggle through the official Kaggle CLI.

## v0.1 endpoints

- `GET /api/health`
- `GET /api/datasets/search`
- `GET /api/datasets/{owner}/{slug}/files`
- `GET /api/datasets/{owner}/{slug}/metadata`
- `GET /api/competitions/search`
- `GET /api/notebooks/search`
- `GET /api/models/search`

All Kaggle endpoints require `Authorization: Bearer <BRIDGE_API_KEY>`.

## Why v0.1 is read-only

The bridge intentionally does not expose dataset uploads/deletes, competition submissions, notebook pushes, or model writes. This reduces the damage a mistaken or malicious action request could cause.

## Kaggle authentication

1. Sign in to Kaggle.
2. Open Kaggle Settings.
3. Under **API**, choose **Generate New Token**.
4. Save the token as the Vercel environment variable `KAGGLE_API_TOKEN`.

Do not commit the token to GitHub.

## Bridge authentication

Generate a separate random secret for ChatGPT to use when calling this bridge:

```bash
openssl rand -hex 32
```

Save that value in Vercel as `BRIDGE_API_KEY`.

## Deploy to Vercel

Set these environment variables in the Vercel project:

```text
KAGGLE_API_TOKEN=...
BRIDGE_API_KEY=...
PUBLIC_BASE_URL=https://YOUR-PROJECT.vercel.app
```

Vercel recognizes the root `index.py` FastAPI application automatically, so no routing configuration is required. Then deploy. Confirm:

```text
https://YOUR-PROJECT.vercel.app/api/health
https://YOUR-PROJECT.vercel.app/openapi.json
```

`/api/health` should report both configuration flags as `true`.

## Connect a Custom GPT

1. In ChatGPT on the web, create or edit a GPT.
2. Open **Actions** and create a new action.
3. Import the OpenAPI schema from:

```text
https://YOUR-PROJECT.vercel.app/openapi.json
```

4. Configure Action authentication as **API Key**.
5. Use **Bearer** authentication and paste the same value you stored as `BRIDGE_API_KEY`.
6. Test prompts such as:
   - Search Kaggle for Ghana tourism datasets.
   - Find Python notebooks about hotel demand forecasting.
   - Find Kaggle models matching time-series forecasting.
   - Show the files inside `owner/dataset-slug`.

## Recommended Custom GPT instructions

```text
Use the Kaggle actions when the user asks to search, inspect, compare, or research Kaggle datasets, competitions, notebooks, or models. Prefer concise result summaries. Do not claim a dataset was downloaded or modified because this bridge is read-only. When comparing results, consider relevance, recency, size, votes/downloads when available, license, and suitability for the user's stated project.
```

## Local development

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn index:app --reload
```

Then open `http://127.0.0.1:8000/docs`.

## Security notes

- Keep `KAGGLE_API_TOKEN` and `BRIDGE_API_KEY` only in environment variables.
- Keep the GitHub repository private until you have reviewed the code and deployment settings.
- Rotate either secret immediately if it appears in a commit, screenshot, chat, log, or public page.
- v0.1 performs only hard-coded read operations. It does not accept arbitrary Kaggle CLI commands from callers.
