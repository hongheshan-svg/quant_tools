"""近分候选有界轮换：保留前半和显著领先者，不修改得分或风险过滤结果。"""
import hashlib
import math


def select_variant(picks, count: int, seed: str | None, run_id: str,
                   ratio: float = .5, score_gap: float = 1.5, require_analysis: bool = False):
    if not math.isfinite(ratio) or not 0 <= ratio <= 1 or not math.isfinite(score_gap) or not 0 <= score_gap <= 1.5:
        raise ValueError('候选轮换比例须为 0..1，分差须为 0..1.5')
    count = min(max(0, count), len(picks))
    baseline = picks[:count]
    metadata = {'applied': False, 'pool_size': 0, 'rotated_slots': 0}
    if not seed or not ratio or count < 2 or len(picks) <= count:
        return baseline, metadata
    cutoff = baseline[-1].score
    protected = {p.code for i, p in enumerate(baseline)
                 if i < count - min(count // 2, max(1, round(count * ratio))) or p.score > cutoff + score_gap}
    slots = count - len(protected)
    pool = [p for p in picks if p.code not in protected and p.score >= cutoff - score_gap
            and p.fits_regime == baseline[-1].fits_regime
            and (not require_analysis or (p.post_analysis or {}).get('status') == 'available')]
    metadata['pool_size'] = len(pool)
    if not slots or len(pool) <= slots:
        return baseline, metadata
    chosen = {p.code for p in sorted(pool, key=lambda p: hashlib.sha256(
        f'{seed}\0{run_id}\0{p.code}'.encode()).digest())[:slots]} | protected
    selected = [p for p in picks if p.code in chosen][:count]
    rotated = len(chosen - {p.code for p in baseline})
    return selected, {**metadata, 'applied': bool(rotated), 'rotated_slots': rotated}
