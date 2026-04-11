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

API_URL_LOCAL = os.environ.get("API_URL_LOCAL", "http://localhost:8000")
API_URL_REMOTE = os.environ.get("API_URL_REMOTE", "http://localhost:8000")

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

def calculate_next_position(wid: str, state: dict) -> dict:
    dx = state["target_x"] - state["pos_x"]
    dy = state["target_y"] - state["pos_y"]
    distance = math.sqrt(dx**2 + dy**2)

    if distance < 1.0:
        state["target_x"] = random.uniform(-15, 15)
        state["target_y"] = random.uniform(-15, 15)
    else:
        move_x = (dx / distance) * state["speed"]
        move_y = (dy / distance) * state["speed"]
        state["pos_x"] += move_x + random.uniform(-0.1, 0.1)
        state["pos_y"] += move_y + random.uniform(-0.1, 0.1)

    return {
        "worker_id": wid,
        "pos_x": round(state["pos_x"], 2),
        "pos_y": round(state["pos_y"], 2),
        "pos_z": 0.0,
        "has_helmet": random.random() >= 0.05,
        "is_danger": state["pos_x"] > 10
    }

def send_telemetry(payloads: list):
    for api_url in [API_URL_LOCAL, API_URL_REMOTE]:
        if not api_url:
            continue
        try:
            res = requests.post(f"{api_url}/api/telemetry", json=payloads, timeout=5)
            print(f"📦 [배치 전송 완료] {len(payloads)}명 데이터 일괄 전송 (URL: {api_url}, Status: {res.status_code})")
        except requests.exceptions.RequestException as e:
            print(f"전송 에러 ({api_url}): {e}")

def simulate_edge_data():
    print("🧠 Human-like Movement Simulation 가동 시작...")
    
    while True:
        payloads = [calculate_next_position(wid, workers_state[wid]) for wid in TARGET_WORKER_IDS]
        send_telemetry(payloads)

        # 초당 1회 업데이트
        time.sleep(1)

if __name__ == "__main__":
    simulate_edge_data()
    