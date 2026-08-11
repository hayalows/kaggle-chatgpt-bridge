import index
import file_access
import inline_file_access
import rows_access
from file_download_patch import download_exact

file_access._download = download_exact

# Keep the older signed-URL route available server-side for compatibility,
# but hide it from GPT Actions. The inline action is preferred.
for route in file_access.router.routes:
    if getattr(route, "operation_id", None) == "getKaggleDatasetFileForAnalysis":
        route.include_in_schema = False

app = index.app
app.version = "0.2.2"
app.description = (
    "Read-only Kaggle bridge for search, metadata, notebooks, models, exact file return, row previews, "
    "paged exact-row retrieval, and Code Interpreter workflows."
)
app.include_router(file_access.router)
app.include_router(inline_file_access.router)
app.include_router(rows_access.router)
