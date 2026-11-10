"""基于标签及动态表头坐标的保守解析；不含特定发票值或固定页面坐标。"""
from dataclasses import dataclass
from decimal import Decimal
import re
import statistics

from .models import AppError, Invoice, Item, number, parse_date


@dataclass(frozen=True)
class Token:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("：", ":").replace("（", "(").replace("）", ")")


def visual_rows(tokens: list[Token]) -> list[list[Token]]:
    if not tokens:
        return []
    tolerance = max(2, statistics.median(t.y1 - t.y0 for t in tokens) * .48)
    rows: list[list[Token]] = []
    for t in sorted(tokens, key=lambda t: (t.cy, t.x0)):
        if rows and abs(statistics.median(w.cy for w in rows[-1]) - t.cy) <= tolerance:
            rows[-1].append(t)
        else:
            rows.append([t])
    return [sorted(row, key=lambda t: t.x0) for row in rows]


def extract_candidates(tokens: list[Token]) -> list[str]:
    candidates = []
    for row in visual_rows(tokens):
        text = compact("".join(t.text for t in row))
        for m in re.finditer(r"发票(?:号码|号)[:]?([0-9]{8,20})(?!\d)", text):
            if len(m[1]) in (8, 20):
                candidates.append(m[1])
    # 有标签时绝不使用税号等其他长数字；无标签仅接受独立20位数电号码。
    if not candidates:
        candidates = [compact(t.text) for t in tokens if re.fullmatch(r"\d{20}", compact(t.text))]
    return list(dict.fromkeys(candidates))


ALIASES = {
    "name": ("项目名称", "货物或应税劳务、服务名称", "货物或应税劳务服务名称", "商品名称", "服务名称"),
    "specification": ("规格型号",), "unit": ("单位",), "quantity": ("数量",),
    "unit_price": ("单价",), "net_amount": ("金额", "未税金额"),
    "tax_rate": ("税率/征收率", "税率", "征收率"), "tax": ("税额",),
}


def headers_in(row: list[Token]) -> dict[str, Token]:
    headers = {}
    used = set()
    # 最长组合优先，处理文字层中的“单 位”“数 量”和 OCR 分开的标题。
    for count in (4, 3, 2, 1):
        for i in range(len(row) - count + 1):
            if any(j in used for j in range(i, i + count)):
                continue
            segment = row[i:i + count]
            text = compact("".join(t.text for t in segment))
            for field, aliases in ALIASES.items():
                if text in aliases and field not in headers:
                    headers[field] = Token(segment[0].x0, min(t.y0 for t in segment), segment[-1].x1, max(t.y1 for t in segment), text)
                    used.update(range(i, i + count))
                    break
    return headers


def parse_items(tokens: list[Token], notices: list[str]) -> list[Item]:
    rows = visual_rows(tokens)
    header_index, columns = None, {}
    for i, row in enumerate(rows):
        found = headers_in(row)
        if "name" in found and len(found) >= 4:
            header_index, columns = i, found
            break
    if header_index is None:
        return []
    sorted_columns = sorted(columns, key=lambda f: columns[f].cx)
    items = []
    for row in rows[header_index + 1:]:
        text = compact("".join(t.text for t in row))
        if any(marker in text for marker in ("合计", "价税", "开票人", "销售方", "购买方", "备注")):
            break
        cells = {key: [] for key in columns}
        specification_tokens = []
        for token in row:
            key = min(sorted_columns, key=lambda key: abs(token.cx - columns[key].cx))
            cells[key].append(token.text)
            if key == "specification":
                specification_tokens.append(token)
        cells = {k: "".join(v).strip() for k, v in cells.items()}
        if specification_tokens:
            # 同行型号被检测为多个框时，保留图上明显的间隔；换行续写仍在下方合并。
            parts = []
            previous = None
            for token in specification_tokens:
                if previous is not None and token.x0 - previous.x1 > min(token.y1 - token.y0, previous.y1 - previous.y0) * .3:
                    parts.append(" ")
                parts.append(token.text)
                previous = token
            cells["specification"] = "".join(parts).strip()
        name = cells.get("name", "")
        if not name:
            if items and cells.get("specification") and not any(cells.get(k) for k in ("quantity", "net_amount", "tax")):
                items[-1].specification += cells["specification"]
            continue
        if items and not any(cells.get(k) for k in ("quantity", "unit_price", "net_amount", "tax_rate", "tax")):
            items[-1].name += name
            items[-1].specification += cells.get("specification", "")
            continue
        values = {}
        for key in ("quantity", "unit_price", "net_amount", "tax"):
            # 同一框内的两个数字可能来自相邻列，不能删掉内部空格后拼成一个数。
            # 跨列文本由扫描层根据表头裁切重识别；未能分开时留空并提示核对。
            value = cells.get(key, "").strip()
            # “15. 64”仍是单个小数；只清理小数点/分组逗号两侧空白，
            # 保留“100 0.106...”两个数字之间的空白，避免误拼接。
            value = re.sub(r"\s*([.,])\s*", r"\1", value).replace("￥", "").replace("¥", "").strip()
            # “免税/不征税”是发票明确给出的税额语义；没有该标记时不填零。
            if key == "tax" and value in ("***", "*") and cells.get("tax_rate") in ("免税", "不征税"):
                values[key] = Decimal(0)
                continue
            try:
                values[key] = number(value, key)
            except AppError:
                values[key] = None
                notices.append(f"第 {len(items) + 1} 行部分数值未能解析，请手动核对")
        items.append(Item(name=name, specification=cells.get("specification", ""), unit=cells.get("unit", ""),
                          tax_rate=cells.get("tax_rate", ""), **values))
    return items


def parse_invoice(tokens: list[Token]) -> Invoice:
    inv = Invoice()
    inv.candidates = extract_candidates(tokens)
    if len(inv.candidates) == 1:
        inv.number = inv.candidates[0]
    elif len(inv.candidates) > 1:
        inv.notices.append("识别到多个候选发票号码，请在候选框中选择或手动确认")
    texts = [compact("".join(t.text for t in row)) for row in visual_rows(tokens)]
    text = "\n".join(texts)
    code = re.search(r"发票代码[:]?(\d{10,12})(?!\d)", text)
    if code:
        inv.code = code[1]
    check = re.search(r"[校效]验码(?:后六位)?[:]?([\d ]{6,})", text)
    if check:
        inv.check_last6 = check[1].replace(" ", "")[-6:]
    dates = re.findall(r"开票日期[:]?((?:19|20)\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2}日?)", text)
    if not dates:
        dates = re.findall(r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日", text)
    if dates:
        try:
            inv.issued_on = parse_date(dates[0])
        except AppError:
            inv.notices.append("开票日期无法解析，请手动填写")
    for row in texts:
        if "小写" in row or "价税合计" in row:
            small = row.split("小写")[-1] if "小写" in row else row
            amounts = re.findall(r"[¥￥]\s*([+-]?\d[\d,]*(?:\.\d+)?)", small)
            if not amounts:
                amounts = re.findall(r"(?:价税合计|小写)[):]*([+-]?\d+\.\d{2})(?!\d)", small)
            if amounts:
                inv.total = number(amounts[-1])
        elif row.startswith("合计"):
            amounts = re.findall(r"[¥￥]\s*([+-]?\d[\d,]*(?:\.\d+)?)", row)
            if len(amounts) == 2:
                inv.net_total, inv.tax_total = (number(a) for a in amounts)
    inv.items = parse_items(tokens, inv.notices)
    return inv
