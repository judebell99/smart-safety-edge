import math
import os
import time
import random
from dotenv import load_dotenv
from supabase import create_client, Client
import requests

# 환경 변수 로드
load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

API_URL_LOCAL = os.environ.get("API_URL_LOCAL")
API_URL_REMOTE = os.environ.get("API_URL_REMOTE")

# 테스트용 작업자 ID (DB에 미리 Insert 되어있어야 함)
TARGET_WORKER_IDS = ["TAG-001", "TAG-002", "TAG-003", "TAG-004", "TAG-005", "TAG-006"]

workers_state = {}
for wid in TARGET_WORKER_IDS:
    workers_state[wid] = {
        "pos_x": random.uniform(-10, 10),
        "pos_y": random.uniform(-10, 10),
        "target_x": random.uniform(-10, 10),
        "target_y": random.uniform(-10, 10),
        "speed": random.uniform(0.6, 1.2) # 사람마다 걷는 속도가 다름
    }

def simulate_edge_data():
    print("🧠 Human-like Movement Simulation 가동 시작...")
    
    while True:
        payloads = []

        for wid in TARGET_WORKER_IDS:
            state = workers_state[wid]
            
            dx = state["target_x"] - state["pos_x"]
            dy = state["target_y"] - state["pos_y"]
            distance = math.sqrt(dx**2 + dy**2)

            if distance < 1.0:
                state["target_x"] = random.uniform(-15, 15)
                state["target_y"] = random.uniform(-15, 15)
                # 이번 턴에 대기하더라도 현재 위치는 배열에 담아 보내야 하므로 위치만 업데이트 생략
            else:
                move_x = (dx / distance) * state["speed"]
                move_y = (dy / distance) * state["speed"]
                state["pos_x"] += move_x + random.uniform(-0.1, 0.1)
                state["pos_y"] += move_y + random.uniform(-0.1, 0.1)

            is_danger = True if state["pos_x"] > 10 else False
            has_helmet = False if random.random() < 0.05 else True

            payloads.append({
                "worker_id": wid,
                "pos_x": round(state["pos_x"], 2),
                "pos_y": round(state["pos_y"], 2),
                "pos_z": 0.0,
                "has_helmet": has_helmet,
                "is_danger": is_danger
            })
        
        try:
            res = requests.post(f"{API_URL_LOCAL}/api/telemetry", json=payloads)
            res = requests.post(f"{API_URL_REMOTE}/api/telemetry", json=payloads)
            print(f"📦 [배치 전송 완료] {len(payloads)}명 데이터 일괄 전송 (Status: {res.status_code})")
        except Exception as e:
            print(f"전송 에러: {e}")

        # 초당 1회 업데이트
        time.sleep(1)

if __name__ == "__main__":
    simulate_edge_data()
    