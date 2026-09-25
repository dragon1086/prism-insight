"""Actual provider -> cache -> real morning/afternoon selection -> JSON."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = r'''
import json,socket,sys
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd

root,mode,case,shape,destination,cache_root=sys.argv[1:]
sys.path[:0]=[str(Path(root)/"prism-us"),root]
def blocked(*a,**kw):raise AssertionError("Live network/order/channel forbidden")
with patch("dotenv.load_dotenv",return_value=False),patch.object(socket.socket,"connect",blocked):
 import us_trigger_batch as batch
 from cores import us_surge_detector as provider
 from prism_core.us_daily_cache import DailyBarCache
 from observability import oneil_watchlist
 tickers=["AAA","BBB","CCC"]
 frames={}
 for i,t in enumerate(tickers):
  frames[t]=pd.DataFrame({"Open":[98.,100.,101.+i],"High":[102.,103.,106.+i],
                         "Low":[97.,99.,100.+i],"Close":[100.,101.,105.+i],
                         "Volume":[1e6,1e6,4e6+i*1e6]},
                        index=pd.to_datetime(["2026-09-10","2026-09-11","2026-09-14"]))
  DailyBarCache(Path(cache_root),now=datetime(2026,9,11,21,tzinfo=timezone.utc)).save_history(t,frames[t])
 calls=[]
 def download(symbols,**kw):
  calls.append(symbols)
  if isinstance(symbols,list):
   assert kw['repair'] is False and kw['auto_adjust'] is True
   raw=pd.concat({t:frames[t].copy() for t in symbols},axis=1)
   if case=='cached':raw.loc['2026-09-11',:]=np.nan
   return raw.swaplevel(axis=1) if shape=='price_first' else raw
  c=np.linspace(70.,104.+tickers.index(symbols),260)
  return pd.DataFrame({'Open':c-1,'High':c+2,'Low':c-2,'Close':c,'Volume':np.full(260,2e6)},
                      index=pd.bdate_range(end='2026-09-14',periods=260))
 with patch.object(batch,'get_major_tickers',return_value=tickers), \
      patch.object(provider.yf,'download',side_effect=download), \
      patch.object(provider.yf,'Ticker',side_effect=lambda t:SimpleNamespace(
          info={'shortName':t,'sector':'Technology'},fast_info={'marketCap':1e11},history=blocked)), \
      patch.object(oneil_watchlist,'enabled',return_value=False):
  result=batch.run_batch(mode,'ERROR',destination,override_date='20260914')
  assert result, 'Recovery must reach nonempty real selection'
  assert sum(isinstance(c,list) for c in calls)==1
  payload=json.loads(Path(destination).read_text())
  previous=payload['metadata']['snapshot_coverage']['previous_provider']
  assert previous['status']=='COMPLETE'
  assert set(previous['sources'].values())==({'cache'} if case=='cached' else {'provider'})
  assert previous['retry_attempts']==0
'''


@pytest.mark.parametrize('mode',['morning','afternoon'])
@pytest.mark.parametrize('shape',['price_first','ticker_first'])
def test_cached_pair_preserves_real_batch_candidates(tmp_path,mode,shape):
    results=[]
    for case in ['fresh','cached']:
        output=tmp_path/f'{case}.json'
        env=dict(os.environ,US_DAILY_CACHE_ENABLED='true',
                 US_SCREENING_UNIVERSE='major_indices',
                 PRISM_US_DAILY_CACHE_DIR=str(tmp_path/case),
                 PRISM_DISABLE_SIGNAL_PUBLISH='1',REPORT_MARKET_CONTEXT_ENABLED='false',
                 PRISM_OBSERVABILITY_SPOOL=str(tmp_path/'events.jsonl'),
                 REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED='false')
        r=subprocess.run([sys.executable,'-c',RUN,str(ROOT),mode,case,shape,str(output),str(tmp_path/case)],
                         cwd=ROOT,env=env,capture_output=True,text=True,timeout=45)
        assert r.returncode==0,r.stdout+r.stderr
        payload=json.loads(output.read_text())
        payload['metadata'].pop('run_time')
        payload['metadata'].pop('snapshot_coverage')
        results.append(payload)
    assert results[0]==results[1]
