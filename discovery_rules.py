"""
Phase 3: Discovery Rules Engine

PRINCIPLE: DYNAMIC RULES BASED ON OBSERVATIONS, NOT HARDCODED REJECTIONS.

This module applies discovery strategies based on:
1. Current firm observation
2. Historical pattern confidence from Phase 2

CRITICAL RULES:
- NO permanent HARD_CASE labels
- NO permanent REJECT labels
- ALL decisions recalculated EVERY run
- Unknown patterns = UNKNOWN_PATTERN (not rejection)
- Failure to observe structure = DISCOVERY_INCOMPLETE (not rejection)
"""

import logging
from dataclasses import dataclass
from typing import Optional

from observation_logger import FirmObservation, ObservationLogger
from pattern_aggregator import PatternAggregator, AggregatedPatterns, StrategyConfidence

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class DiscoveryStrategy:
    """
    A discovery strategy recommendation with confidence.
    
    NOT a success/failure prediction - just strategy recommendation.
    """
    strategy_type: str  # xml_sitemap, alphabet_navigation, directory_listing, etc.
    priority: int  # 1 = try first, 2 = try second, etc.
    confidence_score: float  # 0.0 to 1.0
    reasoning: str
    parameters: dict  # Strategy-specific params


@dataclass
class DiscoveryRecommendation:
    """
    Complete discovery recommendation for a firm.
    
    NEVER contains HARD_CASE or REJECT.
    Contains ordered list of strategies to try.
    """
    firm: str
    strategies: list[DiscoveryStrategy]
    classification: str  # STANDARD, SPA_HEAVY, BOT_PROTECTED, UNKNOWN_PATTERN, DISCOVERY_INCOMPLETE
    notes: list[str]


