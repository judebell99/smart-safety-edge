import os
import time
import json
import asyncio
import logging
import serial
import httpx
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class LoRaEdgeGateway:
    def __init__(self):
        load_dotenv()
        self.port = os.getenv("LORA_SERIAL_PORT", "/dev/serial0")
        self.baudrate = int(os.getenv("LORA_BAUDRATE", 115200))
        
        # FastAPI 서버 엔드포인트 (기본값 설정, .env에서 덮어쓰기 가능)
        self.api_endpoint = os.getenv("API_URL_REMOTE", "http://localhost:8000")
        
        self.serial_conn = None
        self.http_client = httpx.AsyncClient()
        
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
            logging.info(f"📡 포트 오픈 성공: {self.port} (8-N-1)")
            
            self.serial_conn.write(b'AT\r\n')
            time.sleep(0.5)
            if self.serial_conn.in_waiting > 0:
                self.serial_conn.read_all()
                logging.info("✅ LoRa 모듈 핸드쉐이크(OK) 완료.")
                return True
            return False
        except serial.SerialException as e:
            logging.error(f"시리얼 연결 실패: {e}")
            return False

    def parse_payload(self, raw_line: str):
        """
        RAK3272S 수신 데이터 파서
        예: +EVT:RXP2P:-11:12:01000000
        """
        try:
            # 1. ':' 기준으로 분리하여 마지막 실제 페이로드 추출 ("01000000")
            parts = raw_line.strip().split(":")
            if len(parts) < 4:
                return None
                
            payload = parts[-1] 
            
            # 페이로드가 4자리 이상일 때만 정상 처리 (예: "0100...")
            if len(payload) >= 4:
                node_id_hex = payload[0:2] # "01", "02", "03"
                
                # 2. Worker ID 변환 (DB에 있는 W-001, W-002 등과 매핑)
                # 만약 DB에 TAG-001로 저장하셨다면 f"TAG-00{int(node_id_hex)}" 로 수정하세요.
                worker_id = f"TAG-00{int(node_id_hex)}"
                
                # 3. 3번째 자리는 심박, 4번째 자리는 압력으로 매핑 (1=정상, 0=비정상)
                # 01000000 -> 심박 '0'(False), 압력 '0'(False)
                # 01110000 -> 심박 '1'(True),  압력 '1'(True)
                is_heart_normal = (payload[5] == '1')
                is_pressure_normal = (payload[7] == '1')
                
                parsed_data = {
                    "worker_id": worker_id,
                    "is_heart_normal": is_heart_normal,
                    "is_pressure_normal": is_pressure_normal
                }
                
                logging.info(f"✅ [파싱 성공] {worker_id} | 심박: {'정상' if is_heart_normal else '비정상'} | 압력(안전모): {'정상' if is_pressure_normal else '비정상'}")
                return parsed_data
                
        except Exception as e:
            logging.error(f"데이터 파싱 에러 ({raw_line}): {e}")
            
        return None
    
    async def send_to_api(self, parsed_data: dict):
        """파싱된 데이터를 FastAPI로 전송"""
        if not parsed_data:
            return
            
        url = f"{self.api_endpoint}/api/telemetry/lora"
        payload_list = [parsed_data] # FastAPI가 List 모델을 기대하므로 리스트로 감쌉니다.
        
        try:
            # 타임아웃 3초를 주고 비동기로 백엔드에 쏩니다.
            res = await self.http_client.post(url, json=payload_list, timeout=3.0)
            
            if res.status_code == 200:
                logging.info(f"☁️ [API 전송 완료] 상태코드: {res.status_code}")
            else:
                logging.warning(f"☁️ [API 전송 실패] 상태코드: {res.status_code} | 응답: {res.text}")
                
        except Exception as e:
            logging.error(f"☁️ [API 서버 연결 오류] 서버가 켜져 있는지 확인하세요: {e}")
    
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
        logging.info("🚀 RAK3272S 통신 및 API 연동 스케줄러 시작...")
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
                        
                        # 실제 센서 데이터 수신 감지 (+EVT:RXP2P)
                        if "+EVT:RXP2P" in raw_line:
                            logging.info(f"📥 [RX] 수신 원본: {raw_line}")
                            is_received = True
                            
                            # 1. 데이터 파싱
                            parsed_data = self.parse_payload(raw_line)
                            
                            # 2. FastAPI로 전송
                            if parsed_data:
                                await self.send_to_api(parsed_data)
                            
                            break 
                    
                    await asyncio.sleep(0.1)
                    wait_time += 0.1
                
                if not is_received:
                    logging.warning(f"⚠️ [Timeout] Node {node_id} 응답 없음")

                await asyncio.sleep(self.poll_interval)

    async def run(self):
        if not self.connect_serial():
            logging.error("초기화 실패.")
            return

        try:
            await self.poll_sequence()
        except KeyboardInterrupt:
            logging.info("서비스를 종료합니다.")
        finally:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.close()
            await self.http_client.aclose()

if __name__ == "__main__":
    gateway = LoRaEdgeGateway()
    asyncio.run(gateway.run())