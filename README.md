# ComfyUI EXIF Viewer

ComfyUI, A1111/WebUI, NovelAI 이미지에 저장된 프롬프트와 생성 설정을 확인하는 Windows 메타데이터 뷰어입니다. PNG뿐 아니라 WEBP/JPEG의 EXIF `UserComment`와 XMP도 읽습니다.

[최신 Windows 릴리스와 SHA-256](https://github.com/n0va39/ComfyUI-EXIF-viewer/releases/latest) · [전체 릴리스](https://github.com/n0va39/ComfyUI-EXIF-viewer/releases) · [프롬프트 추적 지원 문서](docs/PROMPT_TRACKING.md)

## 빠른 시작

1. 최신 릴리스의 `ComfyUI-EXIF-viewer.exe`를 다운로드해 실행합니다. 설치 과정은 없습니다.
2. 이미지 파일을 창 왼쪽에 드롭하거나 드롭 영역을 클릭해 파일을 엽니다.
3. `Prompt`, `Negative`, `Settings`, `Resources`, `Workflow`, `Raw` 탭에서 저장된 정보를 확인합니다.

여러 파일이나 지원 URL을 한 번에 드롭하면 대기열에 추가되어 순서대로 열립니다. 드롭 영역을 선택한 뒤 `Ctrl+V`를 누르면 로컬 파일 경로, 이미지 URL 또는 클립보드 이미지도 열 수 있습니다.

## 주요 기능

- PNG, WEBP, JPEG에 저장된 ComfyUI/A1111/NovelAI 메타데이터 표시
- Prompt, Negative, Settings, Civitai Resources, Workflow, Raw 데이터 분리
- 여러 이미지와 URL의 순차 열기
- 최근 이미지 목록과 앱 다운로드 캐시 관리
- A1111 `parameters`가 없는 ComfyUI 이미지의 워크플로우 프롬프트 보조 추정
- Windows DPI 배율 보정과 이미지 미리보기

## 지원 파일과 메타데이터

| 파일 | 읽는 영역 |
| --- | --- |
| PNG | `tEXt`, `zTXt`, `iTXt` |
| PNG stealth metadata | `stealth_pnginfo`, `stealth_pngcomp`, `stealth_rgbinfo`, `stealth_rgbcomp` |
| WEBP | RIFF `EXIF`, `XMP` |
| JPEG | APP1 EXIF, XMP, Comment |

| 생성 도구 | 인식하는 데이터 |
| --- | --- |
| ComfyUI | `parameters`, API `prompt`, UI `workflow` |
| A1111/WebUI 호환 도구 | `parameters`, EXIF `UserComment` |
| NovelAI | PNG `Comment` JSON의 `prompt`, `uc` |
| 기타 | EXIF, XMP, Comment 원문 |

메타데이터가 이미지에 저장되지 않았다면 이 프로그램도 해당 값을 복원할 수 없습니다.

## 화면과 데이터 관리

- `File` 메뉴에서 이미지 열기, 현재 탭 복사/저장, 앱 캐시 삭제를 실행할 수 있습니다.
- `Recent images`에서 최근 파일을 다시 열 수 있으며 `View > Recent Images`로 목록 표시를 전환할 수 있습니다.
- 최근 목록은 `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\recent_images.json`에 저장됩니다.
- 클립보드 이미지와 허용된 HTTPS 이미지 URL의 다운로드 결과는 `%LOCALAPPDATA%\ComfyUI-EXIF-viewer\cache`에 저장됩니다.
- `File > Clear Cache`는 앱이 만든 최근 목록과 다운로드 캐시만 지우며 로컬 원본 이미지는 삭제하지 않습니다.
- URL 드롭은 현재 `https://ac-o.namu.la/...` 주소만 허용합니다.

## 보안과 개인정보

- 이미지는 로컬에서 처리하며 앱 자체의 분석, 광고, 계정 또는 백그라운드 업데이트 확인은 없습니다.
- URL 다운로드는 사용자가 직접 드롭하거나 붙여 넣은 `https://ac-o.namu.la/...` 이미지에만 실행되며 다른 호스트로 향하는 리디렉션은 거부합니다.
- 이미지 파일은 512 MB, 압축 해제된 단일 메타데이터 항목은 16 MB로 제한합니다.
- 공식 Windows 바이너리는 GitHub Releases에서만 배포하고 릴리스의 SHA-256과 대조합니다.
- 저장 데이터와 네트워크 동작은 [PRIVACY.md](PRIVACY.md), 취약점 신고 절차는 [SECURITY.md](SECURITY.md)를 참고하세요.

## 워크플로우 프롬프트 추측

![워크플로우 프롬프트 추측](image/%EC%B6%94%EC%B8%A1.png)

ComfyUI `workflow` 또는 API `prompt`는 있지만 A1111/WebUI `parameters`가 없는 이미지에서 사용할 수 있는 보조 기능입니다. 기본값은 OFF이며, 활성화한 경우에만 그래프 추적을 실행합니다.

- `Auto CLIP`: 첫 번째 활성 Sampler의 `positive`/`negative` 입력에서 텍스트 원본을 역추적합니다.
- `Manual nodes`: Positive/Negative 노드 ID를 직접 지정합니다. ID는 쉼표, 공백 또는 세미콜론으로 구분합니다.
- `Concat`: 여러 텍스트를 합칠 구분자입니다. `\n` 같은 escape 문자를 사용할 수 있습니다.
- 결과와 추적 경로, 경고는 `Guess` 탭에 표시됩니다.

자동 추적은 특정 노드 팩의 설치 여부가 아니라 이미지에 저장된 노드 타입, 입력 이름, 출력 포트와 링크 구조를 기준으로 동작합니다.

| 지원 범위 | 대표 예시 |
| --- | --- |
| 기본 Sampler와 Text Encode | `KSampler`, `CLIPTextEncode`, SDXL Text Encode |
| Context/pipe | rgthree Context의 positive/negative 출력, `base_ctx` |
| 가상 연결과 bypass | KJNodes `SetNode`/`GetNode`, bypass 노드 |
| 다중 conditioning 출력 | Efficiency Loader의 `CONDITIONING+`/`CONDITIONING-` |
| 문자열 결합 | Comfyroll `CR Text Concatenate`, 번호형 text/string/prompt 입력 |
| 실행 결과가 저장된 표시 노드 | pysssss `ShowText` |
| Wildcard/동적 프롬프트의 저장 결과 | `populated_text`, `wildcard_text` |
| 최신 워크플로우 구조 | ComfyUI Node 2.0 `definitions.subgraphs`와 객체형 링크 |

지원 판정 규칙, 정확한 입력 키, 출력 포트 처리, 새 노드 추가 절차는 [프롬프트 추적 지원 및 확장 가이드](docs/PROMPT_TRACKING.md)를 참고하세요.

### 추정할 수 없는 경우

- 실행 시점에만 만들어지고 API `prompt`, UI `workflow`, `ShowText` 등에 저장되지 않은 문자열
- 텍스트와 conditioning 사이의 연결이 저장 데이터에서 끊긴 워크플로우
- 출력 포트의 positive/negative 의미를 저장된 정보만으로 구분할 수 없는 커스텀 노드

이 기능은 원본 프롬프트를 보장하는 복구 기능이 아닙니다. API `prompt` 메타데이터까지 함께 저장된 이미지가 가장 정확합니다. A1111/WebUI `parameters`가 이미 있으면 저장된 값을 우선하며 추측은 실행하지 않습니다.

## Civitai 리소스

![ComfyUI Image Saver Civitai 리소스](image/image%20saver.png)

[`alexopus/ComfyUI-Image-Saver`](https://github.com/alexopus/ComfyUI-Image-Saver)가 저장한 `Civitai resources`가 있으면 `Resources` 탭에서 이름, 버전, 가중치와 AIR를 표시합니다. 리소스 이름을 클릭하면 `civitai.red`의 해당 모델 버전으로 이동합니다. 메타데이터에 확정 저장된 리소스만 표시하며 프롬프트 문자열에서 임의로 추정하지 않습니다.

## 소스에서 실행

```bat
run_viewer.bat
```

`run_viewer.bat`은 `.venv`를 우선 사용하며 필요한 패키지가 없으면 `requirements.txt`를 설치합니다. 특정 이미지를 바로 열 수도 있습니다.

```bat
run_viewer.bat "C:\path\to\image.webp"
```

GUI 없이 터미널에서 메타데이터를 출력하려면 다음 명령을 사용합니다.

```bat
.venv\Scripts\python.exe comfy_exif_viewer.py --dump "C:\path\to\image.webp"
```

## 빌드와 테스트

Windows 단일 EXE 빌드:

```bat
build_exe.bat
```

결과는 `dist\ComfyUI-EXIF-viewer.exe`에 생성됩니다.
빌드는 `requirements.lock`의 버전과 해시를 사용하고 테스트, `compileall`, 패키지 smoke, SHA-256 생성을 순서대로 수행합니다. 결과 체크섬은 `dist\SHA256SUMS.txt`에 저장됩니다.

공개 릴리스에 Authenticode 서명을 적용하려면 인증서가 설치된 환경에서 다음 변수를 설정한 뒤 빌드합니다. 인증서 비밀번호나 개인 키는 저장소에 넣지 않습니다.

```powershell
$env:SIGNTOOL_EXE = "C:\path\to\signtool.exe"
$env:CODE_SIGN_CERT_SUBJECT = "Verified publisher name"
$env:CODE_SIGN_TIMESTAMP_URL = "https://timestamp.example.com"
.\scripts\build_windows.ps1
```

서명 변수가 없으면 로컬 테스트용 무서명 EXE를 만들고 경고를 출력합니다. 공개 릴리스에는 서명된 EXE 또는 Microsoft Store의 서명된 MSIX를 사용하세요.

잠금 파일 갱신:

```powershell
uv pip compile requirements.txt --generate-hashes --python-version 3.14 --python-platform windows --output-file requirements.lock
```

전체 테스트:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests
```

## 배경과 참고

이 프로젝트의 이미지 드롭 영역과 프롬프트/옵션 분리 UI는 [`DCP-arca/NAI-Tag-Viewer`](https://github.com/DCP-arca/NAI-Tag-Viewer)를 참고했습니다. ComfyUI-Image-Saver가 JPEG/WEBP에 저장하는 EXIF `UserComment`까지 로컬에서 확인할 필요가 있어 PNG 중심 메타데이터 읽기 범위를 확장했습니다.
