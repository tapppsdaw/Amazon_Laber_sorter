"""PDF 解析与字段提取模块"""

import re
import fitz  # PyMuPDF

# EasyOCR reader 延迟初始化（首次加载较慢）
_ocr_reader = None


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        try:
            import torch
            use_gpu = torch.cuda.is_available()
        except Exception:
            use_gpu = False
        _ocr_reader = easyocr.Reader(['en', 'ch_sim'], gpu=use_gpu, verbose=False)
    return _ocr_reader


def extract_page_text(page):
    """提取页面文本，优先使用文本层"""
    text = page.get_text("text").strip()
    return text


def ocr_page(page):
    """用 EasyOCR 识别页面图片中的文字，返回合并后的文本

    优化：
    - 裁剪底部 40%（序号和仓库代码在页面下半部分）
    - DPI 200（平衡速度和识别率）
    - detail=0 跳过 bounding box 计算
    """
    reader = _get_ocr_reader()
    rect = page.rect
    clip = fitz.Rect(rect.x0, rect.y0 + rect.height * 0.6, rect.x1, rect.y1)
    pix = page.get_pixmap(dpi=200, clip=clip)
    img_bytes = pix.tobytes("png")
    results = reader.readtext(img_bytes, detail=0)
    return "\n".join(results)


def detect_label_type(text):
    """根据文字层内容判断标签类型

    - 有 "Single SKU" 或 FNSKU 或完整亚马逊 SKU 格式 → amazon
    - 文字极少或包含 "序号" → forwarder
    """
    if not text or len(text) <= 5:
        return 'forwarder'
    upper = text.upper()
    if 'SINGLE SKU' in upper or 'FNSKU' in upper:
        return 'amazon'
    if re.search(r'\b(X\d{2}[A-Z0-9]+)\b', text):
        return 'amazon'
    if re.search(r'[A-Z0-9]-[A-Z0-9]{4}-[A-Z0-9]{4}', text):
        return 'amazon'
    if '序号' in text:
        return 'forwarder'
    # 文字层很短（< 50字符），很可能是货代标签
    if len(text) < 50:
        return 'forwarder'
    return 'amazon'


def _find_identifier_near_sku(page):
    """利用位置关系：SKU/FNSKU 值总是在 'SKU' 关键词的正下方"""
    blocks = page.get_text("dict")["blocks"]
    spans = []
    for block in blocks:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                t = span["text"].strip()
                if t:
                    spans.append({
                        "text": t,
                        "y": span["bbox"][1],
                        "x": span["bbox"][0],
                        "h": span["size"]
                    })

    sku_spans = []
    for span in spans:
        upper = span["text"].upper()
        if "SKU" in upper:
            sku_spans.append(span)

    if not sku_spans:
        return None

    sku_span = sku_spans[-1]
    base_y = sku_span["y"] + sku_span["h"]

    candidates = []
    for other in spans:
        dy = other["y"] - base_y
        if 0 < dy < 50 and abs(other["x"] - sku_span["x"]) < 100:
            t = other["text"]
            if re.match(r'^X\d', t):
                return t
            if re.match(r'^[A-Z0-9]-[A-Z0-9]{4}-[A-Z0-9]{4}$', t):
                candidates.append((1, dy, t))
            if re.match(r'^[A-Z0-9]{2,4}-[A-Z0-9]{4}-[A-Z0-9]{4}$', t):
                candidates.append((1, dy, t))
            elif re.match(r'^[A-Za-z0-9\-_]{5,}$', t):
                candidates.append((2, dy, t))

    if candidates:
        candidates.sort()
        return candidates[0][2]
    return None


