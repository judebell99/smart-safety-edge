import math
import os
import time
import serial
import random
import asyncio
import logging
import httpx
from dotenv import load_dotenv

# 로그 설정 (터미널에서 예쁘게 보이도록)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

load_dotenv()
API_URL = os.environ.get("API_URL_REMOTE", "http://localhost:8000")

# DB에 미리 생성해둔 작업자 ID를 사용해야 Foreign Key 에러가 나지 않습니다.
TARGET_WORKER_IDS = ["TAG-001", "TAG-002", "TAG-003"]

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

class LoRaEdgeGateway:
    def __init__(self, client: httpx.AsyncClient):
        self.port = os.getenv("LORA_SERIAL_PORT", "/dev/serial0")
        self.baudrate = int(os.getenv("LORA_BAUDRATE", 115200))
        self.api_endpoint = API_URL
        self.serial_conn = None
        
        # FastAPI 서버로의 통신을 위해 외부에서 생성한 AsyncClient 재사용
        self.http_client = client
        
        self.target_nodes = ["01", "02", "03"]
        self.node_timeout = 2.0  
        self.poll_interval = 2.0

    def connect_serial(self) -> bool:
        """LoRa 포트 오픈 및 기본 핸드쉐이크"""
        try:
            self.serial_conn = serial.Serial(
                port=self.port, baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=1
            )
            logging.info(f"📡 [LoRa] 포트 오픈 성공: {self.port} (8-N-1)")
            
            self.serial_conn.write(b'AT\r\n')
            time.sleep(0.5)
            if self.serial_conn.in_waiting > 0:
                self.serial_conn.read_all()
                logging.info("✅ [LoRa] 모듈 핸드쉐이크(OK) 완료.")
                return True
            return False
        except serial.SerialException as e:
            logging.error(f"시리얼 연결 실패: {e}")
            return False

    def parse_payload(self, raw_line: str):
        """RAK3272S 수신 데이터 파서"""
        try:
            parts = raw_line.strip().split(":")
            if len(parts) < 4:
                return None
                
            payload = parts[-1] 
            if len(payload) >= 4:
                node_id_hex = payload[0:2] # "01", "02", "03"
                worker_id = f"TAG-00{int(node_id_hex)}"
                
                is_heart_normal = (payload[5] == '1')
                is_pressure_normal = (payload[7] == '1')
                
                parsed_data = {
                    "worker_id": worker_id,
                    "is_heart_normal": is_heart_normal,
                    "is_pressure_normal": is_pressure_normal
                }
                
                logging.info(f"✅ [LoRa 파싱] {worker_id} | 심박: {'정상' if is_heart_normal else '비정상'} | 압력: {'정상' if is_pressure_normal else '비정상'}")
                return parsed_data
                
        except Exception as e:
            logging.error(f"데이터 파싱 에러 ({raw_line}): {e}")
            
        return None
    
    async def send_to_api(self, parsed_data: dict):
        """파싱된 데이터를 FastAPI로 전송"""
        if not parsed_data:
            return
            
        url = f"{self.api_endpoint}/api/telemetry/lora"
        payload_list = [parsed_data]
        
        try:
            res = await self.http_client.post(url, json=payload_list, timeout=3.0)
            if res.status_code == 200:
                logging.info(f"☁️ [LoRa API 전송] 상태코드: {res.status_code}")
            else:
                logging.warning(f"☁️ [LoRa API 실패] 상태코드: {res.status_code} | 응답: {res.text}")
        except Exception as e:
            logging.error(f"☁️ [LoRa API 오류] 서버가 켜져 있는지 확인하세요: {e}")
    
    async def send_at_command(self, command: str, wait_timeout: float = 1.0) -> bool:
        """AT 명령어 전송 및 OK 대기"""
        if not self.serial_conn or not self.serial_conn.is_open:
            return False

        self.serial_conn.reset_input_buffer()
        cmd_bytes = f"{command}\r\n".encode('utf-8')
        self.serial_conn.write(cmd_bytes)
        logging.debug(f"▶️ [CMD 전송] {command}")

        elapsed_time = 0.0
        check_interval = 0.05 

        while elapsed_time < wait_timeout:
            if self.serial_conn.in_waiting > 0:
                raw_line = self.serial_conn.readline().decode('utf-8', errors='replace').strip()
                if raw_line == "OK":
                    return True
                elif "ERROR" in raw_line.upper():
                    logging.error(f"❌ [CMD 실패] {command} -> 응답: {raw_line}")
                    return False
            
            await asyncio.sleep(check_interval)
            elapsed_time += check_interval

        return False

    async def poll_sequence(self):
        """동기적 질의-응답 폴링 루프"""
        logging.info("🚀 [LoRa] RAK3272S 통신 스케줄러 시작...")
        await asyncio.sleep(2) 
        
        while True:
            for node_id in self.target_nodes:
                logging.info(f"---------------------------------------------------")
                logging.info(f"🔧 [Node {node_id}] 폴링 사이클 시작")

                if not await self.send_at_command("AT+NWM=0", wait_timeout=1.0): continue
                if not await self.send_at_command("AT+P2P=923000000:7:125:0:8:15", wait_timeout=1.0): continue

                poll_cmd = f"AT+PSEND={node_id}FF"
                logging.info(f"👉 [TX] 폴링 요청 발사 (CMD: {poll_cmd})")
                if not await self.send_at_command(poll_cmd, wait_timeout=3.0): continue
                if not await self.send_at_command("AT+PRECV=65535", wait_timeout=3.0): continue
                
                logging.info("📡 응답 대기 중...")
                
                wait_time = 0.0
                is_received = False
                
                while wait_time < self.node_timeout:
                    if self.serial_conn.in_waiting > 0:
                        raw_line = self.serial_conn.readline().decode('utf-8', errors='replace').strip()
                        
                        if "+EVT:RXP2P" in raw_line:
                            logging.info(f"📥 [RX] 수신 원본: {raw_line}")
                            is_received = True
                            
                            parsed_data = self.parse_payload(raw_line)
                            if parsed_data:
                                await self.send_to_api(parsed_data)
                            
                            break 
                    
                    await asyncio.sleep(0.1)
                    wait_time += 0.1
                
                if not is_received:
                    logging.warning(f"⚠️ [Timeout] Node {node_id} 응답 없음")

                await asyncio.sleep(self.poll_interval)

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

