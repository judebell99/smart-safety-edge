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
        self.api_endpoint = os.getenv("FASTAPI_ENDPOINT", "http://localhost:8000/api/v1/sensor-data")
        self.gateway_id = os.getenv("GATEWAY_ID", "EDGE_PI_01")
        
        self.serial_conn = None
        self.http_client = httpx.AsyncClient()
        
        # 가이드 문서 기반 파라미터 튜닝
        # self.target_nodes = ["01"]
        self.target_nodes = ["01", "02", "03"]
        self.node_timeout = 2.0  # 가이드 권장값: 2초
        self.poll_interval = 2.0 # 각 노드 폴링 간 휴지기

    def connect_serial(self) -> bool:
        """LoRa 포트 오픈 및 기본 핸드쉐이크"""
        try:
            self.serial_conn = serial.Serial(
                port=self.port, baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=1
            )
            logging.info(f"📡 포트 오픈 성공: {self.port} (8-N-1)")
            
            # 모듈 생존 확인
            self.serial_conn.write(b'AT\r\n')
            time.sleep(0.5)
            if self.serial_conn.in_waiting > 0:
                self.serial_conn.read_all() # 버퍼 초기화
                logging.info("✅ LoRa 모듈 핸드쉐이크(OK) 완료.")
                return True
            return False
        except serial.SerialException as e:
            logging.error(f"시리얼 연결 실패: {e}")
            return False

    def parse_payload(self, node_id: str, raw_data: str):
        """가이드 스펙에 맞춘 RAK3272S 데이터 파서"""
        # RAK 모듈은 보통 데이터 수신 시 +EVT:RXP2P:... 형태로 들어옵니다.
        # 실제 데이터 페이로드는 16진수(Hex) 형태이거나 텍스트일 수 있습니다.
        # 가이드 예시(01TEMP:25.5)를 기준으로 문자열 파싱을 수행합니다.
        
        try:
            # 예: 수신된 문자열 어딘가에 "01TEMP:25.5"가 포함되어 있다고 가정
            if node_id in raw_data:
                # "01TEMP:25.5" 추출 로직 (실제 수신 포맷에 따라 수정 필요할 수 있음)
                # 데이터 포맷이 고정된다면 정규표현식(re)을 쓰는 것이 더 좋습니다.
                logging.info(f"✅ [파싱 성공] 노드 {node_id} 데이터 추출: {raw_data}")
                
                # API로 보낼 JSON 딕셔너리 생성
                parsed_data = {
                    "gateway_id": self.gateway_id,
                    "node_id": node_id,
                    "raw_payload": raw_data,
                    "timestamp": int(time.time() * 1000)
                }
                return parsed_data
        except Exception as e:
            logging.error(f"데이터 파싱 에러: {e}")
            
        return None
    
    async def send_at_command(self, command: str, wait_timeout: float = 1.0) -> bool:
        """
        AT 명령어를 전송하고 'OK' 응답이 올 때까지 대기하는 결정론적 래퍼 함수.
        성공하면 True, 실패/타임아웃 시 False를 반환합니다.
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            return False

        # 1. 잔여 버퍼를 깔끔하게 비워서 이전 가비지 데이터를 날림
        self.serial_conn.reset_input_buffer()
        
        # 2. 명령어 전송
        cmd_bytes = f"{command}\r\n".encode('utf-8')
        self.serial_conn.write(cmd_bytes)
        logging.debug(f"▶️ [CMD 전송] {command}")

        # 3. OK 응답 대기 로직 (비동기 논블로킹)
        elapsed_time = 0.0
        check_interval = 0.05  # 50ms마다 버퍼 확인

        while elapsed_time < wait_timeout:
            if self.serial_conn.in_waiting > 0:
                raw_line = self.serial_conn.readline().decode('utf-8', errors='replace').strip()
                
                # 에코(명령어 재출력)는 무시하고 상태 코드만 확인
                if raw_line == "OK":
                    return True
                elif "ERROR" in raw_line.upper():
                    logging.error(f"❌ [CMD 실패] {command} -> 응답: {raw_line}")
                    return False
            
            await asyncio.sleep(check_interval)
            elapsed_time += check_interval

        logging.warning(f"⚠️ [CMD 타임아웃] {command} 명령에 대한 'OK' 응답이 없습니다.")
        return False

    async def poll_sequence(self):
        """RAK3272S 가이드 기반 동기적 질의-응답 폴링 루프 (상태 검증 포함)"""
        logging.info("🚀 RAK3272S 맞춤형 폴링 스케줄러 구동 시작 (OK 검증 모드)...")
        await asyncio.sleep(2) 
        
        while True:
            for node_id in self.target_nodes:
                logging.info(f"---------------------------------------------------")
                logging.info(f"🔧 [Node {node_id}] 폴링 사이클 시작")

                # 1. P2P 모드 전환 확인
                if not await self.send_at_command("AT+NWM=0", wait_timeout=1.0):
                    logging.error(f"Node {node_id} 폴링 건너뜀 (NWM 설정 실패)")
                    continue

                # 2. 통신 파라미터 세팅 확인
                if not await self.send_at_command("AT+P2P=923000000:7:125:0:8:15", wait_timeout=1.0):
                    logging.error(f"Node {node_id} 폴링 건너뜀 (P2P 설정 실패)")
                    continue

                # 3. 폴링 데이터 송신 및 전송 완료 대기
                # RAK 모듈은 PSEND 시 OK를 바로 뱉고 공중으로 쏩니다.
                poll_cmd = f"AT+PSEND={node_id}FF"
                logging.info(f"👉 [TX] 폴링 요청 발사 (CMD: {poll_cmd})")
                if not await self.send_at_command(poll_cmd, wait_timeout=3.0):
                    logging.error(f"Node {node_id} 폴링 요청 실패")
                    continue

                # 4. 연속 수신 모드 진입 확인
                if not await self.send_at_command("AT+PRECV=65535", wait_timeout=3.0):
                    logging.error(f"Node {node_id} 수신 모드 전환 실패")
                    continue
                
                logging.info("📡 수신 모드(PRECV) 설정 성공. 슬레이브 응답 대기 중...")
                
                # 5. 슬레이브 실제 데이터 수신 대기 (가이드 권장 Timeout 2초)
                wait_time = 0.0
                is_received = False
                
                # 버퍼 한번 더 비워주기 (PSEND나 PRECV 과정에서 발생한 이벤트 찌꺼기 제거)
                # 단, 응답이 너무 빨리 왔을 경우를 대비해 신중하게 적용해야 하나 
                # 통상 PRECV 직후에 들어오므로 안전합니다.
                
                while wait_time < self.node_timeout:
                    if self.serial_conn.in_waiting > 0:
                        raw_line = self.serial_conn.readline().decode('utf-8', errors='replace').strip()
                        
                        # 모듈 자체 시스템 로그(+EVT:TXP2P DONE 등)나 쓰레기값 필터링
                        if raw_line and "OK" not in raw_line and not raw_line.startswith("AT+"):
                            
                            # 가이드 문서 규격대로 페이로드에 내 ID가 있는지 검증
                            if node_id in raw_line:
                                logging.info(f"📥 [RX] Node {node_id} 센서 데이터 수신: {raw_line}")
                                is_received = True
                                
                                # 파싱 및 백엔드 전송 (다음 단계에서 HTTP 코드 활성화)
                                parsed_data = self.parse_payload(node_id, raw_line)
                                # if parsed_data:
                                #     logging.info(f"☁️ 클라우드(API) 전송 대기 상태: {parsed_data}")
                                
                                break # 데이터 수신 완료 시 대기 루프 즉시 탈출
                    
                    await asyncio.sleep(0.1)
                    wait_time += 0.1
                
                if not is_received:
                    logging.warning(f"⚠️ [Timeout] Node {node_id} 응답 없음 (권장 타임아웃 {self.node_timeout}초 초과)")

                # 다음 노드로 넘어가기 전 전파 간섭 방지 휴지기
                await asyncio.sleep(self.poll_interval)

    async def run(self):
        """메인 이벤트 루프"""
        if not self.connect_serial():
            logging.error("초기화 실패. 데몬을 중단합니다.")
            return

        try:
            # 단일 스케줄러 실행 (수신/송신이 통합되었으므로 하나만 돌리면 됩니다)
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