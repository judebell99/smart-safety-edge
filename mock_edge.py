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

UWB_TAG_MAPPING = {
    "5D9A": "TAG-001",
    "0B68": "TAG-002",
    "1F09": "TAG-003",
}

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

class UWBEdgeGateway:
    def __init__(self, client: httpx.AsyncClient):
        self.port = os.getenv("UWB_SERIAL_PORT", "/dev/ttyUSB0") # UWB 연결 포트
        self.baudrate = int(os.getenv("UWB_BAUDRATE", 115200))
        self.api_endpoint = f"{API_URL}/api/telemetry/uwb"
        self.http_client = client
        self.serial_conn = None

    def connect_serial(self) -> bool:
        """UWB 포트 오픈 및 스트리밍 시작 명령어(lep) 전송"""
        try:
            self.serial_conn = serial.Serial(
                port=self.port, baudrate=self.baudrate,
                timeout=0.1 # Non-blocking에 가깝게 설정
            )
            logging.info(f"📍 [UWB] 포트 오픈 성공: {self.port}")
            
            # 스트리밍 명령어 전송
            self.serial_conn.write(b'lep\r\n')
            time.sleep(0.1)
            logging.info("✅ [UWB] 위치 데이터 스트리밍(lep) 요청 완료.")
            return True
        except serial.SerialException as e:
            logging.error(f"❌ [UWB] 시리얼 연결 실패: {e}")
            return False

    def parse_payload(self, raw_line: str) -> dict:
        """POS 포맷 데이터를 파싱하여 JSON 형태로 반환"""
        # 예: POS,0,0B68,2.46,3.20,-0.69,65,x03
        try:
            parts = raw_line.strip().split(",")
            if parts[0] == "POS" and len(parts) >= 7:
                hw_tag_id = parts[2]
                
                # DB에 등록된 Worker ID로 변환 (등록되지 않은 태그면 무시)
                worker_id = UWB_TAG_MAPPING.get(hw_tag_id)
                if not worker_id:
                    return None

                x = float(parts[3])
                y = float(parts[4])
                z = float(parts[5])
                quality = int(parts[6])

                # 품질이 너무 낮으면(예: 40 미만) 데이터 튀는 현상 방지를 위해 무시할 수 있습니다.
                if quality < 40:
                    return None

                return {
                    "worker_id": worker_id,
                    "pos_x": x,
                    "pos_y": y,
                    "pos_z": z
                }
        except Exception as e:
            # 쓰레기 값이나 중간에 짤린 문자가 들어올 수 있으므로 패스
            pass
        return None

    async def run_streaming_loop(self):
        """1초 동안 데이터를 버퍼링하여 최신 좌표만 묶어서 API로 전송"""
        last_send_time = time.time()
        batch_buffer = {} # Worker ID를 키로 사용하여 최신 좌표로 덮어쓰기

        logging.info("📍 [UWB] 데이터 수신 및 버퍼링 루프 시작...")

        while True:
            # 1. 시리얼 버퍼에 데이터가 있으면 읽기
            if self.serial_conn.in_waiting > 0:
                raw_line = self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                parsed_data = self.parse_payload(raw_line)
                
                if parsed_data:
                    # 버퍼에 덮어쓰기 (초당 수십 번 들어와도 마지막 최신 위치만 남음)
                    batch_buffer[parsed_data["worker_id"]] = parsed_data

            # 2. 1초(1Hz) 주기로 묶어서 FastAPI로 POST 전송
            current_time = time.time()
            if current_time - last_send_time >= 1.0:
                if batch_buffer:
                    payload_list = list(batch_buffer.values())
                    try:
                        res = await self.http_client.post(self.api_endpoint, json=payload_list, timeout=3.0)
                        logging.info(f"📍 [UWB 전송] {len(payload_list)}개 태그 동기화 완료 (Status: {res.status_code})")
                    except Exception as e:
                        logging.warning(f"☁️ [UWB API 오류] {e}")
                    
                    # 전송 후 버퍼 비우기
                    batch_buffer.clear()
                
                last_send_time = current_time

            # CPU 점유율 최적화 (이벤트 루프 양보)
            await asyncio.sleep(0.01)

