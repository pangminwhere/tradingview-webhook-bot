from fastapi import FastAPI
from app.routers.webhook import router as webhook_router
from app.routers.dashboard import router as dashboard_router

app = FastAPI()
app.include_router(webhook_router)
app.include_router(dashboard_router)

@app.get("/health")
def health():
    return {"status": "alive"}