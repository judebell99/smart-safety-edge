# 🛰️ Smart Safety Control - Edge Gateway
> Raspberry Pi 기반 UWB/LoRa 이종 데이터 통합 송신기

라즈베리파이에서 수집된 UWB 위치 데이터와 LoRa 안전모 센서 데이터를 통합하여 중앙 API 서버로 전송합니다.

## 🛠 Tech Stack
- **Platform:** Raspberry Pi (Linux)
- **Language:** Python 3.x
- **Communication:** HTTP/JSON (to Backend), Serial/SPI (Sensor)

## ⚙️ Environment Variables
`.env` 파일을 생성하고 다음 값을 설정하세요.
```env
API_URL=[https://your-backend-url.com/api/telemetry](https://your-backend-url.com/api/telemetry)
```

## 🚀 Getting Started
```bash
# 센서 드라이버 설치 후 실행
python mock_edge.py
```
