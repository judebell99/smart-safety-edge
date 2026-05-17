import math
import os
import time
import random
import asyncio
import logging
import httpx
from dotenv import load_dotenv

# 로그 설정 (터미널에서 예쁘게 보이도록)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

load_dotenv()
API_URL = os.environ.get("FASTAPI_ENDPOINT", "http://localhost:8000")

# DB에 미리 생성해둔 작업자 ID를 사용해야 Foreign Key 에러가 나지 않습니다.
TARGET_WORKER_IDS = ["TAG-001", "TAG-002", "TAG-003", "TAG-004", "TAG-005", "TAG-006"]

# --- UWB: 인간과 유사한 부드러운 동선 생성을 위한 상태 메모리 ---
workers_state = {}
for wid in TARGET_WORKER_IDS:
    workers_state[wid] = {
        "pos_x": random.uniform(-10, 10),
        "pos_y": random.uniform(-10, 10),
        "target_x": random.uniform(-10, 10),
        "target_y": random.uniform(-10, 10),
        "speed": random.uniform(0.6, 1.2) # 작업자마다 걷는 속도 차이 부여
    }

def calculate_next_position(wid: str, state: dict) -> dict:
    dx = state["target_x"] - state["pos_x"]
    dy = state["target_y"] - state["pos_y"]
    distance = math.sqrt(dx**2 + dy**2)

    # 목표 지점 도달 시 새로운 랜덤 목표 지점 생성
    if distance < 1.0:
        state["target_x"] = random.uniform(-15, 15)
        state["target_y"] = random.uniform(-15, 15)
    else:
        move_x = (dx / distance) * state["speed"]
        move_y = (dy / distance) * state["speed"]
        state["pos_x"] += move_x + random.uniform(-0.1, 0.1) # 약간의 흔들림(노이즈) 추가
        state["pos_y"] += move_y + random.uniform(-0.1, 0.1)

    return {
        "worker_id": wid,
        "pos_x": round(state["pos_x"], 2),
        "pos_y": round(state["pos_y"], 2),
        "pos_z": 0.0 # 평면 이동 가정
    }

def generate_mock_lora_data(wid: str) -> dict:
    # 95% 확률로 정상, 5% 확률로 비정상(위험) 상황 발생 시뮬레이션
    is_heart_normal = random.random() >= 0.05
    is_pressure_normal = random.random() >= 0.05

    return {
        "worker_id": wid,
        "is_heart_normal": is_heart_normal,
        "is_pressure_normal": is_pressure_normal
    }

# --- 비동기 Task 1: UWB 위치 전송 (고빈도: 1초 주기) ---
async def simulate_uwb(client: httpx.AsyncClient):
    logging.info("📍 [UWB] 1Hz 위치 추적 시뮬레이션 가동...")
    while True:
        payloads = [calculate_next_position(wid, workers_state[wid]) for wid in TARGET_WORKER_IDS]
        try:
            res = await client.post(f"{API_URL}/api/telemetry/uwb", json=payloads, timeout=5.0)
            logging.info(f"📍 [UWB] {len(payloads)}명 좌표 동기화 (Status: {res.status_code})")
        except Exception as e:
            logging.error(f"📍 [UWB 전송 에러]: {e}")

        await asyncio.sleep(1.0) # 1초마다 반복

# --- 비동기 Task 2: LoRa 센서 데이터 전송 (저빈도: 5초 주기) ---
async def simulate_lora(client: httpx.AsyncClient):
    logging.info("📡 [LoRa] 5초 주기 센서 폴링 시뮬레이션 가동...")
    await asyncio.sleep(2.0) # UWB와 요청 타이밍이 겹치지 않게 살짝 딜레이

    while True:
        payloads = [generate_mock_lora_data(wid) for wid in TARGET_WORKER_IDS]
        try:
            res = await client.post(f"{API_URL}/api/telemetry/lora", json=payloads, timeout=5.0)
            
            # 위험 데이터가 있는지 확인하여 로그로 강조
            danger_count = sum(1 for p in payloads if not p['is_heart_normal'] or not p['is_pressure_normal'])
            msg = f"📡 [LoRa] 센서 상태 동기화 (Status: {res.status_code})"
            if danger_count > 0:
                msg += f" ⚠️ [이상 감지] {danger_count}건 발생!"
                
            logging.info(msg)
        except Exception as e:
            logging.error(f"📡 [LoRa 전송 에러]: {e}")

        await asyncio.sleep(5.0) # 5초마다 반복

# --- 메인 이벤트 루프 ---
async def main():
    print("🚀 [Smart Safety Edge Simulator] 가상 라즈베리파이 가동 시작!\n")
    
    # httpx를 통해 비동기 HTTP 연결 풀(Pool)을 생성하여 성능 극대화
    async with httpx.AsyncClient() as client:
        # asyncio.gather를 사용해 두 개의 무한 루프(UWB, LoRa)를 동시에 병렬로 돌립니다.
        await asyncio.gather(
            simulate_uwb(client),
            simulate_lora(client)
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 가상 엣지 시뮬레이터를 안전하게 종료합니다.")