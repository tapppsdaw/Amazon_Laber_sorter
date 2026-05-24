"""PDF 分组排序与重构输出模块"""

import os
import fitz


def match_forwarder_to_amazon(pages, remarks):
    """将货代标签与 FBA 标签匹配，复制中文备注

    匹配策略（分层回退）：
    1. 按 seq 精确匹配，选页面最近的
    2. 按 seq 匹配，选页面位置最近的（忽略目的地）
    3. 按页面位置最近匹配（货代标签通常紧跟在 FBA 标签后面）
    """
    fba_pages = [p for p in pages if p.get('label_type', 'amazon') == 'amazon']
    forwarder_pages = [p for p in pages if p.get('label_type') == 'forwarder']

    # 按 seq 建立索引
    fba_by_seq = {}  # seq → [fba, ...]
    for p in fba_pages:
        box = p.get('box_current')
        if box:
            fba_by_seq.setdefault(box, []).append(p)

    fba_matched_count = {}

    def _pick_best(candidates, fwd):
        best = None
        best_score = float('inf')
        for fba in candidates:
            fba_id = id(fba)
            count = fba_matched_count.get(fba_id, 0)
            dist = abs(fwd['page_num'] - fba['page_num'])
            score = count * 10000 + dist
            if score < best_score:
                best_score = score
                best = fba
        return best

    matched = []
    unmatched = []
    for fwd in forwarder_pages:
        seq = fwd.get('seq_num')
        if not seq:
            unmatched.append(fwd)
            continue

        # 第1层：按 seq 匹配
        candidates = fba_by_seq.get(seq, [])
        best = _pick_best(candidates, fwd) if candidates else None

        if best:
            fba_matched_count[id(best)] = fba_matched_count.get(id(best), 0) + 1
            fwd['matched_fba'] = best
            fwd['remark'] = remarks.get(best.get('sku'), '')
            # 从匹配的 FBA 标签获取仓库代码
            if not fwd.get('destination'):
                fwd['destination'] = best.get('destination')
            matched.append(fwd)
        else:
            unmatched.append(fwd)

    return matched, unmatched


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


def process_and_output(pages, exceptions, remarks, output_dir, progress_callback=None):
    """完整处理流程：分组、排序、输出"""
    os.makedirs(output_dir, exist_ok=True)

    # 分离 FBA 和货代页面
    fba_pages = [p for p in pages if p.get('label_type', 'amazon') == 'amazon']
    forwarder_pages = [p for p in pages if p.get('label_type') == 'forwarder']

    # 匹配货代标签
    matched_fwd, unmatched_fwd = match_forwarder_to_amazon(pages, remarks)

    total_pages = 0
    exception_count = 0

    # 文档缓存：避免重复打开同一个 PDF
    doc_cache = {}

    def _get_doc(path):
        if path not in doc_cache:
            doc_cache[path] = fitz.open(path)
        return doc_cache[path]

    # 输出 FBA 标签分组 PDF
    grouped = group_and_sort(fba_pages, remarks)
    for sku, group_pages in grouped.items():
        label = remarks.get(sku, sku)
        safe_label = _sanitize_filename(label)
        output_path = os.path.join(output_dir, f"{safe_label}_整理版.pdf")

        out_doc = fitz.open()
        for box_num, page_data in enumerate(group_pages, start=1):
            src_path = page_data['source_file']
            page_num = page_data['page_num']
            try:
                src_doc = _get_doc(src_path)
                if page_num < len(src_doc):
                    out_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                    new_page = out_doc[-1]
                    _add_label_to_page(
                        new_page, label, box_num, page_data.get('destination')
                    )
            except Exception as e:
                print(f"  警告：页面处理出错 - {e}")
            total_pages += 1
            if progress_callback:
                progress_callback(total_pages)

        if len(out_doc) > 0:
            out_doc.save(output_path)
            print(f"  已生成：{output_path}（{len(out_doc)} 页）")
        out_doc.close()

    # 输出货代标签（按匹配到的 FBA 排序顺序）
    if matched_fwd:
        # 按 FBA 标签的排序顺序排列
        fba_order = {}
        for i, p in enumerate(fba_pages):
            key = ((p.get('destination') or '').upper(), p.get('box_current'))
            fba_order[key] = i

        def _fwd_sort_key(fwd):
            dest = (fwd.get('destination') or '').upper()
            seq = fwd.get('seq_num', 999)
            return fba_order.get((dest, seq), 999)

        matched_fwd.sort(key=_fwd_sort_key)

        fwd_doc = fitz.open()
        # 按 FBA SKU 分组编号（和 FBA 输出保持一致）
        sku_fwd_counter = {}
        for fwd in matched_fwd:
            src_path = fwd['source_file']
            page_num = fwd['page_num']
            try:
                src_doc = _get_doc(src_path)
                if page_num < len(src_doc):
                    fwd_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                    new_page = fwd_doc[-1]
                    remark = fwd.get('remark', '')
                    dest = fwd.get('destination', '')
                    # 使用匹配到的 FBA 的排序序号（和 FBA 输出的第N箱一致）
                    fba = fwd.get('matched_fba', {})
                    fba_sku = (fba.get('sku') or '').upper()
                    sku_fwd_counter[fba_sku] = sku_fwd_counter.get(fba_sku, 0) + 1
                    box_num = sku_fwd_counter[fba_sku]
                    label_text = remark or ''
                    label_text += f" 第{box_num}件"
                    if dest:
                        label_text += f" [{dest}]"
                        if label_text:
                            _add_label_to_page(new_page, label_text, None, None)
            except Exception as e:
                print(f"  警告：货代页面处理出错 - {e}")
            total_pages += 1
            if progress_callback:
                progress_callback(total_pages)

        if len(fwd_doc) > 0:
            fwd_path = os.path.join(output_dir, "货代标签_整理版.pdf")
            fwd_doc.save(fwd_path)
            print(f"  已生成货代文件：{fwd_path}（{len(fwd_doc)} 页）")
        fwd_doc.close()

    # 处理异常页面（包括未匹配的货代标签）
    all_exceptions = list(exceptions) + list(unmatched_fwd)
    for fwd in unmatched_fwd:
        fwd['error'] = fwd.get('error', '货代标签未匹配到对应FBA标签')

    if all_exceptions:
        exc_path = os.path.join(output_dir, "异常_需人工核对.pdf")
        exc_doc = fitz.open()

        for page_data in all_exceptions:
            src_path = page_data['source_file']
            page_num = page_data['page_num']
            try:
                src_doc = _get_doc(src_path)
                if page_num < len(src_doc):
                    exc_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                    new_page = exc_doc[-1]
                    _add_exception_label(new_page, page_data)
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

    # 关闭所有缓存的文档
    for doc in doc_cache.values():
        try:
            doc.close()
        except:
            pass

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

    # 放在页面左下角
    x = 5
    y = rect.height - 10

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


def _sanitize_filename(name):
    """清理文件名中的非法字符"""
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        name = name.replace(ch, '_')
    name = name.strip('. ')
    return name or "unnamed"