# --- 비동기 Task 2: LoRa 센서 데이터 전송 (실제 하드웨어 연동) ---
async def run_real_lora(client: httpx.AsyncClient):
    gateway = LoRaEdgeGateway(client)
    if not gateway.connect_serial():
        logging.error("📡 [LoRa] 시리얼 포트를 열 수 없습니다. 실제 모듈이 연결되어 있는지 확인하세요.")
        return

    try:
        await gateway.poll_sequence()
    except asyncio.CancelledError:
        logging.info("📡 [LoRa] 태스크가 취소되었습니다.")
    except Exception as e:
        logging.error(f"📡 [LoRa] 폴링 중 에러 발생: {e}")
    finally:
        if gateway.serial_conn and gateway.serial_conn.is_open:
            gateway.serial_conn.close()

# --- 메인 이벤트 루프 ---
async def main():
    print("🚀 [Smart Safety Edge Simulator] 가상 라즈베리파이 가동 시작!\n")
    
    # httpx를 통해 비동기 HTTP 연결 풀(Pool)을 생성하여 성능 극대화
    async with httpx.AsyncClient() as client:
        # asyncio.gather를 사용해 두 개의 무한 루프(UWB, LoRa)를 동시에 병렬로 돌립니다.
        await asyncio.gather(
            simulate_uwb(client),
            run_real_lora(client)
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 가상 엣지 시뮬레이터를 안전하게 종료합니다.")