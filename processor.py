"""PDF 分组排序与重构输出模块"""

import os
import fitz


def group_and_sort(pages, remarks):
    """
    分组排序：
    1. 按 SKU 分组
    2. 同 SKU 内按仓库代码排序
    3. 箱号由系统自动从 1 开始编号
    """
    groups = {}
    for page in pages:
        sku = page['sku']
        if sku not in groups:
            groups[sku] = []
        groups[sku].append(page)

    sorted_groups = {}
    for sku, group_pages in groups.items():
        sorted_pages = sorted(
            group_pages,
            key=lambda p: (p.get('destination') or '', p.get('page_num') or 0)
        )
        sorted_groups[sku] = sorted_pages

    return sorted_groups


def generate_output_pdf(grouped_pages, remarks, output_dir, progress_callback=None):
    """生成按 SKU 分组的输出 PDF 文件"""
    os.makedirs(output_dir, exist_ok=True)

    total_pages = 0
    exception_doc = None

    for sku, pages in grouped_pages.items():
        label = remarks.get(sku, sku)
        safe_label = _sanitize_filename(label)
        output_path = os.path.join(output_dir, f"{safe_label}_整理版.pdf")

        out_doc = fitz.open()

        for page_data in pages:
            src_path = page_data['source_file']
            page_num = page_data['page_num']

            try:
                src_doc = fitz.open(src_path)
                if page_num < len(src_doc):
                    out_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                    new_page = out_doc[-1]
                    _add_label_to_page(
                        new_page,
                        label,
                        page_data.get('box_current'),
                        page_data.get('destination')
                    )
                src_doc.close()
            except Exception as e:
                print(f"  警告：处理页面 {page_num} 时出错：{e}")
                continue

            total_pages += 1
            if progress_callback:
                progress_callback(total_pages)

        if len(out_doc) > 0:
            out_doc.save(output_path)
            print(f"  已生成：{output_path}（{len(out_doc)} 页）")
        out_doc.close()

    # 异常文件
    exception_pages = []
    for pages in grouped_pages.values():
        pass  # exceptions handled separately

    return total_pages


def process_and_output(pages, exceptions, remarks, output_dir, progress_callback=None):
    """完整处理流程：分组、排序、输出"""
    os.makedirs(output_dir, exist_ok=True)

    grouped = group_and_sort(pages, remarks)

    total_pages = 0
    exception_count = 0

    # 处理正常分组
    for sku, group_pages in grouped.items():
        label = remarks.get(sku, sku)
        safe_label = _sanitize_filename(label)
        output_path = os.path.join(output_dir, f"{safe_label}_整理版.pdf")

        out_doc = fitz.open()

        for box_num, page_data in enumerate(group_pages, start=1):
            src_path = page_data['source_file']
            page_num = page_data['page_num']

            try:
                src_doc = fitz.open(src_path)
                if page_num < len(src_doc):
                    out_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                    new_page = out_doc[-1]
                    _add_label_to_page(
                        new_page,
                        label,
                        box_num,
                        page_data.get('destination')
                    )
                src_doc.close()
            except Exception as e:
                print(f"  警告：页面处理出错 - {e}")

            total_pages += 1
            if progress_callback:
                progress_callback(total_pages)

        if len(out_doc) > 0:
            out_doc.save(output_path)
            print(f"  已生成：{output_path}（{len(out_doc)} 页）")
        out_doc.close()

    # 处理异常页面
    if exceptions:
        exc_path = os.path.join(output_dir, "异常_需人工核对.pdf")
        exc_doc = fitz.open()

        for page_data in exceptions:
            src_path = page_data['source_file']
            page_num = page_data['page_num']

            try:
                src_doc = fitz.open(src_path)
                if page_num < len(src_doc):
                    exc_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                    new_page = exc_doc[-1]
                    _add_exception_label(new_page, page_data)
                src_doc.close()
            except Exception:
                pass

            exception_count += 1
            total_pages += 1
            if progress_callback:
                progress_callback(total_pages)

        if len(exc_doc) > 0:
            exc_doc.save(exc_path)
            print(f"  已生成异常文件：{exc_path}（{len(exc_doc)} 页）")
        exc_doc.close()

    return total_pages, exception_count


CJK_FONT = "china-s"  # PyMuPDF 内置简体中文支持字体


def _add_label_to_page(page, label, box_num, destination):
    """在 PDF 页面条形码下方添加标签（不遮挡条形码）"""
    rect = page.rect
    text = label
    if box_num is not None:
        text += f" - 第{box_num}箱"
    if destination:
        text += f" [{destination}]"

    font_size = 9
    text_width = fitz.get_text_length(text, fontname=CJK_FONT, fontsize=font_size)

    # 放在条形码下方和 "请不要盖住" 之间的空隙，靠左
    x = 5
    y = rect.height * 0.75

    # 添加白色背景提升可读性
    bg_rect = fitz.Rect(x - 4, y - font_size - 2, x + text_width + 4, y + 4)
    page.draw_rect(bg_rect, color=(0.95, 0.95, 0.95), fill=(0.95, 0.95, 0.95))

    page.insert_text(
        (x, y),
        text,
        fontsize=font_size,
        fontname=CJK_FONT,
        color=(0.1, 0.1, 0.1)
    )


def _add_exception_label(page, page_data):
    """为异常页面添加标记"""
    rect = page.rect
    text = "需人工核对"
    error = page_data.get('error', '')
    if error:
        text += f" - {error[:30]}"

    font_size = 14
    text_width = fitz.get_text_length(text, fontname=CJK_FONT, fontsize=font_size)
    x = max(10, (rect.width - text_width) / 2)
    y = rect.height - 15

    bg_rect = fitz.Rect(x - 4, y - font_size, x + text_width + 4, y + 4)
    page.draw_rect(bg_rect, color=(1, 0.9, 0.9), fill=(1, 0.9, 0.9))

    page.insert_text(
        (x, y),
        text,
        fontsize=font_size,
        fontname=CJK_FONT,
        color=(0.8, 0, 0)
    )


def _detect_text_color(page, x, y, width, height):
    """检测区域背景亮度，返回合适的文字颜色"""
    try:
        # 简单方案：默认使用深色文字
        return (0.1, 0.1, 0.1)
    except Exception:
        return (0.1, 0.1, 0.1)


def _sanitize_filename(name):
    """清理文件名中的非法字符"""
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        name = name.replace(ch, '_')
    name = name.strip('. ')
    return name or "unnamed"
