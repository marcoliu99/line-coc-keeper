"""Isolated exact-key mirror scaling probe; never loads the project .env."""
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

root=Path(tempfile.mkdtemp(prefix='coc-s3-benchmark-'))
os.environ.update(DB_PATH=str(root/'state.db'),DATA_DIR=str(root/'groups'),BACKUP_DIR=str(root/'backups'),PYTHON_DOTENV_DISABLED='1')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db
from app.models import Character, GroupState
from app.repositories.group_state import _save_state_unlocked, save_state

results=[]
state=GroupState('target');state.characters['u']=Character('Ada','u',character_id='char-a');save_state(state)
previous_count=0
for count in (1,10,100,1000):
    with db.transaction() as conn:
        for idx in range(previous_count,count-1):
            db.set_json_tx(conn,'characters',f'other-{idx}:u',{'conversation_id':f'other-{idx}'})
    previous_count=count-1
    timings=[];counts=[]
    for i in range(20):
        state.log=[{'role':'user','content':'fixed-size log update'}]
        start=time.perf_counter()
        with db.transaction() as conn:
            conn.execute('BEGIN IMMEDIATE')
            receipt=_save_state_unlocked(state,conn=conn)
        receipt.apply(state)
        timings.append((time.perf_counter()-start)*1000)
        counts.append([receipt.mirror_reads,receipt.mirror_writes,receipt.mirror_deletes])
    results.append({'groups':count,'median_ms':round(statistics.median(timings),3),'mirror_counts':sorted(set(map(tuple,counts)))})
print(json.dumps(results,indent=2))
