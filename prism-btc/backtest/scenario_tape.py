"""Immutable semantic decision tape; never substitutes WAIT for missing evidence."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def semantic(value):
    if isinstance(value,dict):
        return {k:semantic(v) for k,v in value.items() if k!="input_id"}
    if isinstance(value,list):
        return [semantic(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


class TapeMismatch(ValueError):
    pass


class DecisionTape:
    def __init__(self,path,mode,contract):
        if mode not in {"record","frozen"}:
            raise ValueError("invalid_tape_mode")
        self.path,self.mode=Path(path),mode
        self.contract=copy.deepcopy(contract)
        self.rows=[];self.used=set();self.stream=None
        if mode=="record":
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self.stream=self.path.open("x",encoding="utf-8")
            self._write({"kind":"header","schema":1,"contract":contract,"contract_hash":digest(contract)})
        else:
            lines=self.path.read_text(encoding="utf-8").splitlines()
            if not lines:
                raise TapeMismatch("empty_tape")
            try:
                header=json.loads(lines[0])
                if header!={"kind":"header","schema":1,"contract":contract,"contract_hash":digest(contract)}:
                    raise TapeMismatch("tape_contract_mismatch")
                for line in lines[1:]:
                    row=json.loads(line)
                    proof=row.pop("record_hash")
                    if digest(row)!=proof or row.get("kind")!="decision":
                        raise TapeMismatch("tape_record_corrupted")
                    if any(r["at"]==row["at"] for r in self.rows):
                        raise TapeMismatch("duplicate_decision_time")
                    self.rows.append(row)
            except (KeyError,TypeError,json.JSONDecodeError) as exc:
                raise TapeMismatch("invalid_or_truncated_tape") from exc

    def _write(self,row):
        self.stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+"\n")
        self.stream.flush()

    def append(self,snapshot,context,proposal,latency_ms,*,error=None,raw_response=None):
        if self.mode!="record" or type(latency_ms) is not int or latency_ms<0:
            raise ValueError("invalid_tape_append")
        at=context["now"]
        if any(r["at"]==at for r in self.rows):
            raise TapeMismatch("duplicate_decision_time")
        row={"kind":"decision","at":at,"snapshot":copy.deepcopy(snapshot),"context":copy.deepcopy(context),
             "input_hash":digest(semantic({"snapshot":snapshot,"context":context})),
             "proposal":copy.deepcopy(proposal),"latency_ms":latency_ms,"error":error,"raw_response":raw_response}
        self.rows.append(row)
        self._write({**row,"record_hash":digest(row)})

    def lookup(self,snapshot,context):
        at=context["now"]
        rows=[r for r in self.rows if r["at"]==at]
        if len(rows)!=1 or at in self.used:
            raise TapeMismatch("missing_or_reused_tape_decision")
        row=rows[0]
        if row["input_hash"]!=digest(semantic({"snapshot":snapshot,"context":context})):
            raise TapeMismatch("semantic_input_mismatch")
        self.used.add(at)
        result=copy.deepcopy(row)
        if result["proposal"] is not None:
            result["proposal"]["input_id"]=context["input_id"]
        return result

    def transcript_hash(self):
        return digest({"contract":self.contract,"rows":[semantic({k:v for k,v in r.items()
            if k!="raw_response"}) for r in self.rows]})

    def assert_exhausted(self):
        if self.mode=="frozen" and len(self.used)!=len(self.rows):
            raise TapeMismatch("unused_tape_decisions")

    def close(self):
        if self.stream:
            self.stream.close()

