import index
from file_access import router as file_access_router

app = index.app
app.version = "0.2.0"
app.description = (
    "Read-only Kaggle bridge for search, metadata, notebooks, models, exact dataset-file return, "
    "row previews, and Code Interpreter workflows."
)
app.include_router(file_access_router)
