# Start here

The code is built and pushed.

1. Import `hayalows/kaggle-chatgpt-bridge` into Vercel.
2. In Vercel Project Settings > Environment Variables, add:
   - `KAGGLE_API_TOKEN`
   - `BRIDGE_API_KEY`
   - `PUBLIC_BASE_URL`
3. Redeploy after adding the variables.
4. Open `/api/health`. Both configuration flags should be `true`.
5. In your Custom GPT, create an Action and import `/openapi.json` from the deployed bridge.
6. Configure Action authentication as an API key using Bearer auth. Use the same `BRIDGE_API_KEY` stored in Vercel.

Keep the first version read-only while testing.
