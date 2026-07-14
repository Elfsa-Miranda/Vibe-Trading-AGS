"""Built-in producer adapters for Alpha Quality evidence."""

from src.alpha_quality.adapters.tushare_csi300_pit_v1 import (
    TushareCSI300PITAdapterV1,
)
from src.alpha_quality.adapters.baostock_eligible_universe_v1 import (
    BaoStockAshareEligibleUniverseAdapterV1,
)
from src.alpha_quality.adapters.akshare_supplement_v1 import AKShareSupplementAdapterV1

__all__ = ["AKShareSupplementAdapterV1", "BaoStockAshareEligibleUniverseAdapterV1", "TushareCSI300PITAdapterV1"]
