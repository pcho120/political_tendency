"""
Phase 2: Pattern Aggregator

PRINCIPLE: LEARN FROM OBSERVATIONS, DON'T HARDCODE RULES.

This module analyzes accumulated observations to compute statistical patterns.
It calculates confidence metrics for discovery strategies based on real data.

Outputs rule_confidence.json for Phase 3 rule engine.
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from collections import Counter

from observation_logger import ObservationLogger, FirmObservation

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class StrategyConfidence:
    """
    Confidence metrics for a discovery strategy.
    
    Based on historical observations, NOT hardcoded assumptions.
    """
    strategy_name: str
    total_observations: int
    successful_observations: int
    confidence_score: float  # 0.0 to 1.0
    common_patterns: list[str]
    notes: list[str]


@dataclass
class AggregatedPatterns:
    """
    Aggregated patterns from all firm observations.
    
    These inform Phase 3 rule engine decisions.
    """
    total_firms_observed: int
    
    # Sitemap patterns
    pct_with_xml_sitemap: float
    pct_sitemap_is_attorney_list: float
    avg_sitemap_url_count: float
    
    # Directory patterns
    pct_with_working_directory: float
    pct_directory_requires_auth: float
    pct_directory_has_pagination: float
    
    # Alphabet navigation patterns
    pct_with_alphabet_nav: float
    common_letter_param_formats: list[str]
    
    # JavaScript/SPA patterns
    pct_heavy_javascript: float
    pct_spa_framework: float
    pct_with_json_api: float
    
    # Bot protection patterns
    pct_with_bot_protection: float
    pct_with_cloudflare: float
    pct_with_recaptcha: float
    pct_http_403: float
    
    # Structured data patterns
    pct_with_structured_data: float
    pct_with_schema_org_person: float
    
    # Strategy confidence
    strategy_confidence: dict[str, StrategyConfidence]
    
    # Metadata
    last_updated: str


class PatternAggregator:
    """
    Analyzes observations to compute strategy confidence metrics.
    
    CRITICAL: All metrics are data-driven, NOT assumptions.
    """
    
    def __init__(self, observation_log: str = "firm_observations.jsonl", 
                 confidence_output: str = "rule_confidence.json"):
        self.observation_log = Path(observation_log)
        self.confidence_output = Path(confidence_output)
        self.logger = ObservationLogger(str(observation_log))
    
    def aggregate(self) -> AggregatedPatterns:
        """
        Aggregate all observations into statistical patterns.
        
        Returns AggregatedPatterns with computed confidence metrics.
        """
        logger.info("Starting pattern aggregation...")
        
        observations = self.logger.load_all_observations()
        
        if not observations:
            logger.warning("No observations found - returning default patterns")
            return self._default_patterns()
        
        logger.info(f"Aggregating {len(observations)} observations")
        
        patterns = AggregatedPatterns(
            total_firms_observed=len(observations),
            pct_with_xml_sitemap=self._calc_percentage(observations, lambda o: len(o.xml_sitemaps) > 0),
            pct_sitemap_is_attorney_list=self._calc_percentage(observations, lambda o: o.sitemap_is_attorney_list),
            avg_sitemap_url_count=self._calc_average(observations, lambda o: o.sitemap_total_urls),
            pct_with_working_directory=self._calc_percentage(observations, lambda o: not o.directory_base_empty and 200 in o.http_response_codes),
            pct_directory_requires_auth=self._calc_percentage(observations, lambda o: o.directory_requires_auth),
            pct_directory_has_pagination=self._calc_percentage(observations, lambda o: o.directory_has_pagination),
            pct_with_alphabet_nav=self._calc_percentage(observations, lambda o: o.alphabet_navigation_detected),
            common_letter_param_formats=self._extract_common_patterns(observations, lambda o: o.letter_param_format if o.letter_param_format else None),
            pct_heavy_javascript=self._calc_percentage(observations, lambda o: o.heavy_javascript_detected),
            pct_spa_framework=self._calc_percentage(observations, lambda o: o.react_vue_angular_detected),
            pct_with_json_api=self._calc_percentage(observations, lambda o: len(o.json_api_endpoints_found) > 0),
            pct_with_bot_protection=self._calc_percentage(observations, lambda o: o.bot_protection_detected),
            pct_with_cloudflare=self._calc_percentage(observations, lambda o: o.cloudflare_detected),
            pct_with_recaptcha=self._calc_percentage(observations, lambda o: o.recaptcha_detected),
            pct_http_403=self._calc_percentage(observations, lambda o: o.http_403_encountered),
            pct_with_structured_data=self._calc_percentage(observations, lambda o: o.structured_data_present),
            pct_with_schema_org_person=self._calc_percentage(observations, lambda o: o.schema_org_person_found),
            strategy_confidence=self._compute_strategy_confidence(observations),
            last_updated=""
        )
        
        from datetime import datetime
        patterns.last_updated = datetime.utcnow().isoformat() + "Z"
        
        logger.info(f"Aggregation complete: {patterns.total_firms_observed} firms analyzed")
        logger.info(f"  - {patterns.pct_with_xml_sitemap:.1f}% have XML sitemaps")
        logger.info(f"  - {patterns.pct_sitemap_is_attorney_list:.1f}% sitemaps are attorney lists")
        logger.info(f"  - {patterns.pct_with_alphabet_nav:.1f}% have alphabet navigation")
        logger.info(f"  - {patterns.pct_with_bot_protection:.1f}% have bot protection")
        
        return patterns
    
    def aggregate_and_save(self):
        """Aggregate patterns and save to JSON file."""
        patterns = self.aggregate()
        self._save_confidence_file(patterns)
        logger.info(f"Confidence metrics saved to {self.confidence_output}")
        return patterns
    
    def _calc_percentage(self, observations: list[FirmObservation], predicate) -> float:
        """Calculate percentage of observations matching predicate."""
        if not observations:
            return 0.0
        matching = sum(1 for obs in observations if predicate(obs))
        return (matching / len(observations)) * 100.0
    
    def _calc_average(self, observations: list[FirmObservation], extractor) -> float:
        """Calculate average of extracted numeric values."""
        if not observations:
            return 0.0
        values = [extractor(obs) for obs in observations]
        return sum(values) / len(values) if values else 0.0
    
    def _extract_common_patterns(self, observations: list[FirmObservation], 
                                 extractor, top_n: int = 5) -> list[str]:
        """Extract most common patterns from observations."""
        patterns = [extractor(obs) for obs in observations]
        patterns = [p for p in patterns if p is not None]
        
        if not patterns:
            return []
        
        counter = Counter(patterns)
        return [pattern for pattern, count in counter.most_common(top_n)]
    
    def _compute_strategy_confidence(self, observations: list[FirmObservation]) -> dict[str, StrategyConfidence]:
        """
        Compute confidence scores for each discovery strategy.
        
        IMPORTANT: This is based on OBSERVATIONS, not assumptions.
        A strategy is "successful" if it yields useful signals.
        """
        strategies = {}
        
        # XML Sitemap Strategy
        sitemap_obs = [o for o in observations if len(o.xml_sitemaps) > 0]
        sitemap_successful = [o for o in sitemap_obs if o.sitemap_is_attorney_list]
        strategies["xml_sitemap"] = StrategyConfidence(
            strategy_name="xml_sitemap",
            total_observations=len(sitemap_obs),
            successful_observations=len(sitemap_successful),
            confidence_score=len(sitemap_successful) / len(sitemap_obs) if sitemap_obs else 0.0,
            common_patterns=self._extract_common_patterns(sitemap_obs, lambda o: o.sitemap_url_patterns[0] if o.sitemap_url_patterns else None),
            notes=[f"Found in {len(sitemap_obs)}/{len(observations)} firms"]
        )
        
        # Alphabet Navigation Strategy
        alphabet_obs = [o for o in observations if o.alphabet_navigation_detected]
        strategies["alphabet_navigation"] = StrategyConfidence(
            strategy_name="alphabet_navigation",
            total_observations=len(alphabet_obs),
            successful_observations=len(alphabet_obs),  # Detection = success
            confidence_score=len(alphabet_obs) / len(observations) if observations else 0.0,
            common_patterns=self._extract_common_patterns(alphabet_obs, lambda o: o.letter_param_format),
            notes=[f"Found in {len(alphabet_obs)}/{len(observations)} firms"]
        )
        
        # Directory Strategy
        directory_obs = [o for o in observations if not o.directory_base_empty]
        directory_working = [o for o in directory_obs if not o.directory_requires_auth]
        strategies["directory_listing"] = StrategyConfidence(
            strategy_name="directory_listing",
            total_observations=len(directory_obs),
            successful_observations=len(directory_working),
            confidence_score=len(directory_working) / len(directory_obs) if directory_obs else 0.0,
            common_patterns=self._extract_common_patterns(directory_obs, lambda o: o.directory_paths_tested[0] if o.directory_paths_tested else None),
            notes=[f"{len(directory_working)}/{len(directory_obs)} directories accessible"]
        )
        
        # JavaScript/API Strategy
        api_obs = [o for o in observations if len(o.json_api_endpoints_found) > 0 or o.graphql_endpoint_detected]
        strategies["json_api"] = StrategyConfidence(
            strategy_name="json_api",
            total_observations=len(api_obs),
            successful_observations=len(api_obs),
            confidence_score=len(api_obs) / len(observations) if observations else 0.0,
            common_patterns=self._extract_common_patterns(api_obs, lambda o: o.json_api_endpoints_found[0] if o.json_api_endpoints_found else None),
            notes=[f"API endpoints found in {len(api_obs)}/{len(observations)} firms"]
        )
        
        # Structured Data Strategy
        structured_obs = [o for o in observations if o.structured_data_present]
        person_obs = [o for o in structured_obs if o.schema_org_person_found]
        strategies["structured_data"] = StrategyConfidence(
            strategy_name="structured_data",
            total_observations=len(structured_obs),
            successful_observations=len(person_obs),
            confidence_score=len(person_obs) / len(structured_obs) if structured_obs else 0.0,
            common_patterns=[],
            notes=[f"Person schema in {len(person_obs)}/{len(structured_obs)} structured data sites"]
        )
        
        return strategies
    
    def _default_patterns(self) -> AggregatedPatterns:
        """
        Return default patterns when no observations exist.
        
        ALL CONFIDENCE SCORES = 0.0 (unknown, not rejected).
        """
        from datetime import datetime
        return AggregatedPatterns(
            total_firms_observed=0,
            pct_with_xml_sitemap=0.0,
            pct_sitemap_is_attorney_list=0.0,
            avg_sitemap_url_count=0.0,
            pct_with_working_directory=0.0,
            pct_directory_requires_auth=0.0,
            pct_directory_has_pagination=0.0,
            pct_with_alphabet_nav=0.0,
            common_letter_param_formats=[],
            pct_heavy_javascript=0.0,
            pct_spa_framework=0.0,
            pct_with_json_api=0.0,
            pct_with_bot_protection=0.0,
            pct_with_cloudflare=0.0,
            pct_with_recaptcha=0.0,
            pct_http_403=0.0,
            pct_with_structured_data=0.0,
            pct_with_schema_org_person=0.0,
            strategy_confidence={},
            last_updated=datetime.utcnow().isoformat() + "Z"
        )
    
    def _save_confidence_file(self, patterns: AggregatedPatterns):
        """Save aggregated patterns to JSON file."""
        try:
            # Convert to dict for JSON serialization
            data = asdict(patterns)
            
            with open(self.confidence_output, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved confidence metrics to {self.confidence_output}")
        except Exception as e:
            logger.error(f"Failed to save confidence file: {e}")
    
    def load_confidence_file(self) -> Optional[AggregatedPatterns]:
        """Load confidence metrics from file."""
        if not self.confidence_output.exists():
            logger.warning(f"Confidence file not found: {self.confidence_output}")
            return None
        
        try:
            with open(self.confidence_output, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Convert strategy_confidence dict back to StrategyConfidence objects
            if "strategy_confidence" in data:
                data["strategy_confidence"] = {
                    name: StrategyConfidence(**conf_data)
                    for name, conf_data in data["strategy_confidence"].items()
                }
            
            patterns = AggregatedPatterns(**data)
            logger.info(f"Loaded confidence metrics from {self.confidence_output}")
            return patterns
        except Exception as e:
            logger.error(f"Failed to load confidence file: {e}")
            return None


# Convenience function
def aggregate_patterns(observation_log: str = "firm_observations.jsonl",
                      confidence_output: str = "rule_confidence.json") -> AggregatedPatterns:
    """
    Convenience function to aggregate patterns and save confidence file.
    
    Returns AggregatedPatterns with computed metrics.
    """
    aggregator = PatternAggregator(observation_log, confidence_output)
    return aggregator.aggregate_and_save()
