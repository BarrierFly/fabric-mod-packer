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


def _split_version(v):
    """拆成 (release 段列表, pre-release 标识元组)；build metadata（+ 之后）不参与比较。"""
    s = str(v).split("+", 1)[0]
    if "-" in s:
        release, pre = s.split("-", 1)
        return release.split("."), tuple(pre.split("."))
    return s.split("."), ()


def _cmp(a, b):
    """版本号比较，尽量贴近 semver 语义：

    - release 部分：数字段按数值、数字段小于非数字段（沿用旧实现的粗略排序）、
      短者补零对齐（1.0 == 1.0.0）；
    - pre-release 部分（- 之后）：数字标识按数值、数字标识小于字母标识、按 ASCII、
      前缀相同时短者更小；无 pre-release 的版本更大（1.0.0-beta < 1.0.0）。
    """
    release_a, pre_a = _split_version(a)
    release_b, pre_b = _split_version(b)
    for x, y in zip(release_a, release_b):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            ix, iy = int(x), int(y)
            return (ix > iy) - (ix < iy)
        sx = (1, 0, x) if not x.isdigit() else (0, int(x), "")
        sy = (1, 0, y) if not y.isdigit() else (0, int(y), "")
        return (sx > sy) - (sx < sy)
    rest_a, rest_b = release_a[len(release_b):], release_b[len(release_a):]
    if rest_a or rest_b:
        rest = rest_a or rest_b
        if not all(seg.isdigit() and int(seg) == 0 for seg in rest):
            # 全为零的尾巴视为补零（1.0 == 1.0.0），否则长的一方更大
            return 1 if rest_a else -1
    if pre_a == pre_b:
        return 0
    if not pre_a:
        return 1
    if not pre_b:
        return -1
    for x, y in zip(pre_a, pre_b):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return (int(x) > int(y)) - (int(x) < int(y))
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1  # semver：数字标识小于字母标识
        return (x > y) - (x < y)
    return (len(pre_a) > len(pre_b)) - (len(pre_a) < len(pre_b))


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
