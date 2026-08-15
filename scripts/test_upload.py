"""第 2 步真实上传测试：按浏览器方式（UTF-8 文件名）走 HTTP 全流程。

验证：
1. 正常 PDF：返回文件名/页数/大小/SHA256，且响应不含本机路径
2. 第一页预览：可下载 PNG
3. 坏文件（非 PDF）：返回清晰中文错误
4. 设置接口：只返回 Gemini+DeepSeek / K3+DeepSeek 两组
"""

from __future__ import annotations

import json
import mimetypes
import re
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8766"
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "课堂图纸样张.pdf"
PRIVATE_ROOT = Path(__file__).resolve().parent.parent / "runtime" / "private"

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}  {detail}")


def multipart(field: str, filename: str, raw: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "----testboundary123456"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + raw + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def request(method: str, path: str, body: bytes | None = None, content_type: str | None = None):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}


def main() -> None:
    # 1) 正常上传
    raw = SAMPLE.read_bytes()
    body, ctype = multipart("file", SAMPLE.name, raw, "application/pdf")
    status, data, _ = request("POST", "/api/v1/documents", body, ctype)
    doc = json.loads(data)["document"] if status == 200 else {}
    check("上传返回 200", status == 200, f"status={status}, body={data[:200]}")
    check("文件名 UTF-8 正确", doc.get("filename") == "课堂图纸样张.pdf", f"filename={doc.get('filename')!r}")
    check("页数=3", doc.get("page_count") == 3, f"page_count={doc.get('page_count')}")
    check("大小=7179", doc.get("size") == len(raw), f"size={doc.get('size')}")
    check("SHA256 返回", bool(re.fullmatch(r"[0-9a-f]{64}", doc.get("sha256", ""))), f"sha256={doc.get('sha256')}")
    resp_text = data.decode("utf-8", "ignore")
    check("响应不含本机路径", "D:\\" not in resp_text and "0815" not in resp_text, resp_text[:150])

    doc_id = doc.get("document_id", "")

    # 2) 元信息接口
    status, data, _ = request("GET", f"/api/v1/documents/{doc_id}")
    check("元信息接口 200", status == 200, f"status={status}")
    check("元信息无路径", b"0815" not in data and b"D:\\" not in data)

    # 3) 第一页预览
    status, data, headers = request("GET", f"/api/v1/documents/{doc_id}/preview/first")
    check("预览 200 且为 PNG", status == 200 and headers.get("content-type", "").startswith("image/png"),
          f"status={status}, ct={headers.get('content-type')}")
    check("预览图片非空", len(data) > 1000, f"bytes={len(data)}")
    check("预览无缓存(no-store)", headers.get("cache-control") == "no-store", f"cc={headers.get('cache-control')}")

    # 4) 坏文件：非 PDF
    fake = b"this is not a pdf at all" * 100
    body, ctype = multipart("file", "readme.txt", fake, "text/plain")
    status, data, _ = request("POST", "/api/v1/documents", body, ctype)
    msg = json.loads(data).get("message", "") if status >= 400 else ""
    check("坏文件被拒(400)", status == 400, f"status={status}")
    check("坏文件提示清晰", "PDF" in msg, f"msg={msg}")

    # 5) 伪 PDF 头：%PDF- 开头但损坏
    fake2 = b"%PDF-1.7\n%%EOF garbage not really a pdf"
    body, ctype = multipart("file", "broken.pdf", fake2, "application/pdf")
    status, data, _ = request("POST", "/api/v1/documents", body, ctype)
    msg = json.loads(data).get("message", "") if status >= 400 else ""
    check("损坏 PDF 被拒", status == 400, f"status={status}, msg={msg}")

    # 6) 设置接口
    status, data, _ = request("GET", "/api/v1/settings/providers")
    providers = json.loads(data).get("providers", []) if status == 200 else []
    labels = [p.get("label", "") for p in providers]
    check("设置只有两组", labels == ["Gemini + DeepSeek", "K3 + DeepSeek"], f"labels={labels}")
    check("设置不含密钥字样", b"key" not in data.lower() and b"sk-" not in data, data[:200])

    # 7) 私有目录里确实存了原文件与预览
    originals = list(PRIVATE_ROOT.glob("*_original.pdf")) if PRIVATE_ROOT.exists() else []
    previews = list(PRIVATE_ROOT.glob("*_page_1.png")) if PRIVATE_ROOT.exists() else []
    check("私有目录存有原文件", len(originals) >= 1, f"originals={len(originals)}")
    check("私有目录存有预览图", len(previews) >= 1, f"previews={len(previews)}")

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
