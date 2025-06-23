# app/routers/dashboard.py

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.state import monitor_state

router = APIRouter()

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """
    대시보드 페이지를 반환합니다.
    3초마다 자동 새로고침되어 실시간 상태를 볼 수 있습니다.
    """
    data = monitor_state
    entry_price = data.get("entry_price", 0.0)
    qty         = data.get("position_qty", 0.0)
    first_done  = data.get("first_tp_done", False)
    second_done = data.get("second_tp_done", False)

    # 목표가 계산
    tp1_target = entry_price * 1.005 if entry_price else 0.0
    tp2_target = entry_price * 1.011 if entry_price else 0.0

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8" />
        <meta http-equiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>자동매매 대시보드</title>
        <meta http-equiv="refresh" content="3" />
        <style>
            body {{ background: #f0f2f5; font-family: Arial, sans-serif; color: #333; padding: 20px; }}
            h1 {{ text-align: center; margin-bottom: 30px; }}
            .section {{ background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h2 {{ font-size: 1.4em; margin-bottom: 10px; }}
            p {{ font-size: 1.1em; margin: 6px 0; }}
        </style>
    </head>
    <body>
        <h1>자동매매 상태 대시보드</h1>

        <div class="section">
            <h2>진입 정보 ({'진행 중' if qty > 0 else '미진행'})</h2>
            <p><strong>진입가:</strong> {entry_price:.2f} USDT</p>
            <p><strong>현재 수량:</strong> {qty:.4f}</p>
"""