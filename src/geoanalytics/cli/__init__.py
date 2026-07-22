"""Package geoanalytics.cli — modular CLI submodules."""

from __future__ import annotations

import geoanalytics.cli.backtest
import geoanalytics.cli.futrader
import geoanalytics.cli.market
import geoanalytics.cli.nlp
import geoanalytics.cli.pipeline
import geoanalytics.cli.portfolio
import geoanalytics.cli.services
from geoanalytics.cli.common import app

__all__ = ["app"]
