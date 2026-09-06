"""依赖版本要求的简单合并：只做能安全合并的受限情形，合不了返回 None。

调用方（core.merge_depends）拿到 None 时回退为"保留第一个输入的写法"并警告。
"""

import re

_OP_RE = re.compile(r"^(>=|<=|>|<|=)\s*(.+)$")


def _parse(value):
    """把版本要求解析成 (操作符, 版本号)；只认 = >= > <= < 和裸版本（视为 =）。

    ^ ~ 1.x 这类 x-range、空格复合表达式等一律返回 None（不可合并）。
    """
    value = value.strip()
    if not value or re.search(r"\s", value) or value[0] in "^~" or "*" in value:
        return None
    match = _OP_RE.match(value)
    if match:
        return match.group(1), match.group(2)
    if re.search(r"(^|\.)x($|\.)", value, re.IGNORECASE):
        return None
    return "=", value


def _cmp(a, b):
    """按 '.' 分段比较版本号：数字段按数值、非数字段按字符串、短段按补零对齐。"""

    def parts(v):
        out = []
        for seg in str(v).split("."):
            if seg.isdigit():
                out.append((0, int(seg), ""))
            else:
                out.append((1, 0, seg))
        return out

    pa, pb = parts(a), parts(b)
    width = max(len(pa), len(pb))
    pad = (0, 0, "")
    pa += [pad] * (width - len(pa))
    pb += [pad] * (width - len(pb))
    return (pa > pb) - (pa < pb)


def _satisfied_by(op, bound, ver):
    """精确版本 ver 是否满足约束 (op, bound)。"""
    cmp = _cmp(ver, bound)
    if op == "=":
        return cmp == 0
    if op == ">=":
        return cmp >= 0
    if op == ">":
        return cmp > 0
    if op == "<=":
        return cmp <= 0
    return cmp < 0  # "<"


def _strictest(cons, lower):
    """从同向约束里取最严的一个：下界取版本最大者，上界取版本最小者。

    版本相同时严格比较符（> / <）比闭区间（>= / <=）更严。
    """
    best = cons[0]
    for cand in cons[1:]:
        cmp = _cmp(cand[1], best[1])
        if (cmp > 0) if lower else (cmp < 0):
            best = cand
        elif cmp == 0 and ((lower and cand[0] == ">") or (not lower and cand[0] == "<")):
            best = cand
    return best


def merge_specs(values):
    """合并同一个依赖在多个输入里的版本要求（字符串或单元素数组）。

    返回合并后的字符串；无法安全合并时返回 None。
    """
    specs = []
    for value in values:
        if isinstance(value, list):
            if len(value) != 1:
                return None  # 数组是 OR 语义，不尝试合并
            value = value[0]
        if not isinstance(value, str):
            return None
        if value.strip() == "*":
            continue  # * 不构成约束
        specs.append(value.strip())
    if not specs:
        return "*"
    unique = list(dict.fromkeys(specs))
    if len(unique) == 1:
        return unique[0]
    cons = [_parse(v) for v in unique]
    if any(c is None for c in cons):
        return None
    exacts = [c for c in cons if c[0] == "="]
    lowers = [c for c in cons if c[0] in (">", ">=")]
    uppers = [c for c in cons if c[0] in ("<", "<=")]
    if exacts:
        if len(exacts) > 1:
            return None
        ver = exacts[0][1]
        if all(_satisfied_by(op, bound, ver) for op, bound in lowers + uppers):
            return "=" + ver
        return None
    if lowers and uppers:
        lo, hi = _strictest(lowers, True), _strictest(uppers, False)
        cmp = _cmp(lo[1], hi[1])
        if cmp < 0:
            return f"{lo[0]}{lo[1]} {hi[0]}{hi[1]}"  # 空格连接，Fabric 语义为 AND
        if cmp == 0 and lo[0] == ">=" and hi[0] == "<=":
            return "=" + lo[1]
        return None
    if lowers:
        op, ver = _strictest(lowers, True)
        return op + ver
    if uppers:
        op, ver = _strictest(uppers, False)
        return op + ver
    return None
