from pydantic import BaseModel, Field
from typing import Optional
from deepeval.metrics import GEval


class MetricResult(BaseModel):
    metric:    str
    score:     float
    passed:    Optional[bool] = None
    threshold: Optional[float] = None
    reason:    Optional[str] = None

    @classmethod
    def from_geval(cls, metric: GEval) -> "MetricResult":
        return cls(
            metric=metric.name,
            score=metric.score,
            reason=metric.reason,
        )

    @classmethod
    def empty(cls, metric_name: str = "", threshold: float = 0.0) -> "MetricResult":
        return cls(
            metric=metric_name,
            score=0.0,
            passed=False,
            threshold=threshold,
            reason=None,
        )

    def to_dict(self) -> dict:
        return self.model_dump()