class LoRaEdgeGateway:
    def __init__(self, client: httpx.AsyncClient):
        self.port = os.getenv("LORA_SERIAL_PORT", "/dev/serial0")
        self.baudrate = int(os.getenv("LORA_BAUDRATE", 115200))
        self.api_endpoint = API_URL
        self.serial_conn = None
        
        # FastAPI 서버로의 통신을 위해 외부에서 생성한 AsyncClient 재사용
        self.http_client = client
        
        # self.target_nodes = ["03"]
        self.target_nodes = ["01", "02", "03"]
        self.node_timeout = 2.0  
        # self.poll_interval = 2.0
        self.poll_interval = 0.5

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
        try:
            # 1. 콜론(:) 기준으로 문자열을 분리하여 맨 마지막 페이로드 추출
            parts = raw_line.strip().split(":")
            if len(parts) < 4:
                return None
                
            payload = parts[-1] 
            
            # 2. 새로운 포맷은 최소 8글자 이상이어야 합니다 ("01010FFF")
            if len(payload) >= 8:
                node_id_hex = payload[0:2]       # "01" -> 노드 ID
                pressure_str = payload[2:4]      # "01" -> 안전모 상태
                heart_rate_hex = payload[4:8]    # "0FFF" -> 심박수 (Hex)
                
                # [ID 파싱] 16진수 문자열을 숫자로 바꿔 TAG ID 생성
                worker_id = f"TAG-00{int(node_id_hex, 16)}"
                
                # [안전모 파싱] '01'이면 정상 착용(True), 그 외('00' 등)는 미착용(False) 처리
                is_pressure_normal = (pressure_str == "01")
                
                # [심박수 파싱] 16진수(Hex) 문자열을 10진수 정수(BPM)로 변환
                # 예: "0050" -> 80, "0064" -> 100, "0FFF" -> 4095
                heart_rate = int(heart_rate_hex, 16)
                
                parsed_data = {
                    "worker_id": worker_id,
                    "heart_rate": heart_rate,
                    "is_pressure_normal": is_pressure_normal
                }
                
                # 로그 출력 고도화 (관제실 터미널 모니터링용)
                logging.info(
                    f"✅ [LoRa 파싱] {worker_id} | "
                    f"심박수: {heart_rate} BPM | "
                    f"안전모: {'착용(정상)' if is_pressure_normal else '❌미착용'}"
                )
                return parsed_data
                
        except Exception as e:
            logging.error(f"데이터 파싱 에러 ({raw_line}): {e}")
            
        return None
    
    async def send_to_api(self, parsed_data: dict, node_id: str):
        """파싱된 데이터를 FastAPI로 전송"""
        if not parsed_data:
            return
            
        url = f"{self.api_endpoint}/api/telemetry/lora"
        payload_list = [parsed_data]
        
        try:
            # 라즈베리파이 send_to_api 내부 코드 조각 예시
            res = await self.http_client.post(url, json=payload_list, timeout=3.0)
            if res.status_code == 200:
                response_data = res.json()

                await asyncio.sleep(1)

                print(f"☁️ [LoRa API 응답] {response_data}")
                if response_data.get("buzzer_on"):
                    logging.warning(f"부저를 작동시킵니다. 대상: {response_data.get('target_workers')} AT+PSEND={node_id}EE")
                    if not await self.send_at_command("AT+NWM=0", wait_timeout=1.0): return
                    if not await self.send_at_command("AT+P2P=923000000:7:125:0:8:15", wait_timeout=1.0): return
                    if not await self.send_at_command(f"AT+PSEND={node_id}EE", wait_timeout=3.0): return
                else:
                    logging.warning(f"부저를 종료시킵니다. 대상: {response_data.get('target_workers')} AT+PSEND={node_id}EF")
                    if not await self.send_at_command("AT+NWM=0", wait_timeout=1.0): return
                    if not await self.send_at_command("AT+P2P=923000000:7:125:0:8:15", wait_timeout=1.0): return
                    if not await self.send_at_command(f"AT+PSEND={node_id}EF", wait_timeout=3.0): return
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
                                await self.send_to_api(parsed_data, node_id)
                            
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

async def run_real_uwb(client: httpx.AsyncClient):
    uwb_gateway = UWBEdgeGateway(client)
    if not uwb_gateway.connect_serial():
        logging.error("📍 [UWB] 실제 모듈이 연결되어 있는지, 포트 권한이 있는지 확인하세요.")
        return

    try:
        await uwb_gateway.run_streaming_loop()
    except asyncio.CancelledError:
        logging.info("📍 [UWB] 수신 태스크가 취소되었습니다.")
    finally:
        if uwb_gateway.serial_conn and uwb_gateway.serial_conn.is_open:
            uwb_gateway.serial_conn.close()

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
            # run_real_uwb(client),
            run_real_lora(client),
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 가상 엣지 시뮬레이터를 안전하게 종료합니다.")