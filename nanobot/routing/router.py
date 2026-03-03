"""ModelRouter — uses haiku to score complexity+statefulness, then selects model tier."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.routing.metrics import RoutingMetrics


# Model tier aliases — canonical short names for routing logic
_HAIKU_KEYWORDS   = ("haiku",)
_SONNET_KEYWORDS  = ("sonnet",)
_OPUS_KEYWORDS    = ("opus",)


def _tier(model: str) -> int:
    """Return 1=haiku, 2=sonnet, 3=opus for a model string. Unknown → 2 (sonnet)."""
    m = model.lower()
    if any(k in m for k in _HAIKU_KEYWORDS):
        return 1
    if any(k in m for k in _OPUS_KEYWORDS):
        return 3
    return 2  # sonnet or unknown


_CLASSIFIER_PROMPT = """\
You are a routing classifier. Evaluate the user message and conversation context below.
Return ONLY a JSON object — no explanation, no markdown.

Scoring dimensions (1–5 integers):
- complexity: 1=trivial lookup or single-word answer, 3=moderate analysis, 5=deep multi-step reasoning or creative work
- statefulness: 1=standalone (doesn't depend on prior turns), 3=moderate context needed, 5=deeply depends on conversation history or long context

Message: {message}
Conversation turns so far: {turns}
Has prior context: {has_context}

Return exactly: {{"complexity": N, "statefulness": N}}"""


@dataclass
class RoutingDecision:
    routed_model: str        # Model to actually use
    expected_model: str      # Model that would have been used without routing
    complexity: int          # 1–5
    statefulness: int        # 1–5
    metric: str              # Which bucket this falls into (for RoutingMetrics)
    reason: str              # Human-readable reason


class ModelRouter:
    """
    Classify each inbound message with haiku, then select the appropriate model tier.

    Tier map:
      complexity ≤ 1 AND statefulness ≤ 1  →  haiku  (only truly trivial)
      complexity ≥ 4 AND statefulness ≥ 3  →  opus   (genuinely hard + context-heavy)
      complexity == 5                       →  opus   (highest complexity regardless)
      everything else                       →  sonnet (default, err toward quality)
    """

    def __init__(
        self,
        provider: "LLMProvider",
        classifier_model: str,
        haiku_model: str,
        sonnet_model: str,
        opus_model: str,
        metrics: "RoutingMetrics | None" = None,
    ) -> None:
        self.provider = provider
        self.classifier_model = classifier_model
        self.haiku_model = haiku_model
        self.sonnet_model = sonnet_model
        self.opus_model = opus_model
        self.metrics = metrics

    def _tier_to_model(self, tier: int) -> str:
        if tier == 1:
            return self.haiku_model
        if tier == 3:
            return self.opus_model
        return self.sonnet_model

    def _select_tier(self, complexity: int, statefulness: int) -> int:
        """Map scores to tier (1/2/3), biased toward sonnet."""
        if complexity == 5:
            return 3
        if complexity >= 4 and statefulness >= 3:
            return 3
        if complexity <= 1 and statefulness <= 1:
            return 1
        return 2

    def _metric_key(self, expected_tier: int, routed_tier: int) -> str:
        tier_name = {1: "haiku", 2: "sonnet", 3: "opus"}
        if routed_tier < expected_tier:
            return f"downroute_to_{tier_name[routed_tier]}"
        if routed_tier > expected_tier:
            return f"uproute_to_{tier_name[routed_tier]}"
        return f"stayed_{tier_name[routed_tier]}"

    async def route(
        self,
        message: str,
        history_turns: int,
        expected_model: str,
    ) -> RoutingDecision:
        """Classify message and return a RoutingDecision."""
        has_context = history_turns > 0
        prompt = _CLASSIFIER_PROMPT.format(
            message=message[:800],  # cap to keep classifier call tiny
            turns=history_turns,
            has_context=str(has_context).lower(),
        )

        complexity = 3
        statefulness = 2
        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.classifier_model,
                temperature=0.0,
                max_tokens=32,
            )
            raw = (response.content or "").strip()
            # Strip markdown fences if present
            raw = re.sub(r"```[^\n]*\n?", "", raw).strip()
            scores = json.loads(raw)
            complexity = max(1, min(5, int(scores.get("complexity", 3))))
            statefulness = max(1, min(5, int(scores.get("statefulness", 2))))
        except Exception as exc:
            logger.warning("Routing classifier failed ({}), defaulting to sonnet tier", exc)

        routed_tier = self._select_tier(complexity, statefulness)
        expected_tier = _tier(expected_model)
        routed_model = self._tier_to_model(routed_tier)
        metric = self._metric_key(expected_tier, routed_tier)

        reason = (
            f"complexity={complexity}, statefulness={statefulness} "
            f"→ tier {routed_tier} ({routed_model.split('/')[-1]})"
        )
        logger.debug("ModelRouter: {} | expected={} routed={}", reason, expected_model, routed_model)

        decision = RoutingDecision(
            routed_model=routed_model,
            expected_model=expected_model,
            complexity=complexity,
            statefulness=statefulness,
            metric=metric,
            reason=reason,
        )

        if self.metrics:
            self.metrics.record(metric)

        return decision
