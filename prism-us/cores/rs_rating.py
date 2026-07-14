"""
prism-us/cores/rs_rating.py — 공용 O'Neil RS Rating 모듈 re-export.

prism-us/cores/ 패키지가 루트 cores/ 패키지를 섀도잉하기 때문에
importlib 로 루트의 실제 구현체를 직접 로드해 공용 함수를 재노출한다.
단일 소스: /cores/rs_rating.py (이 파일에는 구현 없음).
"""
from __future__ import annotations

import importlib.util
import pathlib

_impl_path = pathlib.Path(__file__).parent.parent.parent / "cores" / "rs_rating.py"
_spec = importlib.util.spec_from_file_location("_rs_rating_root", _impl_path)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

oneil_weighted_return = _mod.oneil_weighted_return
percentile_ratings = _mod.percentile_ratings

__all__ = ["oneil_weighted_return", "percentile_ratings"]
