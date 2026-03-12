#!/usr/bin/env python3
"""웹 앱 실행 진입점"""
import sys
import os

# economy 패키지의 부모 디렉토리를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from economy.webapp import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
