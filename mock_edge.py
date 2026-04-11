import os
import time
import random
from dotenv import load_dotenv
from supabase import create_client, Client
import requests
from concurrent.futures import ThreadPoolExecutor

# 환경 변수 로드
load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

API_URL_LOCAL = os.environ.get("API_URL_LOCAL")
API_URL_REMOTE = os.environ.get("API_URL_REMOTE")

# 테스트용 작업자 ID (DB에 미리 Insert 되어있어야 함)
TARGET_WORKER_IDS = ["TAG-001", "TAG-002", "TAG-003", "TAG-004", "TAG-005", "TAG-006"]

def send_payload(wid, payload):
    try:
        # timeout을 설정하여 응답이 늦어지더라도 무한 대기하는 것을 방지합니다.
        if API_URL_LOCAL:
            requests.post(f"{API_URL_LOCAL}/api/telemetry", json=payload, timeout=3)
        if API_URL_REMOTE:
            requests.post(f"{API_URL_REMOTE}/api/telemetry", json=payload, timeout=3)
        
        status_msg = "🚨 위험" if payload["is_danger"] or not payload["has_helmet"] else "✅ 정상"
        print(f"[{wid}] {status_msg} | 위치:({payload['pos_x']}, {payload['pos_y']}) | 안전모:{payload['has_helmet']}")
    except Exception as e:
        print(f"[{wid}] 전송 에러: {e}")

def simulate_edge_data():
    # 각 작업자별 현재 위치 저장용 딕셔너리
    positions = {wid: [random.uniform(-5, 5), random.uniform(-5, 5)] for wid in TARGET_WORKER_IDS}

    # HTTP 요청 지연을 없애기 위해 스레드 풀 생성 (작업자 수만큼 병렬 처리)
    executor = ThreadPoolExecutor(max_workers=len(TARGET_WORKER_IDS))

    while True:
        for wid in TARGET_WORKER_IDS:
            # 위치 이동
            positions[wid][0] += random.uniform(-0.3, 0.3)
            positions[wid][1] += random.uniform(-0.3, 0.3)
            
            # 로직: 위험구역(X > 10) 여부
            is_danger = True if positions[wid][0] > 10 else False
            
            # 로직: 10% 확률로 안전모를 벗음 (LoRa 데이터 모사)
            has_helmet = False if random.random() < 0.1 else True

            payload = {
                "worker_id": wid,
                "pos_x": round(positions[wid][0], 2),
                "pos_y": round(positions[wid][1], 2),
                "pos_z": 0.0,
                "has_helmet": has_helmet,
                "is_danger": is_danger,
                "last_updated": "now()"
            }

            executor.submit(send_payload, wid, payload)

        time.sleep(1) # 1초마다 전체 업데이트

if __name__ == "__main__":
    print("Mock Edge Device 가동 시작... (종료: Ctrl+C)")
    simulate_edge_data()