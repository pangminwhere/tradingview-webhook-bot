from fastapi import FastAPI
from app.routers.webhook import router as webhook_router
from app.routers.dashboard import router as dashboard_router
import threading
import logging
from app.services.monitor import start_monitor

app = FastAPI()


@app.on_event("startup")
def on_startup():
    """
    앱 기동 시 모니터 스레드를 안전하게 띄웁니다.
    예외가 터져도 FastAPI 자체는 멈추지 않습니다.
    """
    def safe_monitor():
        try:
            start_monitor()
        except Exception:
            logging.getLogger("monitor").exception("모니터링 스레드 실패")

    thread = threading.Thread(target=safe_monitor, daemon=True)
    thread.start()


app.include_router(webhook_router)
app.include_router(dashboard_router)


@app.get("/health")
def health():
    return {"status": "alive"}