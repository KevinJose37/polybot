"""Reconciler to flag divergence between Polymarket and external references."""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ReconcilerConfig:
    divergence_threshold: float = 0.05  # 5% divergence threshold


class MidReconciler:
    """
    Compares Polymarket mid-prices against external references (like Binance spot)
    and flags significant divergences.
    """

    def __init__(self, config: ReconcilerConfig):
        self.config = config

        # We store the last known values for comparison
        self._poly_mids: dict[str, float] = {}
        self._spot_mids: dict[str, float] = {}

        # Storing the baseline (initial) prices to calculate relative divergence
        self._poly_baselines: dict[str, float] = {}
        self._spot_baselines: dict[str, float] = {}

    def update_polymarket_mid(self, market_id: str, mid_price: float) -> bool:
        """
        Update the Polymarket mid-price.
        Returns True if a divergence flag was raised.
        """
        self._poly_mids[market_id] = mid_price
        if market_id not in self._poly_baselines:
            self._poly_baselines[market_id] = mid_price

        return self._check_divergence(market_id)

    def update_spot_mid(self, asset: str, mid_price: float) -> None:
        """
        Update the external spot mid-price.
        """
        self._spot_mids[asset] = mid_price
        if asset not in self._spot_baselines:
            self._spot_baselines[asset] = mid_price

    def _check_divergence(self, market_id: str) -> bool:
        """
        Check if the relative move in Polymarket diverges significantly
        from the relative move in the underlying spot market.

        Note: The actual mapping from market_id to asset requires a mapping dictionary
        in a full implementation. For MVP smoke testing, this serves as the structural check.
        """
        # In a real scenario, we would map market_id to the specific asset (e.g., 'ETH')
        # and compare the implied probability vs spot price using a pricing model.
        # For this skeleton, we assume we just flag if poly_mid deviates from its own baseline
        # by a huge margin rapidly, representing oracle/feed desync.

        poly_mid = self._poly_mids.get(market_id)
        baseline = self._poly_baselines.get(market_id)

        if poly_mid is None or baseline is None or baseline == 0:
            return False

        divergence = abs(poly_mid - baseline) / baseline
        if divergence > self.config.divergence_threshold:
            logger.warning(
                "price_divergence_flagged",
                market_id=market_id,
                divergence=divergence,
                threshold=self.config.divergence_threshold,
                poly_mid=poly_mid,
                baseline=baseline,
            )
            return True

        return False
