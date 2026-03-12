#!/usr/bin/env python3
"""크롤러 실행 진입점 (크론에서 매시간 호출)"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from economy.crawler import run_crawl

if __name__ == "__main__":
    run_crawl()
