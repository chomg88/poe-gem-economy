#!/bin/bash
# 로컬에서 실행: EC2에 코드 업로드 + 서버 실행
set -e

EC2_HOST="52.79.210.112"
EC2_USER="ec2-user"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PEM_FILE="$SCRIPT_DIR/cmg_poe.pem"
REMOTE_BASE="/home/ec2-user/poe"

echo "=== PEM 권한 설정 ==="
chmod 400 "$PEM_FILE"

echo "=== EC2 디렉토리 생성 ==="
ssh -i "$PEM_FILE" -o StrictHostKeyChecking=no "$EC2_USER@$EC2_HOST" \
    "mkdir -p $REMOTE_BASE/economy"

echo "=== 코드 업로드 (rsync) ==="
rsync -avz \
    --exclude='.git' \
    --exclude='*.pem' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='poe_economy.db' \
    --exclude='deploy.sh' \
    -e "ssh -i $PEM_FILE -o StrictHostKeyChecking=no" \
    "$SCRIPT_DIR/" \
    "$EC2_USER@$EC2_HOST:$REMOTE_BASE/economy/"

echo "=== 서버 셋업 실행 ==="
ssh -i "$PEM_FILE" -o StrictHostKeyChecking=no "$EC2_USER@$EC2_HOST" \
    "bash $REMOTE_BASE/economy/setup_server.sh"

echo ""
echo "배포 완료!"
echo "  대시보드:   http://$EC2_HOST:8000"
echo "  관리자:     http://$EC2_HOST:8000/admin/thresholds"
