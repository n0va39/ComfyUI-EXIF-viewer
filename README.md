# ComfyUI EXIF Viewer

ComfyUI 이미지 메타데이터를 확인하는 간단한 뷰어이다.

## 원인

기존 `ndg_gui.exe`의 원본인 `DCP-arca/NAI-Tag-Viewer`는 README 기준 PNG 중심 뷰어이며, `NaiDictGetter.py`도 PIL `img.info`와 stealth PNG 정보를 주로 확인한다.

`alexopus/ComfyUI-Image-Saver`는 PNG에는 `parameters`, `prompt`, `workflow`를 PNG 텍스트 청크로 저장하지만, JPEG/WEBP에는 A1111/Civitai 형식 문자열을 EXIF `UserComment`에 저장한다. 따라서 WEBP의 RIFF `EXIF` 청크와 TIFF/EXIF `UserComment`를 직접 읽지 않으면 Civitai에서는 보이는 정보가 로컬 뷰어에서는 보이지 않을 수 있다.

## 실행 방법

EXE 빌드 후 실행:

```bat
dist\ComfyUI-EXIF-viewer.exe
```

소스에서 실행:

```bat
run_viewer.bat
```

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
- WEBP: RIFF `EXIF`, `XMP`
- JPEG: APP1 EXIF, XMP, Comment

## 테스트

```bat
python -m unittest discover
```
