# app/main.py
from fastapi import FastAPI
from app.routers.webhook import router as webhook_router
from app.routers.dashboard import router as dashboard_router
from app.services.monitor import start_monitor

app = FastAPI()

# 서버 시작 시 모니터링 시작
@app.on_event("startup")
def on_startup():
    start_monitor()

app.include_router(webhook_router)
app.include_router(dashboard_router)

@app.get("/health")
def health():
    return {"status": "alive"}