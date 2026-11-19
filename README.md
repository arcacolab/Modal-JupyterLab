# Modal JupyterLab Launcher

Modal GPU Sandbox 환경에서 JupyterLab을 실행하고 관리할 수 있는 로컬 데스크톱 런처(Tkinter GUI)입니다.

## 요구 사항

- **Python**: Python 3.10 이상 (3.11+ 권장)
- **Tkinter**: Python 표준 GUI 라이브러리 (Windows 기본 포함 / Linux의 경우 `python3-tk` 필요)
- **Modal 계정**: [Modal](https://modal.com) 계정

## 설치 및 실행

1. 저장소 클론 및 이동
   ```bash
   git clone https://github.com/arcacolab/Modal-JupyterLab.git
   cd Modal-JupyterLab
   ```

2. 런처 실행
   ```bash
   python "Modal_JupyterLab_V1.py"
   ```
   *(필요 시 `pip install -r requirements.txt`로 먼저 설치할 수도 있습니다.)*

3. **최초 실행 시 패키지 설치 / 업데이트**
   - 상단 **인증** 패널에서 **설치 / 업데이트** 버튼을 누르면 자동으로 `modal` 패키지가 설치 및 업데이트됩니다.
   - 설치가 끝나면 **새로고침**으로 패키지 및 인증 상태를 확인할 수 있습니다.

4. **브라우저 로그인으로 토큰 인증**
   - **브라우저 로그인** 버튼을 누르면 Modal 토큰 인증(`modal token new`) 절차가 진행됩니다.
   - 브라우저에서 계정 로그인을 완료하면 로컬 인증 정보가 등록됩니다. (토큰 ID/Secret을 직접 입력하는 **토큰 등록**도 지원합니다.)

## 실행 설정 및 사용법

1. **App 이름 설정**: 좌측 **실행 설정**의 `App 이름` 입력란에서 실행할 Live App 이름을 원하는 대로 편집할 수 있습니다.
2. **GPU 및 최대 시간 설정**:
   - `GPU`에서 사용할 GPU(`T4`, `L4`, `A10G`, `A100`, `H100`)를 선택합니다.
   - `최대 시간`에서 샌드박스 유지 시간(`1시간` ~ `24시간`)을 설정합니다.
3. **샌드박스 시작 및 접속**: **▶ 시작** 버튼을 누르면 원격 Sandbox와 App이 생성되고 JupyterLab 접속 주소가 발급됩니다. (`주소 준비 시 브라우저 자동 열기` 옵션이 켜져 있으면 자동으로 브라우저가 열립니다.)
4. **종료 및 정리**: 작업이 끝나면 **■ 종료** 버튼을 눌러 원격 Sandbox와 App 정리를 시도합니다.

## 참고 사항

- `jupyterlab`, `notebook`, `torch`, `torchvision` 등은 로컬 PC가 아닌 원격 Modal Sandbox 컨테이너 이미지 내부에 설치됩니다.
- 실행 시 같은 폴더에 `_modal_jupyter_worker.py` 파일이 자동 생성되므로 `.gitignore`에 추가해 두는 것을 권장합니다.
