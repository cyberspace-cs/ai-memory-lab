"""运维本体（差异化：运维语料原生）【对照 cognee graph_model / ontology_file_path】
零依赖 dataclasses 声明；pydantic 适配留给 v2 侧车（用户环境装了 pydantic 时可一键转）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── 实体类型（对照 cognee 的 Node 子类声明） ──────────────
@dataclass(frozen=True)
class EntityType:
    name: str
    description: str
    examples: tuple[str, ...]


OPS_ENTITY_TYPES: tuple[EntityType, ...] = (
    EntityType("Service", "可独立部署的服务/组件", ("orderservice", "pay-service", "gateway")),
    EntityType("Alert", "监控告警事件", ("p99 延迟 3.2s", "错误率突增")),
    EntityType("Incident", "一次故障/事件单", ("orderservice-p99-事件",)),
    EntityType("Change", "变更（发布/配置/扩缩容）", ("连接池扩容 200", "v2.3.1 发布")),
    EntityType("Metric", "指标口径", ("p99 latency", "error_rate")),
)


# ── 谓词注册表（本体核心：函数型/多值在此声明） ───────────
@dataclass(frozen=True)
class PredicateSpec:
    name: str
    functional: bool          # True=单值（新值可顶旧值）；False=多值（并存）
    description: str


OPS_PREDICATES: tuple[PredicateSpec, ...] = (
    PredicateSpec("has_alert", True, "当前存在告警（解除后由新事实取代）"),
    PredicateSpec("root_cause", True, "事件根因（初判→更正走 SUPERSEDE）"),
    PredicateSpec("caused_by", True, "直接成因"),
    PredicateSpec("depends_on", True, "运行时依赖（当前拓扑）"),
    PredicateSpec("works_at", True, "服务归属"),
    PredicateSpec("mitigated_by", False, "处置动作（可多条并存）"),
    PredicateSpec("observed_metric", False, "观测到的指标值（时间序列性，并存）"),
    PredicateSpec("likes", False, "偏好（多值）"),
)


def ops_functional_predicates() -> set[str]:
    return {p.name for p in OPS_PREDICATES if p.functional}


def ops_engine(**kwargs):
    """带运维本体的 MemEngine 工厂。"""
    from .lifecycle import MemEngine
    kwargs.setdefault("functional_predicates", ops_functional_predicates())
    return MemEngine(**kwargs)


# ── 抽取 prompt 的运维段落（差异化：运维语料原生） ────────
OPS_EXTRACTION_SUFFIX = """

# 领域本体（运维事件单）
实体类型：{entity_types}
谓词与基数：{predicates}
抽取要求：
- root_cause 是单值关系：初判被更正后，旧判断会被时间轴失效（不删除）
- 指标值请用 observed_metric 并携带观测时间（valid_from）
- 变更动作用 mitigated_by，可多条并存
"""


def ops_extraction_prompt(base_prompt: str) -> str:
    """把本体拼进任意基础抽取 prompt（LLMExtractor 的 input 用）。"""
    return base_prompt + OPS_EXTRACTION_SUFFIX.format(
        entity_types="; ".join(f"{t.name}({t.description})" for t in OPS_ENTITY_TYPES),
        predicates="; ".join(f"{p.name}{'(单值)' if p.functional else '(多值)'}"
                             for p in OPS_PREDICATES),
    )
