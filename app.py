import index
import file_access
import inline_file_access
from file_download_patch import download_exact

file_access._download = download_exact

# Keep the older signed-URL route available server-side for compatibility,
# but hide it from GPT Actions. OpenAI file URL fetching has a 10-second
# secondary fetch timeout; the inline action below is more reliable on Vercel.
for route in file_access.router.routes:
    if getattr(route, "operation_id", None) == "getKaggleDatasetFileForAnalysis":
        route.include_in_schema = False

app = index.app
app.version = "0.2.1"
app.description = (
    "Read-only Kaggle bridge for search, metadata, notebooks, models, exact dataset-file return, "
    "row previews, and Code Interpreter workflows. Small analysis files are returned inline for reliability."
)
app.include_router(file_access.router)
app.include_router(inline_file_access.router)
