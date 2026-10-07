"""近分轮换保留质量、顺序、匿名和同等分析边界。"""
from types import SimpleNamespace

import pytest
from src.strategy.selection_variant import select_variant


def picks():
    return [SimpleNamespace(code=str(i), score=score, fits_regime=True, post_analysis={})
            for i, score in enumerate([99, 95, 80, 79.8, 79.7, 79.6, 78.4, 70])]


def test_variant_changes_only_near_score_membership_with_preserved_order():
    rows = picks()
    outputs = set()
    for run in range(30):
        result, metadata = select_variant(rows, 4, 'opaque', str(run))
        codes = [r.code for r in result]
        assert codes[:2] == ['0', '1'] and len(codes) == 4
        assert codes == sorted(codes, key=int) and '7' not in codes
        assert all(r.score >= 78.3 for r in result)
        assert 'opaque' not in str(metadata)
        assert select_variant(rows, 4, 'opaque', str(run))[0] == result
        outputs.add(tuple(codes))
    assert len(outputs) > 1 and [r.score for r in rows] == [99, 95, 80, 79.8, 79.7, 79.6, 78.4, 70]


def test_variant_disabled_and_unevaluated_tail_preserve_baseline():
    rows = picks()
    for seed, ratio, analysis in [(None, .5, False), ('s', 0, False), ('s', .5, True)]:
        assert select_variant(rows, 4, seed, 'run', ratio, require_analysis=analysis)[0] == rows[:4]


@pytest.mark.parametrize('ratio,gap', [(float('nan'), 1), (2, 1), (.5, 2)])
def test_variant_rejects_unbounded_settings(ratio, gap):
    with pytest.raises(ValueError):
        select_variant(picks(), 4, 's', 'run', ratio, gap)
