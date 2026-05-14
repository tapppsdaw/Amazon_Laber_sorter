"""PDF 解析与字段提取模块"""

import re
import fitz  # PyMuPDF


def extract_page_text(page):
    """提取页面文本，优先使用文本层，必要时使用 OCR"""
    text = page.get_text("text").strip()
    if len(text) > 10:
        return text
    # 文本层为空时尝试 OCR（需要安装 Tesseract）
    try:
        import pytesseract
        from PIL import Image
        import io

        pix = page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, lang="eng")
        return text.strip()
    except Exception:
        return text


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

    # 收集所有 SKU 标签位置（取最后一个，通常是 "Single SKU" 下方的）
    sku_spans = []
    for span in spans:
        upper = span["text"].upper()
        if "SKU" in upper:
            sku_spans.append(span)

    if not sku_spans:
        return None

    # 取最后出现的 SKU 标签（通常是产品信息区的）
    sku_span = sku_spans[-1]
    base_y = sku_span["y"] + sku_span["h"]

    # 收集下方候选值，按优先级返回
    candidates = []
    for other in spans:
        dy = other["y"] - base_y
        if 0 < dy < 50 and abs(other["x"] - sku_span["x"]) < 100:
            t = other["text"]
            if re.match(r'^X\d', t):
                return t  # FNSKU 优先级最高，直接返回
            if re.match(r'^[A-Z0-9]{2,4}-[A-Z0-9]{4}-[A-Z0-9]{4}$', t):
                candidates.append((1, dy, t))  # 亚马逊 SKU 格式
            elif re.match(r'^[A-Za-z0-9\-_]{5,}$', t):
                candidates.append((2, dy, t))  # 其他编号

    if candidates:
        candidates.sort()
        return candidates[0][2]
    return None


def extract_fnsku(text):
    """从纯文本提取 FNSKU（兜底方案）"""
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
    m = re.search(
        r'(?:FBA\s*destination|Ship\s*to|Destination\s*FC|Fulfillment\s*Center)'
        r'[\s:]*([A-Z]{3,4}\d{1,2})',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1).upper()
    m = re.search(r'\b([A-Z]{3,4}\d{1,2})\b', text)
    if m:
        return m.group(1)
    return None


def extract_box_count(text):
    """提取箱号（格式如 1 of 5、FBA编号末尾、第11箱）"""
    # "X of Y" 或 "X/Y"（X 和 Y 均不超过 999，且 X <= Y）
    m = re.search(r'(\d{1,3})\s*(?:of|/)\s*(\d{1,3})', text, re.IGNORECASE)
    if m:
        cur, total = int(m.group(1)), int(m.group(2))
        if 1 <= cur <= total <= 999:
            return cur, total
    # FBA 编号末尾是箱号：如 FBAXXXXXXXXXXU000001 → 箱1
    m = re.search(r'FBA\d+[A-Z]U0+(\d{1,3})\b', text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 999:
            # 尝试从标题找总箱数
            m2 = re.search(r'共\s*(\d{1,3})\s*箱', text)
            total = int(m2.group(1)) if m2 else None
            return n, total
    # "第X箱" 或 "箱第X"（不匹配 "箱出X" 标题）
    m = re.search(r'(?:第|箱第)\s*(\d{1,3})\s*箱', text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 999:
            m2 = re.search(r'共\s*(\d{1,3})\s*箱', text)
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
    # 匹配亚马逊 SKU 格式：XX-XXXX-XXXX（如 AB-CDEF-GHIJ）
    m = re.search(r'\b([A-Z0-9]{2,4}-[A-Z0-9]{4}-[A-Z0-9]{4})\b', text)
    if m:
        return m.group(1)
    return None


def extract_page_info(page, page_num, doc_path):
    """从单个页面提取所有关键信息"""
    text = extract_page_text(page)

    # 优先用位置关系提取（SKU 关键词下方的值）
    spatial_id = _find_identifier_near_sku(page)
    # 兜底用正则提取
    fnsku = extract_fnsku(text)
    sku = extract_sku_from_text(text)

    # 优先级：位置提取 > FNSKU正则 > SKU正则
    identifier = spatial_id or fnsku or sku

    destination = extract_destination_fc(text)
    box_current, box_total = extract_page_box_count(text)

    info = {
        'page_num': page_num,
        'sku': identifier,
        'destination': destination,
        'box_current': box_current,
        'box_total': box_total,
        'source_file': doc_path,
        'text': text,
        'has_text': len(text) > 10,
    }

    # 有 SKU/FNSKU 标识即算成功（箱号和仓库可选）
    if identifier:
        return info, True
    return info, False


def extract_page_box_count(text):
    """提取箱号，返回 (当前箱号, 总箱数)"""
    return extract_box_count(text)


def process_pdf(file_path):
    """处理单个 PDF 文件，返回 (成功页面列表, 异常页面列表)"""
    results = []
    exceptions = []

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return [], [{'page_num': 0, 'error': str(e), 'source_file': file_path}]

    for page_num in range(len(doc)):
        page = doc[page_num]
        info, success = extract_page_info(page, page_num, file_path)

        if success:
            results.append(info)
        else:
            exceptions.append(info)

    doc.close()
    return results, exceptions