class DiscoveryRulesEngine:
    """
    Applies dynamic rules based on observations + historical patterns.
    
    CRITICAL BEHAVIOR:
    - Returns strategies to TRY, not predictions of SUCCESS/FAILURE
    - NEVER returns HARD_CASE or REJECT
    - Unknown patterns = try all strategies with lower confidence
    - Bot protection = note it, but still try strategies
    """
    
    def __init__(self, 
                 observation_log: str = "firm_observations.jsonl",
                 confidence_file: str = "rule_confidence.json"):
        self.observation_logger = ObservationLogger(observation_log)
        self.aggregator = PatternAggregator(observation_log, confidence_file)
        self.patterns: Optional[AggregatedPatterns] = None
        
        # Load historical patterns
        self._load_patterns()
    
    def _load_patterns(self):
        """Load aggregated patterns from Phase 2."""
        self.patterns = self.aggregator.load_confidence_file()
        
        if self.patterns is None:
            logger.warning("No historical patterns found - will use default confidence")
            self.patterns = self.aggregator._default_patterns()
        else:
            logger.info(f"Loaded patterns from {self.patterns.total_firms_observed} observations")
    
    def get_discovery_recommendation(self, firm: str, base_url: str) -> DiscoveryRecommendation:
        """
        Get discovery strategy recommendation for a firm.
        
        Process:
        1. Run Phase 1 observation (or load existing)
        2. Apply Phase 3 rules based on observation + historical patterns
        3. Return ordered list of strategies to try
        
        NEVER returns HARD_CASE or REJECT.
        """
        logger.info(f"Generating discovery recommendation for {firm}")
        
        # Phase 1: Observe firm (or use most recent observation)
        observation = self._get_or_create_observation(firm, base_url)
        
        # Phase 3: Apply rules
        strategies = self._apply_rules(observation)
        classification = self._classify_firm(observation)
        notes = self._generate_notes(observation)
        
        recommendation = DiscoveryRecommendation(
            firm=firm,
            strategies=strategies,
            classification=classification,
            notes=notes
        )
        
        logger.info(f"Recommendation for {firm}: {len(strategies)} strategies, classified as {classification}")
        return recommendation
    
    def _get_or_create_observation(self, firm: str, base_url: str) -> FirmObservation:
        """Get most recent observation or create new one."""
        # Check for existing observations
        existing = self.observation_logger.get_observations_for_firm(firm)
        
        if existing:
            logger.info(f"Using existing observation for {firm} ({len(existing)} total)")
            return existing[-1]  # Most recent
        
        # Create new observation
        logger.info(f"Creating new observation for {firm}")
        return self.observation_logger.observe_firm(firm, base_url)
    
    def _apply_rules(self, obs: FirmObservation) -> list[DiscoveryStrategy]:
        """
        Apply rules to determine ordered strategy list.
        
        Rules are based on CURRENT observation + HISTORICAL patterns.
        """
        strategies = []
        
        # Rule 1: XML Sitemap with attorney list (HIGH PRIORITY)
        if obs.sitemap_is_attorney_list:
            confidence = self._get_strategy_confidence("xml_sitemap")
            strategies.append(DiscoveryStrategy(
                strategy_type="xml_sitemap_as_list",
                priority=1,
                confidence_score=max(confidence, 0.7),  # Observed signal = boost confidence
                reasoning="Sitemap appears to contain attorney URLs directly",
                parameters={
                    "sitemap_urls": obs.xml_sitemaps,
                    "url_patterns": obs.sitemap_url_patterns
                }
            ))
        
        # Rule 2: Alphabet navigation detected (HIGH PRIORITY)
        if obs.alphabet_navigation_detected:
            confidence = self._get_strategy_confidence("alphabet_navigation")
            strategies.append(DiscoveryStrategy(
                strategy_type="alphabet_enumeration",
                priority=1 if not obs.sitemap_is_attorney_list else 2,
                confidence_score=max(confidence, 0.6),
                reasoning="Alphabet navigation links found (A-Z)",
                parameters={
                    "letter_param_format": obs.letter_param_format,
                    "sample_links": obs.letter_links_found[:5]
                }
            ))
        
        # Rule 3: Directory listing available (MEDIUM PRIORITY)
        if not obs.directory_base_empty and not obs.directory_requires_auth:
            confidence = self._get_strategy_confidence("directory_listing")
            strategies.append(DiscoveryStrategy(
                strategy_type="directory_listing",
                priority=2,
                confidence_score=confidence,
                reasoning="Directory pages accessible and non-empty",
                parameters={
                    "paths": obs.directory_paths_tested,
                    "has_pagination": obs.directory_has_pagination,
                    "has_filters": obs.directory_has_filters
                }
            ))
        
        # Rule 4: XML Sitemap as navigation aid (MEDIUM PRIORITY)
        if len(obs.xml_sitemaps) > 0 and not obs.sitemap_is_attorney_list:
            confidence = self._get_strategy_confidence("xml_sitemap")
            strategies.append(DiscoveryStrategy(
                strategy_type="xml_sitemap_navigation",
                priority=2,
                confidence_score=confidence * 0.7,  # Lower confidence if not direct list
                reasoning="Sitemap available but may not be direct attorney list",
                parameters={
                    "sitemap_urls": obs.xml_sitemaps,
                    "contains_people": obs.sitemap_contains_people
                }
            ))
        
        # Rule 5: JSON API endpoints detected (MEDIUM-LOW PRIORITY)
        if len(obs.json_api_endpoints_found) > 0 or obs.graphql_endpoint_detected:
            confidence = self._get_strategy_confidence("json_api")
            strategies.append(DiscoveryStrategy(
                strategy_type="json_api",
                priority=3,
                confidence_score=confidence,
                reasoning="API endpoints detected - may provide attorney data",
                parameters={
                    "endpoints": obs.json_api_endpoints_found,
                    "graphql": obs.graphql_endpoint_detected
                }
            ))
        
        # Rule 6: Search functionality (LOW PRIORITY)
        if obs.search_form_detected:
            strategies.append(DiscoveryStrategy(
                strategy_type="search_based",
                priority=4,
                confidence_score=0.3,  # Search often unreliable for bulk extraction
                reasoning="Search form available",
                parameters={
                    "search_url": obs.search_endpoint_url,
                    "requires_js": obs.search_requires_javascript
                }
            ))
        
        # Rule 7: Structured data present (SUPPLEMENTARY)
        if obs.schema_org_person_found:
            strategies.append(DiscoveryStrategy(
                strategy_type="structured_data_extraction",
                priority=5,
                confidence_score=0.4,
                reasoning="Schema.org Person markup found - can augment other strategies",
                parameters={
                    "jsonld": obs.jsonld_detected,
                    "microdata": obs.microdata_detected
                }
            ))
        
        # Rule 8: BOT_PROTECTED with no usable strategies — emit Playwright-based strategies
        # This MUST come before the generic fallback so we don't add xml_sitemap_navigation
        # (which would trick select_strategies into thinking a real sitemap exists).
        if not strategies and (obs.bot_protection_detected or obs.http_403_encountered):
            logger.info(f"Bot-protected site with no other signals for {obs.firm} — emitting Playwright strategies")
            strategies = [
                DiscoveryStrategy(
                    strategy_type="kirkland_scroll",
                    priority=1,
                    confidence_score=0.85,
                    reasoning="Bot-protected site — use Playwright DOM scroll on attorney directory",
                    parameters={}
                ),
                DiscoveryStrategy(
                    strategy_type="alphabet_enumeration",
                    priority=2,
                    confidence_score=0.60,
                    reasoning="Bot-protected site — try alphabet enumeration via Playwright",
                    parameters={}
                ),
                DiscoveryStrategy(
                    strategy_type="directory_listing",
                    priority=3,
                    confidence_score=0.30,
                    reasoning="Bot-protected site — try direct directory paths",
                    parameters={}
                ),
                DiscoveryStrategy(
                    strategy_type="dom_exhaustion",
                    priority=4,
                    confidence_score=0.20,
                    reasoning="Bot-protected site — last-resort DOM exhaustion",
                    parameters={}
                ),
            ]

        # Rule 9: FALLBACK - If no specific strategies, try everything with low confidence
        if not strategies:
            logger.warning(f"No specific strategies for {obs.firm} - using fallback")
            strategies = self._fallback_strategies(obs)
        
        # Sort by priority
        strategies.sort(key=lambda s: s.priority)
        
        return strategies
    
    def _classify_firm(self, obs: FirmObservation) -> str:
        """
        Classify firm based on observations.
        
        CRITICAL: NEVER returns HARD_CASE or REJECT.
        Classifications are informational, NOT rejection labels.
        """
        # Check for bot protection (informational, not rejection)
        if obs.bot_protection_detected or obs.http_403_encountered:
            return "BOT_PROTECTED"  # Note: still try strategies
        
        # Check for heavy JavaScript/SPA
        if obs.react_vue_angular_detected or obs.heavy_javascript_detected:
            return "SPA_HEAVY"  # Note: still try strategies (API, etc.)
        
        # Check if we found useful signals
        has_signals = (
            obs.sitemap_is_attorney_list or
            obs.alphabet_navigation_detected or
            not obs.directory_base_empty or
            len(obs.json_api_endpoints_found) > 0
        )
        
        if has_signals:
            return "STANDARD"  # We have strategies to try
        
        # No clear signals found
        if len(obs.notes) > 0:
            return "UNKNOWN_PATTERN"  # Observed something, but unclear structure
        else:
            return "DISCOVERY_INCOMPLETE"  # Need more observation
    
    def _generate_notes(self, obs: FirmObservation) -> list[str]:
        """Generate human-readable notes about observation."""
        notes = []
        
        if obs.sitemap_is_attorney_list:
            notes.append(f"✓ Sitemap contains {obs.sitemap_total_urls} attorney URLs")
        
        if obs.alphabet_navigation_detected:
            notes.append(f"✓ Alphabet navigation detected ({len(obs.letter_links_found)} letters)")
        
        if obs.bot_protection_detected:
            notes.append("⚠ Bot protection detected - may require special handling")
        
        if obs.http_403_encountered:
            notes.append("⚠ HTTP 403 encountered - authentication may be required")
        
        if obs.react_vue_angular_detected:
            notes.append("⚠ JavaScript framework detected - may need browser automation")
        
        if not notes:
            notes.append("⚠ No clear discovery signals - will try all strategies")
        
        # Add observation-specific notes
        notes.extend(obs.notes[:5])  # First 5 observation notes
        
        return notes
    
    def _fallback_strategies(self, obs: FirmObservation) -> list[DiscoveryStrategy]:
        """
        Fallback strategies when no specific signals found.
        
        Try everything with low-to-medium confidence.
        """
        return [
            DiscoveryStrategy(
                strategy_type="directory_listing",
                priority=1,
                confidence_score=0.4,
                reasoning="Fallback: trying common directory paths",
                parameters={"paths": ["/attorneys", "/lawyers", "/people", "/team"]}
            ),
            DiscoveryStrategy(
                strategy_type="xml_sitemap_navigation",
                priority=2,
                confidence_score=0.3,
                reasoning="Fallback: checking for sitemaps",
                parameters={"sitemap_urls": ["/sitemap.xml"]}
            ),
            DiscoveryStrategy(
                strategy_type="alphabet_enumeration",
                priority=3,
                confidence_score=0.3,
                reasoning="Fallback: trying alphabet enumeration",
                parameters={"letter_param_format": "?letter={letter}"}
            ),
            DiscoveryStrategy(
                strategy_type="manual_inspection_required",
                priority=4,
                confidence_score=0.0,
                reasoning="No clear automated strategy - manual inspection needed",
                parameters={}
            )
        ]
    
    def _get_strategy_confidence(self, strategy_name: str) -> float:
        """Get historical confidence for a strategy."""
        if not self.patterns or not self.patterns.strategy_confidence:
            return 0.5  # Default medium confidence if no history
        
        strategy_conf = self.patterns.strategy_confidence.get(strategy_name)
        if strategy_conf:
            return strategy_conf.confidence_score
        
        return 0.5  # Default if strategy not in history


# Convenience function
def get_discovery_recommendation(firm: str, base_url: str,
                                 observation_log: str = "firm_observations.jsonl",
                                 confidence_file: str = "rule_confidence.json") -> DiscoveryRecommendation:
    """
    Convenience function to get discovery recommendation.
    
    Returns DiscoveryRecommendation with ordered strategies.
    NEVER returns HARD_CASE or REJECT.
    """
    engine = DiscoveryRulesEngine(observation_log, confidence_file)
    return engine.get_discovery_recommendation(firm, base_url)
