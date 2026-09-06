from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from .features import FEATURE_COLUMNS

@dataclass(frozen=True)
class Prediction:
    signal: str; confidence: float; timestamp: float; reason: str

def train_walk_forward(X: Sequence[Mapping[str,float]], y: Sequence[int], *, min_train: int=50, test_size: int=1) -> dict[str, Any]:
    if len(X)!=len(y) or len(X)<min_train+test_size: raise ValueError("insufficient chronological samples")
    preds=[]; truths=[]
    for end in range(min_train, len(X), test_size):
        stop=min(end+test_size,len(X)); model=build_pipeline(); model.fit([[r[c] for c in FEATURE_COLUMNS] for r in X[:end]], list(y[:end])); preds.extend(model.predict([[X[i][c] for c in FEATURE_COLUMNS] for i in range(end,stop)])); truths.extend(y[end:stop])
    accuracy=sum(a==b for a,b in zip(preds,truths))/len(truths) if truths else 0.0
    return {"accuracy":accuracy,"predictions":preds,"truths":truths,"samples":len(truths)}

def build_pipeline() -> Pipeline:
    return Pipeline([("scale", StandardScaler()), ("model", LogisticRegression(max_iter=500, random_state=42, class_weight="balanced"))])

def save_artifact(model: Pipeline, path: str|Path, *, trained_at: datetime|None=None) -> None:
    joblib.dump({"model":model,"features":list(FEATURE_COLUMNS),"trained_at":(trained_at or datetime.now(timezone.utc)).isoformat()}, path)

def load_artifact(path: str|Path, *, max_age_seconds: int=86400) -> dict[str,Any]:
    artifact=joblib.load(path)
    if artifact.get("features") != list(FEATURE_COLUMNS): raise ValueError("model feature schema mismatch")
    trained=datetime.fromisoformat(artifact["trained_at"].replace("Z","+00:00"))
    if (datetime.now(timezone.utc)-trained).total_seconds()>max_age_seconds: raise ValueError("model artifact is stale")
    return artifact

def predict(model_or_artifact: Any, features: Mapping[str,float], *, threshold: float=0.60, latest_timestamp: float|None=None, max_feature_age_seconds: int=900) -> Prediction:
    missing=set(FEATURE_COLUMNS)-set(features)
    if missing: raise ValueError(f"missing feature columns: {sorted(missing)}")
    ts=float(features.get("timestamp",0)); import time
    if latest_timestamp is not None and latest_timestamp-ts>max_feature_age_seconds: raise ValueError("features are stale")
    model=model_or_artifact.get("model") if isinstance(model_or_artifact,dict) else model_or_artifact
    row=[[float(features[c]) for c in FEATURE_COLUMNS]]; probs=model.predict_proba(row)[0]; classes=list(model.classes_); idx=max(range(len(probs)),key=probs.__getitem__); confidence=float(probs[idx]); label=int(classes[idx])
    signal={1:"BUY",-1:"SELL",0:"HOLD"}.get(label,"HOLD") if confidence>=threshold else "HOLD"
    return Prediction(signal,confidence,ts,"ml_confidence_threshold")
