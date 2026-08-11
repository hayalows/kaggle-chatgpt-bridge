import index
import file_access
from file_download_patch import download_exact

file_access._download = download_exact

app = index.app
app.version = "0.2.0"
app.description = (
    "Read-only Kaggle bridge for search, metadata, notebooks, models, exact dataset-file return, "
    "row previews, and Code Interpreter workflows."
)
app.include_router(file_access.router)
