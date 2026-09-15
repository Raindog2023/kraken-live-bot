import pytest
from app.ml.features import normalize_completed_ohlcv,make_features,FEATURE_COLUMNS
from app.ml.labels import forward_return_labels
from app.ml.model import build_pipeline,predict

def rows(n=25): return [[i*300,100+i,101+i,99+i,100+i,100+i,10+i,1] for i in range(n)]  # Increased for new indicators
def test_completed_and_features_no_lookahead():
 c=normalize_completed_ohlcv(rows(),now=8000,interval_seconds=300); assert len(c)==25
 f=make_features(c); assert len(f)==5 and set(FEATURE_COLUMNS)<=f[-1].keys()  # Reduced expected features due to minimum candle requirement
 c2=c.copy(); c2[-1]['close']*=10; assert make_features(c2)[-2]==f[-2]
def test_labels_forward_only():
 c=[{"close":100+i,"timestamp":i} for i in range(6)]; assert forward_return_labels(c,horizon=2,threshold=0.001)[0]==1 and forward_return_labels(c,horizon=2)[-1] is None
def test_inference_schema():
 X=[{x:float(i) for x in FEATURE_COLUMNS} for i in range(30)]; y=[i%2 for i in range(30)]; m=build_pipeline().fit([[r[x] for x in FEATURE_COLUMNS] for r in X],y); p=predict(m,{**X[-1],"timestamp":100},threshold=.5,latest_timestamp=100); assert p.signal in {"BUY","SELL","HOLD"}
 with pytest.raises(ValueError): predict(m,{"return_1":1})
def test_technical_indicators():
 c=normalize_completed_ohlcv(rows(),now=8000,interval_seconds=300); f=make_features(c)
 assert len(f)>0
 assert "rsi_14" in f[-1]
 assert "macd_signal" in f[-1]
 assert "bollinger_position" in f[-1]
 assert 0 <= f[-1]["rsi_14"] <= 1  # Normalized RSI
 assert 0 <= f[-1]["bollinger_position"] <= 1  # Bollinger position
