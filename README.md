# Modal JupyterLab Launcher

Modal GPU Sandbox 환경에서 JupyterLab을 원클릭으로 실행하고 관리할 수 있는 로컬 데스크톱 런처(Tkinter GUI)입니다.

## 주요 기능

- **Modal 인증 관리**: 패키지 설치/업데이트, 브라우저 로그인(`modal token new`), 토큰 직접 등록 및 상태 확인
- **실행 설정 커스터마이징**: App 이름 지정, GPU 선택(`T4`, `L4`, `A10G`, `A100`, `H100`), 최대 실행 시간(`1시간` ~ `24시간`) 설정
- **원클릭 접속 및 자동 열기**: 터널 준비 완료 시 접속 URL 표시, 원클릭 복사 및 브라우저 자동 실행
- **실시간 로그 뷰어**: 타임스탬프(`[HH:MM:SS]`) 로그 출력, 경고 숨김 필터, 로그 복사/지우기 지원
- **안전한 리소스 정리**: 종료 버튼 또는 창 닫기 시 원격 Modal Sandbox 및 App 정지 처리

## 요구 사항

- **OS**: Windows 권장 (Linux / macOS에서도 실행 가능)
- **Python**: Python 3.10 이상 (3.11+ 권장)
- **Tkinter**: Python 표준 GUI 라이브러리 (Windows 기본 포함 / Linux의 경우 `python3-tk` 필요)
- **Modal 계정**: [Modal](https://modal.com) 계정 및 API 토큰

## 설치 및 실행

1. 저장소 클론
   ```bash
   git clone <repository-url>
   cd <repository-folder>
