# -*- coding: utf-8 -*-
"""
Netlify 배포 스크립트

app/site 폴더 전체를 zip으로 묶어 Netlify에 업로드한다.
환경변수 (GitHub Actions에서는 저장소 비밀 금고 Secrets로 주입):
  NETLIFY_AUTH_TOKEN — Netlify 개인 액세스 토큰
  NETLIFY_SITE_ID    — 사이트 ID (dooin-stats)

실행: python netlify_deploy.py
"""

import io
import os
import sys
import zipfile
from pathlib import Path

import requests

SITE_DIR = Path(__file__).parent / "site"


def main():
    token = os.getenv("NETLIFY_AUTH_TOKEN", "").strip()
    site_id = os.getenv("NETLIFY_SITE_ID", "").strip()
    if not token or not site_id:
        sys.exit("NETLIFY_AUTH_TOKEN / NETLIFY_SITE_ID 환경변수가 필요합니다.")
    if not SITE_DIR.exists():
        sys.exit(f"배포할 폴더가 없습니다: {SITE_DIR} — 먼저 generator.py를 실행하세요.")

    # 사이트 폴더 → zip (메모리 상에서)
    buf = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(SITE_DIR.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(SITE_DIR).as_posix())
                file_count += 1
    buf.seek(0)
    print(f"압축 완료 — 파일 {file_count}개, {buf.getbuffer().nbytes / 1_048_576:.1f}MB")

    resp = requests.post(
        f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/zip"},
        data=buf.read(),
        timeout=300,
    )
    if resp.status_code in (200, 201):
        info = resp.json()
        print(f"배포 성공 — {info.get('ssl_url') or info.get('url')}")
    else:
        sys.exit(f"배포 실패 — HTTP {resp.status_code}: {resp.text[:200]}")


if __name__ == "__main__":
    main()
