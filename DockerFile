# 베이스 이미지 선택
FROM python:3.11-slim

# 작업 디렉터리 설정
WORKDIR /app

# requirements.txt 먼저 복사하고 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 소스 파일 복사
COPY . .

# 서버 실행 명령어 
CMD ["python", "poo.py"]