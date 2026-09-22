"""已完成任务的词框质量报告（只读、确定性分析）。

输入 ``analyze_layout`` 已产出的结果（行分组 + 原始坐标系词框），在**去倾斜
坐标系**下重新测量每个词框的几何量，按行做四类异常检测：

1. ``low_confidence`` 置信度过低（固定绝对阈值）；
2. ``size_outlier`` 字高相对本行中位数 / MAD 稳健离群；
3. ``overlap`` 同行词框水平重叠（垂直投影需大体共线）；
4. ``gap_anomaly`` 行内相邻词间距相对本行间距中位数显著偏大。

本模块**只读取任务结果、绝不写库、也不修改任务结果**；同样的输入永远得到
同样的输出（无随机量、无时间相关逻辑）。
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from .layout import _polygon_metrics, _rotate

# 规则版本号：阈值调整时递增，便于调用方区分报告口径
RULES_VERSION = "1.0"

# ---- 固定阈值（确定性的唯一来源；调参只需改这里） ----
LOW_CONF_MAJOR = 0.50   # 置信度 < 0.50 严重
LOW_CONF_MINOR = 0.75   # 0.50 <= 置信度 < 0.75 提示

SIZE_Z_MAJOR = 3.5      # MAD 稳健 z 分数阈值
SIZE_Z_MINOR = 2.5
SIZE_REL_MAJOR = 0.50   # MAD 退化为 0 时的相对偏差阈值
SIZE_REL_MINOR = 0.30
MIN_WORDS_FOR_SIZE = 3  # 行内至少 3 个词才做字号离群判断

OVERLAP_H_MAJOR = 0.50  # 水平重叠占较窄词宽度比例
OVERLAP_H_MINOR = 0.25
OVERLAP_V_MIN = 0.50    # 垂直方向至少重叠较矮词的一半，避免误报上下行

GAP_Z_MAJOR = 3.5
GAP_Z_MINOR = 2.5
GAP_RATIO_MAJOR = 4.0   # MAD 退化为 0 时，间距 / 中位间距 的倍数阈值
GAP_RATIO_MINOR = 2.5
MIN_WORDS_FOR_GAP = 4   # 至少 3 个间距样本，中位数/MAD 才有意义

KINDS = ("low_confidence", "size_outlier", "overlap", "gap_anomaly")
SEVERITIES = ("major", "minor")


# ---------------------------------------------------------------------------
# 几何重建
# ---------------------------------------------------------------------------

def _xy(p: Any) -> Tuple[float, float]:
    if isinstance(p, dict):
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _place_word(word: Dict[str, Any], skew: float, pos: int
                ) -> Optional[Dict[str, Any]]:
    """把结果中的词框重建到去倾斜坐标系，返回测量量；退化框返回 None。"""
    quad = word.get("quad") or []
    m = _polygon_metrics(quad)
    if m is None:
        return None
    theta = math.radians(-skew)
    pts = [_rotate(x, y, theta) for x, y in (_xy(p) for p in list(quad)[:4])]

    # 与 layout.analyze_layout 完全相同的字高 / 字宽投影口径，保证
    # 质量报告与排序算法对“字号”的理解一致。
    w, h = m["width"], m["height"]
    a = math.radians(m["angle"] - skew)
    width = abs(w * math.cos(a)) + abs(h * math.sin(a))
    height = abs(w * math.sin(a)) + abs(h * math.cos(a))

    return {
        "pos": pos,
        "id": word.get("id"),
        "text": word.get("text") or "",
        "confidence": word.get("confidence"),
        "left": min(p[0] for p in pts),
        "right": max(p[0] for p in pts),
        "top": min(p[1] for p in pts),
        "bottom": max(p[1] for p in pts),
        "width": max(width, 1e-6),
        "height": max(height, 1e-6),
    }


def _median_mad(values: List[float]) -> Tuple[float, float]:
    """中位数与中位数绝对偏差（MAD，未乘 1.4826 系数）。"""
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    return float(med), float(mad)


def _clip(text: str, limit: int = 20) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _id_sort_key(word_id: Any) -> Tuple[int, float, str]:
    """原始 id 排序键：数值型按数值大小，None / 非数值稳定排在后面。"""
    if isinstance(word_id, bool):
        return (1, 0.0, str(word_id))
    if isinstance(word_id, (int, float)):
        return (0, float(word_id), "")
    return (1, 0.0, "" if word_id is None else str(word_id))


# ---------------------------------------------------------------------------
# 四类检查
# ---------------------------------------------------------------------------

def _add(issues: List[Dict[str, Any]], line_id: Any,
         word: Dict[str, Any], kind: str, severity: str,
         reason: str, suggestion: str) -> None:
    issues.append({
        "word_id": word["id"],
        "line_id": line_id,
        "kind": kind,
        "severity": severity,
        "reason": reason,
        "suggestion": suggestion,
    })


def _check_confidence(line_id: Any, words: List[Dict[str, Any]],
                      issues: List[Dict[str, Any]]) -> None:
    for w in words:
        c = w["confidence"]
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            continue  # 未提供置信度时不臆断
        if c < LOW_CONF_MAJOR:
            _add(issues, line_id, w, "low_confidence", "major",
                 f"置信度 {c:.2f} 低于阈值 {LOW_CONF_MAJOR:.2f}",
                 "OCR 识别很可能不可靠，建议对照原图人工复核该词")
        elif c < LOW_CONF_MINOR:
            _add(issues, line_id, w, "low_confidence", "minor",
                 f"置信度 {c:.2f} 低于阈值 {LOW_CONF_MINOR:.2f}",
                 "置信度偏低，建议抽查或结合上下文校对该词")


def _check_sizes(line_id: Any, words: List[Dict[str, Any]],
                 issues: List[Dict[str, Any]]) -> None:
    if len(words) < MIN_WORDS_FOR_SIZE:
        return
    med, mad = _median_mad([w["height"] for w in words])
    if med <= 1e-9:
        return
    scale = 1.4826 * mad
    for w in words:
        dev = abs(w["height"] - med)
        if scale > 1e-9 * med:
            z = dev / scale
            if z >= SIZE_Z_MAJOR:
                severity, metric = "major", f"稳健 z={z:.1f}"
            elif z >= SIZE_Z_MINOR:
                severity, metric = "minor", f"稳健 z={z:.1f}"
            else:
                continue
        else:
            # 行内字号完全（或几乎）一致：MAD 退化为 0，改用相对偏差
            rel = dev / med
            if rel >= SIZE_REL_MAJOR:
                severity, metric = "major", f"相对偏差 {rel:.0%}"
            elif rel >= SIZE_REL_MINOR:
                severity, metric = "minor", f"相对偏差 {rel:.0%}"
            else:
                continue
        _add(issues, line_id, w, "size_outlier", severity,
             f"词框字高 {w['height']:.1f} 偏离行内中位数 {med:.1f}（{metric}）",
             "字号与同行其他词不一致，检查是否误检框、上下标，"
             "或标题/标注混入正文行")


def _check_overlaps(line_id: Any, words: List[Dict[str, Any]],
                    issues: List[Dict[str, Any]]) -> None:
    n = len(words)
    for i in range(n):
        wi = words[i]
        for j in range(i + 1, n):
            wj = words[j]
            hint = max(0.0, min(wi["right"], wj["right"])
                       - max(wi["left"], wj["left"]))
            vint = max(0.0, min(wi["bottom"], wj["bottom"])
                       - max(wi["top"], wj["top"]))
            if hint <= 0.0 or vint <= 0.0:
                continue
            h_ratio = hint / min(wi["width"], wj["width"])
            v_ratio = vint / min(wi["height"], wj["height"])
            if v_ratio < OVERLAP_V_MIN or h_ratio < OVERLAP_H_MINOR:
                continue
            severity = "major" if h_ratio >= OVERLAP_H_MAJOR else "minor"
            for w, other in ((wi, wj), (wj, wi)):
                _add(issues, line_id, w, "overlap", severity,
                     f"与同行词 id={other['id']}（“{_clip(other['text'])}”）"
                     f"水平重叠 {h_ratio:.0%}",
                     "检查词框坐标或 OCR 切分，重叠词框可能造成重复文本"
                     "或阅读顺序错误")


def _check_gaps(line_id: Any, words: List[Dict[str, Any]],
                issues: List[Dict[str, Any]]) -> None:
    if len(words) < MIN_WORDS_FOR_GAP:
        return
    gaps = [
        (i, words[i + 1]["left"] - words[i]["right"])
        for i in range(len(words) - 1)
    ]
    values = [g for _, g in gaps]
    med, mad = _median_mad(values)
    med_h = statistics.median(w["height"] for w in words)
    scale = 1.4826 * mad

    for i, gap in gaps:
        if gap <= med:
            continue  # 只检查异常偏大的间隙；重叠由 overlap 规则负责
        if scale > 1e-9 * max(med, 1.0):
            z = (gap - med) / scale
            if z >= GAP_Z_MAJOR:
                severity = "major"
            elif z >= GAP_Z_MINOR:
                severity = "minor"
            else:
                continue
        else:
            # 行内等距排版：要求绝对间隙至少一个字高，再看相对倍数，
            # 避免在紧贴排版里把微小波动误报为异常
            if med_h <= 1e-9 or gap < med_h:
                continue
            ratio = gap / med if med > 1e-9 else math.inf
            if ratio >= GAP_RATIO_MAJOR:
                severity = "major"
            elif ratio >= GAP_RATIO_MINOR:
                severity = "minor"
            else:
                continue
        nxt = words[i + 1]
        _add(issues, line_id, words[i], "gap_anomaly", severity,
             f"与后一个词 id={nxt['id']}（“{_clip(nxt['text'])}”）的间距 "
             f"{gap:.1f} 显著大于行内中位间距 {med:.1f}",
             "行内间隙异常偏大，检查是否漏检词框、错误合并，"
             "或属于两端对齐/排版造成的断词")


# ---------------------------------------------------------------------------
# 报告组装
# ---------------------------------------------------------------------------

def build_quality_report(task_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """根据已完成任务的结果构造质量报告（纯函数，不读写数据库）。"""
    skew = float(result.get("deskew_angle") or 0.0)

    placed_lines: List[Tuple[Any, List[Dict[str, Any]]]] = []
    scanned_words = 0
    for line in result.get("lines") or []:
        placed: List[Dict[str, Any]] = []
        for pos, w in enumerate(line.get("words") or []):
            pw = _place_word(w, skew, pos)
            if pw is not None:
                placed.append(pw)
        placed_lines.append((line.get("id"), placed))
        scanned_words += len(placed)

    issues: List[Dict[str, Any]] = []
    affected = set()
    for line_id, words in placed_lines:
        before = len(issues)
        _check_confidence(line_id, words, issues)
        _check_sizes(line_id, words, issues)
        _check_overlaps(line_id, words, issues)
        _check_gaps(line_id, words, issues)
        for issue in issues[before:]:
            affected.add((line_id, issue["word_id"]))

    # 同一词可能命中多条规则；按原始 id、规则种类、行号确定性排序
    issues.sort(key=lambda it: (
        _id_sort_key(it["word_id"]), it["kind"], _id_sort_key(it["line_id"]),
    ))

    by_kind = {k: 0 for k in KINDS}
    by_severity = {k: 0 for k in SEVERITIES}
    for issue in issues:
        by_kind[issue["kind"]] += 1
        by_severity[issue["severity"]] += 1

    return {
        "task_id": task_id,
        "rules_version": RULES_VERSION,
        "issues": issues,
        "summary": {
            "total_issues": len(issues),
            "words_with_issues": len(affected),
            "scanned_words": scanned_words,
            "scanned_lines": len(placed_lines),
            "unclassified_words": len(result.get("unclassified") or []),
            "by_kind": by_kind,
            "by_severity": by_severity,
        },
    }
