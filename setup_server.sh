#!/bin/bash
# EC2 (Amazon Linux 2023) 서버 초기 셋업 스크립트
set -e

REMOTE_BASE="/home/ec2-user/poe"
ECONOMY_DIR="$REMOTE_BASE/economy"

echo "=== [1/5] Python3 & pip & cronie 설치 ==="
sudo yum update -y -q
sudo yum install -y python3 python3-pip cronie -q
sudo systemctl enable crond
sudo systemctl start crond

echo "=== [2/5] pip 패키지 설치 ==="
pip3 install --quiet -r "$ECONOMY_DIR/requirements.txt"
# gunicorn 경로 탐색 (pip3 install 위치에 따라 다름)
GUNICORN_BIN=$(python3 -c "import shutil; g=shutil.which('gunicorn'); print(g if g else '/home/ec2-user/.local/bin/gunicorn')")

echo "=== [3/5] systemd 서비스 설정 (gunicorn: $GUNICORN_BIN) ==="
sudo tee /etc/systemd/system/poe-webapp.service > /dev/null <<UNIT
[Unit]
Description=PoE Economy Webapp (gunicorn)
After=network.target

[Service]
User=ec2-user
Environment="PYTHONPATH=${REMOTE_BASE}"
WorkingDirectory=${REMOTE_BASE}
ExecStart=${GUNICORN_BIN} -w 2 -b 0.0.0.0:8000 economy.webapp:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable poe-webapp
sudo systemctl restart poe-webapp

echo "=== [4/5] 크론 설정 (30분마다 크롤링) ==="
PYTHON_BIN=$(which python3)
CRON_JOB="*/30 * * * * ${PYTHON_BIN} ${ECONOMY_DIR}/run_crawler.py >> ${REMOTE_BASE}/crawler.log 2>&1"
(crontab -l 2>/dev/null | grep -v "run_crawler.py"; echo "$CRON_JOB") | crontab -

echo "=== [5/5] 초기 크롤링 실행 ==="
nohup python3 "$ECONOMY_DIR/run_crawler.py" >> "$REMOTE_BASE/crawler.log" 2>&1 &
echo "크롤링을 백그라운드에서 실행 중... (완료까지 1~2분 소요)"

echo ""
echo "======================================"
echo " 설정 완료!"
echo " 웹 대시보드: http://52.79.210.112:8000"
echo " 관리자 페이지: http://52.79.210.112:8000/admin/thresholds"
echo " 크론: 매시간 자동 크롤링"
echo "======================================"
