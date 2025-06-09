## 환경 변수 설정
from dotenv import load_dotenv
import os


load_dotenv()

# 바이낸스 키
EX_API_KEY = os.getenv("EXCHANGE_API_KEY")
EX_API_SECRET = os.getenv("EXCHANGE_API_SECRET")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

