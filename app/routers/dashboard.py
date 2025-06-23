from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import json
from app.state import monitor_state

router = APIRouter()

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """
    간단한 HTML 대시보드를 반환합니다.
    3초마다 자동 새로고침하며, 현재 모니터링 상태를 JSON 형태로 보여줍니다.
    """
    data = monitor_state
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8" />
        <meta http-equiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Trading Bot Dashboard</title>
        <!-- 3초마다 페이지 전체 새로고침 -->
        <meta http-equiv="refresh" content="3" />
        <style>
            body {{ font-family: monospace; background: #f5f5f5; padding: 20px; }}
            pre {{ background: #fff; padding: 10px; border: 1px solid #ddd; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <h1>Trading Bot Dashboard</h1>
        <pre>{json.dumps(data, indent=2, ensure_ascii=False)}</pre>
    </body>
    </html>
    """
    return HTMLResponse(html_content)