import index
import file_access
import inline_file_access
import rows_access
import simple_csv_access
from file_download_patch import download_exact

file_access._download = download_exact

# Keep legacy transfer routes available server-side, but hide them from GPT Actions.
# The simple CSV-text action uses a much simpler schema and is the primary data path.
for route in file_access.router.routes:
    operation_id = getattr(route, "operation_id", None)
    if operation_id == "getKaggleDatasetFileForAnalysis":
        route.include_in_schema = False
    elif operation_id == "previewKaggleDatasetFile":
        route.description = (
            "Preview a small sample from the exact Kaggle file. For full CSV analysis, "
            "use readKaggleDatasetRows and continue while hasMore is true."
        )

for route in inline_file_access.router.routes:
    if getattr(route, "operation_id", None) == "getKaggleDatasetFileForAnalysis":
        route.include_in_schema = False

for route in rows_access.router.routes:
    if getattr(route, "operation_id", None) == "readKaggleDatasetRows":
        route.include_in_schema = False

app = index.app
app.version = "0.2.3"
app.description = (
    "Read-only Kaggle bridge for search, metadata, notebooks, models, previews, and simple CSV-text "
    "retrieval for Code Interpreter workflows."
)
app.include_router(file_access.router)
app.include_router(inline_file_access.router)
app.include_router(rows_access.router)
app.include_router(simple_csv_access.router)
