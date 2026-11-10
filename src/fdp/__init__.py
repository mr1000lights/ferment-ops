"""Fermentation Data Platform - proof of concept.

End-to-end pipeline: public fermentation + commodity + FX data
-> raw landing -> bronze (parquet) -> silver/gold (DuckDB SQL)
-> data quality -> ML early warning -> HTML report.
"""

__version__ = "0.1.0"