def extract_fnsku(text):
    """从纯文本提取 FNSKU"""
    m = re.search(r'FNSKU[\s:]+(X[A-Z0-9]+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    matches = re.findall(r'\b(X\d{2}[A-Z0-9]+)\b', text)
    for m in matches:
        if len(m) >= 5 and m[4].isalpha():
            continue
        return m
    return None


def extract_destination_fc(text):
    """提取目的地仓库代码"""
    # 先尝试常见模式
    m = re.search(
        r'(?:FBA\s*destination|Ship\s*to|Destination\s*FC|Fulfillment\s*Center)'
        r'[\s:]*([A-Z]{3,4}\d{1,2})',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1).upper()
    # 兜底：找 3-4 字母 + 1-2 数字的模式（如 LAX9, TEB9, ONT8, CLT2）
    # 使用 (?=[^A-Z0-9]) 而非 \b 来处理下划线等字符
    m = re.search(r'(?<![A-Z0-9])([A-Z]{3,4}\d{1,2})(?=[^A-Z0-9]|$)', text)
    if m:
        return m.group(1)
    return None


def extract_box_count(text):
    """提取箱号（格式如 1 of 5、纸箱编号1共14个纸箱、第11箱）"""
    # 优先匹配 "纸箱编号 X，共 Y 个纸箱"（最具体，避免误匹配日期）
    m = re.search(r'纸箱编号\s*(\d{1,3})\D{0,5}共\s*(\d{1,3})\s*个?\s*纸箱', text)
    if m:
        cur, total = int(m.group(1)), int(m.group(2))
        if 1 <= cur <= total <= 999:
            return cur, total
    # "X of Y" 或 "X/Y"（排除日期格式如 04/23/2026）
    m = re.search(r'(\d{1,3})\s*(?:of|/)\s*(\d{1,3})(?!\s*/\s*\d)', text, re.IGNORECASE)
    if m:
        cur, total = int(m.group(1)), int(m.group(2))
        if 1 <= cur <= total <= 999:
            return cur, total
    # FBA 编号末尾是箱号：如 FBAXXXXXXXXXXU000001 → 箱1
    m = re.search(r'FBA\d+[A-Z]U0+(\d{1,3})\b', text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 999:
            m2 = re.search(r'共\s*(\d{1,3})\s*(?:个\s*)?纸箱', text)
            total = int(m2.group(1)) if m2 else None
            return n, total
    # "第X箱" 或 "箱第X"
    m = re.search(r'(?:第|箱第)\s*(\d{1,3})\s*箱', text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 999:
            m2 = re.search(r'共\s*(\d{1,3})\s*(?:个\s*)?纸箱', text)
            total = int(m2.group(1)) if m2 else None
            return n, total
    # "Box/Carton N"
    m = re.search(r'(?:Box|Carton)[\s:#]*(\d{1,3})\b', text, re.IGNORECASE)
    if m:
        return int(m.group(1)), None
    return None, None


def extract_sku_from_text(text):
    """尝试从文本中提取 SKU"""
    # 匹配 "SKU:" 标签
    m = re.search(r'SKU[\s:]+([A-Za-z0-9\-_]+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    # 匹配亚马逊 SKU 格式：X-XXXX-XXXX（如 1J-7QR4-KQ4M）
    m = re.search(r'\b([A-Z0-9]-[A-Z0-9]{4}-[A-Z0-9]{4})\b', text)
    if m:
        return m.group(1)
    # 匹配亚马逊 SKU 格式：XX-XXXX-XXXX（如 AB-CDEF-GHIJ）
    m = re.search(r'\b([A-Z0-9]{2,4}-[A-Z0-9]{4}-[A-Z0-9]{4})\b', text)
    if m:
        return m.group(1)
    return None


def extract_forwarder_info(ocr_text, page_num, doc_path):
    """从 OCR 结果中提取货代标签信息"""
    # 提取序号（如 "序号: 1/14件" → 1, 14）
    seq_num = None
    seq_total = None
    m = re.search(r'序号[：:\s]*(\d+)\s*/\s*(\d+)', ocr_text)
    if m:
        seq_num = int(m.group(1))
        seq_total = int(m.group(2))

    # 提取仓库代码（如 LAX9, TEB9, SBD1）— OCR 可能把字母读成数字
    upper_text = ocr_text.upper()
    # 清理干扰文本：移除括号内容（通常是电话号码）和带点号的数字
    clean_text = re.sub(r'\([^)]*\)', ' ', upper_text)
    clean_text = re.sub(r'\b\d+\.\d+\b', ' ', clean_text)
    # 先用标准模式
    destination = extract_destination_fc(clean_text)
    if not destination:
        # 宽松匹配：任何 4-5 字符组合，尝试修正 OCR 错误
        m = re.search(r'\b([A-Z0-9]{4,5})\b', clean_text)
        if m:
            raw = m.group(1)
            # 对前 3 位：数字→字母（8→B, 0→O, 1→I）
            prefix = raw[:3]
            prefix_fixed = prefix.replace('8', 'B').replace('0', 'O').replace('1', 'I')
            # 对后 1-2 位：保持原样（可能是数字也可能是 OCR 误读的字母）
            suffix = raw[3:]
            # 合并后尝试两种修正：
            # 方案 A：后缀保持原样
            candidate_a = prefix_fixed + suffix
            # 方案 B：后缀也做字母→数字修正（O→0, G→9, Q→0）
            suffix_fixed = suffix.replace('O', '0').replace('G', '9').replace('Q', '0')
            candidate_b = prefix_fixed + suffix_fixed
            for candidate in [candidate_a, candidate_b]:
                # 验证：必须是 3-4 个字母 + 1-2 个数字，且不能以数字开头
                if re.match(r'^[A-Z]{3,4}\d{1,2}$', candidate) and candidate[0].isalpha():
                    destination = candidate
                    break

    # 提取 SKU 标识
    sku = None
    m = re.search(r'SKU[\s:]+([A-Za-z0-9\-_]+)', ocr_text, re.IGNORECASE)
    if m:
        sku = m.group(1)

    info = {
        'page_num': page_num,
        'sku': sku,
        'destination': destination,
        'seq_num': seq_num,
        'seq_total': seq_total,
        'source_file': doc_path,
        'text': ocr_text,
        'has_text': True,
        'label_type': 'forwarder',
    }
    return info


def extract_page_info(page, page_num, doc_path):
    """从单个 FBA 页面提取所有关键信息"""
    text = extract_page_text(page)

    spatial_id = _find_identifier_near_sku(page)
    fnsku = extract_fnsku(text)
    sku = extract_sku_from_text(text)

    identifier = spatial_id or fnsku or sku

    destination = extract_destination_fc(text)
    box_current, box_total = extract_box_count(text)

    info = {
        'page_num': page_num,
        'sku': identifier,
        'destination': destination,
        'box_current': box_current,
        'box_total': box_total,
        'source_file': doc_path,
        'text': text,
        'has_text': len(text) > 10,
        'label_type': 'amazon',
    }

    if identifier:
        return info, True
    return info, False


def process_pdf(file_path, page_callback=None):
    """处理单个 PDF 文件，返回 (成功页面列表, 异常页面列表)"""
    results = []
    exceptions = []

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return [], [{'page_num': 0, 'error': str(e), 'source_file': file_path}]

    total = len(doc)
    for page_num in range(total):
        page = doc[page_num]

        if page_callback:
            page_callback(page_num + 1, total)

        text = extract_page_text(page)

        # 第一步：先尝试文字层直接提取（快速路径）
        if text and len(text) > 5:
            # 先尝试按货代标签提取（文字层可能有 "序号: X/Y"）
            fwd_info = extract_forwarder_info(text, page_num, file_path)
            if fwd_info.get('seq_num') is not None:
                results.append(fwd_info)
                continue
            # 再尝试按 FBA 标签提取
            info, success = extract_page_info(page, page_num, file_path)
            if success:
                results.append(info)
                continue

        # 第二步：文字层提取失败，用 OCR 扫描（慢速路径）
        try:
            ocr_text = ocr_page(page)
            # 先尝试按货代标签提取序号
            info = extract_forwarder_info(ocr_text, page_num, file_path)
            if info.get('seq_num') is not None:
                results.append(info)
            else:
                # 货代提取也失败，尝试按 FBA 标签提取
                fba_info, fba_success = extract_page_info(page, page_num, file_path)
                if fba_success:
                    results.append(fba_info)
                else:
                    info['error'] = '序号提取失败'
                    exceptions.append(info)
        except Exception as e:
            exceptions.append({
                'page_num': page_num,
                'error': f'OCR失败: {e}',
                'source_file': file_path,
            })

    doc.close()
    return results, exceptions
