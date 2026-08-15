"""生成课堂测试图纸 PDF（第 2 步真实上传测试用）。

生成一个 3 页的图纸样张：第 1 页含标题栏与图形，第 2/3 页为延续内容。
不含真实图纸，仅用于课堂演示上传与预览。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "samples" / "课堂图纸样张.pdf"


def draw_page(page: pymupdf.Page, page_no: int, total: int) -> None:
    w, h = page.rect.width, page.rect.height
    # 边框
    page.draw_rect(pymupdf.Rect(20, 20, w - 20, h - 20), color=(0, 0, 0), width=1.2)
    # 标题栏（右下角）
    tb = pymupdf.Rect(w - 200, h - 90, w - 30, h - 30)
    page.draw_rect(tb, color=(0, 0, 0), width=1)
    for i in range(1, 3):
        page.draw_line((tb.x0, tb.y0 + i * 20), (tb.x1, tb.y0 + i * 20), color=(0, 0, 0))
    page.draw_line(((tb.x0 + tb.x1) / 2, tb.y0), ((tb.x0 + tb.x1) / 2, tb.y1), color=(0, 0, 0))
    page.insert_text((tb.x0 + 4, tb.y0 + 14), "标题栏", fontsize=8)
    page.insert_text((tb.x0 + 4, tb.y0 + 34), f"第 {page_no}/{total} 页", fontsize=8)
    page.insert_text(((tb.x0 + tb.x1) / 2 + 4, tb.y0 + 14), "图号: 课堂-001", fontsize=8)

    # 简单图形：主视图（矩形+圆）
    center = pymupdf.Rect(w / 2 - 120, h / 2 - 90, w / 2 + 120, h / 2 + 90)
    page.draw_rect(center, color=(0, 0, 0), width=1.5)
    page.draw_circle((w / 2, h / 2), 40, color=(0, 0, 0), width=1.5)
    page.draw_line((w / 2 - 120, h / 2), (w / 2 - 60, h / 2), color=(0, 0, 0))
    # 尺寸标注示意
    page.insert_textbox(
        pymupdf.Rect(w / 2 - 150, h / 2 + 40, w / 2 + 150, h / 2 + 60),
        "Ø80 ±0.05",
        fontsize=10,
        align=pymupdf.TEXT_ALIGN_CENTER,
    )
    # 技术要求
    page.insert_text((60, h - 120), "技术要求：", fontsize=9)
    page.insert_text((60, h - 105), "1. 未注倒角 C0.5；", fontsize=9)
    page.insert_text((60, h - 90), "2. 表面粗糙度 Ra 3.2。", fontsize=9)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for n in range(3):
        page = doc.new_page(width=595, height=842)  # A4 纵向
        draw_page(page, n + 1, 3)
    doc.save(OUT)
    doc.close()
    print(f"生成样张：{OUT}（3 页）")


if __name__ == "__main__":
    main()
