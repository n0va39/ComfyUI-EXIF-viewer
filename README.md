# ComfyUI EXIF Viewer

ComfyUI 이미지 메타데이터를 확인하는 간단한 뷰어이다.

UI 흐름은 원본 저장소 `DCP-arca/NAI-Tag-Viewer`의 이미지 드롭 영역과 프롬프트/옵션 분리 표시를 참고했다.

https://github.com/DCP-arca/NAI-Tag-Viewer

## 원인

기존 `ndg_gui.exe`의 원본인 `DCP-arca/NAI-Tag-Viewer`는 README 기준 PNG 중심 뷰어이며, `NaiDictGetter.py`도 PIL `img.info`와 stealth PNG 정보를 주로 확인한다.

[`alexopus/ComfyUI-Image-Saver`](https://github.com/alexopus/ComfyUI-Image-Saver)는 PNG에는 `parameters`, `prompt`, `workflow`를 PNG 텍스트 청크로 저장하지만, JPEG/WEBP에는 A1111/Civitai 형식 문자열을 EXIF `UserComment`에 저장한다. 따라서 WEBP의 RIFF `EXIF` 청크와 TIFF/EXIF `UserComment`를 직접 읽지 않으면 Civitai에서는 보이는 정보가 로컬 뷰어에서는 보이지 않을 수 있다.

## 실행 방법

EXE 빌드 후 실행:

```bat
dist\ComfyUI-EXIF-viewer.exe
```

소스에서 실행:

```bat
run_viewer.bat
```

`run_viewer.bat`은 `.venv`를 우선 사용하고, 드래그앤드롭/이미지 미리보기에 필요한 패키지가 없으면 `requirements.txt`를 설치한다.

특정 파일을 바로 열려면:

```bat
run_viewer.bat "sample\style_1_2026-06-13-151752.webp"
```

터미널에서 텍스트로 확인하려면:

```bat
python comfy_exif_viewer.py --dump "sample\style_1_2026-06-13-151752.webp"
```

## EXE 빌드

```bat
build_exe.bat
```

빌드 결과는 `dist\ComfyUI-EXIF-viewer.exe`에 생성된다.

## 지원 형식

- PNG: `tEXt`, `zTXt`, `iTXt`
- PNG stealth metadata: `stealth_pnginfo`, `stealth_pngcomp`, `stealth_rgbinfo`, `stealth_rgbcomp`
- WEBP: RIFF `EXIF`, `XMP`
- JPEG: APP1 EXIF, XMP, Comment

## 지원 플랫폼

- ComfyUI: `parameters`, `prompt`, `workflow`
- A1111/WebUI 호환: `parameters`, EXIF `UserComment`
- NovelAI: PNG `Comment` JSON의 `prompt`, `uc`
- 기타: EXIF/XMP/Comment 원본 표시

## UI

- Windows DPI scaling을 적용해 150% 배율에서 창과 텍스트 크기를 보정한다.
- 좌측 드롭 영역에 이미지 파일을 끌어다 놓으면 썸네일을 표시한다.
- Chrome 등 브라우저에서 아카라이브 이미지 URL(`ac-o.namu.la`)을 끌어오면 임시 파일로 다운로드한 뒤 메타데이터를 읽는다.
- 우측 탭에서 Prompt, Negative, Settings, Resources, Workflow, Raw를 분리해서 확인한다.
- `Resources` 탭은 메타데이터에 확정 저장된 `Civitai resources`만 표시한다.
- [`alexopus/ComfyUI-Image-Saver`](https://github.com/alexopus/ComfyUI-Image-Saver) 커스텀 노드 방식으로 저장된 리소스는 이름을 클릭해 `civitai.red`의 해당 모델 버전으로 연결할 수 있다.

## 사용 예시

### 워크플로우 프롬프트 추측

![워크플로우 프롬프트 추측](image/%EC%B6%94%EC%B8%A1.png)

ComfyUI 워크플로우만 있고 A1111/WebUI `parameters`가 없을 때 `Workflow prompt guess`를 켜면 첫 번째 샘플러 기준으로 CLIPTextEncode 입력을 추적해 프롬프트를 보조적으로 추정한다.

### Civitai 리소스 확인

![ComfyUI Image Saver Civitai 리소스](image/image%20saver.png)

[`alexopus/ComfyUI-Image-Saver`](https://github.com/alexopus/ComfyUI-Image-Saver)가 저장한 `Civitai resources` 메타데이터가 있으면 `Resources` 탭에서 사용된 리소스 이름, 버전, 가중치, AIR 값을 확인할 수 있다. 리소스 이름은 `civitai.red` 링크로 연결된다.

## 워크플로우 프롬프트 추측

A1111/WebUI `parameters`가 없는 ComfyUI 이미지에서만 보조적으로 사용한다.

- 기본값은 OFF이며, 켜기 전에는 워크플로우 추적을 실행하지 않는다.
- `Auto CLIP`: KSampler의 `positive`/`negative` 입력에서 CLIPTextEncode 노드를 역추적한다.
- `Manual nodes`: Positive/Negative 노드 ID를 직접 입력한다. 여러 노드는 쉼표, 공백, 세미콜론으로 구분한다.
- `Concat`: 여러 텍스트 노드를 합칠 때 사용할 구분자이다. `\n` 같은 escape 문자를 사용할 수 있다.
- 추측 결과와 추적 정보는 `Guess` 탭에 표시된다.

### 지원하는 노드 범위

워크플로우 추측은 특정 커스텀 노드 이름만 대상으로 하지 않고, 저장된 ComfyUI API prompt/workflow 그래프의 노드 타입과 입력 이름을 기준으로 처리한다.

- Sampler 계열: 타입 또는 클래스명에 `sampler`가 포함된 노드. `selector`, `config`는 제외한다. 여러 샘플러가 있으면 가장 먼저 실행되는 샘플러를 기준으로 한다.
- CLIP text encode 계열: 타입 또는 클래스명에 `cliptextencode`가 포함된 노드.
- Context 계열: 타입 또는 클래스명에 `context`가 포함된 노드. 출력 포트 기준으로 `positive`, `negative`, `base_ctx`를 추적한다.
- Text/String 노드: `caption`, `prompt`, `text`, `string`, `value`, `positive`, `negative` 입력 값을 가진 노드.
- Text/String concat 계열: `text_1`, `prompt_1`, `string_1`처럼 번호가 붙은 입력을 가진 병합 노드.
- UI workflow 저장값: `widgets_values` 또는 `properties`에 들어 있는 문자열 값.

동작 확인된 예시는 `CLIPTextEncode`, `KSampler`/Sampler 계열, `PrimitiveStringMultiline`, `StringConcatenate`, `Merge Strings v2 [RvTools]`, Context/pipe 계열 노드이다. 위 패턴에 맞지 않는 커스텀 노드는 `Manual nodes`에서 노드 ID를 지정해도 텍스트를 찾지 못할 수 있다.

## 테스트

```bat
python -m unittest discover
```
